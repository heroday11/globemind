"""Pure presentation rules for story graph responses."""

from __future__ import annotations

import math
from typing import Optional

_EVENT_TYPE_COLORS = {
    "diplomacy": "#4A90D9",
    "trade_conflict": "#E67E22",
    "military": "#E74C3C",
    "policy_legal": "#2ECC71",
    "protest_repression": "#9B59B6",
    "terrorism_espionage": "#34495E",
    "aid_disaster": "#1ABC9C",
    "appointment_leadership": "#F39C12",
    "human_rights_migration": "#E91E63",
    "other": "#95A5A6",
}

_EDGE_TYPE_COLORS = {
    "continued": "#2ECC71",
    "escalation": "#E74C3C",
    "response": "#F39C12",
    "transition": "#95A5A6",
}

_EDGE_TYPE_DASHES = {
    "continued": False,
    "escalation": False,
    "response": True,
    "transition": True,
}

_EDGE_TYPE_WIDTHS = {
    "continued": 3,
    "escalation": 4,
    "response": 2,
    "transition": 1,
}

_EVENT_TYPE_CN = {
    "diplomacy": "外交",
    "trade_conflict": "贸易冲突",
    "military": "军事",
    "policy_legal": "政策法律",
    "protest_repression": "抗议镇压",
    "terrorism_espionage": "恐袭间谍",
    "aid_disaster": "援助灾难",
    "appointment_leadership": "人事任免",
    "human_rights_migration": "人权移民",
    "other": "其他",
}

_EVENT_TYPE_FAMILY = {
    "military": "conflict",
    "terrorism_espionage": "conflict",
    "protest_repression": "conflict",
    "trade_conflict": "economic",
    "policy_legal": "institutional",
    "diplomacy": "negotiation",
    "appointment_leadership": "negotiation",
    "human_rights_migration": "humanitarian",
    "aid_disaster": "humanitarian",
}


def chinese_event_type(event_type: Optional[str]) -> str:
    return _EVENT_TYPE_CN.get(event_type or "other", "其他")


def chinese_entity(name: Optional[str]) -> str:
    if not name:
        return "?"
    normalized = name.strip().lower()
    entities = {
        "united states": "美国",
        "us": "美国",
        "u.s.": "美国",
        "china": "中国",
        "people's republic of china": "中国",
        "russia": "俄罗斯",
        "russian federation": "俄罗斯",
        "ukraine": "乌克兰",
        "israel": "以色列",
        "iran": "伊朗",
        "islamic republic of iran": "伊朗",
        "india": "印度",
        "japan": "日本",
        "germany": "德国",
        "france": "法国",
        "uk": "英国",
        "united kingdom": "英国",
        "britain": "英国",
        "turkey": "土耳其",
        "turkiye": "土耳其",
        "saudi arabia": "沙特",
        "south korea": "韩国",
        "republic of korea": "韩国",
        "north korea": "朝鲜",
        "dprk": "朝鲜",
        "taiwan": "台湾",
        "australia": "澳大利亚",
        "canada": "加拿大",
        "brazil": "巴西",
        "afghanistan": "阿富汗",
        "syria": "叙利亚",
        "syrian arab republic": "叙利亚",
        "pakistan": "巴基斯坦",
        "trump": "特朗普",
        "donald trump": "特朗普",
        "biden": "拜登",
        "joe biden": "拜登",
        "putin": "普京",
        "vladimir putin": "普京",
        "xi jinping": "习近平",
        "xi": "习近平",
        "zelensky": "泽连斯基",
        "zelenskyy": "泽连斯基",
        "vladimir zelensky": "泽连斯基",
        "netanyahu": "内塔尼亚胡",
        "benjamin netanyahu": "内塔尼亚胡",
        "modi": "莫迪",
        "narendra modi": "莫迪",
        "macron": "马克龙",
        "emmanuel macron": "马克龙",
        "scholz": "朔尔茨",
        "olaf scholz": "朔尔茨",
        "kim jong un": "金正恩",
        "kim": "金正恩",
        "rubio": "卢比奥",
        "marco rubio": "卢比奥",
        "erdogan": "埃尔多安",
        "recep tayyip erdogan": "埃尔多安",
    }
    translated = entities.get(normalized)
    if translated is not None:
        return translated
    short = normalized.split()[0][:6] if normalized.split() else normalized[:6]
    return short.title()


def event_color(event_type: Optional[str]) -> str:
    return _EVENT_TYPE_COLORS.get(event_type or "other", "#95A5A6")


def story_node_size(article_count: int) -> float:
    return 10.0 + min(40.0, math.log2(article_count + 1) * 8)


def event_family(event_type: Optional[str]) -> str:
    return _EVENT_TYPE_FAMILY.get((event_type or "other").lower(), "other")


def get_edge_style(edge_type: str) -> dict[str, object]:
    return {
        "color": _EDGE_TYPE_COLORS.get(edge_type, "#95A5A6"),
        "dashes": _EDGE_TYPE_DASHES.get(edge_type, False),
        "width": _EDGE_TYPE_WIDTHS.get(edge_type, 1),
        "label": edge_type,
    }
