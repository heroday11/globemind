"""Live capability probe for graph briefing persistence."""
from __future__ import annotations

from sqlalchemy.orm import Session

from api.features import FeatureHealthCheck, probe_postgres_relations, run_feature_probe

_GRAPH_BRIEFING_RELATIONS = {
    "public.event_l3_macro_events": ("macro_id", "title"),
    "public.event_l3_macro_members": ("macro_id", "l2_chain_id"),
    "public.event_l2_chains": ("chain_id",),
    "public.event_l2_chain_segments": ("chain_id",),
    "public.news": ("id", "title"),
}


def probe_graph_briefing_health(db: Session) -> FeatureHealthCheck:
    return run_feature_probe(
        "graph-briefing",
        ("postgres:graph-briefing",),
        lambda: probe_postgres_relations(db, _GRAPH_BRIEFING_RELATIONS),
    )


__all__ = ("probe_graph_briefing_health",)
