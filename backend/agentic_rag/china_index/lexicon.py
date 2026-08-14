"""层次化涉华词典：基于 GDELT CAMEO / CNKI 涉华关键词体系的实体级快速评分。

用法:
    from agentic_rag.china_index.lexicon import score_by_lexicon

    row = {"title": "...", "abstract": "...", "body": "..."}
    result = score_by_lexicon(row)
    # -> {"score": 0-1, "matches": {"核心实体": ["习近平", ...], "议题词": [...]}}
"""

from __future__ import annotations

import re
from typing import Dict, List, Tuple

# ── 层级 1：中国核心实体（直接提及 = 高度涉华） ──────────────────────────
_CORE_ENTITIES = [
    # 领导人
    "习近平",
    "Xi Jinping",
    "President Xi",
    "Chairman Xi",
    "习主席",
    "习总书记",
    "习近平总书记",
    # 政党与政府
    "中国共产党",
    "Chinese Communist Party",
    "the CCP",
    "CCP",
    "中共中央",
    "中央政府",
    "中国政府",
    "Chinese government",
    "国务院",
    "全国人大",
    "中国人民",
]

# ── 层级 2：中国地名与机构（涉华概率高） ────────────────────────────
_CHINA_PLACES_ORG = [
    # 领土相关
    "台湾",
    "Taiwan",
    "台海",
    "Taiwan Strait",
    "西藏",
    "Tibet",
    "新疆",
    "Xinjiang",
    "香港",
    "Hong Kong",
    "南海",
    "South China Sea",
    "钓鱼岛",
    "Diaoyu Islands",
    "Senkaku",
    # 核心城市/地区
    "北京",
    "Beijing",
    "上海",
    "Shanghai",
    "深圳",
    "Shenzhen",
    "香港",
    "Hong Kong",
    "澳门",
    "Macau",
    # 核心机构
    "解放军",
    "PLA",
    "People's Liberation Army",
    "外交部",
    "Chinese foreign ministry",
    "国防部",
    "商务部",
    "新华社",
    "Xinhua",
    "人民日报",
    "People's Daily",
    "CCTV",
    "中国央行",
    "People's Bank of China",
    "中国人民银行",
    "华为",
    "Huawei",
    "TikTok",
    "字节跳动",
    "ByteDance",
    "阿里巴巴",
    "Alibaba",
    "腾讯",
    "Tencent",
    "比亚迪",
    "BYD",
]

# ── 层级 3：涉华议题词（上下文相关，涉华概率中高） ─────────────────────
_CHINA_ISSUES = [
    # 经贸
    "中美贸易",
    "US-China trade",
    "Sino-US",
    "Sino-",
    "关税",
    "tariff",
    "trade war",
    "贸易战",
    "科技封锁",
    "chip ban",
    "半导体管制",
    "实体清单",
    "entity list",
    "export control",
    "出口管制",
    # 外交
    "一带一路",
    "Belt and Road",
    "BRI",
    "金砖",
    "BRICS",
    "上合组织",
    "SCO",
    "亚投行",
    "AIIB",
    "全球南方",
    "Global South",
    "中国方案",
    "Chinese-style modernization",
    "中国式现代化",
    "人类命运共同体",
    # 军事
    "南海军演",
    "军事侦察",
    "间谍气球",
    "balloon",
    "军事扩张",
    "中国威胁论",
    "China threat",
    "印太战略",
    "Indo-Pacific",
    "自由航行",
    "FONOP",
    # 法律/人权
    "香港国安法",
    "HK national security",
    "维吾尔",
    "Uyghur",
    "强迫劳动",
    "forced labor",
    "再教育营",
    "re-education",
    "法轮功",
    "Falun Gong",
    "天安门",
    "Tiananmen",
    "民主化",
    "民主运动",
    # 科技
    "5G",
    "人工智能",
    "AI",
    "deepfake",
    " surveillance",
    "监控",
    "人脸识别",
    "facial recognition",
    "社会信用",
    "social credit",
    # 疫情健康
    "COVID",
    "新冠病毒",
    "zero-COVID",
    "动态清零",
    "疫苗外交",
    "vaccine diplomacy",
    # 台湾相关
    "一国两制",
    "one country, two systems",
    "九二共识",
    "1992 Consensus",
    "两岸关系",
    "cross-strait",
    "武统",
    "和平统一",
    "台独",
    "地动山摇",
    "Chinese territory",
    "中国领土",
]

# ── 层级 4：间接指代（涉华概率较低但可能） ────────────────────────────
_CHINA_INDIRECT = [
    "China",
    "Chinese",
    "PRC",
    "共产党",
    "communist",
    "communism",
    "共产",
    "大陆",
    "mainland",
    "内地",
    "the mainland",
    "Beijing",
    "中俄",
    "中欧",
    "中国",
    "中方",
    "中国政府",
    "涉华",
    "中华",
    "中共",
    "国产",
    "中国市场",
    "Chinese market",
    "中国经济",
    "Chinese economy",
    "人民币",
    "renminbi",
    "yuan",
    "Sino",
    "Made in China",
    "中国制造",
]

# ── 权重配置 ────────────────────────────────────────────────────────
_LAYER_WEIGHTS: list[tuple[str, list[str], float]] = [
    ("核心实体", _CORE_ENTITIES, 0.40),
    ("地名机构", _CHINA_PLACES_ORG, 0.25),
    ("议题词", _CHINA_ISSUES, 0.20),
    ("间接指代", _CHINA_INDIRECT, 0.08),
]

# ── 编译正则缓存 ──────────────────────────────────────────────────────
_PATTERNS: list[tuple[str, str, re.Pattern]] = []
for layer_name, keywords, weight in _LAYER_WEIGHTS:
    for kw in keywords:
        _PATTERNS.append((layer_name, kw, re.compile(re.escape(kw), re.IGNORECASE)))

# 公开暴露完整词典供外部检视/调试
CHINA_LEXICON: Dict[str, List[str]] = {
    layer_name: kw_list for layer_name, kw_list, _ in _LAYER_WEIGHTS
}


def score_by_lexicon(
    row: dict,
    *,
    text: str | None = None,
) -> dict:
    """基于层次化词典计算实体级涉华分数。

    Args:
        row: 包含 ``title``, ``abstract``, ``body`` 的新闻字典。
        text: 可选，手动拼接好的文本（优先使用）。

    Returns:
        {"score": float [0-1],
         "matches": {"层级名": [命中关键词, ...], ...},
         "match_count": int}
    """
    if text is None:
        parts = [
            row.get("title") or "",
            row.get("abstract") or "",
            (row.get("body") or "")[:2000],
        ]
        text = "\n".join(parts)

    text_lower = text.lower()

    layer_scores: list[float] = []
    matches: Dict[str, List[str]] = {}
    total_match_count = 0

    for layer_name, keywords, weight in _LAYER_WEIGHTS:
        layer_hits: List[str] = []
        for kw in keywords:
            if kw.lower() in text_lower:
                layer_hits.append(kw)
        score = min(1.0, len(layer_hits) * weight)
        layer_scores.append(score)
        total_match_count += len(layer_hits)
        if layer_hits:
            matches[layer_name] = layer_hits

    # 各层贡献 = min(权重, 命中数 × 权重)，保证单层不超过其权重
    # 最终 = 各层贡献之和，上限 1.0
    # 这样 1 次"习近平"命中 → 0.40，1 次"China"命中 → 0.08
    total = sum(min(weight, len([kw for kw in keywords if kw.lower() in text_lower]) * weight)
               for _, keywords, weight in _LAYER_WEIGHTS)
    final_score = min(1.0, total)

    return {
        "score": max(0.0, min(1.0, final_score)),
        "matches": matches,
        "match_count": total_match_count,
    }


def score_by_entities(
    entities: list[dict],
    entity_pair_sentiments: list[dict] | None = None,
) -> dict:
    """基于已提取的 GLiNER 实体列表计算涉华分数。

    与 ``score_by_lexicon`` 互补——前者未经过 NER，可直接处理原始文本；
    后者利用已提取的结构化实体做更精确的评分。

    Args:
        entities: GLiNER 提取的实体列表 [{"text": ..., "label": ...}, ...]
        entity_pair_sentiments: 可选，实体对情感分析结果。

    Returns:
        {"score": float [0-1],
         "china_entities": 涉华实体列表,
         "sentiment_polarity": 涉华情感极性 [-1, 1] 或 None}
    """
    entity_texts = [e.get("text", "") for e in entities if e.get("text")]

    # 用级联匹配判断每个实体是否涉华
    china_entities: List[str] = []
    for et in entity_texts:
        et_lower = et.lower()
        for _, keywords, _ in _LAYER_WEIGHTS:
            if any(kw.lower() in et_lower for kw in keywords):
                china_entities.append(et)
                break

    # 涉华比例映射到分数
    score = len(china_entities) / max(len(entity_texts), 1)

    # 涉华情感极性
    sentiment_polarity: float | None = None
    if entity_pair_sentiments:
        china_pairs = [
            p
            for p in entity_pair_sentiments
            if any(
                ce in (p.get("initiator", ""), p.get("target", ""))
                for ce in china_entities
            )
        ]
        if china_pairs:
            sentiments = [
                p.get("sentiment_score", 0) or 0 for p in china_pairs
            ]
            sentiment_polarity = sum(sentiments) / len(sentiments)

    return {
        "score": max(0.0, min(1.0, score)),
        "china_entities": china_entities,
        "sentiment_polarity": sentiment_polarity,
    }
