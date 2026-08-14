from __future__ import annotations

from copy import deepcopy


EXPECTED_METRIC_IDS = {
    "graph_briefing.membership_score",
    "graph_briefing.opinion_aggregate",
    "graph_briefing.quality_score",
    "graph_sampling.coverage",
    "ground_news.blindspot_score",
    "ground_news.coverage_signal",
    "ground_news.event_research_value",
    "ground_news.rank_score",
    "ground_news.source_profile_labels",
    "ground_news.timeline_quality",
    "story_graph.edge_weight",
    "story_graph.layout_weight",
    "story_graph.quality_score",
    "story_graph.related_story_score",
    "story_graph.relation_strength",
    "story_graph.research_value",
}


def test_public_graph_metric_inventory_is_bounded_and_fail_closed() -> None:
    from api.features.story_graph.metrics import graph_metric_inventory

    inventory = graph_metric_inventory()

    assert inventory["schema_version"] == "graph-metric-inventory-v1"
    assert inventory["inventory_id"] == "globemind-public-graph-metrics-v1"
    assert inventory["complete_runtime_surface_claim"] is False
    assert inventory["scope"] == ["graph_briefing", "ground_news", "story_graph"]
    assert 1 <= len(inventory["metrics"]) <= 32
    assert {item["metric_id"] for item in inventory["metrics"]} == EXPECTED_METRIC_IDS

    exact_keys = {
        "metric_id",
        "display_name",
        "surfaces",
        "public_fields",
        "metric_kind",
        "method_card",
        "evidence_locator",
        "evidence_state",
        "value_state",
        "fact_status",
        "reason_code",
    }
    for item in inventory["metrics"]:
        assert set(item) == exact_keys
        assert 1 <= len(item["surfaces"]) <= 12
        assert 1 <= len(item["public_fields"]) <= 12
        assert item["fact_status"] == "not_established"
        assert item["evidence_locator"] is None
        assert item["method_card"]["schema_version"] == "graph-metric-method-card-v1"
        assert item["method_card"]["metric_id"] == item["metric_id"]
        assert item["method_card"]["approval_state"] in {
            "not_approved",
            "not_applicable_layout_only",
            "delegated_contract_required",
        }
        if item["value_state"] == "unknown":
            assert item["evidence_state"] == "unavailable"
            assert item["reason_code"] in {
                "EVIDENCE_LOCATOR_NOT_ESTABLISHED",
                "FORMULA_NOT_ESTABLISHED",
                "METHOD_NOT_APPROVED",
            }

    by_id = {item["metric_id"]: item for item in inventory["metrics"]}
    assert "quality_score * 20 if 0 < quality_score <= 1" in (
        by_id["story_graph.research_value"]["method_card"]["formula"]
    )
    assert by_id["story_graph.relation_strength"]["method_card"]["inputs"] == [
        "edge_weight",
        "shared_actor_count",
        "shared_topic_count",
        "relation_reason",
    ]
    assert "edge_weight >= 0.75" in (
        by_id["story_graph.relation_strength"]["method_card"]["formula"]
    )
    assert "unknown_source_count/max(source_count,1)*26" in (
        by_id["ground_news.blindspot_score"]["method_card"]["formula"]
    )
    assert "analysis_status=missing_political_ratings" in (
        by_id["ground_news.coverage_signal"]["method_card"]["formula"]
    )


def test_graph_metric_inventory_is_defensive_and_projection_never_trusts_raw_score() -> None:
    from api.features.story_graph.metrics import (
        graph_metric_inventory,
        project_public_graph_metric,
    )

    first = graph_metric_inventory()
    first["metrics"][0]["method_card"]["approval_state"] = "approved"
    assert graph_metric_inventory()["metrics"][0]["method_card"]["approval_state"] != (
        "approved"
    )

    for metric_id in EXPECTED_METRIC_IDS - {
        "graph_sampling.coverage",
        "ground_news.source_profile_labels",
        "story_graph.layout_weight",
    }:
        projected = project_public_graph_metric(
            metric_id,
            raw_value=99.99,
            raw_method_card={
                "schema_version": "graph-metric-method-card-v1",
                "metric_id": metric_id,
                "approval_state": "approved",
                "formula": "trust me",
                "inputs": ["user text"],
            },
            raw_inputs={"weight": 0.99, "free_text": "high confidence"},
            evidence_locator="https://example.test/fabricated",
        )
        assert projected == {
            "schema_version": "graph-metric-projection-v1",
            "metric_id": metric_id,
            "value": None,
            "value_state": "unknown",
            "evidence_locator": None,
            "evidence_state": "unavailable",
            "fact_status": "not_established",
            "usable_for_ranking": False,
            "usable_as_fact": False,
            "reason_code": projected["reason_code"],
        }

    unknown = project_public_graph_metric("user.free_text", raw_value=100)
    assert unknown["metric_id"] is None
    assert unknown["value"] is None
    assert unknown["reason_code"] == "METRIC_NOT_IN_BOUNDED_INVENTORY"


def test_graph_briefing_legacy_score_aliases_are_unknown_with_disclosures() -> None:
    from api.features.graph_briefing.service import _macro_dto, _micro_dto

    macro = _macro_dto(
        {
            "macro_id": "macro-1",
            "quality_score": 0.98,
            "article_count": 12,
            "l2_chain_count": 3,
            "l1_cluster_count": 4,
            "segment_count": 5,
        }
    )
    assert macro["quality_score"] is None
    assert macro["china_index_avg"] is None
    assert macro["metric_disclosures"][0]["metric_id"] == (
        "graph_briefing.quality_score"
    )
    assert macro["metric_disclosures"][0]["value"] is None

    micro = _micro_dto(
        {
            "chain_id": "chain-1",
            "quality_score": 0.88,
            "importance_score": 0.77,
            "chain_quality": "strong",
        }
    )
    assert micro["quality_score"] is None
    assert micro["china_index_avg"] is None
    assert micro["membership_score"] is None
    assert micro["chain_quality"] is None
    assert {item["metric_id"] for item in micro["metric_disclosures"]} == {
        "graph_briefing.membership_score",
        "graph_briefing.quality_score",
    }


def test_graph_briefing_opinion_aggregates_do_not_escape_without_method_and_evidence() -> None:
    from api.features.graph_briefing.service import GraphBriefingService

    class Repository:
        def get_macro(self, macro_id: str):
            return {"macro_id": macro_id, "quality_score": 0.9}

        def briefing_average(self, macro_id: str):
            return 0.91

        def briefing_sentiment_distribution(self, macro_id: str):
            return [{"label": "positive", "count": 9}]

        def briefing_topic_distribution(self, macro_id: str):
            return [{"topic": "security", "count": 9}]

    service = GraphBriefingService(None, repository=Repository())  # type: ignore[arg-type]
    result = service.get_briefing("macro-1")

    assert result["avg_sentiment_score"] is None
    assert result["sentiment_distribution"] is None
    assert result["topic_distribution"] is None
    assert result["opinion_model_note"] == (
        "Opinion aggregate unavailable: method, model/data identity, and evidence "
        "locator are not established."
    )
    assert result["metric_disclosures"][0]["metric_id"] == (
        "graph_briefing.opinion_aggregate"
    )
    assert result["metric_disclosures"][0]["value"] is None
