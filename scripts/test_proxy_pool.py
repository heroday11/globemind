#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import time
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MANIFEST = PROJECT_ROOT / "data" / "proxy_pool" / "proxy_pool_manifest.json"
DEFAULT_OUTPUT = PROJECT_ROOT / "data" / "proxy_pool" / "proxy_pool_health.json"
TEST_URL = "https://www.bbc.com/robots.txt"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Test local socks proxy pool health.")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--url", default=TEST_URL)
    parser.add_argument("--timeout", type=int, default=20)
    return parser.parse_args()


def test_one(socks_url: str, url: str, timeout: int) -> tuple[bool, int | None, float]:
    hostport = socks_url.split("://", 1)[1]
    t0 = time.perf_counter()
    proc = subprocess.run(
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
            "/dev/null",
            "-w",
            "%{http_code}",
            url,
        ],
        capture_output=True,
        check=False,
        text=True,
    )
    elapsed = time.perf_counter() - t0
    code = None
    try:
        code = int(proc.stdout.strip())
    except Exception:
        code = None
    return proc.returncode == 0 and code is not None and code < 400, code, elapsed


def main() -> None:
    args = parse_args()
    rows = json.loads(args.manifest.read_text(encoding="utf-8"))
    if args.limit > 0:
        rows = rows[: args.limit]
    out = []
    for row in rows:
        ok, code, elapsed = test_one(row["socks_url"], args.url, args.timeout)
        out.append(
            {
                **row,
                "test_ok": ok,
                "http_code": code,
                "elapsed_sec": round(elapsed, 3),
            }
        )
        print(row["listen_port"], ok, code, round(elapsed, 3), row["name"])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
