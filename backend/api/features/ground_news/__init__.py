"""Public backend API for Ground News capabilities."""

from api.features.ground_news.health import probe_ground_news_health
from api.features.ground_news.source_profile import (
    SOURCE_PROFILE_CONTRACT_VERSION,
    SOURCE_PROFILE_METHOD_CARD_SCHEMA_VERSION,
    build_source_profile_contract,
)

__all__ = (
    "SOURCE_PROFILE_CONTRACT_VERSION",
    "SOURCE_PROFILE_METHOD_CARD_SCHEMA_VERSION",
    "build_source_profile_contract",
    "probe_ground_news_health",
)
