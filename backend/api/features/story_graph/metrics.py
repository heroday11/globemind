"""Bounded public inventory and fail-closed projections for graph metrics.

The inventory documents implementation surfaces; it does not approve the
metrics or attest any runtime data.  Derived values remain unavailable until a
future contract binds an exact approved method, bounded inputs, their runtime
identity, and a safe evidence locator.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

_METHOD_SCHEMA_VERSION = "graph-metric-method-card-v1"
_INVENTORY_SCHEMA_VERSION = "graph-metric-inventory-v1"
_PROJECTION_SCHEMA_VERSION = "graph-metric-projection-v1"


def _method_card(
    metric_id: str,
    *,
    method_version: str | None,
    formula: str | None,
    inputs: list[str],
    approval_state: str = "not_approved",
    threshold_state: str = "not_approved",
    output_unit: str | None = None,
    identity_requirements: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": _METHOD_SCHEMA_VERSION,
        "metric_id": metric_id,
        "method_version": method_version,
        "formula": formula,
        "inputs": inputs,
        "input_identity_requirements": identity_requirements
        or ["run_id", "snapshot_id", "data_cutoff"],
        "approval_state": approval_state,
        "threshold_state": threshold_state,
        "output_unit": output_unit,
        "release_rule": (
            "numeric_or_ranked_display_requires_exact_card_inputs_identity_and_evidence"
        ),
    }


def _metric(
    metric_id: str,
    display_name: str,
    *,
    surfaces: list[str],
    public_fields: list[str],
    metric_kind: str,
    method_card: dict[str, Any],
    value_state: str = "unknown",
    evidence_state: str = "unavailable",
    reason_code: str = "EVIDENCE_LOCATOR_NOT_ESTABLISHED",
) -> dict[str, Any]:
    return {
        "metric_id": metric_id,
        "display_name": display_name,
        "surfaces": surfaces,
        "public_fields": public_fields,
        "metric_kind": metric_kind,
        "method_card": method_card,
        "evidence_locator": None,
        "evidence_state": evidence_state,
        "value_state": value_state,
        "fact_status": "not_established",
        "reason_code": reason_code,
    }


_METRICS = (
    _metric(
        "story_graph.research_value",
        "研究价值",
        surfaces=["StoryGraphView.story_card", "StoryGraphView.focus_sheet"],
        public_fields=["research_value"],
        metric_kind="derived_score",
        method_card=_method_card(
            "story_graph.research_value",
            method_version="story-graph-research-value-extracted-v1",
            formula=(
                "round(min(100, min(34, ln(1 + article_count) * 5.2) + "
                "min(22, ln(1 + segment_count + l2_chain_count) * 4.2) + "
                "min(14, ln(1 + date_span_days) * 2.5) + "
                "(quality_score * 20 if 0 < quality_score <= 1 else "
                "min(20, quality_score / 5)) + "
                "min(10, actor_count * 0.8 + topic_count * 0.4)))"
            ),
            inputs=[
                "article_count",
                "segment_count",
                "l2_chain_count",
                "quality_score",
                "date_span_days",
                "actor_count",
                "topic_count",
            ],
            output_unit="prototype_points_0_100",
        ),
    ),
    _metric(
        "story_graph.relation_strength",
        "关系强度",
        surfaces=["StoryGraphView.edge_focus", "StoryGraphView.assistant_context"],
        public_fields=["relation_strength"],
        metric_kind="derived_label",
        method_card=_method_card(
            "story_graph.relation_strength",
            method_version="story-relation-strength-extracted-v1",
            formula=(
                "strong if edge_weight >= 0.75 or shared_actor_count + "
                "shared_topic_count >= 3; medium if edge_weight >= 0.55 or "
                "shared_actor_count + shared_topic_count >= 1; pending if "
                "relation_reason is non-empty and not temporal_overlap; else weak"
            ),
            inputs=[
                "edge_weight",
                "shared_actor_count",
                "shared_topic_count",
                "relation_reason",
            ],
            output_unit="ordinal_label",
        ),
    ),
    _metric(
        "story_graph.edge_weight",
        "关系边权重",
        surfaces=["StoryGraph.current", "StoryGraph.legacy", "GroundNewsTimeline"],
        public_fields=["edge_weight", "weight"],
        metric_kind="stored_derived_score",
        method_card=_method_card(
            "story_graph.edge_weight",
            method_version=None,
            formula=None,
            inputs=["stored_edge_weight"],
            output_unit="unknown",
        ),
        reason_code="FORMULA_NOT_ESTABLISHED",
    ),
    _metric(
        "story_graph.layout_weight",
        "布局权重",
        surfaces=["StoryGraph.client_layout", "StoryGraph.l3_layout_edge"],
        public_fields=["layout_weight", "synthetic_weight"],
        metric_kind="layout_only",
        method_card=_method_card(
            "story_graph.layout_weight",
            method_version="story-graph-layout-weights-v1",
            formula="fixed layout constants by visual edge class: 0.36/0.66/0.72/0.82",
            inputs=["layout_edge_class"],
            approval_state="not_applicable_layout_only",
            threshold_state="not_applicable_layout_only",
            output_unit="layout_only",
            identity_requirements=["layout_algorithm_version"],
        ),
        value_state="layout_only",
        evidence_state="not_applicable",
        reason_code="LAYOUT_VALUE_NOT_ANALYTIC",
    ),
    _metric(
        "story_graph.related_story_score",
        "关联故事排序分",
        surfaces=["StoryGraph.related_stories"],
        public_fields=["score"],
        metric_kind="hidden_ranking_score",
        method_card=_method_card(
            "story_graph.related_story_score",
            method_version=None,
            formula=None,
            inputs=["stored_related_story_score"],
            output_unit="unknown",
        ),
        reason_code="FORMULA_NOT_ESTABLISHED",
    ),
    _metric(
        "story_graph.quality_score",
        "链质量分",
        surfaces=["StoryGraph.l2", "StoryGraph.l3", "DataSearch.hierarchy_results"],
        public_fields=["quality_score", "chain_quality", "importance_score"],
        metric_kind="legacy_quality_score",
        method_card=_method_card(
            "story_graph.quality_score",
            method_version=None,
            formula=None,
            inputs=["quality_score", "chain_quality", "importance_score"],
            output_unit="unknown",
        ),
        reason_code="FORMULA_NOT_ESTABLISHED",
    ),
    _metric(
        "ground_news.rank_score",
        "首页候选排序分",
        surfaces=["GroundNewsHome.lead", "GroundNewsHome.topic_selection"],
        public_fields=["rank_score"],
        metric_kind="editorial_candidate_score",
        method_card=_method_card(
            "ground_news.rank_score",
            method_version="ground-news-rank-score-extracted-v1",
            formula=(
                "round(min(article_count,90)*1.25 + min(source_count,30)*2.8 + "
                "(max(0,28-min(age_days,28))*1.2 if dates valid else 0) + "
                "(max(0,30-abs(left_pct-right_pct))*0.16 if known_bias>0 else 0) + "
                "min(l2_chain_count,6)*5 + min(l2_quality_score,1)*8, 4)"
            ),
            inputs=[
                "article_count",
                "source_count",
                "story_date",
                "latest_story_date",
                "left_pct",
                "right_pct",
                "center_pct",
                "state_aligned_pct",
                "l2_chain_count",
                "l2_quality_score",
            ],
            output_unit="prototype_rank_points",
        ),
        reason_code="METHOD_NOT_APPROVED",
    ),
    _metric(
        "ground_news.blindspot_score",
        "Blindspot 候选分",
        surfaces=["GroundNewsFeed", "GroundNewsTimeline", "GroundNewsDesk"],
        public_fields=["blindspot_score", "blindspot.score", "blindspot.level"],
        metric_kind="directory_composition_candidate_score",
        method_card=_method_card(
            "ground_news.blindspot_score",
            method_version="blindspot_v2",
            formula=(
                "score=max(0, abs(left_pct-right_pct) + "
                "(24 if one side<8 and the other>=8 else 0) + "
                "max(0,18-center_pct)*0.35 + min(max(source_count-2,0),18)*1.8 + "
                "state_aligned_pct*0.25 - min(18, "
                "unknown_source_count/max(source_count,1)*26)); level="
                "insufficient_data if source_count<4 or reviewed_known_source_count<=0, "
                "else high>=55, medium>=32, watch>=18, low"
            ),
            inputs=[
                "left_pct",
                "center_pct",
                "right_pct",
                "state_aligned_pct",
                "source_count",
                "reviewed_known_source_count",
                "unknown_source_count",
                "directory_low_or_unknown_label_pct",
            ],
            output_unit="prototype_candidate_points",
        ),
    ),
    _metric(
        "ground_news.timeline_quality",
        "时间线链质量",
        surfaces=["GroundNewsTimeline", "GroundNewsDesk.l2_chain"],
        public_fields=["quality_score", "chain_quality"],
        metric_kind="legacy_quality_score",
        method_card=_method_card(
            "ground_news.timeline_quality",
            method_version=None,
            formula=None,
            inputs=["quality_score", "chain_quality"],
            output_unit="unknown",
        ),
        reason_code="FORMULA_NOT_ESTABLISHED",
    ),
    _metric(
        "ground_news.event_research_value",
        "事件研究价值",
        surfaces=["GroundNewsDesk.event_value"],
        public_fields=["event_value_score", "event_value_label"],
        metric_kind="derived_score",
        method_card=_method_card(
            "ground_news.event_research_value",
            method_version="ground-news-event-value-extracted-v1",
            formula=(
                "score=round(min(32,source_count*4) + min(24,article_count*1.2) + "
                "min(18,related_chain_count*6) + min(16,segment_count*2) + "
                "min(10,blindspot_score/10)); label=high_value if score>=72, "
                "track if score>=48, else observe"
            ),
            inputs=[
                "source_count",
                "article_count",
                "related_chain_count",
                "segment_count",
                "blindspot_score",
            ],
            output_unit="prototype_points_0_100",
        ),
    ),
    _metric(
        "ground_news.coverage_signal",
        "覆盖信号",
        surfaces=["GroundNewsDesk.coverage_signal", "GroundNewsDesk.coverage_cards"],
        public_fields=["coverage_signal", "known_bias_pct", "coverage_gap"],
        metric_kind="derived_label",
        method_card=_method_card(
            "ground_news.coverage_signal",
            method_version="ground-news-coverage-signal-extracted-v1",
            formula=(
                "single_source if source_count<=1 or analysis_status=single_source; "
                "low_coverage if source_count<4 or analysis_status=low_source_count; "
                "rating_gap if no known directory composition or "
                "analysis_status=missing_political_ratings; blindspot_candidate if "
                "(left_pct<8 and right_pct>=20) or (right_pct<8 and left_pct>=20); "
                "else multi_source_coverage"
            ),
            inputs=[
                "source_count",
                "analysis_status",
                "left_pct",
                "right_pct",
                "known_directory_label_pct",
            ],
            output_unit="ordinal_label",
        ),
    ),
    _metric(
        "graph_briefing.quality_score",
        "Graph Briefing 质量别名",
        surfaces=["GraphBriefing.macro", "GraphBriefing.micro"],
        public_fields=["quality_score", "china_index_avg", "chain_quality"],
        metric_kind="legacy_alias_score",
        method_card=_method_card(
            "graph_briefing.quality_score",
            method_version=None,
            formula=None,
            inputs=["quality_score"],
            output_unit="unknown",
        ),
        reason_code="FORMULA_NOT_ESTABLISHED",
    ),
    _metric(
        "graph_briefing.membership_score",
        "Graph Briefing 成员分",
        surfaces=["GraphBriefing.micro"],
        public_fields=["membership_score", "importance_score"],
        metric_kind="legacy_alias_score",
        method_card=_method_card(
            "graph_briefing.membership_score",
            method_version=None,
            formula=None,
            inputs=["importance_score"],
            output_unit="unknown",
        ),
        reason_code="FORMULA_NOT_ESTABLISHED",
    ),
    _metric(
        "graph_briefing.opinion_aggregate",
        "Graph Briefing 舆情聚合",
        surfaces=["GraphBriefing.briefing"],
        public_fields=[
            "avg_sentiment_score",
            "sentiment_distribution",
            "topic_distribution",
        ],
        metric_kind="model_aggregate",
        method_card=_method_card(
            "graph_briefing.opinion_aggregate",
            method_version=None,
            formula=None,
            inputs=["stance_score", "event_family", "linked_news_membership"],
            output_unit="unknown",
            identity_requirements=[
                "model_id",
                "model_version",
                "method_version",
                "snapshot_id",
                "data_cutoff",
            ],
        ),
        reason_code="FORMULA_NOT_ESTABLISHED",
    ),
    _metric(
        "graph_sampling.coverage",
        "图抽样覆盖",
        surfaces=["StoryGraph", "GroundNewsTimeline", "GraphBriefing"],
        public_fields=["sampling"],
        metric_kind="delegated_contract",
        method_card=_method_card(
            "graph_sampling.coverage",
            method_version="graph-sampling-provenance-v1",
            formula="validated returned/evaluated/excluded arithmetic per bounded component",
            inputs=["sampling"],
            approval_state="delegated_contract_required",
            threshold_state="not_applicable",
            output_unit="mechanical_response_counts",
            identity_requirements=["sampling_component_unit", "selection_rule"],
        ),
        value_state="delegated_gate_required",
        evidence_state="delegated",
        reason_code="GRAPH_SAMPLING_CONTRACT_REQUIRED",
    ),
    _metric(
        "ground_news.source_profile_labels",
        "来源目录标签",
        surfaces=["GroundNewsSource", "GroundNewsDesk.source_profile"],
        public_fields=["source_profile"],
        metric_kind="delegated_contract",
        method_card=_method_card(
            "ground_news.source_profile_labels",
            method_version="ground-news-source-profile-method-card-v1",
            formula="field-level controlled method disposition",
            inputs=["source_profile.method_card", "source_profile.field_dispositions"],
            approval_state="delegated_contract_required",
            threshold_state="not_applicable",
            output_unit="directory_labels_not_fact_accuracy",
            identity_requirements=["profile_version", "method_ids"],
        ),
        value_state="delegated_gate_required",
        evidence_state="delegated",
        reason_code="SOURCE_PROFILE_CONTRACT_REQUIRED",
    ),
)


_INVENTORY = {
    "schema_version": _INVENTORY_SCHEMA_VERSION,
    "inventory_id": "globemind-public-graph-metrics-v1",
    "scope": ["graph_briefing", "ground_news", "story_graph"],
    "complete_runtime_surface_claim": False,
    "metrics": list(_METRICS),
}


def graph_metric_inventory() -> dict[str, Any]:
    """Return a defensive machine-readable inventory snapshot."""

    return deepcopy(_INVENTORY)


def project_public_graph_metric(
    metric_id: object,
    *,
    raw_value: object = None,
    raw_method_card: object = None,
    raw_inputs: object = None,
    evidence_locator: object = None,
) -> dict[str, Any]:
    """Suppress an unbound derived value instead of trusting payload claims."""

    del raw_value, raw_method_card, raw_inputs, evidence_locator
    entry = next(
        (
            item
            for item in _METRICS
            if isinstance(metric_id, str) and item["metric_id"] == metric_id
        ),
        None,
    )
    if entry is None:
        return {
            "schema_version": _PROJECTION_SCHEMA_VERSION,
            "metric_id": None,
            "value": None,
            "value_state": "unknown",
            "evidence_locator": None,
            "evidence_state": "unavailable",
            "fact_status": "not_established",
            "usable_for_ranking": False,
            "usable_as_fact": False,
            "reason_code": "METRIC_NOT_IN_BOUNDED_INVENTORY",
        }
    return {
        "schema_version": _PROJECTION_SCHEMA_VERSION,
        "metric_id": entry["metric_id"],
        "value": None,
        "value_state": entry["value_state"],
        "evidence_locator": None,
        "evidence_state": entry["evidence_state"],
        "fact_status": "not_established",
        "usable_for_ranking": False,
        "usable_as_fact": False,
        "reason_code": entry["reason_code"],
    }


__all__ = (
    "graph_metric_inventory",
    "project_public_graph_metric",
)
