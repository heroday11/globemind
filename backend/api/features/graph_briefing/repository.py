"""Parameterized current-table queries for graph briefing."""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from sqlalchemy import text
from sqlalchemy.orm import Session


class GraphBriefingRepository:
    """Read-only repository over the current L3, L2, L1, and news tables."""

    def __init__(self, db: Session):
        self._db = db

    def _all(
        self,
        statement: str,
        parameters: Mapping[str, Any],
    ) -> list[dict[str, Any]]:
        rows = self._db.execute(text(statement), dict(parameters)).mappings().all()
        return [dict(row) for row in rows]

    def _one(
        self,
        statement: str,
        parameters: Mapping[str, Any],
    ) -> dict[str, Any] | None:
        row = self._db.execute(text(statement), dict(parameters)).mappings().first()
        return dict(row) if row else None

    def _scalar(
        self,
        statement: str,
        parameters: Mapping[str, Any],
    ) -> int:
        value = self._db.execute(text(statement), dict(parameters)).scalar()
        return int(value or 0)

    def search_macros(self, keyword: str, limit: int) -> list[dict[str, Any]]:
        return self._all(
            """
            SELECT macro.macro_id, macro.macro_key, macro.title, macro.summary,
                   macro.family_group, macro.l2_chain_count, macro.l1_cluster_count,
                   macro.segment_count, macro.article_count, macro.start_date,
                   macro.end_date, macro.actor_counts, macro.topic_counts,
                   macro.quality_score
            FROM public.event_l3_macro_events AS macro
            WHERE macro.macro_id = :exact_id
               OR COALESCE(macro.title, '') ILIKE :keyword ESCAPE '!'
               OR COALESCE(macro.summary, '') ILIKE :keyword ESCAPE '!'
               OR COALESCE(macro.macro_key, '') ILIKE :keyword ESCAPE '!'
               OR EXISTS (
                    SELECT 1
                    FROM public.event_l3_macro_members AS member
                    JOIN public.event_l2_chains AS chain
                      ON chain.chain_id = member.l2_chain_id
                    WHERE member.macro_id = macro.macro_id
                      AND COALESCE(chain.title, member.title, '')
                          ILIKE :keyword ESCAPE '!'
               )
               OR EXISTS (
                    SELECT 1
                    FROM public.event_l3_macro_members AS member
                    JOIN public.event_l2_chain_segments AS segment
                      ON segment.chain_id = member.l2_chain_id
                    JOIN public.event_coref_members AS l1_member
                      ON l1_member.cluster_id = segment.l1_cluster_id
                    JOIN public.news AS article ON article.id = l1_member.news_id
                    WHERE member.macro_id = macro.macro_id
                      AND (
                          COALESCE(article.title, '') ILIKE :keyword ESCAPE '!'
                          OR LEFT(COALESCE(article.body, ''), 4000)
                              ILIKE :keyword ESCAPE '!'
                      )
               )
            ORDER BY macro.article_count DESC NULLS LAST, macro.macro_id ASC
            LIMIT :limit
            """,
            {"exact_id": keyword, "keyword": _like_pattern(keyword), "limit": limit},
        )

    def list_macros(self, limit: int) -> list[dict[str, Any]]:
        return self._all(
            """
            SELECT macro_id, macro_key, title, summary, family_group,
                   l2_chain_count, l1_cluster_count, segment_count, article_count,
                   start_date, end_date, actor_counts, topic_counts, quality_score
            FROM public.event_l3_macro_events
            ORDER BY article_count DESC NULLS LAST, macro_id ASC
            LIMIT :limit
            """,
            {"limit": limit},
        )

    def get_macro(self, macro_id: str) -> dict[str, Any] | None:
        return self._one(
            """
            SELECT macro_id, macro_key, title, summary, family_group,
                   l2_chain_count, l1_cluster_count, segment_count, article_count,
                   start_date, end_date, actor_counts, topic_counts, quality_score
            FROM public.event_l3_macro_events
            WHERE macro_id = :macro_id
            """,
            {"macro_id": macro_id},
        )

    def count_micros(self, macro_id: str) -> int:
        return self._scalar(
            """
            SELECT COUNT(*)
            FROM public.event_l3_macro_members
            WHERE macro_id = :macro_id
            """,
            {"macro_id": macro_id},
        )

    def list_micros(
        self,
        macro_id: str,
        limit: int,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        return self._all(
            """
            SELECT member.macro_id, member.l2_chain_id AS chain_id,
                   COALESCE(NULLIF(chain.title, ''), member.title, member.l2_chain_id)
                       AS title,
                   COALESCE(chain.start_date, member.start_date) AS start_date,
                   COALESCE(chain.end_date, member.end_date) AS end_date,
                   COALESCE(chain.article_count, member.article_count) AS article_count,
                   COALESCE(chain.segment_count, member.segment_count) AS segment_count,
                   chain.family_group, chain.event_family, chain.event_action,
                   chain.pair_key, chain.initiator, chain.target, chain.chain_quality,
                   chain.quality_score, member.importance_score, member.role, member.lane
            FROM public.event_l3_macro_members AS member
            LEFT JOIN public.event_l2_chains AS chain
              ON chain.chain_id = member.l2_chain_id
            WHERE member.macro_id = :macro_id
            ORDER BY COALESCE(chain.article_count, member.article_count, 0) DESC,
                     member.l2_chain_id ASC,
                     member.node_order ASC NULLS LAST
            LIMIT :limit OFFSET :offset
            """,
            {"macro_id": macro_id, "limit": limit, "offset": offset},
        )

    def micros_for_macros(
        self,
        macro_ids: Sequence[str],
        per_macro: int,
    ) -> list[dict[str, Any]]:
        if not macro_ids:
            return []
        return self._all(
            """
            WITH ranked AS (
                SELECT member.macro_id, member.l2_chain_id AS chain_id,
                       COALESCE(NULLIF(chain.title, ''), member.title,
                                member.l2_chain_id) AS title,
                       COALESCE(chain.start_date, member.start_date) AS start_date,
                       COALESCE(chain.end_date, member.end_date) AS end_date,
                       COALESCE(chain.article_count, member.article_count)
                           AS article_count,
                       COALESCE(chain.segment_count, member.segment_count)
                           AS segment_count,
                       chain.family_group, chain.event_family, chain.event_action,
                       chain.pair_key, chain.initiator, chain.target,
                       chain.chain_quality, chain.quality_score,
                       member.importance_score, member.role, member.lane,
                       ROW_NUMBER() OVER (
                           PARTITION BY member.macro_id
                           ORDER BY COALESCE(
                                        chain.article_count,
                                        member.article_count,
                                        0
                                    ) DESC,
                                    member.l2_chain_id ASC,
                                    member.node_order ASC NULLS LAST
                       ) AS row_number
                FROM public.event_l3_macro_members AS member
                LEFT JOIN public.event_l2_chains AS chain
                  ON chain.chain_id = member.l2_chain_id
                WHERE member.macro_id = ANY(:macro_ids)
            )
            SELECT *
            FROM ranked
            WHERE row_number <= :per_macro
            ORDER BY macro_id, row_number
            """,
            {"macro_ids": list(macro_ids), "per_macro": per_macro},
        )

    def get_micro(self, chain_id: str) -> dict[str, Any] | None:
        return self._one(
            """
            SELECT chain.chain_id, chain.title, chain.start_date, chain.end_date,
                   chain.article_count, chain.segment_count, chain.family_group,
                   chain.event_family, chain.event_action, chain.pair_key,
                   chain.initiator, chain.target, chain.chain_quality,
                   chain.quality_score, parent.macro_id, parent.importance_score,
                   parent.role, parent.lane
            FROM public.event_l2_chains AS chain
            LEFT JOIN LATERAL (
                SELECT member.macro_id, member.importance_score,
                       member.role, member.lane
                FROM public.event_l3_macro_members AS member
                WHERE member.l2_chain_id = chain.chain_id
                ORDER BY member.importance_score DESC NULLS LAST,
                         member.macro_id ASC
                LIMIT 1
            ) AS parent ON TRUE
            WHERE chain.chain_id = :chain_id
            """,
            {"chain_id": chain_id},
        )

    def news_for_micros(
        self,
        chain_ids: Sequence[str],
        limit_per: int,
    ) -> list[dict[str, Any]]:
        if not chain_ids:
            return []
        return self._all(
            """
            WITH linked AS (
                SELECT DISTINCT segment.chain_id, article.id AS news_id,
                       article.title,
                       LEFT(COALESCE(article.body, ''), 1200) AS abstract,
                       article.published_at AS pub_time,
                       article.url AS request_url,
                       article.language AS language_id
                FROM public.event_l2_chain_segments AS segment
                JOIN public.event_coref_members AS l1_member
                  ON l1_member.cluster_id = segment.l1_cluster_id
                JOIN public.news AS article ON article.id = l1_member.news_id
                WHERE segment.chain_id = ANY(:chain_ids)
            ), ranked AS (
                SELECT linked.*,
                       ROW_NUMBER() OVER (
                           PARTITION BY chain_id
                           ORDER BY pub_time DESC NULLS LAST, news_id DESC
                       ) AS row_number
                FROM linked
            )
            SELECT chain_id, news_id, title, abstract, pub_time,
                   request_url, language_id
            FROM ranked
            WHERE row_number <= :limit_per
            ORDER BY chain_id, row_number
            """,
            {"chain_ids": list(chain_ids), "limit_per": limit_per},
        )

    def count_news(self, chain_id: str) -> int:
        return self._scalar(
            """
            SELECT COUNT(DISTINCT article.id)
            FROM public.event_l2_chain_segments AS segment
            JOIN public.event_coref_members AS l1_member
              ON l1_member.cluster_id = segment.l1_cluster_id
            JOIN public.news AS article ON article.id = l1_member.news_id
            WHERE segment.chain_id = :chain_id
            """,
            {"chain_id": chain_id},
        )

    def list_news(
        self,
        chain_id: str,
        limit: int,
        offset: int,
        *,
        brief: bool,
    ) -> list[dict[str, Any]]:
        statement = _BRIEF_NEWS_SQL if brief else _FULL_NEWS_SQL
        return self._all(
            statement,
            {"chain_id": chain_id, "limit": limit, "offset": offset},
        )

    def list_unclustered_news(self, limit: int) -> list[dict[str, Any]]:
        return self._all(
            """
            SELECT article.id AS news_id, article.title,
                   article.published_at AS pub_time
            FROM public.news AS article
            WHERE NOT EXISTS (
                SELECT 1
                FROM public.event_coref_members AS l1_member
                JOIN public.event_l2_chain_segments AS segment
                  ON segment.l1_cluster_id = l1_member.cluster_id
                WHERE l1_member.news_id = article.id
            )
            ORDER BY article.published_at DESC NULLS LAST, article.id DESC
            LIMIT :limit
            """,
            {"limit": limit},
        )

    def list_ambient_news(
        self,
        limit: int,
        excluded_ids: Sequence[int],
    ) -> list[dict[str, Any]]:
        if limit <= 0:
            return []
        if excluded_ids:
            return self._all(
                """
                SELECT article.id AS news_id, article.title,
                       article.published_at AS pub_time
                FROM public.news AS article
                WHERE NOT (article.id = ANY(:excluded_ids))
                ORDER BY article.published_at DESC NULLS LAST, article.id DESC
                LIMIT :limit
                """,
                {"excluded_ids": list(excluded_ids), "limit": limit},
            )
        return self._all(
            """
            SELECT article.id AS news_id, article.title,
                   article.published_at AS pub_time
            FROM public.news AS article
            ORDER BY article.published_at DESC NULLS LAST, article.id DESC
            LIMIT :limit
            """,
            {"limit": limit},
        )

    def diagnostics(self) -> dict[str, int]:
        return {
            "news_total": self._scalar("SELECT COUNT(*) FROM public.news", {}),
            "macro_total": self._scalar(
                "SELECT COUNT(*) FROM public.event_l3_macro_events",
                {},
            ),
            "linked_news_distinct": self._scalar(
                """
                SELECT COUNT(DISTINCT l1_member.news_id)
                FROM public.event_l2_chain_segments AS segment
                JOIN public.event_coref_members AS l1_member
                  ON l1_member.cluster_id = segment.l1_cluster_id
                """,
                {},
            ),
        }

    def briefing_average(self, macro_id: str) -> float | None:
        value = self._db.execute(
            text(_BRIEFING_LINKED_CTE + "SELECT AVG(stance_score) FROM linked_scores"),
            {"macro_id": macro_id},
        ).scalar()
        return float(value) if value is not None else None

    def briefing_sentiment_distribution(
        self,
        macro_id: str,
    ) -> list[dict[str, Any]]:
        return self._all(
            _BRIEFING_LINKED_CTE
            + """
            SELECT CASE
                       WHEN stance_score > 0.15 THEN 'positive'
                       WHEN stance_score < -0.15 THEN 'negative'
                       ELSE 'neutral'
                   END AS label,
                   COUNT(*)::int AS count
            FROM linked_scores
            GROUP BY 1
            ORDER BY count DESC, label ASC
            """,
            {"macro_id": macro_id},
        )

    def briefing_topic_distribution(
        self,
        macro_id: str,
    ) -> list[dict[str, Any]]:
        return self._all(
            _BRIEFING_LINKED_CTE
            + """
            SELECT COALESCE(NULLIF(TRIM(event_family), ''), '(unlabelled)') AS label,
                   COUNT(*)::int AS count
            FROM linked_scores
            GROUP BY 1
            ORDER BY count DESC, label ASC
            LIMIT 60
            """,
            {"macro_id": macro_id},
        )


def _like_pattern(value: str) -> str:
    escaped = value.replace("!", "!!").replace("%", "!%").replace("_", "!_")
    return f"%{escaped}%"


_BRIEF_NEWS_SQL = """
    SELECT DISTINCT article.id, article.title,
           LEFT(COALESCE(article.body, ''), 1200) AS abstract,
           article.published_at AS pub_time, article.url AS request_url,
           article.language AS language_id
    FROM public.event_l2_chain_segments AS segment
    JOIN public.event_coref_members AS l1_member
      ON l1_member.cluster_id = segment.l1_cluster_id
    JOIN public.news AS article ON article.id = l1_member.news_id
    WHERE segment.chain_id = :chain_id
    ORDER BY article.published_at DESC NULLS LAST, article.id DESC
    LIMIT :limit OFFSET :offset
"""

_FULL_NEWS_SQL = """
    SELECT DISTINCT article.id, article.title,
           LEFT(COALESCE(article.body, ''), 1200) AS abstract, article.body,
           article.published_at AS pub_time, article.url AS request_url,
           article.language AS language_id
    FROM public.event_l2_chain_segments AS segment
    JOIN public.event_coref_members AS l1_member
      ON l1_member.cluster_id = segment.l1_cluster_id
    JOIN public.news AS article ON article.id = l1_member.news_id
    WHERE segment.chain_id = :chain_id
    ORDER BY article.published_at DESC NULLS LAST, article.id DESC
    LIMIT :limit OFFSET :offset
"""

_BRIEFING_LINKED_CTE = """
    WITH linked_scores AS (
        SELECT DISTINCT score.news_id, score.stance_score, score.event_family
        FROM public.event_l3_macro_members AS macro_member
        JOIN public.event_l2_chain_segments AS segment
          ON segment.chain_id = macro_member.l2_chain_id
        JOIN public.event_coref_members AS l1_member
          ON l1_member.cluster_id = segment.l1_cluster_id
        JOIN public.china_opinion_article_scores AS score
          ON score.news_id = l1_member.news_id
        WHERE macro_member.macro_id = :macro_id
    )
"""
