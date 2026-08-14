"""Live data-freshness probe for China opinion analysis."""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.orm import Session

from api.features import FeatureHealthCheck, run_feature_probe
from api.features.freshness import apply_freshness, freshness_sla_hours


def probe_opinion_health(db: Session) -> FeatureHealthCheck:
    check = run_feature_probe(
        "opinion-analysis",
        ("postgres:china-opinion-scores",),
        lambda: {
            "scores_readable": bool(
                db.execute(text("SELECT 1 FROM public.china_opinion_article_scores LIMIT 1")).scalar()
                or 0
            )
        },
    )
    if check.status == "down":
        return check
    try:
        latest = db.execute(text("SELECT MAX(published_date) FROM public.china_opinion_article_scores")).scalar()
    except Exception:
        return check.model_copy(update={"status": "down", "detail": "business freshness probe failed"})
    return apply_freshness(
        check,
        latest,
        sla_hours=freshness_sla_hours("GLOBEMIND_OPINION_FRESHNESS_SLA_HOURS", 72),
        metric_name="latest_score_date",
    )


__all__ = ("probe_opinion_health",)
