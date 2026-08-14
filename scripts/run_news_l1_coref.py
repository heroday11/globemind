#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
from psycopg2.extras import RealDictCursor, execute_values

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from core_pipeline.event_coref_cluster import build_event_coreference_with_embeddings
from core_pipeline.event_extract_v11 import Event, ExtractionResult
from scripts.ensure_news_l1_infra import add_db_args, connect, ensure_news_l1_infra

LOGGER = logging.getLogger("run_news_l1_coref")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run L1 event coreference from news_l1_event_extractions + news_embeddings."
    )
    add_db_args(parser)
    parser.add_argument("--run-id")
    parser.add_argument("--max-rows", type=int, default=50000)
    parser.add_argument("--target-start")
    parser.add_argument("--target-end")
    parser.add_argument("--include-general-news", action="store_true")
    parser.add_argument("--clear-existing", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--body-chars", type=int, default=3500)
    return parser.parse_args()


def parse_dt(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    text = str(value).strip().replace("Z", "+00:00")
    if not text:
        return None
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        try:
            return datetime.fromisoformat(text.replace(" ", "T"))
        except ValueError:
            return None


def decode_embedding(raw: Any) -> np.ndarray | None:
    if raw is None:
        return None
    if isinstance(raw, memoryview):
        raw = bytes(raw)
    if isinstance(raw, bytes):
        try:
            raw = json.loads(raw.decode())
        except Exception:
            raw = np.frombuffer(raw, dtype=np.float32)
    elif isinstance(raw, str):
        raw = json.loads(raw)
    arr = np.asarray(raw, dtype=np.float32)
    if arr.ndim != 1 or arr.shape[0] == 0:
        return None
    norm = float(np.linalg.norm(arr))
    if norm > 1e-12:
        arr = arr / norm
    return arr.astype(np.float32)


def load_rows(conn: Any, args: argparse.Namespace) -> tuple[list[ExtractionResult], dict[int, np.ndarray], dict[int, str], dict[int, str]]:
    filters = [
        "e.parse_success IS TRUE",
        "ne.embedding IS NOT NULL",
    ]
    params: list[Any] = []
    if not args.include_general_news:
        filters.append("e.event_domain = 'political'")
    if args.target_start:
        filters.append("COALESCE(n.published_at, p.published_at_clean) >= %s")
        params.append(args.target_start)
    if args.target_end:
        filters.append("COALESCE(n.published_at, p.published_at_clean) <= %s")
        params.append(args.target_end)
    if args.max_rows:
        params.append(args.max_rows)
        limit_sql = "LIMIT %s"
    else:
        limit_sql = ""

    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            f"""
            SELECT e.news_id,
                   COALESCE(n.title, '') AS title,
                   LEFT(COALESCE(n.body, ''), %s) AS body,
                   COALESCE(n.published_at, p.published_at_clean) AS published_at,
                   e.event_domain,
                   e.event_family,
                   e.event_action,
                   e.initiator,
                   e.target,
                   e.location,
                   e.tone,
                   ne.embedding
            FROM public.news_l1_event_extractions AS e
            JOIN public.news_l1_prep AS p ON p.news_id = e.news_id
            JOIN public.news AS n ON n.id = e.news_id
            JOIN public.news_embeddings AS ne ON ne.news_id = e.news_id
            WHERE {" AND ".join(filters)}
            ORDER BY COALESCE(n.published_at, p.published_at_clean), e.news_id
            {limit_sql}
            """,
            [args.body_chars, *params],
        )
        rows = [dict(row) for row in cur.fetchall()]

    results: list[ExtractionResult] = []
    embeddings: dict[int, np.ndarray] = {}
    bodies: dict[int, str] = {}
    titles: dict[int, str] = {}

    for row in rows:
        news_id = int(row["news_id"])
        event_family = (row.get("event_family") or "other").strip() or "other"
        event_action = (row.get("event_action") or "other").strip() or "other"
        event_domain = (row.get("event_domain") or "political").strip() or "political"
        event = Event(
            domain=event_domain,
            event_type=event_family,
            event_family=event_family,
            event_action=event_action,
            initiator=row.get("initiator"),
            target=row.get("target"),
            location=row.get("location"),
            tone=(row.get("tone") or "neutral"),
        )
        published_at = row.get("published_at")
        dt = parse_dt(published_at)
        results.append(
            ExtractionResult(
                article_id=news_id,
                published_at=dt.isoformat() if dt else (str(published_at) if published_at else None),
                event=event,
                raw_response="",
                parse_success=True,
            )
        )
        vec = decode_embedding(row.get("embedding"))
        if vec is not None:
            embeddings[news_id] = vec
        titles[news_id] = row.get("title") or ""
        bodies[news_id] = row.get("body") or ""

    results = [r for r in results if r.article_id in embeddings]
    return results, embeddings, titles, bodies


def mode(values: list[str | None], default: str | None = None) -> str | None:
    clean = [v for v in values if v]
    if not clean:
        return default
    return Counter(clean).most_common(1)[0][0]


def stable_cluster_id(run_id: str, article_ids: list[int]) -> str:
    payload = ",".join(str(x) for x in sorted(article_ids))
    digest = hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]
    return f"{run_id}_{digest}"


def write_clusters(
    conn: Any,
    clusters: dict[str, list[int]],
    results: list[ExtractionResult],
    titles: dict[int, str],
    *,
    run_id: str,
    clear_existing: bool,
) -> None:
    lookup = {r.article_id: r for r in results}
    with conn.cursor() as cur:
        if clear_existing:
            cur.execute("TRUNCATE public.event_coref_members, public.event_coref_clusters CASCADE")
        else:
            cur.execute("DELETE FROM public.event_coref_clusters WHERE run_id = %s", (run_id,))
    conn.commit()

    cluster_values = []
    member_values = []
    for _, article_ids in sorted(clusters.items(), key=lambda item: (-len(item[1]), min(item[1]))):
        article_ids = sorted(set(int(aid) for aid in article_ids))
        rows = [lookup[aid] for aid in article_ids if aid in lookup and lookup[aid].event]
        if not rows:
            continue
        events = [row.event for row in rows if row.event]
        dates = [parse_dt(row.published_at) for row in rows if row.published_at]
        dates = [dt for dt in dates if dt is not None]
        cluster_id = stable_cluster_id(run_id, article_ids)
        event_family = mode([e.event_family for e in events], "other")
        event_action = mode([e.event_action for e in events], "other")
        event_domain = mode([e.domain for e in events], "political")
        initiator = mode([e.initiator for e in events], None)
        target = mode([e.target for e in events], None)
        location = mode([e.location for e in events], None)
        tone = mode([e.tone for e in events], "neutral")
        title = titles.get(article_ids[0], "")[:200] if article_ids else ""
        quality = "singleton" if len(article_ids) == 1 else "candidate_event"
        cluster_values.append(
            (
                cluster_id,
                run_id,
                len(article_ids),
                event_domain,
                event_family,
                event_family,
                event_action,
                initiator,
                target,
                location,
                tone,
                event_action,
                min(dates).date() if dates else None,
                max(dates).date() if dates else None,
                quality,
                title,
            )
        )
        for row in rows:
            ev = row.event
            member_values.append(
                (
                    cluster_id,
                    run_id,
                    row.article_id,
                    ev.domain,
                    ev.event_family,
                    ev.event_family,
                    ev.event_action,
                    ev.initiator,
                    ev.target,
                    ev.trigger,
                    parse_dt(row.published_at),
                    1.0,
            )
        )

    if not cluster_values:
        return

    with conn.cursor() as cur:
        execute_values(
            cur,
            """
            INSERT INTO public.event_coref_clusters (
                cluster_id, run_id, article_count, event_domain, event_type,
                event_family, event_action, initiator, target, location, tone,
                dominant_trigger, start_date, end_date, cluster_quality, title
            )
            VALUES %s
            ON CONFLICT (cluster_id) DO UPDATE SET
                article_count = EXCLUDED.article_count,
                event_domain = EXCLUDED.event_domain,
                event_type = EXCLUDED.event_type,
                event_family = EXCLUDED.event_family,
                event_action = EXCLUDED.event_action,
                initiator = EXCLUDED.initiator,
                target = EXCLUDED.target,
                location = EXCLUDED.location,
                tone = EXCLUDED.tone,
                dominant_trigger = EXCLUDED.dominant_trigger,
                start_date = EXCLUDED.start_date,
                end_date = EXCLUDED.end_date,
                cluster_quality = EXCLUDED.cluster_quality,
                title = EXCLUDED.title,
                updated_at = now()
            """,
            cluster_values,
            page_size=500,
        )
        execute_values(
            cur,
            """
            INSERT INTO public.event_coref_members (
                cluster_id, run_id, news_id, event_domain, event_type,
                event_family, event_action, initiator, target, trigger,
                published_at, membership_score
            )
            VALUES %s
            ON CONFLICT (cluster_id, news_id) DO UPDATE SET
                run_id = EXCLUDED.run_id,
                event_domain = EXCLUDED.event_domain,
                event_type = EXCLUDED.event_type,
                event_family = EXCLUDED.event_family,
                event_action = EXCLUDED.event_action,
                initiator = EXCLUDED.initiator,
                target = EXCLUDED.target,
                trigger = EXCLUDED.trigger,
                published_at = EXCLUDED.published_at,
                membership_score = EXCLUDED.membership_score
            """,
            member_values,
            page_size=1000,
        )
    conn.commit()


def main() -> None:
    args = parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    if not args.run_id:
        args.run_id = datetime.now(timezone.utc).strftime("l1_%Y%m%dT%H%M%SZ")

    conn = connect(args)
    ensure_news_l1_infra(conn)
    started = time.time()
    try:
        results, embeddings, titles, bodies = load_rows(conn, args)
        LOGGER.info("loaded rows=%d embeddings=%d run_id=%s", len(results), len(embeddings), args.run_id)
        if not results:
            return
        clusters = build_event_coreference_with_embeddings(
            results,
            article_bodies=bodies,
            article_titles=titles,
            embeddings=embeddings,
        )
        sizes = [len(v) for v in clusters.values()]
        LOGGER.info(
            "clusters=%d non_singleton=%d articles=%d max_cluster=%d elapsed=%.1fs",
            len(clusters),
            sum(1 for size in sizes if size > 1),
            sum(sizes),
            max(sizes) if sizes else 0,
            time.time() - started,
        )
        if not args.dry_run:
            write_clusters(
                conn,
                clusters,
                results,
                titles,
                run_id=args.run_id,
                clear_existing=args.clear_existing,
            )
            LOGGER.info("wrote clusters and members for run_id=%s", args.run_id)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
