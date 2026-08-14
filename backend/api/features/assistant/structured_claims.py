"""Strict structured-claim envelope for generated assistant output."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Literal, Mapping, Sequence

from pydantic import BaseModel, ConfigDict, Field, model_validator

STRUCTURED_CLAIM_SCHEMA_VERSION = "globemind.generated-claims.v1"
MAX_STRUCTURED_CLAIMS = 64
MAX_STRUCTURED_CLAIM_OUTPUT_BYTES = 96 * 1024
MAX_STRUCTURED_CLAIM_NODES = 4_096
MAX_STRUCTURED_CLAIM_DEPTH = 12

_SOURCE_ID_RE = re.compile(r"^GM-(?:T-[0-9A-F]{16}|S\d{2})$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_REASON_CODE_RE = re.compile(r"^[A-Z][A-Z0-9_]{2,95}$")
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_RAW_HTML_RE = re.compile(r"</?[A-Za-z][^>]*>")
_ACTIVE_MARKUP_RE = re.compile(
    r"<(?:script|iframe|object|embed|link|meta|img|video|audio|source|svg|style)\b|"
    r"\bon[a-z]+\s*=|\]\(\s*(?:javascript|data)\s*:",
    re.IGNORECASE,
)
_MARKDOWN_IMAGE_RE = re.compile(r"!\[[^\]\r\n]*\]")


class StructuredClaimError(RuntimeError):
    def __init__(self, reason_code: str):
        self.reason_code = reason_code
        super().__init__(reason_code)


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise StructuredClaimError("STRUCTURED_CLAIM_DUPLICATE_JSON_KEY")
        result[key] = value
    return result


def _bounded_tree(value: Any) -> None:
    nodes = 0
    stack: list[tuple[Any, int]] = [(value, 1)]
    while stack:
        current, depth = stack.pop()
        nodes += 1
        if nodes > MAX_STRUCTURED_CLAIM_NODES:
            raise StructuredClaimError("STRUCTURED_CLAIM_NODE_LIMIT_EXCEEDED")
        if depth > MAX_STRUCTURED_CLAIM_DEPTH:
            raise StructuredClaimError("STRUCTURED_CLAIM_DEPTH_LIMIT_EXCEEDED")
        if isinstance(current, Mapping):
            stack.extend((child, depth + 1) for child in current.values())
        elif isinstance(current, list):
            stack.extend((child, depth + 1) for child in current)


class GeneratedClaimInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    statement: str = Field(min_length=1, max_length=2_000)
    disposition: Literal["supported", "unknown", "non_factual"]
    citation_source_ids: list[str] = Field(default_factory=list, max_length=16)
    unknown_reason_code: str | None = Field(default=None, max_length=96)

    @model_validator(mode="after")
    def claim_is_safe_and_disposed(self) -> "GeneratedClaimInput":
        if self.statement != self.statement.strip():
            raise ValueError("claim statement must be trimmed")
        if (
            _CONTROL_RE.search(self.statement)
            or _RAW_HTML_RE.search(self.statement)
            or _ACTIVE_MARKUP_RE.search(self.statement)
            or _MARKDOWN_IMAGE_RE.search(self.statement)
        ):
            raise ValueError("claim statement contains unsafe markup")
        if len(set(self.citation_source_ids)) != len(self.citation_source_ids):
            raise ValueError("claim citation IDs must be unique")
        if any(_SOURCE_ID_RE.fullmatch(value) is None for value in self.citation_source_ids):
            raise ValueError("claim citation ID is invalid")
        if self.disposition == "supported":
            if not self.citation_source_ids or self.unknown_reason_code is not None:
                raise ValueError("supported claim requires citations and no unknown reason")
        elif self.disposition == "unknown":
            if self.citation_source_ids:
                raise ValueError("unknown claim cannot cite a source")
            if not self.unknown_reason_code or _REASON_CODE_RE.fullmatch(
                self.unknown_reason_code
            ) is None:
                raise ValueError("unknown claim requires a bounded reason code")
        elif self.citation_source_ids or self.unknown_reason_code is not None:
            raise ValueError("non-factual content cannot cite a source or reason")
        return self


class GeneratedClaimEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    schema_version: Literal["globemind.generated-claims.v1"] = (
        STRUCTURED_CLAIM_SCHEMA_VERSION
    )
    claims: list[GeneratedClaimInput] = Field(
        min_length=1,
        max_length=MAX_STRUCTURED_CLAIMS,
    )


@dataclass(frozen=True)
class StructuredClaimResult:
    content: str
    metadata: dict[str, Any]


def _claim_id(
    index: int,
    statement_sha256: str,
    citation_bindings: Sequence[tuple[str, str]],
) -> str:
    binding = json.dumps(
        {
            "schema_version": STRUCTURED_CLAIM_SCHEMA_VERSION,
            "ordinal": index,
            "statement_sha256": statement_sha256,
            "citation_source_bindings": [
                {"source_id": source_id, "binding_sha256": binding_sha256}
                for source_id, binding_sha256 in citation_bindings
            ],
        },
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"GM-C-{hashlib.sha256(binding).hexdigest()[:20].upper()}"


def compute_structured_claim_id(
    index: int,
    statement_sha256: str,
    citation_bindings: Sequence[tuple[str, str]],
) -> str:
    """Recompute the stable claim identity for an external verifier."""

    return _claim_id(index, statement_sha256, citation_bindings)


def compute_structured_claim_source_inventory_sha256(
    source_bindings: Mapping[str, str],
) -> str:
    """Bind a normalized source inventory without retaining source bodies."""

    return hashlib.sha256(
        json.dumps(
            {
                "schema_version": "globemind.generated-claim-source-bindings.v1",
                "sources": [
                    {
                        "source_id": source_id,
                        "binding_sha256": source_bindings[source_id],
                    }
                    for source_id in sorted(source_bindings)
                ],
            },
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def finalize_structured_claim_output(
    model_output: str,
    *,
    source_bindings: Mapping[str, str],
) -> StructuredClaimResult:
    """Validate explicit claim records and render safe Markdown for the client."""

    if not isinstance(model_output, str) or not model_output.strip():
        raise StructuredClaimError("STRUCTURED_CLAIM_OUTPUT_EMPTY")
    raw = model_output.encode("utf-8")
    if len(raw) > MAX_STRUCTURED_CLAIM_OUTPUT_BYTES:
        raise StructuredClaimError("STRUCTURED_CLAIM_OUTPUT_LIMIT_EXCEEDED")
    try:
        payload = json.loads(
            raw,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=lambda _value: (_ for _ in ()).throw(
                StructuredClaimError("STRUCTURED_CLAIM_NON_FINITE_JSON")
            ),
        )
        _bounded_tree(payload)
        envelope = GeneratedClaimEnvelope.model_validate(payload)
    except StructuredClaimError:
        raise
    except (UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise StructuredClaimError("STRUCTURED_CLAIM_SCHEMA_INVALID") from exc

    if not isinstance(source_bindings, Mapping):
        raise StructuredClaimError("STRUCTURED_CLAIM_SOURCE_BINDINGS_INVALID")
    normalized_bindings: dict[str, str] = {}
    for source_id, binding_sha256 in source_bindings.items():
        if (
            not isinstance(source_id, str)
            or _SOURCE_ID_RE.fullmatch(source_id) is None
            or not isinstance(binding_sha256, str)
            or _SHA256_RE.fullmatch(binding_sha256) is None
            or source_id in normalized_bindings
        ):
            raise StructuredClaimError("STRUCTURED_CLAIM_SOURCE_BINDINGS_INVALID")
        normalized_bindings[source_id] = binding_sha256
    allowed = set(normalized_bindings)
    source_inventory_binding_sha256 = (
        compute_structured_claim_source_inventory_sha256(normalized_bindings)
    )
    records: list[dict[str, Any]] = []
    rendered: list[str] = []
    for index, claim in enumerate(envelope.claims, 1):
        out_of_scope = sorted(set(claim.citation_source_ids) - allowed)
        if out_of_scope:
            raise StructuredClaimError("STRUCTURED_CLAIM_SOURCE_ID_OUT_OF_SCOPE")
        statement_sha256 = hashlib.sha256(claim.statement.encode("utf-8")).hexdigest()
        citation_bindings = tuple(
            (source_id, normalized_bindings[source_id])
            for source_id in claim.citation_source_ids
        )
        claim_id = compute_structured_claim_id(
            index,
            statement_sha256,
            citation_bindings,
        )
        markers = (
            " ".join(f"[{source_id}]" for source_id in claim.citation_source_ids)
            if claim.disposition == "supported"
            else "[GM-UNKNOWN]"
            if claim.disposition == "unknown"
            else ""
        )
        rendered.append(f"- {claim.statement}{f' {markers}' if markers else ''}")
        records.append(
            {
                "claim_id": claim_id,
                "ordinal": index,
                "statement_sha256": statement_sha256,
                "disposition": claim.disposition,
                "citation_source_ids": list(claim.citation_source_ids),
                "citation_source_bindings": [
                    {"source_id": source_id, "binding_sha256": binding_sha256}
                    for source_id, binding_sha256 in citation_bindings
                ],
                "unknown_reason_code": claim.unknown_reason_code,
                "source_truth": (
                    "not_applicable"
                    if claim.disposition == "non_factual"
                    else "not_verified"
                ),
                "semantic_entailment": (
                    "not_applicable"
                    if claim.disposition == "non_factual"
                    else "not_verified"
                ),
                "fact_check": (
                    "not_applicable"
                    if claim.disposition == "non_factual"
                    else "not_performed"
                ),
            }
        )
    return StructuredClaimResult(
        content="\n".join(rendered),
        metadata={
            "claim_ids": [record["claim_id"] for record in records],
            "claim_partition_state": "model_declared_not_semantically_verified",
            "structured_claim_records": "available",
            "per_claim_unknown_state": "available",
            "claim_id_reason_code": None,
            "claims": records,
            "claim_count": len(records),
            "source_inventory_binding_sha256": source_inventory_binding_sha256,
            "claim_id_binding_scope": (
                "ordinal_statement_sha256_and_exact_source_artifact_sha256"
            ),
            "claim_statement_bodies_retained_in_assurance": False,
        },
    )


__all__ = (
    "GeneratedClaimEnvelope",
    "GeneratedClaimInput",
    "MAX_STRUCTURED_CLAIMS",
    "STRUCTURED_CLAIM_SCHEMA_VERSION",
    "StructuredClaimError",
    "StructuredClaimResult",
    "compute_structured_claim_id",
    "compute_structured_claim_source_inventory_sha256",
    "finalize_structured_claim_output",
)
