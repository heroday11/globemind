"""Versioned three-axis semantics for opinion outputs.

This module describes and projects three independent concepts:

* stance: targeted attitude toward a named target;
* tone: affective or linguistic tone of the text;
* impact: an observed or modeled downstream effect.

Only stance has an established source in the current opinion pipeline.  Tone
and impact therefore remain explicit unknowns.  In particular, this boundary
does not reinterpret legacy ``sentiment`` or ``impact_index`` values as another
axis merely because their signs happen to match a stance score.
"""

from __future__ import annotations

import copy
import math
from collections.abc import Mapping, MutableMapping, Sequence
from typing import Any

from api.features.opinion.constants import METHOD_VERSION

OPINION_SEMANTIC_SCHEMA_VERSION = "opinion-semantic-dimensions-v1"
OPINION_SEMANTIC_CONTRACT_VERSION = "opinion-three-axis-method-v1"
_STANCE_SOURCE_TABLE = "public.china_opinion_article_scores"

_STANCE_SCALES: dict[str, dict[str, Any]] = {
    "article_stance": {
        "unit": "dimensionless",
        "minimum": -1.0,
        "maximum": 1.0,
        "neutral_band": [-0.15, 0.15],
    },
    "aggregate_stance_index": {
        "unit": "index_points",
        "minimum": -100.0,
        "maximum": 100.0,
        "neutral_band": [-15.0, 15.0],
    },
}

_AMBIGUOUS_NUMERIC_ALIASES = frozenset(
    {
        "avg_impact",
        "china_importance",
        "daily_impact",
        "impact_abs",
        "impact_index",
        "l1_total_impact",
        "max_impact",
        "min_impact",
        "sentiment",
        "total_raw_daily",
    }
)


def opinion_semantic_method_card() -> dict[str, Any]:
    """Return a copy-safe, JSON-serializable method card."""

    return {
        "schema_version": OPINION_SEMANTIC_SCHEMA_VERSION,
        "contract_version": OPINION_SEMANTIC_CONTRACT_VERSION,
        "dimensions": {
            "stance": {
                "meaning": "targeted attitude toward the named target",
                "categories": ["supportive", "neutral", "critical", "unknown"],
                "score_scales": copy.deepcopy(_STANCE_SCALES),
                "source_model": METHOD_VERSION,
                "source_table": _STANCE_SOURCE_TABLE,
                "availability": "available_when_trust_gate_passes",
            },
            "tone": {
                "meaning": "affective or linguistic tone independent of target",
                "categories": [
                    "positive",
                    "neutral",
                    "negative",
                    "mixed",
                    "unknown",
                ],
                "score_scale": {"state": "not_established", "unit": "unknown"},
                "source_model": None,
                "source_table": None,
                "availability": "not_available",
            },
            "impact": {
                "meaning": (
                    "observed or modeled downstream effect independent of stance"
                ),
                "directions": [
                    "positive",
                    "neutral",
                    "negative",
                    "mixed",
                    "unknown",
                ],
                "score_scale": {"state": "not_established", "unit": "unknown"},
                "source_model": None,
                "source_table": None,
                "availability": "not_available",
            },
        },
        "combination": {
            "scope": "response_projection",
            "state": "not_combined",
            "combined_score": None,
            "rules": {
                "stance_from_tone": False,
                "stance_from_impact": False,
                "tone_from_stance": False,
                "tone_from_impact": False,
                "impact_from_stance": False,
                "impact_from_tone": False,
            },
        },
        "assurance": {
            "quality_state": "not_established",
            "fact_truth_state": "not_verified",
            "upstream_axis_independence_state": "not_established",
        },
        "legacy_aliases": {
            "sentiment": "deprecated_ambiguous_output_suppressed",
            "tone": "deprecated_ui_style_alias_suppressed",
            "impact_index": "deprecated_ambiguous_output_suppressed",
            "daily_impact": "deprecated_ambiguous_output_suppressed",
            "china_importance": "deprecated_stance_derived_output_suppressed",
            "sentiment_filter": "legacy_wire_name_for_stance_filter_only",
        },
    }


def _finite_number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _unknown_stance(reason_code: str) -> dict[str, Any]:
    return {
        "state": "unknown",
        "category": "unknown",
        "score": None,
        "scale": None,
        "unit": "unknown",
        "source_field": None,
        "source_model": None,
        "reason_code": reason_code,
    }


def _unknown_tone() -> dict[str, Any]:
    return {
        "state": "unknown",
        "category": "unknown",
        "score": None,
        "unit": "unknown",
        "source_field": None,
        "source_model": None,
        "reason_code": "TONE_MODEL_NOT_AVAILABLE",
    }


def _unknown_impact() -> dict[str, Any]:
    return {
        "state": "unknown",
        "direction": "unknown",
        "score": None,
        "unit": "unknown",
        "source_field": None,
        "source_model": None,
        "reason_code": "IMPACT_MODEL_NOT_AVAILABLE",
    }


def _stance_category(score: float, neutral_band: Sequence[float]) -> str:
    lower, upper = float(neutral_band[0]), float(neutral_band[1])
    if score < lower:
        return "critical"
    if score > upper:
        return "supportive"
    return "neutral"


def build_opinion_semantic_dimensions(
    *,
    stance_score: Any = None,
    stance_scale: str | None = None,
    stance_source_field: str | None = None,
    stance_source_model: str | None = None,
    declared_stance_category: str | None = None,
    stance_reason_code: str | None = None,
    tone_category: Any = None,
    tone_score: Any = None,
    impact_direction: Any = None,
    impact_score: Any = None,
) -> dict[str, Any]:
    """Project axes independently; unsupported axes stay unknown.

    Tone and impact arguments are deliberately accepted so callers cannot
    accidentally believe they were overlooked.  Until an independently
    versioned source is established, those values are never emitted.
    """

    del tone_category, tone_score, impact_direction, impact_score
    stance: dict[str, Any]
    if stance_reason_code:
        stance = _unknown_stance(stance_reason_code)
    elif stance_scale not in _STANCE_SCALES:
        stance = _unknown_stance("STANCE_SCALE_NOT_ESTABLISHED")
    elif not isinstance(stance_source_field, str) or not stance_source_field.strip():
        stance = _unknown_stance("STANCE_SOURCE_FIELD_NOT_ESTABLISHED")
    elif stance_source_model != METHOD_VERSION:
        stance = _unknown_stance("STANCE_SOURCE_MODEL_MISMATCH")
    else:
        score = _finite_number(stance_score)
        scale = _STANCE_SCALES[stance_scale]
        if score is None or not scale["minimum"] <= score <= scale["maximum"]:
            stance = _unknown_stance("STANCE_SCORE_INVALID")
        else:
            category = _stance_category(score, scale["neutral_band"])
            if (
                declared_stance_category is not None
                and declared_stance_category != category
            ):
                stance = _unknown_stance("STANCE_CATEGORY_CONFLICT")
            else:
                stance = {
                    "state": "available",
                    "category": category,
                    "score": score,
                    "scale": stance_scale,
                    "unit": scale["unit"],
                    "source_field": stance_source_field.strip(),
                    "source_model": stance_source_model,
                    "reason_code": None,
                }
    return {"stance": stance, "tone": _unknown_tone(), "impact": _unknown_impact()}


def _trust_allows_stance(payload: Mapping[str, Any]) -> bool:
    top = payload.get("trust")
    meta = payload.get("meta")
    nested = meta.get("trust") if isinstance(meta, Mapping) else None
    trust = top if isinstance(top, Mapping) else nested
    return bool(
        isinstance(trust, Mapping)
        and trust.get("is_computable") is True
        and trust.get("model_version") == METHOD_VERSION
        and trust.get("method_version") == METHOD_VERSION
    )


def _dimensions_for(
    *,
    score: Any,
    scale: str,
    source_field: str,
    provenance_ok: bool,
    reason_code: str | None = None,
) -> dict[str, Any]:
    return build_opinion_semantic_dimensions(
        stance_score=score,
        stance_scale=scale,
        stance_source_field=source_field,
        stance_source_model=METHOD_VERSION,
        stance_reason_code=(
            reason_code
            if reason_code is not None
            else None if provenance_ok else "STANCE_PROVENANCE_NOT_ESTABLISHED"
        ),
    )


def _attach_record_stance(
    record: Any,
    *,
    key: str,
    scale: str,
    source_field: str,
    provenance_ok: bool,
    reason_code: str | None = None,
) -> None:
    if not isinstance(record, MutableMapping):
        return
    record["semantic_dimensions"] = _dimensions_for(
        score=record.get(key),
        scale=scale,
        source_field=source_field,
        provenance_ok=provenance_ok,
        reason_code=reason_code,
    )


def _suppress_ambiguous_aliases(value: Any) -> None:
    if isinstance(value, list):
        for item in value:
            _suppress_ambiguous_aliases(item)
        return
    if not isinstance(value, MutableMapping):
        return
    for key, child in list(value.items()):
        if key in _AMBIGUOUS_NUMERIC_ALIASES:
            value[key] = None
        elif key == "tone":
            value[key] = None
        elif key == "polarity":
            value[key] = "unknown"
        else:
            _suppress_ambiguous_aliases(child)


def apply_opinion_semantic_contract(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Attach method metadata and per-record projections on known UI paths."""

    output = copy.deepcopy(dict(payload))
    _suppress_ambiguous_aliases(output)
    provenance_ok = _trust_allows_stance(output)
    output["semantic_contract"] = opinion_semantic_method_card()

    values = output.get("values")
    if (
        output.get("metric_id") == "weighted_target_stance_index"
        and isinstance(values, list)
        and values
    ):
        output["semantic_dimensions"] = _dimensions_for(
            score=values[-1],
            scale="aggregate_stance_index",
            source_field="values[-1]",
            provenance_ok=provenance_ok,
        )
    elif isinstance(output.get("summary"), Mapping):
        output["semantic_dimensions"] = _dimensions_for(
            score=output["summary"].get("current_index"),
            scale="aggregate_stance_index",
            source_field="summary.current_index",
            provenance_ok=provenance_ok,
        )
    else:
        output["semantic_dimensions"] = _dimensions_for(
            score=None,
            scale="aggregate_stance_index",
            source_field="unavailable",
            provenance_ok=provenance_ok,
            reason_code="STANCE_VALUE_UNAVAILABLE",
        )

    summary = output.get("summary")
    _attach_record_stance(
        summary,
        key="current_index",
        scale="aggregate_stance_index",
        source_field="summary.current_index",
        provenance_ok=provenance_ok,
    )

    for item in output.get("target_indices", []) or []:
        if not isinstance(item, MutableMapping):
            continue
        if str(item.get("label") or "").upper() == "CN":
            _attach_record_stance(
                item,
                key="value",
                scale="aggregate_stance_index",
                source_field="target_indices.CN.value",
                provenance_ok=provenance_ok,
            )
        else:
            _attach_record_stance(
                item,
                key="value",
                scale="aggregate_stance_index",
                source_field="target_indices.value",
                provenance_ok=provenance_ok,
                reason_code="TARGET_VALUE_IS_NOT_A_DIRECT_STANCE_SCORE",
            )

    for collection_name in ("briefs", "news"):
        for item in output.get(collection_name, []) or []:
            _attach_record_stance(
                item,
                key="stance_score",
                scale="article_stance",
                source_field=f"{collection_name}.stance_score",
                provenance_ok=provenance_ok,
            )

    for collection_name in ("events", "sub_events"):
        for item in output.get(collection_name, []) or []:
            _attach_record_stance(
                item,
                key="weighted_stance_index",
                scale="aggregate_stance_index",
                source_field=f"{collection_name}.weighted_stance_index",
                provenance_ok=provenance_ok,
            )

    for item in (output.get("top_event"),):
        _attach_record_stance(
            item,
            key="avg_stance",
            scale="article_stance",
            source_field="top_event.avg_stance",
            provenance_ok=provenance_ok,
        )
    for item in output.get("families", []) or []:
        _attach_record_stance(
            item,
            key="avg_stance",
            scale="article_stance",
            source_field="families.avg_stance",
            provenance_ok=provenance_ok,
        )

    dimensions = output.get("dimensions")
    if isinstance(dimensions, Mapping):
        for rows in dimensions.values():
            if not isinstance(rows, list):
                continue
            for item in rows:
                _attach_record_stance(
                    item,
                    key="weighted_stance_index",
                    scale="aggregate_stance_index",
                    source_field="dimensions.weighted_stance_index",
                    provenance_ok=provenance_ok,
                )

    return output


__all__ = (
    "OPINION_SEMANTIC_CONTRACT_VERSION",
    "OPINION_SEMANTIC_SCHEMA_VERSION",
    "apply_opinion_semantic_contract",
    "build_opinion_semantic_dimensions",
    "opinion_semantic_method_card",
)
