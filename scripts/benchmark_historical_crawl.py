#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import random
import statistics
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Any

from extract_historical_articles import fetch_and_extract_with_metrics, read_jsonl


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_INPUT = PROJECT_ROOT / "data" / "historical_news" / "ap_world_2026-06-20_urls.jsonl"
DEFAULT_JSON = PROJECT_ROOT / "data" / "historical_news" / "historical_benchmark_report.json"
DEFAULT_MD = PROJECT_ROOT / "docs" / "HISTORICAL_BENCHMARK_REPORT.md"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Benchmark concurrent historical news extraction throughput.")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--json-output", type=Path, default=DEFAULT_JSON)
    parser.add_argument("--md-output", type=Path, default=DEFAULT_MD)
    parser.add_argument("--workers-list", default="4,8,16,32,64")
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument("--limit", type=int, default=240)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--site-id", action="append", default=[])
    return parser.parse_args()


def hardware_snapshot() -> dict[str, Any]:
    mem_total_gib = None
    mem_available_gib = None
    try:
        with open("/proc/meminfo", "r", encoding="utf-8") as fh:
            meminfo = fh.read().splitlines()
        values = {}
        for line in meminfo:
            key, value = line.split(":", 1)
            values[key] = value.strip()
        if "MemTotal" in values:
            mem_total_kib = int(values["MemTotal"].split()[0])
            mem_total_gib = round(mem_total_kib / 1024 / 1024, 1)
        if "MemAvailable" in values:
            mem_avail_kib = int(values["MemAvailable"].split()[0])
            mem_available_gib = round(mem_avail_kib / 1024 / 1024, 1)
    except Exception:
        pass

    st = os.statvfs("/")
    disk_free_gib = round((st.f_bavail * st.f_frsize) / 1024 / 1024 / 1024, 1)
    return {
        "cpu_count": os.cpu_count(),
        "mem_total_gib": mem_total_gib,
        "mem_available_gib": mem_available_gib,
        "disk_free_gib": disk_free_gib,
    }


def percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = max(0, min(len(ordered) - 1, int(round((len(ordered) - 1) * p))))
    return ordered[idx]


def choose_rows(rows: list[dict[str, Any]], limit: int, seed: int) -> list[dict[str, Any]]:
    if limit <= 0 or len(rows) <= limit:
        return rows
    rng = random.Random(seed)
    picked = rows[:]
    rng.shuffle(picked)
    return picked[:limit]


def run_one_level(rows: list[dict[str, Any]], workers: int, timeout: float) -> dict[str, Any]:
    started_at = datetime.now(timezone.utc).isoformat()
    t0 = perf_counter()
    metrics_rows: list[dict[str, Any]] = []
    error_counter: Counter[str] = Counter()

    with ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
        futures = [executor.submit(fetch_and_extract_with_metrics, row, timeout) for row in rows]
        for future in as_completed(futures):
            _article, _error, metrics = future.result()
            metrics_rows.append(metrics)
            if not metrics["ok"]:
                error_counter[metrics["error"]] += 1

    elapsed = perf_counter() - t0
    ok_rows = [row for row in metrics_rows if row["ok"]]
    latencies = [float(row["elapsed_sec"]) for row in metrics_rows]
    total_download = sum(int(row["download_bytes"]) for row in metrics_rows)
    total_body_chars = sum(int(row["body_chars"]) for row in metrics_rows)

    return {
        "workers": workers,
        "started_at": started_at,
        "elapsed_sec": round(elapsed, 3),
        "requests": len(metrics_rows),
        "successes": len(ok_rows),
        "failures": len(metrics_rows) - len(ok_rows),
        "success_rate": round(len(ok_rows) / len(metrics_rows), 4) if metrics_rows else 0.0,
        "requests_per_sec": round(len(metrics_rows) / elapsed, 3) if elapsed else 0.0,
        "successes_per_sec": round(len(ok_rows) / elapsed, 3) if elapsed else 0.0,
        "successes_per_min": round((len(ok_rows) / elapsed) * 60, 1) if elapsed else 0.0,
        "download_mib": round(total_download / 1024 / 1024, 2),
        "download_mib_per_sec": round((total_download / 1024 / 1024) / elapsed, 2) if elapsed else 0.0,
        "avg_latency_sec": round(statistics.mean(latencies), 3) if latencies else 0.0,
        "p50_latency_sec": round(percentile(latencies, 0.50), 3),
        "p95_latency_sec": round(percentile(latencies, 0.95), 3),
        "avg_body_chars": round(total_body_chars / len(ok_rows), 1) if ok_rows else 0.0,
        "top_errors": error_counter.most_common(5),
    }


def estimate_wave1_time(best_run: dict[str, Any], total_articles: int) -> dict[str, Any]:
    rate = float(best_run.get("successes_per_sec") or 0.0)
    if rate <= 0:
        return {"estimated_hours": None, "estimated_days": None}
    total_seconds = total_articles / rate
    return {
        "estimated_hours": round(total_seconds / 3600, 1),
        "estimated_days": round(total_seconds / 86400, 1),
    }


def write_markdown(
    path: Path,
    input_path: Path,
    sampled_rows: list[dict[str, Any]],
    hardware: dict[str, Any],
    run_rows: list[dict[str, Any]],
    estimate: dict[str, Any],
) -> None:
    best_run = max(run_rows, key=lambda row: (row["successes_per_sec"], row["success_rate"]))
    lines = [
        "# Historical Benchmark Report",
        "",
        f"- Input: [{input_path.name}]({input_path})",
        f"- Sample size: `{len(sampled_rows)}` URLs",
        f"- Hardware: `CPU {hardware.get('cpu_count')}`, `RAM {hardware.get('mem_total_gib')} GiB`, `Free disk {hardware.get('disk_free_gib')} GiB`",
        "",
        "## Results",
        "",
    ]
    for row in run_rows:
        lines.append(
            "- "
            f"`workers={row['workers']}` "
            f"`success={row['successes']}/{row['requests']}` "
            f"`rate={row['successes_per_sec']}/s` "
            f"`min_rate={row['successes_per_min']}/min` "
            f"`download={row['download_mib_per_sec']} MiB/s` "
            f"`p95={row['p95_latency_sec']}s`"
        )
    lines.extend(
        [
            "",
            "## Best Observed Setting",
            "",
            f"- Workers: `{best_run['workers']}`",
            f"- Success throughput: `{best_run['successes_per_sec']}` articles/s",
            f"- Success throughput: `{best_run['successes_per_min']}` articles/min",
            f"- Success rate: `{best_run['success_rate']}`",
            "",
            "## Rough Forecast",
            "",
        ]
    )
    if estimate["estimated_hours"] is None:
        lines.append("- Forecast unavailable because no successful benchmark run was recorded.")
    else:
        lines.append(f"- If the large-scale crawl sustains the benchmark rate, `1,000,000` articles would take about `{estimate['estimated_days']}` days (`{estimate['estimated_hours']}` hours).")
        lines.append("- Real full-run time will be slower than this benchmark because discovery, retries, per-site throttling, and difficult sites add overhead.")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    args = parse_args()
    rows = read_jsonl(args.input)
    if args.site_id:
        keep = set(args.site_id)
        rows = [row for row in rows if row.get("site_id") in keep]
    sampled_rows = choose_rows(rows, args.limit, args.seed)
    workers_list = [int(item.strip()) for item in args.workers_list.split(",") if item.strip()]
    hardware = hardware_snapshot()

    run_rows = [run_one_level(sampled_rows, workers, args.timeout) for workers in workers_list]
    best_run = max(run_rows, key=lambda row: (row["successes_per_sec"], row["success_rate"]))
    estimate = estimate_wave1_time(best_run, total_articles=1_000_000)

    payload = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "input": str(args.input),
        "sample_size": len(sampled_rows),
        "hardware": hardware,
        "runs": run_rows,
        "best_run": best_run,
        "rough_forecast_for_1m_articles": estimate,
    }

    args.json_output.parent.mkdir(parents=True, exist_ok=True)
    args.json_output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_markdown(args.md_output, args.input, sampled_rows, hardware, run_rows, estimate)
    print(f"wrote benchmark json to {args.json_output}")
    print(f"wrote benchmark markdown to {args.md_output}")


if __name__ == "__main__":
    main()
