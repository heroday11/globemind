"""Fixed V0.9.3 PostgreSQL runtime-role policy.

This module is intentionally data-only. The provisioning CLI accepts no role,
database, schema, table, sequence, or SQL overrides.
"""

from __future__ import annotations

from dataclasses import dataclass

POLICY_SCHEMA_VERSION = 1
DATABASE = "news"
SCHEMA = "public"
OWNER_ROLE = "postgres"

LEGACY_RELATION_GAPS = {}

RESOLVED_LEGACY_SURFACES = {
    "api_graph": {
        "mount": "/api/graph/*",
        "current_behavior": "migrated_current_l3_l2_l1",
        "current_relations": (
            "event_l3_macro_events",
            "event_l3_macro_members",
            "event_l2_chains",
            "event_l2_chain_segments",
            "event_coref_members",
            "news",
            "china_opinion_article_scores",
        ),
    },
    "legacy_opinion": {
        "mount": "/api/opinion/{micro-story-sub-events,event-timeseries,global-attention,sentiment-polarity,influence-index,composite-index,topic-breakdown,frame-breakdown,narrative-dispersion}",
        "current_behavior": "retired_410",
    },
    "legacy_search": {
        "mount": "/api/dashboard/search/v11-clusters*",
        "current_behavior": "migrated_current_l3_l2_l1",
        "current_relations": (
            "event_l3_macro_events",
            "event_l3_macro_members",
            "event_l2_chains",
            "event_l2_chain_segments",
            "event_coref_clusters",
            "event_coref_members",
            "news",
        ),
    },
}

SELECT = ("SELECT",)

CURRENT_STORY_GRAPH_TABLES = (
    "event_l2_chains",
    "event_l2_chain_segments",
    "event_l15_segments",
    "event_l15_members",
    "event_l3_macro_events",
    "event_l3_macro_members",
    "event_l3_macro_edges",
    "event_coref_clusters",
    "event_coref_members",
    "news",
)

WEB_TABLES = (
    "app_user",
    "assistant_chat_message",
    "assistant_chat_session",
    "assistant_user_memory",
    "china_opinion_article_scores",
    "china_opinion_feedback",
    "event_coref_clusters",
    "event_coref_members",
    "event_l15_members",
    "event_l15_segments",
    "event_l2_chain_segments",
    "event_l2_chains",
    "event_l3_macro_edges",
    "event_l3_macro_events",
    "event_l3_macro_members",
    "language",
    "lxy_translated",
    "media_source",
    "media_source_profile",
    "news",
    "news_embeddings",
    "news_image_assets",
    "news_l1_event_extractions",
    "news_l1_prep",
    "news_quality_labels",
    "password_reset_token",
    "story_cover_assets",
    "story_source_breakdown",
    "user_favorite",
    "user_search_history",
    "v3_media",
)

WEB_WRITE_PRIVILEGES = {
    "app_user": ("SELECT", "INSERT", "UPDATE"),
    "assistant_chat_message": ("SELECT", "INSERT", "UPDATE", "DELETE"),
    "assistant_chat_session": ("SELECT", "INSERT", "UPDATE", "DELETE"),
    "assistant_user_memory": ("SELECT", "INSERT", "UPDATE", "DELETE"),
    "china_opinion_article_scores": ("SELECT", "INSERT", "UPDATE", "DELETE"),
    "china_opinion_feedback": ("SELECT", "INSERT"),
    "password_reset_token": ("SELECT", "INSERT", "UPDATE", "DELETE"),
    "user_favorite": ("SELECT", "INSERT", "DELETE"),
    "user_search_history": ("SELECT", "INSERT", "DELETE"),
}

WEB_SEQUENCES = (
    "app_user_id_seq",
    "assistant_chat_message_id_seq",
    "assistant_chat_session_id_seq",
    "assistant_user_memory_id_seq",
    "china_opinion_feedback_id_seq",
    "password_reset_token_id_seq",
    "user_favorite_id_seq",
    "user_search_history_id_seq",
)

LOADER_TABLE_PRIVILEGES = {
    "news": ("SELECT", "INSERT"),
    "media_source": ("SELECT", "INSERT", "UPDATE"),
    "globemind_pipeline_checkpoint": ("SELECT", "INSERT", "UPDATE"),
}
LOADER_SEQUENCES = ("news_id_seq", "media_source_id_seq")


@dataclass(frozen=True)
class RolePolicy:
    name: str
    connection_limit: int
    table_privileges: dict[str, tuple[str, ...]]
    sequences: tuple[str, ...]


WEB_TABLE_PRIVILEGES = {table: WEB_WRITE_PRIVILEGES.get(table, SELECT) for table in WEB_TABLES}

ROLE_POLICIES = (
    RolePolicy(
        name="web_runtime",
        connection_limit=64,
        table_privileges=WEB_TABLE_PRIVILEGES,
        sequences=WEB_SEQUENCES,
    ),
    RolePolicy(
        name="wave1_loader",
        connection_limit=2,
        table_privileges=LOADER_TABLE_PRIVILEGES,
        sequences=LOADER_SEQUENCES,
    ),
)

ALL_REQUIRED_TABLES = tuple(
    sorted({table for role in ROLE_POLICIES for table in role.table_privileges})
)
ALL_REQUIRED_SEQUENCES = tuple(
    sorted({sequence for role in ROLE_POLICIES for sequence in role.sequences})
)
ALLOWED_TABLE_PRIVILEGES = frozenset(
    {
        "SELECT",
        "INSERT",
        "UPDATE",
        "DELETE",
        "TRUNCATE",
        "REFERENCES",
        "TRIGGER",
    }
)
ALLOWED_SEQUENCE_PRIVILEGES = frozenset({"SELECT", "USAGE", "UPDATE"})
EXPECTED_ROLE_SETTINGS = {
    "search_path": "public, pg_catalog",
    "statement_timeout": "60s",
    "lock_timeout": "5s",
    "idle_in_transaction_session_timeout": "60s",
}
