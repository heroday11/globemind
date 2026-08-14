#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_INPUT = PROJECT_ROOT / "data" / "proxy_pool" / "subscription_nodes.json"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "data" / "proxy_pool" / "singbox_pool"
DEFAULT_MANIFEST = PROJECT_ROOT / "data" / "proxy_pool" / "proxy_pool_manifest.json"

REGION_PRIORITY = ["HK", "SG", "JP", "US", "TW", "KR", "DE", "UK", "FR"]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build local sing-box configs from parsed subscription nodes.")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--start-port", type=int, default=2100)
    parser.add_argument("--limit", type=int, default=12)
    return parser.parse_args()


def load_nodes(path: Path) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))


def infer_region(name: str) -> str:
    m = re.search(r"\[([A-Z]{2})\]", name)
    if m:
        return m.group(1)
    if "HongKong" in name:
        return "HK"
    if "Singapore" in name:
        return "SG"
    if "Japan" in name:
        return "JP"
    if "USA" in name or "UnitedStates" in name:
        return "US"
    return "ZZ"


def select_nodes(nodes: list[dict], limit: int) -> list[dict]:
    grouped: dict[str, list[dict]] = {}
    for node in nodes:
        if node.get("scheme") != "anytls":
            continue
        region = infer_region(node.get("name", ""))
        node = dict(node)
        node["region"] = region
        grouped.setdefault(region, []).append(node)

    ordered_regions = REGION_PRIORITY + sorted(region for region in grouped if region not in REGION_PRIORITY)
    if limit <= 0:
        limit = sum(len(items) for items in grouped.values())

    selected: list[dict] = []
    while len(selected) < limit:
        progressed = False
        for region in ordered_regions:
            if grouped.get(region):
                selected.append(grouped[region].pop(0))
                progressed = True
                if len(selected) >= limit:
                    return selected
        if not progressed:
            break
    return selected


def build_config(node: dict, listen_port: int) -> dict:
    query = node.get("query", {})
    host = node["host"]
    auth_token = node.get("password") or node.get("username") or ""
    return {
        "log": {"level": "info", "timestamp": True},
        "inbounds": [
            {
                "type": "socks",
                "tag": "socks-in",
                "listen": "127.0.0.1",
                "listen_port": listen_port,
            }
        ],
        "outbounds": [
            {
                "type": "anytls",
                "tag": "proxy",
                "server": host,
                "server_port": int(node["port"]),
                "password": auth_token,
                "tls": {
                    "enabled": True,
                    "server_name": host,
                    "insecure": str(query.get("insecure", "0")) == "1",
                    "utls": {
                        "enabled": True,
                        "fingerprint": query.get("fp", "chrome"),
                    },
                },
            },
            {"type": "direct", "tag": "direct"},
        ],
        "route": {"auto_detect_interface": True, "final": "proxy"},
    }


def main() -> None:
    args = parse_args()
    nodes = load_nodes(args.input)
    selected = select_nodes(nodes, args.limit)
    args.output_dir.mkdir(parents=True, exist_ok=True)

    manifest_rows = []
    for idx, node in enumerate(selected):
        port = args.start_port + idx
        config = build_config(node, port)
        slug = f"{idx+1:02d}_{node['region'].lower()}_{node['host'].split('.')[0]}"
        config_path = args.output_dir / f"{slug}.json"
        config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        manifest_rows.append(
            {
                "name": node.get("name", slug),
                "region": node.get("region", "ZZ"),
                "host": node["host"],
                "listen_port": port,
                "socks_url": f"socks5h://127.0.0.1:{port}",
                "config_path": str(config_path),
            }
        )

    args.manifest.parent.mkdir(parents=True, exist_ok=True)
    args.manifest.write_text(json.dumps(manifest_rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {len(manifest_rows)} configs to {args.output_dir}")
    print(f"wrote manifest to {args.manifest}")


if __name__ == "__main__":
    main()
