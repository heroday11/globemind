"""Read-only dashboard persistence capability."""
from __future__ import annotations

from sqlalchemy.orm import Session

from api.features import FeatureHealthCheck, probe_postgres_relations, run_feature_probe

_DASHBOARD_RELATIONS = {
    "public.news": ("id", "title", "body", "url", "published_at", "language"),
}


def probe_dashboard_health(db: Session) -> FeatureHealthCheck:
    return run_feature_probe(
        "dashboard",
        ("postgres:news",),
        lambda: probe_postgres_relations(db, _DASHBOARD_RELATIONS),
    )


__all__ = ("probe_dashboard_health",)
