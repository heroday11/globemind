"""Bounded public contract for Ground News source-profile methodology.

The catalog contains third-party and structurally seeded labels.  This module
turns only recognized markers into a versioned method card and never upgrades
those labels into a finding about a source's reliability or factual accuracy.
"""
from __future__ import annotations

import copy
import re
from datetime import date, datetime
from typing import Any
from urllib.parse import urlsplit, urlunsplit

SOURCE_PROFILE_CONTRACT_VERSION = "ground-news-source-profile-v1"
SOURCE_PROFILE_METHOD_CARD_SCHEMA_VERSION = (
    "ground-news-source-profile-method-card-v1"
)
MAX_METHOD_NOTE_CHARS = 8192
MAX_METHOD_SEGMENTS = 32
MAX_PUBLIC_TEXT_CHARS = 512

_KNOWN_PROFILE_VERSIONS = {"media_profile_seed_v1"}
_SOURCE_TYPES = {
    "business_media",
    "executive_government",
    "foreign_ministry",
    "foreign_service",
    "global_major_media",
    "international_organization",
    "international_security_org",
    "national_major_media",
    "public_broadcaster",
    "regional_major_media",
    "state_media",
    "supranational_executive",
    "wire_service",
}
_OWNERSHIP_TYPES = {
    "government",
    "intergovernmental",
    "nonprofit",
    "party_affiliated",
    "private",
    "public",
    "state",
    "unknown",
    "wire_service",
}
_GEO_ALIGNMENTS = {
    "china",
    "global_south",
    "middle_east",
    "mixed",
    "neutral",
    "russia",
    "unknown",
    "western",
}
_POLITICAL_LEANINGS = {
    "center",
    "center_left",
    "center_right",
    "left",
    "right",
    "state_aligned",
    "unknown",
}
_CREDIBILITY_TIERS = {"high", "medium", "low", "unknown"}
_CONFIDENCE_LEVELS = {"high", "medium", "low"}
_REVIEW_STATUSES = {"seeded", "needs_review", "reviewed", "locked"}
_READY_REVIEW_STATUSES = {"reviewed", "locked"}
_METHOD_MARKER_PATTERN = re.compile(r"[a-z][a-z0-9_]{0,63}\Z")

_METHODS: dict[str, dict[str, Any]] = {
    "seeded_from_historical_wave1_targets": {
        "method_id": "historical_wave1_seed_v1",
        "method_version": "v1",
        "kind": "catalog_seed",
        "version_state": "profile_version_bound",
        "supports_fields": ["source_identity", "source_structure"],
        "assurance_scope": "catalog_structure_only",
    },
    "ownership_inferred_structurally": {
        "method_id": "structural_ownership_inference_v1",
        "method_version": "v1",
        "kind": "structural_rule",
        "version_state": "controlled_mapping",
        "supports_fields": ["ownership_type"],
        "assurance_scope": "structural_catalog_label_only",
    },
    "geo_alignment_rule_v1": {
        "method_id": "geo_alignment_rule_v1",
        "method_version": "v1",
        "kind": "structural_rule",
        "version_state": "explicit",
        "supports_fields": ["geo_alignment"],
        "assurance_scope": "composition_grouping_only",
    },
    "structural_review_v1": {
        "method_id": "structural_review_v1",
        "method_version": "v1",
        "kind": "structural_review",
        "version_state": "explicit",
        "supports_fields": ["ownership_type", "source_type"],
        "assurance_scope": "structural_catalog_label_only",
    },
    "ground_news_rating_v1": {
        "method_id": "ground_news_rating_v1",
        "method_version": "v1",
        "kind": "third_party_directory_rating",
        "version_state": "explicit",
        "supports_fields": ["political_leaning", "credibility_tier"],
        "assurance_scope": "third_party_catalog_label_only",
    },
    "mbfc_rating_v1": {
        "method_id": "mbfc_rating_v1",
        "method_version": "v1",
        "kind": "third_party_directory_rating",
        "version_state": "explicit",
        "supports_fields": ["political_leaning", "credibility_tier"],
        "assurance_scope": "third_party_catalog_label_only",
    },
    "institutional_override_v1": {
        "method_id": "institutional_override_v1",
        "method_version": "v1",
        "kind": "institutional_evidence",
        "version_state": "explicit",
        "supports_fields": ["ownership_type", "political_leaning"],
        "assurance_scope": "institutional_alignment_only",
    },
    "review_import": {
        "method_id": "review_import_legacy",
        "method_version": None,
        "kind": "manual_review_import",
        "version_state": "legacy_unversioned",
        "supports_fields": ["ownership_type", "review_status"],
        "assurance_scope": "catalog_review_metadata_only",
    },
}

_METHOD_METADATA_PREFIXES = ("secondary_evidence=", "reviewer=", "reviewed_at=")
_RATING_METHOD_IDS = {"ground_news_rating_v1", "mbfc_rating_v1"}
_INSTITUTIONAL_METHOD_ID = "institutional_override_v1"


def _note_disposition(segment: str) -> str | None:
    if segment == "not a political-bias label":
        return "GEO_ALIGNMENT_NOT_POLITICAL_BIAS"
    if segment == "state_aligned is institutional, not a left/right rating":
        return "INSTITUTIONAL_NOT_LEFT_RIGHT"
    if segment.startswith("medium confidence due same-name-source ambiguity"):
        return "SOURCE_IDENTITY_AMBIGUITY"
    if segment.startswith("medium confidence due government-agency context"):
        return "INSTITUTIONAL_CONTEXT_ONLY"
    if segment.startswith("credibility remains medium pending external factuality rating"):
        return "FACTUALITY_RATING_PENDING"
    if segment.startswith(
        (
            "political leaning requires external rating",
            "bias and factuality still",
            "exact third-party bias rating still needed",
        )
    ):
        return "EXTERNAL_RATING_REQUIRED"
    if segment.startswith("MBFC lists"):
        return "MULTI_DIRECTORY_CONSERVATIVE_MAPPING"
    if segment.startswith(
        (
            "ownership_round2:",
            "mapped to ",
            "Mediacorp is state-owned",
            "public broadcaster ownership already set",
        )
    ):
        return "OWNERSHIP_REVIEW_DETAIL"
    return None


def _text(value: Any, *, maximum: int = MAX_PUBLIC_TEXT_CHARS) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    if not normalized or len(normalized) > maximum:
        return None
    if any(ord(character) < 32 or ord(character) == 127 for character in normalized):
        return None
    return normalized


def _safe_evidence_url(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    raw = value.strip()
    if (
        not raw
        or len(raw) > 4000
        or "\\" in raw
        or any(ord(character) <= 32 or ord(character) == 127 for character in raw)
    ):
        return None
    try:
        parsed = urlsplit(raw)
        port = parsed.port
        hostname = parsed.hostname
    except (UnicodeError, ValueError):
        return None
    if (
        parsed.scheme.lower() not in {"http", "https"}
        or not hostname
        or parsed.username is not None
        or parsed.password is not None
    ):
        return None
    try:
        ascii_host = hostname.encode("idna").decode("ascii").lower()
    except UnicodeError:
        return None
    if ":" in ascii_host and not ascii_host.startswith("["):
        ascii_host = f"[{ascii_host}]"
    authority = f"{ascii_host}:{port}" if port is not None else ascii_host
    return urlunsplit((parsed.scheme.lower(), authority, parsed.path or "/", "", ""))


def _enum(value: Any, allowed: set[str]) -> str:
    normalized = _text(value, maximum=64)
    return normalized if normalized in allowed else "unknown"


def _method_marker(segment: str) -> str | None:
    if segment == "seeded_from_historical_wave1_targets":
        return segment
    if segment.startswith("ownership inferred structurally from source_type="):
        return "ownership_inferred_structurally"
    marker = segment.split(":", 1)[0].strip()
    return marker if _METHOD_MARKER_PATTERN.fullmatch(marker) else None


def _parse_methods(value: Any) -> tuple[list[dict[str, Any]], int, bool, list[str]]:
    if value in (None, ""):
        return [], 0, False, []
    if not isinstance(value, str) or len(value) > MAX_METHOD_NOTE_CHARS:
        return [], 1, True, []
    segments = [segment.strip() for segment in value.split(";") if segment.strip()]
    if len(segments) > MAX_METHOD_SEGMENTS:
        return [], 1, True, []

    methods: list[dict[str, Any]] = []
    seen: set[str] = set()
    unknown_count = 0
    note_disposition_codes: set[str] = set()
    for segment in segments:
        if segment.startswith(_METHOD_METADATA_PREFIXES):
            continue
        marker = _method_marker(segment)
        method = _METHODS.get(marker or "")
        if method is None:
            note_code = _note_disposition(segment)
            if note_code is None:
                unknown_count += 1
            else:
                note_disposition_codes.add(note_code)
            continue
        method_id = str(method["method_id"])
        if method_id in seen:
            continue
        seen.add(method_id)
        methods.append(copy.deepcopy(method))
    return (
        methods,
        min(unknown_count, MAX_METHOD_SEGMENTS),
        False,
        sorted(note_disposition_codes),
    )


def _disposition(
    state: str,
    reason_code: str,
    method_ids: list[str] | tuple[str, ...] = (),
) -> dict[str, Any]:
    return {
        "state": state,
        "reason_code": reason_code,
        "method_ids": sorted(set(method_ids)),
    }


def _structural_field(
    raw_value: Any,
    *,
    allowed: set[str],
    profile_known: bool,
    method_input_truncated: bool,
    supported_method_ids: list[str],
) -> tuple[str, dict[str, Any]]:
    if not profile_known:
        return "unknown", _disposition("unknown", "PROFILE_VERSION_UNKNOWN")
    if method_input_truncated:
        return "unknown", _disposition("unknown", "METHOD_INPUT_OUT_OF_BOUNDS")
    value = _enum(raw_value, allowed)
    if value == "unknown":
        return "unknown", _disposition("unknown", "CATALOG_VALUE_UNKNOWN")
    if not supported_method_ids:
        return "unknown", _disposition("unknown", "CONTROLLED_METHOD_MISSING")
    return value, _disposition(
        "catalog_value",
        "CONTROLLED_PROFILE_VERSION",
        supported_method_ids,
    )


def _quality_field(
    raw_value: Any,
    *,
    allowed: set[str],
    profile_known: bool,
    review_status: str,
    method_input_truncated: bool,
    method_ids: list[str],
    institutional: bool = False,
) -> tuple[str, dict[str, Any]]:
    if not profile_known:
        return "unknown", _disposition("unknown", "PROFILE_VERSION_UNKNOWN")
    if method_input_truncated:
        return "unknown", _disposition("unknown", "METHOD_INPUT_OUT_OF_BOUNDS")
    value = _enum(raw_value, allowed)
    if value == "unknown":
        return "unknown", _disposition("unknown", "CATALOG_VALUE_UNKNOWN")
    if review_status not in _READY_REVIEW_STATUSES:
        return "unknown", _disposition("unknown", "PROFILE_NOT_REVIEWED")
    if method_ids:
        return value, _disposition(
            "third_party_catalog_label",
            "CONTROLLED_THIRD_PARTY_DIRECTORY_METHOD",
            method_ids,
        )
    if institutional and value == "state_aligned":
        return value, _disposition(
            "institutional_catalog_label",
            "CONTROLLED_INSTITUTIONAL_ALIGNMENT_METHOD",
            [_INSTITUTIONAL_METHOD_ID],
        )
    return "unknown", _disposition("unknown", "CONTROLLED_RATING_METHOD_MISSING")


def _article_count(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if 0 <= value <= 2**63 - 1 else None


def _timestamp(value: Any) -> str | None:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return _text(value, maximum=64)


def build_source_profile_contract(
    raw_profile: Any,
    *,
    fallback_domain: str = "",
) -> dict[str, Any]:
    """Return an allow-listed source profile with field-level method dispositions."""
    raw = raw_profile if isinstance(raw_profile, dict) else {}
    raw_version = _text(raw.get("profile_version"), maximum=64)
    profile_known = raw_version in _KNOWN_PROFILE_VERSIONS
    (
        methods,
        unknown_method_count,
        method_input_truncated,
        note_disposition_codes,
    ) = _parse_methods(raw.get("evidence_note"))
    method_ids = [str(method["method_id"]) for method in methods]
    seed_method_ids = [
        method_id for method_id in method_ids if method_id == "historical_wave1_seed_v1"
    ]
    rating_method_ids = [
        method_id for method_id in method_ids if method_id in _RATING_METHOD_IDS
    ]
    review_status_raw = _enum(raw.get("review_status"), _REVIEW_STATUSES)
    review_status = review_status_raw if profile_known else "unknown"

    source_type, source_type_disposition = _structural_field(
        raw.get("source_type"),
        allowed=_SOURCE_TYPES,
        profile_known=profile_known,
        method_input_truncated=method_input_truncated,
        supported_method_ids=seed_method_ids,
    )
    ownership_method_ids = [
        method_id
        for method_id in method_ids
        if method_id
        in {
            "historical_wave1_seed_v1",
            "structural_ownership_inference_v1",
            "structural_review_v1",
            "institutional_override_v1",
            "review_import_legacy",
        }
    ]
    ownership_type, ownership_disposition = _structural_field(
        raw.get("ownership_type"),
        allowed=_OWNERSHIP_TYPES,
        profile_known=profile_known,
        method_input_truncated=method_input_truncated,
        supported_method_ids=ownership_method_ids,
    )
    geo_method_ids = [
        method_id
        for method_id in method_ids
        if method_id in {"historical_wave1_seed_v1", "geo_alignment_rule_v1"}
    ]
    geo_alignment, geo_disposition = _structural_field(
        raw.get("geo_alignment"),
        allowed=_GEO_ALIGNMENTS,
        profile_known=profile_known,
        method_input_truncated=method_input_truncated,
        supported_method_ids=geo_method_ids,
    )
    political_leaning, political_disposition = _quality_field(
        raw.get("political_leaning"),
        allowed=_POLITICAL_LEANINGS,
        profile_known=profile_known,
        review_status=review_status,
        method_input_truncated=method_input_truncated,
        method_ids=rating_method_ids,
        institutional=_INSTITUTIONAL_METHOD_ID in method_ids,
    )
    credibility_tier, credibility_disposition = _quality_field(
        raw.get("credibility_tier"),
        allowed=_CREDIBILITY_TIERS,
        profile_known=profile_known,
        review_status=review_status,
        method_input_truncated=method_input_truncated,
        method_ids=rating_method_ids,
    )

    released_quality_method_ids = sorted(
        set(political_disposition["method_ids"] + credibility_disposition["method_ids"])
    )
    raw_confidence = _enum(raw.get("label_confidence"), _CONFIDENCE_LEVELS)
    if not profile_known:
        label_confidence = "unknown"
        confidence_disposition = _disposition("unknown", "PROFILE_VERSION_UNKNOWN")
    elif method_input_truncated:
        label_confidence = "unknown"
        confidence_disposition = _disposition("unknown", "METHOD_INPUT_OUT_OF_BOUNDS")
    elif raw_confidence == "unknown" or not released_quality_method_ids:
        label_confidence = "unknown"
        confidence_disposition = _disposition("unknown", "QUALITY_LABEL_NOT_RELEASED")
    else:
        label_confidence = raw_confidence
        confidence_disposition = _disposition(
            "catalog_metadata",
            "QUALITY_LABEL_RELEASED_WITH_CONTROLLED_METHOD",
            released_quality_method_ids,
        )

    if not profile_known:
        review_disposition = _disposition("unknown", "PROFILE_VERSION_UNKNOWN")
    elif review_status == "unknown":
        review_disposition = _disposition("unknown", "CATALOG_VALUE_UNKNOWN")
    elif not seed_method_ids:
        review_status = "unknown"
        review_disposition = _disposition("unknown", "CONTROLLED_METHOD_MISSING")
    else:
        review_disposition = _disposition(
            "catalog_metadata",
            "CONTROLLED_PROFILE_VERSION",
            seed_method_ids,
        )

    if not profile_known or method_input_truncated or not methods:
        overall_state = "unknown"
    elif unknown_method_count:
        overall_state = "partial_unknown"
    else:
        overall_state = "controlled_catalog"

    domain = _text(raw.get("domain"), maximum=253) or _text(
        fallback_domain, maximum=253
    ) or "unknown"
    source_name = _text(raw.get("source_name"), maximum=200) or domain
    field_dispositions = {
        "source_type": source_type_disposition,
        "ownership_type": ownership_disposition,
        "geo_alignment": geo_disposition,
        "political_leaning": political_disposition,
        "credibility_tier": credibility_disposition,
        "label_confidence": confidence_disposition,
        "review_status": review_disposition,
    }
    return {
        "profile_contract_version": SOURCE_PROFILE_CONTRACT_VERSION,
        "domain": domain,
        "source_name": source_name,
        "country": _text(raw.get("country"), maximum=128),
        "region": _text(raw.get("region"), maximum=64),
        "region_code": _text(raw.get("region_code"), maximum=16),
        "source_type": source_type,
        "ownership_type": ownership_type,
        "geo_alignment": geo_alignment,
        "political_leaning": political_leaning,
        "credibility_tier": credibility_tier,
        "label_confidence": label_confidence,
        "review_status": review_status,
        "profile_version": raw_version if profile_known else None,
        "evidence_url": _safe_evidence_url(raw.get("evidence_url")),
        "article_count_snapshot": _article_count(raw.get("article_count_snapshot")),
        "updated_at": _timestamp(raw.get("updated_at")),
        "method_card": {
            "schema_version": SOURCE_PROFILE_METHOD_CARD_SCHEMA_VERSION,
            "profile_contract_version": SOURCE_PROFILE_CONTRACT_VERSION,
            "catalog_profile_version": raw_version if profile_known else None,
            "catalog_profile_version_state": "recognized" if profile_known else "unknown",
            "overall_state": overall_state,
            "methods": methods,
            "note_disposition_codes": note_disposition_codes,
            "unknown_method_count": unknown_method_count,
            "method_input_truncated": method_input_truncated,
            "assurance": {
                "state": "catalog_labels_only",
                "independent_validation": "not_performed",
                "source_reliability_conclusion": "not_established",
                "fact_accuracy_conclusion": "not_established",
                "reason_code": "DIRECTORY_LABELS_ARE_NOT_RELIABILITY_FINDINGS",
            },
            "field_dispositions": field_dispositions,
        },
    }


__all__ = (
    "SOURCE_PROFILE_CONTRACT_VERSION",
    "SOURCE_PROFILE_METHOD_CARD_SCHEMA_VERSION",
    "build_source_profile_contract",
)
