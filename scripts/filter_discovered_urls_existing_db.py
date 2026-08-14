#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

import psycopg2
from db_runtime_config import (
    require_database_password,
    validate_database_transport,
    validate_loader_database_role,
)


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Filter discovered URL rows already present in public.news.")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--stats", type=Path)
    parser.add_argument("--batch-size", type=int, default=10000)
    parser.add_argument("--host", default="192.168.207.171")
    parser.add_argument("--port", type=int, default=54333)
    parser.add_argument("--user", default="wave1_loader")
    parser.add_argument("--dbname", default="news")
    parser.add_argument("--sslmode", choices=("verify-full", "require", "disable"), required=True)
    parser.add_argument("--allow-private-scram-transport", action="store_true")
    parser.add_argument("--allow-legacy-postgres-role", action="store_true")
    return parser.parse_args(list(argv) if argv is not None else None)


def validate_args(args: argparse.Namespace) -> str:
    validate_loader_database_role(
        args.user,
        allow_legacy_postgres_role=args.allow_legacy_postgres_role,
    )
    return validate_database_transport(
        args.host,
        args.sslmode,
        allow_private_scram_transport=args.allow_private_scram_transport,
    )


def url_hash(url: str) -> str:
    return hashlib.md5(url.encode("utf-8")).hexdigest()


def existing_hashes(cur: Any, hashes: list[str]) -> set[str]:
    if not hashes:
        return set()
    cur.execute("select url_hash from public.news where url_hash = any(%s)", (hashes,))
    return {str(row[0]) for row in cur.fetchall()}


def flush_batch(
    cur: Any,
    dst: Any,
    rows: list[tuple[dict[str, Any], str]],
    by_site_kept: Counter[str],
    by_site_existing: Counter[str],
) -> tuple[int, int]:
    hashes = [digest for _row, digest in rows]
    existing = existing_hashes(cur, hashes)
    kept = 0
    skipped_existing = 0
    for row, digest in rows:
        site_id = str(row.get("site_id") or "__missing_site__")
        if digest in existing:
            skipped_existing += 1
            by_site_existing[site_id] += 1
            continue
        dst.write(json.dumps(row, ensure_ascii=False) + "\n")
        kept += 1
        by_site_kept[site_id] += 1
    return kept, skipped_existing


def main() -> None:
    args = parse_args()
    sslmode = validate_args(args)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if args.stats:
        args.stats.parent.mkdir(parents=True, exist_ok=True)

    conn = psycopg2.connect(
        host=args.host,
        port=args.port,
        user=args.user,
        password=require_database_password(require_file=True),
        dbname=args.dbname,
        sslmode=sslmode,
    )
    input_rows = 0
    kept = 0
    skipped_existing_db = 0
    skipped_duplicate_input = 0
    skipped_invalid = 0
    seen_input_hashes: set[str] = set()
    by_site_kept: Counter[str] = Counter()
    by_site_existing: Counter[str] = Counter()
    by_site_duplicate: Counter[str] = Counter()

    try:
        with conn.cursor() as cur, args.input.open("r", encoding="utf-8") as src, args.output.open("w", encoding="utf-8") as dst:
            batch: list[tuple[dict[str, Any], str]] = []
            for line in src:
                text = line.strip()
                if not text:
                    continue
                input_rows += 1
                try:
                    row = json.loads(text)
                except json.JSONDecodeError:
                    skipped_invalid += 1
                    continue
                url = str(row.get("url") or "").strip()
                if not url:
                    skipped_invalid += 1
                    continue
                digest = url_hash(url)
                site_id = str(row.get("site_id") or "__missing_site__")
                if digest in seen_input_hashes:
                    skipped_duplicate_input += 1
                    by_site_duplicate[site_id] += 1
                    continue
                seen_input_hashes.add(digest)
                batch.append((row, digest))
                if len(batch) >= max(1, args.batch_size):
                    batch_kept, batch_existing = flush_batch(cur, dst, batch, by_site_kept, by_site_existing)
                    kept += batch_kept
                    skipped_existing_db += batch_existing
                    batch.clear()
            if batch:
                batch_kept, batch_existing = flush_batch(cur, dst, batch, by_site_kept, by_site_existing)
                kept += batch_kept
                skipped_existing_db += batch_existing
    finally:
        conn.close()

    payload = {
        "input": str(args.input),
        "output": str(args.output),
        "input_rows": input_rows,
        "kept": kept,
        "skipped_existing_db": skipped_existing_db,
        "skipped_duplicate_input": skipped_duplicate_input,
        "skipped_invalid": skipped_invalid,
        "removed_pct": round(((skipped_existing_db + skipped_duplicate_input + skipped_invalid) / max(1, input_rows)) * 100, 2),
        "top_kept_sites": by_site_kept.most_common(20),
        "top_existing_db_sites": by_site_existing.most_common(20),
        "top_duplicate_input_sites": by_site_duplicate.most_common(20),
    }
    if args.stats:
        args.stats.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
