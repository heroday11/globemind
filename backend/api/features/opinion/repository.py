"""Read-only repository boundary for the opinion trend use case."""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, Mapping, Protocol, Sequence

from sqlalchemy import text
from sqlalchemy.orm import Session

from api.features.opinion.analytics import coerce_date, dimension_conditions
from api.features.opinion.queries import (
    EFFECTIVE_STANCE_EXPR,
    FEEDBACK_VISIBLE_EXPR,
    LATEST_FEEDBACK_CTE,
)


class OpinionTrendRepository(Protocol):
    def current_date(self) -> date: ...

    def latest_score_date(self) -> date | None: ...

    def list_trend_articles(
        self,
        *,
        fetch_start: date,
        end_date: date,
        china_min_score: float,
        sentiment_filter: str,
        region: str | None,
        language: str | None,
        media_source: str | None,
        event_family: str | None,
    ) -> Sequence[Mapping[str, Any]]: ...


class SqlAlchemyOpinionTrendRepository:
    """SELECT-only adapter over materialized China stance scores."""

    def __init__(self, db: Session) -> None:
        self._db = db

    def current_date(self) -> date:
        value = self._db.execute(text("SELECT CURRENT_DATE")).scalar()
        return coerce_date(value) or datetime.now(timezone.utc).date()

    def latest_score_date(self) -> date | None:
        value = self._db.execute(
            text(
                """
                SELECT max(published_date)
                FROM public.china_opinion_article_scores
                WHERE published_at <= now()
                  AND relevance_score >= 0.35
                """
            )
        ).scalar()
        return coerce_date(value)

    def list_trend_articles(
        self,
        *,
        fetch_start: date,
        end_date: date,
        china_min_score: float,
        sentiment_filter: str,
        region: str | None,
        language: str | None,
        media_source: str | None,
        event_family: str | None,
    ) -> Sequence[Mapping[str, Any]]:
        conditions, params = dimension_conditions(
            min_score=china_min_score,
            sentiment_filter=sentiment_filter,
            region=region,
            language=language,
            media_source=media_source,
            event_family=event_family,
            stance_expr=EFFECTIVE_STANCE_EXPR,
        )
        params.update({"fetch_start": fetch_start, "end_date": end_date})
        return (
            self._db.execute(
                text(
                    f"""
                WITH {LATEST_FEEDBACK_CTE}
                SELECT s.news_id,
                       s.published_date AS pub_date,
                       {EFFECTIVE_STANCE_EXPR} AS stance_score,
                       s.confidence,
                       s.relevance_score,
                       s.article_weight,
                       s.method_version,
                       s.media_domain,
                       s.source_domain,
                       lf.correction AS feedback_correction
                FROM public.china_opinion_article_scores AS s
                LEFT JOIN latest_feedback AS lf ON lf.news_id = s.news_id
                WHERE s.published_date BETWEEN :fetch_start AND :end_date
                  AND {FEEDBACK_VISIBLE_EXPR}
                  AND {conditions}
                ORDER BY s.published_date ASC
                """
                ),
                params,
            )
            .mappings()
            .fetchall()
        )


def current_db_date(db: Session) -> date:
    return SqlAlchemyOpinionTrendRepository(db).current_date()


def latest_score_date(db: Session) -> date | None:
    return SqlAlchemyOpinionTrendRepository(db).latest_score_date()


__all__ = (
    "OpinionTrendRepository",
    "SqlAlchemyOpinionTrendRepository",
    "current_db_date",
    "latest_score_date",
)
