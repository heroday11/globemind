from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import pytest

from api.features import graph_briefing, story_graph
from api.routes import story_graph as story_graph_route


MACRO_ID = "fast_l3_v1_macro-sampling"
CHAIN_ID = "fast_l2_v1_chain-sampling"


def _macro_row() -> dict[str, Any]:
    return {
        "macro_id": MACRO_ID,
        "macro_key": "sampling:macro",
        "title": "Sampling macro",
        "article_count": 6,
        "l2_chain_count": 2,
        "l1_cluster_count": 3,
        "segment_count": 4,
    }


def _micro_row(suffix: str = "one") -> dict[str, Any]:
    return {
        "macro_id": MACRO_ID,
        "chain_id": f"{CHAIN_ID}-{suffix}",
        "title": f"Sampling chain {suffix}",
        "article_count": 3,
        "segment_count": 2,
    }


class _BoundedRepository:
    def list_macros(self, _limit: int) -> list[dict[str, Any]]:
        return [_macro_row(), {**_macro_row(), "macro_id": f"{MACRO_ID}-extra"}]

    def micros_for_macros(
        self,
        _macro_ids: list[str],
        _per_macro: int,
    ) -> list[dict[str, Any]]:
        return [_micro_row("one"), _micro_row("two")]

    def news_for_micros(
        self,
        chain_ids: list[str],
        _limit_per: int,
    ) -> list[dict[str, Any]]:
        return [
            {
                "chain_id": chain_ids[0],
                "news_id": 11,
                "title": "First candidate",
                "abstract": "bounded summary",
                "body": "SECRET FULL BODY MUST NOT LEAVE THE SERVICE",
            },
            {
                "chain_id": chain_ids[0],
                "news_id": 12,
                "title": "Overflow candidate",
                "abstract": "bounded summary",
            },
        ]

    def list_unclustered_news(self, _limit: int) -> list[dict[str, Any]]:
        return [
            {"news_id": 21, "title": "Orphan one"},
            {"news_id": 22, "title": "Orphan overflow"},
        ]

    def list_ambient_news(
        self,
        _limit: int,
        _excluded_ids: list[int],
    ) -> list[dict[str, Any]]:
        return []

    def diagnostics(self) -> dict[str, int]:
        return {"news_total": 10, "macro_total": 3, "linked_news_distinct": 8}

    def get_macro(self, _macro_id: str) -> dict[str, Any]:
        return _macro_row()

    def count_micros(self, _macro_id: str) -> int:
        return 2

    def list_micros(
        self,
        _macro_id: str,
        _limit: int,
        _offset: int = 0,
    ) -> list[dict[str, Any]]:
        return [_micro_row("one"), _micro_row("two")]


class _UnknownCountRepository:
    def search_macros(self, _query: str, _limit: int) -> list[dict[str, Any]]:
        return [
            {
                "macro_id": MACRO_ID,
                "article_count": None,
                "l2_chain_count": None,
                "l1_cluster_count": False,
                "segment_count": "3",
            }
        ]

    def get_macro(self, _macro_id: str) -> dict[str, Any]:
        return self.search_macros("", 1)[0]

    def list_micros(
        self,
        _macro_id: str,
        _limit: int,
        _offset: int = 0,
    ) -> list[dict[str, Any]]:
        return [{"macro_id": MACRO_ID, "chain_id": CHAIN_ID}]


def _component(payload: dict[str, Any], unit: str) -> dict[str, Any]:
    sampling = payload["sampling"]
    assert sampling["schema_version"] == "graph-sampling-provenance-v1"
    assert sampling["complete_graph_claim"] is False
    return next(item for item in sampling["components"] if item["unit"] == unit)


def test_graph_sampling_contract_rejects_inconsistent_or_self_upgraded_counts() -> None:
    component = story_graph.GraphSamplingComponent(
        unit="l2_chain_node",
        state="bounded_partial",
        requested_count=2,
        evaluated_count=4,
        returned_count=2,
        excluded_count=2,
        limit=2,
        overflow=True,
        selection_rule="lane_quota_then_importance",
        reason_codes=["DISPLAY_LIMIT", "GRAPH_COMPLETENESS_NOT_ESTABLISHED"],
        excluded_node_ids_disclosed=False,
    )
    envelope = story_graph.GraphSamplingProvenance(
        coverage_state="partial",
        components=[component],
        complete_graph_claim=False,
    )
    assert envelope.complete_graph_claim is False

    with pytest.raises(Exception):
        story_graph.GraphSamplingComponent(
            **{
                **component.model_dump(),
                "returned_count": 3,
            }
        )
    with pytest.raises(Exception):
        story_graph.GraphSamplingProvenance(
            coverage_state="partial",
            components=[component],
            complete_graph_claim=True,
        )
    with pytest.raises(Exception):
        story_graph.GraphSamplingProvenance(
            coverage_state="partial",
            components=[component],
            complete_graph_claim=0,
        )
    with pytest.raises(Exception):
        story_graph.GraphSamplingComponent(
            **{
                **component.model_dump(),
                "returned_count": 2.0,
            }
        )
    with pytest.raises(Exception):
        story_graph.GraphSamplingComponent(
            **{
                **component.model_dump(),
                "reason_codes": ["DISPLAY_LIMIT"],
            }
        )
    with pytest.raises(Exception):
        story_graph.GraphSamplingComponent(
            **{
                **component.model_dump(),
                "excluded_node_ids_disclosed": 0,
            }
        )
    with pytest.raises(Exception):
        story_graph.GraphSamplingComponent(
            **{
                **component.model_dump(),
                "excluded_node_ids": ["secret-node-id"],
            }
        )

    unknown = story_graph.build_graph_sampling_component(
        unit="related_story",
        returned_count=0,
        evaluated_count=None,
        selection_rule="related_story_rank",
        reason_codes=["GRAPH_COMPLETENESS_NOT_ESTABLISHED"],
    )
    mixed = story_graph.build_graph_sampling_provenance(component, unknown)
    assert mixed.coverage_state == "unknown"


def test_universe_caps_every_display_layer_and_reports_coverage_loss() -> None:
    service = graph_briefing.GraphBriefingService(  # type: ignore[arg-type]
        None,
        repository=_BoundedRepository(),
    )

    payload = service.universe(
        macro_limit=1,
        micro_per_macro=1,
        unclustered_limit=1,
        fill_ambient=False,
        news_per_micro=1,
    )

    assert payload["macros_count"] == 1
    assert len(payload["macros"][0]["micro_events"]) == 1
    assert len(payload["macros"][0]["micro_events"][0]["news"]) == 1
    assert payload["unclustered_count"] == 1
    serialized = json.dumps(payload, ensure_ascii=False)
    assert "SECRET FULL BODY" not in serialized
    assert "secret-node-id" not in serialized

    assert _component(payload, "macro_node") == {
        "unit": "macro_node",
        "state": "bounded_partial",
        "requested_count": 1,
        "evaluated_count": 3,
        "returned_count": 1,
        "excluded_count": 2,
        "limit": 1,
        "overflow": True,
        "selection_rule": "top_article_count_then_stable_id",
        "reason_codes": ["DISPLAY_LIMIT", "GRAPH_COMPLETENESS_NOT_ESTABLISHED"],
        "excluded_node_ids_disclosed": False,
    }
    assert _component(payload, "micro_node")["excluded_count"] == 1
    assert _component(payload, "news_item")["excluded_count"] == 2
    assert _component(payload, "unclustered_news_item")["excluded_count"] == 1


def test_tree_and_paginated_micros_expose_bounded_sampling_not_full_graph() -> None:
    service = graph_briefing.GraphBriefingService(  # type: ignore[arg-type]
        None,
        repository=_BoundedRepository(),
    )

    tree = service.get_tree(MACRO_ID, micro_limit=1)
    listing = service.list_micros(MACRO_ID, limit=1, offset=0)

    assert len(tree["micros"]) == 1
    assert _component(tree, "micro_node")["excluded_count"] == 1
    assert _component(tree, "micro_node")["selection_rule"] == (
        "per_parent_article_count_then_stable_id"
    )
    assert len(listing["items"]) == 1
    assert _component(listing, "micro_node")["evaluated_count"] == 2
    assert _component(listing, "micro_node")["returned_count"] == 1
    assert _component(listing, "micro_node")["overflow"] is True
    assert "PAGE_WINDOW_NOT_RETURNED" in _component(
        listing,
        "micro_node",
    )["reason_codes"]


def test_graph_briefing_missing_counts_remain_explicit_unknown_not_zero() -> None:
    service = graph_briefing.GraphBriefingService(  # type: ignore[arg-type]
        None,
        repository=_UnknownCountRepository(),
    )

    macro = service.search_macros("sample", 1)["items"][0]
    tree = service.get_tree(MACRO_ID, micro_limit=1)

    assert macro["article_count"] is None
    assert macro["micro_event_count"] is None
    assert macro["l1_cluster_count"] is None
    assert macro["segment_count"] is None
    assert tree["macro"]["article_count"] is None
    assert tree["micros"][0]["article_count"] is None
    assert tree["micros"][0]["segment_count"] is None
    assert _component(tree, "micro_node")["state"] == "unknown"


@dataclass
class _Rows:
    first_value: dict[str, Any] | None = None
    rows: list[dict[str, Any]] | None = None

    def mappings(self) -> _Rows:
        return self

    def first(self) -> dict[str, Any] | None:
        return self.first_value

    def fetchall(self) -> list[dict[str, Any]]:
        return self.rows or []


class _SequenceSession:
    def __init__(self, *results: _Rows) -> None:
        self._results = list(results)

    def execute(self, *_args: Any, **_kwargs: Any) -> _Rows:
        if not self._results:
            raise AssertionError("unexpected query")
        return self._results.pop(0)


def test_primary_l2_and_l3_graph_paths_emit_sampling_provenance() -> None:
    l2 = story_graph_route.get_l2_chain_graph(
        chain_id=CHAIN_ID,
        run_id="run-l2",
        db=_SequenceSession(
            _Rows(first_value={"chain_id": CHAIN_ID, "segment_count": 201}),
            _Rows(
                rows=[
                    {
                        "segment_id": f"segment-{index}",
                        "segment_order": index,
                    }
                    for index in range(201)
                ]
            ),
        ),
    )
    l2_component = _component(l2, "l15_segment_node")
    assert len(l2["nodes"]) == 200
    assert l2_component["evaluated_count"] == 201
    assert l2_component["returned_count"] == 200
    assert l2_component["excluded_count"] == 1
    assert l2_component["selection_rule"] == "ordered_chain_segments"

    l3 = story_graph_route.get_l3_macro_graph(
        macro_id=MACRO_ID,
        run_id="run-l3",
        max_nodes=8,
        db=_SequenceSession(
            _Rows(first_value={"macro_id": MACRO_ID, "l2_chain_count": 10}),
            _Rows(
                rows=[
                    {
                        "l2_chain_id": f"chain-{index}",
                        "node_order": index,
                    }
                    for index in range(1, 10)
                ]
            ),
            _Rows(rows=[]),
        ),
    )
    l3_component = _component(l3, "l2_chain_node")
    assert l3_component["requested_count"] == 8
    assert l3_component["evaluated_count"] == 10
    assert len(l3["nodes"]) == 8
    assert l3_component["returned_count"] == 8
    assert l3_component["excluded_count"] == 2
    assert l3_component["selection_rule"] == "lane_quota_then_importance"
    assert l3_component["overflow"] is True


def test_ground_news_timeline_is_defensively_bounded_and_discloses_loss() -> None:
    timeline = story_graph_route.get_ground_news_timeline(
        chain_id=CHAIN_ID,
        l1_run_id="run-l1",
        l2_run_id="run-l2",
        db=_SequenceSession(
            _Rows(first_value={"chain_id": CHAIN_ID, "segment_count": 201}),
            _Rows(
                rows=[
                    {"segment_id": f"segment-{index}", "segment_order": index}
                    for index in range(201)
                ]
            ),
        ),
    )

    assert len(timeline["nodes"]) == 200
    assert len(timeline["edges"]) == 199
    component = _component(timeline, "l15_segment_node")
    assert component["evaluated_count"] == 201
    assert component["returned_count"] == 200
    assert component["excluded_count"] == 1
    assert component["overflow"] is True
