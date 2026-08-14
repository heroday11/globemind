"""Strict mutation contracts for temporal entity governance."""

from __future__ import annotations

import re
from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

EntityDecision = Literal["approve", "reject"]
_ENTITY_URN = re.compile(
    r"^urn:globemind:entity:(country|person|organization|location):"
    r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"
)
_EVENT_ID = re.compile(
    r"^egv-[0-9]{10}-[0-9]{8}T[0-9]{12}Z-[0-9a-f]{16}$"
)


class StrictContract(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        str_strip_whitespace=True,
    )


class EvidenceReference(StrictContract):
    article_id: int = Field(ge=1)
    snapshot_id: str = Field(
        pattern=r"^article-[1-9][0-9]*-[0-9a-f]{64}$",
        max_length=160,
    )
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    parser_version: str = Field(
        min_length=1,
        max_length=200,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._/+:-]*$",
    )


class GovernanceMutation(StrictContract):
    expected_previous_event_id: str | None
    reason: str = Field(min_length=3, max_length=1000)
    evidence: EvidenceReference

    @field_validator("expected_previous_event_id")
    @classmethod
    def validate_expected_event_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if _EVENT_ID.fullmatch(value) is None:
            raise ValueError("expected previous event id is invalid")
        return value

    @field_validator("reason")
    @classmethod
    def validate_reason(cls, value: str) -> str:
        if any(ord(character) < 32 or ord(character) == 127 for character in value):
            raise ValueError("reason cannot contain control characters")
        return value


class TemporalMutation(GovernanceMutation):
    valid_from: str | None = None
    valid_to: str | None = None

    @field_validator("valid_from", "valid_to")
    @classmethod
    def validate_date(cls, value: str | None) -> str | None:
        if value is None:
            return None
        try:
            return date.fromisoformat(value).isoformat()
        except ValueError as exc:
            raise ValueError("validity date must be ISO-8601") from exc

    @model_validator(mode="after")
    def validate_interval(self) -> "TemporalMutation":
        if self.valid_from is not None and self.valid_to is not None:
            if self.valid_from > self.valid_to:
                raise ValueError("valid_from must not be after valid_to")
        return self


class EntityDecisionRequest(TemporalMutation):
    decision: EntityDecision


class AliasReviewRequest(TemporalMutation):
    entity_id: str = Field(
        pattern=(
            r"^urn:globemind:entity:(country|person|organization|location):"
            r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"
        )
    )
    alias: str = Field(min_length=1, max_length=300)
    language: str = Field(
        min_length=2,
        max_length=48,
        pattern=r"^(?:[A-Za-z]{2,8}(?:-[A-Za-z0-9]{1,8})*|und)$",
    )
    decision: EntityDecision
    context_dependent: bool


class RelationAddRequest(TemporalMutation):
    subject_id: str = Field(
        pattern=(
            r"^urn:globemind:entity:(country|person|organization|location):"
            r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"
        )
    )
    predicate: str = Field(
        pattern=r"^urn:globemind:predicate:[a-z0-9][a-z0-9._-]{0,95}$"
    )
    object_id: str = Field(
        pattern=(
            r"^urn:globemind:entity:(country|person|organization|location):"
            r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"
        )
    )

    @model_validator(mode="after")
    def reject_self_loop(self) -> "RelationAddRequest":
        if self.subject_id == self.object_id:
            raise ValueError("relation self-loops are forbidden")
        return self


class RelationRetractRequest(GovernanceMutation):
    pass


class MergeDecisionRequest(GovernanceMutation):
    source_entity_id: str = Field(
        pattern=(
            r"^urn:globemind:entity:(country|person|organization|location):"
            r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"
        )
    )
    target_entity_id: str = Field(
        pattern=(
            r"^urn:globemind:entity:(country|person|organization|location):"
            r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"
        )
    )

    @model_validator(mode="after")
    def reject_self_merge(self) -> "MergeDecisionRequest":
        if self.source_entity_id == self.target_entity_id:
            raise ValueError("an entity cannot be merged into itself")
        return self


class SplitDecisionRequest(GovernanceMutation):
    source_entity_id: str = Field(
        pattern=(
            r"^urn:globemind:entity:(country|person|organization|location):"
            r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"
        )
    )
    resulting_entity_ids: list[str] = Field(min_length=2, max_length=20)

    @field_validator("resulting_entity_ids")
    @classmethod
    def validate_resulting_ids(cls, values: list[str]) -> list[str]:
        if len(values) != len(set(values)):
            raise ValueError("split result identifiers must be distinct")
        for value in values:
            if _ENTITY_URN.fullmatch(value) is None:
                raise ValueError("split result entity id is invalid")
        return values

    @model_validator(mode="after")
    def reject_partial_self_split(self) -> "SplitDecisionRequest":
        if self.source_entity_id in self.resulting_entity_ids:
            raise ValueError("split results cannot include the source entity")
        return self


__all__ = (
    "AliasReviewRequest",
    "EntityDecisionRequest",
    "EvidenceReference",
    "MergeDecisionRequest",
    "RelationAddRequest",
    "RelationRetractRequest",
    "SplitDecisionRequest",
)
