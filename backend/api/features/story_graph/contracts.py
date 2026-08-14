"""Stable response contracts for the story graph feature."""

from __future__ import annotations

from typing import Annotated, Any, Dict, List, Literal, Optional

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    field_validator,
    model_validator,
)

GraphSamplingCount = Annotated[
    StrictInt,
    Field(ge=0, le=2_147_483_647),
]
GraphSamplingUnit = Literal[
    "macro_node",
    "micro_node",
    "news_item",
    "unclustered_news_item",
    "l2_chain_node",
    "l15_segment_node",
    "legacy_story_node",
    "related_story",
]
GraphSamplingRule = Literal[
    "top_article_count_then_stable_id",
    "per_parent_article_count_then_stable_id",
    "recent_news_per_parent",
    "orphan_then_ambient_recent",
    "offset_page_stable_order",
    "lane_quota_then_importance",
    "ordered_chain_segments",
    "stored_edge_referenced_nodes",
    "related_story_rank",
]
GraphSamplingReason = Literal[
    "DISPLAY_LIMIT",
    "PER_PARENT_LIMIT",
    "PAGE_WINDOW_NOT_RETURNED",
    "CANDIDATE_UNIVERSE_NOT_COUNTED",
    "EVALUATED_COUNT_UNAVAILABLE_OR_INCONSISTENT",
    "FILTERED_BY_SELECTION_RULE",
    "ISOLATED_NODES_NOT_EVALUATED",
    "RELATED_STORY_LIMIT",
    "AMBIENT_FILL_NOT_GRAPH_MEMBERSHIP",
    "GRAPH_COMPLETENESS_NOT_ESTABLISHED",
]


class GraphSamplingComponent(BaseModel):
    """Bounded aggregate coverage for one graph response layer.

    Excluded identifiers are deliberately not part of the contract. Aggregate
    counts and fixed reason codes disclose coverage loss without exposing row
    identities or article content.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    unit: GraphSamplingUnit
    state: Literal["bounded_partial", "unknown"]
    requested_count: GraphSamplingCount | None = None
    evaluated_count: GraphSamplingCount | None = None
    returned_count: GraphSamplingCount
    excluded_count: GraphSamplingCount | None = None
    limit: GraphSamplingCount | None = None
    overflow: bool | None = None
    selection_rule: GraphSamplingRule
    reason_codes: list[GraphSamplingReason] = Field(min_length=1, max_length=8)
    excluded_node_ids_disclosed: Literal[False] = False

    @field_validator("excluded_node_ids_disclosed", mode="before")
    @classmethod
    def validate_identifier_disclosure_flag(cls, value: Any) -> bool:
        if value is not False:
            raise ValueError("excluded node identifiers cannot be disclosed")
        return False

    @model_validator(mode="after")
    def validate_coverage_arithmetic(self) -> "GraphSamplingComponent":
        if len(self.reason_codes) != len(set(self.reason_codes)):
            raise ValueError("sampling reason codes must be unique")
        if "GRAPH_COMPLETENESS_NOT_ESTABLISHED" not in self.reason_codes:
            raise ValueError("sampling must not imply a complete graph")
        if (
            self.requested_count is not None
            and self.returned_count > self.requested_count
        ):
            raise ValueError("returned count exceeds the requested bound")
        if self.limit is not None and self.returned_count > self.limit:
            raise ValueError("returned count exceeds the display limit")
        if self.state == "unknown":
            if any(
                value is not None
                for value in (
                    self.evaluated_count,
                    self.excluded_count,
                    self.overflow,
                )
            ):
                raise ValueError("unknown sampling cannot expose derived coverage")
            if not any(
                reason
                in {
                    "CANDIDATE_UNIVERSE_NOT_COUNTED",
                    "EVALUATED_COUNT_UNAVAILABLE_OR_INCONSISTENT",
                }
                for reason in self.reason_codes
            ):
                raise ValueError("unknown sampling needs a fixed unknown reason")
            return self

        if (
            self.evaluated_count is None
            or self.excluded_count is None
            or self.overflow is None
        ):
            raise ValueError("bounded sampling needs complete count arithmetic")
        if self.evaluated_count < self.returned_count:
            raise ValueError("evaluated count is smaller than returned count")
        expected_excluded = self.evaluated_count - self.returned_count
        if self.excluded_count != expected_excluded:
            raise ValueError("excluded count is inconsistent")
        if self.overflow is not (expected_excluded > 0):
            raise ValueError("overflow state is inconsistent")
        return self


class GraphSamplingProvenance(BaseModel):
    """Sampling envelope shared by public graph responses."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal["graph-sampling-provenance-v1"] = (
        "graph-sampling-provenance-v1"
    )
    coverage_state: Literal["partial", "unknown"]
    components: list[GraphSamplingComponent] = Field(min_length=1, max_length=8)
    complete_graph_claim: Literal[False] = False

    @field_validator("complete_graph_claim", mode="before")
    @classmethod
    def validate_complete_graph_claim(cls, value: Any) -> bool:
        if value is not False:
            raise ValueError("complete graph claims are forbidden")
        return False

    @model_validator(mode="after")
    def validate_component_identity(self) -> "GraphSamplingProvenance":
        units = [component.unit for component in self.components]
        if len(units) != len(set(units)):
            raise ValueError("sampling component units must be unique")
        expected = (
            "partial"
            if all(item.state == "bounded_partial" for item in self.components)
            else "unknown"
        )
        if self.coverage_state != expected:
            raise ValueError("sampling coverage state is inconsistent")
        return self


def build_graph_sampling_component(
    *,
    unit: GraphSamplingUnit,
    returned_count: int,
    selection_rule: GraphSamplingRule,
    reason_codes: list[GraphSamplingReason],
    requested_count: int | None = None,
    evaluated_count: int | None = None,
    limit: int | None = None,
) -> GraphSamplingComponent:
    """Build known arithmetic or downgrade inconsistent counts to unknown."""

    reasons = list(dict.fromkeys(reason_codes))
    evaluated_is_known = (
        type(evaluated_count) is int
        and evaluated_count >= returned_count
        and evaluated_count <= 2_147_483_647
    )
    if not evaluated_is_known:
        unknown_reason: GraphSamplingReason = (
            "EVALUATED_COUNT_UNAVAILABLE_OR_INCONSISTENT"
            if evaluated_count is not None
            else "CANDIDATE_UNIVERSE_NOT_COUNTED"
        )
        if unknown_reason not in reasons:
            reasons.append(unknown_reason)
        return GraphSamplingComponent(
            unit=unit,
            state="unknown",
            requested_count=requested_count,
            evaluated_count=None,
            returned_count=returned_count,
            excluded_count=None,
            limit=limit,
            overflow=None,
            selection_rule=selection_rule,
            reason_codes=reasons,
            excluded_node_ids_disclosed=False,
        )
    excluded_count = evaluated_count - returned_count
    return GraphSamplingComponent(
        unit=unit,
        state="bounded_partial",
        requested_count=requested_count,
        evaluated_count=evaluated_count,
        returned_count=returned_count,
        excluded_count=excluded_count,
        limit=limit,
        overflow=excluded_count > 0,
        selection_rule=selection_rule,
        reason_codes=reasons,
        excluded_node_ids_disclosed=False,
    )


def build_graph_sampling_provenance(
    *components: GraphSamplingComponent,
) -> GraphSamplingProvenance:
    return GraphSamplingProvenance(
        coverage_state=(
            "partial"
            if components and all(item.state == "bounded_partial" for item in components)
            else "unknown"
        ),
        components=list(components),
        complete_graph_claim=False,
    )


class StoryDerivedClaim(BaseModel):
    """Fail-closed assurance attached to every derived graph relation.

    Story graph relation stores and layout algorithms do not currently retain
    an article-level locator that entails the relationship.  Consequently this
    contract deliberately has no supported/available variant: callers cannot
    upgrade a graph edge into a factual assertion by changing response data.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    claim_id: str = Field(min_length=68, max_length=68, pattern=r"^sgc_[0-9a-f]{64}$")
    citation_locator: None = None
    citation_status: Literal["unavailable"] = "unavailable"
    reason_code: Literal[
        "GRAPH_RELATION_SOURCE_LOCATOR_UNAVAILABLE",
        "GRAPH_LAYOUT_EDGE_NOT_EVIDENCE",
    ]
    unknown_gate: Literal["explicit_unknown"] = "explicit_unknown"
    usable_as_fact: Literal[False] = False


PublicStoryRelationType = Literal[
    "parallel",
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
    "escalation",
    "resolution",
    "de_escalation",
    "diplomacy",
    "market_reaction",
    "analysis_context",
    "context",
    "branch",
    "pair_family",
    "relation_unknown",
]


class StoryRelationSemantics(BaseModel):
    """Closed semantics for a public graph edge.

    Relation categories are presentation/analysis signals only.  This envelope
    deliberately has no supported causal or influence state; those conclusions
    require a separately governed evidence contract and human gold.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal["story-relation-semantics-v1"] = (
        "story-relation-semantics-v1"
    )
    ontology_state: Literal["bounded", "explicit_unknown"]
    public_edge_type: PublicStoryRelationType
    relation_kind: Literal[
        "temporal_overlap",
        "temporal_sequence",
        "contextual_association",
        "event_progression_signal",
        "layout_only",
        "unknown",
    ]
    temporal_basis: Literal["overlap", "ordered_or_adjacent", "not_applicable", "unknown"]
    causal_status: Literal["not_established"] = "not_established"
    influence_status: Literal["not_established"] = "not_established"
    evidence_role: Literal["derived_signal", "layout_only", "unknown"]
    derivation: Literal[
        "stored_derived_relation",
        "computed_bridge",
        "layout_sequence",
        "legacy_payload_unknown",
    ]
    reason_code: Literal[
        "TEMPORAL_OVERLAP_NOT_INFLUENCE",
        "TEMPORAL_ORDER_NOT_CAUSAL",
        "CONTEXT_ASSOCIATION_NOT_CAUSAL",
        "DERIVED_EVENT_SIGNAL_NOT_CAUSAL",
        "LAYOUT_EDGE_NOT_RELATION_EVIDENCE",
        "UNVERIFIED_INFLUENCE_OR_CAUSAL_TYPE",
        "UNKNOWN_LEGACY_RELATION_SCHEMA",
        "RELATION_SEMANTICS_CONTRACT_MISSING_OR_INVALID",
        "SYNTHETIC_INFLUENCE_OR_CAUSAL_REJECTED",
    ]
    source_reason_disclosed: Literal[False] = False

    @model_validator(mode="after")
    def validate_fail_closed_semantics(self) -> "StoryRelationSemantics":
        is_unknown = self.ontology_state == "explicit_unknown"
        if is_unknown:
            if not (
                self.public_edge_type == "relation_unknown"
                and self.relation_kind == "unknown"
                and self.temporal_basis == "unknown"
                and self.evidence_role == "unknown"
                and self.reason_code
                in {
                    "UNVERIFIED_INFLUENCE_OR_CAUSAL_TYPE",
                    "UNKNOWN_LEGACY_RELATION_SCHEMA",
                    "RELATION_SEMANTICS_CONTRACT_MISSING_OR_INVALID",
                    "SYNTHETIC_INFLUENCE_OR_CAUSAL_REJECTED",
                }
            ):
                raise ValueError("unknown relation semantics are inconsistent")
            return self
        if self.public_edge_type == "relation_unknown" or self.relation_kind == "unknown":
            raise ValueError("bounded relation semantics cannot be unknown")
        if self.relation_kind == "temporal_overlap":
            valid = (
                self.public_edge_type == "parallel"
                and self.temporal_basis == "overlap"
                and self.evidence_role == "derived_signal"
                and self.reason_code == "TEMPORAL_OVERLAP_NOT_INFLUENCE"
            )
        elif self.relation_kind == "temporal_sequence":
            valid = (
                self.public_edge_type
                in {
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
                and self.temporal_basis == "ordered_or_adjacent"
                and self.evidence_role == "derived_signal"
                and self.reason_code == "TEMPORAL_ORDER_NOT_CAUSAL"
            )
        elif self.relation_kind == "event_progression_signal":
            valid = (
                self.public_edge_type
                in {
                    "escalation",
                    "resolution",
                    "de_escalation",
                    "diplomacy",
                    "market_reaction",
                    "analysis_context",
                }
                and self.temporal_basis == "not_applicable"
                and self.evidence_role == "derived_signal"
                and self.reason_code == "DERIVED_EVENT_SIGNAL_NOT_CAUSAL"
            )
        elif self.relation_kind == "contextual_association":
            valid = (
                self.public_edge_type in {"context", "branch", "pair_family"}
                and self.temporal_basis == "not_applicable"
                and self.evidence_role == "derived_signal"
                and self.reason_code == "CONTEXT_ASSOCIATION_NOT_CAUSAL"
            )
        else:
            valid = (
                self.relation_kind == "layout_only"
                and self.public_edge_type
                in {"macro_sequence", "branch_sequence", "branch", "context"}
                and self.temporal_basis == "not_applicable"
                and self.derivation == "layout_sequence"
                and self.evidence_role == "layout_only"
                and self.reason_code == "LAYOUT_EDGE_NOT_RELATION_EVIDENCE"
            )
        if not valid:
            raise ValueError("bounded relation semantics are inconsistent")
        return self


class StoryNode(BaseModel):
    id: str
    label: str
    event_type: str
    article_count: int
    date_range: str
    display_time: Optional[str] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    initiator: Optional[str] = None
    target: Optional[str] = None
    cluster_id_raw: Optional[str] = None
    story_id: Optional[int] = None
    story_title: Optional[str] = None
    story_role: Optional[str] = None
    relation_layer: Optional[str] = None
    color: str = ""
    size: float = 10.0


class StoryEdge(BaseModel):
    from_id: str
    to_id: str
    edge_type: PublicStoryRelationType
    weight: float
    layer: Optional[str] = None
    relation_reason: Optional[str] = None
    source_story_id: Optional[int] = None
    target_story_id: Optional[int] = None
    claim: StoryDerivedClaim
    relation_semantics: StoryRelationSemantics

    @model_validator(mode="after")
    def validate_relation_semantics(self) -> "StoryEdge":
        if self.edge_type != self.relation_semantics.public_edge_type:
            raise ValueError("edge type does not match relation semantics")
        return self


class StoryGraphResponse(BaseModel):
    story_id: int
    story_title: str
    story_event_type: str
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    article_count: int
    cluster_count: int
    meta: Dict[str, Any] = Field(default_factory=dict)
    sampling: GraphSamplingProvenance
    nodes: List[StoryNode]
    edges: List[StoryEdge]
    related_stories: List["StoryRelationItem"] = Field(default_factory=list)


class StoryRelationItem(BaseModel):
    story_id: int
    title: str
    relation_type: PublicStoryRelationType
    layer: str
    score: float
    reason: Optional[str] = None
    dominant_type: Optional[str] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    macro_story_id: Optional[int] = None
    pair_key: List[str] = Field(default_factory=list)
    claim: StoryDerivedClaim
    relation_semantics: StoryRelationSemantics

    @model_validator(mode="after")
    def validate_relation_semantics(self) -> "StoryRelationItem":
        if self.relation_type != self.relation_semantics.public_edge_type:
            raise ValueError("relation type does not match relation semantics")
        return self


class StoryListItem(BaseModel):
    id: int
    title: str
    event_type: str
    article_count: int
    cluster_count: int
    start_date: Optional[str] = None
    end_date: Optional[str] = None


class StoryListResponse(BaseModel):
    stories: List[StoryListItem]
    total: int


class ClusterNewsItem(BaseModel):
    news_id: int
    title: Optional[str] = None
    published_at: Optional[str] = None
    url: Optional[str] = None


class ClusterDetail(BaseModel):
    cluster_id: str
    title: Optional[str] = None
    event_type: Optional[str] = None
    initiator: Optional[str] = None
    target: Optional[str] = None
    article_count: int
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    news: List[ClusterNewsItem]


StoryGraphResponse.model_rebuild()
