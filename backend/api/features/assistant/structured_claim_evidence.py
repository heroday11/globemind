"""Offline verification for content-free external structured-claim evidence."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .structured_claims import (
    MAX_STRUCTURED_CLAIM_OUTPUT_BYTES,
    MAX_STRUCTURED_CLAIMS,
    StructuredClaimError,
    compute_structured_claim_id,
    compute_structured_claim_source_inventory_sha256,
)

EXTERNAL_STRUCTURED_CLAIM_OBSERVATION_SCHEMA_VERSION = (
    "globemind.external-structured-claim-observation.v1"
)
EXTERNAL_STRUCTURED_CLAIM_RECEIPT_SCHEMA_VERSION = (
    "globemind.external-structured-claim-verification-receipt.v1"
)
MAX_EXTERNAL_CLAIM_SOURCE_BYTES = 2 * 1024 * 1024
FORBIDDEN_RELEASE_ROOT = Path("/root/data/releases/globemind")
_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_SOURCE_ID_PATTERN = r"^GM-(?:T-[0-9A-F]{16}|S\d{2})$"
_CLAIM_ID_PATTERN = r"^GM-C-[0-9A-F]{20}$"
_REASON_CODE_PATTERN = r"^[A-Z][A-Z0-9_]{2,95}$"


class ExternalStructuredClaimSourceArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    source_id: str = Field(pattern=_SOURCE_ID_PATTERN)
    artifact_locator: str = Field(min_length=1, max_length=500)
    artifact_sha256: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def locator_is_confined(self) -> "ExternalStructuredClaimSourceArtifact":
        locator = PurePosixPath(self.artifact_locator)
        if (
            locator.is_absolute()
            or not locator.parts
            or any(part in {"", ".", ".."} for part in locator.parts)
            or "\\" in self.artifact_locator
        ):
            raise ValueError("source artifact locator must be a confined POSIX path")
        return self


class ExternalStructuredClaimRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    claim_id: str = Field(pattern=_CLAIM_ID_PATTERN)
    ordinal: int = Field(ge=1, le=MAX_STRUCTURED_CLAIMS, strict=True)
    statement_sha256: str = Field(pattern=_SHA256_PATTERN)
    disposition: Literal["supported", "unknown", "non_factual"]
    citation_source_ids: list[str] = Field(default_factory=list, max_length=16)
    unknown_reason_code: str | None = Field(default=None, max_length=96)

    @model_validator(mode="after")
    def disposition_is_bounded(self) -> "ExternalStructuredClaimRecord":
        if len(set(self.citation_source_ids)) != len(self.citation_source_ids):
            raise ValueError("claim citation source IDs must be unique")
        if any(
            re.fullmatch(_SOURCE_ID_PATTERN, source_id) is None
            for source_id in self.citation_source_ids
        ):
            raise ValueError("claim citation source ID is invalid")
        if self.disposition == "supported":
            if not self.citation_source_ids or self.unknown_reason_code is not None:
                raise ValueError("supported claim requires sources and no reason code")
        elif self.disposition == "unknown":
            if self.citation_source_ids or self.unknown_reason_code is None:
                raise ValueError("unknown claim requires only a reason code")
            if re.fullmatch(
                _REASON_CODE_PATTERN,
                self.unknown_reason_code,
            ) is None:
                raise ValueError("unknown claim reason code is invalid")
        elif self.citation_source_ids or self.unknown_reason_code is not None:
            raise ValueError("non-factual claim cannot cite sources or a reason code")
        return self


class ExternalStructuredClaimObservation(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    schema_version: Literal[
        "globemind.external-structured-claim-observation.v1"
    ] = EXTERNAL_STRUCTURED_CLAIM_OBSERVATION_SCHEMA_VERSION
    candidate_id: str = Field(min_length=1, max_length=200)
    observed_at: datetime
    generation_artifact_sha256: str = Field(pattern=_SHA256_PATTERN)
    source_inventory_binding_sha256: str = Field(pattern=_SHA256_PATTERN)
    sources: list[ExternalStructuredClaimSourceArtifact] = Field(max_length=64)
    claims: list[ExternalStructuredClaimRecord] = Field(
        min_length=1,
        max_length=MAX_STRUCTURED_CLAIMS,
    )
    statement_bodies_retained: Literal[False] = False
    source_bodies_retained: Literal[False] = False

    @model_validator(mode="after")
    def inventory_is_well_formed(self) -> "ExternalStructuredClaimObservation":
        if self.observed_at.tzinfo is None or self.observed_at.utcoffset() is None:
            raise ValueError("observed_at must include a timezone")
        source_ids = [source.source_id for source in self.sources]
        if len(source_ids) != len(set(source_ids)):
            raise ValueError("source IDs must be unique")
        if [claim.ordinal for claim in self.claims] != list(
            range(1, len(self.claims) + 1)
        ):
            raise ValueError("claim ordinals must be contiguous and ordered")
        claim_ids = [claim.claim_id for claim in self.claims]
        if len(claim_ids) != len(set(claim_ids)):
            raise ValueError("claim IDs must be unique")
        if any(
            set(claim.citation_source_ids) - set(source_ids) for claim in self.claims
        ):
            raise ValueError("claim cites a source outside the observation inventory")
        return self


class VerifiedExternalClaimSource(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source_id: str
    artifact_locator: str
    artifact_sha256: str = Field(pattern=_SHA256_PATTERN)
    artifact_bytes: int = Field(gt=0, le=MAX_EXTERNAL_CLAIM_SOURCE_BYTES)


class ExternalStructuredClaimVerificationReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[
        "globemind.external-structured-claim-verification-receipt.v1"
    ] = EXTERNAL_STRUCTURED_CLAIM_RECEIPT_SCHEMA_VERSION
    evaluated_at: datetime
    candidate_id: str
    observed_at: datetime
    observation_artifact_sha256: str = Field(pattern=_SHA256_PATTERN)
    generation_artifact_sha256: str = Field(pattern=_SHA256_PATTERN)
    source_inventory_binding_sha256: str = Field(pattern=_SHA256_PATTERN)
    verified_sources: tuple[VerifiedExternalClaimSource, ...]
    claim_ids: tuple[str, ...]
    claim_count: int = Field(ge=1, le=MAX_STRUCTURED_CLAIMS)
    exact_source_artifact_hashes_verified: Literal[True] = True
    claim_id_bindings_recomputed: Literal[True] = True
    statement_bodies_retained: Literal[False] = False
    source_bodies_retained: Literal[False] = False
    structure_verification: Literal["passed"] = "passed"
    source_truth: Literal["not_verified"] = "not_verified"
    semantic_entailment: Literal["not_verified"] = "not_verified"
    release_decision: Literal["not_computable"] = "not_computable"


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for key, value in pairs:
        if key in output:
            raise StructuredClaimError("EXTERNAL_CLAIM_DUPLICATE_JSON_KEY")
        output[key] = value
    return output


def _read_exact_file(path: Path, *, maximum_bytes: int, label: str) -> bytes:
    if not path.is_absolute():
        raise StructuredClaimError("EXTERNAL_CLAIM_PATH_NOT_ABSOLUTE")
    candidate = Path(os.path.abspath(os.path.normpath(path)))
    if (
        candidate == FORBIDDEN_RELEASE_ROOT
        or FORBIDDEN_RELEASE_ROOT in candidate.parents
    ):
        raise StructuredClaimError("EXTERNAL_CLAIM_RELEASE_PATH_REJECTED")
    current = Path(candidate.anchor)
    for part in candidate.parts[1:]:
        current = current / part
        if current.is_symlink():
            raise StructuredClaimError("EXTERNAL_CLAIM_SYMLINK_REJECTED")
    try:
        descriptor = os.open(
            candidate,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
        )
    except OSError as exc:
        raise StructuredClaimError("EXTERNAL_CLAIM_ARTIFACT_UNAVAILABLE") from exc
    try:
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            before = os.fstat(descriptor)
            if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
                raise StructuredClaimError(
                    "EXTERNAL_CLAIM_ARTIFACT_NOT_SINGLE_LINK_FILE"
                )
            if before.st_size <= 0 or before.st_size > maximum_bytes:
                raise StructuredClaimError(f"EXTERNAL_CLAIM_{label}_SIZE_INVALID")
            raw = handle.read(maximum_bytes + 1)
            after = os.fstat(descriptor)
        try:
            path_after = candidate.stat()
        except OSError as exc:
            raise StructuredClaimError(
                "EXTERNAL_CLAIM_ARTIFACT_CHANGED_DURING_READ"
            ) from exc
        before_identity = (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
        )
        after_identity = (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
        )
        path_identity = (
            path_after.st_dev,
            path_after.st_ino,
            path_after.st_size,
            path_after.st_mtime_ns,
        )
        if before_identity != after_identity or after_identity != path_identity:
            raise StructuredClaimError("EXTERNAL_CLAIM_ARTIFACT_CHANGED_DURING_READ")
        if len(raw) != before.st_size or len(raw) > maximum_bytes:
            raise StructuredClaimError("EXTERNAL_CLAIM_ARTIFACT_CHANGED_DURING_READ")
        return raw
    except OSError as exc:
        raise StructuredClaimError("EXTERNAL_CLAIM_ARTIFACT_UNAVAILABLE") from exc
    finally:
        os.close(descriptor)


def verify_external_structured_claim_observation(
    path: Path,
    *,
    expected_sha256: str,
    evaluated_at: datetime,
) -> ExternalStructuredClaimVerificationReceipt:
    """Verify exact external artifacts without executing a model or retaining bodies."""

    if evaluated_at.tzinfo is None or evaluated_at.utcoffset() is None:
        raise StructuredClaimError("EXTERNAL_CLAIM_EVALUATED_AT_INVALID")
    raw = _read_exact_file(
        path,
        maximum_bytes=MAX_STRUCTURED_CLAIM_OUTPUT_BYTES,
        label="OBSERVATION",
    )
    if hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise StructuredClaimError("EXTERNAL_CLAIM_OBSERVATION_SHA256_MISMATCH")
    try:
        json.loads(raw, object_pairs_hook=_reject_duplicate_keys)
        observation = ExternalStructuredClaimObservation.model_validate_json(raw)
    except StructuredClaimError:
        raise
    except (UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise StructuredClaimError("EXTERNAL_CLAIM_OBSERVATION_SCHEMA_INVALID") from exc
    evaluated_utc = evaluated_at.astimezone(timezone.utc)
    if observation.observed_at.astimezone(timezone.utc) > evaluated_utc:
        raise StructuredClaimError("EXTERNAL_CLAIM_OBSERVATION_IN_FUTURE")

    root = path.parent.resolve(strict=True)
    bindings: dict[str, str] = {}
    verified_sources: list[VerifiedExternalClaimSource] = []
    for source in observation.sources:
        source_path = root.joinpath(*PurePosixPath(source.artifact_locator).parts)
        try:
            source_path.resolve(strict=True).relative_to(root)
        except (OSError, ValueError) as exc:
            raise StructuredClaimError("EXTERNAL_CLAIM_SOURCE_PATH_ESCAPES_ROOT") from exc
        source_raw = _read_exact_file(
            source_path,
            maximum_bytes=MAX_EXTERNAL_CLAIM_SOURCE_BYTES,
            label="SOURCE",
        )
        digest = hashlib.sha256(source_raw).hexdigest()
        if digest != source.artifact_sha256:
            raise StructuredClaimError("EXTERNAL_CLAIM_SOURCE_SHA256_MISMATCH")
        bindings[source.source_id] = digest
        verified_sources.append(
            VerifiedExternalClaimSource(
                source_id=source.source_id,
                artifact_locator=source.artifact_locator,
                artifact_sha256=digest,
                artifact_bytes=len(source_raw),
            )
        )
    inventory_sha = compute_structured_claim_source_inventory_sha256(bindings)
    if inventory_sha != observation.source_inventory_binding_sha256:
        raise StructuredClaimError("EXTERNAL_CLAIM_SOURCE_INVENTORY_SHA256_MISMATCH")
    for claim in observation.claims:
        citation_bindings = tuple(
            (source_id, bindings[source_id]) for source_id in claim.citation_source_ids
        )
        expected_claim_id = compute_structured_claim_id(
            claim.ordinal,
            claim.statement_sha256,
            citation_bindings,
        )
        if claim.claim_id != expected_claim_id:
            raise StructuredClaimError("EXTERNAL_CLAIM_ID_BINDING_MISMATCH")

    return ExternalStructuredClaimVerificationReceipt(
        evaluated_at=evaluated_utc,
        candidate_id=observation.candidate_id,
        observed_at=observation.observed_at.astimezone(timezone.utc),
        observation_artifact_sha256=expected_sha256,
        generation_artifact_sha256=observation.generation_artifact_sha256,
        source_inventory_binding_sha256=inventory_sha,
        verified_sources=tuple(verified_sources),
        claim_ids=tuple(claim.claim_id for claim in observation.claims),
        claim_count=len(observation.claims),
    )


__all__ = (
    "EXTERNAL_STRUCTURED_CLAIM_OBSERVATION_SCHEMA_VERSION",
    "EXTERNAL_STRUCTURED_CLAIM_RECEIPT_SCHEMA_VERSION",
    "ExternalStructuredClaimObservation",
    "ExternalStructuredClaimRecord",
    "ExternalStructuredClaimSourceArtifact",
    "ExternalStructuredClaimVerificationReceipt",
    "VerifiedExternalClaimSource",
    "verify_external_structured_claim_observation",
)
