#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import psycopg2
from psycopg2.extras import execute_batch

from db_runtime_config import require_database_password


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_INPUT = PROJECT_ROOT / "data" / "historical_news" / "news_table_rows.jsonl"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Load normalized news rows into PostgreSQL news.public.news.")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--host", default="192.168.207.171")
    parser.add_argument("--port", type=int, default=54333)
    parser.add_argument("--user", default="postgres")
    parser.add_argument("--dbname", default="news")
    parser.add_argument("--batch-size", type=int, default=500)
    parser.add_argument("--limit", type=int, default=0)
    return parser.parse_args()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def ensure_media_source(cur: Any, domain: str, region: str | None) -> int:
    cur.execute(
        """
        insert into public.media_source(domain, region_code)
        values (%s, %s)
        on conflict (domain)
        do update set region_code = coalesce(public.media_source.region_code, excluded.region_code)
        returning id
        """,
        (domain, region),
    )
    return int(cur.fetchone()[0])


def chunked(rows: list[dict[str, Any]], size: int) -> list[list[dict[str, Any]]]:
    return [rows[i : i + size] for i in range(0, len(rows), size)]


def main() -> None:
    args = parse_args()
    rows = read_jsonl(args.input)
    if args.limit > 0:
        rows = rows[: args.limit]

    conn = psycopg2.connect(
        host=args.host,
        port=args.port,
        user=args.user,
        password=require_database_password(),
        dbname=args.dbname,
    )
    inserted = 0
    skipped = 0
    media_cache: dict[str, int] = {}
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute("select count(*) from public.news")
                before_count = int(cur.fetchone()[0])
                for batch in chunked(rows, max(1, args.batch_size)):
                    payload = []
                    for row in batch:
                        domain = str(row.get("media_source_domain") or "").strip()
                        if not domain:
                            skipped += 1
                            continue
                        media_source_id = media_cache.get(domain)
                        if media_source_id is None:
                            media_source_id = ensure_media_source(cur, domain, row.get("region"))
                            media_cache[domain] = media_source_id
                        payload.append(
                            (
                                str(row.get("title") or "").strip(),
                                row.get("body"),
                                row.get("url"),
                                row.get("url_hash"),
                                row.get("published_at"),
                                media_source_id,
                                row.get("language"),
                                row.get("region"),
                                row.get("author"),
                            )
                        )
                    execute_batch(
                        cur,
                        """
                        insert into public.news(
                            title,
                            body,
                            url,
                            url_hash,
                            published_at,
                            media_source_id,
                            language,
                            region,
                            author
                        )
                        values (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                        on conflict (url_hash) do nothing
                        """,
                        payload,
                        page_size=max(1, args.batch_size),
                    )
                cur.execute("select count(*) from public.news")
                after_count = int(cur.fetchone()[0])
                inserted = after_count - before_count
        print(f"input_rows={len(rows)} inserted={inserted} skipped={skipped}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
