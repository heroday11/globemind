#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Dashboard 搜索 API 三模式自测脚本（仅调用后端，不依赖前端）。

用法示例：
  python scripts/test_search_modes.py --base http://127.0.0.1:8001
  python scripts/test_search_modes.py --base http://192.168.207.170:8001 --topic "南海"

环境变量（可选）：
  SEARCH_BEARER_TOKEN  若接口需登录，填入 JWT（Bearer）。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from typing import Any, Dict, Optional


def _post_json(url: str, payload: Dict[str, Any], token: Optional[str], timeout: int) -> Dict[str, Any]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json; charset=utf-8"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {e.code}: {err_body}") from e


def run_mode(
    base: str,
    mode: str,
    topic: str,
    token: Optional[str],
    timeout: int,
    publish_time: Optional[str] = "近一年",
) -> Dict[str, Any]:
    base = base.rstrip("/")
    url = f"{base}/api/dashboard/search"
    # 查询参数 mode 与 body 中 mode 二选一即可；此处同时写入便于核对
    url_with_mode = f"{url}?mode={mode}"
    payload: Dict[str, Any] = {
        "topic": topic,
        "must_include": "",
        "any_include": "",
        "need_exclude": "",
        "hit_location": "全文",
        "page": 1,
        "page_size": 5,
        "sort_by": "pub_time",
        "sort_order": "desc",
        "mode": mode,
    }
    if publish_time:
        payload["publish_time"] = publish_time
    return _post_json(url_with_mode, payload, token=token, timeout=timeout)


def main() -> int:
    parser = argparse.ArgumentParser(description="测试 /api/dashboard/search 的 exact / fuzzy / cluster 模式")
    parser.add_argument(
        "--base",
        default=os.getenv("BACKEND_BASE", "http://127.0.0.1:8001"),
        help="后端根地址，例如 http://127.0.0.1:8001",
    )
    parser.add_argument("--topic", default="测试", help="检索主题/关键词（支持中文）")
    parser.add_argument(
        "--publish-time",
        nargs="?",
        const="",
        default="近一年",
        help='发布时间：近一月 / 近一年；省略本参数为默认近一年；--publish-time 后不加值表示不按时间过滤',
    )
    parser.add_argument("--timeout", type=int, default=300, help="单次请求超时秒数（BGE 首次加载可能较慢）")
    args = parser.parse_args()
    pub = (args.publish_time or "").strip()

    token = os.getenv("SEARCH_BEARER_TOKEN") or None

    modes = ("exact", "fuzzy", "cluster")
    for mode in modes:
        print(f"\n{'=' * 60}")
        print(f"模式: {mode}  POST {args.base}/api/dashboard/search?mode={mode}")
        print("=" * 60)
        try:
            data = run_mode(args.base, mode, args.topic, token, args.timeout, publish_time=pub or None)
        except Exception as e:
            print(f"失败: {e}", file=sys.stderr)
            continue
        total = data.get("total", 0)
        ms = data.get("query_time_ms", 0)
        items = data.get("data") or []
        print(f"total={total}  query_time_ms={ms:.1f}  本页条数={len(items)}")
        for i, row in enumerate(items[:5], 1):
            title = (row.get("title") or "")[:80]
            print(f"  [{i}] id={row.get('id')}  {title}")

    print("\n完成。后端需连接 DB_NAME=postgres 与 public.news；fuzzy/cluster 需 Milvus + pip install pyyaml sentence-transformers。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
