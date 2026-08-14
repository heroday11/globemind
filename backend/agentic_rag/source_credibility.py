#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""新闻来源域名 -> 可信度（0~1），写入 news_analysis.source_credibility。

可通过环境变量 SOURCE_CREDIBILITY_JSON 传入 JSON 对象覆盖/扩展默认表。
空 URL 或无法解析的域名使用 SOURCE_CREDIBILITY_UNKNOWN（默认 0.3）。
"""
from __future__ import annotations

import json
import os
from urllib.parse import urlparse

_DEFAULT_DOMAIN_SCORES: dict[str, float] = {
    "reuters.com": 0.9,
    "xinhuanet.com": 0.9,
    "news.cn": 0.9,
    "bbc.com": 0.85,
    "bbc.co.uk": 0.85,
    "apnews.com": 0.88,
    "ap.org": 0.88,
    "nytimes.com": 0.82,
    "washingtonpost.com": 0.8,
    "theguardian.com": 0.82,
    "ft.com": 0.85,
    "wsj.com": 0.82,
    "bloomberg.com": 0.85,
    "cnbc.com": 0.75,
    "unknown": 0.3,
}


def _merged_domain_map() -> dict[str, float]:
    m = dict(_DEFAULT_DOMAIN_SCORES)
    raw = (os.getenv("SOURCE_CREDIBILITY_JSON") or "").strip()
    if not raw:
        return m
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            for k, v in data.items():
                try:
                    m[str(k).strip().lower()] = float(v)
                except (TypeError, ValueError):
                    continue
    except json.JSONDecodeError:
        pass
    return m


def _unknown_score() -> float:
    try:
        return float(os.getenv("SOURCE_CREDIBILITY_UNKNOWN", "0.3"))
    except ValueError:
        return 0.3


def credibility_from_url(url: str | None) -> float:
    """由 news.url 解析主机名并查表；无 URL / 无匹配时使用 unknown 分。"""
    if not url or not str(url).strip():
        return _unknown_score()
    raw = str(url).strip()
    if "://" not in raw:
        raw = "https://" + raw
    try:
        host = (urlparse(raw).netloc or "").strip().lower()
    except Exception:
        return _unknown_score()
    if host.startswith("www."):
        host = host[4:]
    if not host:
        return _unknown_score()
    m = _merged_domain_map()
    # 最长后缀匹配：sub.reuters.com -> reuters.com
    parts = host.split(".")
    for i in range(len(parts)):
        cand = ".".join(parts[i:])
        if cand in m:
            return float(m[cand])
    return _unknown_score()
