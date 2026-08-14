"""V11 HTTP compatibility over the current L3 -> L2 -> L1 hierarchy."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping

from sqlalchemy import text
from sqlalchemy.orm import Session

from api.models.schemas import (
    V11ClusterItem,
    V11ClusterSearchRequest,
    V11ClusterSearchResponse,
)

_MAX_ITEM_ID_LENGTH = 256
_ITEM_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:@+\-]{0,255}\Z")
_LEVEL_ALIASES = {
    "macro": "macro",
    "l3": "macro",
    "micro": "micro",
    "l2": "micro",
    "cluster": "cluster",
    "l1": "cluster",
}
_CURRENT_LEVEL_LABELS = {
    "macro": "l3",
    "micro": "l2",
    "cluster": "l1",
    "news": "news",
}
_LEGACY_LEVEL_LABELS = {
    "macro": "macro",
    "micro": "micro",
    "cluster": "cluster",
    "news": "news",
}


class V11SearchContractError(ValueError):
    """Raised when an adapter input cannot be mapped safely."""


@dataclass(frozen=True)
class _SearchSpec:
    from_sql: str
    select_sql: str
    direct_predicate: str
    article_predicate: str


_SEARCH_SPECS = {
    "macro": _SearchSpec(
        from_sql="FROM public.event_l3_macro_events parent",
        select_sql="""
            SELECT
                parent.macro_id AS id,
                COALESCE(NULLIF(parent.title, ''), parent.macro_key, parent.macro_id) AS title,
                COALESCE(parent.article_count, 0) AS article_count,
                COALESCE(parent.l2_chain_count, 0) AS children_count,
                NULL::text AS initiator,
                NULL::text AS target,
                parent.start_date,
                parent.end_date,
                parent.family_group AS event_type,
                NULL::text AS dominant_trigger
            FROM public.event_l3_macro_events parent
        """,
        direct_predicate="""
            COALESCE(parent.title, '') ILIKE :keyword ESCAPE '!'
            OR COALESCE(parent.summary, '') ILIKE :keyword ESCAPE '!'
            OR COALESCE(parent.macro_key, '') ILIKE :keyword ESCAPE '!'
        """,
        article_predicate="""
            EXISTS (
                SELECT 1
                FROM public.event_l3_macro_members l3_member
                JOIN public.event_l2_chain_segments l2_segment
                  ON l2_segment.chain_id = l3_member.l2_chain_id
                JOIN public.event_coref_members l1_member
                  ON l1_member.cluster_id = l2_segment.l1_cluster_id
                JOIN public.news article ON article.id = l1_member.news_id
                WHERE l3_member.macro_id = parent.macro_id
                  AND (
                      COALESCE(article.title, '') ILIKE :keyword ESCAPE '!'
                      OR LEFT(COALESCE(article.body, ''), 4000) ILIKE :keyword ESCAPE '!'
                  )
            )
        """,
    ),
    "micro": _SearchSpec(
        from_sql="FROM public.event_l2_chains parent",
        select_sql="""
            SELECT
                parent.chain_id AS id,
                COALESCE(NULLIF(parent.title, ''), parent.pair_key, parent.chain_id) AS title,
                COALESCE(parent.article_count, 0) AS article_count,
                COALESCE(parent.segment_count, 0) AS children_count,
                parent.initiator,
                parent.target,
                parent.start_date,
                parent.end_date,
                COALESCE(parent.event_family, parent.event_action) AS event_type,
                NULL::text AS dominant_trigger
            FROM public.event_l2_chains parent
        """,
        direct_predicate="""
            COALESCE(parent.title, '') ILIKE :keyword ESCAPE '!'
            OR COALESCE(parent.pair_key, '') ILIKE :keyword ESCAPE '!'
            OR COALESCE(parent.initiator, '') ILIKE :keyword ESCAPE '!'
            OR COALESCE(parent.target, '') ILIKE :keyword ESCAPE '!'
            OR COALESCE(parent.event_family, '') ILIKE :keyword ESCAPE '!'
            OR COALESCE(parent.event_action, '') ILIKE :keyword ESCAPE '!'
        """,
        article_predicate="""
            EXISTS (
                SELECT 1
                FROM public.event_l2_chain_segments l2_segment
                JOIN public.event_coref_members l1_member
                  ON l1_member.cluster_id = l2_segment.l1_cluster_id
                JOIN public.news article ON article.id = l1_member.news_id
                WHERE l2_segment.chain_id = parent.chain_id
                  AND (
                      COALESCE(article.title, '') ILIKE :keyword ESCAPE '!'
                      OR LEFT(COALESCE(article.body, ''), 4000) ILIKE :keyword ESCAPE '!'
                  )
            )
        """,
    ),
    "cluster": _SearchSpec(
        from_sql="FROM public.event_coref_clusters parent",
        select_sql="""
            SELECT
                parent.cluster_id AS id,
                COALESCE(
                    NULLIF(parent.title, ''),
                    NULLIF(parent.dominant_trigger, ''),
                    parent.event_type,
                    parent.cluster_id
                ) AS title,
                COALESCE(parent.article_count, 0) AS article_count,
                COALESCE(parent.article_count, 0) AS children_count,
                parent.initiator,
                parent.target,
                parent.start_date,
                parent.end_date,
                COALESCE(parent.event_type, parent.event_family) AS event_type,
                parent.dominant_trigger
            FROM public.event_coref_clusters parent
        """,
        direct_predicate="""
            COALESCE(parent.title, '') ILIKE :keyword ESCAPE '!'
            OR COALESCE(parent.dominant_trigger, '') ILIKE :keyword ESCAPE '!'
            OR COALESCE(parent.event_type, '') ILIKE :keyword ESCAPE '!'
            OR COALESCE(parent.event_family, '') ILIKE :keyword ESCAPE '!'
            OR COALESCE(parent.initiator, '') ILIKE :keyword ESCAPE '!'
            OR COALESCE(parent.target, '') ILIKE :keyword ESCAPE '!'
        """,
        article_predicate="""
            EXISTS (
                SELECT 1
                FROM public.event_coref_members l1_member
                JOIN public.news article ON article.id = l1_member.news_id
                WHERE l1_member.cluster_id = parent.cluster_id
                  AND (
                      COALESCE(article.title, '') ILIKE :keyword ESCAPE '!'
                      OR LEFT(COALESCE(article.body, ''), 4000) ILIKE :keyword ESCAPE '!'
                  )
            )
        """,
    ),
}

_L3_CHILD_COUNT_SQL = """
    SELECT COUNT(*)
    FROM public.event_l3_macro_members
    WHERE macro_id = :item_id
"""
_L3_CHILDREN_SQL = """
    SELECT
        member.l2_chain_id AS id,
        COALESCE(NULLIF(chain.title, ''), member.title, member.l2_chain_id) AS title,
        COALESCE(chain.article_count, member.article_count, 0) AS article_count,
        COALESCE(chain.segment_count, member.segment_count, 0) AS children_count,
        chain.initiator,
        chain.target,
        COALESCE(chain.start_date, member.start_date) AS start_date,
        COALESCE(chain.end_date, member.end_date) AS end_date,
        COALESCE(chain.event_family, chain.event_action, member.role, member.lane) AS event_type,
        NULL::text AS dominant_trigger
    FROM public.event_l3_macro_members member
    LEFT JOIN public.event_l2_chains chain ON chain.chain_id = member.l2_chain_id
    WHERE member.macro_id = :item_id
    ORDER BY member.node_order ASC NULLS LAST, member.importance_score DESC NULLS LAST
    LIMIT :limit OFFSET :offset
"""

_L2_CHILD_COUNT_SQL = """
    SELECT COUNT(DISTINCT l1_cluster_id)
    FROM public.event_l2_chain_segments
    WHERE chain_id = :item_id
"""
_L2_CHILDREN_SQL = """
    SELECT
        segment.l1_cluster_id AS id,
        COALESCE(
            NULLIF(MAX(cluster.title), ''),
            NULLIF(MAX(segment.story_angle), ''),
            segment.l1_cluster_id
        ) AS title,
        COALESCE(MAX(cluster.article_count), MAX(segment.article_count), 0) AS article_count,
        COALESCE(MAX(cluster.article_count), MAX(segment.article_count), 0) AS children_count,
        MAX(cluster.initiator) AS initiator,
        MAX(cluster.target) AS target,
        COALESCE(MIN(cluster.start_date), MIN(segment.start_date)) AS start_date,
        COALESCE(MAX(cluster.end_date), MAX(segment.end_date)) AS end_date,
        COALESCE(MAX(cluster.event_type), MAX(segment.event_family)) AS event_type,
        MAX(cluster.dominant_trigger) AS dominant_trigger,
        MIN(segment.segment_order) AS first_segment_order
    FROM public.event_l2_chain_segments segment
    LEFT JOIN public.event_coref_clusters cluster
      ON cluster.cluster_id = segment.l1_cluster_id
    WHERE segment.chain_id = :item_id
    GROUP BY segment.l1_cluster_id
    ORDER BY first_segment_order ASC NULLS LAST, start_date ASC NULLS LAST
    LIMIT :limit OFFSET :offset
"""

_L1_CHILD_COUNT_SQL = """
    SELECT COUNT(*)
    FROM public.event_coref_members member
    JOIN public.news article ON article.id = member.news_id
    WHERE member.cluster_id = :item_id
"""
_L1_CHILDREN_SQL = """
    SELECT
        article.id,
        COALESCE(NULLIF(article.title, ''), 'Untitled') AS title,
        LEFT(COALESCE(article.body, ''), 1200) AS abstract,
        article.published_at AS pub_time,
        article.url AS request_url,
        article.language AS language_id
    FROM public.event_coref_members member
    JOIN public.news article ON article.id = member.news_id
    WHERE member.cluster_id = :item_id
    ORDER BY article.published_at DESC NULLS LAST, article.id DESC
    LIMIT :limit OFFSET :offset
"""


def _like_pattern(keyword: str) -> str:
    escaped = keyword.replace("!", "!!").replace("%", "!%").replace("_", "!_")
    return f"%{escaped}%"


def _date_text(value: Any) -> str | None:
    return str(value) if value is not None else None


def _validate_page(page: int, page_size: int, *, maximum: int) -> None:
    if page < 1:
        raise V11SearchContractError("page must be at least 1")
    if page_size < 1 or page_size > maximum:
        raise V11SearchContractError(f"page_size must be between 1 and {maximum}")


def _normalize_item_id(item_id: Any) -> str:
    value = str(item_id).strip()
    if (
        not value
        or len(value) > _MAX_ITEM_ID_LENGTH
        or _ITEM_ID_RE.fullmatch(value) is None
    ):
        raise V11SearchContractError("item_id has an invalid format")
    return value


def _normalize_parent_level(level: str) -> tuple[str, bool, str]:
    raw_level = str(level or "").strip().lower()
    canonical_level = _LEVEL_ALIASES.get(raw_level)
    if canonical_level is None:
        raise V11SearchContractError(
            "level must be one of l3, l2, l1, macro, micro, or cluster"
        )
    return canonical_level, raw_level in _LEGACY_LEVEL_LABELS.values(), raw_level


def _row_to_cluster_item(row: Mapping[str, Any], level: str) -> V11ClusterItem:
    return V11ClusterItem(
        id=str(row["id"]),
        title=str(row.get("title") or row["id"]),
        level=level,
        article_count=int(row.get("article_count") or 0),
        children_count=int(row.get("children_count") or 0),
        initiator=row.get("initiator"),
        target=row.get("target"),
        start_date=_date_text(row.get("start_date")),
        end_date=_date_text(row.get("end_date")),
        event_type=row.get("event_type"),
        dominant_trigger=row.get("dominant_trigger"),
    )


def _fetch_mapping_rows(
    db: Session,
    statement: str,
    parameters: Mapping[str, Any],
) -> list[Mapping[str, Any]]:
    return list(db.execute(text(statement), dict(parameters)).mappings().all())


def search_v11_clusters(
    db: Session,
    request: V11ClusterSearchRequest,
) -> V11ClusterSearchResponse:
    """Search the current hierarchy while preserving the V11 response contract."""
    _validate_page(request.page, request.page_size, maximum=100)
    keyword = request.keyword.strip()
    if not keyword:
        return V11ClusterSearchResponse(page=request.page, page_size=request.page_size)

    spec = _SEARCH_SPECS.get(request.level)
    if spec is None:
        raise V11SearchContractError("level must be one of macro, micro, or cluster")
    bind = {"keyword": _like_pattern(keyword)}
    count_statement = f"SELECT COUNT(*) {spec.from_sql} WHERE ({spec.direct_predicate})"
    total = int(db.execute(text(count_statement), bind).scalar() or 0)
    predicate = spec.direct_predicate
    if total == 0:
        predicate = spec.article_predicate
        count_statement = f"SELECT COUNT(*) {spec.from_sql} WHERE ({predicate})"
        total = int(db.execute(text(count_statement), bind).scalar() or 0)

    rows: list[Mapping[str, Any]] = []
    if total:
        offset = (request.page - 1) * request.page_size
        rows = _fetch_mapping_rows(
            db,
            f"""
                {spec.select_sql}
                WHERE ({predicate})
                ORDER BY article_count DESC, start_date DESC NULLS LAST, id ASC
                LIMIT :limit OFFSET :offset
            """,
            {**bind, "limit": request.page_size, "offset": offset},
        )
    items = [_row_to_cluster_item(row, request.level) for row in rows]
    total_pages = (total + request.page_size - 1) // request.page_size
    return V11ClusterSearchResponse(
        items=items,
        total=total,
        page=request.page,
        page_size=request.page_size,
        total_pages=total_pages,
        has_next=request.page < total_pages,
        has_prev=request.page > 1,
    )


def _child_level_label(canonical_parent: str, legacy_labels: bool) -> str:
    child = {"macro": "micro", "micro": "cluster", "cluster": "news"}[
        canonical_parent
    ]
    labels = _LEGACY_LEVEL_LABELS if legacy_labels else _CURRENT_LEVEL_LABELS
    return labels[child]


def expand_v11_cluster_children(
    db: Session,
    item_id: Any,
    parent_level: str,
    page: int = 1,
    page_size: int = 20,
) -> dict[str, Any]:
    """Expand exactly one hierarchy edge using current pipeline relations."""
    _validate_page(page, page_size, maximum=50)
    safe_item_id = _normalize_item_id(item_id)
    canonical_level, legacy_labels, response_parent_level = _normalize_parent_level(
        parent_level
    )
    child_level = _child_level_label(canonical_level, legacy_labels)
    bind = {
        "item_id": safe_item_id,
        "limit": page_size,
        "offset": (page - 1) * page_size,
    }
    count_bind = {"item_id": safe_item_id}

    if canonical_level == "macro":
        total = int(db.execute(text(_L3_CHILD_COUNT_SQL), count_bind).scalar() or 0)
        rows = _fetch_mapping_rows(db, _L3_CHILDREN_SQL, bind) if total else []
        items: list[Any] = [
            _row_to_cluster_item(row, child_level).model_dump() for row in rows
        ]
    elif canonical_level == "micro":
        total = int(db.execute(text(_L2_CHILD_COUNT_SQL), count_bind).scalar() or 0)
        rows = _fetch_mapping_rows(db, _L2_CHILDREN_SQL, bind) if total else []
        items = [_row_to_cluster_item(row, child_level).model_dump() for row in rows]
    else:
        total = int(db.execute(text(_L1_CHILD_COUNT_SQL), count_bind).scalar() or 0)
        rows = _fetch_mapping_rows(db, _L1_CHILDREN_SQL, bind) if total else []
        items = [
            {
                "id": int(row["id"]),
                "title": str(row.get("title") or "Untitled"),
                "level": "news",
                "abstract": str(row.get("abstract") or ""),
                "pub_time": _date_text(row.get("pub_time")),
                "time_semantics": {
                    "schema_version": "search-result-time-semantics-v1",
                    "published_at": _date_text(row.get("pub_time")),
                    "event_time_start": None,
                    "event_time_end": None,
                    "collected_at": None,
                    "updated_at": None,
                    "legacy_pub_time_status": "legacy_alias_of_published_at_value_unverified",
                    "legacy_created_at_status": "legacy_unverified_not_used",
                },
                "request_url": row.get("request_url"),
                "language_id": row.get("language_id"),
                "is_favorited": False,
                "is_warned": False,
            }
            for row in rows
        ]

    total_pages = (total + page_size - 1) // page_size
    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": total_pages,
        "has_next": page < total_pages,
        "has_prev": page > 1,
        "parent_level": response_parent_level,
        "child_level": child_level,
    }
