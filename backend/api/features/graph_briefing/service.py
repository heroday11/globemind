"""Compatibility adapter from current hierarchy rows to graph briefing DTOs."""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Any, Iterable

from sqlalchemy.orm import Session

from api.features.graph_briefing.repository import GraphBriefingRepository
from api.features.story_graph import (
    build_graph_sampling_component,
    build_graph_sampling_provenance,
    project_public_graph_metric,
)

_GRAPH_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:@+\-]{0,255}\Z")


class GraphBriefingInputError(ValueError):
    """Raised when an opaque current hierarchy identifier is invalid."""


class GraphBriefingNotFound(LookupError):
    """Raised when a valid current hierarchy identifier has no row."""


class GraphBriefingService:
    """Preserve the legacy graph DTO surface over current L3/L2/L1 data."""

    def __init__(
        self,
        db: Session,
        repository: GraphBriefingRepository | None = None,
    ):
        self._repository = repository or GraphBriefingRepository(db)

    def search_macros(self, query: str, limit: int) -> dict[str, Any]:
        keyword = query.strip()
        rows = self._repository.search_macros(keyword, limit)[:limit]
        items = [_macro_dto(row) for row in rows]
        sampling = build_graph_sampling_provenance(
            build_graph_sampling_component(
                unit="macro_node",
                requested_count=limit,
                evaluated_count=None,
                returned_count=len(items),
                limit=limit,
                selection_rule="top_article_count_then_stable_id",
                reason_codes=[
                    "DISPLAY_LIMIT",
                    "GRAPH_COMPLETENESS_NOT_ESTABLISHED",
                ],
            )
        )
        return {
            "query": keyword,
            "total": len(items),
            "items": items,
            "sampling": sampling.model_dump(mode="json"),
        }

    def batch_news(
        self,
        event_ids: Iterable[Any],
        limit_per: int,
    ) -> dict[str, Any]:
        chain_ids = _normalize_ids(event_ids, maximum=800)
        if not chain_ids:
            sampling = build_graph_sampling_provenance(
                build_graph_sampling_component(
                    unit="news_item",
                    requested_count=0,
                    evaluated_count=0,
                    returned_count=0,
                    limit=0,
                    selection_rule="recent_news_per_parent",
                    reason_codes=[
                        "PER_PARENT_LIMIT",
                        "GRAPH_COMPLETENESS_NOT_ESTABLISHED",
                    ],
                )
            )
            return {
                "by_event": {},
                "limit_per": limit_per,
                "total_news": 0,
                "sampling": sampling.model_dump(mode="json"),
            }
        rows = self._repository.news_for_micros(chain_ids, limit_per)
        by_event: dict[str, list[dict[str, Any]]] = {chain_id: [] for chain_id in chain_ids}
        for row in rows:
            chain_id = str(row["chain_id"])
            if chain_id not in by_event or len(by_event[chain_id]) >= limit_per:
                continue
            by_event[chain_id].append(_news_dto(row, include_news_id=True))
        total_news = sum(len(items) for items in by_event.values())
        requested = len(chain_ids) * limit_per
        sampling = build_graph_sampling_provenance(
            build_graph_sampling_component(
                unit="news_item",
                requested_count=requested,
                evaluated_count=None,
                returned_count=total_news,
                limit=requested,
                selection_rule="recent_news_per_parent",
                reason_codes=[
                    "PER_PARENT_LIMIT",
                    "GRAPH_COMPLETENESS_NOT_ESTABLISHED",
                ],
            )
        )
        return {
            "by_event": by_event,
            "limit_per": limit_per,
            "total_news": total_news,
            "sampling": sampling.model_dump(mode="json"),
        }

    def universe(
        self,
        *,
        macro_limit: int,
        micro_per_macro: int,
        unclustered_limit: int,
        fill_ambient: bool,
        news_per_micro: int,
    ) -> dict[str, Any]:
        macro_rows = self._repository.list_macros(macro_limit)[:macro_limit]
        macro_ids = [str(row["macro_id"]) for row in macro_rows]
        raw_micro_rows = self._repository.micros_for_macros(
            macro_ids,
            micro_per_macro,
        )
        micros_by_macro: dict[str, list[dict[str, Any]]] = defaultdict(list)
        selected_micro_rows: list[dict[str, Any]] = []
        macro_id_set = set(macro_ids)
        for row in raw_micro_rows:
            macro_id = str(row["macro_id"])
            if (
                macro_id not in macro_id_set
                or len(micros_by_macro[macro_id]) >= micro_per_macro
            ):
                continue
            selected_micro_rows.append(row)
            micros_by_macro[macro_id].append(_micro_dto(row, macro_id=macro_id))

        micro_ids = [
            str(micro["event_id"])
            for micros in micros_by_macro.values()
            for micro in micros
        ]
        news_by_event: dict[str, list[dict[str, Any]]] = {}
        if news_per_micro > 0 and micro_ids:
            for offset in range(0, len(micro_ids), 800):
                batch = self.batch_news(
                    micro_ids[offset : offset + 800],
                    news_per_micro,
                )
                news_by_event.update(batch["by_event"])

        macros = []
        for row in macro_rows:
            macro = _macro_dto(row)
            macro_id = str(macro["macro_id"])
            micros = micros_by_macro.get(macro_id, [])
            for micro in micros:
                micro["id"] = micro["event_id"]
                micro["news"] = list(news_by_event.get(str(micro["event_id"]), []))
            macro.update(
                {
                    "_graphReal": True,
                    "macro_title": macro["title"],
                    "micro_events": micros,
                }
            )
            macros.append(macro)

        unclustered: list[dict[str, Any]] = []
        dust_meta: dict[str, Any] = {}
        if unclustered_limit > 0:
            raw_orphans = self._repository.list_unclustered_news(
                unclustered_limit
            )[:unclustered_limit]
            unclustered = [_dust_news_dto(row, ambient=False) for row in raw_orphans]
            dust_meta = {
                "orphan_count": len(unclustered),
                "ambient_count": 0,
                "fill_ambient": fill_ambient,
            }
            if fill_ambient and len(unclustered) < unclustered_limit:
                excluded = [int(item["news_id"]) for item in unclustered]
                ambient_rows = self._repository.list_ambient_news(
                    unclustered_limit - len(unclustered),
                    excluded,
                )[: unclustered_limit - len(unclustered)]
                ambient = [_dust_news_dto(row, ambient=True) for row in ambient_rows]
                unclustered.extend(ambient)
                dust_meta["ambient_count"] = len(ambient)

        diagnostics = self._repository.diagnostics()
        diagnostics["dust"] = dust_meta
        diagnostics["micro_events_with_news"] = sum(
            1
            for macro in macros
            for micro in macro["micro_events"]
            if micro["news"]
        )
        macro_total = _known_nonnegative_int(diagnostics.get("macro_total"))
        micro_total = _sum_known_counts(macro_rows, "l2_chain_count")
        news_total = _sum_known_counts(selected_micro_rows, "article_count")
        all_news = _known_nonnegative_int(diagnostics.get("news_total"))
        linked_news = _known_nonnegative_int(
            diagnostics.get("linked_news_distinct")
        )
        orphan_total = (
            all_news - linked_news
            if (
                not fill_ambient
                and all_news is not None
                and linked_news is not None
                and all_news >= linked_news
            )
            else None
        )
        sampling = build_graph_sampling_provenance(
            build_graph_sampling_component(
                unit="macro_node",
                requested_count=macro_limit,
                evaluated_count=macro_total,
                returned_count=len(macros),
                limit=macro_limit,
                selection_rule="top_article_count_then_stable_id",
                reason_codes=[
                    "DISPLAY_LIMIT",
                    "GRAPH_COMPLETENESS_NOT_ESTABLISHED",
                ],
            ),
            build_graph_sampling_component(
                unit="micro_node",
                requested_count=len(macros) * micro_per_macro,
                evaluated_count=micro_total,
                returned_count=sum(len(items) for items in micros_by_macro.values()),
                limit=len(macros) * micro_per_macro,
                selection_rule="per_parent_article_count_then_stable_id",
                reason_codes=[
                    "PER_PARENT_LIMIT",
                    "GRAPH_COMPLETENESS_NOT_ESTABLISHED",
                ],
            ),
            build_graph_sampling_component(
                unit="news_item",
                requested_count=len(micro_ids) * news_per_micro,
                evaluated_count=news_total,
                returned_count=sum(
                    len(items) for items in news_by_event.values()
                ),
                limit=len(micro_ids) * news_per_micro,
                selection_rule="recent_news_per_parent",
                reason_codes=[
                    "PER_PARENT_LIMIT",
                    "GRAPH_COMPLETENESS_NOT_ESTABLISHED",
                ],
            ),
            build_graph_sampling_component(
                unit="unclustered_news_item",
                requested_count=unclustered_limit,
                evaluated_count=orphan_total,
                returned_count=len(unclustered),
                limit=unclustered_limit,
                selection_rule="orphan_then_ambient_recent",
                reason_codes=[
                    "DISPLAY_LIMIT",
                    *(
                        ["AMBIENT_FILL_NOT_GRAPH_MEMBERSHIP"]
                        if fill_ambient
                        else []
                    ),
                    "GRAPH_COMPLETENESS_NOT_ESTABLISHED",
                ],
            ),
        )
        return {
            "macro_limit": macro_limit,
            "micro_per_macro": micro_per_macro,
            "news_per_micro": news_per_micro,
            "unclustered_limit": unclustered_limit,
            "fill_ambient": fill_ambient,
            "macros_count": len(macros),
            "unclustered_count": len(unclustered),
            "macros": macros,
            "unclustered_news": unclustered,
            "diagnostics": diagnostics,
            "sampling": sampling.model_dump(mode="json"),
        }

    def get_macro(self, macro_id: Any) -> dict[str, Any]:
        safe_id = normalize_graph_id(macro_id)
        row = self._repository.get_macro(safe_id)
        if row is None:
            raise GraphBriefingNotFound("macro event does not exist")
        return _macro_dto(row)

    def get_briefing(self, macro_id: Any) -> dict[str, Any]:
        safe_id = normalize_graph_id(macro_id)
        row = self._repository.get_macro(safe_id)
        if row is None:
            raise GraphBriefingNotFound("macro event does not exist")
        return {
            "storyline_id": safe_id,
            "macro": _macro_dto(row),
            "avg_sentiment_score": None,
            "sentiment_distribution": None,
            "topic_distribution": None,
            "opinion_model_note": (
                "Opinion aggregate unavailable: method, model/data identity, and "
                "evidence locator are not established."
            ),
            "metric_disclosures": [
                project_public_graph_metric(
                    "graph_briefing.opinion_aggregate",
                    raw_value={
                        "avg_sentiment_score": self._repository.briefing_average(safe_id),
                        "sentiment_distribution": (
                            self._repository.briefing_sentiment_distribution(safe_id)
                        ),
                        "topic_distribution": (
                            self._repository.briefing_topic_distribution(safe_id)
                        ),
                    },
                )
            ],
        }

    def list_micros(
        self,
        macro_id: Any,
        *,
        limit: int,
        offset: int,
    ) -> dict[str, Any]:
        safe_id = normalize_graph_id(macro_id)
        if self._repository.get_macro(safe_id) is None:
            raise GraphBriefingNotFound("macro event does not exist")
        total = self._repository.count_micros(safe_id)
        rows = self._repository.list_micros(safe_id, limit, offset)[:limit]
        sampling = build_graph_sampling_provenance(
            build_graph_sampling_component(
                unit="micro_node",
                requested_count=limit,
                evaluated_count=total,
                returned_count=len(rows),
                limit=limit,
                selection_rule="offset_page_stable_order",
                reason_codes=[
                    "PAGE_WINDOW_NOT_RETURNED",
                    "DISPLAY_LIMIT",
                    "GRAPH_COMPLETENESS_NOT_ESTABLISHED",
                ],
            )
        )
        return {
            "storyline_id": safe_id,
            "total": total,
            "items": [_micro_dto(row, macro_id=safe_id) for row in rows],
            "sampling": sampling.model_dump(mode="json"),
        }

    def get_tree(self, macro_id: Any, *, micro_limit: int) -> dict[str, Any]:
        safe_id = normalize_graph_id(macro_id)
        macro = self._repository.get_macro(safe_id)
        if macro is None:
            raise GraphBriefingNotFound("macro event does not exist")
        rows = self._repository.list_micros(safe_id, micro_limit)[:micro_limit]
        evaluated = _known_nonnegative_int(macro.get("l2_chain_count"))
        sampling = build_graph_sampling_provenance(
            build_graph_sampling_component(
                unit="micro_node",
                requested_count=micro_limit,
                evaluated_count=evaluated,
                returned_count=len(rows),
                limit=micro_limit,
                selection_rule="per_parent_article_count_then_stable_id",
                reason_codes=[
                    "PER_PARENT_LIMIT",
                    "GRAPH_COMPLETENESS_NOT_ESTABLISHED",
                ],
            )
        )
        return {
            "macro": _macro_dto(macro),
            "micros": [_micro_dto(row, macro_id=safe_id) for row in rows],
            "sampling": sampling.model_dump(mode="json"),
        }

    def get_micro(self, event_id: Any) -> dict[str, Any]:
        safe_id = normalize_graph_id(event_id)
        row = self._repository.get_micro(safe_id)
        if row is None:
            raise GraphBriefingNotFound("micro event does not exist")
        return _micro_dto(row, macro_id=row.get("macro_id"))

    def list_news(
        self,
        event_id: Any,
        *,
        page: int,
        page_size: int,
        brief: bool,
    ) -> dict[str, Any]:
        safe_id = normalize_graph_id(event_id)
        if self._repository.get_micro(safe_id) is None:
            raise GraphBriefingNotFound("micro event does not exist")
        total = self._repository.count_news(safe_id)
        rows = self._repository.list_news(
            safe_id,
            page_size,
            (page - 1) * page_size,
            brief=brief,
        )[:page_size]
        sampling = build_graph_sampling_provenance(
            build_graph_sampling_component(
                unit="news_item",
                requested_count=page_size,
                evaluated_count=total,
                returned_count=len(rows),
                limit=page_size,
                selection_rule="offset_page_stable_order",
                reason_codes=[
                    "PAGE_WINDOW_NOT_RETURNED",
                    "DISPLAY_LIMIT",
                    "GRAPH_COMPLETENESS_NOT_ESTABLISHED",
                ],
            )
        )
        return {
            "event_id": safe_id,
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": (total + page_size - 1) // page_size,
            "items": [_news_dto(row) for row in rows],
            "sampling": sampling.model_dump(mode="json"),
        }


def normalize_graph_id(value: Any) -> str:
    if isinstance(value, bool):
        raise GraphBriefingInputError("graph id has an invalid format")
    normalized = str(value).strip()
    if not normalized or _GRAPH_ID_RE.fullmatch(normalized) is None:
        raise GraphBriefingInputError("graph id has an invalid format")
    return normalized


def _normalize_ids(values: Iterable[Any], *, maximum: int) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = normalize_graph_id(value)
        if normalized in seen:
            continue
        seen.add(normalized)
        output.append(normalized)
        if len(output) > maximum:
            raise GraphBriefingInputError(f"at most {maximum} graph ids are allowed")
    return output


def _known_nonnegative_int(value: Any) -> int | None:
    return value if type(value) is int and 0 <= value <= 2_147_483_647 else None


def _sum_known_counts(rows: Iterable[dict[str, Any]], field: str) -> int | None:
    values = [_known_nonnegative_int(row.get(field)) for row in rows]
    if any(value is None for value in values):
        return None
    total = sum(value for value in values if value is not None)
    return total if total <= 2_147_483_647 else None


def _macro_dto(row: dict[str, Any]) -> dict[str, Any]:
    macro_id = str(row["macro_id"])
    return {
        "storyline_id": macro_id,
        "macro_id": macro_id,
        "title": row.get("title") or row.get("macro_key") or macro_id,
        "article_count": _known_nonnegative_int(row.get("article_count")),
        "micro_event_count": _known_nonnegative_int(row.get("l2_chain_count")),
        "l1_cluster_count": _known_nonnegative_int(row.get("l1_cluster_count")),
        "segment_count": _known_nonnegative_int(row.get("segment_count")),
        "start_date": row.get("start_date"),
        "end_date": row.get("end_date"),
        "status": "active",
        "description": row.get("summary") or "",
        "china_index_avg": None,
        "sentiment_main": None,
        "topic_main": row.get("family_group") or row.get("macro_key"),
        "family_group": row.get("family_group"),
        "macro_key": row.get("macro_key"),
        "quality_score": None,
        "actor_counts": row.get("actor_counts") or {},
        "topic_counts": row.get("topic_counts") or {},
        "metric_disclosures": [
            project_public_graph_metric(
                "graph_briefing.quality_score",
                raw_value=row.get("quality_score"),
            )
        ],
    }


def _micro_dto(
    row: dict[str, Any],
    *,
    macro_id: Any = None,
) -> dict[str, Any]:
    chain_id = str(row["chain_id"])
    parent_id = str(macro_id) if macro_id is not None else None
    return {
        "event_id": chain_id,
        "chain_id": chain_id,
        "title": row.get("title") or chain_id,
        "start_date": row.get("start_date"),
        "end_date": row.get("end_date"),
        "article_count": _known_nonnegative_int(row.get("article_count")),
        "segment_count": _known_nonnegative_int(row.get("segment_count")),
        "macro_storyline_id": parent_id,
        "macro_id": parent_id,
        "china_index_avg": None,
        "sentiment_main": None,
        "topic_main": row.get("event_family") or row.get("family_group"),
        "membership_score": None,
        "family_group": row.get("family_group"),
        "event_family": row.get("event_family"),
        "event_action": row.get("event_action"),
        "pair_key": row.get("pair_key"),
        "initiator": row.get("initiator"),
        "target": row.get("target"),
        "chain_quality": None,
        "quality_score": None,
        "role": row.get("role"),
        "lane": row.get("lane"),
        "metric_disclosures": [
            project_public_graph_metric(
                "graph_briefing.quality_score",
                raw_value={
                    "quality_score": row.get("quality_score"),
                    "chain_quality": row.get("chain_quality"),
                },
            ),
            project_public_graph_metric(
                "graph_briefing.membership_score",
                raw_value=row.get("importance_score"),
            ),
        ],
    }


def _news_dto(
    row: dict[str, Any],
    *,
    include_news_id: bool = False,
) -> dict[str, Any]:
    news_id = int(row.get("news_id") if include_news_id else row["id"])
    output = {
        "id": news_id,
        "title": row.get("title") or "",
        "abstract": row.get("abstract") or "",
        "pub_time": row.get("pub_time"),
        "request_url": row.get("request_url"),
        "language_id": row.get("language_id"),
    }
    if include_news_id:
        output["news_id"] = news_id
    return output


def _dust_news_dto(row: dict[str, Any], *, ambient: bool) -> dict[str, Any]:
    news_id = int(row["news_id"])
    return {
        "news_id": news_id,
        "id": news_id,
        "title": row.get("title") or "",
        "pub_time": row.get("pub_time"),
        "ambient_fill": ambient,
    }
