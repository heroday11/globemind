"""Contracts and application logic for the fail-closed governance catalog.

The catalog deliberately separates technical observability from formal research
readiness.  A readable relation or a fresh timestamp is useful evidence, but it
does not make a record release-eligible when ownership, versioning, coverage,
licensing, quality, provenance, or schema governance remains unknown.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, FiniteFloat, ValidationError
from sqlalchemy.orm import Session

from api.core.environment import string_setting
from api.features import FeatureHealthCheck, probe_postgres_relations
from api.features.authoritative_data import connector_descriptors
from api.features.ground_news import probe_ground_news_health
from api.features.model_assurance import (
    AssuranceStoreUnavailable,
    ModelAssuranceService,
    ModelAssuranceStore,
    StoredEvaluation,
)
from api.features.opinion import (
    METHOD_VERSION,
    OPINION_MODEL_VERSION,
    probe_opinion_health,
)
from api.features.story_graph import (
    STORY_GRAPH_HEALTH_RELATIONS,
    probe_story_graph_health,
)

DATA_CATALOG_SCHEMA_VERSION = "data-governance-catalog-v1"
DATA_CATALOG_CONTRACT_VERSION = "1.0.0"
NEWS_QUALITY_PROFILE_PROJECTION_SCHEMA_VERSION = (
    "news-quality-profile-catalog-projection-v1"
)
NEWS_QUALITY_CATALOG_SCOPE_ID = (
    "dataset.news_articles.bounded-offline-profile.v1"
)
NEWS_QUALITY_PROFILE_SCHEMA_VERSION = "news-quality-profile-v3"
NEWS_QUALITY_PROFILE_METHOD_VERSION = "news-ingest-quality-profile-v3"
NEWS_QUALITY_NEAR_DUPLICATE_METHOD_VERSION = (
    "bounded-char5-bottom32-rolling64-lsh8-v1"
)
NEWS_QUALITY_PROFILE_MAX_AGE = timedelta(days=7)
_NEWS_QUALITY_MAX_CANONICAL_BYTES = 1_048_576
_NEWS_QUALITY_MAX_JSON_NODES = 50_000
_NEWS_QUALITY_MAX_JSON_DEPTH = 20
_NEWS_QUALITY_MAX_STRING_CHARS = 4_096
_NEWS_QUALITY_MAX_ROWS = 100_000
_NEWS_QUALITY_MAX_NEAR_ROWS = 20_000
_NEWS_QUALITY_MAX_COMPARISONS = 1_000_000
_NEWS_QUALITY_MAX_SLICE_VALUES = 64
_NEWS_QUALITY_MAX_BUCKET_ROWS = 64
_NEWS_QUALITY_NEAR_KEYS_PER_ROW = 8
_NEWS_QUALITY_REASON_CODES = frozenset(
    {
        "empty_title",
        "missing_url",
        "missing_body",
        "body_too_short",
        "page_like_title",
        "invalid_url",
        "page_like_url",
        "placeholder_body",
        "missing_published_at",
        "published_before_min_year",
        "published_future_too_far",
    }
)

PROJECT_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_OWNER_REGISTRY = PROJECT_ROOT / "ops" / "features" / "registry.json"
DEFAULT_SOURCE_CATALOG = (
    PROJECT_ROOT / "data" / "source_curation" / "full_source_catalog.csv"
)
_PUBLIC_OWNER_REGISTRY_REF = "ops/features/registry.json"
_PUBLIC_SOURCE_CATALOG_REF = "data/source_curation/full_source_catalog.csv"
_TIMESTAMP_METRIC = re.compile(r"(?:_at|_date)$")
_OPINION_ASSURANCE_MODEL_ID = "model.china_opinion_stance"
_MODEL_ASSURANCE_EVIDENCE_REF = "backend/api/features/model_assurance/__init__.py"

CatalogKind = Literal["dataset", "source", "model"]
EvidenceStatus = Literal["verified", "declared", "partial", "unknown"]


class CatalogEvidence(BaseModel):
    reference: str = Field(min_length=1, max_length=240)
    claim: str = Field(min_length=1, max_length=500)
    status: EvidenceStatus


class OwnerRegistration(BaseModel):
    owner_id: str | None = Field(default=None, max_length=128)
    display_name: str | None = Field(default=None, max_length=240)
    assignment_status: Literal["named", "role_only", "unknown"] = "unknown"
    evidence: list[CatalogEvidence] = Field(default_factory=list)


class VersionRegistration(BaseModel):
    value: str | None = Field(default=None, max_length=300)
    status: Literal["verified", "declared", "unknown"] = "unknown"
    scheme: str | None = Field(default=None, max_length=120)
    effective_at: datetime | None = None
    change_log_ref: str | None = Field(default=None, max_length=240)
    evidence: list[CatalogEvidence] = Field(default_factory=list)


class OperationalRegistration(BaseModel):
    state: Literal["available", "degraded", "offline", "unknown"] = "unknown"
    evidence_status: Literal["verified", "unknown"] = "unknown"
    observed_at: datetime
    source: str | None = Field(default=None, max_length=160)
    reason_codes: list[str] = Field(default_factory=list)


class FreshnessRegistration(BaseModel):
    state: Literal["live", "delayed", "stale", "offline"] = "offline"
    evidence_status: Literal["verified", "unknown"] = "unknown"
    cutoff_at: datetime | None = None
    last_success_at: datetime | None = None
    observed_at: datetime
    lag_hours: FiniteFloat | None = Field(default=None, ge=0)
    sla_hours: FiniteFloat | None = Field(default=None, gt=0)
    source: str | None = Field(default=None, max_length=160)
    reason_codes: list[str] = Field(default_factory=list)


CatalogMetric = str | int | FiniteFloat | bool


class CoverageRegistration(BaseModel):
    status: Literal["verified", "partial", "unknown"] = "unknown"
    scope: str | None = Field(default=None, max_length=500)
    metrics: dict[str, CatalogMetric] = Field(default_factory=dict)
    missing_dimensions: list[str] = Field(default_factory=list)
    evidence: list[CatalogEvidence] = Field(default_factory=list)


class LicenseRegistration(BaseModel):
    status: Literal["verified", "restricted", "unknown"] = "unknown"
    identifier: str | None = Field(default=None, max_length=240)
    usage_scope: str | None = Field(default=None, max_length=500)
    terms_ref: str | None = Field(default=None, max_length=500)
    retention_policy: str | None = Field(default=None, max_length=500)
    evidence: list[CatalogEvidence] = Field(default_factory=list)


class QualityRegistration(BaseModel):
    status: Literal["passed", "degraded", "failed", "unknown"] = "unknown"
    evaluated_at: datetime | None = None
    evaluation_version: str | None = Field(default=None, max_length=240)
    metrics: dict[str, CatalogMetric] = Field(default_factory=dict)
    known_issues: list[str] = Field(default_factory=list)
    evidence: list[CatalogEvidence] = Field(default_factory=list)


class NewsQualityProfileProjectionEnvelope(BaseModel):
    """Explicit, offline-only binding for a bounded quality-profile artifact."""

    model_config = ConfigDict(extra="forbid", strict=True)

    schema_version: Literal["news-quality-profile-catalog-projection-v1"]
    target_record_id: str = Field(min_length=1, max_length=128)
    scope_id: str = Field(min_length=1, max_length=160)
    profile_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    profile: dict[str, Any]


class NewsQualityCatalogProjection(BaseModel):
    """Fail-closed catalog fields derived from one validated profile."""

    state: Literal["mechanically_validated", "unavailable"]
    reason_codes: list[str] = Field(default_factory=list)
    quality: QualityRegistration
    coverage: CoverageRegistration


class ProvenanceRegistration(BaseModel):
    status: Literal["verified", "partial", "unknown"] = "unknown"
    capture_timestamp_status: EvidenceStatus = "unknown"
    web_snapshot_status: EvidenceStatus = "unknown"
    content_hash_status: EvidenceStatus = "unknown"
    parser_version: str | None = Field(default=None, max_length=240)
    revision_tracking_status: EvidenceStatus = "unknown"
    evidence: list[CatalogEvidence] = Field(default_factory=list)


class SchemaRegistration(BaseModel):
    status: Literal["verified", "partial", "unknown"] = "unknown"
    record_identifier: str | None = Field(default=None, max_length=240)
    schema_ref: str | None = Field(default=None, max_length=240)
    data_dictionary_ref: str | None = Field(default=None, max_length=240)
    mapping_refs: list[str] = Field(default_factory=list)
    change_log_ref: str | None = Field(default=None, max_length=240)
    evidence: list[CatalogEvidence] = Field(default_factory=list)


class CatalogRecordDraft(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    record_id: str = Field(pattern=r"^(dataset|source|model)\.[a-z0-9][a-z0-9_.-]*$")
    kind: CatalogKind
    title: str = Field(min_length=1, max_length=240)
    description: str = Field(min_length=1, max_length=1000)
    owner: OwnerRegistration
    version: VersionRegistration
    operational: OperationalRegistration
    freshness: FreshnessRegistration
    coverage: CoverageRegistration
    license: LicenseRegistration
    quality: QualityRegistration
    provenance: ProvenanceRegistration
    schema_registration: SchemaRegistration = Field(
        validation_alias="schema",
        serialization_alias="schema",
    )
    evidence: list[CatalogEvidence] = Field(default_factory=list)


class RegistrationStatus(BaseModel):
    state: Literal["eligible", "blocked"]
    release_eligible: bool
    research_ready: bool
    reason_codes: list[str] = Field(default_factory=list)
    evaluated_at: datetime


class CatalogRecord(CatalogRecordDraft):
    status: RegistrationStatus


class CatalogSummary(BaseModel):
    record_count: int = Field(ge=0)
    dataset_count: int = Field(ge=0)
    source_count: int = Field(ge=0)
    model_count: int = Field(ge=0)
    eligible_count: int = Field(ge=0)
    blocked_count: int = Field(ge=0)
    formal_release_status: Literal["ready", "blocked"]
    blocker_counts: dict[str, int] = Field(default_factory=dict)


class RegistrySourceStatus(BaseModel):
    owner_registry: Literal["verified", "unavailable"]
    source_catalog: Literal["verified", "unavailable"]
    references: list[str] = Field(default_factory=list)


class DataCatalogResponse(BaseModel):
    schema_version: Literal["data-governance-catalog-v1"] = (
        DATA_CATALOG_SCHEMA_VERSION
    )
    contract_version: Literal["1.0.0"] = DATA_CATALOG_CONTRACT_VERSION
    available: bool
    generated_at: datetime
    catalog_status: Literal["ready", "incomplete", "unavailable"]
    registry_sources: RegistrySourceStatus
    summary: CatalogSummary
    records: list[CatalogRecord]
    reason_codes: list[str] = Field(default_factory=list)


def _utc_now(value: datetime | None = None) -> datetime:
    current = value or datetime.now(timezone.utc)
    if current.tzinfo is None:
        return current.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc)


def _parse_timestamp(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return _utc_now(value)
    if not isinstance(value, str) or not value.strip():
        return None
    normalized = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    return _utc_now(parsed)


def _latest_metric_timestamp(metrics: Mapping[str, Any]) -> datetime | None:
    timestamps = [
        parsed
        for key, value in metrics.items()
        if _TIMESTAMP_METRIC.search(str(key))
        if (parsed := _parse_timestamp(value)) is not None
    ]
    return max(timestamps) if timestamps else None


def freshness_from_health(
    check: FeatureHealthCheck | None,
    *,
    observed_at: datetime,
) -> FreshnessRegistration:
    """Normalize feature health without interpreting missing evidence as healthy."""

    now = _utc_now(observed_at)
    if check is None:
        return FreshnessRegistration(
            state="offline",
            evidence_status="unknown",
            observed_at=now,
            reason_codes=["FRESHNESS_EVIDENCE_MISSING", "LAST_SUCCESS_UNKNOWN"],
        )
    metrics = check.metrics if isinstance(check.metrics, Mapping) else {}
    backend_state = str(metrics.get("freshness_status") or "").lower()
    cutoff = _latest_metric_timestamp(metrics)
    lag = metrics.get("freshness_lag_hours")
    sla = metrics.get("freshness_sla_hours")
    lag_hours = float(lag) if isinstance(lag, (int, float)) and not isinstance(lag, bool) else None
    sla_hours = float(sla) if isinstance(sla, (int, float)) and not isinstance(sla, bool) else None
    reasons = ["LAST_SUCCESS_UNKNOWN"]
    evidence_status: Literal["verified", "unknown"] = "verified"
    if check.status == "down":
        state = "offline"
        evidence_status = "unknown"
        reasons.append("CAPABILITY_OFFLINE")
    elif backend_state == "missing":
        state = "offline"
        evidence_status = "unknown"
        reasons.append("CUTOFF_UNKNOWN")
    elif backend_state == "stale" or check.status == "stale":
        state = "stale"
        reasons.append("FRESHNESS_SLA_EXCEEDED")
    elif backend_state == "current" and cutoff is not None:
        state = "live"
    elif check.status == "degraded":
        state = "delayed"
        reasons.append("FRESHNESS_STATE_UNVERIFIED")
    else:
        state = "offline"
        evidence_status = "unknown"
        reasons.append("FRESHNESS_STATE_UNVERIFIED")
    if cutoff is None and "CUTOFF_UNKNOWN" not in reasons:
        reasons.append("CUTOFF_UNKNOWN")
        if state == "live":
            state = "offline"
            evidence_status = "unknown"
    return FreshnessRegistration(
        state=state,
        evidence_status=evidence_status,
        cutoff_at=cutoff,
        last_success_at=None,
        observed_at=now,
        lag_hours=lag_hours,
        sla_hours=sla_hours,
        source=f"feature-health:{check.feature_id}",
        reason_codes=reasons,
    )


def operational_from_health(
    check: FeatureHealthCheck | None,
    *,
    observed_at: datetime,
) -> OperationalRegistration:
    """Project technical availability without claiming business freshness."""

    now = _utc_now(observed_at)
    if check is None:
        return OperationalRegistration(
            state="unknown",
            evidence_status="unknown",
            observed_at=now,
            reason_codes=["OPERATIONAL_EVIDENCE_MISSING"],
        )
    state = {
        "up": "available",
        "stale": "available",
        "degraded": "degraded",
        "down": "offline",
    }[check.status]
    reasons: list[str] = []
    if state == "degraded":
        reasons.append("OPERATIONAL_DEGRADED")
    elif state == "offline":
        reasons.append("OPERATIONAL_OFFLINE")
    return OperationalRegistration(
        state=state,
        evidence_status="verified",
        observed_at=now,
        source=f"feature-health:{check.feature_id}",
        reason_codes=reasons,
    )


def load_owner_roles(path: Path = DEFAULT_OWNER_REGISTRY) -> tuple[dict[str, str], bool]:
    """Load accountability roles; malformed files become unavailable, never trusted."""

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        owners = payload.get("owners")
        if not isinstance(owners, list):
            raise ValueError("owners missing")
        result: dict[str, str] = {}
        for item in owners:
            if not isinstance(item, Mapping):
                raise ValueError("owner entry invalid")
            owner_id = item.get("id")
            name = item.get("name")
            if not isinstance(owner_id, str) or not owner_id or owner_id in result:
                raise ValueError("owner id invalid")
            if not isinstance(name, str) or not name:
                raise ValueError("owner name invalid")
            result[owner_id] = name
        return result, True
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return {}, False


def inspect_source_catalog(path: Path = DEFAULT_SOURCE_CATALOG) -> tuple[dict[str, Any], bool]:
    """Return aggregate, non-sensitive evidence from the existing source catalog."""

    try:
        raw = path.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            required = {"url", "domain", "region", "source_type", "classification_basis"}
            if not required.issubset(set(reader.fieldnames or ())):
                raise ValueError("source catalog columns unavailable")
            rows = list(reader)
        domains = [str(row.get("domain") or "").strip().lower() for row in rows]
        urls = [str(row.get("url") or "").strip() for row in rows]
        regions = Counter(str(row.get("region") or "unknown").strip() or "unknown" for row in rows)
        source_types = Counter(
            str(row.get("source_type") or "unknown").strip() or "unknown"
            for row in rows
        )
        bases = Counter(
            str(row.get("classification_basis") or "unknown").strip() or "unknown"
            for row in rows
        )
        non_empty_domains = [value for value in domains if value]
        return {
            "sha256": digest,
            "registered_rows": len(rows),
            "unique_domains": len(set(non_empty_domains)),
            "duplicate_domain_rows": len(non_empty_domains) - len(set(non_empty_domains)),
            "rows_missing_domain": sum(not value for value in domains),
            "rows_missing_url": sum(not value for value in urls),
            "region_values": len(regions),
            "source_type_values": len(source_types),
            "classification_basis_values": len(bases),
        }, True
    except (OSError, UnicodeError, csv.Error, TypeError, ValueError):
        return {}, False


def _evidence(reference: str, claim: str, status: EvidenceStatus) -> CatalogEvidence:
    return CatalogEvidence(reference=reference, claim=claim, status=status)


def _owner(owner_id: str, roles: Mapping[str, str]) -> OwnerRegistration:
    name = roles.get(owner_id)
    if name is None:
        return OwnerRegistration()
    return OwnerRegistration(
        owner_id=owner_id,
        display_name=name,
        assignment_status="role_only",
        evidence=[
            _evidence(
                _PUBLIC_OWNER_REGISTRY_REF,
                "Accountability role is registered; no named person is asserted.",
                "verified",
            )
        ],
    )


def _unknown_coverage(*missing: str) -> CoverageRegistration:
    return CoverageRegistration(status="unknown", missing_dimensions=list(missing))


def _unknown_license() -> LicenseRegistration:
    return LicenseRegistration(status="unknown")


def _unknown_quality(*issues: str) -> QualityRegistration:
    return QualityRegistration(status="unknown", known_issues=list(issues))


def _unavailable_news_quality_projection(
    reason_code: str,
) -> NewsQualityCatalogProjection:
    """Return no derived values when any projection precondition is absent."""

    return NewsQualityCatalogProjection(
        state="unavailable",
        reason_codes=[reason_code],
        quality=QualityRegistration(
            status="unknown",
            metrics={},
            known_issues=[reason_code],
        ),
        coverage=CoverageRegistration(
            status="unknown",
            metrics={},
            missing_dimensions=[
                "country_coverage",
                "language_coverage",
                "completeness_rate",
                "missing_rate",
                "duplicate_rate",
                "full_dataset_coverage",
                "factual_accuracy",
                "source_reliability",
                "approved_quality_thresholds",
                "near_duplicate_human_review",
            ],
        ),
    )


def _bounded_canonical_json_bytes(value: Any) -> bytes:
    """Canonicalize a JSON value only after bounded, cycle-safe inspection."""

    stack: list[tuple[Any, int]] = [(value, 0)]
    seen_containers: set[int] = set()
    nodes = 0
    while stack:
        current, depth = stack.pop()
        nodes += 1
        if nodes > _NEWS_QUALITY_MAX_JSON_NODES:
            raise ValueError("JSON node bound exceeded")
        if depth > _NEWS_QUALITY_MAX_JSON_DEPTH:
            raise ValueError("JSON depth bound exceeded")
        if isinstance(current, dict):
            identity = id(current)
            if identity in seen_containers:
                raise ValueError("cyclic or aliased JSON container")
            seen_containers.add(identity)
            for key, item in current.items():
                if type(key) is not str or not key or len(key) > 160:
                    raise ValueError("JSON object key is invalid")
                stack.append((item, depth + 1))
            continue
        if isinstance(current, list):
            identity = id(current)
            if identity in seen_containers:
                raise ValueError("cyclic or aliased JSON container")
            seen_containers.add(identity)
            stack.extend((item, depth + 1) for item in current)
            continue
        if isinstance(current, str):
            if len(current) > _NEWS_QUALITY_MAX_STRING_CHARS:
                raise ValueError("JSON string bound exceeded")
            continue
        if current is None or type(current) is bool:
            continue
        if type(current) is int:
            if abs(current) > 9_007_199_254_740_991:
                raise ValueError("JSON integer is outside the interoperable range")
            continue
        if type(current) is float and math.isfinite(current):
            continue
        raise ValueError("value is not bounded JSON")

    encoded = json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    if len(encoded) > _NEWS_QUALITY_MAX_CANONICAL_BYTES:
        raise ValueError("canonical JSON byte bound exceeded")
    return encoded


def _strict_profile_timestamp(value: Any) -> datetime:
    if type(value) is not str or not value or len(value) > 64:
        raise ValueError("timestamp must be a bounded string")
    normalized = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError("timestamp is invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp must include an offset")
    return parsed.astimezone(timezone.utc)


def _profile_ratio(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        raise ValueError("ratio denominator must be positive")
    return round(numerator / denominator, 6)


def _exact_profile_object(
    value: Any,
    *,
    keys: set[str],
    label: str,
) -> dict[str, Any]:
    if type(value) is not dict or set(value) != keys:
        raise ValueError(f"{label} shape is invalid")
    return value


def _profile_integer(
    value: Any,
    *,
    label: str,
    minimum: int = 0,
    maximum: int | None = None,
) -> int:
    if type(value) is not int or value < minimum:
        raise ValueError(f"{label} is invalid")
    if maximum is not None and value > maximum:
        raise ValueError(f"{label} exceeds its bound")
    return value


def _profile_expected_integer(
    value: Any,
    *,
    label: str,
    expected: int,
) -> int:
    numeric = _profile_integer(
        value,
        label=label,
        minimum=expected,
        maximum=expected,
    )
    return numeric


def _profile_rate(value: Any, *, label: str) -> float:
    if type(value) not in (int, float):
        raise ValueError(f"{label} is not numeric")
    numeric = float(value)
    if not math.isfinite(numeric) or not 0.0 <= numeric <= 1.0:
        raise ValueError(f"{label} is outside zero to one")
    return numeric


def _validate_duplicate_summary(
    value: Any,
    *,
    label: str,
    maximum_rows: int,
) -> dict[str, int]:
    summary = _exact_profile_object(
        value,
        keys={"duplicate_groups", "duplicate_rows", "excess_rows"},
        label=label,
    )
    groups = _profile_integer(
        summary["duplicate_groups"], label=f"{label}.duplicate_groups"
    )
    rows = _profile_integer(
        summary["duplicate_rows"],
        label=f"{label}.duplicate_rows",
        maximum=maximum_rows,
    )
    excess = _profile_integer(
        summary["excess_rows"],
        label=f"{label}.excess_rows",
        maximum=maximum_rows,
    )
    if rows != groups + excess or not groups <= excess <= rows:
        raise ValueError(f"{label} counts are inconsistent")
    return {"duplicate_groups": groups, "duplicate_rows": rows, "excess_rows": excess}


def _validate_profile_slice(
    value: Any,
    *,
    dimension: Literal["source_domain", "language", "publication_month"],
    expected_rows: int,
    total_good: int,
    total_bad: int,
) -> dict[str, Any]:
    expected_policies = {
        "source_domain": "public_dns_hostname_only",
        "language": "normalized_bcp47_or_und_or_invalid",
        "publication_month": "valid_publication_time_utc_month",
    }
    summary = _exact_profile_object(
        value,
        keys={
            "items",
            "distinct_values",
            "overflow_values",
            "overflow_rows",
            "value_policy",
        },
        label=f"slices.{dimension}",
    )
    if summary["value_policy"] != expected_policies[dimension]:
        raise ValueError(f"slices.{dimension} policy is incompatible")
    items = summary["items"]
    if type(items) is not list or len(items) > _NEWS_QUALITY_MAX_SLICE_VALUES:
        raise ValueError(f"slices.{dimension}.items is invalid")
    distinct_values = _profile_integer(
        summary["distinct_values"], label=f"slices.{dimension}.distinct_values"
    )
    overflow_values = _profile_integer(
        summary["overflow_values"], label=f"slices.{dimension}.overflow_values"
    )
    overflow_rows = _profile_integer(
        summary["overflow_rows"],
        label=f"slices.{dimension}.overflow_rows",
        maximum=expected_rows,
    )
    if distinct_values != len(items) + overflow_values:
        raise ValueError(f"slices.{dimension} distinct count is inconsistent")
    if overflow_values == 0 and overflow_rows != 0:
        raise ValueError(f"slices.{dimension} overflow is inconsistent")

    values: set[str] = set()
    item_rows = 0
    item_good = 0
    item_bad = 0
    ordering: list[tuple[int, str]] = []
    for index, raw_item in enumerate(items):
        item = _exact_profile_object(
            raw_item,
            keys={"value", "evaluated_rows", "good_count", "bad_count", "bad_rate"},
            label=f"slices.{dimension}.items[{index}]",
        )
        raw_value = item["value"]
        if type(raw_value) is not str or not raw_value or len(raw_value) > 253:
            raise ValueError(f"slices.{dimension} value is invalid")
        if raw_value in values:
            raise ValueError(f"slices.{dimension} values are not unique")
        if dimension == "source_domain" and (
            raw_value != raw_value.lower()
            or "." not in raw_value
            or not re.fullmatch(r"[a-z0-9.-]+", raw_value)
        ):
            raise ValueError("source-domain slice value is invalid")
        if dimension == "language" and raw_value not in {"und", "invalid"} and not re.fullmatch(
            r"[a-z]{2,3}(?:-[a-z0-9]{2,8}){0,3}", raw_value
        ):
            raise ValueError("language slice value is invalid")
        if dimension == "publication_month" and not re.fullmatch(
            r"20\d{2}-(?:0[1-9]|1[0-2])", raw_value
        ):
            raise ValueError("publication-month slice value is invalid")
        values.add(raw_value)
        evaluated = _profile_integer(
            item["evaluated_rows"],
            label=f"slices.{dimension}.evaluated_rows",
            minimum=1,
            maximum=expected_rows,
        )
        good = _profile_integer(
            item["good_count"],
            label=f"slices.{dimension}.good_count",
            maximum=evaluated,
        )
        bad = _profile_integer(
            item["bad_count"],
            label=f"slices.{dimension}.bad_count",
            maximum=evaluated,
        )
        if good + bad != evaluated:
            raise ValueError(f"slices.{dimension} label counts are inconsistent")
        rate = _profile_rate(
            item["bad_rate"], label=f"slices.{dimension}.bad_rate"
        )
        if rate != _profile_ratio(bad, evaluated):
            raise ValueError(f"slices.{dimension} bad rate is inconsistent")
        ordering.append((-evaluated, raw_value))
        item_rows += evaluated
        item_good += good
        item_bad += bad
    if ordering != sorted(ordering):
        raise ValueError(f"slices.{dimension} ordering is non-deterministic")
    if item_rows + overflow_rows != expected_rows:
        raise ValueError(f"slices.{dimension} row count is inconsistent")
    if item_good > total_good or item_bad > total_bad:
        raise ValueError(f"slices.{dimension} labels exceed the profile totals")
    return summary


def _validated_news_quality_profile_summary(
    profile: dict[str, Any],
) -> dict[str, CatalogMetric | datetime]:
    """Validate the entire v3 aggregate contract before selecting safe fields."""

    top = _exact_profile_object(
        profile,
        keys={
            "schema_version",
            "method_version",
            "generated_at",
            "scope",
            "labels",
            "reason_counts",
            "completeness",
            "exact_duplicates",
            "near_duplicate_candidates",
            "source_coverage",
            "slices",
            "schema_observation",
            "publication_time",
            "assurance",
        },
        label="profile",
    )
    if top["schema_version"] != NEWS_QUALITY_PROFILE_SCHEMA_VERSION:
        raise ValueError("profile schema is incompatible")
    if top["method_version"] != NEWS_QUALITY_PROFILE_METHOD_VERSION:
        raise ValueError("profile method is incompatible")
    generated_at = _strict_profile_timestamp(top["generated_at"])

    scope = _exact_profile_object(
        top["scope"],
        keys={
            "evaluated_rows",
            "max_rows",
            "truncated",
            "article_content_retained",
            "row_identifiers_retained",
        },
        label="scope",
    )
    total = _profile_integer(
        scope["evaluated_rows"],
        label="scope.evaluated_rows",
        minimum=1,
        maximum=_NEWS_QUALITY_MAX_ROWS,
    )
    max_rows = _profile_integer(
        scope["max_rows"],
        label="scope.max_rows",
        minimum=1,
        maximum=_NEWS_QUALITY_MAX_ROWS,
    )
    if total > max_rows or scope["truncated"] is not False:
        raise ValueError("profile scope is incomplete")
    if scope["article_content_retained"] is not False:
        raise ValueError("profile retained article content")
    if scope["row_identifiers_retained"] is not False:
        raise ValueError("profile retained row identifiers")

    labels = _exact_profile_object(
        top["labels"],
        keys={
            "good_count",
            "bad_count",
            "good_rate",
            "bad_rate",
            "rule_set",
            "gold_standard_state",
        },
        label="labels",
    )
    good = _profile_integer(
        labels["good_count"], label="labels.good_count", maximum=total
    )
    bad = _profile_integer(
        labels["bad_count"], label="labels.bad_count", maximum=total
    )
    if good + bad != total:
        raise ValueError("profile label counts are inconsistent")
    good_rate = _profile_rate(labels["good_rate"], label="labels.good_rate")
    bad_rate = _profile_rate(labels["bad_rate"], label="labels.bad_rate")
    if good_rate != _profile_ratio(good, total) or bad_rate != _profile_ratio(
        bad, total
    ):
        raise ValueError("profile label rates are inconsistent")
    if labels["rule_set"] != "deterministic_heuristics":
        raise ValueError("profile rule set is incompatible")
    if labels["gold_standard_state"] != "not_provided":
        raise ValueError("profile cannot self-assert a gold standard")

    reason_items = top["reason_counts"]
    if type(reason_items) is not list or len(reason_items) > len(
        _NEWS_QUALITY_REASON_CODES
    ):
        raise ValueError("profile reason counts are invalid")
    reasons: dict[str, int] = {}
    ordered_codes: list[str] = []
    for index, raw_item in enumerate(reason_items):
        item = _exact_profile_object(
            raw_item,
            keys={"code", "count", "rate"},
            label=f"reason_counts[{index}]",
        )
        code = item["code"]
        if type(code) is not str or code not in _NEWS_QUALITY_REASON_CODES or code in reasons:
            raise ValueError("profile reason code is invalid")
        count = _profile_integer(
            item["count"],
            label=f"reason_counts.{code}.count",
            minimum=1,
            maximum=total,
        )
        rate = _profile_rate(item["rate"], label=f"reason_counts.{code}.rate")
        if rate != _profile_ratio(count, total):
            raise ValueError("profile reason rate is inconsistent")
        reasons[code] = count
        ordered_codes.append(code)
    if ordered_codes != sorted(ordered_codes):
        raise ValueError("profile reason ordering is non-deterministic")
    if bad and not reasons:
        raise ValueError("bad rows have no mechanical reason observation")
    if sum(reasons.values()) < bad:
        raise ValueError("reason observations cannot cover the bad-row count")

    completeness = _exact_profile_object(
        top["completeness"],
        keys={
            "title_present",
            "body_present",
            "published_at_present",
            "valid_http_url",
        },
        label="completeness",
    )
    completeness_rates: dict[str, float] = {}
    completeness_counts: dict[str, int] = {}
    for field_name in (
        "title_present",
        "body_present",
        "published_at_present",
        "valid_http_url",
    ):
        item = _exact_profile_object(
            completeness[field_name],
            keys={"count", "rate"},
            label=f"completeness.{field_name}",
        )
        count = _profile_integer(
            item["count"],
            label=f"completeness.{field_name}.count",
            maximum=total,
        )
        rate = _profile_rate(
            item["rate"], label=f"completeness.{field_name}.rate"
        )
        if rate != _profile_ratio(count, total):
            raise ValueError(f"completeness.{field_name} rate is inconsistent")
        completeness_counts[field_name] = count
        completeness_rates[field_name] = rate
    if completeness_counts["title_present"] != total - reasons.get(
        "empty_title", 0
    ):
        raise ValueError("title completeness is inconsistent")
    if completeness_counts["body_present"] != total - reasons.get(
        "missing_body", 0
    ):
        raise ValueError("body completeness is inconsistent")
    if completeness_counts["valid_http_url"] != total - reasons.get(
        "missing_url", 0
    ) - reasons.get("invalid_url", 0):
        raise ValueError("URL completeness is inconsistent")

    exact = _exact_profile_object(
        top["exact_duplicates"],
        keys={"url", "normalized_content", "method", "near_duplicate_state"},
        label="exact_duplicates",
    )
    url_duplicates = _validate_duplicate_summary(
        exact["url"],
        label="exact_duplicates.url",
        maximum_rows=completeness_counts["valid_http_url"],
    )
    content_duplicates = _validate_duplicate_summary(
        exact["normalized_content"],
        label="exact_duplicates.normalized_content",
        maximum_rows=total,
    )
    if exact["method"] != "canonical_url_and_sha256_normalized_title_body":
        raise ValueError("exact-duplicate method is incompatible")
    if exact["near_duplicate_state"] != "candidate_observation_available":
        raise ValueError("near-duplicate state is incompatible")

    near = _exact_profile_object(
        top["near_duplicate_candidates"],
        keys={
            "method_version",
            "observation_state",
            "profile_evaluated_rows",
            "evaluated_rows",
            "row_evaluation_limit",
            "row_evaluation_truncated",
            "profile_scope_truncated",
            "eligible_rows",
            "ineligible_low_information_rows",
            "text_character_limit_per_row",
            "text_truncated_rows",
            "shingle_character_width",
            "minimum_distinct_shingles",
            "bottom_k_size",
            "candidate_generation_keys_per_row",
            "shingle_hash_method",
            "candidate_pairs_compared",
            "candidate_pair_comparison_limit",
            "candidate_pairs_skipped_at_least",
            "comparison_overflow",
            "candidate_generation_bucket_row_limit",
            "candidate_generation_bucket_overflow_events",
            "candidate_generation_overflow",
            "candidate_pairs_observed",
            "exact_duplicate_pairs_excluded",
            "similarity_metric",
            "candidate_minimum_similarity",
            "candidate_threshold_approval_state",
            "human_review_state",
            "duplicate_fact_state",
            "release_decision",
            "article_content_retained",
            "urls_or_row_identifiers_retained",
        },
        label="near_duplicate_candidates",
    )
    if near["method_version"] != NEWS_QUALITY_NEAR_DUPLICATE_METHOD_VERSION:
        raise ValueError("near-duplicate method is incompatible")
    if near["observation_state"] != "candidate_pairs_only":
        raise ValueError("near-duplicate observation state is incompatible")
    near_rows = min(total, _NEWS_QUALITY_MAX_NEAR_ROWS)
    near_profile_rows = _profile_integer(
        near["profile_evaluated_rows"],
        label="near_duplicate_candidates.profile_evaluated_rows",
        maximum=_NEWS_QUALITY_MAX_ROWS,
    )
    near_evaluated_rows = _profile_integer(
        near["evaluated_rows"],
        label="near_duplicate_candidates.evaluated_rows",
        maximum=_NEWS_QUALITY_MAX_NEAR_ROWS,
    )
    _profile_expected_integer(
        near["row_evaluation_limit"],
        label="near_duplicate_candidates.row_evaluation_limit",
        expected=_NEWS_QUALITY_MAX_NEAR_ROWS,
    )
    if (
        near_profile_rows != total
        or near_evaluated_rows != near_rows
        or near["row_evaluation_truncated"] is not False
        or near["profile_scope_truncated"] is not False
    ):
        raise ValueError("near-duplicate row scope is incomplete")
    eligible = _profile_integer(
        near["eligible_rows"],
        label="near_duplicate_candidates.eligible_rows",
        maximum=near_rows,
    )
    ineligible = _profile_integer(
        near["ineligible_low_information_rows"],
        label="near_duplicate_candidates.ineligible_low_information_rows",
        maximum=near_rows,
    )
    if eligible + ineligible != near_rows:
        raise ValueError("near-duplicate eligibility counts are inconsistent")
    text_truncated_rows = _profile_integer(
        near["text_truncated_rows"],
        label="near_duplicate_candidates.text_truncated_rows",
        maximum=near_rows,
    )
    if text_truncated_rows != 0:
        raise ValueError("near-duplicate input text was truncated")
    _profile_expected_integer(
        near["text_character_limit_per_row"],
        label="near_duplicate_candidates.text_character_limit_per_row",
        expected=4_096,
    )
    _profile_expected_integer(
        near["shingle_character_width"],
        label="near_duplicate_candidates.shingle_character_width",
        expected=5,
    )
    _profile_expected_integer(
        near["minimum_distinct_shingles"],
        label="near_duplicate_candidates.minimum_distinct_shingles",
        expected=32,
    )
    _profile_expected_integer(
        near["bottom_k_size"],
        label="near_duplicate_candidates.bottom_k_size",
        expected=32,
    )
    _profile_expected_integer(
        near["candidate_generation_keys_per_row"],
        label="near_duplicate_candidates.candidate_generation_keys_per_row",
        expected=_NEWS_QUALITY_NEAR_KEYS_PER_ROW,
    )
    if (
        near["shingle_hash_method"] != "rolling64_polynomial_base257_v1"
    ):
        raise ValueError("near-duplicate text method is incompatible")
    comparison_limit = _profile_integer(
        near["candidate_pair_comparison_limit"],
        label="near_duplicate_candidates.candidate_pair_comparison_limit",
        minimum=1,
        maximum=_NEWS_QUALITY_MAX_COMPARISONS,
    )
    pair_ceiling = eligible * (eligible - 1) // 2
    compared = _profile_integer(
        near["candidate_pairs_compared"],
        label="near_duplicate_candidates.candidate_pairs_compared",
        maximum=min(comparison_limit, pair_ceiling),
    )
    skipped = _profile_integer(
        near["candidate_pairs_skipped_at_least"],
        label="near_duplicate_candidates.candidate_pairs_skipped_at_least",
        maximum=pair_ceiling,
    )
    if compared + skipped > pair_ceiling:
        raise ValueError("near-duplicate pair counts exceed their ceiling")
    if near["comparison_overflow"] is not False or skipped != 0:
        raise ValueError("near-duplicate comparison was truncated")
    bucket_events = _profile_integer(
        near["candidate_generation_bucket_overflow_events"],
        label=(
            "near_duplicate_candidates."
            "candidate_generation_bucket_overflow_events"
        ),
        maximum=eligible * _NEWS_QUALITY_NEAR_KEYS_PER_ROW,
    )
    _profile_expected_integer(
        near["candidate_generation_bucket_row_limit"],
        label="near_duplicate_candidates.candidate_generation_bucket_row_limit",
        expected=_NEWS_QUALITY_MAX_BUCKET_ROWS,
    )
    if (
        bucket_events != 0
        or near["candidate_generation_overflow"] is not False
    ):
        raise ValueError("near-duplicate candidate buckets overflowed")
    candidates = _profile_integer(
        near["candidate_pairs_observed"],
        label="near_duplicate_candidates.candidate_pairs_observed",
        maximum=compared,
    )
    exact_excluded = _profile_integer(
        near["exact_duplicate_pairs_excluded"],
        label="near_duplicate_candidates.exact_duplicate_pairs_excluded",
        maximum=compared,
    )
    if candidates + exact_excluded > compared:
        raise ValueError("near-duplicate candidate counts are inconsistent")
    if (
        near["similarity_metric"] != "bottom_k_set_jaccard"
        or type(near["candidate_minimum_similarity"]) not in (int, float)
        or float(near["candidate_minimum_similarity"]) != 0.8
        or near["candidate_threshold_approval_state"] != "not_approved"
        or near["human_review_state"] != "not_provided"
        or near["duplicate_fact_state"] != "not_established"
        or near["release_decision"] != "not_computable"
        or near["article_content_retained"] is not False
        or near["urls_or_row_identifiers_retained"] is not False
    ):
        raise ValueError("near-duplicate assurance contract is incompatible")

    publication = _exact_profile_object(
        top["publication_time"],
        keys={"valid_count", "earliest_at", "cutoff_at"},
        label="publication_time",
    )
    valid_publications = _profile_integer(
        publication["valid_count"],
        label="publication_time.valid_count",
        maximum=total,
    )
    expected_valid_publications = total - reasons.get(
        "missing_published_at", 0
    ) - reasons.get("published_before_min_year", 0) - reasons.get(
        "published_future_too_far", 0
    )
    if valid_publications != expected_valid_publications:
        raise ValueError("publication validity count is inconsistent")
    if valid_publications == 0:
        if publication["earliest_at"] is not None or publication["cutoff_at"] is not None:
            raise ValueError("empty publication range is inconsistent")
        raise ValueError("publication cutoff is unavailable")
    earliest = _strict_profile_timestamp(publication["earliest_at"])
    cutoff = _strict_profile_timestamp(publication["cutoff_at"])
    if earliest > cutoff or cutoff > generated_at:
        raise ValueError("publication range is invalid")

    source = _exact_profile_object(
        top["source_coverage"],
        keys={
            "distinct_domains",
            "rows_with_valid_domain",
            "domain_names_retained",
            "domain_name_policy",
        },
        label="source_coverage",
    )
    source_domains = _profile_integer(
        source["distinct_domains"],
        label="source_coverage.distinct_domains",
        maximum=completeness_counts["valid_http_url"],
    )
    rows_with_valid_domain = _profile_integer(
        source["rows_with_valid_domain"],
        label="source_coverage.rows_with_valid_domain",
        maximum=total,
    )
    if (
        rows_with_valid_domain != completeness_counts["valid_http_url"]
        or source["domain_names_retained"] is not True
        or source["domain_name_policy"] != "public_dns_hostname_only"
    ):
        raise ValueError("source coverage is inconsistent")

    slices = _exact_profile_object(
        top["slices"],
        keys={
            "source_domain",
            "language",
            "publication_month",
            "max_values_per_dimension",
        },
        label="slices",
    )
    _profile_expected_integer(
        slices["max_values_per_dimension"],
        label="slices.max_values_per_dimension",
        expected=_NEWS_QUALITY_MAX_SLICE_VALUES,
    )
    source_slice = _validate_profile_slice(
        slices["source_domain"],
        dimension="source_domain",
        expected_rows=completeness_counts["valid_http_url"],
        total_good=good,
        total_bad=bad,
    )
    language_slice = _validate_profile_slice(
        slices["language"],
        dimension="language",
        expected_rows=total,
        total_good=good,
        total_bad=bad,
    )
    publication_slice = _validate_profile_slice(
        slices["publication_month"],
        dimension="publication_month",
        expected_rows=valid_publications,
        total_good=good,
        total_bad=bad,
    )
    if source_slice["distinct_values"] != source_domains:
        raise ValueError("source slice and coverage counts disagree")
    if language_slice["overflow_values"] or publication_slice["overflow_values"]:
        raise ValueError("profile slice coverage overflowed")

    schema_observation = _exact_profile_object(
        top["schema_observation"],
        keys={
            "known_field_set_version",
            "rows_with_unknown_fields",
            "unknown_field_occurrences",
            "distinct_unknown_fields",
            "unknown_field_names_retained",
            "drift_assessment",
        },
        label="schema_observation",
    )
    unknown_rows = _profile_integer(
        schema_observation["rows_with_unknown_fields"],
        label="schema_observation.rows_with_unknown_fields",
        maximum=total,
    )
    unknown_occurrences = _profile_integer(
        schema_observation["unknown_field_occurrences"],
        label="schema_observation.unknown_field_occurrences",
    )
    distinct_unknown = _profile_integer(
        schema_observation["distinct_unknown_fields"],
        label="schema_observation.distinct_unknown_fields",
    )
    expected_drift = (
        "observed_unreviewed" if unknown_rows else "no_unknown_fields_observed"
    )
    if (
        schema_observation["known_field_set_version"] != "news-article-fields-v2"
        or schema_observation["unknown_field_names_retained"] is not False
        or schema_observation["drift_assessment"] != expected_drift
        or (unknown_rows == 0 and (unknown_occurrences or distinct_unknown))
        or (unknown_rows > 0 and (
            unknown_occurrences < unknown_rows
            or distinct_unknown < 1
            or distinct_unknown > unknown_occurrences
        ))
    ):
        raise ValueError("schema observation is inconsistent")

    assurance = _exact_profile_object(
        top["assurance"],
        keys={
            "evaluation_state",
            "threshold_approval_state",
            "release_decision",
            "human_label_review",
            "limitations",
        },
        label="assurance",
    )
    limitations = assurance["limitations"]
    if (
        assurance["evaluation_state"] != "observed"
        or assurance["threshold_approval_state"] != "not_approved"
        or assurance["release_decision"] != "not_computable"
        or assurance["human_label_review"] != "not_provided"
        or type(limitations) is not list
        or len(limitations) != 4
        or any(type(item) is not str or not item for item in limitations)
    ):
        raise ValueError("profile assurance is incompatible")

    return {
        "generated_at": generated_at,
        "evaluated_rows": total,
        "max_rows": max_rows,
        "good_count": good,
        "bad_count": bad,
        "good_rate": good_rate,
        "bad_rate": bad_rate,
        "title_present_rate": completeness_rates["title_present"],
        "body_present_rate": completeness_rates["body_present"],
        "published_at_present_rate": completeness_rates[
            "published_at_present"
        ],
        "valid_http_url_rate": completeness_rates["valid_http_url"],
        "exact_url_duplicate_excess_rows": url_duplicates["excess_rows"],
        "exact_content_duplicate_excess_rows": content_duplicates["excess_rows"],
        "publication_cutoff_at": cutoff,
        "near_duplicate_evaluated_rows": near_rows,
        "near_duplicate_candidate_pairs_observed": candidates,
    }


def project_news_quality_profile(
    projection: Mapping[str, Any]
    | NewsQualityProfileProjectionEnvelope
    | None,
    *,
    observed_at: datetime | None = None,
) -> NewsQualityCatalogProjection:
    """Project a content-bound v3 profile without conferring a quality pass."""

    now = _utc_now(observed_at)
    if projection is None:
        return _unavailable_news_quality_projection("PROFILE_NOT_PROVIDED")
    try:
        if isinstance(projection, NewsQualityProfileProjectionEnvelope):
            raw_projection: Any = projection.model_dump(mode="python")
        elif isinstance(projection, Mapping):
            raw_projection = dict(projection)
        else:
            raise ValueError("projection envelope must be an object")
        _bounded_canonical_json_bytes(raw_projection)
        envelope = NewsQualityProfileProjectionEnvelope.model_validate(
            raw_projection
        )
    except (TypeError, ValueError, ValidationError, OverflowError):
        return _unavailable_news_quality_projection(
            "PROFILE_ENVELOPE_INVALID"
        )

    if (
        envelope.target_record_id != "dataset.news_articles"
        or envelope.scope_id != NEWS_QUALITY_CATALOG_SCOPE_ID
    ):
        return _unavailable_news_quality_projection("PROFILE_SCOPE_MISMATCH")
    try:
        profile_bytes = _bounded_canonical_json_bytes(envelope.profile)
    except (TypeError, ValueError, OverflowError):
        return _unavailable_news_quality_projection("PROFILE_CONTRACT_INVALID")
    if not hashlib.sha256(profile_bytes).hexdigest() == envelope.profile_sha256:
        return _unavailable_news_quality_projection("PROFILE_HASH_MISMATCH")

    profile = envelope.profile
    if profile.get("schema_version") != NEWS_QUALITY_PROFILE_SCHEMA_VERSION:
        return _unavailable_news_quality_projection(
            "PROFILE_SCHEMA_INCOMPATIBLE"
        )
    if profile.get("method_version") != NEWS_QUALITY_PROFILE_METHOD_VERSION:
        return _unavailable_news_quality_projection(
            "PROFILE_METHOD_INCOMPATIBLE"
        )
    try:
        generated_at = _strict_profile_timestamp(profile.get("generated_at"))
    except ValueError:
        return _unavailable_news_quality_projection(
            "PROFILE_TIMESTAMP_INVALID"
        )
    if generated_at > now:
        return _unavailable_news_quality_projection("PROFILE_FUTURE_DATED")
    if now - generated_at > NEWS_QUALITY_PROFILE_MAX_AGE:
        return _unavailable_news_quality_projection("PROFILE_STALE")

    scope = profile.get("scope")
    if isinstance(scope, dict) and scope.get("truncated") is True:
        return _unavailable_news_quality_projection("PROFILE_SCOPE_TRUNCATED")
    near = profile.get("near_duplicate_candidates")
    if isinstance(near, dict):
        if near.get("row_evaluation_truncated") is True:
            return _unavailable_news_quality_projection(
                "NEAR_DUPLICATE_ROW_TRUNCATED"
            )
        if near.get("text_truncated_rows", 0) != 0:
            return _unavailable_news_quality_projection(
                "NEAR_DUPLICATE_TEXT_TRUNCATED"
            )
        if near.get("comparison_overflow") is True:
            return _unavailable_news_quality_projection(
                "NEAR_DUPLICATE_COMPARISON_OVERFLOW"
            )
        if near.get("candidate_generation_overflow") is True:
            return _unavailable_news_quality_projection(
                "NEAR_DUPLICATE_BUCKET_OVERFLOW"
            )
        candidate_threshold = near.get("candidate_threshold_approval_state")
        if candidate_threshold is not None and candidate_threshold != "not_approved":
            return _unavailable_news_quality_projection(
                "NEAR_DUPLICATE_THRESHOLD_STATE_UNSUPPORTED"
            )
        duplicate_fact = near.get("duplicate_fact_state")
        if duplicate_fact is not None and duplicate_fact != "not_established":
            return _unavailable_news_quality_projection(
                "NEAR_DUPLICATE_FACT_STATE_UNSUPPORTED"
            )
        human_review = near.get("human_review_state")
        if human_review is not None and human_review != "not_provided":
            return _unavailable_news_quality_projection(
                "NEAR_DUPLICATE_REVIEW_STATE_UNSUPPORTED"
            )
    slices = profile.get("slices")
    if isinstance(slices, dict):
        for dimension in ("source_domain", "language", "publication_month"):
            item = slices.get(dimension)
            if isinstance(item, dict) and (
                item.get("overflow_values", 0) != 0
                or item.get("overflow_rows", 0) != 0
            ):
                return _unavailable_news_quality_projection(
                    "PROFILE_SLICE_OVERFLOW"
                )
    assurance = profile.get("assurance")
    if isinstance(assurance, dict):
        threshold = assurance.get("threshold_approval_state")
        if threshold is not None and threshold != "not_approved":
            return _unavailable_news_quality_projection(
                "PROFILE_THRESHOLD_STATE_UNSUPPORTED"
            )
        release_decision = assurance.get("release_decision")
        if release_decision is not None and release_decision != "not_computable":
            return _unavailable_news_quality_projection(
                "PROFILE_RELEASE_STATE_UNSUPPORTED"
            )

    try:
        summary = _validated_news_quality_profile_summary(profile)
    except (TypeError, ValueError, OverflowError):
        return _unavailable_news_quality_projection("PROFILE_CONTRACT_INVALID")

    cutoff = summary["publication_cutoff_at"]
    if not isinstance(cutoff, datetime):
        return _unavailable_news_quality_projection(
            "PROFILE_CUTOFF_UNAVAILABLE"
        )
    if cutoff > now:
        return _unavailable_news_quality_projection(
            "PROFILE_CUTOFF_FUTURE_DATED"
        )
    if now - cutoff > NEWS_QUALITY_PROFILE_MAX_AGE:
        return _unavailable_news_quality_projection("PROFILE_CUTOFF_STALE")

    quality_metrics: dict[str, CatalogMetric] = {
        "artifact_state": "mechanically_validated",
        "profile_sha256": envelope.profile_sha256,
        "evaluated_rows": int(summary["evaluated_rows"]),
        "good_count": int(summary["good_count"]),
        "bad_count": int(summary["bad_count"]),
        "good_rate": float(summary["good_rate"]),
        "bad_rate": float(summary["bad_rate"]),
        "title_present_rate": float(summary["title_present_rate"]),
        "body_present_rate": float(summary["body_present_rate"]),
        "published_at_present_rate": float(
            summary["published_at_present_rate"]
        ),
        "valid_http_url_rate": float(summary["valid_http_url_rate"]),
        "exact_url_duplicate_excess_rows": int(
            summary["exact_url_duplicate_excess_rows"]
        ),
        "exact_content_duplicate_excess_rows": int(
            summary["exact_content_duplicate_excess_rows"]
        ),
        "publication_cutoff_at": cutoff.isoformat(),
        "near_duplicate_observation_state": "candidate_pairs_only",
        "near_duplicate_candidate_pairs_observed": int(
            summary["near_duplicate_candidate_pairs_observed"]
        ),
        "near_duplicate_duplicate_fact_state": "not_established",
        "near_duplicate_threshold_approval_state": "not_approved",
        "profile_threshold_approval_state": "not_approved",
        "profile_release_decision": "not_computable",
    }
    quality = QualityRegistration(
        status="unknown",
        evaluated_at=summary["generated_at"],
        evaluation_version=NEWS_QUALITY_PROFILE_METHOD_VERSION,
        metrics=quality_metrics,
        known_issues=[
            "PROFILE_THRESHOLDS_NOT_APPROVED",
            "FACTUAL_ACCURACY_UNVERIFIED",
            "SOURCE_RELIABILITY_UNVERIFIED",
            "NEAR_DUPLICATE_HUMAN_REVIEW_NOT_PROVIDED",
        ],
        evidence=[
            _evidence(
                "scripts/news_ingest_quality.py#profile_news_rows",
                "The server recomputed the bounded canonical profile hash and "
                "validated the complete v3 mechanical contract; this does not "
                "authenticate source rows or establish factual quality.",
                "verified",
            )
        ],
    )
    coverage = CoverageRegistration(
        status="partial",
        scope=(
            "One explicitly bound, bounded offline input profile; no full-corpus "
            "coverage is asserted."
        ),
        metrics={
            "profile_scope_id": NEWS_QUALITY_CATALOG_SCOPE_ID,
            "profile_evaluated_rows": int(summary["evaluated_rows"]),
            "profile_max_rows": int(summary["max_rows"]),
            "profile_scope_truncated": False,
            "near_duplicate_evaluated_rows": int(
                summary["near_duplicate_evaluated_rows"]
            ),
            "near_duplicate_row_evaluation_truncated": False,
            "near_duplicate_comparison_overflow": False,
            "near_duplicate_bucket_overflow": False,
        },
        missing_dimensions=[
            "country_coverage",
            "full_language_coverage",
            "full_dataset_coverage",
            "factual_accuracy",
            "source_reliability",
            "cross_source_consistency",
            "approved_quality_thresholds",
            "near_duplicate_human_review",
        ],
        evidence=[
            _evidence(
                "scripts/news_ingest_quality.py#profile_news_rows",
                "Counts describe only the explicitly bounded offline input; "
                "they are not a corpus-size or source-quality assertion.",
                "partial",
            )
        ],
    )
    return NewsQualityCatalogProjection(
        state="mechanically_validated",
        reason_codes=[
            "PROFILE_THRESHOLDS_NOT_APPROVED",
            "QUALITY_FACT_NOT_ESTABLISHED",
        ],
        quality=quality,
        coverage=coverage,
    )


def _add_optional_metric(
    metrics: dict[str, CatalogMetric],
    name: str,
    value: FiniteFloat | float | None,
) -> None:
    if value is not None:
        metrics[name] = float(value)


def _project_assurance_quality(entry: StoredEvaluation) -> QualityRegistration:
    """Expose aggregate recomputed evidence without ledger or dataset internals."""

    result = entry.result
    overall = result.overall
    strata = result.strata
    coverage = result.coverage
    metrics: dict[str, CatalogMetric] = {
        "assurance_evidence_status": result.evidence_status,
        "release_eligible": result.release_eligible,
        "overall_sample_count": overall.sample_count,
        "overall_positive_count": overall.positive_count,
        "overall_predicted_positive_count": overall.predicted_positive_count,
        "overall_brier_score": float(overall.brier_score),
        "overall_expected_calibration_error": float(
            overall.expected_calibration_error
        ),
        "strata_count": len(strata),
        "country_strata_count": sum(item.dimension == "country" for item in strata),
        "language_strata_count": sum(
            item.dimension == "language" for item in strata
        ),
        "topic_strata_count": sum(item.dimension == "topic" for item in strata),
        "coverage_state": coverage.state,
        "coverage_minimum_samples_satisfied": coverage.minimum_samples_satisfied,
        "calibration_bin_count": len(entry.manifest.overall.calibration_bins),
        "drift_state": result.drift.state,
        "rollback_action": result.rollback.action,
        "metric_method_version": result.metric_method_version,
    }
    _add_optional_metric(metrics, "overall_precision", overall.precision)
    _add_optional_metric(metrics, "overall_recall", overall.recall)
    _add_optional_metric(metrics, "overall_f1", overall.f1)
    for dimension in ("country", "language", "topic"):
        metrics[f"coverage_expected_{dimension}_count"] = len(
            coverage.expected[dimension]
        )
        metrics[f"coverage_observed_{dimension}_count"] = len(
            coverage.observed[dimension]
        )
        metrics[f"coverage_missing_{dimension}_count"] = len(
            coverage.missing[dimension]
        )
        metrics[f"coverage_unexpected_{dimension}_count"] = len(
            coverage.unexpected[dimension]
        )
    for field_name, public_name, aggregate in (
        ("precision", "minimum_stratum_precision", min),
        ("recall", "minimum_stratum_recall", min),
        ("f1", "minimum_stratum_f1", min),
        ("brier_score", "maximum_stratum_brier_score", max),
        (
            "expected_calibration_error",
            "maximum_stratum_expected_calibration_error",
            max,
        ),
    ):
        values = [
            float(value)
            for item in strata
            if (value := getattr(item.metrics, field_name)) is not None
        ]
        if values:
            metrics[public_name] = aggregate(values)
    _add_optional_metric(metrics, "drift_f1_delta", result.drift.f1_delta)
    _add_optional_metric(metrics, "drift_brier_delta", result.drift.brier_delta)
    _add_optional_metric(metrics, "drift_ece_delta", result.drift.ece_delta)
    return QualityRegistration(
        status="passed",
        evaluated_at=result.evaluated_at,
        evaluation_version=result.metric_method_version,
        metrics=metrics,
        known_issues=[
            "Evidence remains manifest-only; the public catalog does not reverify "
            "dataset bytes or external review artifacts."
        ],
        evidence=[
            _evidence(
                _MODEL_ASSURANCE_EVIDENCE_REF,
                "An exact model, model-version, and method-version match passed "
                "the hash-chain-verified model-assurance release gate; only "
                "aggregate server-recomputed metrics are projected publicly.",
                "verified",
            )
        ],
    )


def _opinion_model_assurance_quality(root: Path) -> QualityRegistration:
    unavailable = _unknown_quality(
        "No exact-match release-eligible model-assurance entry was verified."
    )
    try:
        entry = ModelAssuranceService(
            ModelAssuranceStore(root)
        ).latest_release_eligible_evaluation(
            model_id=_OPINION_ASSURANCE_MODEL_ID,
            model_version=OPINION_MODEL_VERSION,
            method_version=METHOD_VERSION,
        )
    except AssuranceStoreUnavailable:
        return unavailable
    return _project_assurance_quality(entry) if entry is not None else unavailable


def _partial_schema(identifier: str, schema_ref: str) -> SchemaRegistration:
    return SchemaRegistration(
        status="partial",
        record_identifier=identifier,
        schema_ref=schema_ref,
        evidence=[
            _evidence(
                schema_ref,
                "A physical or response schema is visible in source, but no complete data dictionary, cross-source mapping, or change log is registered.",
                "partial",
            )
        ],
    )


def _authoritative_source_drafts(
    *,
    observed_at: datetime,
    owner_roles: Mapping[str, str],
) -> list[CatalogRecordDraft]:
    """Register connector capabilities without interpreting config as live data."""

    drafts: list[CatalogRecordDraft] = []
    for descriptor in connector_descriptors():
        source_id = descriptor.source.source_id
        reference = "backend/api/features/authoritative_data/sources.py"
        license_status: Literal["restricted", "unknown"] = (
            "restricted" if descriptor.license.state == "restricted" else "unknown"
        )
        drafts.append(
            CatalogRecordDraft(
                record_id=f"source.{source_id.replace('-', '_')}",
                kind="source",
                title=f"{descriptor.source.authority} bounded connector",
                description=(
                    "Checked-in HTTPS connector registration. The catalog entry does "
                    "not perform a live probe and does not assert current availability."
                ),
                owner=_owner("runtime-operations", owner_roles),
                version=VersionRegistration(
                    value=f"{descriptor.api_version};{descriptor.adapter_version}",
                    status="verified",
                    scheme="api-and-adapter-version",
                    evidence=[
                        _evidence(
                            reference,
                            "API and adapter versions are declared in the checked-in connector descriptor.",
                            "verified",
                        )
                    ],
                ),
                operational=OperationalRegistration(
                    state="unknown",
                    evidence_status="unknown",
                    observed_at=observed_at,
                    source=f"connector-registration:{source_id}",
                    reason_codes=["LIVE_STATUS_NOT_OBSERVED"],
                ),
                freshness=FreshnessRegistration(
                    state="offline",
                    evidence_status="unknown",
                    observed_at=observed_at,
                    source=f"connector-registration:{source_id}",
                    reason_codes=[
                        "LIVE_STATUS_NOT_OBSERVED",
                        "CUTOFF_UNKNOWN",
                        "LAST_SUCCESS_UNKNOWN",
                    ],
                ),
                coverage=CoverageRegistration(
                    status="unknown",
                    scope="A single bounded query may return at most the registered limit.",
                    metrics={
                        "maximum_records_per_request": descriptor.maximum_records_per_request,
                    },
                    missing_dimensions=[
                        "country_coverage",
                        "indicator_coverage",
                        "temporal_coverage",
                        "availability_history",
                    ],
                    evidence=[
                        _evidence(
                            reference,
                            "Only request bounds are registered; corpus coverage has not been measured.",
                            "declared",
                        )
                    ],
                ),
                license=LicenseRegistration(
                    status=license_status,
                    identifier=descriptor.license.identifier,
                    usage_scope=descriptor.license.scope,
                    terms_ref=descriptor.license.terms_url,
                    retention_policy=None,
                    evidence=[
                        _evidence(
                            reference,
                            "License evidence is copied from the connector descriptor and remains subject to dataset-level review.",
                            "partial",
                        )
                    ],
                ),
                quality=_unknown_quality(
                    "No candidate-environment availability history, cross-source comparison, or domain review is registered."
                ),
                provenance=ProvenanceRegistration(
                    status="partial",
                    parser_version=descriptor.adapter_version,
                    content_hash_status="declared",
                    evidence=[
                        _evidence(
                            "backend/api/features/authoritative_data/service.py",
                            "Successful bounded responses carry a normalized payload hash, but durable source snapshots and revision history are not registered.",
                            "partial",
                        )
                    ],
                ),
                schema=_partial_schema(
                    f"{source_id}.record_id",
                    "backend/api/features/authoritative_data/contracts.py#AuthorityRecord",
                ),
                evidence=[
                    _evidence(
                        descriptor.source.documentation_url,
                        "The connector descriptor links to first-party API documentation; this is not operational evidence.",
                        "declared",
                    )
                ],
            )
        )
    return drafts


def evaluate_catalog_record(
    draft: CatalogRecordDraft,
    *,
    evaluated_at: datetime,
) -> CatalogRecord:
    """Recompute formal readiness; callers cannot self-declare eligibility."""

    reasons: list[str] = []

    def add(code: str) -> None:
        if code not in reasons:
            reasons.append(code)

    if draft.owner.assignment_status != "named":
        add("OWNER_NOT_NAMED")
    elif (
        not draft.owner.owner_id
        or not draft.owner.display_name
        or not any(item.status == "verified" for item in draft.owner.evidence)
    ):
        add("OWNER_EVIDENCE_INCOMPLETE")
    if draft.version.status != "verified" or not draft.version.value:
        add("VERSION_UNVERIFIED")
    elif (
        not draft.version.scheme
        or not any(item.status == "verified" for item in draft.version.evidence)
    ):
        add("VERSION_EVIDENCE_INCOMPLETE")
    if draft.version.change_log_ref is None:
        add("CHANGE_LOG_UNAVAILABLE")
    if draft.operational.evidence_status != "verified":
        add("OPERATIONAL_STATUS_UNVERIFIED")
    if draft.operational.state != "available":
        add(f"OPERATIONAL_{draft.operational.state.upper()}")
    if (
        draft.operational.evidence_status == "verified"
        and not draft.operational.source
    ):
        add("OPERATIONAL_EVIDENCE_INCOMPLETE")
    for reason_code in draft.operational.reason_codes:
        add(reason_code)
    if draft.freshness.evidence_status != "verified":
        add("FRESHNESS_UNVERIFIED")
    if draft.freshness.state != "live":
        add(f"FRESHNESS_{draft.freshness.state.upper()}")
    if draft.freshness.cutoff_at is None:
        add("CUTOFF_UNKNOWN")
    if draft.freshness.last_success_at is None:
        add("LAST_SUCCESS_UNKNOWN")
    for reason_code in draft.freshness.reason_codes:
        add(reason_code)
    if (
        draft.freshness.evidence_status == "verified"
        and (not draft.freshness.source or draft.freshness.reason_codes)
    ):
        add("FRESHNESS_EVIDENCE_INCOMPLETE")
    if draft.coverage.status != "verified":
        add("COVERAGE_UNVERIFIED")
    elif (
        not draft.coverage.scope
        or not draft.coverage.metrics
        or not any(item.status == "verified" for item in draft.coverage.evidence)
    ):
        add("COVERAGE_EVIDENCE_INCOMPLETE")
    if draft.coverage.missing_dimensions:
        add("COVERAGE_DIMENSIONS_MISSING")
    if draft.license.status == "unknown" or not draft.license.usage_scope:
        add("LICENSE_UNKNOWN")
    elif (
        not draft.license.identifier
        or not draft.license.terms_ref
        or not draft.license.retention_policy
        or not any(item.status == "verified" for item in draft.license.evidence)
    ):
        add("LICENSE_EVIDENCE_INCOMPLETE")
    if draft.quality.status != "passed":
        add("QUALITY_UNVERIFIED" if draft.quality.status == "unknown" else "QUALITY_NOT_PASSED")
    elif (
        draft.quality.evaluated_at is None
        or not draft.quality.evaluation_version
        or not draft.quality.metrics
        or not any(item.status == "verified" for item in draft.quality.evidence)
    ):
        add("QUALITY_EVIDENCE_INCOMPLETE")
    if draft.provenance.status != "verified":
        add("PROVENANCE_INCOMPLETE")
    elif (
        draft.provenance.capture_timestamp_status != "verified"
        or draft.provenance.web_snapshot_status != "verified"
        or draft.provenance.content_hash_status != "verified"
        or draft.provenance.revision_tracking_status != "verified"
        or not draft.provenance.parser_version
        or not any(item.status == "verified" for item in draft.provenance.evidence)
    ):
        add("PROVENANCE_EVIDENCE_INCOMPLETE")
    if draft.schema_registration.status != "verified":
        add("SCHEMA_GOVERNANCE_INCOMPLETE")
    elif (
        not draft.schema_registration.record_identifier
        or not draft.schema_registration.schema_ref
        or not draft.schema_registration.data_dictionary_ref
        or not draft.schema_registration.mapping_refs
        or not draft.schema_registration.change_log_ref
        or not any(
            item.status == "verified"
            for item in draft.schema_registration.evidence
        )
    ):
        add("SCHEMA_EVIDENCE_INCOMPLETE")
    eligible = not reasons
    status = RegistrationStatus(
        state="eligible" if eligible else "blocked",
        release_eligible=eligible,
        research_ready=eligible,
        reason_codes=reasons,
        evaluated_at=_utc_now(evaluated_at),
    )
    return CatalogRecord(**draft.model_dump(), status=status)


def _default_drafts(
    *,
    observed_at: datetime,
    owner_roles: Mapping[str, str],
    health_checks: Mapping[str, FeatureHealthCheck],
    source_catalog: Mapping[str, Any],
    source_catalog_available: bool,
    opinion_model_quality: QualityRegistration,
    news_quality_projection: NewsQualityCatalogProjection,
) -> list[CatalogRecordDraft]:
    news_freshness = freshness_from_health(
        health_checks.get("ground-news"), observed_at=observed_at
    )
    news_operational = operational_from_health(
        health_checks.get("ground-news"), observed_at=observed_at
    )
    opinion_freshness = freshness_from_health(
        health_checks.get("opinion-analysis"), observed_at=observed_at
    )
    opinion_operational = operational_from_health(
        health_checks.get("opinion-analysis"), observed_at=observed_at
    )
    graph_freshness = freshness_from_health(
        health_checks.get("story-graph"), observed_at=observed_at
    )
    graph_operational = operational_from_health(
        health_checks.get("story-graph"), observed_at=observed_at
    )
    source_metrics = {
        key: value
        for key, value in source_catalog.items()
        if key != "sha256" and isinstance(value, (str, int, float, bool))
    }
    source_version = source_catalog.get("sha256") if source_catalog_available else None
    source_evidence_status: EvidenceStatus = (
        "verified" if source_catalog_available else "unknown"
    )
    source_evidence = [
        _evidence(
            _PUBLIC_SOURCE_CATALOG_REF,
            "Existing curated source inventory was aggregated without exposing row-level content.",
            source_evidence_status,
        )
    ]
    drafts = [
        CatalogRecordDraft(
            record_id="dataset.news_articles",
            kind="dataset",
            title="新闻文章主数据",
            description="Search, Ground News, and story analysis read article records from public.news.",
            owner=_owner("news-intelligence", owner_roles),
            version=VersionRegistration(),
            operational=news_operational,
            freshness=news_freshness,
            coverage=news_quality_projection.coverage,
            license=_unknown_license(),
            quality=news_quality_projection.quality,
            provenance=ProvenanceRegistration(
                status="partial",
                capture_timestamp_status="partial",
                web_snapshot_status="partial",
                content_hash_status="partial",
                parser_version="article-display-v1",
                revision_tracking_status="partial",
                evidence=[
                    _evidence(
                        "backend/api/features/evidence/ledger.py#EvidenceSnapshotLedger",
                        "Explicit authenticated captures now provide immutable normalized-body hashes, parser version, revision events, and downstream impact review; historical coverage and source-page preservation rights are not yet measured.",
                        "partial",
                    )
                ],
            ),
            schema=_partial_schema("public.news.id", "backend/api/orm/models.py#News"),
            evidence=[
                _evidence(
                    "backend/api/features/ground_news/health.py",
                    "Business freshness is observed through the existing Ground News health probe.",
                    "verified",
                )
            ],
        ),
        CatalogRecordDraft(
            record_id="dataset.event_hierarchy",
            kind="dataset",
            title="事件层级与故事图数据",
            description="L1/L1.5/L2/L3 event hierarchy relations used by Story Graph and briefings.",
            owner=_owner("knowledge-graph", owner_roles),
            version=VersionRegistration(),
            operational=graph_operational,
            freshness=graph_freshness,
            coverage=_unknown_coverage(
                "country_coverage",
                "language_coverage",
                "temporal_coverage",
                "orphan_rate",
                "duplicate_rate",
            ),
            license=_unknown_license(),
            quality=_unknown_quality(
                "No registered hierarchy completeness, orphan, duplication, or revision benchmark."
            ),
            provenance=ProvenanceRegistration(),
            schema=_partial_schema(
                "event hierarchy composite keys",
                "backend/api/features/story_graph/health.py#STORY_GRAPH_HEALTH_RELATIONS",
            ),
            evidence=[
                _evidence(
                    "backend/api/features/story_graph/health.py",
                    "Relation readability is probed, but no business watermark is currently supplied.",
                    "partial",
                )
            ],
        ),
        CatalogRecordDraft(
            record_id="dataset.china_opinion_scores",
            kind="dataset",
            title="涉华舆情评分数据",
            description="Article-level targeted-China stance scores and dimensions used by opinion analysis.",
            owner=_owner("opinion-intelligence", owner_roles),
            version=VersionRegistration(),
            operational=opinion_operational,
            freshness=opinion_freshness,
            coverage=_unknown_coverage(
                "country_coverage",
                "language_coverage",
                "model_evaluation_coverage",
                "revision_coverage",
            ),
            license=_unknown_license(),
            quality=_unknown_quality(
                "Runtime trust gates exist, but no catalogued gold-standard, calibration, or drift evaluation is available."
            ),
            provenance=ProvenanceRegistration(
                status="partial",
                parser_version=METHOD_VERSION,
                revision_tracking_status="partial",
                evidence=[
                    _evidence(
                        "backend/api/features/opinion/trust.py",
                        "Method and snapshot identifiers are exposed for composite responses; an explicit article evidence ledger exists, but source-article capture coverage and propagation into every historical score remain incomplete.",
                        "partial",
                    )
                ],
            ),
            schema=_partial_schema(
                "public.china_opinion_article_scores.news_id",
                "backend/api/routes/opinion_v2.py#_OPINION_WRITE_COLUMNS",
            ),
            evidence=[
                _evidence(
                    "backend/api/features/opinion/health.py",
                    "Latest score date is observed through the existing opinion health probe.",
                    "verified",
                )
            ],
        ),
        CatalogRecordDraft(
            record_id="source.news_ingestion_network",
            kind="source",
            title="新闻采集来源集合",
            description="Aggregate registration of the current curated media source inventory; individual source licensing and coverage registration is not yet complete.",
            owner=_owner("news-intelligence", owner_roles),
            version=VersionRegistration(
                value=f"sha256:{source_version}" if source_version else None,
                status="verified" if source_version else "unknown",
                scheme="content-sha256" if source_version else None,
                evidence=source_evidence,
            ),
            operational=OperationalRegistration(
                state="unknown",
                evidence_status="unknown",
                observed_at=observed_at,
                reason_codes=["SOURCE_LEVEL_OPERATIONAL_STATUS_UNKNOWN"],
            ),
            freshness=FreshnessRegistration(
                state="offline",
                evidence_status="unknown",
                observed_at=observed_at,
                reason_codes=[
                    "SOURCE_LEVEL_FRESHNESS_UNKNOWN",
                    "LAST_SUCCESS_UNKNOWN",
                ],
            ),
            coverage=CoverageRegistration(
                status="partial" if source_catalog_available else "unknown",
                scope="Inventory rows and classification dimensions only; this is not evidence of country, language, topic, or crawl-success coverage.",
                metrics=source_metrics,
                missing_dimensions=[
                    "country_coverage",
                    "language_coverage",
                    "topic_coverage",
                    "crawl_success_rate",
                    "last_success_at",
                ],
                evidence=source_evidence,
            ),
            license=_unknown_license(),
            quality=_unknown_quality(
                "Priority and quality tiers are curation labels, not independently validated source reliability scores."
            ),
            provenance=ProvenanceRegistration(
                status="partial" if source_catalog_available else "unknown",
                content_hash_status=source_evidence_status,
                parser_version="data-governance-source-csv-v1",
                evidence=source_evidence,
            ),
            schema=_partial_schema(
                "domain",
                _PUBLIC_SOURCE_CATALOG_REF,
            ),
            evidence=source_evidence,
        ),
        CatalogRecordDraft(
            record_id="model.china_opinion_stance",
            kind="model",
            title="涉华目标立场评分模型/方法",
            description="Versioned targeted-China stance method used by the opinion score pipeline.",
            owner=_owner("opinion-intelligence", owner_roles),
            version=VersionRegistration(
                value=METHOD_VERSION,
                status="verified",
                scheme="application-method-version",
                evidence=[
                    _evidence(
                        "backend/api/features/opinion/constants.py#METHOD_VERSION",
                        "The runtime scoring method version is defined in one source constant.",
                        "verified",
                    )
                ],
            ),
            operational=OperationalRegistration(
                state="unknown",
                evidence_status="unknown",
                observed_at=observed_at,
                reason_codes=["MODEL_RUNTIME_STATUS_UNKNOWN"],
            ),
            freshness=FreshnessRegistration(
                state="offline",
                evidence_status="unknown",
                observed_at=observed_at,
                reason_codes=["MODEL_REVIEW_DATE_UNKNOWN", "LAST_SUCCESS_UNKNOWN"],
            ),
            coverage=_unknown_coverage(
                "language_evaluation",
                "country_evaluation",
                "topic_evaluation",
                "calibration",
                "drift",
            ),
            license=_unknown_license(),
            quality=opinion_model_quality,
            provenance=ProvenanceRegistration(
                status="partial",
                parser_version=METHOD_VERSION,
                evidence=[
                    _evidence(
                        "backend/api/features/opinion/trust.py",
                        "Runtime method metadata is emitted with opinion results; training-data lineage and evaluation artifacts remain unregistered.",
                        "partial",
                    )
                ],
            ),
            schema=_partial_schema(
                "news_id",
                "backend/api/features/opinion/trust.py#evaluate_opinion_trust",
            ),
            evidence=[
                _evidence(
                    "backend/api/features/opinion/constants.py#METHOD_VERSION",
                    "Version is reused without truncation.",
                    "verified",
                )
            ],
        ),
    ]
    drafts.extend(
        _authoritative_source_drafts(
            observed_at=observed_at,
            owner_roles=owner_roles,
        )
    )
    return drafts


def _summary(records: Sequence[CatalogRecord]) -> CatalogSummary:
    blocker_counts = Counter(
        reason for record in records for reason in record.status.reason_codes
    )
    eligible = sum(record.status.release_eligible for record in records)
    return CatalogSummary(
        record_count=len(records),
        dataset_count=sum(record.kind == "dataset" for record in records),
        source_count=sum(record.kind == "source" for record in records),
        model_count=sum(record.kind == "model" for record in records),
        eligible_count=eligible,
        blocked_count=len(records) - eligible,
        formal_release_status="ready" if records and eligible == len(records) else "blocked",
        blocker_counts=dict(sorted(blocker_counts.items())),
    )


def build_data_catalog(
    *,
    health_checks: Mapping[str, FeatureHealthCheck] | None = None,
    generated_at: datetime | None = None,
    owner_registry_path: Path = DEFAULT_OWNER_REGISTRY,
    source_catalog_path: Path = DEFAULT_SOURCE_CATALOG,
    model_assurance_root: Path | None = None,
    news_quality_profile_projection: Mapping[str, Any]
    | NewsQualityProfileProjectionEnvelope
    | None = None,
    kind: CatalogKind | None = None,
) -> DataCatalogResponse:
    now = _utc_now(generated_at)
    owner_roles, owners_available = load_owner_roles(owner_registry_path)
    source_catalog, sources_available = inspect_source_catalog(source_catalog_path)
    assurance_root = model_assurance_root or Path(
        string_setting("MODEL_ASSURANCE_ROOT", "/root/data/web/model_assurance")
    )
    drafts = _default_drafts(
        observed_at=now,
        owner_roles=owner_roles,
        health_checks=health_checks or {},
        source_catalog=source_catalog,
        source_catalog_available=sources_available,
        opinion_model_quality=_opinion_model_assurance_quality(assurance_root),
        news_quality_projection=project_news_quality_profile(
            news_quality_profile_projection,
            observed_at=now,
        ),
    )
    records = [evaluate_catalog_record(item, evaluated_at=now) for item in drafts]
    if kind is not None:
        records = [record for record in records if record.kind == kind]
    summary = _summary(records)
    reason_codes: list[str] = []
    if not owners_available:
        reason_codes.append("OWNER_REGISTRY_UNAVAILABLE")
    if not sources_available:
        reason_codes.append("SOURCE_CATALOG_UNAVAILABLE")
    if summary.formal_release_status == "blocked":
        reason_codes.append("FORMAL_REGISTRATION_INCOMPLETE")
    return DataCatalogResponse(
        available=True,
        generated_at=now,
        catalog_status="ready" if summary.formal_release_status == "ready" else "incomplete",
        registry_sources=RegistrySourceStatus(
            owner_registry="verified" if owners_available else "unavailable",
            source_catalog="verified" if sources_available else "unavailable",
            references=[_PUBLIC_OWNER_REGISTRY_REF, _PUBLIC_SOURCE_CATALOG_REF],
        ),
        summary=summary,
        records=records,
        reason_codes=reason_codes,
    )


def unavailable_data_catalog(*, generated_at: datetime | None = None) -> DataCatalogResponse:
    now = _utc_now(generated_at)
    return DataCatalogResponse(
        available=False,
        generated_at=now,
        catalog_status="unavailable",
        registry_sources=RegistrySourceStatus(
            owner_registry="unavailable",
            source_catalog="unavailable",
            references=[_PUBLIC_OWNER_REGISTRY_REF, _PUBLIC_SOURCE_CATALOG_REF],
        ),
        summary=CatalogSummary(
            record_count=0,
            dataset_count=0,
            source_count=0,
            model_count=0,
            eligible_count=0,
            blocked_count=0,
            formal_release_status="blocked",
        ),
        records=[],
        reason_codes=["CATALOG_UNAVAILABLE"],
    )


def collect_catalog_health(db: Session) -> dict[str, FeatureHealthCheck]:
    """Reuse existing read-only probes; failures remain isolated and redacted."""

    checks = (
        probe_ground_news_health(db),
        probe_opinion_health(db),
        probe_story_graph_health(
            lambda: probe_postgres_relations(db, STORY_GRAPH_HEALTH_RELATIONS)
        ),
    )
    return {check.feature_id: check for check in checks}


__all__ = (
    "CatalogEvidence",
    "CatalogKind",
    "CatalogRecord",
    "CatalogRecordDraft",
    "CatalogSummary",
    "CoverageRegistration",
    "DataCatalogResponse",
    "DATA_CATALOG_CONTRACT_VERSION",
    "DATA_CATALOG_SCHEMA_VERSION",
    "FreshnessRegistration",
    "LicenseRegistration",
    "OperationalRegistration",
    "OwnerRegistration",
    "ProvenanceRegistration",
    "QualityRegistration",
    "RegistrationStatus",
    "RegistrySourceStatus",
    "SchemaRegistration",
    "VersionRegistration",
    "build_data_catalog",
    "collect_catalog_health",
    "evaluate_catalog_record",
    "freshness_from_health",
    "inspect_source_catalog",
    "load_owner_roles",
    "operational_from_health",
    "unavailable_data_catalog",
)
