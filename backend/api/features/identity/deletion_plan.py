"""Read-only, metadata-only account deletion impact planning.

The planner consumes the already bounded personal-data export contract.  It
never mutates a database or filesystem and never returns exported record
bodies.  A plan is intentionally non-executable: external retention,
checkpoint, dependency, and human-authority decisions remain blockers.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any, Mapping

from .privacy import (
    MAX_PERSONAL_EXPORT_TOTAL_BYTES,
    PRIVACY_EXPORT_SCHEMA_VERSION,
    PrivacyRightsUnavailable,
)

ACCOUNT_DELETION_PLAN_SCHEMA_VERSION = "account-deletion-impact-plan-v1"
MAX_DELETION_PLAN_ITEMS = 128
MAX_DELETION_PLAN_UNAVAILABLE_SCOPES = 96
MAX_DELETION_PLAN_RESPONSE_BYTES = 128 * 1024

_SCOPE = re.compile(r"^[a-z][a-z0-9_]{1,95}(?:\.[a-z][a-z0-9_]{1,95})*$")
_REASON = re.compile(r"^[A-Z][A-Z0-9_]{2,127}$")
_DISPOSITIONS = ("delete", "anonymize", "retain", "review_required", "unavailable")
_FORBIDDEN_SCOPE_COMPONENTS = frozenset(
    {
        "access_token",
        "api_key",
        "authorization",
        "cookie",
        "credentials",
        "password",
        "refresh_token",
        "secret",
        "token",
    }
)
_RELATIONAL_POLICIES = {
    "search_history": (
        "identity.search_history",
        "delete",
        "canonical_user_id_foreign_key",
        "SUBJECT_OWNED_RELATIONAL_ROWS",
    ),
    "favorites": (
        "identity.favorites",
        "delete",
        "canonical_user_id_foreign_key",
        "SUBJECT_OWNED_RELATIONAL_ROWS",
    ),
    "assistant_sessions": (
        "assistant.chat_sessions",
        "delete",
        "canonical_user_id_foreign_key",
        "SUBJECT_OWNED_RELATIONAL_ROWS",
    ),
    "assistant_messages": (
        "assistant.chat_messages",
        "delete",
        "canonical_user_id_foreign_key",
        "SUBJECT_OWNED_RELATIONAL_ROWS",
    ),
    "assistant_memory": (
        "assistant.memory",
        "delete",
        "canonical_user_id_foreign_key",
        "SUBJECT_OWNED_RELATIONAL_ROWS",
    ),
}
_ADAPTER_SCOPES = {
    "assistant_workspace_files",
    "assistant_schedules_and_generated_reports",
    "research_workflow_projects",
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    current = value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc).isoformat()


def _subject_id(value: Any) -> int:
    if type(value) is not int or value <= 0:
        raise PrivacyRightsUnavailable("canonical deletion-plan subject is invalid")
    return value


def _username(value: Any) -> str:
    if type(value) is not str:
        raise PrivacyRightsUnavailable("canonical deletion-plan subject is invalid")
    username = value.strip()
    if (
        not username
        or username != value
        or len(username) > 128
        or any(ord(character) < 32 or ord(character) == 127 for character in username)
    ):
        raise PrivacyRightsUnavailable("canonical deletion-plan subject is invalid")
    return username


def _canonical_size(value: Any) -> int:
    try:
        return len(
            json.dumps(
                value,
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        )
    except (TypeError, ValueError) as exc:
        raise PrivacyRightsUnavailable("deletion-plan source is not canonical") from exc


def _safe_scope(value: Any) -> str:
    if not isinstance(value, str):
        raise PrivacyRightsUnavailable("deletion-plan source scope is invalid")
    scope = value
    if _SCOPE.fullmatch(scope) is None or any(
        component in _FORBIDDEN_SCOPE_COMPONENTS for component in scope.split(".")
    ):
        raise PrivacyRightsUnavailable("deletion-plan source scope is invalid")
    return scope


def _safe_reason(value: Any, fallback: str) -> str:
    reason = str(value or "")
    return reason if _REASON.fullmatch(reason) is not None else fallback


def _bounded_list(value: Any, *, maximum: int, label: str) -> list[Any]:
    if not isinstance(value, list) or len(value) > maximum:
        raise PrivacyRightsUnavailable(f"deletion-plan {label} is unavailable")
    return value


def _count_status(scope: str, truncated: set[str]) -> str:
    return "lower_bound" if scope in truncated else "exact"


def _item(
    *,
    scope: str,
    disposition: str,
    record_count: int | None,
    count_status: str,
    ownership_basis: str,
    reason_code: str,
) -> dict[str, Any]:
    safe_scope = _safe_scope(scope)
    if disposition not in _DISPOSITIONS:
        raise PrivacyRightsUnavailable("deletion-plan disposition is invalid")
    if record_count is not None and (
        isinstance(record_count, bool) or record_count < 0 or record_count > 10_000_000
    ):
        raise PrivacyRightsUnavailable("deletion-plan count is invalid")
    if count_status not in {"exact", "lower_bound", "unavailable"}:
        raise PrivacyRightsUnavailable("deletion-plan count status is invalid")
    if count_status == "unavailable" and record_count is not None:
        raise PrivacyRightsUnavailable("unavailable deletion-plan count must be empty")
    return {
        "scope": safe_scope,
        "disposition": disposition,
        "record_count": record_count,
        "count_status": count_status,
        "ownership_basis": ownership_basis,
        "reason_code": _safe_reason(reason_code, "DELETION_POLICY_REVIEW_REQUIRED"),
    }


def _append_unique(items: list[dict[str, Any]], candidate: dict[str, Any]) -> None:
    existing = next((item for item in items if item["scope"] == candidate["scope"]), None)
    if existing is None:
        if len(items) >= MAX_DELETION_PLAN_ITEMS:
            raise PrivacyRightsUnavailable("deletion-plan item bound was exceeded")
        items.append(candidate)
        return
    # Never replace uncertainty with a more optimistic disposition.
    if existing["disposition"] == "unavailable":
        return
    if candidate["disposition"] == "unavailable":
        existing.clear()
        existing.update(candidate)
        return
    if existing != candidate:
        raise PrivacyRightsUnavailable("deletion-plan scope policy is ambiguous")


def _planned_collection(
    items: list[dict[str, Any]],
    *,
    source_scope: str,
    plan_scope: str,
    rows: list[Any],
    disposition: str,
    ownership_basis: str,
    reason_code: str,
    truncated: set[str],
) -> None:
    status = _count_status(source_scope, truncated)
    if status == "lower_bound":
        _append_unique(
            items,
            _item(
                scope=plan_scope,
                disposition="unavailable",
                record_count=None,
                count_status="unavailable",
                ownership_basis="bounded_source_incomplete",
                reason_code="SOURCE_BOUND_REACHED",
            ),
        )
        return
    _append_unique(
        items,
        _item(
            scope=plan_scope,
            disposition=disposition,
            record_count=len(rows),
            count_status="exact",
            ownership_basis=ownership_basis,
            reason_code=reason_code,
        ),
    )


def _assistant_items(
    data: Mapping[str, Any],
    *,
    truncated: set[str],
    items: list[dict[str, Any]],
) -> None:
    workspace_scope = "assistant_workspace_files"
    workspace_data = data.get(workspace_scope)
    if workspace_data is not None:
        if not isinstance(workspace_data, Mapping):
            raise PrivacyRightsUnavailable("assistant deletion-plan inventory is invalid")
        workspaces = _bounded_list(
            workspace_data.get("workspaces"), maximum=50, label="workspace inventory"
        )
        files = _bounded_list(
            workspace_data.get("file_metadata"),
            maximum=5000,
            label="workspace file inventory",
        )
        for row in (*workspaces, *files):
            if not isinstance(row, Mapping):
                raise PrivacyRightsUnavailable("assistant deletion-plan inventory is invalid")
        _planned_collection(
            items,
            source_scope=workspace_scope,
            plan_scope="assistant.workspaces",
            rows=workspaces,
            disposition="delete",
            ownership_basis="canonical_subject_directory",
            reason_code="SUBJECT_CONFINED_WORKSPACE_METADATA",
            truncated=truncated,
        )
        _planned_collection(
            items,
            source_scope=workspace_scope,
            plan_scope="assistant.workspace_entries",
            rows=files,
            disposition="delete",
            ownership_basis="canonical_subject_directory",
            reason_code="SUBJECT_CONFINED_WORKSPACE_METADATA",
            truncated=truncated,
        )

    automation_scope = "assistant_schedules_and_generated_reports"
    automation_data = data.get(automation_scope)
    if automation_data is not None:
        if not isinstance(automation_data, Mapping):
            raise PrivacyRightsUnavailable("assistant automation deletion inventory is invalid")
        schedules = _bounded_list(
            automation_data.get("schedules"), maximum=500, label="schedule inventory"
        )
        reports = _bounded_list(
            automation_data.get("generated_report_metadata"),
            maximum=5000,
            label="report inventory",
        )
        for row in (*schedules, *reports):
            if not isinstance(row, Mapping):
                raise PrivacyRightsUnavailable("assistant automation deletion inventory is invalid")
        _planned_collection(
            items,
            source_scope=automation_scope,
            plan_scope="assistant.schedules",
            rows=schedules,
            disposition="delete",
            ownership_basis="canonical_schedule_owner_and_user_id",
            reason_code="SUBJECT_OWNED_AUTOMATION_METADATA",
            truncated=truncated,
        )
        _planned_collection(
            items,
            source_scope=automation_scope,
            plan_scope="assistant.generated_report_references",
            rows=reports,
            disposition="delete",
            ownership_basis="validated_subject_schedule_reference",
            reason_code="SUBJECT_OWNED_REPORT_REFERENCE",
            truncated=truncated,
        )


def _research_items(
    data: Mapping[str, Any],
    *,
    truncated: set[str],
    items: list[dict[str, Any]],
) -> None:
    source_scope = "research_workflow_projects"
    research_data = data.get(source_scope)
    if research_data is None:
        return
    if not isinstance(research_data, Mapping):
        raise PrivacyRightsUnavailable("research deletion-plan inventory is invalid")
    projects = _bounded_list(
        research_data.get("projects"), maximum=50, label="research project inventory"
    )
    authored_count = 0
    for project in projects:
        if not isinstance(project, Mapping):
            raise PrivacyRightsUnavailable("research deletion-plan inventory is invalid")
        if not isinstance(project.get("project"), Mapping) or not isinstance(
            project.get("subject_membership"), Mapping
        ):
            raise PrivacyRightsUnavailable("research deletion-plan ownership is invalid")
        authored = project.get("subject_authored")
        if not isinstance(authored, Mapping) or len(authored) > 16:
            raise PrivacyRightsUnavailable("research deletion-plan authorship is invalid")
        for collection in authored.values():
            rows = _bounded_list(
                collection,
                maximum=200,
                label="research authored collection",
            )
            if not all(isinstance(row, Mapping) for row in rows):
                raise PrivacyRightsUnavailable("research deletion-plan authorship is invalid")
            authored_count += len(rows)
            if authored_count > 10_000:
                raise PrivacyRightsUnavailable(
                    "research deletion-plan authorship bound was exceeded"
                )

    source_truncated = source_scope in truncated
    research_specs = (
        (
            "research.project_memberships",
            "delete",
            len(projects),
            "current_project_acl_subject_membership",
            "SUBJECT_MEMBERSHIP_CAN_BE_REMOVED_AFTER_REVIEW",
        ),
        (
            "research.subject_authored_records",
            "anonymize",
            authored_count,
            "canonical_subject_author_marker",
            "SHARED_RESEARCH_AUTHORSHIP_REQUIRES_ANONYMIZATION",
        ),
        (
            "research.shared_project_containers",
            "retain",
            len(projects),
            "current_acl_context_not_subject_ownership",
            "SHARED_PROJECT_CONTAINER_NOT_SUBJECT_DELETABLE",
        ),
    )
    for scope, disposition, count, ownership_basis, reason in research_specs:
        if source_truncated:
            candidate = _item(
                scope=scope,
                disposition="unavailable",
                record_count=None,
                count_status="unavailable",
                ownership_basis="bounded_source_incomplete",
                reason_code="SOURCE_BOUND_REACHED",
            )
        else:
            candidate = _item(
                scope=scope,
                disposition=disposition,
                record_count=count,
                count_status="exact",
                ownership_basis=ownership_basis,
                reason_code=reason,
            )
        _append_unique(items, candidate)


def _unavailable_items(
    unavailable_scopes: list[Any], items: list[dict[str, Any]]
) -> None:
    for raw in unavailable_scopes:
        if not isinstance(raw, Mapping):
            raise PrivacyRightsUnavailable("deletion-plan unavailable scope is invalid")
        scope = _safe_scope(raw.get("scope"))
        # Source reason values are deliberately not reflected.  A compromised
        # adapter must not be able to smuggle sensitive material through what
        # appears to be an operational reason code.
        _append_unique(
            items,
            _item(
                scope=scope,
                disposition="unavailable",
                record_count=None,
                count_status="unavailable",
                ownership_basis="ownership_or_inventory_not_proven",
                reason_code="SOURCE_SCOPE_UNAVAILABLE",
            ),
        )


def _summary(items: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    result = {
        disposition: {
            "scope_count": 0,
            "exact_record_count": 0,
            "unavailable_scope_count": 0,
        }
        for disposition in _DISPOSITIONS
    }
    for item in items:
        bucket = result[item["disposition"]]
        bucket["scope_count"] += 1
        if item["count_status"] == "exact":
            bucket["exact_record_count"] += int(item["record_count"] or 0)
        else:
            bucket["unavailable_scope_count"] += 1
    return result


def build_account_deletion_impact_plan(
    *,
    subject_id: int,
    subject_username: str,
    account: Mapping[str, Any],
    personal_export: Mapping[str, Any],
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    """Return a bounded deletion-impact plan without executing or recording it."""

    user_id = _subject_id(subject_id)
    username = _username(subject_username)
    if not isinstance(account, Mapping) or not isinstance(personal_export, Mapping):
        raise PrivacyRightsUnavailable("canonical deletion-plan source is unavailable")
    try:
        account_id = _subject_id(account.get("id"))
        account_username = _username(account.get("username"))
    except PrivacyRightsUnavailable as exc:
        raise PrivacyRightsUnavailable("canonical deletion-plan subject is unavailable") from exc
    subject_ref = f"user:{user_id}"
    if account_id != user_id or account_username != username:
        raise PrivacyRightsUnavailable("canonical deletion-plan subject does not match account")
    if (
        personal_export.get("schema_version") != PRIVACY_EXPORT_SCHEMA_VERSION
        or personal_export.get("subject_ref") != subject_ref
        or _canonical_size(personal_export) > MAX_PERSONAL_EXPORT_TOTAL_BYTES
    ):
        raise PrivacyRightsUnavailable("personal export cannot support a deletion plan")

    data = personal_export.get("data")
    if not isinstance(data, Mapping) or len(data) > 32:
        raise PrivacyRightsUnavailable("deletion-plan source data is invalid")
    data_scopes = {_safe_scope(key) for key in data}
    source_account = data.get("account")
    if not isinstance(source_account, Mapping):
        raise PrivacyRightsUnavailable("deletion-plan account source is invalid")
    try:
        source_id = _subject_id(source_account.get("id"))
        source_username = _username(source_account.get("username"))
    except PrivacyRightsUnavailable as exc:
        raise PrivacyRightsUnavailable("deletion-plan account source is invalid") from exc
    if source_id != user_id or source_username != username:
        raise PrivacyRightsUnavailable("deletion-plan source subject does not match account")

    truncated_rows = _bounded_list(
        personal_export.get("truncated_sections"),
        maximum=MAX_DELETION_PLAN_UNAVAILABLE_SCOPES,
        label="truncation inventory",
    )
    truncated = {_safe_scope(scope) for scope in truncated_rows}
    unavailable = _bounded_list(
        personal_export.get("unavailable_scopes"),
        maximum=MAX_DELETION_PLAN_UNAVAILABLE_SCOPES,
        label="unavailable inventory",
    )

    items: list[dict[str, Any]] = []
    _append_unique(
        items,
        _item(
            scope="identity.account",
            disposition="review_required",
            record_count=1,
            count_status="exact",
            ownership_basis="canonical_database_account_row",
            reason_code="RETENTION_AND_RELATIONAL_POLICY_REQUIRED",
        ),
    )
    for source_scope, (plan_scope, disposition, basis, reason) in _RELATIONAL_POLICIES.items():
        rows = _bounded_list(
            data.get(source_scope),
            maximum=5000,
            label=f"{source_scope} inventory",
        )
        if not all(isinstance(row, Mapping) for row in rows):
            raise PrivacyRightsUnavailable("relational deletion-plan inventory is invalid")
        _planned_collection(
            items,
            source_scope=source_scope,
            plan_scope=plan_scope,
            rows=rows,
            disposition=disposition,
            ownership_basis=basis,
            reason_code=reason,
            truncated=truncated,
        )

    _assistant_items(data, truncated=truncated, items=items)
    _research_items(data, truncated=truncated, items=items)
    _unavailable_items(unavailable, items)

    known_sources = {"account", *_RELATIONAL_POLICIES, *_ADAPTER_SCOPES}
    for unknown in sorted(data_scopes - known_sources):
        _append_unique(
            items,
            _item(
                scope=_safe_scope(unknown),
                disposition="unavailable",
                record_count=None,
                count_status="unavailable",
                ownership_basis="no_approved_deletion_policy_mapping",
                reason_code="DELETION_POLICY_MAPPING_UNAVAILABLE",
            ),
        )

    for scope, reason in (
        (
            "identity.assurance_and_session_ledger",
            "SECURITY_RETENTION_AND_SUBJECT_READER_REQUIRED",
        ),
        (
            "identity.privacy_request_ledger",
            "PRIVACY_REQUEST_RETENTION_POLICY_REQUIRED",
        ),
        ("operations.backups_and_logs", "BACKUP_AND_LOG_PROVENANCE_REQUIRED"),
    ):
        _append_unique(
            items,
            _item(
                scope=scope,
                disposition="unavailable",
                record_count=None,
                count_status="unavailable",
                ownership_basis="external_retention_inventory_required",
                reason_code=reason,
            ),
        )

    items.sort(key=lambda item: (item["disposition"], item["scope"]))
    blockers = [
        {
            "code": "RETENTION_AND_LEGAL_BASIS_REVIEW_REQUIRED",
            "category": "retention_legal_basis",
            "status": "open",
            "required_authority": "privacy_or_legal_owner",
        },
        {
            "code": "DURABLE_CHECKPOINT_AND_RECOVERY_PLAN_REQUIRED",
            "category": "checkpoint_and_recovery",
            "status": "open",
            "required_authority": "operations_owner",
        },
        {
            "code": "SHARED_RESOURCE_AND_RELATIONAL_IMPACT_REVIEW_REQUIRED",
            "category": "dependency_review",
            "status": "open",
            "required_authority": "data_owner",
        },
        {
            "code": "MANUAL_DELETION_AUTHORITY_REQUIRED",
            "category": "manual_authority",
            "status": "open",
            "required_authority": "authorized_human_operator",
        },
        {
            "code": "UNAVAILABLE_SCOPES_MUST_BE_RESOLVED",
            "category": "scope_completeness",
            "status": "open",
            "required_authority": "system_owner",
        },
    ]
    payload = {
        "schema_version": ACCOUNT_DELETION_PLAN_SCHEMA_VERSION,
        "generated_at": _iso(generated_at or _utcnow()),
        "subject_ref": subject_ref,
        "canonical_identity_verified": True,
        "operation_mode": "read_only_preflight",
        "deletion_performed": False,
        "execution_state": "blocked",
        "request_registration_state": "not_checked",
        "scope_complete": not any(
            item["disposition"] == "unavailable" for item in items
        ),
        "impact_items": items,
        "disposition_summary": _summary(items),
        "external_blockers": blockers,
        "execution_contract": {
            "may_execute": False,
            "manual_authority_required": True,
            "request_registration_is_not_execution": True,
            "retention_and_legal_basis_status": "unverified",
            "checkpoint_status": "not_proven",
        },
        "limits": {
            "impact_items": MAX_DELETION_PLAN_ITEMS,
            "unavailable_scopes": MAX_DELETION_PLAN_UNAVAILABLE_SCOPES,
            "source_bytes": MAX_PERSONAL_EXPORT_TOTAL_BYTES,
            "response_bytes": MAX_DELETION_PLAN_RESPONSE_BYTES,
        },
        "excluded_fields": [
            "record_bodies",
            "absolute_paths",
            "other_subject_identifiers",
            "credentials_and_tokens",
        ],
    }
    if _canonical_size(payload) > MAX_DELETION_PLAN_RESPONSE_BYTES:
        raise PrivacyRightsUnavailable("deletion-plan response exceeded its byte bound")
    return payload


__all__ = (
    "ACCOUNT_DELETION_PLAN_SCHEMA_VERSION",
    "MAX_DELETION_PLAN_ITEMS",
    "MAX_DELETION_PLAN_RESPONSE_BYTES",
    "MAX_DELETION_PLAN_UNAVAILABLE_SCOPES",
    "build_account_deletion_impact_plan",
)
