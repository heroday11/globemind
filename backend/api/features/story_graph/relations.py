"""Fail-closed public ontology projection for story-graph relations."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from api.features.story_graph.contracts import (
    PublicStoryRelationType,
    StoryRelationSemantics,
)

RelationDerivation = Literal[
    "stored_derived_relation",
    "computed_bridge",
    "layout_sequence",
]

_TYPE_PATTERN = re.compile(r"^[a-z0-9][a-z0-9 _-]{0,95}$")
_OVERLAP_REASONS = frozenset(
    {
        "时间重叠",
        "temporal overlap",
        "temporal_overlap",
        "overlap",
    }
)
_OVERLAP_TYPES = frozenset({"parallel", "overlap", "temporal_overlap", "concurrent"})
_TEMPORAL_TYPES = frozenset(
    {
        "macro_sequence",
        "pair_sequence",
        "branch_sequence",
        "continuation",
        "continued",
        "progression",
        "transition",
        "response",
        "same_thread",
        "preview_to_event",
        "event_to_outcome",
        "outcome_to_context",
        "chain_start",
        "start",
    }
)
_EVENT_SIGNAL_TYPES = frozenset(
    {
        "escalation",
        "resolution",
        "de_escalation",
        "diplomacy",
        "market_reaction",
        "analysis_context",
    }
)
_CONTEXT_TYPES = frozenset({"context", "branch", "pair_family", "gap"})
_LAYOUT_TYPES = frozenset({"macro_sequence", "branch_sequence", "branch", "context"})

_PUBLIC_REASONS = {
    "temporal_overlap": "仅表示时间重叠，不代表影响或因果",
    "temporal_sequence": "仅表示时间排序或相邻，不代表影响或因果",
    "contextual_association": "仅表示派生关联线索，不代表影响或因果",
    "event_progression_signal": "仅表示事件进展分类信号，不代表影响或因果",
    "layout_only": "仅用于图形布局，不代表影响、相关或因果",
    "unknown": "关系类型未知，不可解释为影响或因果",
}


@dataclass(frozen=True, slots=True)
class StoryRelationProjection:
    public_edge_type: PublicStoryRelationType
    public_relation_reason: str
    semantics: StoryRelationSemantics


def _normalized_type(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip().lower()
    if not _TYPE_PATTERN.fullmatch(normalized):
        return None
    normalized = re.sub(r"[\s-]+", "_", normalized)
    return normalized


def _normalized_reason(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip().lower()
    if not normalized or len(normalized) > 96:
        return None
    return normalized


def _projection(
    *,
    public_edge_type: PublicStoryRelationType,
    relation_kind: Literal[
        "temporal_overlap",
        "temporal_sequence",
        "contextual_association",
        "event_progression_signal",
        "layout_only",
        "unknown",
    ],
    temporal_basis: Literal["overlap", "ordered_or_adjacent", "not_applicable", "unknown"],
    evidence_role: Literal["derived_signal", "layout_only", "unknown"],
    derivation: RelationDerivation,
    reason_code: str,
    ontology_state: Literal["bounded", "explicit_unknown"] = "bounded",
) -> StoryRelationProjection:
    semantics = StoryRelationSemantics(
        ontology_state=ontology_state,
        public_edge_type=public_edge_type,
        relation_kind=relation_kind,
        temporal_basis=temporal_basis,
        causal_status="not_established",
        influence_status="not_established",
        evidence_role=evidence_role,
        derivation=derivation,
        reason_code=reason_code,
        source_reason_disclosed=False,
    )
    return StoryRelationProjection(
        public_edge_type=public_edge_type,
        public_relation_reason=_PUBLIC_REASONS[relation_kind],
        semantics=semantics,
    )


def _unknown_projection(
    *,
    derivation: RelationDerivation,
    reason_code: Literal[
        "UNVERIFIED_INFLUENCE_OR_CAUSAL_TYPE",
        "UNKNOWN_LEGACY_RELATION_SCHEMA",
        "SYNTHETIC_INFLUENCE_OR_CAUSAL_REJECTED",
    ],
) -> StoryRelationProjection:
    return _projection(
        public_edge_type="relation_unknown",
        relation_kind="unknown",
        temporal_basis="unknown",
        evidence_role="unknown",
        derivation=derivation,
        reason_code=reason_code,
        ontology_state="explicit_unknown",
    )


def project_story_relation(
    *,
    edge_type: object,
    relation_reason: object = None,
    derivation: RelationDerivation,
) -> StoryRelationProjection:
    """Project stored or synthetic labels into a closed non-causal ontology.

    Arbitrary reason text is never disclosed or used as a display label.  Only
    exact, fixed overlap markers may repair a known historical misclassification.
    """

    if derivation not in {
        "stored_derived_relation",
        "computed_bridge",
        "layout_sequence",
    }:
        raise ValueError("relation derivation is unsupported")
    raw_type = _normalized_type(edge_type)
    reason = _normalized_reason(relation_reason)

    if derivation == "layout_sequence":
        if raw_type in _LAYOUT_TYPES:
            return _projection(
                public_edge_type=raw_type,
                relation_kind="layout_only",
                temporal_basis="not_applicable",
                evidence_role="layout_only",
                derivation=derivation,
                reason_code="LAYOUT_EDGE_NOT_RELATION_EVIDENCE",
            )
        if raw_type and (raw_type == "influence" or raw_type.startswith("causal_")):
            return _unknown_projection(
                derivation=derivation,
                reason_code="SYNTHETIC_INFLUENCE_OR_CAUSAL_REJECTED",
            )
        return _unknown_projection(
            derivation=derivation,
            reason_code="UNKNOWN_LEGACY_RELATION_SCHEMA",
        )

    if derivation == "computed_bridge":
        if raw_type and (raw_type == "influence" or raw_type.startswith("causal_")):
            return _unknown_projection(
                derivation=derivation,
                reason_code="UNVERIFIED_INFLUENCE_OR_CAUSAL_TYPE",
            )
        governed_inputs = (
            _OVERLAP_TYPES
            | _TEMPORAL_TYPES
            | _EVENT_SIGNAL_TYPES
            | _CONTEXT_TYPES
        )
        if raw_type not in governed_inputs:
            return _unknown_projection(
                derivation=derivation,
                reason_code="UNKNOWN_LEGACY_RELATION_SCHEMA",
            )
        public_type: PublicStoryRelationType = (
            raw_type if raw_type in {"context", "branch", "pair_family"} else "context"
        )
        return _projection(
            public_edge_type=public_type,
            relation_kind="contextual_association",
            temporal_basis="not_applicable",
            evidence_role="derived_signal",
            derivation=derivation,
            reason_code="CONTEXT_ASSOCIATION_NOT_CAUSAL",
        )

    if raw_type in _OVERLAP_TYPES or reason in _OVERLAP_REASONS:
        return _projection(
            public_edge_type="parallel",
            relation_kind="temporal_overlap",
            temporal_basis="overlap",
            evidence_role="derived_signal",
            derivation=derivation,
            reason_code="TEMPORAL_OVERLAP_NOT_INFLUENCE",
        )
    if raw_type and (raw_type == "influence" or raw_type.startswith("causal_")):
        return _unknown_projection(
            derivation=derivation,
            reason_code="UNVERIFIED_INFLUENCE_OR_CAUSAL_TYPE",
        )
    if raw_type in _TEMPORAL_TYPES:
        return _projection(
            public_edge_type=raw_type,
            relation_kind="temporal_sequence",
            temporal_basis="ordered_or_adjacent",
            evidence_role="derived_signal",
            derivation=derivation,
            reason_code="TEMPORAL_ORDER_NOT_CAUSAL",
        )
    if raw_type in _EVENT_SIGNAL_TYPES:
        return _projection(
            public_edge_type=raw_type,
            relation_kind="event_progression_signal",
            temporal_basis="not_applicable",
            evidence_role="derived_signal",
            derivation=derivation,
            reason_code="DERIVED_EVENT_SIGNAL_NOT_CAUSAL",
        )
    if raw_type in _CONTEXT_TYPES:
        public_type = "context" if raw_type == "gap" else raw_type
        return _projection(
            public_edge_type=public_type,
            relation_kind="contextual_association",
            temporal_basis="not_applicable",
            evidence_role="derived_signal",
            derivation=derivation,
            reason_code="CONTEXT_ASSOCIATION_NOT_CAUSAL",
        )
    return _unknown_projection(
        derivation=derivation,
        reason_code="UNKNOWN_LEGACY_RELATION_SCHEMA",
    )


__all__ = (
    "RelationDerivation",
    "StoryRelationProjection",
    "project_story_relation",
)
