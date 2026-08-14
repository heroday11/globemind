"""Shared read-query fragments with unreviewed feedback fail-closed."""

LATEST_FEEDBACK_CTE = """
latest_feedback AS (
    SELECT NULL::bigint AS news_id,
           NULL::text AS correction,
           NULL::timestamptz AS created_at
    WHERE FALSE
)
"""
EFFECTIVE_STANCE_EXPR = "s.stance_score"
FEEDBACK_VISIBLE_EXPR = "TRUE"
VALID_SCORE_EXPR = """
s.published_date IS NOT NULL
AND s.stance_score BETWEEN -1.0 AND 1.0
AND s.confidence BETWEEN 0.0 AND 1.0
AND s.relevance_score BETWEEN 0.0 AND 1.0
AND s.article_weight > 0.0
AND s.article_weight <= 1.0
AND s.method_version = :method_version
"""


__all__ = (
    "EFFECTIVE_STANCE_EXPR",
    "FEEDBACK_VISIBLE_EXPR",
    "LATEST_FEEDBACK_CTE",
    "VALID_SCORE_EXPR",
)
