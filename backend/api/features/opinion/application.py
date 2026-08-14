"""Transport-independent application service for China stance trends."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from sqlalchemy.orm import Session

from api.features.opinion.analytics import (
    coerce_date,
    compute_weighted_stance_trend,
    finite_float,
    sentiment_matches,
)
from api.features.opinion.constants import DECAY_MAX_LAG, METHOD_VERSION
from api.features.opinion.contracts import OpinionTrendQuery
from api.features.opinion.repository import (
    OpinionTrendRepository,
    SqlAlchemyOpinionTrendRepository,
)
from api.features.opinion.trust import evaluate_opinion_trust

_REJECTED_ROW_STATES = frozenset(
    {"discarded", "excluded", "invalid", "irrelevant", "rejected"}
)


def _row_rejection_state(row: Any) -> str | None:
    for field in ("review_status", "validation_status", "status"):
        value = str(row.get(field) or "").strip().lower()
        if value in _REJECTED_ROW_STATES:
            return value
    if (
        row.get("is_valid") is False
        or row.get("valid") is False
        or row.get("accepted") is False
        or row.get("is_rejected") is True
        or row.get("rejected") is True
    ):
        return "invalid"
    return None


def _valid_article_row(
    row: Any,
    *,
    current_date: date,
    minimum_relevance: float,
) -> dict[str, Any] | None:
    published_date = coerce_date(row.get("pub_date") or row.get("published_date"))
    stance = finite_float(row.get("stance_score"))
    confidence = finite_float(row.get("confidence"))
    relevance = finite_float(row.get("relevance_score"))
    if (
        published_date is None
        or published_date > current_date
        or stance is None
        or not -1.0 <= stance <= 1.0
        or confidence is None
        or not 0.0 <= confidence <= 1.0
        or relevance is None
        or not minimum_relevance <= relevance <= 1.0
        or str(row.get("method_version") or "") != METHOD_VERSION
    ):
        return None
    return {
        "pub_date": published_date,
        "stance_score": stance,
        "confidence": confidence,
        "relevance_score": relevance,
        "media_domain": row.get("media_domain") or row.get("source_domain"),
        "method_version": METHOD_VERSION,
    }


class OpinionTrendService:
    """Build the stable public trend DTO from a read-only repository."""

    def __init__(self, repository: OpinionTrendRepository) -> None:
        self._repository = repository

    def build(self, query: OpinionTrendQuery) -> dict[str, Any]:
        current_date = self._repository.current_date()
        # Discovery is anchored to the database date, never to a global maximum.
        # The extra decay window lets a stale-but-recent filtered cutoff still
        # retain every article that contributes to its first displayed point.
        discovery_span = query.days - 1 + 2 * (DECAY_MAX_LAG - 1)
        fetch_start = current_date - timedelta(days=discovery_span)

        rows = self._repository.list_trend_articles(
            fetch_start=fetch_start,
            end_date=current_date,
            china_min_score=query.china_min_score,
            sentiment_filter=query.sentiment_filter,
            region=query.region,
            language=query.language,
            media_source=query.media_source,
            event_family=query.event_family,
        )
        article_rows: list[dict[str, Any]] = []
        invalid_dates: list[date] = []
        rejected_dates: list[date] = []
        for row in rows:
            row_date = coerce_date(row.get("pub_date") or row.get("published_date"))
            if _row_rejection_state(row) is not None:
                if row_date is not None:
                    rejected_dates.append(row_date)
                continue
            normalized = _valid_article_row(
                row,
                current_date=current_date,
                minimum_relevance=query.china_min_score,
            )
            if normalized is None:
                if row_date is not None:
                    invalid_dates.append(row_date)
                continue
            if not sentiment_matches(
                normalized["stance_score"], query.sentiment_filter
            ):
                continue
            article_rows.append(normalized)

        current_contribution_start = current_date - timedelta(days=DECAY_MAX_LAG - 1)
        terminal_candidates = [
            row
            for row in article_rows
            if current_contribution_start <= row["pub_date"] <= current_date
        ]
        cutoff_date = (
            max(row["pub_date"] for row in terminal_candidates)
            if terminal_candidates
            else None
        )
        end_date = cutoff_date
        start_date = (
            end_date - timedelta(days=query.days - 1) if end_date is not None else None
        )
        trend_fetch_start = (
            start_date - timedelta(days=DECAY_MAX_LAG - 1)
            if start_date is not None
            else None
        )
        trend_rows = (
            [
                row
                for row in article_rows
                if trend_fetch_start <= row["pub_date"] <= end_date
            ]
            if trend_fetch_start is not None and end_date is not None
            else []
        )

        trend = (
            compute_weighted_stance_trend(start_date, end_date, trend_rows)
            if start_date is not None and end_date is not None
            else []
        )
        values = [point["weighted_stance_index"] for point in trend]
        non_zero_values = [value for value in values if abs(value) > 0.001]
        heat_values = [point["heat"] for point in trend]
        coverage_start = (
            end_date - timedelta(days=DECAY_MAX_LAG - 1)
            if end_date is not None
            else None
        )
        coverage_rows = (
            [
                row
                for row in article_rows
                if coverage_start <= row["pub_date"] <= end_date
            ]
            if coverage_start is not None and end_date is not None
            else []
        )
        coverage_sources = {
            str(row["media_domain"]).strip().lower()
            for row in coverage_rows
            if row.get("media_domain")
        }
        recent_invalid = sum(
            current_contribution_start <= item <= current_date
            for item in invalid_dates
        )
        recent_rejected = sum(
            current_contribution_start <= item <= current_date
            for item in rejected_dates
        )
        filters = {
            "days": query.days,
            "china_min_score": query.china_min_score,
            "sentiment_filter": query.sentiment_filter,
            "region": query.region,
            "language": query.language,
            "media_source": query.media_source,
            "event_family": query.event_family,
        }
        trust = evaluate_opinion_trust(
            current_date=current_date,
            cutoff_date=cutoff_date,
            article_count=len(coverage_rows),
            source_count=len(coverage_sources),
            coverage_start=coverage_start,
            coverage_end=end_date,
            invalid_article_count=recent_invalid,
            rejected_article_count=recent_rejected,
            filters=filters,
        )
        content = {
            "dates": [point["date"] for point in trend],
            "values": values,
            "heat": heat_values,
            "metric_id": "weighted_target_stance_index",
            "meta": {
                "method_version": METHOD_VERSION,
                "definition": trust["method"]["definition"],
                "schema_version": trust["schema_version"],
                "model_version": trust["model_version"],
                "model": trust["model"],
                "method": trust["method"],
                "source": trust["source"],
                "snapshot": trust["snapshot"],
                "total_articles": len(trend_rows),
                "coverage_articles": len(coverage_rows),
                "coverage_sources": len(coverage_sources),
                "invalid_articles": recent_invalid,
                "rejected_articles": recent_rejected,
                "last_article_date": cutoff_date.isoformat() if cutoff_date else None,
                "start_date": start_date.isoformat() if start_date else None,
                "end_date": end_date.isoformat() if end_date else None,
                "days": len(trend),
                "average_weighted_stance_index": (
                    round(sum(non_zero_values) / len(non_zero_values), 3)
                    if non_zero_values
                    else 0.0
                ),
                "maximum_weighted_stance_index": (
                    round(max(values), 3) if values else 0.0
                ),
                "minimum_weighted_stance_index": (
                    round(min(values), 3) if values else 0.0
                ),
                # Deprecated ambiguous aliases stay present only as explicit
                # unknowns for older clients.  They are not impact measures.
                "avg_impact": None,
                "max_impact": None,
                "min_impact": None,
                "max_heat": round(max(heat_values), 3) if heat_values else 0.0,
                "china_min_score": query.china_min_score,
                "filters": filters,
                "trust": trust,
            },
        }
        return content


def build_trend_content(
    db: Session,
    *,
    days: int,
    china_min_score: float,
    sentiment_filter: str,
    region: str | None = None,
    language: str | None = None,
    media_source: str | None = None,
    event_family: str | None = None,
) -> dict[str, Any]:
    return OpinionTrendService(SqlAlchemyOpinionTrendRepository(db)).build(
        OpinionTrendQuery(
            days=days,
            china_min_score=china_min_score,
            sentiment_filter=sentiment_filter,
            region=region,
            language=language,
            media_source=media_source,
            event_family=event_family,
        )
    )


__all__ = ("OpinionTrendService", "build_trend_content")
