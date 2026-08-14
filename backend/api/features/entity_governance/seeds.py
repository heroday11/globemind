"""Bounded seed discovery through the public search feature facade."""

from __future__ import annotations

from typing import Any

from api.features.search import (
    ENTITY_ALIAS_CATALOG_REVIEW_STATUS,
    ENTITY_ALIAS_CATALOG_VERSION,
    resolve_entity_alias,
)

SEARCH_SEED_PROBES = (
    "China",
    "United States",
    "Russia",
    "Ukraine",
    "Japan",
    "South Korea",
    "North Korea",
    "India",
    "Iran",
    "Israel",
    "Palestine",
    "Myanmar",
    "Türkiye",
    "Xi Jinping",
    "Vladimir Putin",
    "Donald Trump",
    "United Nations",
    "NATO",
    "South China Sea",
    "Taiwan Strait",
    "Gaza Strip",
)


class SeedCatalogUnavailable(RuntimeError):
    pass


def load_search_seed_entities() -> dict[str, dict[str, Any]]:
    """Resolve the bounded checked-in seed inventory without private imports."""
    entities: dict[str, dict[str, Any]] = {}
    for probe in SEARCH_SEED_PROBES:
        match = resolve_entity_alias(probe)
        if match is None:
            raise SeedCatalogUnavailable("SEARCH_ENTITY_SEED_RESOLUTION_UNAVAILABLE")
        entity_id = str(match.entity_id)
        if entity_id in entities:
            raise SeedCatalogUnavailable("SEARCH_ENTITY_SEED_DUPLICATED")
        if not entity_id.startswith(f"urn:globemind:entity:{match.entity_type}:"):
            raise SeedCatalogUnavailable("SEARCH_ENTITY_SEED_ID_INVALID")
        aliases = [dict(item) for item in match.alias_details]
        if not aliases:
            raise SeedCatalogUnavailable("SEARCH_ENTITY_SEED_ALIASES_UNAVAILABLE")
        entities[entity_id] = {
            "entity_id": entity_id,
            "entity_type": str(match.entity_type),
            "canonical_names": dict(match.canonical_names),
            "aliases": aliases,
            "source_catalog_version": str(match.catalog_version),
            "source_catalog_review_status": str(match.review_status),
            "source_review_note": str(match.review_note),
            "source_valid_from": match.valid_from,
            "source_valid_to": match.valid_to,
            "governance_review_status": "review_required",
        }
    if ENTITY_ALIAS_CATALOG_REVIEW_STATUS not in {"approved", "review_required"}:
        raise SeedCatalogUnavailable("SEARCH_ENTITY_CATALOG_REVIEW_STATE_INVALID")
    if not ENTITY_ALIAS_CATALOG_VERSION:
        raise SeedCatalogUnavailable("SEARCH_ENTITY_CATALOG_VERSION_UNAVAILABLE")
    return entities


__all__ = (
    "SEARCH_SEED_PROBES",
    "SeedCatalogUnavailable",
    "load_search_seed_entities",
)
