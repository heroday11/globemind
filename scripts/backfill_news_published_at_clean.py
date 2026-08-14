#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import time
from collections import Counter
from pathlib import Path
from typing import Any

import psycopg2
from psycopg2.extras import execute_values

from news_date_cleaning import body_lead_date, clean_published_at
from db_runtime_config import require_database_password


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_INPUT = (
    PROJECT_ROOT
    / "data"
    / "historical_news"
    / "jobs"
    / "wave1_1y_prod_20260621"
    / "wave1_articles_merged.jsonl"
)
BODY_SOURCES = {"body_byline", "body_label", "body_lead"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Recompute and backfill public.news.published_at from raw crawl rows."
    )
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--apply", action="store_true", help="Actually update public.news. Default is dry-run.")
    parser.add_argument("--batch-size", type=int, default=5000)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument(
        "--only-suspicious",
        action="store_true",
        help="Only update rows whose current DB date is null, before 1800, or more than 3 days in the future.",
    )
    parser.add_argument(
        "--source",
        action="append",
        default=[],
        help="Only keep clean-date candidates from these sources, e.g. body_byline, body_label, url, lastmod.",
    )
    parser.add_argument("--min-confidence", type=int, default=0)
    parser.add_argument("--host", default="192.168.207.171")
    parser.add_argument("--port", type=int, default=54333)
    parser.add_argument("--user", default="postgres")
    parser.add_argument("--dbname", default="news")
    return parser.parse_args()


def final_url(row: dict[str, Any]) -> str:
    return (
        str(row.get("response_url") or "").strip()
        or str(row.get("request_url") or "").strip()
        or str(row.get("url") or "").strip()
    )


def url_hash(url: str) -> str:
    return hashlib.md5(url.encode("utf-8")).hexdigest()


def iter_updates(
    path: Path,
    limit: int,
    hash_filter: set[str] | None = None,
    source_filter: set[str] | None = None,
    min_confidence: int = 0,
) -> Any:
    seen = 0
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            if limit and seen >= limit:
                break
            line = line.strip()
            if not line:
                continue
            seen += 1
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            url = final_url(row)
            if not url:
                continue
            digest = url_hash(url)
            if hash_filter is not None and digest not in hash_filter:
                continue
            if source_filter and source_filter.issubset(BODY_SOURCES):
                body_candidate = body_lead_date(row.get("title"), row.get("body"))
                if body_candidate is None or body_candidate.source not in source_filter:
                    continue
            result = clean_published_at(row)
            if not result.published_at:
                continue
            if source_filter and result.source not in source_filter:
                continue
            if result.confidence < min_confidence:
                continue
            yield (
                digest,
                result.published_at,
                result.source,
                result.confidence,
            )


def connect(args: argparse.Namespace) -> Any:
    return psycopg2.connect(
        host=args.host,
        port=args.port,
        user=args.user,
        password=require_database_password(),
        dbname=args.dbname,
    )


def load_suspicious_hashes(conn: Any) -> set[str]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT url_hash
            FROM public.news
            WHERE url_hash IS NOT NULL
              AND (
                published_at IS NULL
                OR published_at < timestamp '1800-01-01'
                OR published_at > now() + interval '3 days'
              )
            """
        )
        return {str(row[0]) for row in cur.fetchall() if row[0]}


def flush_batch(conn: Any, batch: list[tuple[str, Any, str, int]], *, apply: bool, only_suspicious: bool) -> dict[str, int]:
    if not batch:
        return {"matched": 0, "changed": 0}

    suspicious_sql = ""
    if only_suspicious:
        suspicious_sql = """
          AND (
            n.published_at IS NULL
            OR n.published_at < timestamp '1800-01-01'
            OR n.published_at > now() + interval '3 days'
          )
        """

    if not apply:
        with conn.cursor() as cur:
            execute_values(
                cur,
                f"""
                WITH incoming(url_hash, clean_published_at, source, confidence) AS (VALUES %s)
                SELECT
                  count(*) AS matched,
                  count(*) FILTER (
                    WHERE (
                      n.published_at IS NULL
                      OR n.published_at::date IS DISTINCT FROM incoming.clean_published_at::date
                    )
                    {suspicious_sql}
                  ) AS changed
                FROM incoming
                JOIN public.news n ON n.url_hash = incoming.url_hash
                """,
                batch,
                template="(%s, %s::timestamptz, %s, %s)",
                page_size=len(batch),
            )
            matched, changed = cur.fetchone()
        return {"matched": int(matched or 0), "changed": int(changed or 0)}

    with conn:
        with conn.cursor() as cur:
            execute_values(
                cur,
                f"""
                WITH incoming(url_hash, clean_published_at, source, confidence) AS (VALUES %s),
                updated AS (
                  UPDATE public.news n
                  SET published_at = incoming.clean_published_at
                  FROM incoming
                  WHERE n.url_hash = incoming.url_hash
                    AND (
                      n.published_at IS NULL
                      OR n.published_at::date IS DISTINCT FROM incoming.clean_published_at::date
                    )
                    {suspicious_sql}
                  RETURNING n.id
                )
                SELECT
                  (SELECT count(*) FROM incoming JOIN public.news n ON n.url_hash = incoming.url_hash) AS matched,
                  (SELECT count(*) FROM updated) AS changed
                """,
                batch,
                template="(%s, %s::timestamptz, %s, %s)",
                page_size=len(batch),
            )
            matched, changed = cur.fetchone()
    return {"matched": int(matched or 0), "changed": int(changed or 0)}


def main() -> None:
    args = parse_args()
    conn = connect(args)
    totals = Counter()
    source_counts: Counter[str] = Counter()
    t0 = time.time()
    batch: list[tuple[str, Any, str, int]] = []
    hash_filter = load_suspicious_hashes(conn) if args.only_suspicious else None
    source_filter = set(args.source) if args.source else None
    if hash_filter is not None:
        print(f"loaded suspicious url_hashes={len(hash_filter)}", flush=True)
    try:
        for item in iter_updates(
            args.input,
            args.limit,
            hash_filter=hash_filter,
            source_filter=source_filter,
            min_confidence=args.min_confidence,
        ):
            batch.append(item)
            source_counts[item[2]] += 1
            totals["candidate_rows"] += 1
            if len(batch) >= max(1, args.batch_size):
                result = flush_batch(conn, batch, apply=args.apply, only_suspicious=args.only_suspicious)
                totals.update(result)
                batch.clear()
                if totals["candidate_rows"] % (args.batch_size * 10) == 0:
                    print(dict(totals), flush=True)
        result = flush_batch(conn, batch, apply=args.apply, only_suspicious=args.only_suspicious)
        totals.update(result)
    finally:
        conn.close()

    print(
        json.dumps(
            {
                "mode": "apply" if args.apply else "dry_run",
                "only_suspicious": args.only_suspicious,
                "source_filter": sorted(source_filter) if source_filter else [],
                "min_confidence": args.min_confidence,
                "elapsed_sec": round(time.time() - t0, 1),
                "totals": dict(totals),
                "source_counts": source_counts.most_common(),
            },
            ensure_ascii=False,
            indent=2,
            default=str,
        )
    )


if __name__ == "__main__":
    main()
