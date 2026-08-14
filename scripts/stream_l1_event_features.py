#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import os
import re
import sys
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import psycopg2
from psycopg2.extras import execute_values

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from core_pipeline.entity_normalizer import entity_pair_key, normalize
from core_pipeline.event_extract_v11 import ExtractionResult, extract_one
from scripts.news_date_cleaning import parse_datetime
from scripts.db_runtime_config import require_database_password


LOGGER = logging.getLogger("stream_l1_event_features")
PROCESSOR_VERSION = "l1_feature_stream_v2"
MODEL_DIR = PROJECT_ROOT / "data" / "models"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Incrementally prepare crawled news for L1 event clustering."
    )
    parser.add_argument("--host", default="192.168.207.171")
    parser.add_argument("--port", type=int, default=54333)
    parser.add_argument("--user", default="postgres")
    parser.add_argument("--dbname", default="news")
    parser.add_argument(
        "--mode",
        choices=("all", "prep", "extract"),
        default="all",
        help="all=legacy combined mode, prep=only fill news_l1_prep, extract=only fill event extraction table.",
    )
    parser.add_argument("--batch-size", type=int, default=200)
    parser.add_argument("--max-rows", type=int)
    parser.add_argument("--poll-sec", type=float, default=0.0)
    parser.add_argument("--max-empty-polls", type=int, default=0)
    parser.add_argument("--target-start", default="2025-06-21")
    parser.add_argument("--target-end", default="2026-06-20")
    parser.add_argument("--include-out-of-window", action="store_true")
    parser.add_argument("--skip-event-extraction", action="store_true")
    parser.add_argument("--retry-failed", action="store_true")
    parser.add_argument("--event-concurrency", type=int, default=8)
    parser.add_argument("--domain-gate-threshold", type=float, default=0.30)
    parser.add_argument("--disable-domain-gate", action="store_true")
    parser.add_argument("--disable-quality-label-gate", action="store_true")
    parser.add_argument("--quality-label-version", default="quality_v1_20260629")
    parser.add_argument("--log-every", type=int, default=1000)
    return parser.parse_args()


def connect(args: argparse.Namespace) -> Any:
    return psycopg2.connect(
        host=args.host,
        port=args.port,
        user=args.user,
        password=require_database_password(),
        dbname=args.dbname,
        connect_timeout=20,
    )


def ensure_schema(conn: Any) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS public.news_l1_prep (
                news_id BIGINT PRIMARY KEY,
                source_domain TEXT,
                url_hash TEXT,
                title_hash TEXT,
                body_hash TEXT,
                dedupe_key TEXT,
                text_quality_flag TEXT NOT NULL DEFAULT 'unknown',
                text_chars INTEGER NOT NULL DEFAULT 0,
                embedding_text_hash TEXT,
                embedding_text_chars INTEGER NOT NULL DEFAULT 0,
                published_at_raw TIMESTAMPTZ,
                published_at_clean TIMESTAMPTZ,
                published_date DATE,
                in_target_window BOOLEAN NOT NULL DEFAULT FALSE,
                processing_status TEXT NOT NULL DEFAULT 'pending_event',
                processor_version TEXT NOT NULL DEFAULT 'l1_feature_stream_v1',
                processed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS public.news_l1_event_extractions (
                news_id BIGINT PRIMARY KEY,
                language TEXT,
                region TEXT,
                event_domain TEXT,
                event_family TEXT,
                event_action TEXT,
                initiator TEXT,
                target TEXT,
                location TEXT,
                tone TEXT,
                canonical_initiator TEXT,
                canonical_target TEXT,
                entity_pair_key TEXT,
                parse_success BOOLEAN,
                extraction_error TEXT,
                raw_response TEXT,
                processor_version TEXT NOT NULL DEFAULT 'l1_feature_stream_v2',
                extracted_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_news_l1_prep_processing_status "
            "ON public.news_l1_prep (processing_status)"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_news_l1_event_extractions_family_action "
            "ON public.news_l1_event_extractions (event_family, event_action, entity_pair_key)"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_news_l1_event_extractions_domain "
            "ON public.news_l1_event_extractions (event_domain)"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_news_l1_event_extractions_parse_success "
            "ON public.news_l1_event_extractions (parse_success)"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_news_l1_prep_published_date "
            "ON public.news_l1_prep (published_date)"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_news_l1_prep_dedupe_key "
            "ON public.news_l1_prep (dedupe_key)"
        )
    conn.commit()


def sha256_text(value: str | None) -> str | None:
    text = (value or "").strip()
    if not text:
        return None
    return hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest()


def normalize_space(value: str | None) -> str:
    return re.sub(r"\s+", " ", (value or "").strip())


def build_embedding_text(title: str | None, body: str | None) -> str:
    title_clean = normalize_space(title)
    body_clean = normalize_space(body)
    if len(body_clean) > 2400:
        body_clean = body_clean[:2400]
    return f"{title_clean}\n{body_clean}".strip()


def build_gate_text(title: str | None, body: str | None) -> str:
    return f"{normalize_space(title)} {normalize_space(body)[:500]}".strip()


def load_domain_gate(args: argparse.Namespace) -> tuple[Any, Any] | None:
    if args.disable_domain_gate:
        return None
    import joblib

    vectorizer_path = MODEL_DIR / "domain_tfidf_lr.joblib"
    model_path = MODEL_DIR / "domain_classifier_lr.joblib"
    if not vectorizer_path.exists() or not model_path.exists():
        raise FileNotFoundError(f"domain gate model files missing under {MODEL_DIR}")
    return joblib.load(vectorizer_path), joblib.load(model_path)


def score_domain_gate(rows: list[dict[str, Any]], gate: tuple[Any, Any] | None) -> dict[int, float]:
    if gate is None:
        return {int(row["id"]): 1.0 for row in rows}
    vectorizer, model = gate
    texts = [build_gate_text(row.get("title"), row.get("body")) for row in rows]
    scores = model.predict_proba(vectorizer.transform(texts))[:, 1]
    return {int(row["id"]): float(score) for row, score in zip(rows, scores)}


def text_quality_flag(title: str | None, body: str | None) -> str:
    title_clean = normalize_space(title)
    body_text = (body or "").strip()
    if not title_clean:
        return "missing_title"
    if not body_text:
        return "missing_body"
    if len(body_text) < 100:
        return "too_short"
    head = body_text[:600]
    css_chars = len(re.findall(r"[\{\};#]", head))
    if css_chars > max(20, len(head) * 0.04):
        return "css_or_template"
    lines = [line.strip() for line in head.splitlines() if line.strip()]
    if len(lines) >= 5:
        short_nav = sum(
            1
            for line in lines
            if len(line) < 60 and not line.endswith((".", "!", "?", "。"))
        )
        if short_nav / len(lines) > 0.75:
            return "nav_or_listing"
    return "ok"


def canonical_entity(value: str | None) -> str | None:
    if not value:
        return None
    parts = normalize(value)
    return "&".join(parts) if parts else None


def clean_dt(value: Any, start: datetime, end: datetime) -> tuple[Any, Any, bool]:
    if value is None:
        return None, None, False
    dt = parse_datetime(value)
    if dt is None:
        return value, None, False
    in_window = start <= dt <= end
    return value, dt, in_window


def target_bounds(args: argparse.Namespace) -> tuple[datetime, datetime]:
    start = datetime.fromisoformat(args.target_start).replace(tzinfo=timezone.utc)
    end = datetime.fromisoformat(args.target_end).replace(
        hour=23, minute=59, second=59, microsecond=999999, tzinfo=timezone.utc
    )
    return start, end


def fetch_rows(
    conn: Any,
    args: argparse.Namespace,
    *,
    remaining: int | None,
) -> list[dict[str, Any]]:
    start, end = target_bounds(args)
    limit = args.batch_size if remaining is None else max(1, min(args.batch_size, remaining))

    mode = "prep" if args.skip_event_extraction else args.mode
    status_filter = "f.news_id IS NULL"
    if mode == "extract":
        status_filter = (
            "f.news_id IS NOT NULL "
            "AND COALESCE(f.processing_status, '') = 'pending_event' "
            "AND f.text_quality_flag = 'ok' "
            "AND e.news_id IS NULL"
        )
        if args.retry_failed:
            status_filter = (
                "f.news_id IS NOT NULL "
                "AND f.text_quality_flag = 'ok' "
                "AND (COALESCE(f.processing_status, '') IN ('pending_event', 'event_failed') "
                "OR e.parse_success = FALSE)"
            )
    elif mode == "all":
        status_filter = (
            "(f.news_id IS NULL OR "
            "COALESCE(f.processing_status, '') = 'pending_event')"
        )
        if args.retry_failed:
            status_filter = (
                "(f.news_id IS NULL OR "
                "COALESCE(f.processing_status, '') = 'pending_event' "
                "OR COALESCE(f.processing_status, '') = 'event_failed' "
                "OR e.parse_success = FALSE)"
            )

    window_filter = ""
    quality_join = ""
    params: list[Any] = []
    if not args.disable_quality_label_gate:
        quality_join = """
            JOIN public.news_quality_labels q
              ON q.news_id = n.id
             AND q.is_good IS TRUE
             AND q.label_version = %s
        """
        params.append(args.quality_label_version)
    if not args.include_out_of_window:
        window_filter = "AND n.published_at BETWEEN %s AND %s"
        params.extend([start, end])
    params.append(limit)

    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT n.id,
                   COALESCE(n.title, '') AS title,
                   COALESCE(n.body, '') AS body,
                   n.url_hash,
                   n.published_at,
                   n.language,
                   n.region,
                   ms.domain AS source_domain
            FROM public.news n
            LEFT JOIN public.media_source ms ON ms.id = n.media_source_id
            LEFT JOIN public.news_l1_prep f ON f.news_id = n.id
            LEFT JOIN public.news_l1_event_extractions e ON e.news_id = n.id
            {quality_join}
            WHERE {status_filter}
              AND COALESCE(n.title, '') <> ''
              AND COALESCE(n.body, '') <> ''
              {window_filter}
            ORDER BY n.id
            LIMIT %s
            """,
            params,
        )
        columns = [desc[0] for desc in cur.description]
        rows = [dict(zip(columns, row)) for row in cur.fetchall()]
    conn.commit()
    return rows


async def extract_events(
    rows: list[dict[str, Any]],
    *,
    concurrency: int,
) -> dict[int, ExtractionResult]:
    import aiohttp

    sem = asyncio.Semaphore(max(1, concurrency))
    connector = aiohttp.TCPConnector(limit=max(2, concurrency + 4), ttl_dns_cache=300)
    results: dict[int, ExtractionResult] = {}
    async with aiohttp.ClientSession(connector=connector) as session:
        tasks = []
        for row in rows:
            text = build_embedding_text(row.get("title"), row.get("body"))
            tasks.append(
                asyncio.create_task(
                    extract_one(
                        session,
                        sem,
                        int(row["id"]),
                        text,
                        str(row.get("published_at") or "") if row.get("published_at") else None,
                    )
                )
            )
        for task in asyncio.as_completed(tasks):
            result = await task
            results[int(result.article_id)] = result
    return results


def build_feature_row(
    source: dict[str, Any],
    event_result: ExtractionResult | None,
    args: argparse.Namespace,
) -> dict[str, Any]:
    start, end = target_bounds(args)
    title = source.get("title") or ""
    body = source.get("body") or ""
    embedding_text = build_embedding_text(title, body)
    quality = text_quality_flag(title, body)
    published_raw, published_clean, in_window = clean_dt(source.get("published_at"), start, end)
    title_hash = sha256_text(normalize_space(title).lower())
    body_hash = sha256_text(normalize_space(body))
    dedupe_key = sha256_text(f"{title_hash or ''}:{body_hash or ''}")

    event = event_result.event if event_result else None
    parse_success = event_result.parse_success if event_result else None

    if args.skip_event_extraction or args.mode == "prep":
        status = "pending_event" if quality == "ok" else "skipped_low_quality"
    elif event and parse_success:
        status = "event_extracted"
    elif quality != "ok":
        status = "skipped_low_quality"
    else:
        status = "event_failed"

    return {
        "news_id": int(source["id"]),
        "source_domain": source.get("source_domain"),
        "url_hash": source.get("url_hash"),
        "title_hash": title_hash,
        "body_hash": body_hash,
        "dedupe_key": dedupe_key,
        "text_quality_flag": quality,
        "text_chars": len(body),
        "embedding_text_hash": sha256_text(embedding_text),
        "embedding_text_chars": len(embedding_text),
        "published_at_raw": published_raw,
        "published_at_clean": published_clean,
        "published_date": published_clean.date() if published_clean else None,
        "in_target_window": in_window,
        "processing_status": status,
        "processor_version": PROCESSOR_VERSION,
    }


def build_event_row(
    source: dict[str, Any],
    event_result: ExtractionResult | None,
) -> dict[str, Any] | None:
    if event_result is None:
        return None

    event = event_result.event
    canonical_initiator = canonical_entity(event.initiator) if event else None
    canonical_target = canonical_entity(event.target) if event else None
    pair_key = entity_pair_key(event.initiator or "", event.target or "") if event else None

    return {
        "news_id": int(source["id"]),
        "language": source.get("language"),
        "region": source.get("region"),
        "event_domain": event.domain if event else None,
        "event_family": event.event_family if event else None,
        "event_action": event.event_action if event else None,
        "initiator": event.initiator if event else None,
        "target": event.target if event else None,
        "location": event.location if event else None,
        "tone": event.tone if event else None,
        "canonical_initiator": canonical_initiator,
        "canonical_target": canonical_target,
        "entity_pair_key": pair_key,
        "parse_success": event_result.parse_success,
        "extraction_error": event_result.error,
        "raw_response": event_result.raw_response,
        "processor_version": PROCESSOR_VERSION,
    }


def upsert_features(conn: Any, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    columns = [
        "news_id",
        "source_domain",
        "url_hash",
        "title_hash",
        "body_hash",
        "dedupe_key",
        "text_quality_flag",
        "text_chars",
        "embedding_text_hash",
        "embedding_text_chars",
        "published_at_raw",
        "published_at_clean",
        "published_date",
        "in_target_window",
        "processing_status",
        "processor_version",
    ]
    values = [[row.get(column) for column in columns] for row in rows]
    assignments = ", ".join(
        f"{column} = EXCLUDED.{column}"
        for column in columns
        if column != "news_id"
    )
    sql = f"""
        INSERT INTO public.news_l1_prep ({", ".join(columns)})
        VALUES %s
        ON CONFLICT (news_id) DO UPDATE SET
            {assignments},
            processed_at = now(),
            updated_at = now()
    """
    with conn.cursor() as cur:
        execute_values(cur, sql, values, page_size=500)
    conn.commit()


def upsert_event_extractions(conn: Any, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    columns = [
        "news_id",
        "language",
        "region",
        "event_domain",
        "event_family",
        "event_action",
        "initiator",
        "target",
        "location",
        "tone",
        "canonical_initiator",
        "canonical_target",
        "entity_pair_key",
        "parse_success",
        "extraction_error",
        "raw_response",
        "processor_version",
    ]
    values = [[row.get(column) for column in columns] for row in rows]
    assignments = ", ".join(
        f"{column} = EXCLUDED.{column}"
        for column in columns
        if column != "news_id"
    )
    sql = f"""
        INSERT INTO public.news_l1_event_extractions ({", ".join(columns)})
        VALUES %s
        ON CONFLICT (news_id) DO UPDATE SET
            {assignments},
            updated_at = now()
    """
    with conn.cursor() as cur:
        execute_values(cur, sql, values, page_size=500)
    conn.commit()


def update_prep_statuses(conn: Any, statuses: dict[int, str]) -> None:
    if not statuses:
        return
    values = [
        (int(news_id), status, PROCESSOR_VERSION)
        for news_id, status in statuses.items()
    ]
    sql = """
        UPDATE public.news_l1_prep AS prep
        SET processing_status = updates.processing_status,
            processor_version = updates.processor_version,
            updated_at = now()
        FROM (VALUES %s) AS updates(news_id, processing_status, processor_version)
        WHERE prep.news_id = updates.news_id
    """
    with conn.cursor() as cur:
        execute_values(cur, sql, values, page_size=1000)
    conn.commit()


def count_status(conn: Any) -> dict[str, int]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT processing_status, count(*)
            FROM public.news_l1_prep
            GROUP BY processing_status
            ORDER BY processing_status
            """
        )
        rows = {str(status): int(count) for status, count in cur.fetchall()}
    conn.commit()
    return rows


def main() -> None:
    args = parse_args()
    if args.skip_event_extraction:
        args.mode = "prep"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    conn = connect(args)
    ensure_schema(conn)
    domain_gate = load_domain_gate(args) if args.mode in ("all", "extract") else None
    if args.mode == "extract" and domain_gate is not None:
        LOGGER.info("domain gate enabled threshold=%.3f model_dir=%s", args.domain_gate_threshold, MODEL_DIR)

    total = 0
    total_kept = 0
    total_gate_skipped = 0
    total_extracted = 0
    total_failed = 0
    empty_polls = 0
    started = time.time()
    try:
        while True:
            remaining = None if args.max_rows is None else max(0, args.max_rows - total)
            if remaining == 0:
                break
            rows = fetch_rows(conn, args, remaining=remaining)
            if not rows:
                conn.rollback()
                empty_polls += 1
                if args.poll_sec <= 0:
                    break
                if args.max_empty_polls and empty_polls >= args.max_empty_polls:
                    break
                LOGGER.info("no rows; sleeping %.1fs", args.poll_sec)
                time.sleep(args.poll_sec)
                continue

            empty_polls = 0
            if args.mode == "extract":
                scores = score_domain_gate(rows, domain_gate)
                kept_rows = [
                    row for row in rows
                    if scores.get(int(row["id"]), 0.0) >= args.domain_gate_threshold
                ]
                skipped_rows = [
                    row for row in rows
                    if scores.get(int(row["id"]), 0.0) < args.domain_gate_threshold
                ]
                update_prep_statuses(
                    conn,
                    {int(row["id"]): "skipped_domain_gate" for row in skipped_rows},
                )

                event_results: dict[int, ExtractionResult] = {}
                if kept_rows:
                    event_results = asyncio.run(
                        extract_events(kept_rows, concurrency=args.event_concurrency)
                    )
                    event_rows = [
                        event_row
                        for row in kept_rows
                        if (
                            event_row := build_event_row(
                                row,
                                event_results.get(int(row["id"])),
                            )
                        )
                    ]
                    upsert_event_extractions(conn, event_rows)

                    status_updates: dict[int, str] = {}
                    for row in kept_rows:
                        news_id = int(row["id"])
                        result = event_results.get(news_id)
                        if result and result.parse_success and result.event:
                            status_updates[news_id] = "event_extracted"
                        else:
                            status_updates[news_id] = "event_failed"
                    update_prep_statuses(conn, status_updates)

                    total_extracted += sum(1 for status in status_updates.values() if status == "event_extracted")
                    total_failed += sum(1 for status in status_updates.values() if status == "event_failed")

                total += len(rows)
                total_kept += len(kept_rows)
                total_gate_skipped += len(skipped_rows)
                if total % max(1, args.log_every) == 0 or args.max_rows is not None:
                    elapsed = time.time() - started
                    LOGGER.info(
                        "mode=extract scanned=%s kept=%s gate_skipped=%s extracted=%s failed=%s elapsed=%.1fs rate=%.2f rows/s status=%s",
                        total,
                        total_kept,
                        total_gate_skipped,
                        total_extracted,
                        total_failed,
                        elapsed,
                        total / max(elapsed, 1.0),
                        json.dumps(count_status(conn), ensure_ascii=False),
                    )
                continue

            event_results: dict[int, ExtractionResult] = {}
            if args.mode == "all":
                event_results = asyncio.run(
                    extract_events(rows, concurrency=args.event_concurrency)
                )

            feature_rows = [
                build_feature_row(row, event_results.get(int(row["id"])), args)
                for row in rows
            ]
            event_rows = [
                event_row
                for row in rows
                if (
                    event_row := build_event_row(
                        row,
                        event_results.get(int(row["id"])),
                    )
                )
            ]
            upsert_features(conn, feature_rows)
            upsert_event_extractions(conn, event_rows)
            total += len(feature_rows)
            if total % max(1, args.log_every) == 0 or args.max_rows is not None:
                elapsed = time.time() - started
                LOGGER.info(
                    "mode=%s processed=%s elapsed=%.1fs rate=%.2f rows/s status=%s",
                    args.mode,
                    total,
                    elapsed,
                    total / max(elapsed, 1.0),
                    json.dumps(count_status(conn), ensure_ascii=False),
                )
    finally:
        conn.close()

    elapsed = time.time() - started
    LOGGER.info("done processed=%s elapsed=%.1fs", total, elapsed)


if __name__ == "__main__":
    main()
