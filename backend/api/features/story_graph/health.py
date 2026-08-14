"""Dependency-inverted live capability contract for Story Graph."""

from __future__ import annotations

from collections.abc import Callable, Mapping

from api.features import FeatureHealthCheck, run_feature_probe

STORY_GRAPH_HEALTH_RELATIONS = {
    "public.event_l2_chains": ("run_id", "chain_id", "title"),
    "public.event_l2_chain_segments": (
        "run_id",
        "chain_id",
        "segment_id",
        "l15_run_id",
    ),
    "public.event_l15_segments": ("segment_id", "l1_cluster_id", "title"),
    "public.event_l15_members": ("run_id", "segment_id", "news_id"),
    "public.event_l3_macro_events": ("run_id", "macro_id", "title"),
    "public.event_l3_macro_members": (
        "run_id",
        "macro_id",
        "l2_run_id",
        "l2_chain_id",
    ),
    "public.event_l3_macro_edges": (
        "run_id",
        "macro_id",
        "from_chain_id",
        "to_chain_id",
    ),
    "public.event_coref_clusters": ("cluster_id", "title"),
    "public.event_coref_members": ("cluster_id", "news_id"),
    "public.news": ("id", "title", "published_at", "url"),
}


def probe_story_graph_health(
    relation_probe: Callable[[], Mapping[str, int | float | str | bool]],
) -> FeatureHealthCheck:
    return run_feature_probe(
        "story-graph",
        ("postgres:story-graph-current-l2-l3",),
        relation_probe,
    )


__all__ = ("STORY_GRAPH_HEALTH_RELATIONS", "probe_story_graph_health")
