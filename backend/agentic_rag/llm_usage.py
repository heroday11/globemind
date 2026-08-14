#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
OpenAI 兼容 API（含 DashScope 等）的 token 累计，供微观 LLM、宏观命名等共用。

线程安全；与 naming_service 中原有计数器合并为单一数据源。
"""
from __future__ import annotations

import threading
from typing import Any, Dict

_LOCK = threading.Lock()
_COUNTS: Dict[str, int] = {
    "prompt_tokens": 0,
    "completion_tokens": 0,
    "total_tokens": 0,
    "api_calls": 0,
}


def reset_usage_counters() -> None:
    with _LOCK:
        for k in _COUNTS:
            _COUNTS[k] = 0


def get_usage_snapshot() -> Dict[str, int]:
    with _LOCK:
        return dict(_COUNTS)


def _add_tokens(pt: int, ct: int, tt: int) -> None:
    with _LOCK:
        _COUNTS["prompt_tokens"] += pt
        _COUNTS["completion_tokens"] += ct
        if tt > 0:
            _COUNTS["total_tokens"] += tt
        else:
            _COUNTS["total_tokens"] += pt + ct
        _COUNTS["api_calls"] += 1


def accumulate_from_chat_completion(resp: Any) -> None:
    """OpenAI Python SDK: chat.completions.create 的返回对象。"""
    u = getattr(resp, "usage", None)
    if u is None:
        return
    pt = int(getattr(u, "prompt_tokens", None) or 0)
    ct = int(getattr(u, "completion_tokens", None) or 0)
    tt = int(getattr(u, "total_tokens", None) or 0)
    _add_tokens(pt, ct, tt)


def accumulate_from_usage_dict(u: Dict[str, Any] | None) -> None:
    """HTTP 原始 JSON 中的 usage 字段（如部分 vLLM/OpenAI 兼容服务）。"""
    if not u or not isinstance(u, dict):
        return
    pt = int(u.get("prompt_tokens") or u.get("input_tokens") or 0)
    ct = int(u.get("completion_tokens") or u.get("output_tokens") or 0)
    tt = int(u.get("total_tokens") or 0)
    _add_tokens(pt, ct, tt)
