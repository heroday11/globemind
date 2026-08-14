from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from fastapi import HTTPException

from api.features import story_graph
from api.routes import story_graph as story_graph_route

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONTRACT_NAMES = (
    "StoryNode",
    "StoryEdge",
    "StoryGraphResponse",
    "StoryRelationItem",
    "StoryListItem",
    "StoryListResponse",
    "ClusterNewsItem",
    "ClusterDetail",
)
SCHEMA_CONTRACT_NAMES = (
    "GraphSamplingComponent",
    "GraphSamplingProvenance",
    "StoryDerivedClaim",
    "StoryRelationSemantics",
    *CONTRACT_NAMES,
)
SCHEMA_FINGERPRINT = "bfe651e89dbe81c1728b7691dacde4cd132f74f124f9de8a520f23def46c78ec"


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


class _CapturingSession:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def execute(
        self,
        statement: Any,
        parameters: dict[str, Any],
    ) -> _QueryResult:
        self.calls.append((str(statement), parameters))
        if len(self.calls) == 1:
            return _QueryResult(first={"run_id": "l2-run", "chain_id": "chain-1"})
        return _QueryResult()


class _FailingSession:
    def execute(self, *_args: Any, **_kwargs: Any) -> _QueryResult:
        raise RuntimeError("postgresql" + "://user:secret@example.test/private")


class _L2ClaimSession:
    def __init__(self, title_suffix: str) -> None:
        self.calls = 0
        self.title_suffix = title_suffix

    def execute(self, *_args: Any, **_kwargs: Any) -> _QueryResult:
        self.calls += 1
        if self.calls == 1:
            return _QueryResult(first={"run_id": "run-1", "chain_id": "chain-1"})
        return _QueryResult(
            rows=[
                {
                    "segment_id": "segment-1",
                    "title": f"first-{self.title_suffix}",
                    "url": f"https://example.test/{self.title_suffix}",
                    "edge_type": "chain_start",
                },
                {
                    "segment_id": "segment-2",
                    "title": f"second-{self.title_suffix}",
                    "url": f"https://example.test/{self.title_suffix}/2",
                    "edge_type": "continuation",
                    "edge_weight": 0.75,
                    "relation_reason": "derived ordering",
                },
            ]
        )


class _LegacyStoryClaimSession:
    def __init__(self) -> None:
        self.calls = 0

    def execute(self, *_args: Any, **_kwargs: Any) -> _QueryResult:
        self.calls += 1
        if self.calls == 1:
            return _QueryResult(
                first={
                    "id": 7,
                    "title": "Unverified story title",
                    "event_type": "",
                    "article_count": 2,
                    "cluster_count": 1,
                    "start_date": None,
                    "end_date": None,
                    "meta": {},
                }
            )
        if self.calls == 2:
            return _QueryResult(
                rows=[
                    {
                        "from_cluster_id": "cluster-a",
                        "to_cluster_id": "cluster-b",
                        "edge_type": "causal_escalation",
                        "weight": 0.8,
                    }
                ]
            )
        return _QueryResult(
            rows=[
                {
                    "cluster_id": cluster_id,
                    "title": f"title-{cluster_id}",
                    "event_type": "other",
                    "initiator": None,
                    "target": None,
                    "article_count": 1,
                    "start_date": None,
                    "end_date": None,
                    "display_time": None,
                }
                for cluster_id in ("cluster-a", "cluster-b")
            ]
        )


def test_story_graph_contract_schema_is_behaviorally_stable() -> None:
    schemas = {
        name: getattr(story_graph, name).model_json_schema()
        for name in SCHEMA_CONTRACT_NAMES
    }
    serialized = json.dumps(
        schemas,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode()

    assert hashlib.sha256(serialized).hexdigest() == SCHEMA_FINGERPRINT


def test_story_graph_route_uses_only_feature_public_contracts() -> None:
    for name in CONTRACT_NAMES:
        assert getattr(story_graph_route, name) is getattr(story_graph, name)

    source = (PROJECT_ROOT / "backend/api/routes/story_graph.py").read_text(encoding="utf-8")
    assert "from api.features.story_graph import (" in source
    assert "api.features.story_graph.contracts" not in source
    assert "api.features.story_graph.presentation" not in source
    assert "from pydantic" not in source


def test_l2_detail_segment_join_is_bound_to_the_declared_l15_run() -> None:
    session = _CapturingSession()

    payload = story_graph_route.get_l2_chain_graph(
        chain_id="chain-1",
        run_id="l2-run",
        db=session,
    )

    assert payload["run_id"] == "l2-run"
    assert len(session.calls) == 2
    segment_sql, parameters = session.calls[1]
    normalized_sql = " ".join(segment_sql.split())
    assert (
        "JOIN public.event_l15_segments AS s "
        "ON s.run_id = cs.l15_run_id AND s.segment_id = cs.segment_id"
    ) in normalized_sql
    assert parameters == {"run_id": "l2-run", "chain_id": "chain-1"}


def test_story_graph_http_response_models_remain_bound_to_feature_contracts() -> None:
    response_models = {
        route.path: route.response_model
        for route in story_graph_route.router.routes
        if route.path
        in {
            "/api/story-graph/list",
            "/api/story-graph/{story_id}",
            "/api/story-graph/cluster/{cluster_id}",
        }
    }

    assert response_models == {
        "/api/story-graph/list": story_graph.StoryListResponse,
        "/api/story-graph/{story_id}": story_graph.StoryGraphResponse,
        "/api/story-graph/cluster/{cluster_id}": story_graph.ClusterDetail,
    }


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, "?"),
        ("United States", "美国"),
        ("People's Republic of China", "中国"),
        ("Donald Trump", "特朗普"),
        ("Recep Tayyip Erdogan", "埃尔多安"),
        ("Unknown Person", "Unknow"),
    ],
)
def test_story_graph_entity_presentation_contract(
    value: str | None,
    expected: str,
) -> None:
    assert story_graph.chinese_entity(value) == expected


def test_story_graph_visual_presentation_contract() -> None:
    assert story_graph.chinese_event_type(None) == "其他"
    assert story_graph.chinese_event_type("diplomacy") == "外交"
    assert story_graph.event_color("military") == "#E74C3C"
    assert story_graph.event_color("missing") == "#95A5A6"
    assert story_graph.story_node_size(0) == 10.0
    assert story_graph.story_node_size(31) == 50.0
    assert story_graph.event_family("military") == "conflict"
    assert story_graph.event_family("diplomacy") == "negotiation"
    assert story_graph.get_edge_style("response") == {
        "color": "#F39C12",
        "dashes": True,
        "width": 2,
        "label": "response",
    }


def test_story_graph_numeric_coercion_rejects_non_finite_values() -> None:
    assert story_graph_route._safe_float("NaN", default=0.25) == 0.25
    assert story_graph_route._safe_float("Infinity", default=0.25) == 0.25
    assert story_graph_route._safe_float("-Infinity", default=0.25) == 0.25
    assert story_graph_route._safe_int(float("inf"), default=7) == 7
    assert story_graph_route._safe_int(False, default=7) == 7


def test_story_relation_claims_are_stable_bounded_and_fail_closed() -> None:
    claim = story_graph.build_unavailable_story_relation_claim(
        graph_scope_id="l2:run-1:chain-1",
        from_id="segment-1",
        to_id="segment-2",
        relation_kind="continuation",
        derivation="stored_derived_relation",
    )
    repeated = story_graph.build_unavailable_story_relation_claim(
        graph_scope_id="l2:run-1:chain-1",
        from_id="segment-1",
        to_id="segment-2",
        relation_kind="continuation",
        derivation="stored_derived_relation",
    )

    assert claim == repeated
    assert claim.claim_id.startswith("sgc_")
    assert len(claim.claim_id) == 68
    assert "segment-1" not in claim.claim_id
    assert claim.citation_locator is None
    assert claim.citation_status == "unavailable"
    assert claim.reason_code == "GRAPH_RELATION_SOURCE_LOCATOR_UNAVAILABLE"
    assert claim.unknown_gate == "explicit_unknown"
    assert claim.usable_as_fact is False

    layout_claim = story_graph.build_unavailable_story_relation_claim(
        graph_scope_id="l3:run-1:macro-1",
        from_id="chain-1",
        to_id="chain-2",
        relation_kind="macro_sequence",
        derivation="layout_sequence",
    )
    assert layout_claim.claim_id != claim.claim_id
    assert layout_claim.reason_code == "GRAPH_LAYOUT_EDGE_NOT_EVIDENCE"

    with pytest.raises(ValueError, match="from_id"):
        story_graph.build_unavailable_story_relation_claim(
            graph_scope_id="l2:run-1:chain-1",
            from_id="segment-1\nsecret",
            to_id="segment-2",
            relation_kind="continuation",
            derivation="stored_derived_relation",
        )


def test_story_edges_cannot_omit_or_upgrade_unknown_claim_assurance() -> None:
    claim = story_graph.build_unavailable_story_relation_claim(
        graph_scope_id="legacy:7",
        from_id="cluster-a",
        to_id="cluster-b",
        relation_kind="response",
        derivation="stored_derived_relation",
    )
    relation_projection = story_graph.project_story_relation(
        edge_type="response",
        derivation="stored_derived_relation",
    )
    edge = story_graph.StoryEdge(
        from_id="cluster-a",
        to_id="cluster-b",
        edge_type=relation_projection.public_edge_type,
        weight=0.8,
        claim=claim,
        relation_semantics=relation_projection.semantics,
    )
    assert edge.claim == claim
    assert edge.relation_semantics.causal_status == "not_established"
    assert edge.relation_semantics.influence_status == "not_established"

    with pytest.raises(Exception):
        story_graph.StoryEdge(
            from_id="cluster-a",
            to_id="cluster-b",
            edge_type="response",
            weight=0.8,
        )
    with pytest.raises(Exception):
        story_graph.StoryEdge(
            from_id="cluster-a",
            to_id="cluster-b",
            edge_type="parallel",
            weight=0.8,
            claim=claim,
            relation_semantics=relation_projection.semantics,
        )
    with pytest.raises(Exception):
        story_graph.StoryDerivedClaim(
            claim_id=claim.claim_id,
            citation_locator="https://example.test/unverified",
            citation_status="available",
            reason_code="GRAPH_RELATION_SOURCE_LOCATOR_UNAVAILABLE",
            unknown_gate="supported",
            usable_as_fact=True,
        )


def test_l2_route_emits_claim_assurance_without_hashing_titles_or_urls() -> None:
    first = story_graph_route.get_l2_chain_graph(
        chain_id="chain-1",
        run_id="run-1",
        db=_L2ClaimSession("secret-a"),
    )
    second = story_graph_route.get_l2_chain_graph(
        chain_id="chain-1",
        run_id="run-1",
        db=_L2ClaimSession("secret-b"),
    )

    first_claim = first["edges"][0]["claim"]
    second_claim = second["edges"][0]["claim"]
    assert first_claim == second_claim
    assert first_claim == {
        "claim_id": first_claim["claim_id"],
        "citation_locator": None,
        "citation_status": "unavailable",
        "reason_code": "GRAPH_RELATION_SOURCE_LOCATOR_UNAVAILABLE",
        "unknown_gate": "explicit_unknown",
        "usable_as_fact": False,
    }
    assert "secret" not in json.dumps(first_claim)
    assert first["edges"][0]["edge_type"] == "continuation"
    assert first["edges"][0]["relation_semantics"]["relation_kind"] == "temporal_sequence"
    assert first["edges"][0]["relation_semantics"]["causal_status"] == "not_established"
    assert first["edges"][0]["relation_semantics"]["influence_status"] == "not_established"


def test_inventory_story_route_emits_fail_closed_claim_on_every_edge() -> None:
    payload = story_graph_route.get_story_graph(
        story_id=7,
        include_related=False,
        expanded=False,
        related_limit=12,
        db=_LegacyStoryClaimSession(),
    )

    assert len(payload.edges) == 1
    claim = payload.edges[0].claim
    assert claim.citation_locator is None
    assert claim.citation_status == "unavailable"
    assert claim.reason_code == "GRAPH_RELATION_SOURCE_LOCATOR_UNAVAILABLE"
    assert claim.unknown_gate == "explicit_unknown"
    assert claim.usable_as_fact is False
    assert payload.edges[0].edge_type == "relation_unknown"
    assert payload.edges[0].relation_semantics.ontology_state == "explicit_unknown"
    assert payload.edges[0].relation_semantics.causal_status == "not_established"
    assert payload.edges[0].relation_semantics.influence_status == "not_established"
    sampling = payload.sampling
    assert sampling.complete_graph_claim is False
    node_sampling = next(
        item for item in sampling.components if item.unit == "legacy_story_node"
    )
    assert node_sampling.state == "bounded_partial"
    assert node_sampling.returned_count == len(payload.nodes) == 2
    assert "ISOLATED_NODES_NOT_EVALUATED" in node_sampling.reason_codes


def test_story_graph_feature_has_no_route_or_database_dependency() -> None:
    feature_root = PROJECT_ROOT / "backend/api/features/story_graph"
    forbidden = (
        "api.routes",
        "sqlalchemy",
        "fastapi",
        "canonical_postgresql_url",
    )
    for source_path in feature_root.glob("*.py"):
        source = source_path.read_text(encoding="utf-8")
        assert not any(token in source for token in forbidden), source_path


def test_story_graph_evidence_requires_one_bounded_target() -> None:
    with pytest.raises(HTTPException) as exc_info:
        story_graph_route.get_story_graph_evidence(db=_FailingSession())
    assert exc_info.value.status_code == 422

    with pytest.raises(HTTPException) as exc_info:
        story_graph_route.get_story_graph_evidence(
            cluster_id="cluster-1",
            segment_id="segment-1",
            db=_FailingSession(),
        )
    assert exc_info.value.status_code == 422


def test_story_graph_errors_do_not_expose_database_exception_text() -> None:
    with pytest.raises(HTTPException) as exc_info:
        story_graph_route.get_story_graph_evidence(
            cluster_id="cluster-1",
            db=_FailingSession(),
        )

    assert exc_info.value.status_code == 500
    assert "secret" not in str(exc_info.value.detail)
    assert "postgresql" not in str(exc_info.value.detail)


def test_story_graph_evidence_urls_are_public_http_locators_only() -> None:
    rows = [
        {
            "news_id": 1,
            "title": "Valid",
            "url": "https://example.test/story?id=1&token=secret#fragment",
        },
        {"news_id": 2, "title": "Script", "url": "javascript:alert(1)"},
        {
            "news_id": 3,
            "title": "Credential",
            "url": "https://user:password@example.test/private",
        },
    ]

    payload = story_graph_route._news_rows_payload(rows)

    assert payload[0]["url"] == "https://example.test/story"
    assert payload[1]["url"] is None
    assert payload[2]["url"] is None

    source_row = story_graph_route._source_row_for_evidence(
        {
            "news_id": 4,
            "url": "javascript:alert(1)",
            "evidence_url": "https://reviewer:secret@example.test/method?token=x",
        }
    )
    assert source_row["url"] is None
    assert source_row["evidence_url"] is None
