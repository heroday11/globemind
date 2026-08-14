#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_INPUT = PROJECT_ROOT / "data" / "proxy_pool" / "proxy_pool_benchmark.json"
DEFAULT_OUTPUT = PROJECT_ROOT / "data" / "proxy_pool" / "proxy_pool_manifest_optimized.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Select a high-quality proxy manifest from benchmark results.")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--limit", type=int, default=24)
    parser.add_argument("--max-per-region", type=int, default=5)
    parser.add_argument("--min-speed-mbps", type=float, default=50.0)
    parser.add_argument("--min-availability", type=float, default=0.8)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    payload = json.loads(args.input.read_text(encoding="utf-8"))
    rows = payload["results"]
    candidates = [
        row
        for row in rows
        if row.get("latency_ok")
        and row.get("speed_ok")
        and float(row.get("speed_Mbps") or 0.0) >= args.min_speed_mbps
        and float(row.get("availability_ratio") or 0.0) >= args.min_availability
    ]
    candidates.sort(
        key=lambda item: (
            float(item.get("availability_ratio") or 0.0),
            float(item.get("score") or -1.0),
            float(item.get("speed_Mbps") or 0.0),
        ),
        reverse=True,
    )

    selected: list[dict] = []
    per_region: dict[str, int] = {}
    deferred: list[dict] = []
    for row in candidates:
        region = str(row.get("region") or "ZZ")
        if per_region.get(region, 0) >= max(1, args.max_per_region):
            deferred.append(row)
            continue
        selected.append(row)
        per_region[region] = per_region.get(region, 0) + 1
        if len(selected) >= max(1, args.limit):
            break

    if len(selected) < max(1, args.limit):
        for row in deferred:
            selected.append(row)
            if len(selected) >= max(1, args.limit):
                break

    manifest = [
        {
            "name": row["name"],
            "region": row["region"],
            "host": row["host"],
            "listen_port": row["listen_port"],
            "socks_url": row["socks_url"],
            "config_path": row["config_path"],
            "score": row["score"],
            "speed_Mbps": row["speed_Mbps"],
            "latency_sec": row["latency_sec"],
            "availability_ratio": row.get("availability_ratio"),
            "latency_ok_count": row.get("latency_ok_count"),
            "speed_ok_count": row.get("speed_ok_count"),
            "repeats": row.get("repeats"),
        }
        for row in selected
    ]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"selected {len(manifest)} proxies into {args.output}")
    for row in manifest:
        print(
            json.dumps(
                {
                    "region": row["region"],
                    "port": row["listen_port"],
                    "speed_Mbps": row["speed_Mbps"],
                    "latency_sec": row["latency_sec"],
                    "availability_ratio": row.get("availability_ratio"),
                    "score": row["score"],
                    "name": row["name"],
                },
                ensure_ascii=False,
            )
        )


if __name__ == "__main__":
    main()
