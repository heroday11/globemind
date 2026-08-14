"""Versioned, curated entity aliases used by the dashboard search contract."""

from __future__ import annotations

import json
import math
import os
import re
import stat
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

_CATALOG_PATH = Path(__file__).with_name("data") / "entity_aliases.v2.json"
_SUPPORTED_ENTITY_TYPES = frozenset({"country", "person", "organization", "location"})
_REVIEW_STATUSES = frozenset({"approved", "review_required"})
_ALIAS_STATUSES = frozenset({"active", "context_dependent", "review_required"})
_ALIAS_KINDS = frozenset(
    {
        "preferred_name",
        "formal_name",
        "abbreviation",
        "alternative_name",
        "transliterated_name",
        "historical_or_contextual_name",
    }
)
_LANGUAGE_TAG = re.compile(r"^(?:[A-Za-z]{2,8}(?:-[A-Za-z0-9]{1,8})*|und)$")
_ENTITY_ID = re.compile(
    r"^urn:globemind:entity:(country|person|organization|location):"
    r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"
)
_COUNTRY_ID = re.compile(r"^urn:globemind:entity:country:[A-Z]{2}$")
_CURATED_ENTITY_ID = re.compile(
    r"^urn:globemind:entity:(person|organization|location):"
    r"[a-z0-9][a-z0-9._-]{0,127}$"
)
_REVIEW_EVIDENCE_URN = re.compile(
    r"^urn:globemind:review-evidence:[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$"
)
_MAX_CATALOG_BYTES = 1024 * 1024
_MAX_ENTITIES = 10_000
_MAX_ALIASES_PER_ENTITY = 500
_FORBIDDEN_RELEASE_ROOT = Path("/root/data/releases/globemind")
_ENTITY_KEYS = frozenset(
    {
        "entity_id",
        "entity_type",
        "canonical_names",
        "aliases",
        "review_status",
        "review_note",
        "valid_from",
        "valid_to",
        "reviewed_at",
        "reviewed_by",
        "review_evidence",
        "accuracy_claim",
    }
)
_ALIAS_KEYS = frozenset({"value", "language", "kind", "status"})


@dataclass(frozen=True)
class EntityAliasMatch:
    entity_id: str
    entity_type: str
    canonical_names: dict[str, str]
    matched_alias: str
    aliases: tuple[str, ...]
    alias_details: tuple[dict[str, str], ...]
    catalog_version: str
    review_status: str
    review_note: str
    matched_alias_status: str
    valid_from: str | None
    valid_to: str | None
    reviewed_at: str | None
    reviewed_by: str | None
    review_evidence: str | None

    def as_explain_dict(self) -> dict[str, Any]:
        return {
            "entity_id": self.entity_id,
            "entity_type": self.entity_type,
            "canonical_names": dict(self.canonical_names),
            "matched_alias": self.matched_alias,
            "expanded_aliases": list(self.aliases),
            "expanded_alias_details": [dict(item) for item in self.alias_details],
            "catalog_version": self.catalog_version,
            "review_status": self.review_status,
            "review_note": self.review_note,
            "matched_alias_status": self.matched_alias_status,
            "valid_from": self.valid_from,
            "valid_to": self.valid_to,
            "reviewed_at": self.reviewed_at,
            "reviewed_by": self.reviewed_by,
            "review_evidence": self.review_evidence,
        }


def _optional_iso_date(value: Any, *, field: str, entity_id: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value or value.strip() != value:
        raise RuntimeError(f"search entity {entity_id} has an invalid {field}")
    raw = value
    try:
        parsed = date.fromisoformat(raw)
    except ValueError as exc:
        raise RuntimeError(f"search entity {entity_id} has an invalid {field}") from exc
    return parsed.isoformat()


def _optional_review_time(value: Any, *, entity_id: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value or value.strip() != value:
        raise RuntimeError(f"search entity {entity_id} has an invalid reviewed_at")
    raw = value
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise RuntimeError(f"search entity {entity_id} has an invalid reviewed_at") from exc
    if parsed.tzinfo is None:
        raise RuntimeError(f"search entity {entity_id} has an invalid reviewed_at")
    normalized = parsed.astimezone(timezone.utc)
    if normalized > datetime.now(timezone.utc):
        raise RuntimeError(f"search entity {entity_id} review is dated in the future")
    return normalized.isoformat().replace("+00:00", "Z")


def _strict_text(
    value: Any,
    *,
    field: str,
    maximum: int,
    pattern: re.Pattern[str] | None = None,
) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > maximum
        or value.strip() != value
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
        or unicodedata.normalize("NFC", value) != value
        or (pattern is not None and pattern.fullmatch(value) is None)
    ):
        raise RuntimeError(f"search entity catalog has an invalid {field}")
    return value


def _safe_review_evidence(value: str, *, field: str) -> str:
    evidence = _strict_text(value, field=field, maximum=500)
    if _REVIEW_EVIDENCE_URN.fullmatch(evidence) is not None:
        return evidence
    if "\\" in evidence:
        raise RuntimeError(f"search entity catalog has an unsafe {field}")
    parsed = urlsplit(evidence)
    try:
        port = parsed.port
    except ValueError as exc:
        raise RuntimeError(f"search entity catalog has an unsafe {field}") from exc
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or port not in {None, 443}
    ):
        raise RuntimeError(f"search entity catalog has an unsafe {field}")
    return evidence


def _reject_duplicate_json_keys(
    pairs: list[tuple[str, Any]],
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise RuntimeError("search entity catalog JSON has duplicate keys")
        result[key] = value
    return result


def _reject_non_finite_json_number(_value: str) -> None:
    raise RuntimeError("search entity catalog JSON has a non-finite number")


def _finite_json_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed):
        _reject_non_finite_json_number(value)
    return parsed


def _path_has_symlink(path: Path) -> bool:
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current = current / part
        if current.is_symlink():
            return True
    return False


def _read_catalog_payload() -> dict[str, Any]:
    path = Path(os.path.abspath(os.fspath(_CATALOG_PATH)))
    try:
        path.relative_to(_FORBIDDEN_RELEASE_ROOT)
    except ValueError:
        pass
    else:
        raise RuntimeError("search entity catalog release paths are forbidden")
    if _path_has_symlink(path):
        raise RuntimeError("search entity catalog path contains a symbolic link")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = -1
    try:
        descriptor = os.open(path, flags)
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise RuntimeError("search entity catalog file is unsafe")
        if before.st_size > _MAX_CATALOG_BYTES:
            raise RuntimeError("search entity catalog exceeds its size limit")
        encoded = b""
        while len(encoded) <= _MAX_CATALOG_BYTES:
            chunk = os.read(
                descriptor,
                min(65_536, _MAX_CATALOG_BYTES + 1 - len(encoded)),
            )
            if not chunk:
                break
            encoded += chunk
        after = os.fstat(descriptor)
        if len(encoded) > _MAX_CATALOG_BYTES:
            raise RuntimeError("search entity catalog exceeds its size limit")
        if (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        ) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        ):
            raise RuntimeError("search entity catalog changed while being read")
        payload = json.loads(
            encoded.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_json_keys,
            parse_constant=_reject_non_finite_json_number,
            parse_float=_finite_json_float,
        )
    except RuntimeError:
        raise
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError, RecursionError) as exc:
        raise RuntimeError("search entity catalog JSON is unreadable") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if not isinstance(payload, dict):
        raise RuntimeError("search entity catalog JSON root must be an object")
    return payload


def _load_catalog() -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    payload = _read_catalog_payload()
    if (
        isinstance(payload.get("schema_version"), bool)
        or not isinstance(payload.get("schema_version"), int)
        or payload.get("schema_version") != 2
    ):
        raise RuntimeError("unsupported search entity alias schema")
    _strict_text(
        payload.get("catalog_version"),
        field="catalog_version",
        maximum=200,
    )
    if payload.get("accuracy_claim") != "not_measured":
        raise RuntimeError("search entity alias catalog has an unsupported accuracy claim")
    if payload.get("curation_method") != "ai_seed":
        raise RuntimeError("search entity alias catalog has an unsupported curation method")

    alias_index: dict[str, dict[str, Any]] = {}
    entity_ids: set[str] = set()
    default_review_status = payload.get("default_review_status", "review_required")
    if default_review_status not in _REVIEW_STATUSES:
        raise RuntimeError("search entity catalog has an invalid default review status")
    catalog_review_status = payload.get("catalog_review_status")
    if catalog_review_status not in _REVIEW_STATUSES:
        raise RuntimeError("search entity catalog has an invalid review lifecycle")
    human_review_evidence = payload.get("human_review_evidence")
    if catalog_review_status == "approved":
        if not isinstance(human_review_evidence, str):
            raise RuntimeError("approved search entity catalog has no review evidence")
        _safe_review_evidence(
            human_review_evidence,
            field="human_review_evidence",
        )
    lifecycle = payload.get("review_lifecycle")
    statuses = lifecycle.get("statuses") if isinstance(lifecycle, dict) else None
    if (
        not isinstance(statuses, list)
        or any(not isinstance(item, str) for item in statuses)
        or len(statuses) != len(_REVIEW_STATUSES)
        or set(statuses) != _REVIEW_STATUSES
    ):
        raise RuntimeError("search entity catalog has no valid review lifecycle contract")
    entities = payload.get("entities")
    if not isinstance(entities, list) or not 1 <= len(entities) <= _MAX_ENTITIES:
        raise RuntimeError("search entity alias catalog has an invalid entity inventory")
    for entity in entities:
        if not isinstance(entity, dict):
            raise RuntimeError("search entity alias catalog contains a non-object entity")
        if set(entity) - _ENTITY_KEYS:
            raise RuntimeError("search entity alias catalog contains unknown entity fields")
        entity_id = _strict_text(
            entity.get("entity_id"),
            field="entity_id",
            maximum=180,
            pattern=_ENTITY_ID,
        )
        if not entity_id or entity_id in entity_ids:
            raise RuntimeError("search entity alias catalog has an invalid entity_id")
        entity_ids.add(entity_id)
        entity_type = entity.get("entity_type")
        if entity_type not in _SUPPORTED_ENTITY_TYPES:
            raise RuntimeError(f"search entity {entity_id} has an invalid entity_type")
        if not entity_id.startswith(f"urn:globemind:entity:{entity_type}:"):
            raise RuntimeError(f"search entity {entity_id} does not match its type")
        if entity_type == "country" and _COUNTRY_ID.fullmatch(entity_id) is None:
            raise RuntimeError(f"search entity {entity_id} has an unstable country identifier")
        if entity_type != "country" and _CURATED_ENTITY_ID.fullmatch(entity_id) is None:
            raise RuntimeError(f"search entity {entity_id} has an unstable curated identifier")
        if entity.get("accuracy_claim", "not_measured") != "not_measured":
            raise RuntimeError(f"search entity {entity_id} has an unsupported accuracy claim")
        entity["accuracy_claim"] = "not_measured"
        canonical_names = entity.get("canonical_names") or {}
        if not isinstance(canonical_names, dict) or not 1 <= len(canonical_names) <= 20:
            raise RuntimeError(f"search entity {entity_id} has no canonical name")
        for language, name in canonical_names.items():
            _strict_text(
                language,
                field=f"canonical name language for {entity_id}",
                maximum=48,
                pattern=_LANGUAGE_TAG,
            )
            _strict_text(
                name,
                field=f"canonical name for {entity_id}",
                maximum=300,
            )
        review_status = entity.get("review_status", default_review_status)
        if review_status not in _REVIEW_STATUSES:
            raise RuntimeError(f"search entity {entity_id} has an invalid review status")
        entity["review_status"] = review_status
        entity["review_note"] = _strict_text(
            entity.get("review_note")
            or payload.get("default_review_note")
            or "No human review evidence is recorded.",
            field=f"review note for {entity_id}",
            maximum=1000,
        )
        valid_from = _optional_iso_date(
            entity.get("valid_from", payload.get("default_valid_from")),
            field="valid_from",
            entity_id=entity_id,
        )
        valid_to = _optional_iso_date(
            entity.get("valid_to", payload.get("default_valid_to")),
            field="valid_to",
            entity_id=entity_id,
        )
        if valid_from is not None and valid_to is not None and valid_from > valid_to:
            raise RuntimeError(f"search entity {entity_id} has an inverted validity interval")
        reviewed_at = _optional_review_time(entity.get("reviewed_at"), entity_id=entity_id)
        reviewed_by = entity.get("reviewed_by")
        if reviewed_by is not None:
            reviewed_by = _strict_text(
                reviewed_by,
                field=f"reviewed_by for {entity_id}",
                maximum=200,
            )
        review_evidence = entity.get("review_evidence")
        if review_evidence is not None:
            if not isinstance(review_evidence, str):
                raise RuntimeError(f"search entity {entity_id} has invalid review evidence")
            review_evidence = _safe_review_evidence(
                review_evidence,
                field=f"review_evidence for {entity_id}",
            )
        review_fields = (reviewed_at, reviewed_by, review_evidence)
        if any(item is not None for item in review_fields) and not all(review_fields):
            raise RuntimeError(f"search entity {entity_id} has partial review evidence")
        if review_status == "approved" and not all(review_fields):
            raise RuntimeError(f"search entity {entity_id} approval has no review evidence")
        entity.update(
            {
                "valid_from": valid_from,
                "valid_to": valid_to,
                "reviewed_at": reviewed_at,
                "reviewed_by": reviewed_by,
                "review_evidence": review_evidence,
            }
        )
        aliases = entity.get("aliases")
        if not isinstance(aliases, list) or not 1 <= len(aliases) <= _MAX_ALIASES_PER_ENTITY:
            raise RuntimeError(f"search entity {entity_id} has no aliases")
        for alias in aliases:
            if not isinstance(alias, dict):
                raise RuntimeError(f"search entity {entity_id} has a non-object alias")
            if set(alias) - _ALIAS_KEYS:
                raise RuntimeError(f"search entity {entity_id} alias has unknown fields")
            value = _strict_text(
                alias.get("value"),
                field=f"alias value for {entity_id}",
                maximum=300,
            )
            language = _strict_text(
                alias.get("language"),
                field=f"alias language for {entity_id}",
                maximum=48,
                pattern=_LANGUAGE_TAG,
            )
            kind = alias.get("kind")
            if not isinstance(kind, str) or kind not in _ALIAS_KINDS:
                raise RuntimeError(f"search alias {value!r} has an invalid kind")
            key = value.casefold()
            existing = alias_index.get(key)
            if existing is not None:
                raise RuntimeError(f"search alias {value!r} has multiple alias records")
            alias_status = alias.get("status", "active")
            if not isinstance(alias_status, str) or alias_status not in _ALIAS_STATUSES:
                raise RuntimeError(f"search alias {value!r} has an invalid status")
            alias_index[key] = {
                "entity": entity,
                "alias_status": alias_status,
                "language": language,
                "kind": kind,
            }
    return payload, alias_index


ENTITY_ALIAS_CATALOG, _ENTITY_ALIAS_INDEX = _load_catalog()
ENTITY_ALIAS_CATALOG_VERSION = str(ENTITY_ALIAS_CATALOG["catalog_version"])
ENTITY_ALIAS_SCHEMA_VERSION = int(ENTITY_ALIAS_CATALOG["schema_version"])
ENTITY_ALIAS_CATALOG_REVIEW_STATUS = str(
    ENTITY_ALIAS_CATALOG["catalog_review_status"]
)


def resolve_entity_alias(value: Any) -> EntityAliasMatch | None:
    if not isinstance(value, str):
        return None
    raw = value.strip()
    if not raw:
        return None
    indexed = _ENTITY_ALIAS_INDEX.get(raw.casefold())
    if indexed is None:
        return None
    entity = indexed["entity"]
    alias_details = tuple(
        {
            "value": str(alias["value"]).strip(),
            "language": str(alias.get("language") or "und").strip(),
            "kind": str(alias.get("kind") or "unspecified").strip(),
            "status": str(alias.get("status") or "active").strip(),
        }
        for alias in entity.get("aliases") or []
        if str(alias.get("value") or "").strip()
    )
    executable_aliases: list[str] = [raw]
    seen_aliases = {raw.casefold()}
    for alias in alias_details:
        value = alias["value"]
        if alias["status"] != "active" or value.casefold() in seen_aliases:
            continue
        executable_aliases.append(value)
        seen_aliases.add(value.casefold())
    return EntityAliasMatch(
        entity_id=str(entity["entity_id"]),
        entity_type=str(entity.get("entity_type") or "entity"),
        canonical_names={
            str(key): str(name)
            for key, name in (entity.get("canonical_names") or {}).items()
        },
        matched_alias=raw,
        aliases=tuple(executable_aliases),
        alias_details=alias_details,
        catalog_version=ENTITY_ALIAS_CATALOG_VERSION,
        review_status=str(entity["review_status"]),
        review_note=str(entity["review_note"]),
        matched_alias_status=str(indexed["alias_status"]),
        valid_from=entity["valid_from"],
        valid_to=entity["valid_to"],
        reviewed_at=entity["reviewed_at"],
        reviewed_by=entity["reviewed_by"],
        review_evidence=entity["review_evidence"],
    )


def entity_alias_variants(value: Any) -> tuple[str, ...]:
    if not isinstance(value, str):
        return ()
    match = resolve_entity_alias(value)
    if match is None:
        raw = value.strip()
        return (raw,) if raw else ()
    variants: list[str] = []
    seen: set[str] = set()
    for alias in (match.matched_alias, *match.aliases):
        key = alias.casefold()
        if key not in seen:
            seen.add(key)
            variants.append(alias)
    return tuple(variants)


__all__ = (
    "ENTITY_ALIAS_CATALOG",
    "ENTITY_ALIAS_CATALOG_VERSION",
    "ENTITY_ALIAS_CATALOG_REVIEW_STATUS",
    "ENTITY_ALIAS_SCHEMA_VERSION",
    "EntityAliasMatch",
    "entity_alias_variants",
    "resolve_entity_alias",
)
