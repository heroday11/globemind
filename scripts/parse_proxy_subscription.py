#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import json
import subprocess
import urllib.parse
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT = PROJECT_ROOT / "data" / "proxy_pool" / "subscription_nodes.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Parse a base64 proxy subscription into structured nodes.")
    parser.add_argument("--url", required=True)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--proxy", default="socks5h://127.0.0.1:2080")
    return parser.parse_args()


def fetch_text(url: str, proxy: str) -> str:
    parsed = urllib.parse.urlsplit(proxy)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or 2080
    cmd = [
        "curl",
        "--socks5-hostname",
        f"{host}:{port}",
        "-L",
        "--max-time",
        "30",
        "-A",
        "Mozilla/5.0",
        url,
    ]
    proc = subprocess.run(cmd, capture_output=True, check=True)
    return proc.stdout.decode("utf-8", "ignore")


def decode_subscription(text: str) -> list[str]:
    clean = "".join(text.strip().split())
    decoded = base64.b64decode(clean + "=" * (-len(clean) % 4)).decode("utf-8", "ignore")
    return [line.strip() for line in decoded.splitlines() if line.strip()]


def parse_line(line: str) -> dict | None:
    if "://" not in line:
        return None
    scheme = line.split("://", 1)[0]
    parsed = urllib.parse.urlsplit(line)
    name = urllib.parse.unquote(parsed.fragment or "")
    if any(key in name for key in ["剩余流量", "下次重置剩余", "套餐到期"]):
        return None
    return {
        "scheme": scheme,
        "raw": line,
        "name": name,
        "host": parsed.hostname,
        "port": parsed.port,
        "username": urllib.parse.unquote(parsed.username or ""),
        "password": urllib.parse.unquote(parsed.password or ""),
        "query": dict(urllib.parse.parse_qsl(parsed.query)),
    }


def main() -> None:
    args = parse_args()
    text = fetch_text(args.url, args.proxy)
    lines = decode_subscription(text)
    nodes = [item for item in (parse_line(line) for line in lines) if item]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(nodes, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {len(nodes)} nodes to {args.output}")


if __name__ == "__main__":
    main()
