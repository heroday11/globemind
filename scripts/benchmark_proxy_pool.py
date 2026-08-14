#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any
from statistics import median


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MANIFEST = PROJECT_ROOT / "data" / "proxy_pool" / "proxy_pool_manifest.json"
DEFAULT_OUTPUT = PROJECT_ROOT / "data" / "proxy_pool" / "proxy_pool_benchmark.json"
DEFAULT_TEST_URL = "https://www.bbc.com/robots.txt"
DEFAULT_SPEED_URL = "https://speed.cloudflare.com/__down?bytes=50000000"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Benchmark local socks proxy pool for latency and throughput.")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--latency-url", default=DEFAULT_TEST_URL)
    parser.add_argument("--speed-url", default=DEFAULT_SPEED_URL)
    parser.add_argument("--latency-timeout", type=int, default=12)
    parser.add_argument("--speed-timeout", type=int, default=10)
    parser.add_argument("--repeats", type=int, default=3)
    return parser.parse_args()


def curl_probe(socks_url: str, url: str, timeout: int, output: str) -> subprocess.CompletedProcess[str]:
    hostport = socks_url.split("://", 1)[1]
    return subprocess.run(
        [
            "curl",
            "--socks5-hostname",
            hostport,
            "-L",
            "--max-time",
            str(timeout),
            "-A",
            "Mozilla/5.0",
            "-s",
            "-o",
            output,
            "-w",
            "%{http_code} %{size_download} %{speed_download} %{time_total}",
            url,
        ],
        capture_output=True,
        check=False,
        text=True,
    )


def parse_probe_output(text: str) -> dict[str, Any]:
    parts = text.strip().split()
    payload: dict[str, Any] = {
        "http_code": None,
        "size_download": 0,
        "speed_Bps": 0.0,
        "time_total": None,
    }
    if len(parts) >= 4:
        payload["http_code"] = int(parts[0])
        payload["size_download"] = int(float(parts[1]))
        payload["speed_Bps"] = float(parts[2])
        payload["time_total"] = float(parts[3])
    return payload


def score_row(row: dict[str, Any]) -> float:
    if not row.get("latency_ok") or not row.get("speed_ok"):
        return -1.0
    speed_mbps = float(row.get("speed_Mbps") or 0.0)
    latency = float(row.get("latency_sec") or 999.0)
    availability = float(row.get("availability_ratio") or 0.0)
    return round(speed_mbps - latency * 10.0 + availability * 100.0, 3)


def benchmark_one(
    row: dict[str, Any],
    latency_url: str,
    speed_url: str,
    latency_timeout: int,
    speed_timeout: int,
    repeats: int,
) -> dict[str, Any]:
    latency_samples: list[dict[str, Any]] = []
    speed_samples: list[dict[str, Any]] = []
    for _ in range(max(1, repeats)):
        latency_proc = curl_probe(row["socks_url"], latency_url, latency_timeout, "/dev/null")
        latency_stats = parse_probe_output(latency_proc.stdout)
        latency_samples.append(
            {
                "ok": latency_proc.returncode == 0 and latency_stats["http_code"] is not None and latency_stats["http_code"] < 400,
                "http_code": latency_stats["http_code"],
                "time_total": latency_stats["time_total"],
            }
        )
        speed_proc = curl_probe(row["socks_url"], speed_url, speed_timeout, "/dev/null")
        speed_stats = parse_probe_output(speed_proc.stdout)
        speed_samples.append(
            {
                "ok": speed_proc.returncode == 0 and speed_stats["http_code"] == 200 and speed_stats["size_download"] > 0,
                "http_code": speed_stats["http_code"],
                "size_download": speed_stats["size_download"],
                "speed_Bps": speed_stats["speed_Bps"],
                "time_total": speed_stats["time_total"],
            }
        )

    latency_ok_count = sum(1 for sample in latency_samples if sample["ok"])
    speed_ok_count = sum(1 for sample in speed_samples if sample["ok"])
    latency_ok = latency_ok_count > 0
    speed_ok = speed_ok_count > 0
    latency_times = [float(sample["time_total"]) for sample in latency_samples if sample["ok"] and sample["time_total"] is not None]
    speed_bps = [float(sample["speed_Bps"]) for sample in speed_samples if sample["ok"]]
    speed_sizes = [int(sample["size_download"]) for sample in speed_samples if sample["ok"]]
    speed_times = [float(sample["time_total"]) for sample in speed_samples if sample["ok"] and sample["time_total"] is not None]

    result = {
        **row,
        "latency_ok": latency_ok,
        "latency_http_code": next((sample["http_code"] for sample in latency_samples if sample["ok"]), latency_samples[-1]["http_code"]),
        "latency_sec": round(median(latency_times), 3) if latency_times else None,
        "speed_ok": speed_ok,
        "speed_http_code": next((sample["http_code"] for sample in speed_samples if sample["ok"]), speed_samples[-1]["http_code"]),
        "speed_bytes": max(speed_sizes) if speed_sizes else 0,
        "speed_Bps": median(speed_bps) if speed_bps else 0.0,
        "speed_Mbps": round((median(speed_bps) if speed_bps else 0.0) * 8 / 1_000_000, 3),
        "speed_sec": round(median(speed_times), 3) if speed_times else None,
        "latency_ok_count": latency_ok_count,
        "speed_ok_count": speed_ok_count,
        "repeats": max(1, repeats),
        "availability_ratio": round((latency_ok_count + speed_ok_count) / (2 * max(1, repeats)), 3),
        "score": 0.0,
    }
    result["score"] = score_row(result)
    return result


def main() -> None:
    args = parse_args()
    rows = json.loads(args.manifest.read_text(encoding="utf-8"))
    if args.limit > 0:
        rows = rows[: args.limit]

    results: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as executor:
        futures = {
            executor.submit(
                benchmark_one,
                row,
                args.latency_url,
                args.speed_url,
                args.latency_timeout,
                args.speed_timeout,
                args.repeats,
            ): row
            for row in rows
        }
        for future in as_completed(futures):
            result = future.result()
            results.append(result)
            print(
                json.dumps(
                    {
                        "port": result.get("listen_port"),
                        "region": result.get("region"),
                        "latency_ok": result.get("latency_ok"),
                        "latency_sec": result.get("latency_sec"),
                        "speed_ok": result.get("speed_ok"),
                        "speed_Mbps": result.get("speed_Mbps"),
                        "availability_ratio": result.get("availability_ratio"),
                        "score": result.get("score"),
                        "name": result.get("name"),
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )

    results.sort(
        key=lambda item: (
            float(item.get("availability_ratio") or 0.0),
            item.get("score", -1.0),
            item.get("speed_Mbps", 0.0),
            -float(item.get("latency_sec") or 999.0),
        ),
        reverse=True,
    )
    payload = {
        "manifest": str(args.manifest),
        "count": len(results),
        "results": results,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote benchmark to {args.output}")


if __name__ == "__main__":
    main()
