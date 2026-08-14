"""极简关键词兜底：捕获 LR 可能漏掉的核心涉华表述。

LR（BGE+逻辑回归）是主分类器，但以下类型的表述在标题中可能不出现
"中国"/"China"等显性关键词，导致 LR 得分偏低。本模块提供一个
精简的 regex 列表，若命中则将分数提升至 MIN_OVERRIDE_SCORE。
"""
from __future__ import annotations

import re
from typing import Pattern

# 命中后最低保证分数（不低于 LR 原始分）
MIN_OVERRIDE_SCORE = 0.55

_PATTERNS: list[Pattern[str]] = [
    # ── 一带一路 / Belt and Road ──
    re.compile(r"一带一路", re.IGNORECASE),
    re.compile(r"belt\s*and\s*road", re.IGNORECASE),
    re.compile(r"\bBRI\b"),
    # ── 两会 / 全国人大 / 政协 ──
    re.compile(r"(全国)?两会|人大|政协|npc|cppcc", re.IGNORECASE),
    # ── 台湾 / 两岸 / 台海（不含"中国"也属涉华） ──
    re.compile(r"台[湾海]|兩岸|cross.?strait", re.IGNORECASE),
    # ── 南海 / 南沙 / 西沙 ──
    re.compile(r"南[海沙]|south\s*china\s*sea", re.IGNORECASE),
    # ── 涉华特定议题（不含"中国"也明显涉华） ──
    re.compile(r"六方会谈|中欧班列|9[1-3]?2?\s*?kmt|反分裂|武统|一国两制",
               re.IGNORECASE),
    # ── 核心涉华英文缩写/简称 ──
    re.compile(r"\bPLA\b|\bCCP\b|\bCPC\b|\bPRC\b"),
    # ── 新疆 / 西藏 / 香港 / 澳门 ──
    re.compile(r"新疆|x[iï]?njiang|西藏|tibet|香港|hong\s*kong|澳門|macau",
               re.IGNORECASE),
    # ── 人民币 / 离岸人民币 ──
    re.compile(r"人民币|renminbi|RMB|CNY\b", re.IGNORECASE),
    # ── 中国制造2025 / Made in China 2025 ──
    re.compile(r"made\s*in\s*china\s*2025|中国制造2025", re.IGNORECASE),
    # ── 孔子学院 / Confucius Institute ──
    re.compile(r"孔子|confucius\s*institute", re.IGNORECASE),
]


def keyword_override_score(title: str, abstract: str = "") -> float | None:
    """若标题或摘要命中关键词，返回 MIN_OVERRIDE_SCORE（信号量）。

    Args:
        title: 新闻标题。
        abstract: 新闻摘要（可选）。

    Returns:
        MIN_OVERRIDE_SCORE 或 None（未命中）。
    """
    text = f"{title or ''}  {abstract or ''}"
    for pat in _PATTERNS:
        if pat.search(text):
            return MIN_OVERRIDE_SCORE
    return None
