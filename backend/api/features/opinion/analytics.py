"""Pure stance analytics and dimension-query helpers."""

from __future__ import annotations

import math
from datetime import date, datetime, timedelta
from typing import Any, Mapping, Sequence

from api.features.opinion.constants import (
    DECAY_ALPHA,
    DECAY_MAX_LAG,
    DECAY_TAU_BASE,
    DECAY_TAU_SCALE,
    METHOD_VERSION,
)


def coerce_date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def finite_float(value: Any, *, default: float | None = None) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return default
    return number if math.isfinite(number) else default


def article_decay_weight(delta_days: float, importance: float) -> float:
    delta = finite_float(delta_days)
    if delta is None:
        return 0.0
    if delta <= 0:
        return 1.0
    safe_importance = finite_float(importance, default=0.0) or 0.0
    tau = DECAY_TAU_BASE + DECAY_TAU_SCALE * max(0.0, min(1.0, safe_importance))
    weight = 1.0 / (1 + (delta / tau) ** DECAY_ALPHA)
    return weight if math.isfinite(weight) else 0.0


def sentiment_matches(score: float, sentiment_filter: str) -> bool:
    safe_score = finite_float(score)
    if safe_score is None:
        return False
    if sentiment_filter == "positive":
        return safe_score > 0.15
    if sentiment_filter == "negative":
        return safe_score < -0.15
    return True


def compute_weighted_stance_trend(
    start_date: date,
    end_date: date,
    article_rows: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    if start_date > end_date:
        return []
    day_count = (end_date - start_date).days + 1
    daily_numerator = [0.0] * day_count
    daily_denominator = [0.0] * day_count
    daily_heat = [0.0] * day_count

    for row in article_rows:
        published_date = coerce_date(row.get("pub_date") or row.get("published_date"))
        stance = finite_float(row.get("stance_score") or 0.0)
        relevance = finite_float(row.get("relevance_score") or row.get("article_weight") or 0.0)
        confidence = finite_float(row.get("confidence") or 0.0)
        if published_date is None or stance is None or relevance is None or confidence is None:
            continue
        weight = max(0.0, min(1.0, relevance)) * max(0.35, min(1.0, confidence))
        if weight <= 0:
            continue
        published_offset = (published_date - start_date).days
        if published_offset >= day_count:
            continue
        first_index = max(0, published_offset)
        last_index = min(day_count, published_offset + DECAY_MAX_LAG)
        for index in range(first_index, last_index):
            decay = article_decay_weight(index - published_offset, weight)
            decayed_weight = weight * decay
            daily_numerator[index] += stance * decayed_weight
            daily_denominator[index] += decayed_weight
            daily_heat[index] += decayed_weight

    result: list[dict[str, Any]] = []
    for index in range(day_count):
        stance_index = (
            0.0
            if daily_denominator[index] <= 0
            else 100.0 * daily_numerator[index] / daily_denominator[index]
        )
        result.append(
            {
                "date": (start_date + timedelta(days=index)).isoformat(),
                "weighted_stance_index": round(stance_index, 3),
                "heat": round(daily_heat[index], 3),
            }
        )
    return result


def trend_values_for_rows(
    start_date: date,
    end_date: date,
    rows: Sequence[Mapping[str, Any]],
) -> list[float]:
    trend = compute_weighted_stance_trend(
        start_date,
        end_date,
        [
            {
                "pub_date": row["published_date"],
                "stance_score": finite_float(row["stance_score"], default=0.0),
                "confidence": finite_float(row["confidence"], default=0.0),
                "relevance_score": finite_float(row["relevance_score"], default=0.0),
            }
            for row in rows
        ],
    )
    return [point["weighted_stance_index"] for point in trend]


def dimension_conditions(
    *,
    min_score: float,
    sentiment_filter: str,
    region: str | None = None,
    language: str | None = None,
    media_source: str | None = None,
    event_family: str | None = None,
    alias: str = "s",
    stance_expr: str | None = None,
) -> tuple[str, dict[str, Any]]:
    stance = stance_expr or f"{alias}.stance_score"
    clauses = [
        f"{alias}.relevance_score >= :min_score",
        f"{alias}.relevance_score <= 1.0",
        f"{alias}.directness_score >= 0.55",
        f"{alias}.stance_score BETWEEN -1.0 AND 1.0",
        f"{alias}.confidence BETWEEN 0.0 AND 1.0",
        f"{alias}.article_weight > 0.0",
        f"{alias}.article_weight <= 1.0",
        f"{alias}.method_version = :method_version",
    ]
    params: dict[str, Any] = {
        "method_version": METHOD_VERSION,
        "min_score": min_score,
    }
    if sentiment_filter == "positive":
        clauses.append(f"{stance} > 0.15")
    elif sentiment_filter == "negative":
        clauses.append(f"{stance} < -0.15")
    if region:
        clauses.append(f"{alias}.region = :region")
        params["region"] = region
    if language:
        clauses.append(f"{alias}.language = :language")
        params["language"] = language
    if media_source:
        clauses.append(f"coalesce({alias}.media_domain, {alias}.source_domain, '') = :media_source")
        params["media_source"] = media_source
    if event_family:
        clauses.append(f"{alias}.event_family = :event_family")
        params["event_family"] = event_family
    return " AND ".join(clauses), params


def classify_index_label(value: float) -> str:
    safe_value = finite_float(value, default=0.0) or 0.0
    if safe_value <= -35:
        return "明显负面"
    if safe_value <= -12:
        return "偏负面"
    if safe_value < 12:
        return "中性震荡"
    if safe_value < 35:
        return "偏正面"
    return "明显正面"


def format_signed(value: float, digits: int = 1) -> str:
    safe_value = finite_float(value, default=0.0) or 0.0
    return f"{safe_value:+.{digits}f}"


__all__ = (
    "article_decay_weight",
    "classify_index_label",
    "coerce_date",
    "compute_weighted_stance_trend",
    "dimension_conditions",
    "finite_float",
    "format_signed",
    "sentiment_matches",
    "trend_values_for_rows",
)
