"""Stable, bounded identities for public opinion-overview derivations.

The identities describe a metric and its controlled slice.  They deliberately
exclude rendered labels, values, article prose, and feedback.  The underlying
score table is not a public citation, so every claim explicitly reports that a
safe citation locator is unavailable and that source truth was not verified.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import re
from dataclasses import asdict, dataclass
from datetime import date
from typing import Any, Mapping

OPINION_CLAIM_SCHEMA_VERSION = "opinion-derived-claim-contract-v1"
OPINION_CLAIM_MAX_CLAIMS = 48
OPINION_CLAIM_MAX_FAMILIES = 8
OPINION_CLAIM_MAX_BRIEFS = 6

_SOURCE_ID = "public.china_opinion_article_scores"
_SNAPSHOT_RE = re.compile(r"^opinion-[0-9a-f]{64}$")
_VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$")
_FAMILY_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_ENTITY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


@dataclass(frozen=True)
class OpinionDerivedClaim:
    """Serialized claim metadata; this class makes no truth assertion."""

    claim_id: str
    metric: str
    output_paths: tuple[str, ...]
    identity: Mapping[str, Any]
    claim_state: str
    reason_code: str
    source_truth_state: str
    citation_locator: None
    citation_status: str
    citation_reason_code: str


def _finite_number(value: Any) -> float | int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not math.isfinite(float(value)):
        return None
    return value


def _positive_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return None
    return value


def _same_optional_number(left: Any, right: Any) -> bool:
    left_number = _finite_number(left)
    right_number = _finite_number(right)
    if left is None or right is None:
        return left is None and right is None
    if left_number is None or right_number is None:
        return False
    return math.isclose(float(left_number), float(right_number), abs_tol=1e-9)


def _unknown_target_indices() -> list[dict[str, Any]]:
    return [
        {"label": label, "value": None, "trend_values": [], "state": "unavailable"}
        for label in ("CN", "NEG", "POS")
    ]


def _canonical_target_indices(
    raw: Any,
    summary: Mapping[str, Any],
) -> list[dict[str, Any]]:
    if not isinstance(raw, (list, tuple)) or len(raw) != 3:
        return _unknown_target_indices()
    by_label: dict[str, Mapping[str, Any]] = {}
    for item in raw:
        if not isinstance(item, Mapping):
            return _unknown_target_indices()
        label = item.get("label")
        if label not in {"CN", "NEG", "POS"} or label in by_label:
            return _unknown_target_indices()
        by_label[label] = item
    if set(by_label) != {"CN", "NEG", "POS"}:
        return _unknown_target_indices()

    current = _finite_number(summary.get("current_index"))
    negative_share = _finite_number(summary.get("negative_pct"))
    positive_share = _finite_number(summary.get("positive_pct"))
    expected = {
        "CN": current,
        "NEG": -float(negative_share) if negative_share is not None else None,
        "POS": positive_share,
    }
    if any(
        not _same_optional_number(by_label[label].get("value"), expected[label])
        for label in ("CN", "NEG", "POS")
    ):
        return _unknown_target_indices()
    cn_trend = by_label["CN"].get("trend_values", [])
    if (
        not isinstance(cn_trend, list)
        or len(cn_trend) > 14
        or any(_finite_number(value) is None for value in cn_trend)
        or (
            cn_trend
            and current is not None
            and not _same_optional_number(cn_trend[-1], current)
        )
    ):
        return _unknown_target_indices()
    if any(by_label[label].get("trend_values", []) not in ([], None) for label in ("NEG", "POS")):
        return _unknown_target_indices()

    def state(label: str, value: Any) -> str:
        if value is None:
            return "unavailable"
        if label == "CN":
            return "negative" if value < -12 else "positive" if value > 12 else "warning"
        return "negative" if label == "NEG" else "positive"

    return [
        {
            "label": label,
            "value": expected[label],
            "trend_values": list(cn_trend) if label == "CN" else [],
            "state": state(label, expected[label]),
        }
        for label in ("CN", "NEG", "POS")
    ]


def _identity_metadata(content: Mapping[str, Any]) -> dict[str, Any] | None:
    trust = content.get("trust")
    if not isinstance(trust, Mapping):
        return None
    model_version = trust.get("model_version")
    method_version = trust.get("method_version")
    snapshot_id = trust.get("snapshot_id")
    cutoff = trust.get("cutoff_date")
    source = trust.get("source")
    snapshot = trust.get("snapshot")
    if (
        not isinstance(model_version, str)
        or not _VERSION_RE.fullmatch(model_version)
        or not isinstance(method_version, str)
        or not _VERSION_RE.fullmatch(method_version)
        or not isinstance(snapshot_id, str)
        or not _SNAPSHOT_RE.fullmatch(snapshot_id)
        or not isinstance(source, Mapping)
        or source.get("id") != _SOURCE_ID
        or not isinstance(snapshot, Mapping)
        or snapshot.get("id") != snapshot_id
    ):
        return None
    if cutoff is not None:
        try:
            if date.fromisoformat(str(cutoff)).isoformat() != cutoff:
                return None
        except (TypeError, ValueError):
            return None
    filters = snapshot.get("filters")
    days = filters.get("days") if isinstance(filters, Mapping) else None
    if isinstance(days, bool) or not isinstance(days, int) or not 7 <= days <= 365:
        return None
    return {
        "model_version": model_version,
        "method_version": method_version,
        "data_cutoff": cutoff,
        "snapshot_id": snapshot_id,
        "source_id": _SOURCE_ID,
        "window_days": days,
    }


def _claim(
    *,
    metric: str,
    output_paths: tuple[str, ...],
    slice_identity: Mapping[str, Any],
    value: Any,
    metadata: Mapping[str, Any],
) -> dict[str, Any]:
    identity = {
        "metric": metric,
        "slice": dict(slice_identity),
        "model_version": metadata["model_version"],
        "method_version": metadata["method_version"],
        "data_cutoff": metadata["data_cutoff"],
        "snapshot_id": metadata["snapshot_id"],
        "source_id": metadata["source_id"],
    }
    canonical = json.dumps(
        identity, ensure_ascii=True, sort_keys=True, separators=(",", ":")
    )
    available = _finite_number(value) is not None
    claim = OpinionDerivedClaim(
        claim_id="opinion-claim-" + hashlib.sha256(canonical.encode()).hexdigest(),
        metric=metric,
        output_paths=output_paths,
        identity=identity,
        claim_state="derived_not_verified" if available else "explicit_unknown",
        reason_code=(
            "DERIVED_VALUE_NOT_SOURCE_VERIFIED"
            if available
            else "DERIVED_VALUE_UNAVAILABLE"
        ),
        source_truth_state="not_verified",
        citation_locator=None,
        citation_status="unavailable",
        citation_reason_code="SAFE_CITATION_LOCATOR_UNAVAILABLE",
    )
    payload = asdict(claim)
    payload["output_paths"] = list(claim.output_paths)
    return payload


def _suppress_overview_derivations(output: dict[str, Any]) -> None:
    summary = output.get("summary")
    if isinstance(summary, dict):
        for field in (
            "current_index",
            "change_24h",
            "growth_pct",
            "article_count",
            "source_count",
            "family_count",
            "positive_pct",
            "negative_pct",
            "neutral_pct",
        ):
            summary[field] = None
        summary["trend_label"] = "不可计算"
    for item in output.get("target_indices") or []:
        if isinstance(item, dict):
            item["value"] = None
            item["trend_values"] = []
            item["state"] = "unavailable"
    for item in output.get("families") or []:
        if isinstance(item, dict):
            item["avg_stance"] = None
            item["article_count"] = None
    for item in output.get("briefs") or []:
        if isinstance(item, dict):
            item["stance_score"] = None
            item["confidence"] = None
            item["severity"] = "unavailable"
    top_event = output.get("top_event")
    if isinstance(top_event, dict):
        for field in ("avg_stance", "article_count", "china_articles"):
            if field in top_event:
                top_event[field] = None
    for metric in output.get("metrics") or []:
        if isinstance(metric, dict):
            metric["value"] = "不可计算"
            metric["display_tone"] = "neutral"


def assure_opinion_overview_claims(content: Mapping[str, Any]) -> dict[str, Any]:
    """Replace any supplied claim metadata with a bounded derived contract."""

    output = copy.deepcopy(dict(content))
    output["families"] = list(output.get("families") or [])[:OPINION_CLAIM_MAX_FAMILIES]
    output["briefs"] = list(output.get("briefs") or [])[:OPINION_CLAIM_MAX_BRIEFS]
    summary = output.get("summary") if isinstance(output.get("summary"), Mapping) else {}
    output["target_indices"] = _canonical_target_indices(
        output.get("target_indices"), summary
    )
    metadata = _identity_metadata(output)
    if metadata is None:
        _suppress_overview_derivations(output)
        output["claim_contract"] = {
            "schema_version": OPINION_CLAIM_SCHEMA_VERSION,
            "status": "unavailable",
            "reason_codes": ["CLAIM_IDENTITY_METADATA_UNAVAILABLE"],
            "claims": [],
            "max_claims": OPINION_CLAIM_MAX_CLAIMS,
        }
        return output

    claims: list[dict[str, Any]] = []
    population_slice = {
        "population": "china_relevant_direct_articles",
        "window_days": metadata["window_days"],
    }
    core_specs = (
        ("weighted_stance_index", ("summary.current_index",), "current_index"),
        ("weighted_stance_change_24h", ("summary.change_24h",), "change_24h"),
        ("article_volume_change_pct", ("summary.growth_pct",), "growth_pct"),
        ("article_count", ("summary.article_count",), "article_count"),
        ("source_count", ("summary.source_count",), "source_count"),
        ("event_family_count", ("summary.family_count",), "family_count"),
        ("positive_stance_share_pct", ("summary.positive_pct",), "positive_pct"),
        ("negative_stance_share_pct", ("summary.negative_pct",), "negative_pct"),
        ("neutral_stance_share_pct", ("summary.neutral_pct",), "neutral_pct"),
    )
    for metric, paths, field in core_specs:
        claims.append(
            _claim(
                metric=metric,
                output_paths=paths,
                slice_identity=population_slice,
                value=summary.get(field),
                metadata=metadata,
            )
        )

    target_by_label = {
        item["label"]: item for item in output["target_indices"]
    }
    for metric, label in (
        ("target_weighted_stance_index", "CN"),
        ("negative_stance_pressure_index", "NEG"),
        ("positive_stance_support_index", "POS"),
    ):
        claims.append(
            _claim(
                metric=metric,
                output_paths=(f"target_indices.{label}.value",),
                slice_identity={**population_slice, "target": label},
                value=target_by_label[label]["value"],
                metadata=metadata,
            )
        )

    safe_families: list[dict[str, Any]] = []
    seen_families: set[str] = set()
    for raw in output["families"]:
        if not isinstance(raw, Mapping):
            continue
        family = raw.get("event_family")
        if not isinstance(family, str) or not _FAMILY_RE.fullmatch(family) or family in seen_families:
            continue
        seen_families.add(family)
        item = dict(raw)
        safe_families.append(item)
        family_slice = {**population_slice, "event_family": family}
        for metric, field in (
            ("event_family_article_count", "article_count"),
            ("event_family_average_stance", "avg_stance"),
        ):
            claims.append(
                _claim(
                    metric=metric,
                    output_paths=(f"families.event_family={family}.{field}",),
                    slice_identity=family_slice,
                    value=item.get(field),
                    metadata=metadata,
                )
            )
    output["families"] = safe_families

    safe_briefs: list[dict[str, Any]] = []
    seen_news: set[int] = set()
    for raw in output["briefs"]:
        if not isinstance(raw, Mapping):
            continue
        news_id = _positive_int(raw.get("id"))
        if news_id is None or news_id in seen_news:
            continue
        seen_news.add(news_id)
        item = dict(raw)
        safe_briefs.append(item)
        article_slice = {**population_slice, "news_id": news_id}
        for metric, field in (
            ("article_stance_score", "stance_score"),
            ("article_model_confidence", "confidence"),
        ):
            claims.append(
                _claim(
                    metric=metric,
                    output_paths=(f"briefs.news_id={news_id}.{field}",),
                    slice_identity=article_slice,
                    value=item.get(field),
                    metadata=metadata,
                )
            )
    output["briefs"] = safe_briefs

    top_event = output.get("top_event")
    if isinstance(top_event, Mapping):
        chain_id = str(top_event.get("chain_id") or "")
        if _ENTITY_RE.fullmatch(chain_id):
            event_slice = {**population_slice, "chain_id": chain_id}
            for metric, field in (
                ("top_event_average_stance", "avg_stance"),
                ("top_event_article_count", "article_count"),
                ("top_event_china_article_count", "china_articles"),
            ):
                if field in top_event:
                    claims.append(
                        _claim(
                            metric=metric,
                            output_paths=(f"top_event.{field}",),
                            slice_identity=event_slice,
                            value=top_event.get(field),
                            metadata=metadata,
                        )
                    )
        else:
            top_copy = dict(top_event)
            for field in ("avg_stance", "article_count", "china_articles"):
                if field in top_copy:
                    top_copy[field] = None
            output["top_event"] = top_copy

    output["claim_contract"] = {
        "schema_version": OPINION_CLAIM_SCHEMA_VERSION,
        "status": "complete",
        "reason_codes": [],
        "claims": claims[:OPINION_CLAIM_MAX_CLAIMS],
        "max_claims": OPINION_CLAIM_MAX_CLAIMS,
    }
    return output


__all__ = (
    "OPINION_CLAIM_MAX_CLAIMS",
    "OPINION_CLAIM_SCHEMA_VERSION",
    "OpinionDerivedClaim",
    "assure_opinion_overview_claims",
)
