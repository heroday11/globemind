#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_INPUT_DIR = PROJECT_ROOT / "data" / "historical_news" / "wave1"
DEFAULT_OUTPUT = PROJECT_ROOT / "data" / "historical_news" / "wave1_discovered_urls_merged.jsonl"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Merge discovered per-site URL queues into one global queue.")
    parser.add_argument("--input-dir", type=Path, default=DEFAULT_INPUT_DIR)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    rows: list[dict] = []
    for path in sorted(args.input_dir.rglob("*_urls_*.jsonl")):
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    rows.append(json.loads(line))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(f"wrote {len(rows)} rows to {args.output}")


if __name__ == "__main__":
    main()
