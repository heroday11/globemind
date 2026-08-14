"""Fail-closed trust policy for opinion scoring and composite responses.

The opinion feature may keep evidence and coverage visible when a composite is
unavailable, but it must never expose precise composite values unless the
freshness, coverage, provenance, and snapshot contract all validate.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
from datetime import date
from typing import Any, Iterable, Mapping

from api.features.opinion.constants import (
    DECAY_ALPHA,
    DECAY_MAX_LAG,
    DECAY_TAU_BASE,
    DECAY_TAU_SCALE,
    METHOD_VERSION,
)

OPINION_FRESHNESS_MAX_AGE_DAYS = 2
OPINION_MIN_ARTICLES = 10
OPINION_MIN_SOURCES = 3
OPINION_TRUST_SCHEMA_VERSION = "opinion-trust-v1"
OPINION_MODEL_VERSION = METHOD_VERSION
OPINION_SOURCE_ID = "public.china_opinion_article_scores"

_NULL_WHEN_UNTRUSTED = frozenset(
    {
        "avg_impact",
        "avg_stance",
        "average_weighted_stance_index",
        "change_24h",
        "china_importance",
        "china_index",
        "confidence",
        "current_index",
        "daily_impact",
        "directness_score",
        "growth_pct",
        "impact_abs",
        "impact_index",
        "l1_total_impact",
        "max_heat",
        "max_impact",
        "maximum_weighted_stance_index",
        "min_impact",
        "minimum_weighted_stance_index",
        "negative_pct",
        "neutral_pct",
        "positive_pct",
        "quality_score",
        "relevance_score",
        "sentiment",
        "stance_score",
        "weighted_stance_contribution",
        "weighted_stance_contribution_abs",
        "weighted_stance_index",
        "total_raw_daily",
    }
)
_CLEAR_WHEN_UNTRUSTED = frozenset({"trend_values"})
_UNAVAILABLE_WHEN_UNTRUSTED = frozenset({"severity"})


def _coerce_date(value: Any) -> date | None:
    if isinstance(value, date):
        return value
    if value in (None, ""):
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def _non_negative_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(number) or number < 0 or not number.is_integer():
        return None
    return int(number)


def _snapshot_identifier(payload: Mapping[str, Any]) -> str:
    raw = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return "opinion-" + hashlib.sha256(raw.encode()).hexdigest()


def _unique_reason_codes(values: Iterable[Any]) -> list[str]:
    result: list[str] = []
    for value in values:
        code = str(value or "").strip()
        if code and code not in result:
            result.append(code)
    return result


def evaluate_opinion_trust(
    *,
    current_date: date,
    cutoff_date: date | None,
    article_count: int,
    source_count: int | None,
    method_version: str = METHOD_VERSION,
    model_version: str = OPINION_MODEL_VERSION,
    minimum_articles: int = OPINION_MIN_ARTICLES,
    minimum_sources: int = OPINION_MIN_SOURCES,
    coverage_start: date | None = None,
    coverage_end: date | None = None,
    invalid_article_count: int = 0,
    rejected_article_count: int = 0,
    filters: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the complete, stable trust contract used by every opinion client."""

    normalized_articles = _non_negative_int(article_count)
    normalized_sources = _non_negative_int(source_count)
    normalized_invalid = _non_negative_int(invalid_article_count)
    normalized_rejected = _non_negative_int(rejected_article_count)
    normalized_minimum_articles = _non_negative_int(minimum_articles)
    normalized_minimum_sources = _non_negative_int(minimum_sources)
    contract_invalid = any(
        value is None
        for value in (
            normalized_articles,
            normalized_invalid,
            normalized_rejected,
            normalized_minimum_articles,
            normalized_minimum_sources,
        )
    )
    article_total = normalized_articles or 0
    invalid_total = normalized_invalid or 0
    rejected_total = normalized_rejected or 0
    minimum_article_total = normalized_minimum_articles or OPINION_MIN_ARTICLES
    minimum_source_total = normalized_minimum_sources or OPINION_MIN_SOURCES
    age_days = (current_date - cutoff_date).days if cutoff_date else None
    reason_codes: list[str] = []

    if contract_invalid:
        reason_codes.append("INVALID_COVERAGE_METADATA")
    if cutoff_date is None:
        reason_codes.append("MISSING_CUTOFF")
    elif age_days is not None and age_days > OPINION_FRESHNESS_MAX_AGE_DAYS:
        reason_codes.append("STALE_DATA")
    elif age_days is not None and age_days < 0:
        reason_codes.append("INVALID_FUTURE_CUTOFF")

    if article_total < minimum_article_total:
        reason_codes.append("LOW_ARTICLE_COVERAGE")
    if normalized_sources is None:
        reason_codes.append("MISSING_SOURCE_COVERAGE")
    elif normalized_sources < minimum_source_total:
        reason_codes.append("LOW_SOURCE_COVERAGE")
    if method_version != METHOD_VERSION or model_version != OPINION_MODEL_VERSION:
        reason_codes.append("METHOD_VERSION_MISMATCH")

    computable = not reason_codes
    freshness_state = (
        "missing"
        if cutoff_date is None
        else "invalid"
        if age_days is not None and age_days < 0
        else "stale"
        if age_days is not None and age_days > OPINION_FRESHNESS_MAX_AGE_DAYS
        else "fresh"
    )
    coverage_sufficient = (
        article_total >= minimum_article_total
        and normalized_sources is not None
        and normalized_sources >= minimum_source_total
    )
    source_state = "available" if cutoff_date is not None and article_total else "missing"
    snapshot_seed = {
        "schema_version": OPINION_TRUST_SCHEMA_VERSION,
        "current_date": current_date.isoformat(),
        "cutoff_date": cutoff_date.isoformat() if cutoff_date else None,
        "coverage_start": coverage_start.isoformat() if coverage_start else None,
        "coverage_end": coverage_end.isoformat() if coverage_end else None,
        "article_count": article_total,
        "source_count": normalized_sources,
        "method_version": method_version,
        "model_version": model_version,
        "filters": dict(filters or {}),
    }
    snapshot_id = _snapshot_identifier(snapshot_seed)
    method = {
        "version": method_version,
        "definition": (
            "confidence- and relevance-weighted targeted stance toward China, "
            "with bounded polynomial time decay"
        ),
        "output_scale": {"minimum": -100, "maximum": 100, "center": 0},
        "decay": {
            "maximum_lag_days": DECAY_MAX_LAG,
            "tau_base": DECAY_TAU_BASE,
            "tau_scale": DECAY_TAU_SCALE,
            "alpha": DECAY_ALPHA,
        },
        "coverage_numerator": (
            "valid, non-rejected filtered articles whose decayed weight contributes "
            "to the terminal observation"
        ),
        "coverage_denominator": "same terminal contribution window before trust thresholds",
    }
    source = {
        "id": OPINION_SOURCE_ID,
        "status": source_state,
        "cutoff_date": cutoff_date.isoformat() if cutoff_date else None,
        "valid_articles": article_total,
        "invalid_articles": invalid_total,
        "rejected_articles": rejected_total,
        "distinct_sources": normalized_sources,
    }
    snapshot = {
        "id": snapshot_id,
        "evaluated_on": current_date.isoformat(),
        "cutoff_date": cutoff_date.isoformat() if cutoff_date else None,
        "coverage_start": coverage_start.isoformat() if coverage_start else None,
        "coverage_end": coverage_end.isoformat() if coverage_end else None,
        "filters": dict(filters or {}),
    }

    return {
        "schema_version": OPINION_TRUST_SCHEMA_VERSION,
        "status": "ready" if computable else "unavailable",
        "trust_status": "trusted" if computable else "unavailable",
        "is_computable": computable,
        "computability": "computable" if computable else "not_computable",
        "display_mode": "current" if computable else "historical_context",
        "reason_codes": reason_codes,
        "cutoff_date": cutoff_date.isoformat() if cutoff_date else None,
        "freshness": {
            "state": freshness_state,
            "age_days": age_days,
            "maximum_age_days": OPINION_FRESHNESS_MAX_AGE_DAYS,
        },
        "coverage": {
            "state": "sufficient" if coverage_sufficient else "insufficient",
            "article_count": article_total,
            "source_count": normalized_sources,
            "minimum_articles": minimum_article_total,
            "minimum_sources": minimum_source_total,
            "article_threshold_ratio": round(
                min(1.0, article_total / max(1, minimum_article_total)), 4
            ),
            "source_threshold_ratio": (
                round(
                    min(1.0, normalized_sources / max(1, minimum_source_total)),
                    4,
                )
                if normalized_sources is not None
                else None
            ),
            "window_start": coverage_start.isoformat() if coverage_start else None,
            "window_end": coverage_end.isoformat() if coverage_end else None,
            "invalid_article_count": invalid_total,
            "rejected_article_count": rejected_total,
        },
        "model_version": model_version,
        "method_version": method_version,
        "source_status": source_state,
        "snapshot_id": snapshot_id,
        "model": {
            "version": model_version,
            "version_source": "china_opinion_article_scores.method_version",
            "separately_versioned": False,
            "output": "targeted_china_stance",
            "input_unit": "article",
        },
        "method": method,
        "source": source,
        "snapshot": snapshot,
    }


def _raw_trust(content: Mapping[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
    reasons: list[str] = []
    top = content.get("trust")
    meta = content.get("meta")
    nested = meta.get("trust") if isinstance(meta, Mapping) else None
    candidates = [item for item in (top, nested) if item is not None]
    if not candidates:
        return None, ["MISSING_TRUST_METADATA"]
    if any(not isinstance(item, Mapping) for item in candidates):
        return None, ["INVALID_TRUST_METADATA"]
    if len(candidates) == 2 and dict(candidates[0]) != dict(candidates[1]):
        reasons.append("CONFLICTING_TRUST_METADATA")
    return dict(candidates[0]), reasons


def _validated_trust(
    content: Mapping[str, Any],
    *,
    current_date: date,
    force_reason_codes: Iterable[str] = (),
) -> dict[str, Any]:
    raw, validation_reasons = _raw_trust(content)
    if raw is None:
        trust = evaluate_opinion_trust(
            current_date=current_date,
            cutoff_date=None,
            article_count=0,
            source_count=None,
        )
        trust["reason_codes"] = _unique_reason_codes(
            [*trust["reason_codes"], *validation_reasons, *force_reason_codes]
        )
        trust["is_computable"] = False
        trust["computability"] = "not_computable"
        trust["status"] = "unavailable"
        trust["trust_status"] = "unavailable"
        return trust

    coverage = raw.get("coverage")
    freshness = raw.get("freshness")
    source = raw.get("source")
    snapshot = raw.get("snapshot")
    model = raw.get("model")
    method = raw.get("method")
    if raw.get("schema_version") != OPINION_TRUST_SCHEMA_VERSION:
        validation_reasons.append("INVALID_SCHEMA_METADATA")
    if not isinstance(coverage, Mapping) or not isinstance(freshness, Mapping):
        validation_reasons.append("INVALID_TRUST_METADATA")
        coverage = {}
    if not isinstance(source, Mapping) or not source.get("id") or not source.get("status"):
        validation_reasons.append("MISSING_SOURCE_METADATA")
        source = {}
    if (
        not isinstance(snapshot, Mapping)
        or not snapshot.get("id")
        or not snapshot.get("evaluated_on")
    ):
        validation_reasons.append("MISSING_SNAPSHOT_METADATA")
        snapshot = {}
    if not isinstance(model, Mapping) or not model.get("version"):
        validation_reasons.append("MISSING_MODEL_METADATA")
        model = {}
    if not isinstance(method, Mapping) or not method.get("version"):
        validation_reasons.append("MISSING_METHOD_METADATA")
        method = {}
    if raw.get("is_computable") not in (True, False):
        validation_reasons.append("INVALID_COMPUTABILITY_METADATA")
    else:
        declared_computable = raw["is_computable"]
        if raw.get("computability") != (
            "computable" if declared_computable else "not_computable"
        ):
            validation_reasons.append("CONFLICTING_COMPUTABILITY_METADATA")
        expected_status = "ready" if declared_computable else "unavailable"
        expected_trust_status = "trusted" if declared_computable else "unavailable"
        if raw.get("status") != expected_status:
            validation_reasons.append("CONFLICTING_STATUS_METADATA")
        if raw.get("trust_status") != expected_trust_status:
            validation_reasons.append("CONFLICTING_STATUS_METADATA")
        if raw.get("display_mode") != (
            "current" if declared_computable else "historical_context"
        ):
            validation_reasons.append("CONFLICTING_DISPLAY_MODE_METADATA")

    cutoff = _coerce_date(raw.get("cutoff_date"))
    if raw.get("cutoff_date") not in (None, "") and cutoff is None:
        validation_reasons.append("INVALID_CUTOFF_METADATA")
    article_count = _non_negative_int(coverage.get("article_count"))
    source_count = _non_negative_int(coverage.get("source_count"))
    invalid_count = _non_negative_int(coverage.get("invalid_article_count"))
    rejected_count = _non_negative_int(coverage.get("rejected_article_count"))
    minimum_article_count = _non_negative_int(coverage.get("minimum_articles"))
    minimum_source_count = _non_negative_int(coverage.get("minimum_sources"))
    if (
        minimum_article_count != OPINION_MIN_ARTICLES
        or minimum_source_count != OPINION_MIN_SOURCES
    ):
        validation_reasons.append("CONFLICTING_COVERAGE_METADATA")
    coverage_start = _coerce_date(coverage.get("window_start"))
    coverage_end = _coerce_date(coverage.get("window_end"))

    model_version = str(raw.get("model_version") or model.get("version") or "")
    method_version = str(raw.get("method_version") or method.get("version") or "")
    if model_version != OPINION_MODEL_VERSION or method_version != METHOD_VERSION:
        validation_reasons.append("METHOD_VERSION_MISMATCH")
    if model.get("version") not in (None, model_version):
        validation_reasons.append("CONFLICTING_MODEL_METADATA")
    if method.get("version") not in (None, method_version):
        validation_reasons.append("CONFLICTING_METHOD_METADATA")

    snapshot_cutoff = _coerce_date(snapshot.get("cutoff_date"))
    snapshot_evaluated_on = _coerce_date(snapshot.get("evaluated_on"))
    source_cutoff = _coerce_date(source.get("cutoff_date"))
    if snapshot and snapshot_cutoff != cutoff:
        validation_reasons.append("CONFLICTING_SNAPSHOT_METADATA")
    if source and source_cutoff != cutoff:
        validation_reasons.append("CONFLICTING_SOURCE_METADATA")
    expected_source_state = (
        "available" if cutoff is not None and (article_count or 0) > 0 else "missing"
    )
    if source and (
        source.get("id") != OPINION_SOURCE_ID
        or source.get("status") != expected_source_state
    ):
        validation_reasons.append("CONFLICTING_SOURCE_METADATA")
    if snapshot and (
        _coerce_date(snapshot.get("coverage_start")) != coverage_start
        or _coerce_date(snapshot.get("coverage_end")) != coverage_end
    ):
        validation_reasons.append("CONFLICTING_SNAPSHOT_METADATA")
    if isinstance(freshness, Mapping):
        snapshot_age = (
            (snapshot_evaluated_on - cutoff).days
            if snapshot_evaluated_on is not None and cutoff is not None
            else None
        )
        snapshot_state = (
            "missing"
            if cutoff is None
            else "invalid"
            if snapshot_age is not None and snapshot_age < 0
            else "stale"
            if snapshot_age is not None
            and snapshot_age > OPINION_FRESHNESS_MAX_AGE_DAYS
            else "fresh"
        )
        if (
            freshness.get("age_days") != snapshot_age
            or freshness.get("state") != snapshot_state
            or freshness.get("maximum_age_days")
            != OPINION_FRESHNESS_MAX_AGE_DAYS
        ):
            validation_reasons.append("CONFLICTING_FRESHNESS_METADATA")
    if raw.get("snapshot_id") != snapshot.get("id"):
        validation_reasons.append("CONFLICTING_SNAPSHOT_METADATA")
    if raw.get("source_status") != source.get("status"):
        validation_reasons.append("CONFLICTING_SOURCE_METADATA")
    for value in (
        content.get("latest_date"),
        content.get("meta", {}).get("last_article_date")
        if isinstance(content.get("meta"), Mapping)
        else None,
    ):
        if value not in (None, "") and _coerce_date(value) != cutoff:
            validation_reasons.append("CONFLICTING_CUTOFF_METADATA")

    filters = snapshot.get("filters") if isinstance(snapshot.get("filters"), Mapping) else {}
    meta_filters = (
        content.get("meta", {}).get("filters")
        if isinstance(content.get("meta"), Mapping)
        else None
    )
    if isinstance(meta_filters, Mapping) and dict(meta_filters) != dict(filters):
        validation_reasons.append("CONFLICTING_SNAPSHOT_METADATA")
    if snapshot_evaluated_on is not None:
        expected_snapshot_id = _snapshot_identifier(
            {
                "schema_version": OPINION_TRUST_SCHEMA_VERSION,
                "current_date": snapshot_evaluated_on.isoformat(),
                "cutoff_date": cutoff.isoformat() if cutoff else None,
                "coverage_start": coverage_start.isoformat() if coverage_start else None,
                "coverage_end": coverage_end.isoformat() if coverage_end else None,
                "article_count": article_count or 0,
                "source_count": source_count,
                "method_version": method_version,
                "model_version": model_version,
                "filters": dict(filters),
            }
        )
        if snapshot.get("id") != expected_snapshot_id:
            validation_reasons.append("CONFLICTING_SNAPSHOT_METADATA")
    trust = evaluate_opinion_trust(
        current_date=current_date,
        cutoff_date=cutoff,
        article_count=article_count if article_count is not None else -1,
        source_count=source_count,
        method_version=method_version,
        model_version=model_version,
        minimum_articles=OPINION_MIN_ARTICLES,
        minimum_sources=OPINION_MIN_SOURCES,
        coverage_start=coverage_start,
        coverage_end=coverage_end,
        invalid_article_count=invalid_count if invalid_count is not None else -1,
        rejected_article_count=rejected_count if rejected_count is not None else -1,
        filters=filters,
    )
    if isinstance(coverage, Mapping) and coverage.get("state") != trust["coverage"]["state"]:
        validation_reasons.append("CONFLICTING_COVERAGE_METADATA")
    if source:
        expected_source = trust["source"]
        for field in (
            "valid_articles",
            "invalid_articles",
            "rejected_articles",
            "distinct_sources",
        ):
            if source.get(field) != expected_source.get(field):
                validation_reasons.append("CONFLICTING_SOURCE_METADATA")
                break
    if model and dict(model) != trust["model"]:
        validation_reasons.append("CONFLICTING_MODEL_METADATA")
    if method and dict(method) != trust["method"]:
        validation_reasons.append("CONFLICTING_METHOD_METADATA")
    declared_reasons = (
        raw.get("reason_codes") if isinstance(raw.get("reason_codes"), list) else []
    )
    if raw.get("is_computable") is False and not declared_reasons:
        validation_reasons.append("DECLARED_UNCOMPUTABLE")
    all_reasons = _unique_reason_codes(
        [
            *trust["reason_codes"],
            *declared_reasons,
            *validation_reasons,
            *force_reason_codes,
        ]
    )
    if raw.get("is_computable") is False or all_reasons:
        trust["is_computable"] = False
        trust["computability"] = "not_computable"
        trust["status"] = "unavailable"
        trust["trust_status"] = "unavailable"
        trust["display_mode"] = "historical_context"
    trust["reason_codes"] = all_reasons
    return trust


def _suppress_nested_composites(value: Any) -> None:
    if isinstance(value, list):
        for item in value:
            _suppress_nested_composites(item)
        return
    if not isinstance(value, dict):
        return
    for key in list(value):
        if key in _NULL_WHEN_UNTRUSTED:
            value[key] = None
        elif key in _CLEAR_WHEN_UNTRUSTED:
            value[key] = []
        elif key in _UNAVAILABLE_WHEN_UNTRUSTED:
            value[key] = "unavailable"
        else:
            _suppress_nested_composites(value[key])
    if "polarity" in value and any(key in value for key in ("impact_index", "daily_impact")):
        value["polarity"] = "unavailable"


def sanitize_opinion_payload(
    content: Mapping[str, Any],
    *,
    current_date: date | None = None,
    force_reason_codes: Iterable[str] = (),
) -> dict[str, Any]:
    """Revalidate provenance and atomically suppress every unsafe composite field."""

    output = copy.deepcopy(dict(content))
    evaluated_on = current_date or date.today()
    trust = _validated_trust(
        output,
        current_date=evaluated_on,
        force_reason_codes=force_reason_codes,
    )
    meta = output.setdefault("meta", {})
    if not isinstance(meta, dict):
        meta = {}
        output["meta"] = meta
    output["trust"] = trust
    meta["trust"] = copy.deepcopy(trust)
    meta.update(
        {
            "schema_version": trust["schema_version"],
            "model_version": trust["model_version"],
            "method_version": trust["method_version"],
            "model": copy.deepcopy(trust["model"]),
            "method": copy.deepcopy(trust["method"]),
            "source": copy.deepcopy(trust["source"]),
            "snapshot": copy.deepcopy(trust["snapshot"]),
        }
    )
    if trust["is_computable"]:
        return output

    if "values" in output or "heat" in output or "dates" in output:
        output["dates"] = []
        output["values"] = []
        output["heat"] = []
    _suppress_nested_composites(output)
    for item in output.get("target_indices") or []:
        if isinstance(item, dict):
            item["value"] = None
            item["trend_values"] = []
            item["state"] = "unavailable"
    summary = output.get("summary")
    if isinstance(summary, dict):
        summary["trend_label"] = "不可计算"
    for metric in output.get("metrics") or []:
        if not isinstance(metric, dict):
            continue
        label = str(metric.get("label") or "")
        if any(token in label for token in ("变化", "指数", "立场", "影响", "当前值")):
            metric["value"] = "不可计算"
            metric["display_tone"] = "neutral"
    meta["composite_suppressed"] = True
    return output


def suppress_composite_trend(
    content: Mapping[str, Any],
    *,
    current_date: date | None = None,
) -> dict[str, Any]:
    """Compatibility wrapper around the unified opinion payload sanitizer."""

    return sanitize_opinion_payload(content, current_date=current_date)


__all__ = (
    "OPINION_FRESHNESS_MAX_AGE_DAYS",
    "OPINION_MIN_ARTICLES",
    "OPINION_MIN_SOURCES",
    "OPINION_MODEL_VERSION",
    "OPINION_SOURCE_ID",
    "OPINION_TRUST_SCHEMA_VERSION",
    "evaluate_opinion_trust",
    "sanitize_opinion_payload",
    "suppress_composite_trend",
)
