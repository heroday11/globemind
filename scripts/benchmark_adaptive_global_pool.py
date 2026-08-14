#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
PYTHON_BIN = PROJECT_ROOT / ".env_torch" / "bin" / "python"
EXTRACTOR = PROJECT_ROOT / "scripts" / "adaptive_global_extractor.py"
DEFAULT_INPUT = PROJECT_ROOT / "data" / "historical_news" / "discovered_urls_sample.jsonl"
DEFAULT_JSON = PROJECT_ROOT / "data" / "historical_news" / "adaptive_benchmark.json"
DEFAULT_MD = PROJECT_ROOT / "docs" / "ADAPTIVE_BENCHMARK_REPORT.md"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Benchmark adaptive global extraction settings.")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--json-output", type=Path, default=DEFAULT_JSON)
    parser.add_argument("--md-output", type=Path, default=DEFAULT_MD)
    parser.add_argument("--proxy-pool", type=Path)
    parser.add_argument("--global-list", default="8,16,32,64")
    parser.add_argument("--per-domain-list", default="2,4")
    parser.add_argument("--limit", type=int, default=120)
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument("--base-delay-ms", type=int, default=0)
    parser.add_argument("--jitter-ms", type=int, default=150)
    parser.add_argument("--shuffle", action="store_true")
    return parser.parse_args()


def run_one(args: argparse.Namespace, global_concurrency: int, max_per_domain: int) -> dict:
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        out_path = tmpdir / "articles.jsonl"
        err_path = tmpdir / "errors.jsonl"
        stats_path = tmpdir / "stats.json"
        cmd = [
            str(PYTHON_BIN),
            str(EXTRACTOR),
            "--input",
            str(args.input),
            "--output",
            str(out_path),
            "--errors",
            str(err_path),
            "--stats",
            str(stats_path),
            "--limit",
            str(args.limit),
            "--global-concurrency",
            str(global_concurrency),
            "--max-per-domain",
            str(max_per_domain),
            "--timeout",
            str(args.timeout),
            "--base-delay-ms",
            str(args.base_delay_ms),
            "--jitter-ms",
            str(args.jitter_ms),
        ]
        if args.proxy_pool:
            cmd.extend(["--proxy-pool", str(args.proxy_pool)])
        if args.shuffle:
            cmd.append("--shuffle")
        subprocess.run(cmd, check=True, cwd=PROJECT_ROOT, capture_output=True)
        return json.loads(stats_path.read_text(encoding="utf-8"))


def write_markdown(path: Path, input_path: Path, runs: list[dict], best_run: dict) -> None:
    lines = [
        "# Adaptive Benchmark Report",
        "",
        f"- Input: [{input_path.name}]({input_path})",
        f"- Created at: `{datetime.now(timezone.utc).isoformat()}`",
        "",
        "## Results",
        "",
    ]
    for row in runs:
        lines.append(
            "- "
            f"`global={row['global_concurrency']}` "
            f"`per_domain={row['max_per_domain']}` "
            f"`success={row['successes']}/{row['rows']}` "
            f"`rate={row['successes_per_sec']}/s` "
            f"`min_rate={row['successes_per_min']}/min`"
        )
    lines.extend(
        [
            "",
            "## Best Setting",
            "",
            f"- `global_concurrency={best_run['global_concurrency']}`",
            f"- `max_per_domain={best_run['max_per_domain']}`",
            f"- Throughput: `{best_run['successes_per_min']} articles/min`",
            f"- Success rate: `{best_run['success_rate']}`",
        ]
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    args = parse_args()
    globals_ = [int(item.strip()) for item in args.global_list.split(",") if item.strip()]
    per_domains = [int(item.strip()) for item in args.per_domain_list.split(",") if item.strip()]
    runs = []
    for g in globals_:
        for p in per_domains:
            runs.append(run_one(args, g, p))
    best_run = max(runs, key=lambda row: (row["successes_per_sec"], row["success_rate"]))
    payload = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "input": str(args.input),
        "runs": runs,
        "best_run": best_run,
    }
    args.json_output.parent.mkdir(parents=True, exist_ok=True)
    args.json_output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_markdown(args.md_output, args.input, runs, best_run)
    print(f"wrote adaptive benchmark json to {args.json_output}")
    print(f"wrote adaptive benchmark markdown to {args.md_output}")


if __name__ == "__main__":
    main()
