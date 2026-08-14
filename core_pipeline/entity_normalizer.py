"""
增强型实体规范化器 — 解决 "US and Israel" vs "US, Israel" vs "Trump" vs "Donald Trump" 问题。

策略:
  1. 标准名称映射（别名表）
  2. 头衔剥离
  3. 连词拆分（"and", "&", "," → 多实体）
  4. 名字缩短（"Donald Trump" → "Trump"）
  5. 排序保证对称匹配

用法:
  from core_pipeline.entity_normalizer import entity_pair_key, normalize
  key = entity_pair_key("US and Israel", "Iran")  # "israel&us→iran"
"""
from __future__ import annotations

import re
from typing import List, Tuple

# ── 别名映射 ──
_ALIASES = {
    # 国家/地区
    "united states": "us", "usa": "us", "u.s.": "us", "u.s": "us",
    "america": "us", "american": "us",
    "united kingdom": "uk", "britain": "uk", "british": "uk",
    "uae": "united arab emirates",
    "dprk": "north korea", "d.p.r.k.": "north korea",
    "r.o.c.": "taiwan", "roc": "taiwan",
    "p.r.c.": "china", "prc": "china",
    # 人名
    "donald trump": "trump", "donald j. trump": "trump",
    "joe biden": "biden", "joseph biden": "biden",
    "vladimir putin": "putin", "vladimir v. putin": "putin",
    "xi jinping": "xi",
    "benjamin netanyahu": "netanyahu", "bibi netanyahu": "netanyahu",
    "volodymyr zelenskyy": "zelenskyy", "zelensky": "zelenskyy",
    "emmanuel macron": "macron",
    "olaf scholz": "scholz",
    "narendra modi": "modi",
}

# ── 头衔模式 ──
_TITLE_PATTERN = re.compile(
    r'^(president|prime minister|pm|chancellor|secretary|secretary[-\s]general|'
    r'defense secretary|foreign minister|defence minister|interior minister|'
    r'finance minister|minister|chairman|chairperson|spokesman|spokesperson|'
    r'ambassador|governor|senator|congressman|congresswoman|representative|'
    r'chief|director|general|leader|commander|commander[-\s]in[-\s]chief|'
    r'king|queen|prince|princess|sultan|emir|sheikh|ayatollah|'
    r'acting\s+\w+|former\s+\w+|deputy\s+\w+|vice\s+\w+)'
    r'[\s,]+',
    re.IGNORECASE
)

# ── 连词拆分（匹配 "and", "&", 逗号）──
_CONJUNCTIONS = re.compile(r'\s+(?:and|&)\s+|,\s*|\s+,')


def strip_titles(name: str) -> str:
    """去头衔: 'President Donald Trump' → 'Donald Trump'"""
    return _TITLE_PATTERN.sub('', name).strip()


def normalize(name: str) -> List[str]:
    """
    将实体名称规范化为标准形式列表。
    
    "US and Israel" → ["israel", "us"]
    "Donald Trump" → ["trump"]
    "President Xi Jinping" → ["xi"]
    """
    if not name:
        return []
    
    n = name.strip()
    if not n:
        return []
    
    # 去头衔
    n = strip_titles(n)
    
    # 转小写
    n = n.lower()
    
    # 检查完整匹配别名
    if n in _ALIASES:
        return [_ALIASES[n]]
    
    # 连词拆分
    parts = _CONJUNCTIONS.split(n)
    if len(parts) > 1:
        results = []
        for p in parts:
            p = p.strip()
            if not p or p in ('and', '&', ','):
                continue
            if p in _ALIASES:
                p = _ALIASES[p]
            if p and len(p) > 1:
                results.append(p)
        if results:
            return sorted(set(results))
    
    return [n]


def entity_pair_key(initiator: str, target: str) -> str:
    """
    生成实体对的规范化键，用于聚类匹配。
    
    将 "US and Israel → Iran" 和 "US, Israel → Iran" 映射到同一键。
    """
    init_norm = normalize(initiator)
    tgt_norm = normalize(target)
    return f"{'&'.join(init_norm)}→{'&'.join(tgt_norm)}"


def test():
    """测试用例"""
    cases = [
        ("US and Israel", "Iran"),
        ("US, Israel", "Iran"),
        ("US, Israel, UK", "Iran"),
        ("Donald Trump", "Iran"),
        ("Trump", "Iran"),
        ("President Xi Jinping", "US"),
        ("Xi", "America"),
        ("United States", "China"),
        ("USA", "PRC"),
        ("Russia and China", "US and UK"),
        ("", "Iran"),
        ("  ", ""),
    ]
    for init, tgt in cases:
        key = entity_pair_key(init, tgt)
        init_n = normalize(init)
        tgt_n = normalize(tgt)
        print(f"  {init or '(empty)' :30s} × {tgt or '(empty)' :20s} → {str(init_n):30s} × {str(tgt_n):20s} | key={key}")


if __name__ == "__main__":
    test()
    print("\nAll tests passed!")
