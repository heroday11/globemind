#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""历史流式模拟：通过 SIM_START / SIM_END 限制 news.pub_time 的半开区间 [start, end)。"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Tuple


def sim_bounds() -> Tuple[str, str] | None:
    s = (os.getenv("SIM_START") or "").strip()
    e = (os.getenv("SIM_END") or "").strip()
    if not s or not e:
        return None
    return s.replace("'", "''"), e.replace("'", "''")


def sim_pub_time_and(alias: str = "n") -> str:
    """返回 SQL 片段：AND n.pub_time IS NOT NULL AND ...；无 SIM 窗口时返回空串。"""
    b = sim_bounds()
    if not b:
        return ""
    s, e = b
    # 末尾保留空格，便于与 ORDER BY / LIMIT 等拼接（避免 ::timestamptzORDER BY 语法错误）
    return (
        f" AND {alias}.pub_time IS NOT NULL "
        f"AND {alias}.pub_time >= '{s}'::timestamptz "
        f"AND {alias}.pub_time < '{e}'::timestamptz "
    )


def sim_fetch_order_by() -> str:
    """有 SIM 窗口时按 pub_time 升序处理，否则保持按 id。"""
    if sim_bounds() is None:
        return "ORDER BY n.id ASC"
    return "ORDER BY n.pub_time ASC NULLS LAST, n.id ASC"


def sim_bounds_datetimes() -> Tuple[datetime, datetime] | None:
    """解析 SIM_START/SIM_END 为 aware datetime（用于 Python 侧与 pub_time 比较）。"""
    s0 = (os.getenv("SIM_START") or "").strip()
    e0 = (os.getenv("SIM_END") or "").strip()
    if not s0 or not e0:
        return None

    def _parse_one(x: str) -> datetime:
        x = x.strip()
        if x.endswith("Z"):
            x = x[:-1] + "+00:00"
        if "T" in x or " " in x:
            d = datetime.fromisoformat(x.replace(" ", "T"))
        else:
            d = datetime.fromisoformat(x + "T00:00:00+00:00")
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return d

    return _parse_one(s0), _parse_one(e0)
