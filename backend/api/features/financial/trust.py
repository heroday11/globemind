"""Trust and freshness gate for the financial terminal dashboard."""
from __future__ import annotations

import copy
import hashlib
import json
import math
import re
from datetime import datetime, timezone
from typing import Any

from api.core.environment import float_setting

TRUST_SCHEMA_VERSION = "financial-trust-v1"
COMPOSITE_METHOD_VERSION = "world-state-composite-v0.9.0"
MODEL_VERSION = "deterministic-ruleset-v0.9.0"
HARD_MINIMUM_SOURCE_COVERAGE = 0.5
MAXIMUM_FUTURE_CLOCK_SKEW_SECONDS = 300
MAX_SOURCE_RECORDS = 128
MAX_SOURCE_OBSERVATIONS = 2**63 - 1
SOURCE_ID_PATTERN = re.compile(r"[a-z0-9][a-z0-9._:-]{0,127}")
COMPOSITE_METHOD_CARD_SCHEMA_VERSION = "financial-composite-method-card-v1"
DERIVED_CLAIM_IDENTITY_SCHEMA_VERSION = "financial-derived-claim-identity-v1"
DERIVED_CLAIM_ID_PREFIX = "fdc_"
DERIVED_METRIC_ID_PATTERN = re.compile(r"IDX-[A-Z0-9][A-Z0-9._:-]{0,123}\Z")
SHORT_SAMPLE_TREND_SCHEMA_VERSION = "financial-short-sample-trend-v1"
SHORT_SAMPLE_TREND_METHOD_CARD_SCHEMA_VERSION = (
    "financial-short-sample-trend-method-card-v1"
)
MAX_TREND_DISCLOSURE_POINTS = 4096
MAX_TREND_POINT_TIMESTAMP = 253_402_300_799
MAX_ABSOLUTE_TREND_POINT_VALUE = 1_000_000_000_000_000.0
WSI_COMPONENT_ORDER = (
    "diplomacy",
    "security",
    "energy",
    "supply",
    "technology",
    "society",
    "macro",
)
WSI_COMPONENT_WEIGHTS = {
    "diplomacy": 0.14,
    "security": 0.20,
    "energy": 0.14,
    "supply": 0.15,
    "technology": 0.12,
    "society": 0.13,
    "macro": 0.12,
}
MINIMUM_SOURCE_COVERAGE = max(
    HARD_MINIMUM_SOURCE_COVERAGE,
    min(
        1.0,
        float_setting(
            "FINANCIAL_TERMINAL_MIN_SOURCE_COVERAGE",
            0.5,
            minimum=0.0,
        ),
    ),
)

_COMPOSITE_METRIC_PREFIX = "IDX-"
_CRITICAL_GROUPS: tuple[dict[str, Any], ...] = (
    {
        "id": "current-events",
        "source_ids": ("ground-news-local", "gdelt"),
        "minimum_available": 1,
        "requires_records": True,
        "index_ids": (
            "IDX-DIPLOMACY",
            "IDX-SECURITY",
            "IDX-ENERGY",
            "IDX-SUPPLY",
            "IDX-TECH",
            "IDX-SOCIETY",
            "IDX-WSI",
        ),
    },
    {
        "id": "macro-baseline",
        "source_ids": ("worldbank-gdp", "worldbank-inflation"),
        "minimum_available": 2,
        "requires_records": True,
        "index_ids": ("IDX-MACRO", "IDX-WSI"),
    },
    {
        "id": "security-context",
        "source_ids": ("usgs-earthquake", "worldbank-military", "un-sanctions"),
        "minimum_available": 1,
        "requires_records": True,
        "index_ids": ("IDX-SECURITY", "IDX-WSI"),
    },
    {
        "id": "energy-baseline",
        "source_ids": ("eia", "worldbank-electricity"),
        "minimum_available": 1,
        "requires_records": True,
        "index_ids": ("IDX-ENERGY", "IDX-WSI"),
    },
    {
        "id": "logistics-context",
        "source_ids": ("opensky", "worldbank-trade"),
        "minimum_available": 1,
        "requires_records": True,
        "index_ids": ("IDX-SUPPLY", "IDX-WSI"),
    },
    {
        "id": "technology-context",
        "source_ids": ("openalex-tech", "nvd", "cisa-kev", "first-epss", "noaa-kp"),
        "minimum_available": 2,
        "requires_records": True,
        "index_ids": ("IDX-TECH", "IDX-WSI"),
    },
    {
        "id": "society-context",
        "source_ids": ("usgs-earthquake", "nasa-eonet", "gdacs", "openaq", "nasa-firms"),
        "minimum_available": 2,
        "requires_records": True,
        "index_ids": ("IDX-SOCIETY", "IDX-WSI"),
    },
)

_COMPOSITE_METHOD_CARD: dict[str, Any] = {
    "schema_version": COMPOSITE_METHOD_CARD_SCHEMA_VERSION,
    "method_version": COMPOSITE_METHOD_VERSION,
    "implementation_status": "prototype_code_extracted",
    "approval_status": "not_approved",
    "formula_status": "partially_extracted_not_governed",
    "input_units_status": "not_dimensionally_validated",
    "baseline_status": "not_established",
    "threshold_status": "not_approved",
    "normalization": {
        "status": "partially_extracted_not_governed",
        "count_transform": "min(cap, base + ln(1 + count) * scale)",
        "non_positive_fallback": "base * 0.65",
        "parameters": "vary_by_subindex_in_implementation",
    },
    "frequency_alignment": {
        "status": "not_approved",
        "anchor_selection": "longest_non_empty_input_array",
        "interpolation": "linear_by_array_position_not_observation_timestamp",
        "empty_input": "zero_fill",
        "singleton_input": "repeat_single_value",
    },
    "missing_value_policy": "not_established",
    "revision_policy": "not_established",
    "wsi_aggregation": {
        "status": "extracted_from_deterministic_implementation",
        "component_order": list(WSI_COMPONENT_ORDER),
        "weights": dict(WSI_COMPONENT_WEIGHTS),
        "output_unit": "指数",
    },
    "test_vectors": [
        {
            "id": "wsi-equal-components-v1",
            "inputs": [50.0, 50.0, 50.0, 50.0, 50.0, 50.0, 50.0],
            "expected": 50.0,
        },
        {
            "id": "wsi-ordered-components-v1",
            "inputs": [10.0, 20.0, 30.0, 40.0, 50.0, 60.0, 70.0],
            "expected": 37.8,
        },
    ],
}

_SHORT_SAMPLE_TREND_METHOD_CARD: dict[str, Any] = {
    "schema_version": SHORT_SAMPLE_TREND_METHOD_CARD_SCHEMA_VERSION,
    "statistical_method_version": None,
    "implementation_status": "disclosure_gate_only",
    "approval_status": "not_approved",
    "baseline_period_status": "not_established",
    "sample_size_semantics": "provided_series_point_count_only",
    "independence_status": "not_validated",
    "minimum_sample_size": None,
    "uncertainty_method_status": "not_established",
    "confidence_level": None,
    "outlier_policy_status": "not_established",
    "release_rule": "suppress_change_pct_until_approved_method",
    "maximum_provided_points": MAX_TREND_DISCLOSURE_POINTS,
}


def calculate_extracted_wsi(component_values: Any) -> float:
    """Evaluate only the WSI aggregation that is directly bound to current code."""
    if not isinstance(component_values, (list, tuple)):
        raise ValueError("WSI components must be an ordered bounded sequence")
    if len(component_values) != len(WSI_COMPONENT_ORDER):
        raise ValueError("WSI requires exactly seven component values")
    normalized: list[float] = []
    for value in component_values:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("WSI components must be finite numbers")
        number = float(value)
        if not math.isfinite(number) or not 0 <= number <= 100:
            raise ValueError("WSI components must be finite values in [0, 100]")
        normalized.append(number)
    return sum(
        normalized[index] * WSI_COMPONENT_WEIGHTS[component]
        for index, component in enumerate(WSI_COMPONENT_ORDER)
    )


def composite_method_card() -> dict[str, Any]:
    """Return a defensive copy of the exact, deliberately unapproved method card."""
    return copy.deepcopy(_COMPOSITE_METHOD_CARD)


def short_sample_trend_method_card() -> dict[str, Any]:
    """Return the bounded disclosure gate, not an invented statistical method."""
    return copy.deepcopy(_SHORT_SAMPLE_TREND_METHOD_CARD)


def _composite_method_card_matches(value: Any) -> bool:
    return isinstance(value, dict) and value == _COMPOSITE_METHOD_CARD


def _short_sample_trend_method_card_matches(value: Any) -> bool:
    return isinstance(value, dict) and value == _SHORT_SAMPLE_TREND_METHOD_CARD


def _parse_timestamp(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(float(value), tz=timezone.utc)
        raw = str(value).strip()
        if not raw:
            return None
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except (OverflowError, TypeError, ValueError):
        return None


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _maximum_age_hours(cadence: Any) -> float:
    raw = str(cadence or "").strip().lower()
    if "annual" in raw:
        return 24 * 1460
    if "3d" in raw:
        return 24 * 7
    if "daily" in raw or raw in {"1d", "day"}:
        return 72
    if any(token in raw for token in ("15m", "30m", "1h")):
        return 6
    if any(token in raw for token in ("2h", "3h", "5h")):
        return 12
    return 72


def _record_count(source: dict[str, Any]) -> int:
    value = source.get("records")
    if isinstance(value, bool) or not isinstance(value, int):
        return 0
    if value <= 0 or value > MAX_SOURCE_OBSERVATIONS:
        return 0
    return value


def _source_freshness(source: dict[str, Any], now: datetime) -> str:
    status = str(source.get("status") or "").strip().lower()
    if status == "mock":
        return "mock"
    if status in {"disabled", "offline"}:
        return "offline"

    detail = str(source.get("detail") or "").lower()
    observed_at = _parse_timestamp(source.get("last_updated"))
    if observed_at is None:
        return "offline"
    age_seconds = (now - observed_at).total_seconds()
    if age_seconds < -MAXIMUM_FUTURE_CLOCK_SKEW_SECONDS:
        return "offline"
    age_hours = max(0.0, age_seconds / 3600)
    too_old = age_hours > _maximum_age_hours(source.get("cadence"))

    if too_old or "stale cache" in detail:
        return "stale"
    if status in {"degraded", "delayed"}:
        return "delayed"
    if status == "live":
        return "live"
    return "offline"


def _reason(code: str, message: str, **context: Any) -> dict[str, Any]:
    return {"code": code, "message": message, **context}


def _normalize_source_records(
    raw_sources: Any,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    reasons: list[dict[str, Any]] = []
    if not isinstance(raw_sources, list):
        return [], [
            _reason(
                "INVALID_SOURCE_INVENTORY",
                "Financial source inventory must be a bounded JSON array.",
            )
        ]

    if len(raw_sources) > MAX_SOURCE_RECORDS:
        reasons.append(
            _reason(
                "SOURCE_INVENTORY_TOO_LARGE",
                "Financial source inventory exceeds its declared processing bound.",
                actual=len(raw_sources),
                maximum=MAX_SOURCE_RECORDS,
            )
        )

    normalized: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    duplicate_ids: set[str] = set()
    invalid_count = 0
    for raw in raw_sources[:MAX_SOURCE_RECORDS]:
        if not isinstance(raw, dict):
            invalid_count += 1
            continue
        source_id = raw.get("id")
        if (
            not isinstance(source_id, str)
            or SOURCE_ID_PATTERN.fullmatch(source_id) is None
        ):
            invalid_count += 1
            continue
        if source_id in seen_ids:
            duplicate_ids.add(source_id)
            continue
        seen_ids.add(source_id)
        normalized.append(dict(raw))

    if invalid_count:
        reasons.append(
            _reason(
                "INVALID_SOURCE_RECORD",
                "Financial source inventory contains malformed records.",
                invalid_count=invalid_count,
            )
        )
    if duplicate_ids:
        reasons.append(
            _reason(
                "DUPLICATE_SOURCE_ID",
                "Financial source inventory contains duplicate stable IDs.",
                source_ids=sorted(duplicate_ids),
            )
        )
    return normalized, reasons


def _annotate_sources(
    sources: list[dict[str, Any]],
    *,
    now: datetime,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    summary = {"live": 0, "delayed": 0, "stale": 0, "offline": 0, "mock": 0}
    annotated: list[dict[str, Any]] = []
    for raw in sources:
        source = dict(raw)
        freshness = _source_freshness(source, now)
        source["freshness_status"] = freshness
        source["data_as_of"] = source.get("last_updated") or None
        record_count = _record_count(source)
        usable = freshness == "live" and record_count > 0
        source["contribution_state"] = "usable" if usable else "not_usable"
        if usable:
            source["contribution_reason_code"] = None
        elif freshness == "mock":
            source["contribution_reason_code"] = "EXPLICIT_MOCK_SOURCE"
        elif freshness != "live":
            source["contribution_reason_code"] = f"SOURCE_{freshness.upper()}"
        else:
            source["contribution_reason_code"] = "NO_POSITIVE_RECORD_COUNT"
        summary[freshness] += 1
        annotated.append(source)
    return annotated, summary


def _critical_group_state(
    sources_by_id: dict[str, dict[str, Any]],
    group: dict[str, Any],
) -> dict[str, Any]:
    candidates = [
        sources_by_id[source_id]
        for source_id in group["source_ids"]
        if source_id in sources_by_id
    ]
    present: list[dict[str, Any]] = []
    stale: list[dict[str, Any]] = []
    for source in candidates:
        has_records = not group["requires_records"] or _record_count(source) > 0
        if source.get("freshness_status") == "stale" and has_records:
            stale.append(source)
        if source.get("freshness_status") == "live" and has_records:
            present.append(source)

    cutoff_candidates = sorted(
        [
            timestamp
            for timestamp in (
                _parse_timestamp(source.get("last_updated")) for source in present
            )
            if timestamp is not None
        ],
        reverse=True,
    )
    required = int(group["minimum_available"])
    cutoff = cutoff_candidates[required - 1] if len(cutoff_candidates) >= required else None
    return {
        "id": group["id"],
        "required": required,
        "available": len(present),
        "source_ids": list(group["source_ids"]),
        "index_ids": list(group.get("index_ids") or ()),
        "available_source_ids": [str(source.get("id") or "") for source in present],
        "stale_source_ids": [str(source.get("id") or "") for source in stale],
        "as_of": _iso(cutoff),
    }


def _snapshot_id(payload: dict[str, Any], sources: list[dict[str, Any]]) -> str:
    evidence = {
        "last_updated": payload.get("last_updated"),
        "method_version": COMPOSITE_METHOD_VERSION,
        "model_version": MODEL_VERSION,
        "sources": [
            {
                "id": source.get("id"),
                "status": source.get("status"),
                "freshness_status": source.get("freshness_status"),
                "records": _record_count(source),
                "last_updated": source.get("last_updated"),
            }
            for source in sorted(sources, key=lambda item: str(item.get("id") or ""))
        ],
    }
    canonical = json.dumps(evidence, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return f"fin-{hashlib.sha256(canonical.encode('utf-8')).hexdigest()[:16]}"


def assess_dashboard_trust(
    payload: dict[str, Any],
    *,
    cache_state: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Return the single trust decision used by every financial client."""
    evaluated_at = now or datetime.now(timezone.utc)
    if evaluated_at.tzinfo is None:
        evaluated_at = evaluated_at.replace(tzinfo=timezone.utc)
    evaluated_at = evaluated_at.astimezone(timezone.utc)

    normalized_sources, inventory_reasons = _normalize_source_records(
        payload.get("sources")
    )
    sources, source_summary = _annotate_sources(
        normalized_sources,
        now=evaluated_at,
    )
    sources_by_id = {str(source.get("id") or ""): source for source in sources}
    critical_groups = [
        _critical_group_state(sources_by_id, group) for group in _CRITICAL_GROUPS
    ]

    non_mock_sources = [
        source for source in sources if source.get("freshness_status") != "mock"
    ]
    source_total = len(non_mock_sources)
    usable_source_rows = [
        source
        for source in non_mock_sources
        if source.get("freshness_status") == "live" and _record_count(source) > 0
    ]
    usable_sources = len(usable_source_rows)
    coverage_ratio = usable_sources / source_total if source_total else 0.0
    usable_source_ids = sorted(
        str(source["id"]) for source in usable_source_rows
    )
    unavailable_source_ids = sorted(
        str(source["id"])
        for source in non_mock_sources
        if source.get("contribution_state") != "usable"
    )
    reasons: list[dict[str, Any]] = list(inventory_reasons)

    if cache_state == "stale":
        reasons.append(
            _reason(
                "STALE_DASHBOARD_CACHE",
                "Dashboard cache has expired; composite scores and alerts are suppressed.",
            )
        )
    elif cache_state == "invalid":
        reasons.append(
            _reason(
                "INVALID_CACHED_TRUST_CONTRACT",
                "Cached financial data has no complete matching trust contract.",
            )
        )

    for group in critical_groups:
        if group["available"] >= group["required"]:
            continue
        code = "CRITICAL_INPUT_STALE" if group["stale_source_ids"] else "CRITICAL_INPUT_MISSING"
        reasons.append(
            _reason(
                code,
                f"Critical input group {group['id']} does not meet its availability gate.",
                group=group["id"],
                required=group["required"],
                available=group["available"],
                source_ids=group["source_ids"],
                index_ids=group["index_ids"],
            )
        )

    if coverage_ratio < MINIMUM_SOURCE_COVERAGE:
        reasons.append(
            _reason(
                "INSUFFICIENT_SOURCE_COVERAGE",
                "Available source coverage is below the composite-score threshold.",
                actual=round(coverage_ratio, 4),
                required=round(MINIMUM_SOURCE_COVERAGE, 4),
            )
        )

    method_card = composite_method_card()
    trend_method_card = short_sample_trend_method_card()
    if method_card["approval_status"] != "approved":
        reasons.append(
            _reason(
                "COMPOSITE_METHOD_NOT_APPROVED",
                (
                    "The prototype composite method has no approved complete method "
                    "definition; precise scores and alert evaluation are suppressed."
                ),
            )
        )

    critical_cutoffs = [
        timestamp
        for timestamp in (_parse_timestamp(group.get("as_of")) for group in critical_groups)
        if timestamp is not None
    ]
    data_as_of = min(critical_cutoffs) if critical_cutoffs else None
    computable = not reasons
    snapshot_id = _snapshot_id(payload, sources)

    if cache_state == "stale" or any(
        reason["code"] == "CRITICAL_INPUT_STALE" for reason in reasons
    ):
        freshness_status = "stale"
    elif usable_sources == 0:
        freshness_status = "offline"
    elif (
        source_summary["delayed"]
        or source_summary["stale"]
        or usable_sources < source_total
        or (
            data_as_of is not None
            and (evaluated_at - data_as_of).total_seconds() > 48 * 3600
        )
    ):
        freshness_status = "delayed"
    else:
        freshness_status = "live"

    if not computable:
        trust_status = "unavailable"
    elif freshness_status == "live":
        trust_status = "trusted"
    else:
        trust_status = "limited"

    return {
        "schema_version": TRUST_SCHEMA_VERSION,
        "snapshot_id": snapshot_id,
        "trust_status": trust_status,
        "freshness_status": freshness_status,
        "computability": "computable" if computable else "not_computable",
        "computable": computable,
        "data_as_of": _iso(data_as_of),
        "evaluated_at": _iso(evaluated_at),
        "coverage_ratio": round(coverage_ratio, 4),
        "minimum_coverage_ratio": round(MINIMUM_SOURCE_COVERAGE, 4),
        "usable_sources": usable_sources,
        "source_total": source_total,
        "usable_source_ids": usable_source_ids,
        "unavailable_source_ids": unavailable_source_ids,
        "source_status": source_summary,
        "critical_inputs": critical_groups,
        "model_version": MODEL_VERSION,
        "method_version": COMPOSITE_METHOD_VERSION,
        "composite_method_card": method_card,
        "short_sample_trend_method_card": trend_method_card,
        "unavailable_reasons": reasons,
        "alerts_enabled": computable,
        "method": {
            "coverage_numerator": (
                "sources with live, cadence-valid, non-empty observations"
            ),
            "coverage_denominator": "configured non-mock sources",
            "critical_groups": [group["id"] for group in _CRITICAL_GROUPS],
            "source_inventory_bound": MAX_SOURCE_RECORDS,
            "source_weighting": "not_established",
            "contribution_semantics": (
                "availability_gate_only_not_numeric_attribution"
            ),
        },
        "sources": sources,
    }


def _index_metadata(trust: dict[str, Any]) -> dict[str, Any]:
    return {
        "availability": (
            "available" if trust["computable"] else "not_computable"
        ),
        "trust_status": trust["trust_status"],
        "freshness_status": trust["freshness_status"],
        "data_as_of": trust["data_as_of"],
        "coverage_ratio": trust["coverage_ratio"],
        "model_version": trust["model_version"],
        "method_version": trust["method_version"],
        "schema_version": trust["schema_version"],
        "snapshot_id": trust["snapshot_id"],
        "unavailable_reasons": trust["unavailable_reasons"],
    }


def _bounded_series_point_count(value: Any) -> int | None:
    """Count only a small, structurally valid array of provided chart points.

    This is deliberately not called a count of independent observations.  The
    current prototype can interpolate and repeat values by array position, so
    the stronger statistical interpretation is unavailable.
    """
    if not isinstance(value, list) or len(value) > MAX_TREND_DISCLOSURE_POINTS:
        return None
    for point in value:
        if not isinstance(point, dict) or set(point) != {"time", "value"}:
            return None
        point_time = point["time"]
        point_value = point["value"]
        if (
            isinstance(point_time, bool)
            or not isinstance(point_time, int)
            or not 0 <= point_time <= MAX_TREND_POINT_TIMESTAMP
            or isinstance(point_value, bool)
            or not isinstance(point_value, (int, float))
            or not math.isfinite(float(point_value))
            or abs(float(point_value)) > MAX_ABSOLUTE_TREND_POINT_VALUE
        ):
            return None
    return len(value)


def _composite_series_point_counts(payload: dict[str, Any]) -> dict[str, int | None]:
    rows = payload.get("series")
    if not isinstance(rows, list):
        return {}
    counts: dict[str, int | None] = {}
    duplicated: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        metric_id = row.get("id")
        if (
            not isinstance(metric_id, str)
            or DERIVED_METRIC_ID_PATTERN.fullmatch(metric_id) is None
        ):
            continue
        if metric_id in counts:
            duplicated.add(metric_id)
            continue
        counts[metric_id] = _bounded_series_point_count(row.get("points"))
    for metric_id in duplicated:
        counts[metric_id] = None
    return counts


def _short_sample_trend_disclosure(
    semantic_metric_id: Any,
    trust: dict[str, Any],
    *,
    provided_point_count: int | None,
) -> dict[str, Any]:
    metric_id = (
        semantic_metric_id
        if isinstance(semantic_metric_id, str)
        and DERIVED_METRIC_ID_PATTERN.fullmatch(semantic_metric_id) is not None
        else None
    )
    sample_available = (
        isinstance(provided_point_count, int)
        and not isinstance(provided_point_count, bool)
        and 0 <= provided_point_count <= MAX_TREND_DISCLOSURE_POINTS
    )
    reason_codes = [
        "BASELINE_PERIOD_NOT_ESTABLISHED",
        "TREND_METHOD_NOT_APPROVED",
        "UNCERTAINTY_METHOD_NOT_ESTABLISHED",
    ]
    if not sample_available:
        reason_codes.append("BOUNDED_SERIES_POINTS_NOT_AVAILABLE")
    return {
        "schema_version": SHORT_SAMPLE_TREND_SCHEMA_VERSION,
        "semantic_metric_id": metric_id,
        "snapshot_id": trust["snapshot_id"],
        "data_cutoff": trust["data_as_of"],
        "statistical_method_version": None,
        "approval_status": "not_approved",
        "trend_status": "not_computable",
        "baseline_period": {
            "status": "not_established",
            "start": None,
            "end": None,
        },
        "sample_size": {
            "status": (
                "provided_series_point_count"
                if sample_available
                else "not_available"
            ),
            "count": provided_point_count if sample_available else None,
            "unit": "provided_series_points",
            "independence_status": "not_validated",
        },
        "uncertainty": {
            "status": "not_computable",
            "confidence_level": None,
            "interval_lower": None,
            "interval_upper": None,
            "reason_code": "UNCERTAINTY_METHOD_NOT_ESTABLISHED",
        },
        "outlier_policy_status": "not_established",
        "reason_codes": reason_codes,
    }


def _short_sample_trend_disclosure_matches(
    row: Any,
    semantic_metric_id: Any,
    trust: dict[str, Any],
    *,
    provided_point_count: int | None,
) -> bool:
    if not isinstance(row, dict):
        return False
    return row.get("trend_disclosure") == _short_sample_trend_disclosure(
        semantic_metric_id,
        trust,
        provided_point_count=provided_point_count,
    )


def _derived_metric_claim(
    semantic_metric_id: Any,
    trust: dict[str, Any],
) -> dict[str, Any]:
    """Bind one composite output to code-owned semantic identity.

    A dashboard snapshot is not itself a verified evidence artifact.  Until a
    durable numerical-provenance record exists, the citation locator must stay
    explicitly unavailable instead of being inferred from display labels or
    source-inventory availability.
    """
    unavailable = {
        "claim_id": None,
        "claim_identity": None,
        "claim_unavailable_reason": "SEMANTIC_METRIC_ID_INVALID",
        "citation_locator": None,
        "citation_locator_state": "unavailable",
        "citation_unavailable_reason": (
            "VERIFIED_NUMERIC_EVIDENCE_LOCATOR_NOT_ESTABLISHED"
        ),
    }
    if (
        not isinstance(semantic_metric_id, str)
        or DERIVED_METRIC_ID_PATTERN.fullmatch(semantic_metric_id) is None
    ):
        return unavailable

    identity = {
        "schema_version": DERIVED_CLAIM_IDENTITY_SCHEMA_VERSION,
        "semantic_metric_id": semantic_metric_id,
        "metric_class": "composite_index",
        "method_version": trust["method_version"],
        "model_version": trust["model_version"],
        "snapshot_id": trust["snapshot_id"],
        "data_cutoff": trust["data_as_of"],
        "availability": (
            "available" if trust["computable"] else "not_computable"
        ),
    }
    canonical = json.dumps(
        identity,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return {
        "claim_id": f"{DERIVED_CLAIM_ID_PREFIX}{hashlib.sha256(canonical).hexdigest()}",
        "claim_identity": identity,
        "claim_unavailable_reason": None,
        "citation_locator": None,
        "citation_locator_state": "unavailable",
        "citation_unavailable_reason": (
            "VERIFIED_NUMERIC_EVIDENCE_LOCATOR_NOT_ESTABLISHED"
        ),
    }


def _derived_metric_claim_matches(
    row: Any,
    semantic_metric_id: Any,
    trust: dict[str, Any],
) -> bool:
    if not isinstance(row, dict):
        return False
    expected = _derived_metric_claim(semantic_metric_id, trust)
    if expected["claim_id"] is None:
        return False
    return all(row.get(key) == value for key, value in expected.items())


def apply_dashboard_trust_gate(
    payload: dict[str, Any],
    *,
    cache_state: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Attach trust metadata and remove precise composite outputs when unsafe."""
    output = copy.deepcopy(payload)
    output["cache"] = cache_state
    trust = assess_dashboard_trust(output, cache_state=cache_state, now=now)
    output["trust"] = {key: value for key, value in trust.items() if key != "sources"}
    output["sources"] = trust["sources"]
    output["trust_status"] = trust["trust_status"]
    output["freshness_status"] = trust["freshness_status"]
    output["computability"] = trust["computability"]
    output["computable"] = trust["computable"]
    output["alerts_enabled"] = trust["alerts_enabled"]
    output["data_as_of"] = trust["data_as_of"]
    output["model_version"] = trust["model_version"]
    output["method_version"] = trust["method_version"]
    output["composite_method_card"] = trust["composite_method_card"]
    output["short_sample_trend_method_card"] = trust[
        "short_sample_trend_method_card"
    ]
    output["schema_version"] = trust["schema_version"]
    output["snapshot_id"] = trust["snapshot_id"]
    output["unavailable_reasons"] = trust["unavailable_reasons"]
    output["mode"] = (
        "live"
        if trust["freshness_status"] == "live"
        else "delayed"
        if trust["computable"]
        else "historical"
        if trust["freshness_status"] in {"delayed", "stale"}
        else "unavailable"
    )

    coverage = dict(output.get("coverage") or {})
    coverage.update(
        {
            "coverage_ratio": trust["coverage_ratio"],
            "minimum_coverage_ratio": trust["minimum_coverage_ratio"],
            "usable_sources": trust["usable_sources"],
            "sources_total": trust["source_total"],
            "source_status": trust["source_status"],
        }
    )
    output["coverage"] = coverage

    metadata = _index_metadata(trust)
    point_counts = _composite_series_point_counts(output)
    for index in output.get("indices") or []:
        if not isinstance(index, dict):
            continue
        index.update(metadata)
        semantic_metric_id = index.get("metric_id")
        claim = _derived_metric_claim(semantic_metric_id, trust)
        index.update(claim)
        index["trend_disclosure"] = _short_sample_trend_disclosure(
            semantic_metric_id,
            trust,
            provided_point_count=point_counts.get(str(semantic_metric_id)),
        )
        if not trust["computable"] or claim["claim_id"] is None:
            index["availability"] = "not_computable"
            index["value"] = None
            index["change_pct"] = None
            index["spark"] = []

    for metric in output.get("series") or []:
        if not isinstance(metric, dict) or not str(metric.get("id") or "").startswith(
            _COMPOSITE_METRIC_PREFIX
        ):
            continue
        metric.update(metadata)
        semantic_metric_id = metric.get("id")
        claim = _derived_metric_claim(semantic_metric_id, trust)
        metric.update(claim)
        metric["trend_disclosure"] = _short_sample_trend_disclosure(
            semantic_metric_id,
            trust,
            provided_point_count=point_counts.get(str(semantic_metric_id)),
        )
        if not trust["computable"] or claim["claim_id"] is None:
            metric["availability"] = "not_computable"
            metric["latest"] = None
            metric["change_pct"] = None
            metric["status"] = "unavailable"
            metric["points"] = []

    if not trust["computable"]:
        output["bars"] = []
        output["ma20"] = []
        output["ma50"] = []
        output["ma200"] = []
        output["alert_rules"] = []
        output["alerts_suppressed"] = True
    else:
        output["alerts_suppressed"] = False
    return output


def _valid_source_id_list(value: Any) -> bool:
    if not isinstance(value, list) or len(value) > MAX_SOURCE_RECORDS:
        return False
    if value != sorted(value) or len(value) != len(set(value)):
        return False
    return all(
        isinstance(source_id, str)
        and SOURCE_ID_PATTERN.fullmatch(source_id) is not None
        for source_id in value
    )


def _source_inventory_matches_trust(
    dashboard: dict[str, Any],
    trust: dict[str, Any],
) -> bool:
    source_total = trust.get("source_total")
    usable_sources = trust.get("usable_sources")
    usable_source_ids = trust.get("usable_source_ids")
    unavailable_source_ids = trust.get("unavailable_source_ids")
    if (
        isinstance(source_total, bool)
        or not isinstance(source_total, int)
        or not 0 < source_total <= MAX_SOURCE_RECORDS
        or isinstance(usable_sources, bool)
        or not isinstance(usable_sources, int)
        or not 0 < usable_sources <= source_total
        or not _valid_source_id_list(usable_source_ids)
        or not _valid_source_id_list(unavailable_source_ids)
        or len(usable_source_ids) != usable_sources
        or len(unavailable_source_ids) != source_total - usable_sources
        or set(usable_source_ids) & set(unavailable_source_ids)
    ):
        return False

    method = trust.get("method")
    if (
        not isinstance(method, dict)
        or method.get("source_inventory_bound") != MAX_SOURCE_RECORDS
        or method.get("source_weighting") != "not_established"
        or method.get("contribution_semantics")
        != "availability_gate_only_not_numeric_attribution"
    ):
        return False

    raw_sources = dashboard.get("sources")
    if not isinstance(raw_sources, list) or len(raw_sources) > MAX_SOURCE_RECORDS:
        return False
    seen_ids: set[str] = set()
    computed_usable_ids: list[str] = []
    computed_unavailable_ids: list[str] = []
    computed_status = {
        "live": 0,
        "delayed": 0,
        "stale": 0,
        "offline": 0,
        "mock": 0,
    }
    for source in raw_sources:
        if not isinstance(source, dict):
            return False
        source_id = source.get("id")
        freshness = source.get("freshness_status")
        if (
            not isinstance(source_id, str)
            or SOURCE_ID_PATTERN.fullmatch(source_id) is None
            or source_id in seen_ids
            or freshness not in computed_status
        ):
            return False
        seen_ids.add(source_id)
        computed_status[freshness] += 1
        usable = freshness == "live" and _record_count(source) > 0
        expected_state = "usable" if usable else "not_usable"
        if source.get("contribution_state") != expected_state:
            return False
        if freshness == "mock":
            continue
        if usable:
            computed_usable_ids.append(source_id)
        else:
            computed_unavailable_ids.append(source_id)

    if len(computed_usable_ids) + len(computed_unavailable_ids) != source_total:
        return False
    if sorted(computed_usable_ids) != usable_source_ids:
        return False
    if sorted(computed_unavailable_ids) != unavailable_source_ids:
        return False

    source_status = trust.get("source_status")
    if not isinstance(source_status, dict) or set(source_status) - set(computed_status):
        return False
    for status, expected in computed_status.items():
        value = source_status.get(status, 0)
        if isinstance(value, bool) or not isinstance(value, int) or value != expected:
            return False
    return True


def dashboard_is_computable(dashboard: dict[str, Any]) -> bool:
    """Only accept a complete, internally consistent trust contract."""
    if not isinstance(dashboard, dict):
        return False
    trust = dashboard.get("trust")
    if not isinstance(trust, dict):
        return False
    if trust.get("schema_version") != TRUST_SCHEMA_VERSION:
        return False
    if dashboard.get("schema_version") != TRUST_SCHEMA_VERSION:
        return False
    snapshot_id = trust.get("snapshot_id")
    if not isinstance(snapshot_id, str) or not snapshot_id:
        return False
    if dashboard.get("snapshot_id") != snapshot_id:
        return False
    expected_pairs = (
        ("trust_status", {"trusted", "limited"}),
        ("freshness_status", {"live", "delayed"}),
        ("model_version", {MODEL_VERSION}),
        ("method_version", {COMPOSITE_METHOD_VERSION}),
    )
    for key, allowed in expected_pairs:
        if trust.get(key) not in allowed or dashboard.get(key) != trust.get(key):
            return False
    method_card = trust.get("composite_method_card")
    if not _composite_method_card_matches(method_card):
        return False
    if dashboard.get("composite_method_card") != method_card:
        return False
    if method_card.get("approval_status") != "approved":
        return False
    trend_method_card = trust.get("short_sample_trend_method_card")
    if not _short_sample_trend_method_card_matches(trend_method_card):
        return False
    if dashboard.get("short_sample_trend_method_card") != trend_method_card:
        return False
    if trend_method_card.get("approval_status") != "approved":
        return False
    if dashboard.get("data_as_of") != trust.get("data_as_of"):
        return False
    if not isinstance(trust.get("data_as_of"), str) or not trust["data_as_of"]:
        return False
    if trust.get("computability") != "computable":
        return False
    if trust.get("computable") is not True or trust.get("alerts_enabled") is not True:
        return False
    if dashboard.get("computability") != trust.get("computability"):
        return False
    if dashboard.get("computable") is not trust.get("computable"):
        return False
    if dashboard.get("alerts_enabled") is not trust.get("alerts_enabled"):
        return False
    if trust.get("unavailable_reasons") not in ([], ()):  # malformed or contradictory
        return False
    if dashboard.get("unavailable_reasons") != trust.get("unavailable_reasons"):
        return False
    raw_indices = dashboard.get("indices")
    if not isinstance(raw_indices, list) or not raw_indices:
        return False
    point_counts = _composite_series_point_counts(dashboard)
    if any(
        not _derived_metric_claim_matches(index, index.get("metric_id"), trust)
        for index in raw_indices
        if isinstance(index, dict)
    ) or any(not isinstance(index, dict) for index in raw_indices):
        return False
    if any(
        not _short_sample_trend_disclosure_matches(
            index,
            index.get("metric_id"),
            trust,
            provided_point_count=point_counts.get(str(index.get("metric_id"))),
        )
        for index in raw_indices
    ):
        return False
    raw_series = dashboard.get("series")
    if not isinstance(raw_series, list):
        return False
    for metric in raw_series:
        if not isinstance(metric, dict):
            return False
        metric_id = metric.get("id")
        if str(metric_id or "").startswith(_COMPOSITE_METRIC_PREFIX) and not (
            _derived_metric_claim_matches(metric, metric_id, trust)
        ):
            return False
        if str(metric_id or "").startswith(_COMPOSITE_METRIC_PREFIX) and not (
            _short_sample_trend_disclosure_matches(
                metric,
                metric_id,
                trust,
                provided_point_count=point_counts.get(str(metric_id)),
            )
        ):
            return False
    coverage_ratio = trust.get("coverage_ratio")
    minimum_coverage = trust.get("minimum_coverage_ratio")
    usable_sources = trust.get("usable_sources")
    if (
        isinstance(coverage_ratio, bool)
        or not isinstance(coverage_ratio, (int, float))
        or not math.isfinite(coverage_ratio)
        or not 0 <= coverage_ratio <= 1
    ):
        return False
    if (
        isinstance(minimum_coverage, bool)
        or not isinstance(minimum_coverage, (int, float))
        or not math.isfinite(minimum_coverage)
        or not HARD_MINIMUM_SOURCE_COVERAGE <= minimum_coverage <= 1
        or coverage_ratio < minimum_coverage
    ):
        return False
    if isinstance(usable_sources, bool) or not isinstance(usable_sources, int):
        return False
    if usable_sources <= 0:
        return False
    if not _source_inventory_matches_trust(dashboard, trust):
        return False
    coverage = dashboard.get("coverage")
    if not isinstance(coverage, dict):
        return False
    for key in (
        "coverage_ratio",
        "minimum_coverage_ratio",
        "usable_sources",
        "source_status",
    ):
        if coverage.get(key) != trust.get(key):
            return False
    if coverage.get("sources_total") != trust.get("source_total"):
        return False
    if dashboard.get("alerts_suppressed") is not False:
        return False
    return True


__all__ = (
    "COMPOSITE_METHOD_CARD_SCHEMA_VERSION",
    "COMPOSITE_METHOD_VERSION",
    "HARD_MINIMUM_SOURCE_COVERAGE",
    "MINIMUM_SOURCE_COVERAGE",
    "MODEL_VERSION",
    "SHORT_SAMPLE_TREND_METHOD_CARD_SCHEMA_VERSION",
    "SHORT_SAMPLE_TREND_SCHEMA_VERSION",
    "TRUST_SCHEMA_VERSION",
    "WSI_COMPONENT_ORDER",
    "WSI_COMPONENT_WEIGHTS",
    "apply_dashboard_trust_gate",
    "assess_dashboard_trust",
    "calculate_extracted_wsi",
    "composite_method_card",
    "dashboard_is_computable",
    "short_sample_trend_method_card",
)
