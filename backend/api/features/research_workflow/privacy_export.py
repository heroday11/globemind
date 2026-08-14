"""Subject-safe, bounded privacy export for accessible research projects."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Mapping
from urllib.parse import quote

from .repository import (
    ResearchProjectNotFound,
    ResearchProjectRepository,
    ResearchRepositoryCapacityExceeded,
    ResearchRepositoryUnavailable,
)

RESEARCH_SUBJECT_EXPORT_SCHEMA_VERSION = "research-subject-export-v1"
MAX_EXPORT_PROJECTS = 50
MAX_EXPORT_ITEMS_PER_COLLECTION = 200
MAX_EXPORT_ITEM_BYTES = 64 * 1024
MAX_EXPORT_TOTAL_BYTES = 2 * 1024 * 1024
_DATA_BUDGET_BYTES = MAX_EXPORT_TOTAL_BYTES - (64 * 1024)
_SAFE_USERNAME = re.compile(r"^[A-Za-z0-9_.@-]{1,96}$")
_SENSITIVE_KEYS = frozenset(
    {
        "access_token",
        "api_key",
        "apikey",
        "api_keys",
        "authorization",
        "cookie",
        "credentials",
        "password",
        "password_hash",
        "refresh_token",
        "secret",
        "token",
    }
)
_SENSITIVE_KEY_SUFFIXES = (
    "_api_key",
    "_apikey",
    "_credential",
    "_credentials",
    "_password",
    "_private_key",
    "_secret",
    "_token",
)
_OWN_COLLECTIONS = (
    "research_questions",
    "saved_searches",
    "evidence_items",
    "information_gaps",
    "alternative_hypotheses",
    "judgments",
    "human_decisions",
    "reviews",
)


class ResearchSubjectExportUnavailable(RuntimeError):
    """Research privacy data cannot be proven safe and internally consistent."""


def _subject_id(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ResearchSubjectExportUnavailable("canonical subject id is invalid")
    return value


def _username(value: Any) -> str:
    username = str(value or "").strip()
    if username in {".", ".."} or _SAFE_USERNAME.fullmatch(username) is None:
        raise ResearchSubjectExportUnavailable("canonical subject username is invalid")
    return username


def _canonical(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ResearchSubjectExportUnavailable("research export value is invalid") from exc


def _is_sensitive_key(value: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]+", "_", value.strip().lower()).strip("_")
    compact = normalized.replace("_", "")
    return (
        normalized in _SENSITIVE_KEYS
        or normalized.endswith(_SENSITIVE_KEY_SUFFIXES)
        or compact.endswith(
            (
                "apikey",
                "credential",
                "credentials",
                "password",
                "privatekey",
                "secret",
                "token",
            )
        )
    )


def _redact_sensitive(
    value: Any,
    *,
    path: str = "",
    redacted: list[str],
) -> Any:
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for raw_key, nested in value.items():
            key = str(raw_key)
            item_path = f"{path}.{key}" if path else key
            if _is_sensitive_key(key):
                redacted.append(item_path)
                continue
            result[key] = _redact_sensitive(nested, path=item_path, redacted=redacted)
        return result
    if isinstance(value, list):
        return [
            _redact_sensitive(item, path=f"{path}[{index}]", redacted=redacted)
            for index, item in enumerate(value)
        ]
    return value


def _bounded_item(
    item: Mapping[str, Any],
    *,
    collection: str,
    truncation_reasons: list[str],
) -> dict[str, Any]:
    public = dict(item)
    public.pop("created_by", None)
    redacted: list[str] = []
    public = _redact_sensitive(public, redacted=redacted)
    if redacted:
        public["redacted_fields"] = sorted(redacted)
        truncation_reasons.append(f"{collection}:sensitive_fields_redacted")
    encoded = _canonical(public)
    if len(encoded) <= MAX_EXPORT_ITEM_BYTES:
        return public
    truncation_reasons.append(f"{collection}:item_byte_limit")
    return {
        "id": str(item.get("id") or item.get("manifest_id") or ""),
        "created_at": item.get("created_at"),
        "export_status": "metadata_only_item_byte_limit",
        "content_sha256": hashlib.sha256(encoded).hexdigest(),
    }


def _own_items(
    project: Mapping[str, Any],
    collection: str,
    username: str,
    truncation_reasons: list[str],
) -> list[dict[str, Any]]:
    rows = [
        item
        for item in project.get(collection, [])
        if isinstance(item, Mapping) and item.get("created_by") == username
    ]
    if len(rows) > MAX_EXPORT_ITEMS_PER_COLLECTION:
        truncation_reasons.append(f"{collection}:item_count_limit")
    return [
        _bounded_item(
            item,
            collection=collection,
            truncation_reasons=truncation_reasons,
        )
        for item in rows[:MAX_EXPORT_ITEMS_PER_COLLECTION]
    ]


def _own_author_events(
    project: Mapping[str, Any],
    username: str,
    truncation_reasons: list[str],
) -> list[dict[str, Any]]:
    audit_by_version = {
        item.get("version"): item
        for item in project.get("audit_events", [])
        if isinstance(item, Mapping) and item.get("actor") == username
    }
    changes = [
        item
        for item in project.get("change_history", [])
        if isinstance(item, Mapping) and item.get("actor") == username
    ]
    if len(changes) > MAX_EXPORT_ITEMS_PER_COLLECTION:
        truncation_reasons.append("author_events:item_count_limit")
    result: list[dict[str, Any]] = []
    for change in changes[:MAX_EXPORT_ITEMS_PER_COLLECTION]:
        audit = audit_by_version.get(change.get("version"), {})
        resource_type = change.get("resource_type")
        resource_id = change.get("resource_id")
        membership_event = resource_type == "member"
        result.append(
            _bounded_item(
                {
                    "id": change.get("change_id"),
                    "version": change.get("version"),
                    "previous_version": change.get("previous_version"),
                    "timestamp": change.get("timestamp"),
                    "reason": None if membership_event else change.get("reason"),
                    "reason_status": (
                        "redacted_membership_event"
                        if membership_event
                        else "subject_authored"
                    ),
                    "action": change.get("action"),
                    "resource_type": resource_type,
                    "resource_id": None if membership_event else resource_id,
                    "resource_id_relation": (
                        "self"
                        if membership_event and resource_id == username
                        else "another_project_member"
                        if membership_event
                        else "subject_authored_resource"
                    ),
                    "reason_sha256": audit.get("reason_sha256"),
                    "changed_fields": audit.get("changed_fields", []),
                },
                collection="author_events",
                truncation_reasons=truncation_reasons,
            )
        )
    return result


def _own_manifest_metadata(
    project: Mapping[str, Any],
    username: str,
    truncation_reasons: list[str],
) -> list[dict[str, Any]]:
    manifests = [
        item
        for item in project.get("export_manifests", [])
        if isinstance(item, Mapping) and item.get("created_by") == username
    ]
    if len(manifests) > MAX_EXPORT_ITEMS_PER_COLLECTION:
        truncation_reasons.append("export_manifests:item_count_limit")
    project_id = quote(str(project.get("id") or ""), safe="")
    result: list[dict[str, Any]] = []
    for manifest in manifests[:MAX_EXPORT_ITEMS_PER_COLLECTION]:
        export_version = int(manifest.get("export_version") or 0)
        result.append(
            _bounded_item(
                {
                    "manifest_id": manifest.get("manifest_id"),
                    "schema_version": manifest.get("schema_version"),
                    "export_version": export_version,
                    "project_version": manifest.get("project_version"),
                    "report_title": manifest.get("report_title"),
                    "created_at": manifest.get("created_at"),
                    "integrity_sha256": manifest.get("integrity_sha256"),
                    "artifact_download_paths": {
                        "json": (
                            f"/api/research/projects/{project_id}/exports/"
                            f"{export_version}/artifact?format=json"
                        ),
                        "markdown": (
                            f"/api/research/projects/{project_id}/exports/"
                            f"{export_version}/artifact?format=markdown"
                        ),
                    },
                    "body_status": "not_inlined_use_authenticated_artifact",
                },
                collection="export_manifests",
                truncation_reasons=truncation_reasons,
            )
        )
    return result


def _project_export(
    project: Mapping[str, Any],
    *,
    username: str,
    membership: Mapping[str, Any],
    truncation_reasons: list[str],
) -> dict[str, Any]:
    authored = {
        collection: _own_items(project, collection, username, truncation_reasons)
        for collection in _OWN_COLLECTIONS
    }
    authored["export_manifests"] = _own_manifest_metadata(
        project,
        username,
        truncation_reasons,
    )
    authored["author_events"] = _own_author_events(
        project,
        username,
        truncation_reasons,
    )
    return {
        "project": {
            "id": project.get("id"),
            "title": project.get("title"),
            "description": project.get("description"),
            "scope_countries": list(project.get("scope_countries", [])),
            "version": project.get("version"),
            "created_at": project.get("created_at"),
            "updated_at": project.get("updated_at"),
        },
        "subject_membership": {
            "role": membership.get("role"),
            "added_at": membership.get("added_at"),
            "added_by_relation": (
                "self" if membership.get("added_by") == username else "another_project_member"
            ),
        },
        "subject_authored": authored,
    }


def build_research_subject_export(
    repository: ResearchProjectRepository,
    *,
    subject_id: int,
    username: str,
) -> dict[str, Any]:
    """Return only active-ACL metadata and content authored by this subject."""

    user_id = _subject_id(subject_id)
    actor = _username(username)
    try:
        all_projects = repository.list_projects()
    except (
        ResearchProjectNotFound,
        ResearchRepositoryUnavailable,
        ResearchRepositoryCapacityExceeded,
    ) as exc:
        raise ResearchSubjectExportUnavailable("research repository is unavailable") from exc

    accessible: list[tuple[dict[str, Any], Mapping[str, Any]]] = []
    for project in all_projects:
        membership = next(
            (
                item
                for item in project.get("members", [])
                if isinstance(item, Mapping)
                and item.get("username") == actor
                and item.get("role") in {"owner", "reviewer", "reader"}
            ),
            None,
        )
        if membership is not None:
            accessible.append((project, membership))
    accessible.sort(key=lambda item: str(item[0].get("id") or ""))

    truncation_reasons: list[str] = []
    if len(accessible) > MAX_EXPORT_PROJECTS:
        truncation_reasons.append("projects:item_count_limit")
    projects: list[dict[str, Any]] = []
    consumed = 0
    for project, membership in accessible[:MAX_EXPORT_PROJECTS]:
        item = _project_export(
            project,
            username=actor,
            membership=membership,
            truncation_reasons=truncation_reasons,
        )
        encoded = _canonical(item)
        if consumed + len(encoded) > _DATA_BUDGET_BYTES:
            truncation_reasons.append("projects:total_byte_limit")
            break
        projects.append(item)
        consumed += len(encoded)

    unavailable_subscopes = [
        {
            "scope": "other_members_private_fields",
            "reason": "PROJECT_OTHER_MEMBER_FIELDS_EXCLUDED",
        },
        {
            "scope": "other_members_authored_content",
            "reason": "PROJECT_OTHER_MEMBER_CONTENT_EXCLUDED",
        },
        {
            "scope": "full_manifest_bodies",
            "reason": "AGGREGATED_MANIFEST_BODY_NOT_INLINED",
        },
        {
            "scope": "projects_without_current_acl",
            "reason": "CURRENT_PROJECT_ACL_REQUIRED",
        },
    ]
    result = {
        "schema_version": RESEARCH_SUBJECT_EXPORT_SCHEMA_VERSION,
        "scope": "research_workflow_projects",
        "status": "partial",
        "subject_ref": f"user:{user_id}",
        "data": {"projects": projects},
        "truncated": bool(truncation_reasons),
        "truncation_reasons": sorted(set(truncation_reasons)),
        "limits": {
            "projects": MAX_EXPORT_PROJECTS,
            "items_per_collection": MAX_EXPORT_ITEMS_PER_COLLECTION,
            "bytes_per_item": MAX_EXPORT_ITEM_BYTES,
            "bytes_total": MAX_EXPORT_TOTAL_BYTES,
        },
        "unavailable_subscopes": unavailable_subscopes,
    }
    if len(_canonical(result)) > MAX_EXPORT_TOTAL_BYTES:
        raise ResearchSubjectExportUnavailable("research export exceeded its byte bound")
    return result


__all__ = (
    "MAX_EXPORT_ITEM_BYTES",
    "MAX_EXPORT_ITEMS_PER_COLLECTION",
    "MAX_EXPORT_PROJECTS",
    "MAX_EXPORT_TOTAL_BYTES",
    "RESEARCH_SUBJECT_EXPORT_SCHEMA_VERSION",
    "ResearchSubjectExportUnavailable",
    "build_research_subject_export",
)
