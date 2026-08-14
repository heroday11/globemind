from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import pytest

from api.features import story_graph
from api.routes import story_graph as story_graph_route
from scripts import run_news_l3_macro_events as l3_builder


PROJECT_ROOT = Path(__file__).resolve().parents[2]


PUBLIC_EDGE_SURFACE_INVENTORY = (
    ("current-l2", "backend/api/routes/story_graph.py", "def get_l2_chain_graph("),
    ("current-l3-stored", "backend/api/routes/story_graph.py", "event_l3_macro_edges"),
    ("current-l3-layout", "backend/api/routes/story_graph.py", 'edge_type="macro_sequence"'),
    ("ground-news-timeline", "backend/api/routes/story_graph.py", "def get_ground_news_timeline("),
    ("legacy-story-bundle", "backend/api/routes/story_graph.py", "def _fetch_story_bundle("),
    ("legacy-expanded-bridge", "backend/api/routes/story_graph.py", "def _bridge_edge_type("),
    ("legacy-story-route", "backend/api/routes/story_graph.py", "def get_story_graph("),
    ("l3-offline-builder", "scripts/run_news_l3_macro_events.py", "def classify_edge("),
    ("l2-offline-builder", "scripts/run_news_l2_storylines.py", "def edge_type("),
    ("legacy-offline-builder", "core_pipeline/event_evolution_chain.py", "def _classify_edge("),
    ("legacy-causal-relabeler", "scripts/llm_causal_edges.py", '"causal_type"'),
)


class _QueryResult:
    def __init__(
        self,
        *,
        first: dict[str, Any] | None = None,
        rows: list[dict[str, Any]] | None = None,
    ) -> None:
        self.first_value = first
        self.rows = rows or []

    def mappings(self) -> _QueryResult:
        return self

    def first(self) -> dict[str, Any] | None:
        return self.first_value

    def fetchall(self) -> list[dict[str, Any]]:
        return self.rows


class _TimelineOverlapSession:
    def __init__(self) -> None:
        self.calls = 0

    def execute(self, *_args: Any, **_kwargs: Any) -> _QueryResult:
        self.calls += 1
        if self.calls == 1:
            return _QueryResult(
                first={
                    "run_id": "run-1",
                    "chain_id": "chain-1",
                    "segment_count": 2,
                    "article_count": None,
                }
            )
        return _QueryResult(
            rows=[
                {
                    "segment_id": "segment-1",
                    "segment_order": 1,
                    "edge_type": "chain_start",
                },
                {
                    "segment_id": "segment-2",
                    "segment_order": 2,
                    "edge_type": "influence",
                    "relation_reason": "时间重叠",
                    "gap_days": 0,
                },
            ]
        )


class _L3LegacyEdgeSession:
    def __init__(self, edge_type: str, reason: str | None) -> None:
        self.calls = 0
        self.edge_type = edge_type
        self.reason = reason

    def execute(self, *_args: Any, **_kwargs: Any) -> _QueryResult:
        self.calls += 1
        if self.calls == 1:
            return _QueryResult(
                first={
                    "run_id": "run-1",
                    "macro_id": "macro-1",
                    "l2_chain_count": 2,
                }
            )
        if self.calls == 2:
            return _QueryResult(
                rows=[
                    {"l2_chain_id": "chain-1", "node_order": 1, "lane": "context"},
                    {"l2_chain_id": "chain-2", "node_order": 2, "lane": "context"},
                ]
            )
        return _QueryResult(
            rows=[
                {
                    "from_chain_id": "chain-1",
                    "to_chain_id": "chain-2",
                    "edge_type": self.edge_type,
                    "relation_reason": self.reason,
                    "layer": "story",
                    "edge_weight": 0.9,
                    "metadata": {"model_label": self.edge_type, "raw_reason": self.reason},
                }
            ]
        )


def _chain(
    chain_id: str,
    *,
    start_date: date,
    end_date: date,
) -> l3_builder.L2Chain:
    return l3_builder.L2Chain(
        chain_id=chain_id,
        run_id="l2-run",
        segment_count=2,
        article_count=2,
        family_group="context",
        event_family="context",
        event_action="other",
        pair_key="actor-a -> actor-b",
        initiator="actor-a",
        target="actor-b",
        start_date=start_date,
        end_date=end_date,
        title="shared subject update",
        chain_quality="usable",
        quality_score=0.5,
        risk_flags=[],
        l1_cluster_ids=[],
    )


def test_edge_surface_inventory_is_executable_and_graph_briefing_has_no_edge_surface() -> None:
    assert len({item[0] for item in PUBLIC_EDGE_SURFACE_INVENTORY}) == len(
        PUBLIC_EDGE_SURFACE_INVENTORY
    )
    for _surface_id, relative_path, locator in PUBLIC_EDGE_SURFACE_INVENTORY:
        source = (PROJECT_ROOT / relative_path).read_text(encoding="utf-8")
        assert locator in source

    graph_briefing_source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (PROJECT_ROOT / "backend/api/features/graph_briefing").glob("*.py")
    )
    assert "edge_type" not in graph_briefing_source
    assert '"edges"' not in graph_briefing_source


@pytest.mark.parametrize(
    ("edge_type", "reason", "derivation", "expected_type", "expected_kind", "expected_state"),
    [
        (
            "influence",
            "时间重叠",
            "stored_derived_relation",
            "parallel",
            "temporal_overlap",
            "bounded",
        ),
        (
            "causal_escalation",
            "时间重叠",
            "stored_derived_relation",
            "parallel",
            "temporal_overlap",
            "bounded",
        ),
        (
            "influence",
            "相邻节点",
            "stored_derived_relation",
            "relation_unknown",
            "unknown",
            "explicit_unknown",
        ),
        (
            "causal_de_escalation",
            None,
            "stored_derived_relation",
            "relation_unknown",
            "unknown",
            "explicit_unknown",
        ),
        (
            "influence",
            "layout",
            "layout_sequence",
            "relation_unknown",
            "unknown",
            "explicit_unknown",
        ),
        (
            "macro_sequence",
            "可视节点时间推进",
            "layout_sequence",
            "macro_sequence",
            "layout_only",
            "bounded",
        ),
        (
            "unregistered_old_relation",
            None,
            "stored_derived_relation",
            "relation_unknown",
            "unknown",
            "explicit_unknown",
        ),
        (
            "relation_unknown",
            None,
            "computed_bridge",
            "relation_unknown",
            "unknown",
            "explicit_unknown",
        ),
        (
            "parallel",
            "时间重叠",
            "stored_derived_relation",
            "parallel",
            "temporal_overlap",
            "bounded",
        ),
    ],
)
def test_relation_projection_never_upgrades_overlap_adjacency_or_layout_to_influence(
    edge_type: str,
    reason: str | None,
    derivation: str,
    expected_type: str,
    expected_kind: str,
    expected_state: str,
) -> None:
    projection = story_graph.project_story_relation(
        edge_type=edge_type,
        relation_reason=reason,
        derivation=derivation,
    )

    assert projection.public_edge_type == expected_type
    assert projection.semantics.relation_kind == expected_kind
    assert projection.semantics.ontology_state == expected_state
    assert projection.semantics.causal_status == "not_established"
    assert projection.semantics.influence_status == "not_established"
    assert projection.semantics.public_edge_type == projection.public_edge_type
    assert "已证实" not in projection.public_relation_reason


def test_l3_builder_uses_context_not_influence_for_non_backbone_similarity() -> None:
    left = _chain(
        "chain-1",
        start_date=date(2026, 1, 1),
        end_date=date(2026, 1, 1),
    )
    right = _chain(
        "chain-2",
        start_date=date(2026, 1, 2),
        end_date=date(2026, 1, 2),
    )

    edge_type, reason = l3_builder.classify_edge(left, right, backbone=False)
    assert edge_type == "context"
    assert "影响" not in reason

    overlap = _chain(
        "chain-overlap",
        start_date=date(2026, 1, 1),
        end_date=date(2026, 1, 1),
    )
    assert l3_builder.classify_edge(left, overlap, backbone=False) == (
        "parallel",
        "时间重叠",
    )


def test_ground_news_timeline_repairs_legacy_overlap_without_exposing_influence() -> None:
    payload = story_graph_route.get_ground_news_timeline(
        chain_id="chain-1",
        l1_run_id="l1-run",
        l2_run_id="run-1",
        db=_TimelineOverlapSession(),
    )

    edge = payload["edges"][0]
    assert edge["edge_type"] == "parallel"
    assert edge["relation_semantics"]["relation_kind"] == "temporal_overlap"
    assert edge["relation_semantics"]["causal_status"] == "not_established"
    assert edge["relation_semantics"]["influence_status"] == "not_established"
    assert edge["relation_reason"] == "仅表示时间重叠，不代表影响或因果"
    assert "edge_type" not in payload["nodes"][1]
    assert "relation_reason" not in payload["nodes"][1]


@pytest.mark.parametrize(
    ("edge_type", "reason", "expected_type", "expected_state"),
    [
        ("influence", "时间重叠", "parallel", "bounded"),
        ("causal_escalation", "模型判断", "relation_unknown", "explicit_unknown"),
        ("future_schema_type", None, "relation_unknown", "explicit_unknown"),
    ],
)
def test_l3_public_route_projects_stored_old_schema_fail_closed(
    edge_type: str,
    reason: str | None,
    expected_type: str,
    expected_state: str,
) -> None:
    payload = story_graph_route.get_l3_macro_graph(
        macro_id="macro-1",
        run_id="run-1",
        max_nodes=8,
        db=_L3LegacyEdgeSession(edge_type, reason),
    )

    edge = payload["edges"][0]
    assert edge["edge_type"] == expected_type
    assert edge["relation_semantics"]["ontology_state"] == expected_state
    assert edge["relation_semantics"]["causal_status"] == "not_established"
    assert edge["relation_semantics"]["influence_status"] == "not_established"
    assert "metadata" not in edge
