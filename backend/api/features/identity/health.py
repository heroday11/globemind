"""Live capability probe for identity persistence."""
from __future__ import annotations

from sqlalchemy.orm import Session

from api.features import FeatureHealthCheck, probe_postgres_relations, run_feature_probe

_IDENTITY_RELATIONS = {
    "public.app_user": (
        "id",
        "username",
        "password_hash",
        "is_active",
        "role",
        "api_keys",
    ),
    "public.user_search_history": ("id", "user_id", "keyword"),
    "public.user_favorite": ("id", "user_id", "news_id", "topic", "item_kind"),
    "public.assistant_chat_session": ("id", "user_id", "title"),
    "public.assistant_chat_message": ("id", "session_id", "user_id", "role"),
    "public.assistant_user_memory": ("id", "user_id", "memory_summary"),
}


def probe_identity_health(db: Session) -> FeatureHealthCheck:
    return run_feature_probe(
        "identity",
        ("postgres:identity",),
        lambda: probe_postgres_relations(db, _IDENTITY_RELATIONS),
    )


__all__ = ("probe_identity_health",)
