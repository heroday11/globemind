"""Live capability probe for search persistence."""
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.orm import Session

from api.features import FeatureHealthCheck, probe_postgres_relations, run_feature_probe
from api.features.freshness import apply_freshness, freshness_sla_hours

_SEARCH_RELATIONS = {
    "public.news": ("id", "title", "body", "published_at", "language"),
    "public.event_coref_clusters": ("cluster_id",),
    "public.event_l2_chains": ("chain_id",),
    "public.event_l3_macro_events": ("macro_id",),
}


def probe_search_health(db: Session) -> FeatureHealthCheck:
    check = run_feature_probe(
        "search",
        ("postgres:search-hierarchy",),
        lambda: probe_postgres_relations(db, _SEARCH_RELATIONS),
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
        sla_hours=freshness_sla_hours("GLOBEMIND_NEWS_FRESHNESS_SLA_HOURS", 48),
        metric_name="latest_news_at",
    )


__all__ = ("probe_search_health",)
