#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import socket
import subprocess
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MANIFEST = PROJECT_ROOT / "data" / "proxy_pool" / "proxy_pool_manifest.json"
SINGBOX_BIN = Path("/usr/local/bin/sing-box")
PID_DIR = PROJECT_ROOT / "data" / "proxy_pool" / "pids"
LOG_DIR = PROJECT_ROOT / "data" / "proxy_pool" / "logs"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Start local sing-box pool instances.")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--limit", type=int, default=0)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    rows = json.loads(args.manifest.read_text(encoding="utf-8"))
    if args.limit > 0:
        rows = rows[: args.limit]
    PID_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    for row in rows:
        port = int(row["listen_port"])
        with socket.socket() as sock:
            sock.settimeout(0.2)
            if sock.connect_ex(("127.0.0.1", port)) == 0:
                print(f"port {port} already listening, skip {row.get('name', '')}")
                continue
        log_handle = (LOG_DIR / f"{port}.log").open("ab")
        proc = subprocess.Popen(
            [str(SINGBOX_BIN), "run", "-c", row["config_path"]],
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        (PID_DIR / f"{port}.pid").write_text(str(proc.pid) + "\n", encoding="utf-8")
        print(f"started port {port} pid {proc.pid}")


if __name__ == "__main__":
    main()
