"""
故事图可视化 API — 为前端 StoryGraphView.vue 提供数据。

端点:
  GET /api/story-graph/list           — 所有故事概览列表
  GET /api/story-graph/<story_id>     — 单条故事图的节点+边数据
  GET /api/story-graph/cluster/<cluster_id> — L1 簇详情（含新闻列表）
"""

from __future__ import annotations

import html
import math
import re
from collections import defaultdict
from datetime import date, datetime
from typing import Any, Dict, List, Optional
from urllib.parse import urlsplit, urlunsplit

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.encoders import jsonable_encoder
from sqlalchemy import text
from sqlalchemy.orm import Session

from api.cache import TTLStore, make_cache_key
from api.core.db import SQLALCHEMY_DATABASE_URL, SessionLocal, engine, get_db
from api.features.ground_news import build_source_profile_contract
from api.features.story_graph import (
    ClusterDetail,
    ClusterNewsItem,
    GraphSamplingProvenance,
    StoryEdge,
    StoryGraphResponse,
    StoryListItem,
    StoryListResponse,
    StoryNode,
    StoryRelationItem,
    build_graph_sampling_component,
    build_graph_sampling_provenance,
    build_unavailable_story_relation_claim,
    chinese_entity,
    chinese_event_type,
    event_color,
    event_family,
    project_story_relation,
    story_node_size,
)
from api.features.story_graph import (
    get_edge_style as get_edge_style,
)

router = APIRouter(tags=["故事图可视化"])
_PUBLIC_CACHE = TTLStore("story-graph")
_LEGACY_GRAPH_NODE_LIMIT = 300
_L2_CHAIN_NODE_LIMIT = 200
_GROUND_NEWS_TIMELINE_NODE_LIMIT = 200


def _make_l1_database_url():
    """Use the same canonical role and endpoint as the primary Web engine."""
    return SQLALCHEMY_DATABASE_URL


_L1_ENGINE = engine
_L1_SESSION_LOCAL = SessionLocal
get_l1_db = get_db


def _news_rows_payload(rows: Any) -> List[Dict[str, Any]]:
    news_items: List[Dict[str, Any]] = []
    for row in rows:
        item = _row_dict(row)
        news_id = item.get("news_id") or item.get("id")
        if news_id is None:
            continue
        news_items.append(
            {
                "news_id": int(news_id),
                "title": item.get("title"),
                "published_at": str(item.get("published_at")) if item.get("published_at") else None,
                "url": _safe_evidence_url(item.get("url")),
                "cluster_id": item.get("cluster_id") or item.get("l1_cluster_id"),
                "segment_id": item.get("segment_id"),
            }
        )
    return news_items


def _safe_evidence_url(value: Any) -> str | None:
    raw = str(value or "").strip()
    if (
        not raw
        or len(raw) > 4000
        or "\\" in raw
        or any(ord(character) <= 32 or ord(character) == 127 for character in raw)
    ):
        return None
    try:
        parsed = urlsplit(raw)
        port = parsed.port
        hostname = parsed.hostname
    except (UnicodeError, ValueError):
        return None
    if (
        parsed.scheme.lower() not in {"http", "https"}
        or not hostname
        or parsed.username is not None
        or parsed.password is not None
    ):
        return None
    try:
        ascii_host = hostname.encode("idna").decode("ascii").lower()
    except UnicodeError:
        return None
    if ":" in ascii_host and not ascii_host.startswith("["):
        ascii_host = f"[{ascii_host}]"
    authority = f"{ascii_host}:{port}" if port is not None else ascii_host
    return urlunsplit(
        (parsed.scheme.lower(), authority, parsed.path or "/", "", "")
    )


def _evidence_target(value: Any, *, field: str) -> str | None:
    if value is None or not isinstance(value, str):
        return None
    normalized = value.strip()
    if (
        not normalized
        or len(normalized) > 256
        or any(ord(character) < 32 or ord(character) == 127 for character in normalized)
    ):
        raise HTTPException(status_code=422, detail=f"{field} is invalid")
    return normalized


def _row_dict(row: Any) -> Dict[str, Any]:
    if row is None:
        return {}
    data = dict(row)
    return jsonable_encoder(data)


def _rows(rows: Any) -> List[Dict[str, Any]]:
    return [_row_dict(row) for row in rows]


_NODE_RELATION_INPUT_FIELDS = frozenset(
    {
        "edge_type",
        "edge_weight",
        "relation_reason",
        "title_similarity",
        "shared_actor_count",
        "shared_topic_count",
        "gap_days",
    }
)


def _strip_node_relation_inputs(nodes: List[Dict[str, Any]]) -> None:
    """Keep stored edge labels on governed edge DTOs, never on node DTOs."""

    for node in nodes:
        for field in _NODE_RELATION_INPUT_FIELDS:
            node.pop(field, None)


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    return html.unescape(str(value)).strip()


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        normalized = float(value)
        return normalized if math.isfinite(normalized) else default
    except (OverflowError, TypeError, ValueError):
        return default


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or isinstance(value, bool):
            return default
        return int(value)
    except (OverflowError, TypeError, ValueError):
        return default


def _known_graph_count(value: Any) -> int | None:
    return value if type(value) is int and 0 <= value <= 2_147_483_647 else None


def _legacy_graph_sampling(
    *,
    evaluated_nodes: int,
    returned_nodes: int,
    include_related: bool,
    returned_related: int,
    related_limit: int,
) -> GraphSamplingProvenance:
    """Describe legacy edge-referenced candidates without claiming a full graph."""

    related_requested = related_limit if include_related else 0
    return build_graph_sampling_provenance(
        build_graph_sampling_component(
            unit="legacy_story_node",
            requested_count=_LEGACY_GRAPH_NODE_LIMIT,
            evaluated_count=evaluated_nodes,
            returned_count=returned_nodes,
            limit=_LEGACY_GRAPH_NODE_LIMIT,
            selection_rule="stored_edge_referenced_nodes",
            reason_codes=[
                "DISPLAY_LIMIT",
                "ISOLATED_NODES_NOT_EVALUATED",
                "GRAPH_COMPLETENESS_NOT_ESTABLISHED",
            ],
        ),
        build_graph_sampling_component(
            unit="related_story",
            requested_count=related_requested,
            evaluated_count=None if include_related else 0,
            returned_count=returned_related,
            limit=related_requested,
            selection_rule="related_story_rank",
            reason_codes=[
                "RELATED_STORY_LIMIT",
                "GRAPH_COMPLETENESS_NOT_ESTABLISHED",
            ],
        ),
    )


_GROUND_NEWS_EXPERT_MIN_SOURCES = 2


def _ground_news_low_value_title_where(alias: str = "c") -> str:
    title = f"COALESCE({alias}.title, '')"
    title_l = f"LOWER({title})"
    return f"""
    NOT (
        {title} ~* '^[[:space:]]*[[:alpha:]][[:alpha:].'' -]{{2,80}}[[:space:]]+[|][[:space:]]+'
        OR {title_l} LIKE '%latest news%'
        OR {title_l} LIKE '%news and updates%'
        OR {title_l} LIKE '%top stories%'
        OR {title_l} LIKE '%most read%'
        OR {title_l} LIKE '%big reveal announcement%'
        OR {title_l} LIKE '%closing remarks%'
        OR {title_l} LIKE '%read experts opinion%'
        OR {title_l} LIKE '%whole-of-society approach%'
    )
    """


_POLITICAL_GROUP_MAP = {
    "left": "left",
    "center_left": "left",
    "lean_left": "left",
    "left_center": "left",
    "center": "center",
    "least_biased": "center",
    "mixed": "center",
    "center_right": "right",
    "lean_right": "right",
    "right_center": "right",
    "right": "right",
    "state_aligned": "state_aligned",
    "government": "state_aligned",
    "state": "state_aligned",
}

_BIAS_GROUP_LABELS = {
    "left": "左翼 / 偏左",
    "center": "中间 / 低偏见",
    "right": "右翼 / 偏右",
    "state_aligned": "国家立场",
    "unknown": "未评级",
}

def _normalize_political_group(value: Any) -> str:
    text_value = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    if not text_value:
        return "unknown"
    if text_value in _POLITICAL_GROUP_MAP:
        return _POLITICAL_GROUP_MAP[text_value]
    if "state" in text_value or "government" in text_value:
        return "state_aligned"
    if "left" in text_value:
        return "left"
    if "right" in text_value:
        return "right"
    if "center" in text_value or "centre" in text_value or "least" in text_value:
        return "center"
    return "unknown"


def _top_json_entries(value: Any, limit: int = 5) -> List[Dict[str, Any]]:
    if not isinstance(value, dict):
        return []
    rows = [
        {"key": str(key), "value": _safe_float(raw)}
        for key, raw in value.items()
        if _safe_float(raw) > 0
    ]
    rows.sort(key=lambda item: (-item["value"], item["key"]))
    return rows[:limit]


def _format_date_range(start: Any, end: Any) -> str:
    if not start and not end:
        return "日期未知"
    start_text = str(start or end)[:10]
    end_text = str(end or start)[:10]
    if start_text and end_text and start_text != end_text:
        return f"{start_text} 至 {end_text}"
    return start_text or end_text


def _story_public_title(story: Dict[str, Any]) -> str:
    return (
        story.get("canonical_title")
        or story.get("display_title")
        or story.get("title")
        or story.get("l1_title")
        or story.get("cluster_id")
        or "未命名事件"
    )


def _directory_low_or_unknown_label_pct(
    source_breakdown: Dict[str, Any], article_count: int
) -> float:
    counts = source_breakdown.get("credibility_tier_counts") or {}
    normalized_counts = {
        str(key or "unknown").lower(): max(0.0, _safe_float(value))
        for key, value in counts.items()
    }
    total = sum(normalized_counts.values()) or max(article_count, 1)
    if total <= 0:
        return 0.0
    watched = normalized_counts.get("low", 0.0) + normalized_counts.get(
        "unknown", 0.0
    )
    return round((watched / total) * 100.0, 2)


def _blindspot_assessment(
    story: Dict[str, Any], source_breakdown: Dict[str, Any]
) -> Dict[str, Any]:
    bias = (
        source_breakdown.get("political_group_pct_reviewed_known_sources")
        or story.get("political_group_pct_reviewed_known_sources")
        or {}
    )
    left = _safe_float(bias.get("left"))
    center = _safe_float(bias.get("center"))
    right = _safe_float(bias.get("right"))
    state = _safe_float(bias.get("state_aligned"))
    source_count = _safe_int(source_breakdown.get("source_count") or story.get("source_count"))
    reviewed_count = _safe_int(source_breakdown.get("reviewed_known_political_source_count"))
    unknown_count = _safe_int(source_breakdown.get("unknown_political_source_count"))
    unknown_ratio = unknown_count / max(source_count, 1)
    directory_low_or_unknown_label_pct = _directory_low_or_unknown_label_pct(
        source_breakdown,
        _safe_int(source_breakdown.get("article_count") or story.get("article_count")),
    )

    side_gap = abs(left - right)
    missing_side = "left" if left < 8 <= right else "right" if right < 8 <= left else ""
    center_anchor = max(0.0, 18.0 - center) * 0.35
    source_signal = min(max(source_count - 2, 0), 18) * 1.8
    state_signal = state * 0.25
    unknown_penalty = min(18.0, unknown_ratio * 26.0)
    score = (
        side_gap + (24.0 if missing_side else 0.0) + center_anchor + source_signal + state_signal
    )
    score = max(0.0, score - unknown_penalty)

    reasons: List[str] = []
    if source_count < 4:
        reasons.append("信源数不足，暂不作为强盲区")
    if reviewed_count <= 0:
        reasons.append("缺少已审核政治倾向评级")
    if missing_side:
        reasons.append(f"{_BIAS_GROUP_LABELS[missing_side]}覆盖明显偏少")
    elif side_gap >= 22:
        reasons.append("左右来源覆盖差距较大")
    if center < 18 and (left or right):
        reasons.append("中间来源占比偏低")
    if state >= 20:
        reasons.append("国家立场来源占比较高")
    if directory_low_or_unknown_label_pct >= 45:
        reasons.append(
            "第三方目录标签中的“低/未知”文章来源占比较高；"
            "该标签不构成事实准确率或来源可靠性结论"
        )
    if unknown_ratio >= 0.35:
        reasons.append("未评级来源占比较高")
    if not reasons:
        reasons.append("左右与中间来源覆盖相对均衡")

    if source_count < 4 or reviewed_count <= 0:
        level = "insufficient_data"
    elif score >= 55:
        level = "high"
    elif score >= 32:
        level = "medium"
    elif score >= 18:
        level = "watch"
    else:
        level = "low"

    return {
        "score": round(score, 4),
        "level": level,
        "missing_side": missing_side,
        "side_gap": round(side_gap, 2),
        "left_pct": round(left, 2),
        "center_pct": round(center, 2),
        "right_pct": round(right, 2),
        "state_aligned_pct": round(state, 2),
        "source_count": source_count,
        "reviewed_known_source_count": reviewed_count,
        "unknown_source_count": unknown_count,
        "unknown_source_pct": round(unknown_ratio * 100.0, 2),
        "directory_low_or_unknown_label_pct": directory_low_or_unknown_label_pct,
        "directory_label_assurance": {
            "state": "catalog_composition_only",
            "source_reliability_conclusion": "not_established",
            "fact_accuracy_conclusion": "not_established",
        },
        "reasons": reasons,
    }


def _source_row_for_evidence(item: Dict[str, Any]) -> Dict[str, Any]:
    raw_profile = dict(item)
    if "updated_at" not in raw_profile:
        raw_profile["updated_at"] = item.get("profile_updated_at")
    profile = build_source_profile_contract(
        raw_profile,
        fallback_domain=str(item.get("domain") or ""),
    )
    political_group = _normalize_political_group(profile.get("political_leaning"))
    return {
        "news_id": item.get("news_id"),
        "title": item.get("title"),
        "published_at": item.get("published_at"),
        "url": _safe_evidence_url(item.get("url")),
        "profile_contract_version": profile["profile_contract_version"],
        "domain": profile["domain"],
        "source_name": profile["source_name"],
        "country": profile["country"] or "未知",
        "region": profile["region"],
        "region_code": profile["region_code"],
        "source_type": profile["source_type"],
        "ownership_type": profile["ownership_type"],
        "geo_alignment": profile["geo_alignment"],
        "political_leaning": profile["political_leaning"],
        "political_group": political_group,
        "political_group_label": _BIAS_GROUP_LABELS.get(political_group, "未评级"),
        "credibility_tier": profile["credibility_tier"],
        "review_status": profile["review_status"],
        "profile_version": profile["profile_version"],
        "evidence_url": profile["evidence_url"],
        "label_confidence": profile["label_confidence"],
        "profile_updated_at": profile["updated_at"],
        "method_card": profile["method_card"],
    }


def _make_story_comparison(
    story: Dict[str, Any],
    source_breakdown: Dict[str, Any],
    segments: List[Dict[str, Any]],
    evidence: List[Dict[str, Any]],
) -> Dict[str, Any]:
    title = _story_public_title(story)
    source_count = _safe_int(source_breakdown.get("source_count") or story.get("source_count"))
    article_count = _safe_int(story.get("article_count") or source_breakdown.get("article_count"))
    date_range = _format_date_range(story.get("start_date"), story.get("end_date"))
    actor_line = " -> ".join(
        [str(value) for value in [story.get("initiator"), story.get("target")] if value]
    )
    top_countries = _top_json_entries(source_breakdown.get("country_counts"), 3)
    top_country_text = "、".join(item["key"] for item in top_countries if item["key"]) or "地区未知"
    neutral_summary = (
        f"{title}。该事件发生在{date_range}，当前聚合 {article_count} 条报道、"
        f"{source_count} 个独立信源，主要来源地区包括{top_country_text}。"
    )
    if actor_line:
        neutral_summary += f" 事件主体关系为 {actor_line}。"

    facts = [
        f"时间范围：{date_range}",
        f"报道规模：{article_count} 条新闻，{source_count} 个信源",
    ]
    if actor_line:
        facts.append(f"核心主体：{actor_line}")
    if story.get("location"):
        facts.append(f"地点线索：{story.get('location')}")
    if segments:
        main_segments = sorted(segments, key=lambda item: -_safe_int(item.get("article_count")))[:3]
        facts.append(
            "主要报道切面："
            + "；".join(
                f"{item.get('title') or item.get('segment_id')}（{_safe_int(item.get('article_count'))} 条）"
                for item in main_segments
            )
        )
    blindspot = _blindspot_assessment(story, source_breakdown)
    if blindspot.get("level") not in {"low", "insufficient_data"}:
        facts.append("覆盖风险：" + "；".join(blindspot.get("reasons", [])[:2]))

    grouped: Dict[str, List[Dict[str, Any]]] = {
        "left": [],
        "center": [],
        "right": [],
        "state_aligned": [],
        "unknown": [],
    }
    source_table = [_source_row_for_evidence(item) for item in evidence]
    for row in source_table:
        grouped.setdefault(row["political_group"], []).append(row)

    groups: List[Dict[str, Any]] = []
    for key in ["left", "center", "right", "state_aligned", "unknown"]:
        rows = grouped.get(key, [])
        source_names = []
        seen_sources: set[str] = set()
        for row in rows:
            source_name = row.get("source_name") or row.get("domain")
            if source_name and source_name not in seen_sources:
                seen_sources.add(source_name)
                source_names.append(source_name)
        groups.append(
            {
                "key": key,
                "label": _BIAS_GROUP_LABELS.get(key, key),
                "article_count": len(rows),
                "source_count": len(seen_sources),
                "top_sources": source_names[:6],
                "headlines": [
                    {
                        "news_id": row.get("news_id"),
                        "title": row.get("title"),
                        "source_name": row.get("source_name"),
                        "domain": row.get("domain"),
                        "published_at": row.get("published_at"),
                        "url": row.get("url"),
                    }
                    for row in rows[:8]
                ],
            }
        )

    difference_lines: List[str] = []
    for group in groups:
        if group["key"] == "unknown" or not group["headlines"]:
            continue
        sample = group["headlines"][0]
        difference_lines.append(
            f"{group['label']}样本主要来自 {', '.join(group['top_sources'][:3]) or '未知信源'}，"
            f"代表标题为“{sample.get('title') or '无标题'}”。"
        )
    if not difference_lines:
        difference_lines.append("当前缺少足够的已评级来源，暂不能稳定比较左右叙事差异。")

    coverage_details = {
        "source_count": source_count,
        "article_count": article_count,
        "reviewed_known_political_source_count": _safe_int(
            source_breakdown.get("reviewed_known_political_source_count")
        ),
        "unknown_political_source_count": _safe_int(
            source_breakdown.get("unknown_political_source_count")
        ),
        "country_counts": source_breakdown.get("country_counts") or {},
        "source_type_counts": source_breakdown.get("source_type_counts") or {},
        "ownership_type_counts": source_breakdown.get("ownership_type_counts") or {},
        "credibility_tier_counts": source_breakdown.get("credibility_tier_counts") or {},
        "political_group_pct_reviewed_known_sources": (
            source_breakdown.get("political_group_pct_reviewed_known_sources") or {}
        ),
    }

    return {
        "neutral_summary": neutral_summary,
        "key_facts": facts[:7],
        "bias_groups": groups,
        "difference_summary": difference_lines,
        "coverage_details": coverage_details,
        "blindspot": blindspot,
        "source_table": source_table,
        "generated_by": "dynamic_source_comparison_v1",
    }


def _parse_datetimeish(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    text = str(value).strip()
    try:
        if "T" in text or ":" in text:
            return datetime.fromisoformat(text.replace("Z", "+00:00"))
        return datetime.fromisoformat(f"{text}T12:00:00")
    except ValueError:
        return None


def _node_time(node: StoryNode) -> Optional[datetime]:
    return (
        _parse_datetimeish(node.display_time)
        or _parse_datetimeish(node.start_date)
        or _parse_datetimeish(node.end_date)
    )


def _cluster_identity(node: StoryNode) -> set[str]:
    values = []
    if node.initiator:
        values.append(node.initiator.strip().lower())
    if node.target:
        values.append(node.target.strip().lower())
    return {value for value in values if value}


def _node_sort_key(node: StoryNode) -> tuple:
    timestamp = _node_time(node)
    return (
        timestamp or datetime.min,
        node.event_type or "",
        node.id,
    )


def _order_story_nodes(nodes: List[StoryNode], edges: List[StoryEdge]) -> List[StoryNode]:
    if len(nodes) <= 1:
        return nodes[:]

    node_map = {node.id: node for node in nodes}
    outgoing: Dict[str, str] = {}
    incoming: set[str] = set()
    for edge in edges:
        outgoing[edge.from_id] = edge.to_id
        incoming.add(edge.to_id)

    start = next((node.id for node in nodes if node.id not in incoming), None)
    if not start:
        return sorted(nodes, key=_node_sort_key)

    ordered: List[StoryNode] = []
    seen: set[str] = set()
    cursor = start
    while cursor and cursor not in seen and cursor in node_map:
        seen.add(cursor)
        ordered.append(node_map[cursor])
        cursor = outgoing.get(cursor)

    remainder = [node for node in nodes if node.id not in seen]
    ordered.extend(sorted(remainder, key=_node_sort_key))
    return ordered


def _bridge_edge_type(relation: StoryRelationItem) -> str:
    """Return only an already-governed bridge candidate type.

    Entity overlap and temporal proximity choose layout anchors; they do not
    establish parallelism, influence, or causality.
    """

    if relation.relation_type == "pair_family":
        return "branch"
    return relation.relation_type


def _fetch_story_relation_rows(
    db: Session, story_id: int, related_limit: int
) -> List[StoryRelationItem]:
    relation_rows = (
        db.execute(
            text("""
            SELECT sr.neighbor_story_id, sr.relation_type, sr.layer, sr.score, sr.reason,
                   st.title, st.start_date, st.end_date, st.meta
            FROM story_relations sr
            LEFT JOIN story_trees st ON st.id = sr.neighbor_story_id
            WHERE sr.story_id = :sid
            ORDER BY
                CASE WHEN sr.layer = 'backbone' THEN 0 ELSE 1 END,
                sr.rank ASC,
                sr.score DESC,
                sr.neighbor_story_id ASC
            LIMIT :limit
        """),
            {"sid": story_id, "limit": related_limit},
        )
        .mappings()
        .fetchall()
    )
    items: List[StoryRelationItem] = []
    seen_story_ids: set[int] = set()
    for row in relation_rows[:related_limit]:
        neighbor_story_id = int(row["neighbor_story_id"])
        if neighbor_story_id in seen_story_ids:
            continue
        seen_story_ids.add(neighbor_story_id)
        relation_projection = project_story_relation(
            edge_type=row["relation_type"],
            relation_reason=row["reason"],
            derivation="stored_derived_relation",
        )
        items.append(
            StoryRelationItem(
                story_id=neighbor_story_id,
                title=row["title"] or f"Story {neighbor_story_id}",
                relation_type=relation_projection.public_edge_type,
                layer=row["layer"],
                score=float(row["score"] or 0.0),
                reason=relation_projection.public_relation_reason,
                dominant_type=(row["meta"] or {}).get("dominant_type"),
                start_date=str(row["start_date"]) if row["start_date"] else None,
                end_date=str(row["end_date"]) if row["end_date"] else None,
                macro_story_id=(row["meta"] or {}).get("macro_story_id"),
                pair_key=(row["meta"] or {}).get("pair_key") or [],
                claim=build_unavailable_story_relation_claim(
                    graph_scope_id=f"legacy-relations:{story_id}",
                    from_id=str(story_id),
                    to_id=str(neighbor_story_id),
                    relation_kind=relation_projection.public_edge_type,
                    derivation="stored_derived_relation",
                ),
                relation_semantics=relation_projection.semantics,
            )
        )
    return items


def _fetch_story_bundle(
    db: Session,
    story_ids: List[int],
    primary_story_id: int,
    related_lookup: Dict[int, StoryRelationItem],
) -> Dict[int, Dict[str, Any]]:
    if not story_ids:
        return {}

    tree_rows = (
        db.execute(
            text("""
            SELECT id, title, start_date, end_date, meta
            FROM story_trees
            WHERE id = ANY(:sids)
        """),
            {"sids": story_ids},
        )
        .mappings()
        .fetchall()
    )

    edge_rows = (
        db.execute(
            text("""
            SELECT story_id, from_cluster_id, to_cluster_id, edge_type, weight
            FROM story_edges
            WHERE story_id = ANY(:sids)
            ORDER BY story_id, created_at NULLS LAST, from_cluster_id, to_cluster_id
        """),
            {"sids": story_ids},
        )
        .mappings()
        .fetchall()
    )

    cluster_ids: set[str] = set()
    story_edges_raw: Dict[int, List[Dict[str, Any]]] = defaultdict(list)
    story_cluster_ids: Dict[int, set[str]] = defaultdict(set)
    for row in edge_rows:
        story_edges_raw[row["story_id"]].append(dict(row))
        story_cluster_ids[row["story_id"]].add(row["from_cluster_id"])
        story_cluster_ids[row["story_id"]].add(row["to_cluster_id"])
        cluster_ids.add(row["from_cluster_id"])
        cluster_ids.add(row["to_cluster_id"])

    cluster_templates: Dict[str, Dict[str, Any]] = {}
    if cluster_ids:
        cluster_rows = (
            db.execute(
                text("""
                SELECT ec.cluster_id, ec.title, ec.event_type, ec.initiator, ec.target,
                       ec.article_count, ec.start_date, ec.end_date,
                       tm.display_time
                FROM event_coref_clusters ec
                LEFT JOIN (
                    SELECT cluster_id,
                           to_timestamp(
                               percentile_cont(0.5) WITHIN GROUP (
                                   ORDER BY extract(epoch FROM published_at)
                               )
                           ) AS display_time
                    FROM event_coref_members
                    WHERE cluster_id = ANY(:cids) AND published_at IS NOT NULL
                    GROUP BY cluster_id
                ) tm ON ec.cluster_id = tm.cluster_id
                WHERE ec.cluster_id = ANY(:cids)
            """),
                {"cids": list(cluster_ids)},
            )
            .mappings()
            .fetchall()
        )

        for row in cluster_rows:
            etype = row["event_type"] or "other"
            title = (row["title"] or "").strip()
            if title:
                label = title[:30]
            else:
                init_cn = chinese_entity(row["initiator"])
                tgt_cn = chinese_entity(row["target"])
                etype_cn = chinese_event_type(etype)
                label = f"{etype_cn}·{init_cn}→{tgt_cn}"[:30]
            dr = ""
            if row["start_date"] or row["end_date"]:
                dr = f"{row['start_date'] or '?'} ~ {row['end_date'] or '?'}"
            cluster_templates[row["cluster_id"]] = {
                "cluster_id_raw": row["cluster_id"],
                "label": label[:30],
                "event_type": etype,
                "article_count": row["article_count"],
                "date_range": dr[:20],
                "display_time": str(row["display_time"]) if row["display_time"] else None,
                "start_date": str(row["start_date"]) if row["start_date"] else None,
                "end_date": str(row["end_date"]) if row["end_date"] else None,
                "initiator": row["initiator"],
                "target": row["target"],
                "color": event_color(etype),
                "size": story_node_size(row["article_count"]),
            }

    bundle: Dict[int, Dict[str, Any]] = {}
    for row in tree_rows:
        sid = row["id"]
        relation = related_lookup.get(sid)
        role = "primary" if sid == primary_story_id else "branch"
        story_title = row["title"] or f"Story {sid}"
        nodes: List[StoryNode] = []
        for cluster_id in sorted(story_cluster_ids.get(sid, set())):
            template = cluster_templates.get(cluster_id)
            if template is None:
                template = {
                    "cluster_id_raw": cluster_id,
                    "label": cluster_id[:20],
                    "event_type": "other",
                    "article_count": 1,
                    "date_range": "",
                    "display_time": None,
                    "start_date": None,
                    "end_date": None,
                    "initiator": None,
                    "target": None,
                    "color": "#95A5A6",
                    "size": 10.0,
                }
            compound_id = f"{sid}::{cluster_id}"
            nodes.append(
                StoryNode(
                    id=compound_id,
                    story_id=sid,
                    story_title=story_title,
                    story_role=role,
                    relation_layer=relation.layer if relation else "backbone",
                    **template,
                )
            )

        edges: List[StoryEdge] = []
        for edge in story_edges_raw.get(sid, []):
            relation_projection = project_story_relation(
                edge_type=edge["edge_type"],
                derivation="stored_derived_relation",
            )
            edges.append(
                StoryEdge(
                    from_id=f"{sid}::{edge['from_cluster_id']}",
                    to_id=f"{sid}::{edge['to_cluster_id']}",
                    edge_type=relation_projection.public_edge_type,
                    weight=float(edge["weight"]),
                    layer="story",
                    relation_reason=relation_projection.public_relation_reason,
                    source_story_id=sid,
                    target_story_id=sid,
                    claim=build_unavailable_story_relation_claim(
                        graph_scope_id=f"legacy:{sid}",
                        from_id=edge["from_cluster_id"],
                        to_id=edge["to_cluster_id"],
                        relation_kind=relation_projection.public_edge_type,
                        derivation="stored_derived_relation",
                    ),
                    relation_semantics=relation_projection.semantics,
                )
            )

        bundle[sid] = {
            "story_id": sid,
            "title": story_title,
            "start_date": str(row["start_date"]) if row["start_date"] else None,
            "end_date": str(row["end_date"]) if row["end_date"] else None,
            "meta": row["meta"] or {},
            "nodes": nodes,
            "edges": edges,
        }

    for sid, info in bundle.items():
        info["ordered_nodes"] = _order_story_nodes(info["nodes"], info["edges"])

    return bundle


def _choose_bridge_pairs(
    source_story: Dict[str, Any],
    target_story: Dict[str, Any],
    relation: StoryRelationItem,
) -> List[tuple[StoryNode, StoryNode, float]]:
    source_nodes = source_story.get("ordered_nodes", [])
    target_nodes = target_story.get("ordered_nodes", [])
    if not source_nodes or not target_nodes:
        return []

    if relation.relation_type in {"pair_sequence", "macro_sequence"}:
        source_tail = source_nodes[-1]
        target_head = target_nodes[0]
        if (
            _node_time(target_nodes[-1])
            and _node_time(source_nodes[0])
            and _node_time(target_nodes[-1]) < _node_time(source_nodes[0])
        ):
            return [(target_nodes[-1], source_nodes[0], relation.score)]
        return [(source_tail, target_head, relation.score)]

    candidates: List[tuple[float, StoryNode, StoryNode]] = []
    for source_node in source_nodes:
        source_time = _node_time(source_node)
        source_entities = _cluster_identity(source_node)
        for target_node in target_nodes:
            target_time = _node_time(target_node)
            target_entities = _cluster_identity(target_node)
            gap_score = 0.0
            if source_time and target_time:
                gap_days = abs((target_time - source_time).days)
                gap_score = max(0.0, 1.0 - min(gap_days, 14) / 14.0)
            same_type = 1.0 if source_node.event_type == target_node.event_type else 0.0
            same_family = (
                1.0
                if event_family(source_node.event_type) == event_family(target_node.event_type)
                else 0.0
            )
            entity_overlap = 1.0 if source_entities & target_entities else 0.0
            score = (
                relation.score * 0.45
                + gap_score * 0.20
                + same_type * 0.20
                + same_family * 0.10
                + entity_overlap * 0.05
            )
            candidates.append((score, source_node, target_node))

    candidates.sort(key=lambda item: (-item[0], item[1].id, item[2].id))
    limit = 2 if relation.layer == "context" else 3
    chosen: List[tuple[StoryNode, StoryNode, float]] = []
    used_pairs: set[tuple[str, str]] = set()
    for score, source_node, target_node in candidates:
        key = (source_node.id, target_node.id)
        if key in used_pairs:
            continue
        used_pairs.add(key)
        chosen.append((source_node, target_node, round(score, 4)))
        if len(chosen) >= limit:
            break
    return chosen


# ── 端点 ──


@router.get("/api/story-graph/ground-news/list")
def list_ground_news_story_cards(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    q: Optional[str] = Query(None, description="按标题/实体搜索 L1 story card"),
    event_family: Optional[str] = Query(None, description="按 event_family 过滤"),
    min_articles: int = Query(2, ge=1, le=100),
    min_sources: int = Query(
        _GROUND_NEWS_EXPERT_MIN_SOURCES, ge=1, le=100, description="最少独立信源数"
    ),
    date_days: int = Query(120, ge=0, le=3650, description="只看最近 N 天；0 表示全部"),
    sort: str = Query(
        "recent", description="recent=最新优先，importance/impact=重要性优先，coverage=多信源优先"
    ),
    quality: str = Query(
        "usable",
        description="all=不过滤，usable=至少多信源，ready=达到正式分析口径，expert=专家分析口径",
    ),
    exclude_low_value_titles: bool = Query(
        True, description="剔除栏目页、作者页、公告页等低分析价值标题"
    ),
    include_first_detail: bool = Query(
        False, description="首屏优化：同时返回当前页第一条 story 的详情"
    ),
    l1_run_id: str = Query("fast_l1_v2"),
    l15_run_id: str = Query("fast_l15_v1"),
    l2_run_id: str = Query("fast_l2_v1"),
    db: Session = Depends(get_l1_db),
) -> Dict[str, Any]:
    """Ground-News-style 主 story card 列表，基于正式 L1 聚类。"""
    try:
        ck = make_cache_key(
            "ground-news-list:v2",
            page=page,
            page_size=page_size,
            q=(q or "").strip(),
            event_family=event_family or "",
            min_articles=min_articles,
            min_sources=min_sources,
            date_days=date_days,
            sort=(sort or "recent").strip().lower(),
            quality=(quality or "usable").strip().lower(),
            exclude_low_value_titles=exclude_low_value_titles,
            include_first_detail=include_first_detail,
            l1_run_id=l1_run_id,
            l15_run_id=l15_run_id,
            l2_run_id=l2_run_id,
        )
        cached = _PUBLIC_CACHE.get(ck)
        if cached is not None:
            return cached
        sort_mode = (sort or "recent").strip().lower()
        if sort_mode not in {"recent", "importance", "impact", "popular", "coverage"}:
            sort_mode = "recent"
        quality_mode = (quality or "usable").strip().lower()
        if quality_mode not in {"all", "usable", "ready", "expert"}:
            quality_mode = "usable"
        if quality_mode == "expert":
            min_sources = max(min_sources, _GROUND_NEWS_EXPERT_MIN_SOURCES)
        if sort_mode == "coverage":
            order_by_sql = """
                    COALESCE(sb.source_count, 0) DESC,
                    c.article_count DESC,
                    COALESCE(c.end_date, c.start_date) DESC NULLS LAST,
                    c.cluster_id
            """
        elif sort_mode in {"importance", "impact", "popular"}:
            order_by_sql = """
                    c.article_count DESC,
                    COALESCE(sb.source_count, 0) DESC,
                    COALESCE(c.end_date, c.start_date) DESC NULLS LAST,
                    c.cluster_id
            """
        else:
            order_by_sql = """
                    COALESCE(c.end_date, c.start_date) DESC NULLS LAST,
                    c.article_count DESC,
                    COALESCE(sb.source_count, 0) DESC,
                    c.cluster_id
            """
        where = [
            "c.run_id = :l1_run_id",
            "c.article_count >= :min_articles",
            "COALESCE(sb.source_count, 0) >= :min_sources",
            """
            (
                COALESCE(c.end_date, c.start_date) IS NULL
                OR COALESCE(c.end_date, c.start_date) <= CURRENT_DATE
            )
            """,
        ]
        params: Dict[str, Any] = {
            "l1_run_id": l1_run_id,
            "l15_run_id": l15_run_id,
            "min_articles": min_articles,
            "min_sources": min_sources,
            "date_days": date_days,
            "limit": page_size,
            "offset": (page - 1) * page_size,
        }
        if date_days > 0:
            where.append(
                """
                COALESCE(c.end_date, c.start_date) >=
                    CURRENT_DATE - CAST(:date_days AS integer) * INTERVAL '1 day'
                """
            )
        if quality_mode == "ready":
            where.append("COALESCE(sb.analysis_status, 'not_built') = 'ready'")
        elif quality_mode in {"usable", "expert"}:
            where.append("COALESCE(sb.analysis_status, 'not_built') <> 'single_source'")
        if exclude_low_value_titles:
            where.append(_ground_news_low_value_title_where("c"))
        if event_family:
            where.append("c.event_family = :event_family")
            params["event_family"] = event_family
        if q:
            where.append(
                """
                (
                    c.title ILIKE :q
                    OR c.initiator ILIKE :q
                    OR c.target ILIKE :q
                    OR EXISTS (
                        SELECT 1
                        FROM public.event_l15_segments sq
                        WHERE sq.run_id = :l15_run_id
                          AND sq.l1_cluster_id = c.cluster_id
                          AND sq.title ILIKE :q
                    )
                )
                """
            )
            params["q"] = f"%{q.strip()}%"

        total = int(
            db.execute(
                text(
                    f"""
                    SELECT COUNT(*)
                    FROM public.event_coref_clusters AS c
                    LEFT JOIN public.story_source_breakdown AS sb
                      ON sb.story_id = c.cluster_id
                    WHERE {" AND ".join(where)}
                    """
                ),
                params,
            ).scalar()
            or 0
        )

        sql = f"""
            WITH page_clusters AS (
                SELECT
                    c.cluster_id,
                    c.article_count,
                    c.event_domain,
                    c.event_family,
                    c.event_action,
                    c.initiator,
                    c.target,
                    c.location,
                    c.tone,
                    c.start_date,
                    c.end_date,
                    c.title AS l1_title,
                    sc.cover_url,
                    sc.cover_kind,
                    sc.source_news_id AS cover_source_news_id,
                    sc.credit AS cover_credit,
                    sc.score AS cover_score,
                    sb.source_count AS source_count,
                    COALESCE(sb.analysis_status, 'not_built') AS source_analysis_status,
                    COALESCE(sb.country_counts, '{{}}'::jsonb) AS country_counts,
                    COALESCE(sb.source_type_counts, '{{}}'::jsonb) AS source_type_counts,
                    COALESCE(sb.credibility_tier_counts, '{{}}'::jsonb) AS credibility_tier_counts,
                    COALESCE(sb.political_group_pct_reviewed_known_sources, '{{}}'::jsonb)
                        AS political_group_pct_reviewed_known_sources
                FROM public.event_coref_clusters AS c
                LEFT JOIN public.story_source_breakdown AS sb
                  ON sb.story_id = c.cluster_id
                LEFT JOIN public.story_cover_assets AS sc
                  ON sc.cluster_id = c.cluster_id
                 AND sc.run_id = c.run_id
                 AND sc.status = 'ok'
                WHERE {" AND ".join(where)}
                ORDER BY
                    {order_by_sql}
                LIMIT :limit OFFSET :offset
            )
            SELECT
                pc.*,
                COALESCE(main_title.title, pc.l1_title) AS canonical_title,
                COALESCE(angles.angle_counts, '{{}}'::jsonb) AS angle_counts,
                COALESCE(angles.angle_article_counts, '{{}}'::jsonb) AS angle_article_counts
            FROM page_clusters AS pc
            LEFT JOIN LATERAL (
                SELECT s.title
                FROM public.event_l15_segments AS s
                WHERE s.run_id = :l15_run_id
                  AND s.l1_cluster_id = pc.cluster_id
                  AND s.story_angle IN ('main_event', 'context_update', 'outcome_reaction')
                ORDER BY
                    CASE s.story_angle
                        WHEN 'main_event' THEN 0
                        WHEN 'context_update' THEN 1
                        ELSE 2
                    END,
                    s.article_count DESC,
                    s.start_date NULLS LAST
                LIMIT 1
            ) AS main_title ON TRUE
            LEFT JOIN LATERAL (
                SELECT
                    jsonb_object_agg(x.story_angle, x.segment_count) AS angle_counts,
                    jsonb_object_agg(x.story_angle, x.article_count) AS angle_article_counts
                FROM (
                    SELECT story_angle, COUNT(*) AS segment_count, SUM(article_count) AS article_count
                    FROM public.event_l15_segments
                    WHERE run_id = :l15_run_id
                      AND l1_cluster_id = pc.cluster_id
                    GROUP BY story_angle
                ) AS x
            ) AS angles ON TRUE
        """
        result = db.execute(text(sql), params).mappings().fetchall()
        stories = []
        for row in result:
            item = _row_dict(row)
            item["canonical_title"] = _clean_text(item.get("canonical_title"))
            item["l1_title"] = _clean_text(item.get("l1_title"))
            item["cover"] = {
                "kind": item.get("cover_kind")
                or ("remote_image" if item.get("cover_url") else "editorial_vector"),
                "image_url": item.get("cover_url"),
                "credit": item.get("cover_credit") or "",
                "source_news_id": item.get("cover_source_news_id"),
                "score": item.get("cover_score"),
            }
            stories.append(item)
        initial_detail = None
        if include_first_detail and stories:
            initial_detail = get_ground_news_story_card(
                stories[0]["cluster_id"],
                l1_run_id=l1_run_id,
                l15_run_id=l15_run_id,
                l2_run_id=l2_run_id,
                db=db,
            )
        return _PUBLIC_CACHE.set(
            ck,
            {
                "stories": stories,
                "total": total,
                "page": page,
                "page_size": page_size,
                "initial_detail": initial_detail,
                "run_ids": {"l1": l1_run_id, "l15": l15_run_id, "l2": l2_run_id},
                "sort": sort_mode,
                "filters": {
                    "min_articles": min_articles,
                    "min_sources": min_sources,
                    "date_days": date_days,
                    "quality": quality_mode,
                    "exclude_low_value_titles": exclude_low_value_titles,
                    "event_family": event_family or "",
                    "q": (q or "").strip(),
                },
                "quality_notes": {
                    "default_profile": "expert_usable",
                    "min_sources_floor": _GROUND_NEWS_EXPERT_MIN_SOURCES,
                    "low_value_title_filter": exclude_low_value_titles,
                },
            },
            ttl_seconds=120,
        )
    except Exception:
        raise HTTPException(status_code=500, detail="Ground News story cards are unavailable") from None


@router.get("/api/story-graph/ground-news/home")
def get_ground_news_home(
    candidate_limit: int = Query(260, ge=40, le=500),
    recent_candidate_limit: int = Query(120, ge=20, le=240),
    min_articles: int = Query(2, ge=1, le=100),
    min_sources: int = Query(
        _GROUND_NEWS_EXPERT_MIN_SOURCES, ge=1, le=100, description="首页候选最少独立信源数"
    ),
    exclude_low_value_titles: bool = Query(
        True, description="剔除栏目页、作者页、公告页等低分析价值标题"
    ),
    l1_run_id: str = Query("fast_l1_v2"),
    l15_run_id: str = Query("fast_l15_v1"),
    l2_run_id: str = Query("fast_l2_v1"),
    db: Session = Depends(get_l1_db),
) -> Dict[str, Any]:
    """Ground-News-style 首页事件流：按综合分组装首屏、专题分区和 L2 走势入口。"""
    try:
        title_filter_sql = (
            _ground_news_low_value_title_where("c") if exclude_low_value_titles else "TRUE"
        )
        aggregate = (
            db.execute(
                text(
                    """
                SELECT
                    COUNT(*) AS total_stories,
                    COALESCE(SUM(c.article_count), 0) AS total_articles,
                    MAX(COALESCE(c.end_date, c.start_date)) AS latest_story_date
                FROM public.event_coref_clusters AS c
                LEFT JOIN public.story_source_breakdown AS sb
                  ON sb.story_id = c.cluster_id
                WHERE c.run_id = :l1_run_id
                  AND c.article_count >= :min_articles
                  AND COALESCE(sb.source_count, 0) >= :min_sources
                  AND __LOW_VALUE_TITLE_FILTER__
                """.replace("__LOW_VALUE_TITLE_FILTER__", title_filter_sql)
                ),
                {"l1_run_id": l1_run_id, "min_articles": min_articles, "min_sources": min_sources},
            )
            .mappings()
            .first()
        )
        latest_story_date = aggregate["latest_story_date"] if aggregate else None
        today = date.today()
        health = (
            db.execute(
                text(
                    """
                WITH product_stories AS (
                    SELECT c.cluster_id, c.run_id, COALESCE(c.end_date, c.start_date) AS story_date
                    FROM public.event_coref_clusters AS c
                    LEFT JOIN public.story_source_breakdown AS sb
                      ON sb.story_id = c.cluster_id
                    WHERE c.run_id = :l1_run_id
                      AND c.article_count >= :min_articles
                      AND COALESCE(sb.source_count, 0) >= :min_sources
                      AND __LOW_VALUE_TITLE_FILTER__
                ),
                realtime_stories AS (
                    SELECT COALESCE(end_date, start_date) AS story_date
                    FROM public.event_coref_clusters
                    WHERE run_id = :l1_run_id
                ),
                cover_stats AS (
                    SELECT
                        COUNT(*) FILTER (WHERE sc.status = 'ok') AS ok_covers
                    FROM product_stories ps
                    LEFT JOIN public.story_cover_assets sc
                      ON sc.cluster_id = ps.cluster_id
                     AND sc.run_id = ps.run_id
                ),
                source_breakdown_stats AS (
                    SELECT
                        COUNT(*) FILTER (WHERE sb.analysis_status = 'ready') AS ready_stories,
                        COUNT(*) FILTER (WHERE COALESCE(sb.source_count, 0) >= 2) AS usable_stories,
                        COUNT(*) FILTER (
                            WHERE sb.analysis_status = 'ready'
                              AND ps.story_date >= CAST(:today AS date) - INTERVAL '7 days'
                        ) AS ready_stories_7d,
                        COUNT(*) FILTER (
                            WHERE COALESCE(sb.source_count, 0) >= 2
                              AND ps.story_date >= CAST(:today AS date) - INTERVAL '7 days'
                        ) AS usable_stories_7d,
                        COUNT(*) FILTER (
                            WHERE sb.analysis_status = 'low_source_count'
                              AND ps.story_date >= CAST(:today AS date) - INTERVAL '7 days'
                        ) AS low_source_stories_7d
                    FROM product_stories ps
                    LEFT JOIN public.story_source_breakdown sb
                      ON sb.story_id = ps.cluster_id
                ),
                source_profile_stats AS (
                    SELECT
                        COUNT(*) AS total_profiles,
                        COUNT(*) FILTER (WHERE review_status IN ('reviewed', 'locked')) AS reviewed_profiles,
                        COUNT(*) FILTER (
                            WHERE political_leaning IS NOT NULL AND political_leaning <> 'unknown'
                        ) AS known_bias_profiles,
                        COUNT(*) FILTER (
                            WHERE credibility_tier IS NOT NULL AND credibility_tier <> 'unknown'
                        ) AS known_factuality_profiles,
                        COUNT(*) FILTER (
                            WHERE ownership_type IS NOT NULL AND ownership_type <> 'unknown'
                        ) AS known_ownership_profiles
                    FROM public.media_source_profile
                )
                SELECT
                    (SELECT COUNT(*) FROM product_stories WHERE story_date > :today) AS future_story_count,
                    (SELECT MAX(story_date) FROM product_stories WHERE story_date <= :today) AS latest_valid_story_date,
                    (SELECT MAX(story_date) FROM realtime_stories WHERE story_date <= :today) AS latest_realtime_story_date,
                    (SELECT ok_covers FROM cover_stats) AS ok_story_covers,
                    (SELECT row_to_json(source_breakdown_stats) FROM source_breakdown_stats) AS source_breakdown_coverage,
                    (SELECT row_to_json(source_profile_stats) FROM source_profile_stats) AS source_profile_coverage
                """.replace("__LOW_VALUE_TITLE_FILTER__", title_filter_sql)
                ),
                {
                    "l1_run_id": l1_run_id,
                    "min_articles": min_articles,
                    "min_sources": min_sources,
                    "today": today,
                },
            )
            .mappings()
            .first()
        )

        rows = (
            db.execute(
                text(
                    """
                WITH candidate_ids AS MATERIALIZED (
                    SELECT cluster_id
                    FROM (
                        SELECT c.cluster_id
                        FROM public.event_coref_clusters AS c
                        LEFT JOIN public.story_source_breakdown sb ON sb.story_id = c.cluster_id
                        WHERE c.run_id = :l1_run_id
                          AND c.article_count >= :min_articles
                          AND COALESCE(sb.source_count, 0) >= :min_sources
                          AND __LOW_VALUE_TITLE_FILTER__
                          AND (
                              COALESCE(c.end_date, c.start_date) IS NULL
                              OR COALESCE(c.end_date, c.start_date) <= :today
                          )
                        ORDER BY
                            COALESCE(end_date, start_date) DESC NULLS LAST,
                            c.article_count DESC,
                            c.cluster_id
                        LIMIT :recent_candidate_limit
                    ) AS live_ids
                    UNION
                    SELECT cluster_id
                    FROM (
                        SELECT c.cluster_id
                        FROM public.event_coref_clusters AS c
                        LEFT JOIN public.story_source_breakdown sb ON sb.story_id = c.cluster_id
                        WHERE c.run_id = :l1_run_id
                          AND c.article_count >= :min_articles
                          AND COALESCE(sb.source_count, 0) >= :min_sources
                          AND __LOW_VALUE_TITLE_FILTER__
                          AND (
                              COALESCE(c.end_date, c.start_date) IS NULL
                              OR COALESCE(c.end_date, c.start_date) <= :today
                          )
                          AND COALESCE(c.end_date, c.start_date) >= CAST(:today AS date) - INTERVAL '180 days'
                        ORDER BY
                            COALESCE(end_date, start_date) DESC NULLS LAST,
                            COALESCE(c.article_count, 0) DESC,
                            cluster_id
                        LIMIT :candidate_limit
                    ) AS recent_multi_ids
                    UNION
                    SELECT cluster_id
                    FROM (
                        SELECT c.cluster_id
                        FROM public.event_coref_clusters AS c
                        LEFT JOIN public.story_source_breakdown sb ON sb.story_id = c.cluster_id
                        WHERE c.run_id = :l1_run_id
                          AND c.article_count >= :min_articles
                          AND COALESCE(sb.source_count, 0) >= :min_sources
                          AND __LOW_VALUE_TITLE_FILTER__
                          AND (
                              COALESCE(c.end_date, c.start_date) IS NULL
                              OR COALESCE(c.end_date, c.start_date) <= :today
                          )
                        ORDER BY
                            c.article_count DESC,
                            COALESCE(sb.source_count, 0) DESC,
                            COALESCE(end_date, start_date) DESC NULLS LAST,
                            c.cluster_id
                        LIMIT GREATEST(80, :candidate_limit / 2)
                    ) AS deep_ids
                ),
                candidates AS MATERIALIZED (
                    SELECT
                        c.cluster_id,
                        c.article_count,
                        c.event_domain,
                        c.event_family,
                        c.event_action,
                        c.initiator,
                        c.target,
                        c.location,
                        c.tone,
                        c.start_date,
                        c.end_date,
                        c.title AS l1_title,
                        COALESCE(main_title.title, c.title) AS canonical_title,
                        COALESCE(sb.source_count, 0) AS source_count,
                        COALESCE(sb.analysis_status, 'not_built') AS source_analysis_status,
                        COALESCE(sb.country_counts, '{}'::jsonb) AS country_counts,
                        COALESCE(sb.source_type_counts, '{}'::jsonb) AS source_type_counts,
                        COALESCE(sb.credibility_tier_counts, '{}'::jsonb) AS credibility_tier_counts,
                        COALESCE(sb.ownership_type_counts, '{}'::jsonb) AS ownership_type_counts,
                        COALESCE(sb.reviewed_known_political_source_count, 0)
                            AS reviewed_known_political_source_count,
                        COALESCE(sb.unknown_political_source_count, 0) AS unknown_political_source_count,
                        COALESCE(sb.political_group_pct_reviewed_known_sources, '{}'::jsonb)
                            AS political_group_pct_reviewed_known_sources,
                        COALESCE((sb.political_group_pct_reviewed_known_sources ->> 'left')::numeric, 0) AS left_pct,
                        COALESCE((sb.political_group_pct_reviewed_known_sources ->> 'center')::numeric, 0) AS center_pct,
                        COALESCE((sb.political_group_pct_reviewed_known_sources ->> 'right')::numeric, 0) AS right_pct,
                        COALESCE((sb.political_group_pct_reviewed_known_sources ->> 'state_aligned')::numeric, 0)
                            AS state_aligned_pct,
                        COALESCE(l2.chain_count, 0) AS l2_chain_count,
                        COALESCE(l2.best_quality_score, 0) AS l2_best_quality_score,
                        sc.cover_url,
                        sc.cover_kind,
                        sc.source_news_id AS cover_source_news_id,
                        sc.credit AS cover_credit,
                        sc.score AS cover_score,
                        COALESCE(samples.sample_news, '[]'::jsonb) AS sample_news,
                        COALESCE(samples.source_names, '[]'::jsonb) AS source_names
                    FROM public.event_coref_clusters AS c
                    JOIN candidate_ids AS ci ON ci.cluster_id = c.cluster_id
                    LEFT JOIN public.story_source_breakdown AS sb
                      ON sb.story_id = c.cluster_id
                    LEFT JOIN public.story_cover_assets AS sc
                      ON sc.cluster_id = c.cluster_id
                     AND sc.run_id = c.run_id
                     AND sc.status = 'ok'
                    LEFT JOIN LATERAL (
                        SELECT s.title
                        FROM public.event_l15_segments AS s
                        WHERE s.run_id = :l15_run_id
                          AND s.l1_cluster_id = c.cluster_id
                          AND s.story_angle IN ('main_event', 'context_update', 'outcome_reaction')
                        ORDER BY
                            CASE s.story_angle
                                WHEN 'main_event' THEN 0
                                WHEN 'context_update' THEN 1
                                ELSE 2
                            END,
                            s.article_count DESC,
                            s.start_date NULLS LAST
                        LIMIT 1
                    ) AS main_title ON TRUE
                    LEFT JOIN LATERAL (
                        SELECT
                            COUNT(DISTINCT ch.chain_id) AS chain_count,
                            MAX(ch.quality_score) AS best_quality_score
                        FROM public.event_l2_chain_segments AS cs
                        JOIN public.event_l2_chains AS ch
                          ON ch.run_id = cs.run_id
                         AND ch.chain_id = cs.chain_id
                        WHERE cs.run_id = :l2_run_id
                          AND cs.l1_cluster_id = c.cluster_id
                    ) AS l2 ON TRUE
                    LEFT JOIN LATERAL (
                        SELECT
                            jsonb_agg(
                                jsonb_build_object(
                                    'news_id', q.news_id,
                                    'title', q.title,
                                    'source_name', q.source_name,
                                    'domain', q.domain,
                                    'published_at', q.published_at
                                )
                                ORDER BY q.published_at NULLS LAST, q.news_id
                            ) AS sample_news,
                            jsonb_agg(DISTINCT COALESCE(q.source_name, q.domain)) AS source_names
                        FROM (
                            SELECT
                                n.id AS news_id,
                                n.title,
                                n.published_at,
                                ms.domain,
                                COALESCE(msp.source_name, ms.domain) AS source_name
                            FROM public.event_coref_members AS m
                            JOIN public.news AS n ON n.id = m.news_id
                            LEFT JOIN public.media_source AS ms ON ms.id = n.media_source_id
                            LEFT JOIN public.media_source_profile AS msp ON msp.domain = ms.domain
                            WHERE m.run_id = :l1_run_id
                              AND m.cluster_id = c.cluster_id
                            ORDER BY n.published_at NULLS LAST, n.id
                            LIMIT 4
                        ) AS q
                    ) AS samples ON TRUE
                    WHERE c.run_id = :l1_run_id
                      AND COALESCE(sb.source_count, 0) >= :min_sources
                      AND __LOW_VALUE_TITLE_FILTER__
                      AND (
                          COALESCE(c.end_date, c.start_date) IS NULL
                          OR COALESCE(c.end_date, c.start_date) <= :today
                      )
                )
                SELECT *
                FROM candidates
                ORDER BY
                    article_count DESC,
                    source_count DESC,
                    COALESCE(end_date, start_date) DESC NULLS LAST,
                    cluster_id
                """.replace("__LOW_VALUE_TITLE_FILTER__", title_filter_sql)
                ),
                {
                    "l1_run_id": l1_run_id,
                    "l15_run_id": l15_run_id,
                    "l2_run_id": l2_run_id,
                    "min_articles": min_articles,
                    "min_sources": min_sources,
                    "candidate_limit": candidate_limit,
                    "recent_candidate_limit": recent_candidate_limit,
                    "today": today,
                },
            )
            .mappings()
            .fetchall()
        )

        def story_score(item: Dict[str, Any]) -> float:
            article_count = _safe_int(item.get("article_count"))
            source_count = _safe_int(item.get("source_count"))
            left = _safe_float(item.get("left_pct"))
            right = _safe_float(item.get("right_pct"))
            center = _safe_float(item.get("center_pct"))
            known_bias = left + right + center + _safe_float(item.get("state_aligned_pct"))
            l2_count = _safe_int(item.get("l2_chain_count"))
            l2_quality = _safe_float(item.get("l2_best_quality_score"))
            story_date = item.get("end_date") or item.get("start_date")
            freshness = 0.0
            if latest_story_date and story_date:
                try:
                    age_days = max(0, (latest_story_date - story_date).days)
                    freshness = max(0.0, 28.0 - min(age_days, 28)) * 1.2
                except Exception:
                    freshness = 0.0
            balance_signal = max(0.0, 30.0 - abs(left - right)) * 0.16 if known_bias else 0.0
            source_signal = min(source_count, 30) * 2.8
            volume_signal = min(article_count, 90) * 1.25
            l2_signal = min(l2_count, 6) * 5.0 + min(l2_quality, 1.0) * 8.0
            return round(volume_signal + source_signal + freshness + balance_signal + l2_signal, 4)

        def blindspot_score(item: Dict[str, Any]) -> float:
            return _safe_float(_blindspot_assessment(item, item).get("score"))

        def cover_for(item: Dict[str, Any]) -> Dict[str, Any]:
            family = (item.get("event_family") or "other").lower()
            themes = {
                "diplomacy": ("diplomatic_wire", "外交", "国际关系"),
                "military_security": ("security_grid", "安全", "冲突与防务"),
                "economic_trade": ("market_routes", "经贸", "贸易与市场"),
                "technology_industry": ("tech_signal", "科技", "产业与技术"),
                "domestic_politics": ("civic_chamber", "政治", "选举与治理"),
                "civil_unrest": ("street_signal", "社会", "抗议与公共安全"),
                "law_policy": ("legal_index", "政策", "法律与监管"),
                "public_development": ("public_works", "公共事务", "城市与基础设施"),
                "human_rights_migration": ("border_crossing", "人权与迁徙", "边境与流动"),
                "disaster_environment": ("climate_map", "环境", "灾害与气候"),
            }
            theme, label, motif = themes.get(family, ("global_dispatch", "全球", "综合新闻"))
            seed = abs(hash(item.get("cluster_id") or "")) % 997
            if item.get("cover_url"):
                return {
                    "kind": item.get("cover_kind") or "remote_image",
                    "image_url": item.get("cover_url"),
                    "credit": item.get("cover_credit") or "",
                    "source_news_id": item.get("cover_source_news_id"),
                    "score": item.get("cover_score"),
                    "theme": theme,
                    "label": label,
                    "motif": motif,
                    "seed": seed,
                }
            return {
                "kind": "editorial_vector",
                "theme": theme,
                "label": label,
                "motif": motif,
                "seed": seed,
            }

        stories: List[Dict[str, Any]] = []
        for raw in _rows(rows):
            raw["canonical_title"] = _clean_text(raw.get("canonical_title"))
            raw["l1_title"] = _clean_text(raw.get("l1_title"))
            for sample in raw.get("sample_news") or []:
                if isinstance(sample, dict) and sample.get("title"):
                    sample["title"] = _clean_text(sample.get("title"))
            raw["rank_score"] = story_score(raw)
            raw["blindspot"] = _blindspot_assessment(raw, raw)
            raw["blindspot_score"] = blindspot_score(raw)
            raw["cover"] = cover_for(raw)
            raw["display_title"] = (
                raw.get("canonical_title") or raw.get("l1_title") or raw.get("cluster_id")
            )
            raw["detail_url"] = f"/data-service/ground-news-desk?cluster_id={raw.get('cluster_id')}"
            stories.append(raw)
        stories.sort(
            key=lambda item: (
                -_safe_float(item.get("rank_score")),
                -_safe_int(item.get("article_count")),
                item.get("cluster_id") or "",
            )
        )
        product_stories = [
            item for item in stories if _safe_int(item.get("article_count")) >= min_articles
        ]
        reference_date = (
            health["latest_realtime_story_date"]
            if health and health.get("latest_realtime_story_date")
            else latest_story_date or today
        )
        rotation_bucket = today.toordinal() // 3

        def story_day(item: Dict[str, Any]) -> Optional[date]:
            raw_date = item.get("end_date") or item.get("start_date")
            if isinstance(raw_date, datetime):
                return raw_date.date()
            if isinstance(raw_date, date):
                return raw_date
            if raw_date:
                try:
                    return datetime.fromisoformat(str(raw_date)[:10]).date()
                except Exception:
                    return None
            return None

        def age_days(item: Dict[str, Any]) -> int:
            day = story_day(item)
            if not day:
                return 99999
            return max(0, (reference_date - day).days)

        def recent_pool(max_days: int, *, min_count: int = 1) -> List[Dict[str, Any]]:
            return [
                item
                for item in product_stories
                if _safe_int(item.get("article_count")) >= min_count and age_days(item) <= max_days
            ]

        def by_latest(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
            return sorted(
                items,
                key=lambda item: (
                    story_day(item) or date.min,
                    _safe_int(item.get("article_count")),
                    _safe_float(item.get("rank_score")),
                ),
                reverse=True,
            )

        def by_editor_score(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
            return sorted(
                items,
                key=lambda item: (
                    max(0, 220 - age_days(item)) * 0.9
                    + min(_safe_int(item.get("article_count")), 30) * 1.4
                    + min(_safe_int(item.get("source_count")), 20) * 1.8
                    + min(_safe_int(item.get("l2_chain_count")), 6) * 5.0
                    + _safe_float(item.get("blindspot_score")) * 0.18,
                    story_day(item) or date.min,
                    item.get("cluster_id") or "",
                ),
                reverse=True,
            )

        def rotate(items: List[Dict[str, Any]], key: str) -> List[Dict[str, Any]]:
            if len(items) <= 1:
                return items
            seed = sum(ord(ch) for ch in key)
            offset = (rotation_bucket * ((seed % 11) + 1)) % len(items)
            return items[offset:] + items[:offset]

        latest_pool = sorted(
            product_stories,
            key=lambda item: (
                item.get("end_date") or item.get("start_date") or "",
                _safe_int(item.get("article_count")),
                _safe_float(item.get("rank_score")),
            ),
            reverse=True,
        )

        def has_story_image(item: Dict[str, Any]) -> bool:
            return bool(item.get("cover_url"))

        def has_feature_weight(item: Dict[str, Any]) -> bool:
            return (
                _safe_int(item.get("article_count")) >= max(5, min_articles)
                or _safe_int(item.get("source_count")) >= 2
                or _safe_int(item.get("l2_chain_count")) > 0
            )

        def has_live_wire_weight(item: Dict[str, Any]) -> bool:
            article_count = _safe_int(item.get("article_count"))
            source_count = _safe_int(item.get("source_count"))
            l2_count = _safe_int(item.get("l2_chain_count"))
            rank_score = _safe_float(item.get("rank_score"))
            if source_count < 2 and article_count < 20 and rank_score < 55:
                return False
            return (
                rank_score >= 45
                or article_count >= 8
                or source_count >= 4
                or (article_count >= 5 and source_count >= 2)
                or (l2_count > 0 and article_count >= 4 and source_count >= 2 and rank_score >= 28)
            )

        low_value_title_patterns = [
            r"\blatest news\b",
            r"\bheadlines?\b",
            r"\bnews and updates\b",
            r"\btop stories\b",
            r"\bmost read\b",
            r"\bbig reveal\b",
            r"\bpress conference\b",
            r"\bwhole-of-society approach\b",
        ]

        def has_actionable_title(item: Dict[str, Any]) -> bool:
            title = _clean_text(
                item.get("display_title") or item.get("canonical_title") or item.get("l1_title")
            )
            if len(title) < 18:
                return False
            title_l = title.lower()
            if any(re.search(pattern, title_l) for pattern in low_value_title_patterns):
                return False
            if re.fullmatch(r"[\w .'-]{2,24}\s*-\s*(efe|ansa|ap|reuters|afp)", title_l):
                return False
            if "|" in title and len(title.split("|", 1)[0].strip()) < 18:
                return False
            if title.count(" - ") >= 2 and _safe_int(item.get("source_count")) <= 1:
                return False
            return True

        def has_editorial_floor(item: Dict[str, Any]) -> bool:
            article_count = _safe_int(item.get("article_count"))
            source_count = _safe_int(item.get("source_count"))
            l2_count = _safe_int(item.get("l2_chain_count"))
            rank_score = _safe_float(item.get("rank_score"))
            if not has_actionable_title(item):
                return False
            if source_count <= 1 and article_count < 8 and rank_score < 40:
                return False
            return source_count >= 2 or article_count >= 5 or l2_count > 0 or rank_score >= 35

        def pick(
            predicate: Any,
            limit: int,
            *,
            pool: Optional[List[Dict[str, Any]]] = None,
            min_score: Optional[float] = None,
            exclude: Optional[set[str]] = None,
            allow_repeat_if_short: bool = True,
        ) -> List[Dict[str, Any]]:
            selected: List[Dict[str, Any]] = []
            blocked = exclude or set()
            source_pool = product_stories if pool is None else pool
            for item in source_pool:
                cid = item.get("cluster_id")
                if not cid or cid in blocked:
                    continue
                if min_score is not None and _safe_float(item.get("rank_score")) < min_score:
                    continue
                if predicate(item):
                    selected.append(item)
                    blocked.add(cid)
                    if len(selected) >= limit:
                        break
            if allow_repeat_if_short and len(selected) < min(limit, 4):
                for item in source_pool:
                    cid = item.get("cluster_id")
                    if (
                        not cid
                        or cid in blocked
                        or any(row.get("cluster_id") == cid for row in selected)
                    ):
                        continue
                    if min_score is not None and _safe_float(item.get("rank_score")) < min_score:
                        continue
                    if predicate(item):
                        selected.append(item)
                        blocked.add(cid)
                        if len(selected) >= limit:
                            break
            return selected

        latest_anchor_ids = {
            item.get("cluster_id") for item in latest_pool[:4] if item.get("cluster_id")
        }
        lead_candidates = [
            item
            for item in by_editor_score(recent_pool(45, min_count=min_articles))
            if item.get("cluster_id") not in latest_anchor_ids
            and has_feature_weight(item)
            and has_editorial_floor(item)
        ]
        lead_story = (
            lead_candidates[0]
            if lead_candidates
            else next(
                (item for item in latest_pool if item.get("cluster_id") not in latest_anchor_ids),
                None,
            )
            or (
                latest_pool[0] if latest_pool else (product_stories[0] if product_stories else None)
            )
        )
        used: set[str] = {lead_story["cluster_id"]} if lead_story else set()
        live_pool = latest_pool
        seventy_two_hour_pool = [
            item
            for item in by_latest(recent_pool(3, min_count=min_articles))
            if has_editorial_floor(item)
            and (
                _safe_int(item.get("source_count")) >= 2
                or _safe_int(item.get("article_count")) >= 4
                or _safe_int(item.get("l2_chain_count")) > 0
            )
        ] or live_pool
        week_multi_pool = [
            item
            for item in by_latest(recent_pool(10, min_count=2))
            or by_latest(recent_pool(45, min_count=2))
            if has_editorial_floor(item)
            and (
                _safe_int(item.get("source_count")) >= 2
                or _safe_int(item.get("article_count")) >= 4
            )
        ] or by_latest(
            [
                item
                for item in product_stories
                if _safe_int(item.get("article_count")) >= 2
                and has_editorial_floor(item)
                and (
                    _safe_int(item.get("source_count")) >= 2
                    or _safe_int(item.get("article_count")) >= 4
                )
            ]
        )
        live_wire_pool = [
            item
            for item in by_latest(recent_pool(14, min_count=min_articles)) or latest_pool
            if has_editorial_floor(item) and _safe_int(item.get("source_count")) >= min_sources
        ]
        if len(live_wire_pool) < 4:
            live_wire_seen = {item.get("cluster_id") for item in live_wire_pool}
            live_wire_pool.extend(
                item
                for item in latest_pool
                if item.get("cluster_id") not in live_wire_seen
                and has_editorial_floor(item)
                and has_live_wire_weight(item)
            )
        month_pool = by_editor_score(recent_pool(45, min_count=1)) or by_editor_score(
            product_stories
        )
        quarter_pool = by_editor_score(recent_pool(120, min_count=1)) or by_editor_score(
            product_stories
        )
        half_year_pool = by_editor_score(recent_pool(180, min_count=1)) or by_editor_score(
            product_stories
        )
        confirmed_pool = by_editor_score(
            [
                item
                for item in recent_pool(60, min_count=3)
                if has_editorial_floor(item)
                and (
                    _safe_int(item.get("source_count")) >= 3
                    or _safe_int(item.get("article_count")) >= 8
                )
            ]
        )
        storyline_pool = by_editor_score(
            [
                item
                for item in recent_pool(120, min_count=2)
                if has_editorial_floor(item)
                and _safe_int(item.get("l2_chain_count")) > 0
                and (
                    _safe_int(item.get("article_count")) >= 4
                    or _safe_int(item.get("source_count")) >= 3
                )
            ]
        )
        risk_pool = by_editor_score(
            [
                item
                for item in recent_pool(120, min_count=2)
                if has_editorial_floor(item)
                and item.get("event_family")
                in {"military_security", "security_crime", "civil_unrest", "disaster_environment"}
                and (
                    _safe_int(item.get("source_count")) >= 2
                    or _safe_int(item.get("article_count")) >= 5
                    or _safe_int(item.get("l2_chain_count")) > 0
                )
            ]
        )
        diplomacy_pool = by_editor_score(
            [
                item
                for item in recent_pool(120, min_count=2)
                if has_editorial_floor(item)
                and item.get("event_family") == "diplomacy"
                and (
                    _safe_int(item.get("source_count")) >= 3
                    or _safe_int(item.get("article_count")) >= 5
                    or _safe_int(item.get("l2_chain_count")) > 0
                )
            ]
        )
        market_policy_pool = by_editor_score(
            [
                item
                for item in recent_pool(180, min_count=2)
                if has_editorial_floor(item)
                and item.get("event_family")
                in {"economic_trade", "technology_industry", "law_policy", "public_development"}
                and (
                    _safe_int(item.get("source_count")) >= 2
                    or _safe_int(item.get("article_count")) >= 5
                )
            ]
        )
        blindspot_pool = by_editor_score(
            [
                item
                for item in recent_pool(120, min_count=2)
                if has_editorial_floor(item)
                and _safe_float(item.get("blindspot_score")) >= 35
                and _safe_int(item.get("source_count")) >= 2
            ]
        )
        sections = [
            {
                "key": "latest",
                "title": "最新快讯",
                "subtitle": "按事件时间优先展示达到覆盖/热度阈值的重要快讯，图片不再作为硬门槛",
                "window_days": None,
                "min_articles": min_articles,
                "rotation": "live",
                "requires_image": False,
                "stories": pick(
                    lambda _item: True,
                    12,
                    pool=live_wire_pool,
                    exclude=used,
                    allow_repeat_if_short=False,
                ),
            },
            {
                "key": "pulse_72h",
                "title": "72 小时现场",
                "subtitle": "最近三天的事件切片，打开页面时最容易变化",
                "window_days": 3,
                "min_articles": min_articles,
                "rotation": "live",
                "stories": pick(lambda _item: True, 8, pool=seventy_two_hour_pool, exclude=used),
            },
            {
                "key": "week_watch",
                "title": "本周多源",
                "subtitle": "优先展示一周内已有多篇报道的事件",
                "window_days": 10,
                "min_articles": min_articles,
                "rotation": "live",
                "stories": pick(lambda _item: True, 8, pool=week_multi_pool, exclude=used),
            },
            {
                "key": "l2_movers",
                "title": "走势追踪",
                "subtitle": "已经进入 L2 走势链，且具备多篇报道或多信源支撑的事件",
                "window_days": 120,
                "rotation": "3d",
                "stories": pick(
                    lambda _item: True,
                    8,
                    pool=rotate(storyline_pool, "l2_movers"),
                    exclude=used,
                    allow_repeat_if_short=False,
                ),
            },
            {
                "key": "deep_tracking",
                "title": "多源确认",
                "subtitle": "近期已有多信源或高报道量确认，适合进入详情页横向比较",
                "window_days": 60,
                "rotation": "3d",
                "stories": pick(
                    lambda _item: True,
                    8,
                    pool=rotate(confirmed_pool, "deep_tracking"),
                    exclude=used,
                    allow_repeat_if_short=False,
                ),
            },
            {
                "key": "blindspot",
                "title": "盲区核查",
                "subtitle": "多信源但覆盖结构不均衡，优先提示需要核查的信息盲区",
                "window_days": 120,
                "rotation": "3d",
                "stories": pick(
                    lambda _item: True,
                    8,
                    pool=rotate(blindspot_pool, "blindspot"),
                    exclude=used,
                    allow_repeat_if_short=False,
                ),
            },
            {
                "key": "security",
                "title": "风险升级",
                "subtitle": "冲突、安全、社会动荡和灾害类事件，要求具备报道量或走势链信号",
                "window_days": 120,
                "rotation": "3d",
                "stories": pick(
                    lambda _item: True,
                    8,
                    pool=rotate(risk_pool, "security"),
                    exclude=used,
                    allow_repeat_if_short=False,
                ),
            },
            {
                "key": "diplomacy",
                "title": "谈判外交",
                "subtitle": "会晤、谈判、访问和跨国关系，只保留有持续报道或多信源支撑的事件",
                "window_days": 120,
                "rotation": "3d",
                "stories": pick(
                    lambda _item: True,
                    8,
                    pool=rotate(diplomacy_pool, "diplomacy"),
                    exclude=used,
                    allow_repeat_if_short=False,
                ),
            },
            {
                "key": "economy_tech",
                "title": "市场政策",
                "subtitle": "经贸、科技、政策和公共事务中已有交叉报道的事件",
                "window_days": 180,
                "rotation": "3d",
                "stories": pick(
                    lambda _item: True,
                    8,
                    pool=rotate(market_policy_pool, "economy_tech"),
                    exclude=used,
                    allow_repeat_if_short=False,
                ),
            },
        ]

        l2_rows = (
            db.execute(
                text(
                    """
                SELECT
                    c.chain_id,
                    c.title,
                    c.segment_count,
                    c.article_count,
                    c.family_group,
                    c.event_family,
                    c.event_action,
                    c.initiator,
                    c.target,
                    c.start_date,
                    c.end_date,
                    c.chain_quality,
                    c.quality_score,
                    c.risk_flags
                FROM public.event_l2_chains AS c
                WHERE c.run_id = :l2_run_id
                  AND c.segment_count >= 2
                  AND (
                      COALESCE(c.end_date, c.start_date) IS NULL
                      OR COALESCE(c.end_date, c.start_date) <= :today
                  )
                ORDER BY
                    COALESCE(c.end_date, c.start_date) DESC NULLS LAST,
                    CASE c.chain_quality
                        WHEN 'strong' THEN 0
                        WHEN 'usable' THEN 1
                        ELSE 2
                    END,
                    c.quality_score DESC NULLS LAST,
                    c.article_count DESC,
                    c.chain_id
                LIMIT 14
                """
                ),
                {"l2_run_id": l2_run_id, "today": today},
            )
            .mappings()
            .fetchall()
        )

        return {
            "lead_story": lead_story,
            "sections": sections,
            "l2_watchlist": _rows(l2_rows),
            "edition": {
                "reference_date": reference_date,
                "rotation_bucket": rotation_bucket,
                "rotation_days": 3,
                "live_sections": ["latest", "pulse_72h", "week_watch"],
                "rotating_sections": [
                    "l2_movers",
                    "deep_tracking",
                    "blindspot",
                    "security",
                    "diplomacy",
                    "economy_tech",
                ],
                "stable_sections": ["daily_brief", "topic_index", "source_leaders"],
            },
            "metrics": {
                "total_stories": _safe_int(aggregate["total_stories"] if aggregate else 0),
                "total_articles": _safe_int(aggregate["total_articles"] if aggregate else 0),
                "latest_story_date": latest_story_date,
                "candidate_count": len(stories),
                "product_candidate_count": len(product_stories),
                "min_articles": min_articles,
                "min_sources": min_sources,
                "exclude_low_value_titles": exclude_low_value_titles,
                "latest_valid_story_date": health["latest_valid_story_date"]
                if health
                else latest_story_date,
                "latest_realtime_story_date": health["latest_realtime_story_date"]
                if health
                else latest_story_date,
                "future_story_count": _safe_int(health["future_story_count"] if health else 0),
                "ok_story_covers": _safe_int(health["ok_story_covers"] if health else 0),
                "source_breakdown_coverage": health["source_breakdown_coverage"] if health else {},
                "source_profile_coverage": health["source_profile_coverage"] if health else {},
            },
            "run_ids": {"l1": l1_run_id, "l15": l15_run_id, "l2": l2_run_id},
        }
    except Exception:
        raise HTTPException(status_code=500, detail="Ground News home is unavailable") from None


@router.get("/api/story-graph/ground-news/blindspots")
def list_ground_news_blindspots(
    page: int = Query(1, ge=1),
    page_size: int = Query(40, ge=1, le=120),
    min_articles: int = Query(2, ge=1, le=100),
    min_sources: int = Query(4, ge=1, le=100),
    l1_run_id: str = Query("fast_l1_v2"),
    db: Session = Depends(get_l1_db),
) -> Dict[str, Any]:
    """Ground-News-style Blindspot feed：按结构化盲区评分排序。"""
    try:
        rows = (
            db.execute(
                text(
                    """
                SELECT
                    c.cluster_id,
                    c.article_count,
                    c.event_domain,
                    c.event_family,
                    c.event_action,
                    c.initiator,
                    c.target,
                    c.location,
                    c.tone,
                    c.start_date,
                    c.end_date,
                    c.title AS canonical_title,
                    COALESCE(sb.source_count, 0) AS source_count,
                    COALESCE(sb.analysis_status, 'not_built') AS source_analysis_status,
                    COALESCE(sb.country_counts, '{}'::jsonb) AS country_counts,
                    COALESCE(sb.source_type_counts, '{}'::jsonb) AS source_type_counts,
                    COALESCE(sb.ownership_type_counts, '{}'::jsonb) AS ownership_type_counts,
                    COALESCE(sb.credibility_tier_counts, '{}'::jsonb) AS credibility_tier_counts,
                    COALESCE(sb.political_group_pct_reviewed_known_sources, '{}'::jsonb)
                        AS political_group_pct_reviewed_known_sources,
                    COALESCE(sb.reviewed_known_political_source_count, 0)
                        AS reviewed_known_political_source_count,
                    COALESCE(sb.unknown_political_source_count, 0) AS unknown_political_source_count,
                    sc.cover_url,
                    sc.credit AS cover_credit,
                    COALESCE(samples.sample_news, '[]'::jsonb) AS sample_news
                FROM public.event_coref_clusters AS c
                LEFT JOIN public.story_source_breakdown AS sb ON sb.story_id = c.cluster_id
                LEFT JOIN public.story_cover_assets AS sc
                  ON sc.cluster_id = c.cluster_id
                 AND sc.run_id = c.run_id
                 AND sc.status = 'ok'
                LEFT JOIN LATERAL (
                    SELECT jsonb_agg(
                        jsonb_build_object(
                            'news_id', q.news_id,
                            'title', q.title,
                            'source_name', q.source_name,
                            'domain', q.domain,
                            'published_at', q.published_at
                        )
                        ORDER BY q.published_at NULLS LAST, q.news_id
                    ) AS sample_news
                    FROM (
                        SELECT
                            n.id AS news_id,
                            n.title,
                            n.published_at,
                            ms.domain,
                            COALESCE(msp.source_name, ms.domain) AS source_name
                        FROM public.event_coref_members AS m
                        JOIN public.news AS n ON n.id = m.news_id
                        LEFT JOIN public.media_source AS ms ON ms.id = n.media_source_id
                        LEFT JOIN public.media_source_profile AS msp ON msp.domain = ms.domain
                        WHERE m.run_id = :l1_run_id
                          AND m.cluster_id = c.cluster_id
                        ORDER BY n.published_at NULLS LAST, n.id
                        LIMIT 3
                    ) AS q
                ) AS samples ON TRUE
                WHERE c.run_id = :l1_run_id
                  AND c.article_count >= :min_articles
                  AND COALESCE(sb.source_count, 0) >= :min_sources
                ORDER BY c.article_count DESC, COALESCE(sb.source_count, 0) DESC, c.start_date DESC NULLS LAST
                LIMIT 600
                """
                ),
                {
                    "l1_run_id": l1_run_id,
                    "min_articles": min_articles,
                    "min_sources": min_sources,
                },
            )
            .mappings()
            .fetchall()
        )
        scored: List[Dict[str, Any]] = []
        for row in _rows(rows):
            row["display_title"] = _story_public_title(row)
            row["blindspot"] = _blindspot_assessment(row, row)
            row["blindspot_score"] = row["blindspot"]["score"]
            row["detail_url"] = f"/data-service/ground-news-desk?cluster_id={row.get('cluster_id')}"
            row["cover"] = {
                "kind": "remote_image" if row.get("cover_url") else "editorial_vector",
                "image_url": row.get("cover_url"),
                "credit": row.get("cover_credit") or "",
            }
            if row["blindspot"]["level"] not in {"low", "insufficient_data"}:
                scored.append(row)
        scored.sort(
            key=lambda item: (
                -_safe_float(item.get("blindspot_score")),
                -_safe_int(item.get("source_count")),
            )
        )
        total = len(scored)
        start = (page - 1) * page_size
        return {
            "items": scored[start : start + page_size],
            "total": total,
            "page": page,
            "page_size": page_size,
            "run_id": l1_run_id,
            "formula": {
                "version": "blindspot_v2",
                "signals": [
                    "left_right_gap",
                    "missing_side_threshold",
                    "center_anchor",
                    "source_count",
                    "state_aligned_share",
                    "unknown_political_label_penalty",
                ],
                "directory_label_assurance": {
                    "state": "catalog_composition_only",
                    "source_reliability_conclusion": "not_established",
                    "fact_accuracy_conclusion": "not_established",
                },
            },
        }
    except Exception:
        raise HTTPException(status_code=500, detail="blindspot feed is unavailable") from None


@router.get("/api/story-graph/ground-news/source/{domain}")
def get_ground_news_source_profile(
    domain: str,
    page_size: int = Query(40, ge=1, le=120),
    l1_run_id: str = Query("fast_l1_v2"),
    db: Session = Depends(get_l1_db),
) -> Dict[str, Any]:
    """媒体来源页：评级、近期报道、参与过的 L1 stories 和相似来源。"""
    try:
        profile = (
            db.execute(
                text(
                    """
                SELECT *
                FROM public.media_source_profile
                WHERE domain = :domain
                """
                ),
                {"domain": domain},
            )
            .mappings()
            .first()
        )

        recent_articles = (
            db.execute(
                text(
                    """
                SELECT
                    n.id AS news_id,
                    n.title,
                    n.published_at,
                    n.url,
                    ms.domain
                FROM public.news AS n
                JOIN public.media_source AS ms ON ms.id = n.media_source_id
                WHERE ms.domain = :domain
                ORDER BY n.published_at DESC NULLS LAST, n.id DESC
                LIMIT :limit
                """
                ),
                {"domain": domain, "limit": page_size},
            )
            .mappings()
            .fetchall()
        )

        stories = (
            db.execute(
                text(
                    """
                WITH source_members AS MATERIALIZED (
                    SELECT DISTINCT m.cluster_id
                    FROM public.event_coref_members AS m
                    JOIN public.news AS n ON n.id = m.news_id
                    JOIN public.media_source AS ms ON ms.id = n.media_source_id
                    WHERE m.run_id = :l1_run_id
                      AND ms.domain = :domain
                )
                SELECT
                    c.cluster_id,
                    c.title AS canonical_title,
                    c.article_count,
                    c.event_family,
                    c.event_action,
                    c.initiator,
                    c.target,
                    c.start_date,
                    c.end_date,
                    COALESCE(sb.source_count, 0) AS source_count,
                    COALESCE(sb.political_group_pct_reviewed_known_sources, '{}'::jsonb)
                        AS political_group_pct_reviewed_known_sources
                FROM source_members sm
                JOIN public.event_coref_clusters AS c ON c.cluster_id = sm.cluster_id
                LEFT JOIN public.story_source_breakdown AS sb ON sb.story_id = c.cluster_id
                WHERE c.run_id = :l1_run_id
                ORDER BY COALESCE(c.end_date, c.start_date) DESC NULLS LAST, c.article_count DESC
                LIMIT :limit
                """
                ),
                {"domain": domain, "l1_run_id": l1_run_id, "limit": page_size},
            )
            .mappings()
            .fetchall()
        )

        raw_profile_item = _row_dict(profile)
        profile_item = build_source_profile_contract(
            raw_profile_item,
            fallback_domain=domain,
        )
        peers = []
        if raw_profile_item:
            peer_rows = _rows(
                db.execute(
                    text(
                        """
                        SELECT
                            domain,
                            source_name,
                            country,
                            region,
                            region_code,
                            source_type,
                            ownership_type,
                            geo_alignment,
                            political_leaning,
                            credibility_tier,
                            label_confidence,
                            evidence_url,
                            evidence_note,
                            review_status,
                            article_count_snapshot,
                            profile_version,
                            updated_at
                        FROM public.media_source_profile
                        WHERE domain <> :domain
                          AND (
                            political_leaning = :political_leaning
                            OR source_type = :source_type
                            OR country = :country
                          )
                        ORDER BY
                            CASE WHEN political_leaning = :political_leaning THEN 0 ELSE 1 END,
                            CASE WHEN source_type = :source_type THEN 0 ELSE 1 END,
                            source_name NULLS LAST,
                            domain
                        LIMIT 12
                        """
                    ),
                    {
                        "domain": domain,
                        "political_leaning": raw_profile_item.get("political_leaning"),
                        "source_type": raw_profile_item.get("source_type"),
                        "country": raw_profile_item.get("country"),
                    },
                )
                .mappings()
                .fetchall()
            )
            peers = [
                build_source_profile_contract(
                    peer,
                    fallback_domain=str(peer.get("domain") or ""),
                )
                for peer in peer_rows
            ]

        return {
            "profile": profile_item,
            "recent_articles": _rows(recent_articles),
            "stories": [
                {
                    **item,
                    "display_title": _story_public_title(item),
                    "detail_url": f"/data-service/ground-news-desk?cluster_id={item.get('cluster_id')}",
                }
                for item in _rows(stories)
            ],
            "similar_sources": peers,
            "run_id": l1_run_id,
        }
    except Exception:
        raise HTTPException(status_code=500, detail="source profile is unavailable") from None


@router.get("/api/story-graph/ground-news/topic/{topic}")
def get_ground_news_topic(
    topic: str,
    page: int = Query(1, ge=1),
    page_size: int = Query(40, ge=1, le=120),
    l1_run_id: str = Query("fast_l1_v2"),
    db: Session = Depends(get_l1_db),
) -> Dict[str, Any]:
    """Topic feed：按 event_family 或标题/实体关键词聚合 L1 story cards。"""
    try:
        q = f"%{topic.strip()}%"
        params = {
            "topic": topic.strip(),
            "q": q,
            "l1_run_id": l1_run_id,
            "limit": page_size,
            "offset": (page - 1) * page_size,
        }
        where = """
            c.run_id = :l1_run_id
            AND c.article_count >= 2
            AND (
                c.event_family = :topic
                OR c.event_domain = :topic
                OR c.event_action = :topic
                OR c.title ILIKE :q
                OR c.initiator ILIKE :q
                OR c.target ILIKE :q
                OR c.location ILIKE :q
            )
        """
        rows = (
            db.execute(
                text(
                    f"""
                SELECT
                    COUNT(*) OVER() AS total_count,
                    c.cluster_id,
                    c.title AS canonical_title,
                    c.article_count,
                    c.event_domain,
                    c.event_family,
                    c.event_action,
                    c.initiator,
                    c.target,
                    c.location,
                    c.start_date,
                    c.end_date,
                    COALESCE(sb.source_count, 0) AS source_count,
                    COALESCE(sb.political_group_pct_reviewed_known_sources, '{{}}'::jsonb)
                        AS political_group_pct_reviewed_known_sources,
                    COALESCE(sb.country_counts, '{{}}'::jsonb) AS country_counts,
                    COALESCE(sb.source_type_counts, '{{}}'::jsonb) AS source_type_counts
                FROM public.event_coref_clusters AS c
                LEFT JOIN public.story_source_breakdown AS sb ON sb.story_id = c.cluster_id
                WHERE {where}
                ORDER BY COALESCE(c.end_date, c.start_date) DESC NULLS LAST, c.article_count DESC, c.cluster_id
                LIMIT :limit OFFSET :offset
                """
                ),
                params,
            )
            .mappings()
            .fetchall()
        )
        items = _rows(rows)
        total = _safe_int(items[0].get("total_count")) if items else 0
        for item in items:
            item.pop("total_count", None)
            item["display_title"] = _story_public_title(item)
            item["detail_url"] = (
                f"/data-service/ground-news-desk?cluster_id={item.get('cluster_id')}"
            )
        return {
            "topic": topic,
            "items": items,
            "total": total,
            "page": page,
            "page_size": page_size,
            "run_id": l1_run_id,
        }
    except Exception:
        raise HTTPException(status_code=500, detail="topic feed is unavailable") from None


@router.get("/api/story-graph/ground-news/search")
def search_ground_news_product(
    q: str = Query(..., min_length=1),
    page_size: int = Query(20, ge=1, le=80),
    l1_run_id: str = Query("fast_l1_v2"),
    l2_run_id: str = Query("fast_l2_v1"),
    db: Session = Depends(get_l1_db),
) -> Dict[str, Any]:
    """全站搜索：stories、sources、timelines。"""
    try:
        pattern = f"%{q.strip()}%"
        stories = _rows(
            db.execute(
                text(
                    """
                    SELECT
                        c.cluster_id,
                        c.title AS canonical_title,
                        c.article_count,
                        c.event_family,
                        c.initiator,
                        c.target,
                        c.start_date,
                        c.end_date,
                        COALESCE(sb.source_count, 0) AS source_count,
                        COALESCE(sb.political_group_pct_reviewed_known_sources, '{}'::jsonb)
                            AS political_group_pct_reviewed_known_sources
                    FROM public.event_coref_clusters AS c
                    LEFT JOIN public.story_source_breakdown AS sb ON sb.story_id = c.cluster_id
                    WHERE c.run_id = :l1_run_id
                      AND c.article_count >= 2
                      AND (
                        c.title ILIKE :q OR c.initiator ILIKE :q OR c.target ILIKE :q
                        OR c.location ILIKE :q OR c.event_family ILIKE :q
                      )
                    ORDER BY c.article_count DESC, COALESCE(c.end_date, c.start_date) DESC NULLS LAST
                    LIMIT :limit
                    """
                ),
                {"q": pattern, "l1_run_id": l1_run_id, "limit": page_size},
            )
            .mappings()
            .fetchall()
        )
        raw_sources = _rows(
            db.execute(
                text(
                    """
                    SELECT
                        domain,
                        source_name,
                        country,
                        region,
                        region_code,
                        source_type,
                        ownership_type,
                        geo_alignment,
                        political_leaning,
                        credibility_tier,
                        label_confidence,
                        evidence_url,
                        evidence_note,
                        review_status,
                        article_count_snapshot,
                        profile_version,
                        updated_at
                    FROM public.media_source_profile
                    WHERE domain ILIKE :q OR source_name ILIKE :q OR country ILIKE :q
                    ORDER BY article_count_snapshot DESC NULLS LAST, source_name NULLS LAST, domain
                    LIMIT :limit
                    """
                ),
                {"q": pattern, "limit": page_size},
            )
            .mappings()
            .fetchall()
        )
        sources = [
            build_source_profile_contract(
                source,
                fallback_domain=str(source.get("domain") or ""),
            )
            for source in raw_sources
        ]
        timelines = _rows(
            db.execute(
                text(
                    """
                    SELECT chain_id, title, segment_count, article_count, family_group,
                           chain_quality, quality_score, start_date, end_date
                    FROM public.event_l2_chains
                    WHERE run_id = :l2_run_id
                      AND (title ILIKE :q OR pair_key ILIKE :q OR initiator ILIKE :q OR target ILIKE :q)
                    ORDER BY quality_score DESC NULLS LAST, article_count DESC, end_date DESC NULLS LAST
                    LIMIT :limit
                    """
                ),
                {"q": pattern, "l2_run_id": l2_run_id, "limit": page_size},
            )
            .mappings()
            .fetchall()
        )
        for item in stories:
            item["display_title"] = _story_public_title(item)
            item["detail_url"] = (
                f"/data-service/ground-news-desk?cluster_id={item.get('cluster_id')}"
            )
        return {"q": q, "stories": stories, "sources": sources, "timelines": timelines}
    except Exception:
        raise HTTPException(status_code=500, detail="Ground News search is unavailable") from None


@router.get("/api/story-graph/ground-news/timeline/{chain_id}")
def get_ground_news_timeline(
    chain_id: str,
    l1_run_id: str = Query("fast_l1_v2"),
    l2_run_id: str = Query("fast_l2_v1"),
    db: Session = Depends(get_l1_db),
) -> Dict[str, Any]:
    """Ground-News-style timeline：L2 节点按 L1 story 增强 coverage/bias/cover 信息。"""
    try:
        chain = (
            db.execute(
                text(
                    "SELECT * FROM public.event_l2_chains WHERE run_id = :l2_run_id AND chain_id = :chain_id"
                ),
                {"l2_run_id": l2_run_id, "chain_id": chain_id},
            )
            .mappings()
            .first()
        )
        if not chain:
            raise HTTPException(status_code=404, detail="L2 timeline 不存在")
        rows = (
            db.execute(
                text(
                    """
                SELECT
                    cs.segment_order,
                    cs.edge_type,
                    cs.edge_weight,
                    cs.relation_reason,
                    cs.title_similarity,
                    cs.shared_topic_count,
                    cs.gap_days,
                    s.segment_id,
                    s.l1_cluster_id,
                    s.story_angle,
                    s.title AS segment_title,
                    s.article_count AS segment_article_count,
                    s.event_family,
                    s.event_action,
                    s.start_date,
                    s.end_date,
                    c.title AS story_title,
                    c.article_count AS story_article_count,
                    c.initiator,
                    c.target,
                    c.location,
                    COALESCE(sb.source_count, 0) AS source_count,
                    COALESCE(sb.country_counts, '{}'::jsonb) AS country_counts,
                    COALESCE(sb.source_type_counts, '{}'::jsonb) AS source_type_counts,
                    COALESCE(sb.credibility_tier_counts, '{}'::jsonb) AS credibility_tier_counts,
                    COALESCE(sb.ownership_type_counts, '{}'::jsonb) AS ownership_type_counts,
                    COALESCE(sb.political_group_pct_reviewed_known_sources, '{}'::jsonb)
                        AS political_group_pct_reviewed_known_sources,
                    COALESCE(sb.reviewed_known_political_source_count, 0)
                        AS reviewed_known_political_source_count,
                    COALESCE(sb.unknown_political_source_count, 0) AS unknown_political_source_count,
                    sc.cover_url,
                    sc.credit AS cover_credit
                FROM public.event_l2_chain_segments AS cs
                JOIN public.event_l15_segments AS s
                  ON s.run_id = cs.l15_run_id
                 AND s.segment_id = cs.segment_id
                LEFT JOIN public.event_coref_clusters AS c
                  ON c.cluster_id = s.l1_cluster_id
                 AND c.run_id = :l1_run_id
                LEFT JOIN public.story_source_breakdown AS sb ON sb.story_id = s.l1_cluster_id
                LEFT JOIN public.story_cover_assets AS sc
                  ON sc.cluster_id = s.l1_cluster_id
                 AND sc.run_id = :l1_run_id
                 AND sc.status = 'ok'
                WHERE cs.run_id = :l2_run_id
                  AND cs.chain_id = :chain_id
                ORDER BY cs.segment_order
                """
                ),
                {"chain_id": chain_id, "l1_run_id": l1_run_id, "l2_run_id": l2_run_id},
            )
            .mappings()
            .fetchall()
        )
        nodes = _rows(rows)[:_GROUND_NEWS_TIMELINE_NODE_LIMIT]
        for node in nodes:
            node["display_title"] = (
                node.get("segment_title") or node.get("story_title") or node.get("segment_id")
            )
            node["detail_url"] = (
                f"/data-service/ground-news-desk?cluster_id={node.get('l1_cluster_id')}"
            )
            node["blindspot"] = _blindspot_assessment(
                {
                    "article_count": node.get("story_article_count")
                    or node.get("segment_article_count"),
                    "source_count": node.get("source_count"),
                    "political_group_pct_reviewed_known_sources": node.get(
                        "political_group_pct_reviewed_known_sources"
                    ),
                },
                node,
            )
            node["cover"] = {
                "kind": "remote_image" if node.get("cover_url") else "editorial_vector",
                "image_url": node.get("cover_url"),
                "credit": node.get("cover_credit") or "",
            }
        edges: List[Dict[str, Any]] = []
        for prev, curr in zip(nodes, nodes[1:]):
            relation_projection = project_story_relation(
                edge_type=curr.get("edge_type"),
                relation_reason=curr.get("relation_reason"),
                derivation="stored_derived_relation",
            )
            edges.append(
                {
                    "from_id": prev.get("segment_id"),
                    "to_id": curr.get("segment_id"),
                    "edge_type": relation_projection.public_edge_type,
                    "edge_weight": curr.get("edge_weight"),
                    "relation_reason": relation_projection.public_relation_reason,
                    "title_similarity": curr.get("title_similarity"),
                    "shared_topic_count": curr.get("shared_topic_count"),
                    "gap_days": curr.get("gap_days"),
                    "claim": build_unavailable_story_relation_claim(
                        graph_scope_id=f"ground-news-l2:{l2_run_id}:{chain_id}",
                        from_id=prev.get("segment_id"),
                        to_id=curr.get("segment_id"),
                        relation_kind=relation_projection.public_edge_type,
                        derivation="stored_derived_relation",
                    ).model_dump(mode="json"),
                    "relation_semantics": relation_projection.semantics.model_dump(
                        mode="json"
                    ),
                }
            )
        _strip_node_relation_inputs(nodes)
        sampling = build_graph_sampling_provenance(
            build_graph_sampling_component(
                unit="l15_segment_node",
                requested_count=_GROUND_NEWS_TIMELINE_NODE_LIMIT,
                evaluated_count=_known_graph_count(chain.get("segment_count")),
                returned_count=len(nodes),
                limit=_GROUND_NEWS_TIMELINE_NODE_LIMIT,
                selection_rule="ordered_chain_segments",
                reason_codes=[
                    "DISPLAY_LIMIT",
                    "GRAPH_COMPLETENESS_NOT_ESTABLISHED",
                ],
            )
        )
        return {
            "chain": _row_dict(chain),
            "nodes": nodes,
            "edges": edges,
            "run_ids": {"l1": l1_run_id, "l2": l2_run_id},
            "sampling": sampling.model_dump(mode="json"),
        }
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=500, detail="Ground News timeline is unavailable") from None


@router.get("/api/story-graph/ground-news/{cluster_id}")
def get_ground_news_story_card(
    cluster_id: str,
    l1_run_id: str = Query("fast_l1_v2"),
    l15_run_id: str = Query("fast_l15_v1"),
    l2_run_id: str = Query("fast_l2_v1"),
    db: Session = Depends(get_l1_db),
) -> Dict[str, Any]:
    """单个 L1 story card 详情：L1 元信息、L1.5 sections、来源构成、相关 L2 链。"""
    try:
        story = (
            db.execute(
                text(
                    """
                SELECT
                    c.*,
                    COALESCE(main_title.title, c.title) AS canonical_title,
                    sc.cover_url,
                    sc.cover_kind,
                    sc.source_news_id AS cover_source_news_id,
                    sc.credit AS cover_credit,
                    sc.score AS cover_score
                FROM public.event_coref_clusters AS c
                LEFT JOIN public.story_cover_assets AS sc
                  ON sc.cluster_id = c.cluster_id
                 AND sc.run_id = c.run_id
                 AND sc.status = 'ok'
                LEFT JOIN LATERAL (
                    SELECT s.title
                    FROM public.event_l15_segments AS s
                    WHERE s.run_id = :l15_run_id
                      AND s.l1_cluster_id = c.cluster_id
                      AND s.story_angle IN ('main_event', 'context_update', 'outcome_reaction')
                    ORDER BY
                        CASE s.story_angle
                            WHEN 'main_event' THEN 0
                            WHEN 'context_update' THEN 1
                            ELSE 2
                        END,
                        s.article_count DESC,
                        s.start_date NULLS LAST
                    LIMIT 1
                ) AS main_title ON TRUE
                WHERE c.run_id = :l1_run_id
                  AND c.cluster_id = :cluster_id
                """
                ),
                {"cluster_id": cluster_id, "l1_run_id": l1_run_id, "l15_run_id": l15_run_id},
            )
            .mappings()
            .first()
        )
        if not story:
            raise HTTPException(status_code=404, detail="L1 story card 不存在")

        source_breakdown = (
            db.execute(
                text("SELECT * FROM public.story_source_breakdown WHERE story_id = :cluster_id"),
                {"cluster_id": cluster_id},
            )
            .mappings()
            .first()
        )

        segments = (
            db.execute(
                text(
                    """
                SELECT s.*
                FROM public.event_l15_segments AS s
                WHERE s.run_id = :l15_run_id
                  AND s.l1_cluster_id = :cluster_id
                ORDER BY
                    s.start_date NULLS LAST,
                    CASE s.story_angle
                        WHEN 'main_event' THEN 0
                        WHEN 'context_update' THEN 1
                        WHEN 'outcome_reaction' THEN 2
                        WHEN 'market_reaction' THEN 3
                        WHEN 'analysis_context' THEN 4
                        WHEN 'preview_planning' THEN 5
                        WHEN 'official_update' THEN 6
                        WHEN 'video_clip' THEN 7
                        ELSE 8
                    END,
                    s.article_count DESC
                """
                ),
                {"cluster_id": cluster_id, "l15_run_id": l15_run_id},
            )
            .mappings()
            .fetchall()
        )
        segment_items = _rows(segments)
        segment_ids = [item["segment_id"] for item in segment_items]
        sample_by_segment: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        if segment_ids:
            sample_rows = (
                db.execute(
                    text(
                        """
                    WITH ranked AS MATERIALIZED (
                        SELECT
                            m.segment_id,
                            m.news_id,
                            row_number() OVER (
                                PARTITION BY m.segment_id
                                ORDER BY m.published_at NULLS LAST, m.news_id
                            ) AS rn
                        FROM public.event_l15_members AS m
                        WHERE m.run_id = :l15_run_id
                          AND m.segment_id = ANY(:segment_ids)
                    )
                    SELECT
                        r.segment_id,
                        n.id AS news_id,
                        n.title,
                        n.published_at,
                        n.url
                    FROM ranked AS r
                    JOIN public.news AS n ON n.id = r.news_id
                    WHERE r.rn <= 8
                    ORDER BY r.segment_id, n.published_at NULLS LAST, n.id
                    """
                    ),
                    {"l15_run_id": l15_run_id, "segment_ids": segment_ids},
                )
                .mappings()
                .fetchall()
            )
            for row in _rows(sample_rows):
                segment_id = row.pop("segment_id")
                sample_by_segment[segment_id].append(row)
        for item in segment_items:
            item["sample_news"] = sample_by_segment.get(item["segment_id"], [])

        l2_chains = (
            db.execute(
                text(
                    """
                SELECT c.*
                FROM public.event_l2_chains AS c
                WHERE c.run_id = :l2_run_id
                  AND EXISTS (
                      SELECT 1
                      FROM public.event_l2_chain_segments AS s
                      WHERE s.run_id = c.run_id
                        AND s.chain_id = c.chain_id
                        AND s.l1_cluster_id = :cluster_id
                  )
                ORDER BY
                    CASE c.chain_quality
                        WHEN 'strong' THEN 0
                        WHEN 'usable' THEN 1
                        ELSE 2
                    END,
                    c.quality_score DESC NULLS LAST,
                    c.segment_count DESC,
                    c.article_count DESC,
                    c.start_date NULLS LAST
                LIMIT 12
                """
                ),
                {"cluster_id": cluster_id, "l2_run_id": l2_run_id},
            )
            .mappings()
            .fetchall()
        )

        evidence = (
            db.execute(
                text(
                    """
                SELECT
                    n.id AS news_id,
                    n.title,
                    n.published_at,
                    n.url,
                    ms.domain,
                    COALESCE(msp.source_name, ms.domain) AS source_name,
                    msp.country,
                    msp.region,
                    msp.region_code,
                    msp.source_type,
                    msp.ownership_type,
                    msp.geo_alignment,
                    msp.political_leaning,
                    msp.credibility_tier,
                    msp.label_confidence,
                    msp.evidence_url,
                    msp.evidence_note,
                    msp.review_status,
                    msp.profile_version,
                    msp.updated_at AS profile_updated_at
                FROM public.event_coref_members AS m
                JOIN public.news AS n ON n.id = m.news_id
                LEFT JOIN public.media_source AS ms ON ms.id = n.media_source_id
                LEFT JOIN public.media_source_profile AS msp ON msp.domain = ms.domain
                WHERE m.run_id = :l1_run_id
                  AND m.cluster_id = :cluster_id
                ORDER BY n.published_at NULLS LAST, n.id
                LIMIT 100
                """
                ),
                {"cluster_id": cluster_id, "l1_run_id": l1_run_id},
            )
            .mappings()
            .fetchall()
        )

        story_item = _row_dict(story)
        story_item["display_title"] = _story_public_title(story_item)
        story_item["cover"] = {
            "kind": story_item.get("cover_kind")
            or ("remote_image" if story_item.get("cover_url") else "editorial_vector"),
            "image_url": story_item.get("cover_url"),
            "credit": story_item.get("cover_credit") or "",
            "source_news_id": story_item.get("cover_source_news_id"),
            "score": story_item.get("cover_score"),
        }
        source_breakdown_item = _row_dict(source_breakdown)
        evidence_items = _rows(evidence)
        comparison = _make_story_comparison(
            story_item,
            source_breakdown_item,
            segment_items,
            evidence_items,
        )

        return {
            "story": story_item,
            "source_breakdown": source_breakdown_item,
            "segments": segment_items,
            "related_l2_chains": _rows(l2_chains),
            "evidence": comparison["source_table"],
            "comparison": comparison,
            "run_ids": {"l1": l1_run_id, "l15": l15_run_id, "l2": l2_run_id},
        }
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=500, detail="Ground News story card is unavailable") from None


@router.get("/api/story-graph/l2-chain/list")
def list_l2_chains(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    q: Optional[str] = Query(None),
    family_group: Optional[str] = Query(None),
    chain_quality: Optional[str] = Query(None),
    min_segments: int = Query(2, ge=2, le=50),
    sort: str = Query(
        "recent", description="recent=最新优先，quality=质量优先，impact=覆盖/文章量优先"
    ),
    run_id: str = Query("fast_l2_v1"),
    db: Session = Depends(get_l1_db),
) -> Dict[str, Any]:
    """L2 大事件/走势链列表。"""
    try:
        where = ["c.run_id = :run_id", "c.segment_count >= :min_segments"]
        params: Dict[str, Any] = {
            "run_id": run_id,
            "min_segments": min_segments,
            "limit": page_size,
            "offset": (page - 1) * page_size,
        }
        if family_group:
            where.append("c.family_group = :family_group")
            params["family_group"] = family_group
        if chain_quality:
            where.append("c.chain_quality = :chain_quality")
            params["chain_quality"] = chain_quality
        if q:
            where.append(
                "(c.title ILIKE :q OR c.pair_key ILIKE :q OR c.initiator ILIKE :q OR c.target ILIKE :q)"
            )
            params["q"] = f"%{q.strip()}%"
        sort_mode = (sort or "recent").strip().lower()
        if sort_mode not in {"recent", "quality", "impact", "coverage"}:
            sort_mode = "recent"
        if sort_mode in {"impact", "coverage"}:
            order_by_sql = """
                    c.article_count DESC,
                    c.segment_count DESC,
                    COALESCE(c.end_date, c.start_date) DESC NULLS LAST,
                    c.quality_score DESC NULLS LAST,
                    c.chain_id
            """
        elif sort_mode == "quality":
            order_by_sql = """
                    CASE c.chain_quality
                        WHEN 'strong' THEN 0
                        WHEN 'usable' THEN 1
                        ELSE 2
                    END,
                    c.quality_score DESC NULLS LAST,
                    c.segment_count DESC,
                    c.article_count DESC,
                    COALESCE(c.end_date, c.start_date) DESC NULLS LAST,
                    c.chain_id
            """
        else:
            order_by_sql = """
                    COALESCE(c.end_date, c.start_date) DESC NULLS LAST,
                    CASE c.chain_quality
                        WHEN 'strong' THEN 0
                        WHEN 'usable' THEN 1
                        ELSE 2
                    END,
                    c.quality_score DESC NULLS LAST,
                    c.article_count DESC,
                    c.chain_id
            """

        result = (
            db.execute(
                text(
                    f"""
                SELECT
                    COUNT(*) OVER() AS total_count,
                    c.*,
                    COALESCE(edge_stats.avg_edge_weight, 0) AS avg_edge_weight,
                    COALESCE(edge_stats.min_edge_weight, 0) AS min_edge_weight,
                    COALESCE(edge_stats.avg_title_similarity, 0) AS avg_title_similarity
                FROM public.event_l2_chains AS c
                LEFT JOIN LATERAL (
                    SELECT
                        AVG(NULLIF(edge_weight, 1.0)) FILTER (WHERE segment_order > 1) AS avg_edge_weight,
                        MIN(edge_weight) FILTER (WHERE segment_order > 1) AS min_edge_weight,
                        AVG(title_similarity) FILTER (WHERE segment_order > 1) AS avg_title_similarity
                    FROM public.event_l2_chain_segments
                    WHERE run_id = c.run_id
                      AND chain_id = c.chain_id
                ) AS edge_stats ON TRUE
                WHERE {" AND ".join(where)}
                ORDER BY
                    {order_by_sql}
                LIMIT :limit OFFSET :offset
                """
                ),
                params,
            )
            .mappings()
            .fetchall()
        )
        total = int(result[0]["total_count"]) if result else 0
        chains = []
        for row in result:
            item = _row_dict(row)
            item.pop("total_count", None)
            chains.append(item)
        latest_end_date = max(
            (item.get("end_date") or item.get("start_date") for item in chains), default=None
        )
        return {
            "chains": chains,
            "total": total,
            "page": page,
            "page_size": page_size,
            "run_id": run_id,
            "sort": sort_mode,
            "freshness": {"latest_end_date": latest_end_date},
        }
    except Exception:
        raise HTTPException(status_code=500, detail="L2 chains are unavailable") from None


@router.get("/api/story-graph/l2-chain/{chain_id}")
def get_l2_chain_graph(
    chain_id: str,
    run_id: str = Query("fast_l2_v1"),
    db: Session = Depends(get_l1_db),
) -> Dict[str, Any]:
    """单条 L2 走势链，返回 segment 节点和带质量字段的有向边。"""
    try:
        chain = (
            db.execute(
                text(
                    "SELECT * FROM public.event_l2_chains WHERE run_id = :run_id AND chain_id = :chain_id"
                ),
                {"run_id": run_id, "chain_id": chain_id},
            )
            .mappings()
            .first()
        )
        if not chain:
            raise HTTPException(status_code=404, detail="L2 chain 不存在")

        segment_rows = (
            db.execute(
                text(
                    """
                SELECT
                    cs.segment_order,
                    cs.edge_type,
                    cs.edge_weight,
                    cs.relation_reason,
                    cs.title_similarity,
                    cs.shared_topic_count,
                    cs.gap_days,
                    s.*
                FROM public.event_l2_chain_segments AS cs
                JOIN public.event_l15_segments AS s
                  ON s.run_id = cs.l15_run_id
                 AND s.segment_id = cs.segment_id
                WHERE cs.run_id = :run_id
                  AND cs.chain_id = :chain_id
                ORDER BY cs.segment_order
                """
                ),
                {"run_id": run_id, "chain_id": chain_id},
            )
            .mappings()
            .fetchall()
        )
        nodes = _rows(segment_rows)[:_L2_CHAIN_NODE_LIMIT]
        edges: List[Dict[str, Any]] = []
        for prev, curr in zip(nodes, nodes[1:]):
            relation_projection = project_story_relation(
                edge_type=curr.get("edge_type"),
                relation_reason=curr.get("relation_reason"),
                derivation="stored_derived_relation",
            )
            edges.append(
                {
                    "from_id": prev["segment_id"],
                    "to_id": curr["segment_id"],
                    "edge_type": relation_projection.public_edge_type,
                    "edge_weight": curr.get("edge_weight"),
                    "relation_reason": relation_projection.public_relation_reason,
                    "title_similarity": curr.get("title_similarity"),
                    "shared_topic_count": curr.get("shared_topic_count"),
                    "gap_days": curr.get("gap_days"),
                    "claim": build_unavailable_story_relation_claim(
                        graph_scope_id=f"l2:{run_id}:{chain_id}",
                        from_id=prev["segment_id"],
                        to_id=curr["segment_id"],
                        relation_kind=relation_projection.public_edge_type,
                        derivation="stored_derived_relation",
                    ).model_dump(mode="json"),
                    "relation_semantics": relation_projection.semantics.model_dump(mode="json"),
                }
            )
        _strip_node_relation_inputs(nodes)
        sampling = build_graph_sampling_provenance(
            build_graph_sampling_component(
                unit="l15_segment_node",
                requested_count=_L2_CHAIN_NODE_LIMIT,
                evaluated_count=_known_graph_count(chain.get("segment_count")),
                returned_count=len(nodes),
                limit=_L2_CHAIN_NODE_LIMIT,
                selection_rule="ordered_chain_segments",
                reason_codes=[
                    "DISPLAY_LIMIT",
                    "GRAPH_COMPLETENESS_NOT_ESTABLISHED",
                ],
            )
        )
        return {
            "chain": _row_dict(chain),
            "nodes": nodes,
            "edges": edges,
            "run_id": run_id,
            "sampling": sampling.model_dump(mode="json"),
        }
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=500, detail="L2 chain is unavailable") from None


@router.get("/api/story-graph/l3-macro/list")
def list_l3_macros(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    q: Optional[str] = Query(None),
    family_group: Optional[str] = Query(None),
    min_chains: int = Query(8, ge=1, le=5000),
    sort: str = Query(
        "recent", description="recent=最新优先，quality=质量优先，impact=覆盖/文章量优先"
    ),
    run_id: str = Query("fast_l3_v1"),
    db: Session = Depends(get_l1_db),
) -> Dict[str, Any]:
    """L3 大事件/宏观脉络列表。"""
    try:
        where = ["m.run_id = :run_id", "m.l2_chain_count >= :min_chains"]
        params: Dict[str, Any] = {
            "run_id": run_id,
            "min_chains": min_chains,
            "limit": page_size,
            "offset": (page - 1) * page_size,
        }
        if family_group:
            where.append("m.family_group = :family_group")
            params["family_group"] = family_group
        if q:
            where.append("(m.title ILIKE :q OR m.macro_key ILIKE :q OR m.summary ILIKE :q)")
            params["q"] = f"%{q.strip()}%"
        sort_mode = (sort or "recent").strip().lower()
        if sort_mode not in {"recent", "quality", "impact", "coverage"}:
            sort_mode = "recent"
        if sort_mode in {"impact", "coverage"}:
            order_by_sql = """
                    m.article_count DESC,
                    m.segment_count DESC,
                    m.l2_chain_count DESC,
                    m.end_date DESC NULLS LAST,
                    m.quality_score DESC NULLS LAST,
                    m.macro_id
            """
        elif sort_mode == "quality":
            order_by_sql = """
                    m.quality_score DESC NULLS LAST,
                    m.l2_chain_count DESC,
                    m.segment_count DESC,
                    m.article_count DESC,
                    m.end_date DESC NULLS LAST,
                    m.macro_id
            """
        else:
            order_by_sql = """
                    m.end_date DESC NULLS LAST,
                    m.quality_score DESC NULLS LAST,
                    m.l2_chain_count DESC,
                    m.article_count DESC,
                    m.macro_id
            """

        result = (
            db.execute(
                text(
                    f"""
                SELECT COUNT(*) OVER() AS total_count, m.*
                FROM public.event_l3_macro_events AS m
                WHERE {" AND ".join(where)}
                ORDER BY
                    {order_by_sql}
                LIMIT :limit OFFSET :offset
                """
                ),
                params,
            )
            .mappings()
            .fetchall()
        )
        total = int(result[0]["total_count"]) if result else 0
        macros = []
        for row in result:
            item = _row_dict(row)
            item.pop("total_count", None)
            macros.append(item)
        latest_end_date = max(
            (item.get("end_date") or item.get("start_date") for item in macros), default=None
        )
        return {
            "macros": macros,
            "total": total,
            "page": page,
            "page_size": page_size,
            "run_id": run_id,
            "sort": sort_mode,
            "freshness": {"latest_end_date": latest_end_date},
        }
    except Exception:
        raise HTTPException(status_code=500, detail="L3 macro events are unavailable") from None


@router.get("/api/story-graph/l3-macro/{macro_id}")
def get_l3_macro_graph(
    macro_id: str,
    run_id: str = Query("fast_l3_v1"),
    max_nodes: int = Query(56, ge=8, le=240),
    db: Session = Depends(get_l1_db),
) -> Dict[str, Any]:
    """单个 L3 大事件，返回 L2 micro-chain 节点和宏观影响边。"""
    try:
        macro = (
            db.execute(
                text(
                    "SELECT * FROM public.event_l3_macro_events WHERE run_id = :run_id AND macro_id = :macro_id"
                ),
                {"run_id": run_id, "macro_id": macro_id},
            )
            .mappings()
            .first()
        )
        if not macro:
            raise HTTPException(status_code=404, detail="L3 macro event 不存在")

        lane_quota = max(6, math.ceil(max_nodes / 5))
        core_limit = max(12, math.ceil(max_nodes * 0.35))
        member_rows = (
            db.execute(
                text(
                    """
                WITH ranked AS (
                    SELECT
                        m.*,
                        c.chain_quality,
                        c.quality_score AS l2_quality_score,
                        c.event_family,
                        c.event_action,
                        c.initiator,
                        c.target,
                        ROW_NUMBER() OVER (
                            PARTITION BY m.lane
                            ORDER BY
                                m.importance_score DESC NULLS LAST,
                                m.segment_count DESC,
                                m.article_count DESC,
                                m.node_order
                        ) AS lane_rank,
                        ROW_NUMBER() OVER (
                            ORDER BY
                                m.importance_score DESC NULLS LAST,
                                m.segment_count DESC,
                                m.article_count DESC,
                                m.node_order
                        ) AS importance_rank
                    FROM public.event_l3_macro_members AS m
                    JOIN public.event_l2_chains AS c
                      ON c.run_id = m.l2_run_id
                     AND c.chain_id = m.l2_chain_id
                    WHERE m.run_id = :run_id
                      AND m.macro_id = :macro_id
                )
                SELECT *
                FROM ranked
                WHERE lane_rank <= :lane_quota
                   OR importance_rank <= :core_limit
                ORDER BY node_order
                LIMIT :max_nodes
                """
                ),
                {
                    "run_id": run_id,
                    "macro_id": macro_id,
                    "max_nodes": max_nodes,
                    "lane_quota": lane_quota,
                    "core_limit": core_limit,
                },
            )
            .mappings()
            .fetchall()
        )
        nodes = _rows(member_rows)[:max_nodes]
        node_ids = [item["l2_chain_id"] for item in nodes]

        edges: List[Dict[str, Any]] = []
        if node_ids:
            edge_rows = (
                db.execute(
                    text(
                        """
                    SELECT *
                    FROM public.event_l3_macro_edges
                    WHERE run_id = :run_id
                      AND macro_id = :macro_id
                      AND from_chain_id = ANY(:node_ids)
                      AND to_chain_id = ANY(:node_ids)
                    ORDER BY
                        CASE layer WHEN 'story' THEN 0 ELSE 1 END,
                        edge_weight DESC NULLS LAST,
                        gap_days NULLS LAST
                    """
                    ),
                    {"run_id": run_id, "macro_id": macro_id, "node_ids": node_ids},
                )
                .mappings()
                .fetchall()
            )
            edges = _rows(edge_rows)
            for edge in edges:
                source_edge = dict(edge)
                relation_projection = project_story_relation(
                    edge_type=source_edge.get("edge_type"),
                    relation_reason=source_edge.get("relation_reason"),
                    derivation="stored_derived_relation",
                )
                edge.clear()
                edge.update(
                    {
                        field: source_edge.get(field)
                        for field in (
                            "macro_id",
                            "run_id",
                            "from_chain_id",
                            "to_chain_id",
                            "layer",
                            "edge_weight",
                            "gap_days",
                            "shared_actor_count",
                            "shared_topic_count",
                            "title_similarity",
                        )
                    }
                )
                edge.update(
                    {
                        "edge_type": relation_projection.public_edge_type,
                        "relation_reason": relation_projection.public_relation_reason,
                        "relation_semantics": relation_projection.semantics.model_dump(
                            mode="json"
                        ),
                    }
                )
                edge["claim"] = build_unavailable_story_relation_claim(
                    graph_scope_id=f"l3:{run_id}:{macro_id}",
                    from_id=source_edge.get("from_chain_id"),
                    to_id=source_edge.get("to_chain_id"),
                    relation_kind=relation_projection.public_edge_type,
                    derivation="stored_derived_relation",
                ).model_dump(mode="json")

        existing_pairs = {(edge.get("from_chain_id"), edge.get("to_chain_id")) for edge in edges}
        ordered_nodes = sorted(
            nodes,
            key=lambda item: (_safe_int(item.get("node_order")), item.get("l2_chain_id") or ""),
        )
        for prev, curr in zip(ordered_nodes, ordered_nodes[1:]):
            pair = (prev.get("l2_chain_id"), curr.get("l2_chain_id"))
            if not pair[0] or not pair[1] or pair in existing_pairs:
                continue
            prev_end = prev.get("end_date")
            curr_start = curr.get("start_date")
            gap = None
            if prev_end and curr_start:
                try:
                    gap = (
                        datetime.fromisoformat(str(curr_start)).date()
                        - datetime.fromisoformat(str(prev_end)).date()
                    ).days
                except Exception:
                    gap = None
            relation_projection = project_story_relation(
                edge_type="macro_sequence",
                relation_reason="可视节点时间推进",
                derivation="layout_sequence",
            )
            edges.append(
                {
                    "macro_id": macro_id,
                    "run_id": run_id,
                    "from_chain_id": pair[0],
                    "to_chain_id": pair[1],
                    "edge_type": relation_projection.public_edge_type,
                    "layer": "story",
                    "edge_weight": 0.36,
                    "relation_reason": relation_projection.public_relation_reason,
                    "gap_days": gap,
                    "shared_actor_count": None,
                    "shared_topic_count": None,
                    "title_similarity": None,
                    "metadata": {},
                    "claim": build_unavailable_story_relation_claim(
                        graph_scope_id=f"l3:{run_id}:{macro_id}",
                        from_id=pair[0],
                        to_id=pair[1],
                        relation_kind=relation_projection.public_edge_type,
                        derivation="layout_sequence",
                    ).model_dump(mode="json"),
                    "relation_semantics": relation_projection.semantics.model_dump(mode="json"),
                }
            )
            existing_pairs.add(pair)

        sampling = build_graph_sampling_provenance(
            build_graph_sampling_component(
                unit="l2_chain_node",
                requested_count=max_nodes,
                evaluated_count=_known_graph_count(macro.get("l2_chain_count")),
                returned_count=len(nodes),
                limit=max_nodes,
                selection_rule="lane_quota_then_importance",
                reason_codes=[
                    "DISPLAY_LIMIT",
                    "FILTERED_BY_SELECTION_RULE",
                    "GRAPH_COMPLETENESS_NOT_ESTABLISHED",
                ],
            )
        )
        return {
            "macro": _row_dict(macro),
            "nodes": nodes,
            "edges": edges,
            "run_id": run_id,
            "visible_node_count": len(nodes),
            "total_node_count": _known_graph_count(macro.get("l2_chain_count")),
            "sampling": sampling.model_dump(mode="json"),
        }
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=500, detail="L3 macro event is unavailable") from None


@router.get("/api/story-graph/evidence")
def get_story_graph_evidence(
    cluster_id: Optional[str] = Query(None, max_length=256),
    segment_id: Optional[str] = Query(None, max_length=256),
    chain_id: Optional[str] = Query(None, max_length=256),
    macro_id: Optional[str] = Query(None, max_length=256),
    run_id: str = Query("fast_l2_v1", min_length=1, max_length=128),
    l3_run_id: str = Query("fast_l3_v1", min_length=1, max_length=128),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_l1_db),
) -> Dict[str, Any]:
    """Return clickable source articles for L1/L1.5/L2/L3 graph nodes."""
    cluster_id = _evidence_target(cluster_id, field="cluster_id")
    segment_id = _evidence_target(segment_id, field="segment_id")
    chain_id = _evidence_target(chain_id, field="chain_id")
    macro_id = _evidence_target(macro_id, field="macro_id")
    targets = tuple(
        value for value in (cluster_id, segment_id, chain_id, macro_id) if value
    )
    if len(targets) != 1:
        raise HTTPException(
            status_code=422,
            detail="exactly one story graph evidence target is required",
        )
    try:
        if cluster_id:
            meta = (
                db.execute(
                    text(
                        """
                    SELECT cluster_id, title, event_type, initiator, target,
                           article_count, start_date, end_date
                    FROM public.event_coref_clusters
                    WHERE cluster_id = :cluster_id
                    """
                    ),
                    {"cluster_id": cluster_id},
                )
                .mappings()
                .first()
            )
            rows = (
                db.execute(
                    text(
                        """
                    SELECT n.id AS news_id, n.title, n.published_at, n.url,
                           ecm.cluster_id AS l1_cluster_id, NULL::text AS segment_id
                    FROM public.event_coref_members AS ecm
                    JOIN public.news AS n ON n.id = ecm.news_id
                    WHERE ecm.cluster_id = :cluster_id
                    ORDER BY n.published_at DESC NULLS LAST, n.id DESC
                    LIMIT :limit
                    """
                    ),
                    {"cluster_id": cluster_id, "limit": limit},
                )
                .mappings()
                .fetchall()
            )
            payload = _row_dict(meta) if meta else {"cluster_id": cluster_id}
            payload.update({"scope": "cluster", "news": _news_rows_payload(rows)})
            return payload

        if segment_id:
            meta = (
                db.execute(
                    text(
                        """
                    SELECT segment_id, l1_cluster_id AS cluster_id, title, story_angle AS event_type,
                           initiator, target, article_count, start_date, end_date
                    FROM public.event_l15_segments
                    WHERE segment_id = :segment_id
                    """
                    ),
                    {"segment_id": segment_id},
                )
                .mappings()
                .first()
            )
            rows = (
                db.execute(
                    text(
                        """
                    WITH ranked AS (
                        SELECT n.id AS news_id, n.title, n.published_at, n.url,
                               lm.l1_cluster_id, lm.segment_id,
                               ROW_NUMBER() OVER (
                                   PARTITION BY n.id
                                   ORDER BY lm.membership_score DESC NULLS LAST,
                                            n.published_at DESC NULLS LAST,
                                            n.id DESC
                               ) AS rn
                        FROM public.event_l15_members AS lm
                        JOIN public.news AS n ON n.id = lm.news_id
                        WHERE lm.segment_id = :segment_id
                    )
                    SELECT news_id, title, published_at, url, l1_cluster_id, segment_id
                    FROM ranked
                    WHERE rn = 1
                    ORDER BY published_at DESC NULLS LAST, news_id DESC
                    LIMIT :limit
                    """
                    ),
                    {"segment_id": segment_id, "limit": limit},
                )
                .mappings()
                .fetchall()
            )
            payload = _row_dict(meta) if meta else {"segment_id": segment_id}
            payload.update({"scope": "segment", "news": _news_rows_payload(rows)})
            return payload

        if chain_id:
            meta = (
                db.execute(
                    text(
                        """
                    SELECT chain_id, title, family_group AS event_type, initiator, target,
                           article_count, start_date, end_date, segment_count, run_id
                    FROM public.event_l2_chains
                    WHERE run_id = :run_id
                      AND chain_id = :chain_id
                    """
                    ),
                    {"run_id": run_id, "chain_id": chain_id},
                )
                .mappings()
                .first()
            )
            rows = (
                db.execute(
                    text(
                        """
                    WITH chain_segments AS (
                        SELECT DISTINCT segment_id, l15_run_id, l1_cluster_id
                        FROM public.event_l2_chain_segments
                        WHERE run_id = :run_id
                          AND chain_id = :chain_id
                    ),
                    ranked AS (
                        SELECT n.id AS news_id, n.title, n.published_at, n.url,
                               COALESCE(lm.l1_cluster_id, cs.l1_cluster_id) AS l1_cluster_id,
                               cs.segment_id,
                               ROW_NUMBER() OVER (
                                   PARTITION BY n.id
                                   ORDER BY lm.membership_score DESC NULLS LAST,
                                            n.published_at DESC NULLS LAST,
                                            n.id DESC
                               ) AS rn
                        FROM chain_segments AS cs
                        LEFT JOIN public.event_l15_members AS lm
                          ON lm.run_id = cs.l15_run_id
                         AND lm.segment_id = cs.segment_id
                        JOIN public.news AS n ON n.id = lm.news_id
                    )
                    SELECT news_id, title, published_at, url, l1_cluster_id, segment_id
                    FROM ranked
                    WHERE rn = 1
                    ORDER BY published_at DESC NULLS LAST, news_id DESC
                    LIMIT :limit
                    """
                    ),
                    {"run_id": run_id, "chain_id": chain_id, "limit": limit},
                )
                .mappings()
                .fetchall()
            )
            payload = _row_dict(meta) if meta else {"chain_id": chain_id, "run_id": run_id}
            payload.update({"scope": "chain", "news": _news_rows_payload(rows)})
            return payload

        if macro_id:
            meta = (
                db.execute(
                    text(
                        """
                    SELECT macro_id, title, family_group AS event_type,
                           article_count, start_date, end_date, l2_run_id, l2_chain_count
                    FROM public.event_l3_macro_events
                    WHERE run_id = :l3_run_id
                      AND macro_id = :macro_id
                    """
                    ),
                    {"l3_run_id": l3_run_id, "macro_id": macro_id},
                )
                .mappings()
                .first()
            )
            rows = (
                db.execute(
                    text(
                        """
                    WITH macro_segments AS (
                        SELECT DISTINCT cs.segment_id, cs.l15_run_id, cs.l1_cluster_id
                        FROM public.event_l3_macro_members AS mm
                        JOIN public.event_l2_chain_segments AS cs
                          ON cs.run_id = mm.l2_run_id
                         AND cs.chain_id = mm.l2_chain_id
                        WHERE mm.run_id = :l3_run_id
                          AND mm.macro_id = :macro_id
                    ),
                    ranked AS (
                        SELECT n.id AS news_id, n.title, n.published_at, n.url,
                               COALESCE(lm.l1_cluster_id, ms.l1_cluster_id) AS l1_cluster_id,
                               ms.segment_id,
                               ROW_NUMBER() OVER (
                                   PARTITION BY n.id
                                   ORDER BY lm.membership_score DESC NULLS LAST,
                                            n.published_at DESC NULLS LAST,
                                            n.id DESC
                               ) AS rn
                        FROM macro_segments AS ms
                        LEFT JOIN public.event_l15_members AS lm
                          ON lm.run_id = ms.l15_run_id
                         AND lm.segment_id = ms.segment_id
                        JOIN public.news AS n ON n.id = lm.news_id
                    )
                    SELECT news_id, title, published_at, url, l1_cluster_id, segment_id
                    FROM ranked
                    WHERE rn = 1
                    ORDER BY published_at DESC NULLS LAST, news_id DESC
                    LIMIT :limit
                    """
                    ),
                    {"l3_run_id": l3_run_id, "macro_id": macro_id, "limit": limit},
                )
                .mappings()
                .fetchall()
            )
            payload = _row_dict(meta) if meta else {"macro_id": macro_id, "run_id": l3_run_id}
            payload.update({"scope": "macro", "news": _news_rows_payload(rows)})
            return payload

        raise HTTPException(status_code=422, detail="story graph evidence target is invalid")
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(
            status_code=500,
            detail="story graph evidence is unavailable",
        ) from None


@router.get("/api/story-graph/list")
def list_stories(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    event_type: Optional[str] = Query(None, description="按事件类型过滤"),
    db: Session = Depends(get_db),
) -> StoryListResponse:
    """获取所有故事概览列表。"""
    try:
        where = ""
        params: Dict[str, Any] = {}
        if event_type:
            where = "WHERE ms.event_type = :event_type"
            params["event_type"] = event_type

        # Only show new L2 stories (from story_edges + story_trees)
        try:
            new_rows = (
                db.execute(
                    text("""
                    SELECT se.story_id, COALESCE(st.title, '') as title,
                           MIN(ec.event_type) as event_type,
                           COUNT(DISTINCT se.from_cluster_id) as nodes,
                           COUNT(*) as edges,
                           MIN(ec.start_date) as start_date,
                           MAX(ec.end_date) as end_date
                    FROM story_edges se
                    LEFT JOIN story_trees st ON se.story_id = st.id
                    LEFT JOIN event_coref_clusters ec ON se.from_cluster_id = ec.cluster_id
                    GROUP BY se.story_id, st.title
                    HAVING COUNT(DISTINCT se.from_cluster_id) >= 2
                    ORDER BY edges DESC
                    LIMIT :limit OFFSET :offset
                """),
                    {"limit": page_size, "offset": (page - 1) * page_size},
                )
                .mappings()
                .fetchall()
            )
            total_new = (
                db.execute(
                    text(
                        "SELECT COUNT(DISTINCT story_id) FROM story_edges WHERE story_id IN (SELECT story_id FROM story_edges GROUP BY story_id HAVING COUNT(DISTINCT from_cluster_id) >= 2)"
                    ),
                ).scalar()
                or 0
            )
        except Exception:
            new_rows = []
            total_new = 0

        total = total_new
        stories = []
        for r in new_rows:
            stories.append(
                StoryListItem(
                    id=r["story_id"],
                    title=r["title"] or f"Story {r['story_id']}",
                    event_type=r["event_type"] or "",
                    article_count=r["nodes"],
                    cluster_count=r["edges"],
                    start_date=str(r["start_date"]) if r["start_date"] else None,
                    end_date=str(r["end_date"]) if r["end_date"] else None,
                )
            )

        return StoryListResponse(stories=stories, total=total)
    except Exception:
        raise HTTPException(status_code=500, detail="story list is unavailable") from None


@router.get("/api/story-graph/{story_id}")
def get_story_graph(
    story_id: int,
    include_related: bool = Query(False, description="是否返回 story-to-story 分析关系"),
    expanded: bool = Query(False, description="是否返回多分支扩展图"),
    related_limit: int = Query(12, ge=1, le=50),
    db: Session = Depends(get_db),
) -> StoryGraphResponse:
    """获取单条故事图的节点+边数据。"""
    try:
        include_related = include_related or expanded
        # 获取故事元信息（从 story_edges + story_trees）
        story_row = (
            db.execute(
                text("""
                SELECT se.story_id as id, COALESCE(st.title, '') as title,
                       '' as event_type,
                       COUNT(DISTINCT se.from_cluster_id) as article_count,
                       COUNT(*) as cluster_count,
                       MIN(ec.start_date) as start_date,
                       MAX(ec.end_date) as end_date,
                       st.meta as meta
                FROM story_edges se
                LEFT JOIN story_trees st ON se.story_id = st.id
                LEFT JOIN event_coref_clusters ec ON se.from_cluster_id = ec.cluster_id
                WHERE se.story_id = :sid
                GROUP BY se.story_id, st.title, st.meta
            """),
                {"sid": story_id},
            )
            .mappings()
            .first()
        )
        if not story_row:
            raise HTTPException(status_code=404, detail="故事不存在")
        story = story_row

        related_stories: List[StoryRelationItem] = []
        if include_related:
            related_stories = _fetch_story_relation_rows(db, story_id, related_limit)

        if expanded:
            related_lookup = {item.story_id: item for item in related_stories}
            story_ids = [story_id] + [item.story_id for item in related_stories]
            bundle = _fetch_story_bundle(db, story_ids, story_id, related_lookup)
            primary_story = bundle.get(story_id)
            if not primary_story:
                raise HTTPException(status_code=404, detail="故事不存在")

            expanded_nodes: List[StoryNode] = list(primary_story["nodes"])
            expanded_edges: List[StoryEdge] = list(primary_story["edges"])
            realized_related_stories: List[StoryRelationItem] = []

            for relation in related_stories:
                branch_story = bundle.get(relation.story_id)
                if not branch_story or not branch_story.get("nodes"):
                    continue
                realized_related_stories.append(relation)
                expanded_nodes.extend(branch_story["nodes"])
                expanded_edges.extend(branch_story["edges"])
                for source_node, target_node, bridge_score in _choose_bridge_pairs(
                    primary_story, branch_story, relation
                ):
                    relation_projection = project_story_relation(
                        edge_type=_bridge_edge_type(relation),
                        relation_reason=relation.reason,
                        derivation="computed_bridge",
                    )
                    expanded_edges.append(
                        StoryEdge(
                            from_id=source_node.id,
                            to_id=target_node.id,
                            edge_type=relation_projection.public_edge_type,
                            weight=bridge_score,
                            layer=relation.layer,
                            relation_reason=relation_projection.public_relation_reason,
                            source_story_id=source_node.story_id,
                            target_story_id=target_node.story_id,
                            claim=build_unavailable_story_relation_claim(
                                graph_scope_id=f"legacy-expanded:{story_id}",
                                from_id=source_node.id,
                                to_id=target_node.id,
                                relation_kind=relation_projection.public_edge_type,
                                derivation="computed_bridge",
                            ),
                            relation_semantics=relation_projection.semantics,
                        )
                    )

            dedup_nodes: Dict[str, StoryNode] = {node.id: node for node in expanded_nodes}
            dedup_edges: Dict[tuple[str, str, str, str, Optional[str]], StoryEdge] = {}
            for edge in expanded_edges:
                key = (
                    edge.from_id,
                    edge.to_id,
                    edge.edge_type,
                    edge.layer or "",
                    edge.relation_reason,
                )
                existing = dedup_edges.get(key)
                if existing is None or edge.weight > existing.weight:
                    dedup_edges[key] = edge

            evaluated_nodes = len(dedup_nodes)
            selected_nodes = list(dedup_nodes.values())[:_LEGACY_GRAPH_NODE_LIMIT]
            selected_node_ids = {node.id for node in selected_nodes}
            selected_edges = [
                edge
                for edge in dedup_edges.values()
                if edge.from_id in selected_node_ids and edge.to_id in selected_node_ids
            ]
            sampling = _legacy_graph_sampling(
                evaluated_nodes=evaluated_nodes,
                returned_nodes=len(selected_nodes),
                include_related=include_related,
                returned_related=len(realized_related_stories),
                related_limit=related_limit,
            )

            return StoryGraphResponse(
                story_id=story["id"],
                story_title=story["title"] or "",
                story_event_type=story["event_type"] or "",
                start_date=str(story["start_date"]) if story["start_date"] else None,
                end_date=str(story["end_date"]) if story["end_date"] else None,
                article_count=len(selected_nodes),
                cluster_count=len(selected_edges),
                meta={
                    **(story["meta"] or {}),
                    "expanded_graph": True,
                    "branch_story_count": len(realized_related_stories),
                },
                sampling=sampling,
                nodes=selected_nodes,
                edges=selected_edges,
                related_stories=realized_related_stories,
            )

        # 获取所有边
        edge_rows = (
            db.execute(
                text("""
                SELECT from_cluster_id, to_cluster_id, edge_type, weight
                FROM story_edges
                WHERE story_id = :sid
                ORDER BY weight DESC
            """),
                {"sid": story_id},
            )
            .mappings()
            .fetchall()
        )

        # 收集所有涉及到的 cluster_id
        cluster_ids: set = set()
        for e in edge_rows:
            cluster_ids.add(e["from_cluster_id"])
            cluster_ids.add(e["to_cluster_id"])

        # 获取 cluster 详情
        nodes: Dict[str, StoryNode] = {}
        if cluster_ids:
            cluster_rows = (
                db.execute(
                    text("""
                    SELECT ec.cluster_id, ec.title, ec.event_type, ec.initiator, ec.target,
                           ec.article_count, ec.start_date, ec.end_date,
                           tm.min_published_at, tm.max_published_at, tm.display_time
                    FROM event_coref_clusters ec
                    LEFT JOIN (
                        SELECT cluster_id,
                               MIN(published_at) AS min_published_at,
                               MAX(published_at) AS max_published_at,
                               to_timestamp(
                                   percentile_cont(0.5) WITHIN GROUP (
                                       ORDER BY extract(epoch FROM published_at)
                                   )
                               ) AS display_time
                        FROM event_coref_members
                        WHERE cluster_id = ANY(:cids) AND published_at IS NOT NULL
                        GROUP BY cluster_id
                    ) tm ON ec.cluster_id = tm.cluster_id
                    WHERE ec.cluster_id = ANY(:cids)
                """),
                    {"cids": list(cluster_ids)},
                )
                .mappings()
                .fetchall()
            )

            for c in cluster_rows:
                cid = c["cluster_id"]
                etype = c["event_type"] or "other"
                title = (c["title"] or "").strip()
                if title:
                    label = title[:30]
                else:
                    init_cn = chinese_entity(c["initiator"])
                    tgt_cn = chinese_entity(c["target"])
                    etype_cn = chinese_event_type(etype)
                    label = f"{etype_cn}·{init_cn}→{tgt_cn}"[:30]
                dr = ""
                if c["start_date"] or c["end_date"]:
                    dr = f"{c['start_date'] or '?'} ~ {c['end_date'] or '?'}"
                nodes[cid] = StoryNode(
                    id=cid,
                    label=label[:30],
                    event_type=etype,
                    article_count=c["article_count"],
                    date_range=dr[:20],
                    display_time=str(c["display_time"]) if c["display_time"] else None,
                    start_date=str(c["start_date"]) if c["start_date"] else None,
                    end_date=str(c["end_date"]) if c["end_date"] else None,
                    initiator=c["initiator"],
                    target=c["target"],
                    color=event_color(etype),
                    size=story_node_size(c["article_count"]),
                )

        # 如果边引用了不在 event_coref_clusters 中的 cluster_id，创建占位节点
        for e in edge_rows:
            for cid in (e["from_cluster_id"], e["to_cluster_id"]):
                if cid not in nodes:
                    nodes[cid] = StoryNode(
                        id=cid,
                        start_date=None,
                        display_time=None,
                        label=cid[:20],
                        event_type="other",
                        article_count=1,
                        date_range="",
                        color="#95A5A6",
                        size=10.0,
                    )

        edges_list: List[StoryEdge] = []
        for edge in edge_rows:
            relation_projection = project_story_relation(
                edge_type=edge["edge_type"],
                derivation="stored_derived_relation",
            )
            edges_list.append(
                StoryEdge(
                    from_id=edge["from_cluster_id"],
                    to_id=edge["to_cluster_id"],
                    edge_type=relation_projection.public_edge_type,
                    weight=float(edge["weight"]),
                    relation_reason=relation_projection.public_relation_reason,
                    claim=build_unavailable_story_relation_claim(
                        graph_scope_id=f"legacy:{story_id}",
                        from_id=edge["from_cluster_id"],
                        to_id=edge["to_cluster_id"],
                        relation_kind=relation_projection.public_edge_type,
                        derivation="stored_derived_relation",
                    ),
                    relation_semantics=relation_projection.semantics,
                )
            )

        evaluated_nodes = len(nodes)
        selected_nodes = list(nodes.values())[:_LEGACY_GRAPH_NODE_LIMIT]
        selected_node_ids = {node.id for node in selected_nodes}
        selected_edges = [
            edge
            for edge in edges_list
            if edge.from_id in selected_node_ids and edge.to_id in selected_node_ids
        ]
        sampling = _legacy_graph_sampling(
            evaluated_nodes=evaluated_nodes,
            returned_nodes=len(selected_nodes),
            include_related=include_related,
            returned_related=len(related_stories),
            related_limit=related_limit,
        )

        return StoryGraphResponse(
            story_id=story["id"],
            story_title=story["title"] or "",
            story_event_type=story["event_type"] or "",
            start_date=str(story["start_date"]) if story["start_date"] else None,
            end_date=str(story["end_date"]) if story["end_date"] else None,
            article_count=len(selected_nodes),
            cluster_count=len(selected_edges),
            meta=story["meta"] or {},
            sampling=sampling,
            nodes=selected_nodes,
            edges=selected_edges,
            related_stories=related_stories,
        )
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=500, detail="story graph is unavailable") from None


@router.get("/api/story-graph/cluster/{cluster_id}")
def get_cluster_detail(
    cluster_id: str,
    db: Session = Depends(get_l1_db),
) -> ClusterDetail:
    """获取 L1 簇详情（含新闻列表）。"""
    try:
        cluster = (
            db.execute(
                text("""
                SELECT cluster_id, title, event_type, initiator, target,
                       article_count, start_date, end_date
                FROM event_coref_clusters
                WHERE cluster_id = :cid
            """),
                {"cid": cluster_id},
            )
            .mappings()
            .first()
        )

        if not cluster:
            raise HTTPException(status_code=404, detail="簇不存在")

        news_rows = (
            db.execute(
                text("""
                SELECT n.id, n.title, n.published_at, n.url
                FROM event_coref_members ecm
                JOIN news n ON n.id = ecm.news_id
                WHERE ecm.cluster_id = :cid
                ORDER BY n.published_at DESC NULLS LAST
                LIMIT 50
            """),
                {"cid": cluster_id},
            )
            .mappings()
            .fetchall()
        )

        news_list = [
            ClusterNewsItem(
                news_id=r["id"],
                title=r["title"],
                published_at=str(r["published_at"]) if r["published_at"] else None,
                url=r.get("url"),
            )
            for r in news_rows
        ]

        return ClusterDetail(
            cluster_id=cluster["cluster_id"],
            title=cluster["title"],
            event_type=cluster["event_type"],
            initiator=cluster["initiator"],
            target=cluster["target"],
            article_count=cluster["article_count"],
            start_date=str(cluster["start_date"]) if cluster["start_date"] else None,
            end_date=str(cluster["end_date"]) if cluster["end_date"] else None,
            news=news_list,
        )
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=500, detail="story cluster is unavailable") from None
