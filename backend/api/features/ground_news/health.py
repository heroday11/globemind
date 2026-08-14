"""Live capability probe for Ground News persistence."""
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.orm import Session

from api.features import FeatureHealthCheck, probe_postgres_relations, run_feature_probe
from api.features.freshness import apply_freshness, freshness_sla_hours

_GROUND_NEWS_RELATIONS = {
    "public.news": ("id", "title", "published_at"),
    "public.event_coref_clusters": ("cluster_id",),
    "public.event_l15_segments": ("segment_id",),
    "public.media_source_profile": ("domain",),
}


def probe_ground_news_health(db: Session) -> FeatureHealthCheck:
    check = run_feature_probe(
        "ground-news",
        ("postgres:ground-news",),
        lambda: probe_postgres_relations(db, _GROUND_NEWS_RELATIONS),
    )
    if check.status == "down":
        return check
    try:
        latest = db.execute(text("SELECT MAX(published_at) FROM public.news WHERE published_at <= now()" )).scalar()
    except Exception:
        return check.model_copy(update={"status": "down", "detail": "business freshness probe failed"})
    return apply_freshness(
        check,
        latest,
        sla_hours=freshness_sla_hours("GLOBEMIND_GROUND_NEWS_FRESHNESS_SLA_HOURS", 48),
        metric_name="latest_story_source_at",
    )


__all__ = ("probe_ground_news_health",)
