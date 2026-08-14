#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sqlite3
import time
from collections import Counter
from hashlib import md5
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parent.parent
JOB_DIR = PROJECT_ROOT / "data" / "historical_news" / "jobs" / "wave1_1y_prod_20260621"
DEFAULT_INPUT = JOB_DIR / "wave1_discovered_urls_merged_pruned.jsonl"
DEFAULT_PROGRESS = JOB_DIR / "wave1_articles_merged_progress.json"
DEFAULT_OUTPUT_DIR = JOB_DIR / "wave1_remaining_shards"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build stable shard input files for remaining Wave1 URLs.")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--progress", type=Path, default=DEFAULT_PROGRESS)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--shards", type=int, default=2)
    parser.add_argument("--max-rows", type=int, default=0)
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def row_key(row: dict[str, Any]) -> str:
    return str(row.get("request_url") or row.get("url") or "")


def load_processed_keys(progress_path: Path) -> set[str]:
    progress = json.loads(progress_path.read_text(encoding="utf-8"))
    index_path = progress.get("resume_index_path")
    if not index_path:
        raise RuntimeError(f"progress file has no resume_index_path: {progress_path}")
    conn = sqlite3.connect(str(index_path))
    try:
        return {str(row[0]) for row in conn.execute("SELECT key FROM processed_keys")}
    finally:
        conn.close()


def shard_for_key(key: str, shard_count: int) -> int:
    return int(md5(key.encode("utf-8")).hexdigest(), 16) % shard_count


def main() -> None:
    args = parse_args()
    if args.shards < 1:
        raise SystemExit("--shards must be >= 1")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    input_dir = args.output_dir / "inputs"
    input_dir.mkdir(parents=True, exist_ok=True)
    shard_paths = [input_dir / f"shard_{idx}.jsonl" for idx in range(args.shards)]
    if not args.overwrite:
        existing = [str(path) for path in shard_paths if path.exists()]
        if existing:
            raise SystemExit(f"refusing to overwrite existing shard files; pass --overwrite: {existing[:3]}")

    processed = load_processed_keys(args.progress)
    handles = [path.open("w", encoding="utf-8") for path in shard_paths]
    shard_counts = [0 for _ in shard_paths]
    domain_counts: Counter[str] = Counter()
    raw_rows = 0
    skipped_processed = 0
    skipped_invalid = 0
    kept = 0

    try:
        with args.input.open("r", encoding="utf-8") as src:
            for line in src:
                raw_rows += 1
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    skipped_invalid += 1
                    continue
                key = row_key(row)
                if not key:
                    skipped_invalid += 1
                    continue
                if key in processed:
                    skipped_processed += 1
                    continue
                shard = shard_for_key(key, args.shards)
                handles[shard].write(json.dumps(row, ensure_ascii=False) + "\n")
                shard_counts[shard] += 1
                domain_counts[str(row.get("domain") or "")] += 1
                kept += 1
                if args.max_rows > 0 and kept >= args.max_rows:
                    break
    finally:
        for handle in handles:
            handle.close()

    manifest = {
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "input": str(args.input),
        "progress": str(args.progress),
        "output_dir": str(args.output_dir),
        "shards": args.shards,
        "shard_inputs": [str(path) for path in shard_paths],
        "shard_counts": shard_counts,
        "raw_rows_scanned": raw_rows,
        "processed_index_rows": len(processed),
        "skipped_processed": skipped_processed,
        "skipped_invalid": skipped_invalid,
        "remaining_rows_written": kept,
        "top_domains": domain_counts.most_common(20),
    }
    (args.output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
