"""Typed contracts for claim-level article evidence."""
from __future__ import annotations

from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, Field


class ClaimType(str, Enum):
    INFORMATION = "information"
    HYPOTHESIS = "hypothesis"
    JUDGMENT = "judgment"
    UNKNOWN = "unknown"
    INDICATOR = "indicator"


class ParagraphCitation(BaseModel):
    status: Literal["available"] = "available"
    article_id: int
    paragraph_number: int = Field(ge=1)
    anchor_id: str
    relation: Literal["input", "support", "oppose", "background"] = "input"
    matched_text: str
    excerpt: str
    source_url: Optional[str] = None


class EvidenceClaim(BaseModel):
    id: str
    claim_type: ClaimType
    text: str
    source: str
    evidence_status: Literal["available", "unavailable"]
    citations: list[ParagraphCitation] = Field(default_factory=list)
    unavailable_reason: Optional[str] = None


class EvidenceProvenance(BaseModel):
    body_status: Literal["available", "unavailable"]
    response_body_sha256: Optional[str] = None
    hash_scope: Optional[Literal["normalized-display-body"]] = None
    snapshot_status: Literal["unavailable"] = "unavailable"
    snapshot_id: None = None
    captured_at: None = None
    parser_version: None = None
    update_status: Literal["unavailable"] = "unavailable"
    correction_status: Literal["unavailable"] = "unavailable"


class ArticleEvidenceChain(BaseModel):
    schema_version: Literal["article-evidence-v1"] = "article-evidence-v1"
    article_id: int
    paragraph_count: int = Field(ge=0)
    claims: list[EvidenceClaim]
    provenance: EvidenceProvenance


__all__ = (
    "ArticleEvidenceChain",
    "ClaimType",
    "EvidenceClaim",
    "EvidenceProvenance",
    "ParagraphCitation",
)
