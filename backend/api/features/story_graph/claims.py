"""Content-free identifiers and fail-closed assurance for graph relations."""

from __future__ import annotations

import hashlib
import json
from typing import Literal

from api.features.story_graph.contracts import StoryDerivedClaim

StoryRelationDerivation = Literal[
    "stored_derived_relation",
    "computed_bridge",
    "layout_sequence",
]

_REASONS = {
    "stored_derived_relation": "GRAPH_RELATION_SOURCE_LOCATOR_UNAVAILABLE",
    "computed_bridge": "GRAPH_LAYOUT_EDGE_NOT_EVIDENCE",
    "layout_sequence": "GRAPH_LAYOUT_EDGE_NOT_EVIDENCE",
}


def _bounded_identity(value: object, *, field: str, maximum: int = 256) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    normalized = value.strip()
    if (
        not normalized
        or len(normalized) > maximum
        or any(ord(character) < 32 or ord(character) == 127 for character in normalized)
    ):
        raise ValueError(f"{field} is invalid or exceeds its length limit")
    return normalized


def build_unavailable_story_relation_claim(
    *,
    graph_scope_id: str,
    from_id: str,
    to_id: str,
    relation_kind: str,
    derivation: StoryRelationDerivation,
) -> StoryDerivedClaim:
    """Build a stable ID without treating labels, URLs, or layout as evidence."""

    if derivation not in _REASONS:
        raise ValueError("derivation is unsupported")
    identity = {
        "schema": "globemind.story-derived-claim.v1",
        "graph_scope_id": _bounded_identity(graph_scope_id, field="graph_scope_id"),
        "from_id": _bounded_identity(from_id, field="from_id"),
        "to_id": _bounded_identity(to_id, field="to_id"),
        "relation_kind": _bounded_identity(
            relation_kind,
            field="relation_kind",
            maximum=96,
        ),
        "derivation": derivation,
    }
    canonical = json.dumps(
        identity,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")
    claim_id = f"sgc_{hashlib.sha256(canonical).hexdigest()}"
    return StoryDerivedClaim(
        claim_id=claim_id,
        reason_code=_REASONS[derivation],
    )


__all__ = (
    "StoryRelationDerivation",
    "build_unavailable_story_relation_claim",
)
