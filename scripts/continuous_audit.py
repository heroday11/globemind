#!/usr/bin/env python3
"""Run the offline, read-only GlobeMind audit-registry checks.

The scanner never imports application code or contacts services.  It reads the
checked-in registry and repository-relative evidence/validator files, then
writes a new JSON and Markdown report to an explicitly selected directory
outside the repository.  Existing output is never replaced.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REGISTRY = REPOSITORY_ROOT / "ops" / "audit" / "registry.json"
DEFAULT_VALIDATOR_PLAN = REPOSITORY_ROOT / "config" / "continuous-audit-validators.json"
FORBIDDEN_RELEASE_ROOT = Path("/root/data/releases/globemind")
METHOD_VERSION = "continuous-audit-v2"
SCHEMA_VERSION = "globemind-audit-registry-v1"
REPORT_SCHEMA_VERSION = "globemind-continuous-audit-report-v2"
REPORT_JSON_NAME = "continuous-audit.json"
REPORT_MARKDOWN_NAME = "continuous-audit.md"
MAX_REGISTRY_BYTES = 2 * 1024 * 1024
MAX_VALIDATOR_PLAN_BYTES = 256 * 1024
MAX_CLOCK_SKEW = timedelta(minutes=5)
SYSTEM_GIT = Path("/usr/bin/git")

ALLOWED_STATUSES = (
    "PROVEN_CODE",
    "OBSERVED_SAMPLE",
    "PARTIAL",
    "EXTERNAL_BLOCKED",
    "NOT_STARTED_OR_UNVERIFIED",
)
ALLOWED_SEVERITIES = {"P0", "P1", "P2"}
VALIDATOR_KINDS = {
    "pytest",
    "node_test",
    "static_inspection",
    "manual_review",
    "external_acceptance",
    "not_configured",
}
BLOCKER_KINDS = {
    "partial_scope",
    "verification_gap",
    "implementation_gap",
    "external_dependency",
}
EXPECTED_DOMAIN_COUNTS = {
    "IA": 10,
    "SR": 12,
    "FR": 12,
    "ML": 10,
    "EV": 13,
    "EG": 14,
    "CD": 18,
    "AI": 12,
    "WF": 14,
    "QA": 15,
}
EXPECTED_IDS = tuple(
    f"{domain}-{number:02d}"
    for domain, count in EXPECTED_DOMAIN_COUNTS.items()
    for number in range(1, count + 1)
)

ROOT_KEYS = {
    "schema_version",
    "registry_id",
    "method_version",
    "updated_at",
    "scope",
    "allowed_statuses",
    "automation_state",
    "automation_state_reason",
    "owner_roles",
    "items",
}
OWNER_KEYS = {"id", "description"}
ITEM_KEYS = {
    "id",
    "domain",
    "title",
    "severity",
    "owner_role",
    "status",
    "validator",
    "evidence",
    "blocker",
    "status_basis",
}
VALIDATOR_KEYS = {"kind", "locator"}
EVIDENCE_KEYS = {"locator", "observed_at", "max_age_days", "scope"}
BLOCKER_KEYS = {"kind", "description", "needed"}
ROLE_ID_RE = re.compile(r"^[a-z][a-z0-9-]*$")
ITEM_ID_RE = re.compile(r"^(IA|SR|FR|ML|EV|EG|CD|AI|WF|QA)-([0-9]{2})$")
SHA1_RE = re.compile(r"^[0-9a-f]{40}$")
VALIDATOR_PLAN_ID_RE = re.compile(r"^[a-z][a-z0-9-]{2,79}$")
VALIDATOR_PLAN_ROOT_KEYS = {
    "schema_version",
    "plan_state",
    "execution_state",
    "scheduler_state",
    "artifact_retention_state",
    "issue_integration_state",
    "automation_owner_state",
    "runner_locator",
    "scheduler_locator",
    "artifact_retention_locator",
    "automation_owner_role",
    "safety",
    "validators",
}
VALIDATOR_PLAN_SAFETY_KEYS = {
    "network_access",
    "database_access",
    "service_control",
    "release_access",
    "production_mutation",
}
VALIDATOR_PLAN_ITEM_KEYS = {"id", "domain", "kind", "locator", "policy_locator"}


class ContinuousAuditError(RuntimeError):
    """Raised when a local audit input/output violates the safety contract."""


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ContinuousAuditError(f"duplicate JSON key: {key!r}")
        result[key] = value
    return result


def _require_object(value: object, location: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ContinuousAuditError(f"{location} must be an object")
    return value


def _require_exact_keys(value: dict[str, Any], expected: set[str], location: str) -> None:
    missing = sorted(expected - set(value))
    unknown = sorted(set(value) - expected)
    if missing or unknown:
        raise ContinuousAuditError(
            f"{location} schema mismatch: missing={missing}, unknown={unknown}"
        )


def _require_string(value: object, location: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ContinuousAuditError(f"{location} must be a trimmed non-empty string")
    if "\n" in value or "\r" in value:
        raise ContinuousAuditError(f"{location} must be a single line")
    return value


def _parse_utc(value: object, location: str) -> datetime:
    text = _require_string(value, location)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ContinuousAuditError(f"{location} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise ContinuousAuditError(f"{location} must include a timezone")
    return parsed.astimezone(timezone.utc)


def _assert_not_release_path(path: Path, location: str) -> None:
    candidate = Path(os.path.normpath(os.path.abspath(os.fspath(path))))
    forbidden = FORBIDDEN_RELEASE_ROOT
    if candidate == forbidden or forbidden in candidate.parents:
        raise ContinuousAuditError(f"{location} cannot use a production release path")


def _assert_no_symlink_components(path: Path, location: str) -> None:
    absolute = path if path.is_absolute() else Path.cwd() / path
    probe = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        probe = probe / part
        if probe.is_symlink():
            raise ContinuousAuditError(f"{location} cannot contain symlink components")


def _safe_repository_root(path: Path) -> Path:
    _assert_not_release_path(path, "repository root")
    _assert_no_symlink_components(path, "repository root")
    resolved = path.resolve(strict=True)
    if not resolved.is_dir():
        raise ContinuousAuditError("repository root must be an existing directory")
    if not (resolved / "AGENTS.md").is_file():
        raise ContinuousAuditError("repository root must contain AGENTS.md")
    return resolved


def _safe_repo_file(root: Path, raw_locator: object, location: str) -> tuple[str, Path]:
    locator = _require_string(raw_locator, location)
    pure = PurePosixPath(locator)
    if pure.is_absolute() or ".." in pure.parts or "\\" in locator:
        raise ContinuousAuditError(
            f"{location} must be a repository-relative POSIX file locator"
        )
    candidate = root / pure
    _assert_no_symlink_components(candidate, location)
    resolved = candidate.resolve(strict=False)
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ContinuousAuditError(f"{location} escapes the repository") from exc
    return locator, resolved


def _read_registry(path: Path, *, repository_root: Path) -> dict[str, Any]:
    _assert_not_release_path(path, "registry")
    _assert_no_symlink_components(path, "registry")
    resolved = path.resolve(strict=True)
    try:
        resolved.relative_to(repository_root)
    except ValueError as exc:
        raise ContinuousAuditError("registry must be inside the repository") from exc
    if not resolved.is_file():
        raise ContinuousAuditError("registry must be a regular file")
    stat = resolved.stat()
    if stat.st_nlink != 1:
        raise ContinuousAuditError("hard-linked registries are forbidden")
    if stat.st_size > MAX_REGISTRY_BYTES:
        raise ContinuousAuditError("registry exceeds the byte limit")
    try:
        payload = json.loads(
            resolved.read_text(encoding="utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=lambda value: (_ for _ in ()).throw(
                ContinuousAuditError(f"non-finite JSON number: {value}")
            ),
        )
    except ContinuousAuditError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ContinuousAuditError(f"cannot read audit registry: {exc}") from exc
    return _require_object(payload, "registry")


def validate_validator_plan(
    plan_path: Path,
    *,
    repository_root: Path,
) -> dict[str, Any]:
    """Validate a content-free offline validator plan without executing it."""

    _assert_not_release_path(plan_path, "validator plan")
    _assert_no_symlink_components(plan_path, "validator plan")
    resolved = plan_path.resolve(strict=True)
    try:
        resolved.relative_to(repository_root)
    except ValueError as exc:
        raise ContinuousAuditError("validator plan must be inside the repository") from exc
    stat = resolved.stat()
    if not resolved.is_file() or stat.st_nlink != 1:
        raise ContinuousAuditError("validator plan must be a single-link file")
    if stat.st_size <= 0 or stat.st_size > MAX_VALIDATOR_PLAN_BYTES:
        raise ContinuousAuditError("validator plan exceeds the byte limit")
    try:
        payload = json.loads(
            resolved.read_text(encoding="utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=lambda value: (_ for _ in ()).throw(
                ContinuousAuditError(f"non-finite JSON number: {value}")
            ),
        )
    except ContinuousAuditError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ContinuousAuditError("cannot read validator plan") from exc
    plan = _require_object(payload, "validator_plan")
    _require_exact_keys(plan, VALIDATOR_PLAN_ROOT_KEYS, "validator_plan")
    if plan["schema_version"] != "globemind-continuous-audit-validator-plan-v1":
        raise ContinuousAuditError("validator plan schema_version is invalid")
    for key, expected in (
        ("plan_state", "configured_manual_and_repository_ci"),
        ("execution_state", "configured_not_observed"),
        ("scheduler_state", "configured_daily_repository_workflow"),
        ("artifact_retention_state", "configured_30_day_repository_workflow"),
        ("issue_integration_state", "not_configured"),
        ("automation_owner_state", "role_declared_person_not_assigned"),
    ):
        if plan[key] != expected:
            raise ContinuousAuditError(f"validator_plan.{key} must be {expected}")
    safety = _require_object(plan["safety"], "validator_plan.safety")
    _require_exact_keys(safety, VALIDATOR_PLAN_SAFETY_KEYS, "validator_plan.safety")
    if any(value is not False for value in safety.values()):
        raise ContinuousAuditError("validator plan safety capabilities must all be false")
    _, runner_path = _safe_repo_file(
        repository_root,
        plan["runner_locator"],
        "validator_plan.runner_locator",
    )
    if not runner_path.is_file():
        raise ContinuousAuditError("validator_plan.runner_locator is missing")
    scheduler_locator, scheduler_path = _safe_repo_file(
        repository_root,
        plan["scheduler_locator"],
        "validator_plan.scheduler_locator",
    )
    retention_locator, retention_path = _safe_repo_file(
        repository_root,
        plan["artifact_retention_locator"],
        "validator_plan.artifact_retention_locator",
    )
    if scheduler_locator != ".github/workflows/quality-gate.yml":
        raise ContinuousAuditError("validator_plan.scheduler_locator is not approved")
    if retention_locator != scheduler_locator or retention_path != scheduler_path:
        raise ContinuousAuditError(
            "validator_plan.artifact_retention_locator must bind the scheduler workflow"
        )
    workflow_text = scheduler_path.read_text(encoding="utf-8")
    required_workflow_markers = (
        'cron: "17 2 * * *"',
        "python -B scripts/continuous_audit.py",
        "python -B scripts/run_continuous_audit_validators.py",
        "python -B scripts/continuous_audit_triage.py",
        "actions/upload-artifact@v6",
        "retention-days: 30",
    )
    if any(marker not in workflow_text for marker in required_workflow_markers):
        raise ContinuousAuditError(
            "validator plan workflow lacks the approved schedule, runner, or retention contract"
        )
    automation_owner_role = _require_string(
        plan["automation_owner_role"], "validator_plan.automation_owner_role"
    )
    if automation_owner_role != "quality-security-accessibility":
        raise ContinuousAuditError("validator_plan.automation_owner_role is invalid")
    validators = plan["validators"]
    if not isinstance(validators, list) or not 1 <= len(validators) <= 32:
        raise ContinuousAuditError("validator_plan.validators must contain 1-32 items")
    ids: list[str] = []
    domains: set[str] = set()
    checked_locators = 3
    for index, raw in enumerate(validators):
        location = f"validator_plan.validators[{index}]"
        item = _require_object(raw, location)
        _require_exact_keys(item, VALIDATOR_PLAN_ITEM_KEYS, location)
        validator_id = _require_string(item["id"], f"{location}.id")
        if VALIDATOR_PLAN_ID_RE.fullmatch(validator_id) is None:
            raise ContinuousAuditError(f"{location}.id is invalid")
        ids.append(validator_id)
        domain = _require_string(item["domain"], f"{location}.domain")
        if domain not in EXPECTED_DOMAIN_COUNTS:
            raise ContinuousAuditError(f"{location}.domain is invalid")
        domains.add(domain)
        kind = _require_string(item["kind"], f"{location}.kind")
        if kind not in {"python_static", "pytest_contract"}:
            raise ContinuousAuditError(f"{location}.kind is invalid")
        _, locator_path = _safe_repo_file(
            repository_root, item["locator"], f"{location}.locator"
        )
        if not locator_path.is_file():
            raise ContinuousAuditError(f"{location}.locator is missing")
        checked_locators += 1
        if item["policy_locator"] is not None:
            _, policy_path = _safe_repo_file(
                repository_root,
                item["policy_locator"],
                f"{location}.policy_locator",
            )
            if not policy_path.is_file():
                raise ContinuousAuditError(f"{location}.policy_locator is missing")
            checked_locators += 1
    if len(ids) != len(set(ids)):
        raise ContinuousAuditError("validator plan IDs must be unique")
    return {
        "status": "passed",
        "plan_state": plan["plan_state"],
        "execution_state": plan["execution_state"],
        "scheduler_state": plan["scheduler_state"],
        "artifact_retention_state": plan["artifact_retention_state"],
        "issue_integration_state": plan["issue_integration_state"],
        "automation_owner_state": plan["automation_owner_state"],
        "runner_locator": plan["runner_locator"],
        "scheduler_locator": plan["scheduler_locator"],
        "artifact_retention_locator": plan["artifact_retention_locator"],
        "automation_owner_role": automation_owner_role,
        "validator_count": len(validators),
        "domain_count": len(domains),
        "checked_locator_count": checked_locators,
    }


def _validate_owner_roles(value: object) -> set[str]:
    if not isinstance(value, list) or not value:
        raise ContinuousAuditError("registry.owner_roles must be a non-empty array")
    role_ids: list[str] = []
    for index, raw_role in enumerate(value):
        location = f"registry.owner_roles[{index}]"
        role = _require_object(raw_role, location)
        _require_exact_keys(role, OWNER_KEYS, location)
        role_id = _require_string(role["id"], f"{location}.id")
        if ROLE_ID_RE.fullmatch(role_id) is None:
            raise ContinuousAuditError(f"{location}.id has an invalid role identifier")
        _require_string(role["description"], f"{location}.description")
        role_ids.append(role_id)
    duplicates = sorted(name for name, count in Counter(role_ids).items() if count > 1)
    if duplicates:
        raise ContinuousAuditError(f"registry.owner_roles contains duplicates: {duplicates}")
    return set(role_ids)


def _validate_blocker(value: object, location: str) -> dict[str, str] | None:
    if value is None:
        return None
    blocker = _require_object(value, location)
    _require_exact_keys(blocker, BLOCKER_KEYS, location)
    kind = _require_string(blocker["kind"], f"{location}.kind")
    if kind not in BLOCKER_KINDS:
        raise ContinuousAuditError(
            f"{location}.kind must be one of {sorted(BLOCKER_KINDS)}"
        )
    return {
        "kind": kind,
        "description": _require_string(blocker["description"], f"{location}.description"),
        "needed": _require_string(blocker["needed"], f"{location}.needed"),
    }


def _locator_check(
    root: Path,
    raw_locator: object,
    location: str,
    missing: list[dict[str, str]],
) -> str | None:
    if raw_locator is None:
        return None
    locator, resolved = _safe_repo_file(root, raw_locator, location)
    if not resolved.is_file():
        missing.append({"location": location, "locator": locator})
    return locator


def validate_registry(
    payload: dict[str, Any],
    *,
    repository_root: Path,
    now: datetime,
) -> dict[str, Any]:
    """Validate a decoded registry and return a content-free audit report."""

    if not isinstance(now, datetime) or now.tzinfo is None:
        raise ContinuousAuditError("evaluation time must include a timezone")
    now = now.astimezone(timezone.utc)

    _require_exact_keys(payload, ROOT_KEYS, "registry")
    if payload["schema_version"] != SCHEMA_VERSION:
        raise ContinuousAuditError(f"registry.schema_version must be {SCHEMA_VERSION!r}")
    if payload["method_version"] != METHOD_VERSION:
        raise ContinuousAuditError(f"registry.method_version must be {METHOD_VERSION!r}")
    _require_string(payload["registry_id"], "registry.registry_id")
    registry_updated_at = _parse_utc(payload["updated_at"], "registry.updated_at")
    if registry_updated_at > now + MAX_CLOCK_SKEW:
        raise ContinuousAuditError("registry.updated_at cannot be in the future")
    _require_string(payload["scope"], "registry.scope")
    if payload["allowed_statuses"] != list(ALLOWED_STATUSES):
        raise ContinuousAuditError(
            "registry.allowed_statuses must exactly match the five approved statuses"
        )
    automation_state = _require_string(
        payload["automation_state"], "registry.automation_state"
    )
    if automation_state not in {"not_configured", "configured_discovery_only"}:
        raise ContinuousAuditError(
            "registry.automation_state must be not_configured or configured_discovery_only"
        )
    _require_string(
        payload["automation_state_reason"], "registry.automation_state_reason"
    )
    owner_roles = _validate_owner_roles(payload["owner_roles"])

    items = payload["items"]
    if not isinstance(items, list):
        raise ContinuousAuditError("registry.items must be an array")

    ids: list[str] = []
    missing_locators: list[dict[str, str]] = []
    stale_evidence: list[dict[str, Any]] = []
    checked_locators = 0
    evidence_count = 0
    status_counts: Counter[str] = Counter()
    severity_counts: Counter[str] = Counter()
    domain_statuses: dict[str, Counter[str]] = defaultdict(Counter)
    blocker_counts: Counter[str] = Counter()

    for index, raw_item in enumerate(items):
        location = f"registry.items[{index}]"
        item = _require_object(raw_item, location)
        _require_exact_keys(item, ITEM_KEYS, location)
        item_id = _require_string(item["id"], f"{location}.id")
        match = ITEM_ID_RE.fullmatch(item_id)
        if match is None:
            raise ContinuousAuditError(f"{location}.id has an invalid audit identifier")
        domain = _require_string(item["domain"], f"{location}.domain")
        if domain != match.group(1):
            raise ContinuousAuditError(f"{location}.domain does not match its ID")
        ids.append(item_id)
        _require_string(item["title"], f"{location}.title")
        severity = _require_string(item["severity"], f"{location}.severity")
        if severity not in ALLOWED_SEVERITIES:
            raise ContinuousAuditError(f"{location}.severity is not P0, P1, or P2")
        owner_role = _require_string(item["owner_role"], f"{location}.owner_role")
        if owner_role not in owner_roles:
            raise ContinuousAuditError(f"{location}.owner_role is not registered")
        status = _require_string(item["status"], f"{location}.status")
        if status not in ALLOWED_STATUSES:
            raise ContinuousAuditError(f"{location}.status is not an approved status")
        _require_string(item["status_basis"], f"{location}.status_basis")

        validator = _require_object(item["validator"], f"{location}.validator")
        _require_exact_keys(validator, VALIDATOR_KEYS, f"{location}.validator")
        validator_kind = _require_string(
            validator["kind"], f"{location}.validator.kind"
        )
        if validator_kind not in VALIDATOR_KINDS:
            raise ContinuousAuditError(
                f"{location}.validator.kind is not an approved validator kind"
            )
        validator_locator = _locator_check(
            repository_root,
            validator["locator"],
            f"{location}.validator.locator",
            missing_locators,
        )
        if validator_locator is not None:
            checked_locators += 1
        if validator_kind in {"pytest", "node_test", "static_inspection", "manual_review"}:
            if validator_locator is None:
                raise ContinuousAuditError(
                    f"{location}.validator.locator is required for {validator_kind}"
                )
        elif validator_locator is not None:
            raise ContinuousAuditError(
                f"{location}.validator.locator must be null for {validator_kind}"
            )

        evidence = item["evidence"]
        if not isinstance(evidence, list):
            raise ContinuousAuditError(f"{location}.evidence must be an array")
        evidence_fingerprints: set[str] = set()
        for evidence_index, raw_evidence in enumerate(evidence):
            evidence_location = f"{location}.evidence[{evidence_index}]"
            record = _require_object(raw_evidence, evidence_location)
            _require_exact_keys(record, EVIDENCE_KEYS, evidence_location)
            locator = _locator_check(
                repository_root,
                record["locator"],
                f"{evidence_location}.locator",
                missing_locators,
            )
            assert locator is not None
            checked_locators += 1
            evidence_count += 1
            if locator in evidence_fingerprints:
                raise ContinuousAuditError(
                    f"{location}.evidence contains duplicate locator {locator!r}"
                )
            evidence_fingerprints.add(locator)
            observed_at = _parse_utc(record["observed_at"], f"{evidence_location}.observed_at")
            if observed_at > now + MAX_CLOCK_SKEW:
                raise ContinuousAuditError(
                    f"{evidence_location}.observed_at cannot be in the future"
                )
            if observed_at > registry_updated_at:
                raise ContinuousAuditError(
                    f"{evidence_location}.observed_at cannot exceed the registry source cutoff"
                )
            max_age_days = record["max_age_days"]
            if type(max_age_days) is not int or max_age_days <= 0 or max_age_days > 3650:
                raise ContinuousAuditError(
                    f"{evidence_location}.max_age_days must be an integer from 1 to 3650"
                )
            scope = _require_string(record["scope"], f"{evidence_location}.scope")
            expires_at = observed_at + timedelta(days=max_age_days)
            if now > expires_at:
                stale_evidence.append(
                    {
                        "item_id": item_id,
                        "locator": locator,
                        "scope": scope,
                        "observed_at": observed_at.isoformat().replace("+00:00", "Z"),
                        "expired_at": expires_at.isoformat().replace("+00:00", "Z"),
                    }
                )

        blocker = _validate_blocker(item["blocker"], f"{location}.blocker")
        if blocker is not None:
            blocker_counts[blocker["kind"]] += 1

        if status == "PROVEN_CODE":
            if not evidence or validator_kind in {"external_acceptance", "not_configured"}:
                raise ContinuousAuditError(
                    f"{location} PROVEN_CODE requires evidence and an executable/static validator"
                )
            if blocker is not None:
                raise ContinuousAuditError(f"{location} PROVEN_CODE cannot have a blocker")
        elif status in {"OBSERVED_SAMPLE", "PARTIAL"}:
            if not evidence or blocker is None:
                raise ContinuousAuditError(
                    f"{location} {status} requires evidence and an honest blocker"
                )
        elif status == "EXTERNAL_BLOCKED":
            if blocker is None or blocker["kind"] != "external_dependency":
                raise ContinuousAuditError(
                    f"{location} EXTERNAL_BLOCKED requires an external_dependency blocker"
                )
            if validator_kind != "external_acceptance":
                raise ContinuousAuditError(
                    f"{location} EXTERNAL_BLOCKED requires external_acceptance"
                )
        else:
            if blocker is None or validator_kind != "not_configured" or evidence:
                raise ContinuousAuditError(
                    f"{location} NOT_STARTED_OR_UNVERIFIED requires no evidence, "
                    "a blocker, and validator kind not_configured"
                )

        status_counts[status] += 1
        severity_counts[severity] += 1
        domain_statuses[domain][status] += 1

    duplicates = sorted(name for name, count in Counter(ids).items() if count > 1)
    observed_ids = set(ids)
    missing_ids = sorted(set(EXPECTED_IDS) - observed_ids)
    unexpected_ids = sorted(observed_ids - set(EXPECTED_IDS))
    completeness_passed = not missing_ids and not unexpected_ids and len(ids) == len(EXPECTED_IDS)
    uniqueness_passed = not duplicates and len(ids) == len(observed_ids)
    locators_passed = not missing_locators
    staleness_passed = not stale_evidence

    checks = {
        "registry_completeness": {
            "status": "passed" if completeness_passed else "failed",
            "expected_count": len(EXPECTED_IDS),
            "observed_count": len(ids),
            "missing_ids": missing_ids,
            "unexpected_ids": unexpected_ids,
        },
        "registry_uniqueness": {
            "status": "passed" if uniqueness_passed else "failed",
            "duplicate_ids": duplicates,
        },
        "locator_existence": {
            "status": "passed" if locators_passed else "failed",
            "checked_count": checked_locators,
            "missing": missing_locators,
        },
        "evidence_staleness": {
            "status": "passed" if staleness_passed else "finding",
            "checked_count": evidence_count,
            "stale": stale_evidence,
        },
        "automation_configuration": {
            "status": "finding" if automation_state == "not_configured" else "passed",
            "automation_state": automation_state,
            "reason": payload["automation_state_reason"],
        },
    }
    integrity_passed = completeness_passed and uniqueness_passed and locators_passed
    findings: list[dict[str, Any]] = []
    if not completeness_passed:
        findings.append(
            {
                "code": "AUDIT_REGISTRY_INCOMPLETE",
                "severity": "error",
                "detail": "The registry does not contain exactly the expected 130 audit IDs.",
            }
        )
    if not uniqueness_passed:
        findings.append(
            {
                "code": "AUDIT_REGISTRY_DUPLICATE_ID",
                "severity": "error",
                "detail": "One or more audit IDs are duplicated.",
            }
        )
    if not locators_passed:
        findings.append(
            {
                "code": "AUDIT_LOCATOR_MISSING",
                "severity": "error",
                "detail": "One or more repository-relative validator/evidence files are missing.",
            }
        )
    if stale_evidence:
        findings.append(
            {
                "code": "AUDIT_EVIDENCE_STALE",
                "severity": "warning",
                "detail": f"{len(stale_evidence)} evidence locator(s) exceeded their declared age.",
            }
        )
    if automation_state == "not_configured":
        findings.append(
            {
                "code": "AUDIT_AUTOMATION_NOT_CONFIGURED",
                "severity": "warning",
                "detail": payload["automation_state_reason"],
            }
        )

    source_revision = read_source_revision(repository_root)
    if source_revision["worktree_state"] == "dirty":
        findings.append(
            {
                "code": "AUDIT_DIRTY_WORKTREE_UNATTESTED",
                "severity": "warning",
                "detail": (
                    "Git HEAD does not bind the current uncommitted worktree; "
                    "no worktree content hash is asserted."
                ),
            }
        )

    return {
        "report_schema_version": REPORT_SCHEMA_VERSION,
        "method_version": METHOD_VERSION,
        "registry_id": payload["registry_id"],
        "generated_at": now.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        "source_cutoff_at": payload["updated_at"],
        "source_revision": source_revision,
        "scope": "repository source, registry, validator files, and evidence-file metadata only",
        "content_retention": {
            "secrets": False,
            "article_bodies": False,
            "personal_information": False,
        },
        "status": (
            "failed"
            if not integrity_passed
            else ("completed_with_findings" if findings else "passed")
        ),
        "integrity_passed": integrity_passed,
        "checks": checks,
        "summary": {
            "items": len(ids),
            "statuses": {status: status_counts[status] for status in ALLOWED_STATUSES},
            "severities": {
                severity: severity_counts[severity]
                for severity in sorted(ALLOWED_SEVERITIES)
            },
            "domains": {
                domain: {
                    status: domain_statuses[domain][status]
                    for status in ALLOWED_STATUSES
                }
                for domain in EXPECTED_DOMAIN_COUNTS
            },
            "blockers": dict(sorted(blocker_counts.items())),
        },
        "findings": findings,
        "limitations": [
            "This run does not import or execute GlobeMind application code.",
            "This run does not access releases, services, databases, credentials, "
            "or external APIs.",
            "A present test/evidence locator does not prove that candidate or "
            "production acceptance passed.",
            "The repository declares scheduling and 30-day artifact retention; "
            "observed execution, issue creation, trend comparison, and named human "
            "ownership remain separate evidence or configuration.",
            "Git HEAD is reported separately from the worktree; no aggregate "
            "worktree content hash is computed or asserted.",
        ],
    }


def _safe_git_environment() -> dict[str, str]:
    """Return a minimal environment for read-only local Git observations."""

    return {
        "PATH": "/usr/bin:/bin",
        "LC_ALL": "C",
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_TERMINAL_PROMPT": "0",
    }


def _safe_git_prefix() -> list[str]:
    """Disable repository-configured helpers for local read-only Git calls."""

    return [
        str(SYSTEM_GIT),
        "-c",
        "core.fsmonitor=false",
        "-c",
        "core.hooksPath=/dev/null",
        "-c",
        "submodule.recurse=false",
    ]


def read_code_sha(repository_root: Path) -> str | None:
    """Read HEAD without hooks, locks, network, or inherited credential variables."""

    if not SYSTEM_GIT.is_file():
        return None

    try:
        result = subprocess.run(
            [*_safe_git_prefix(), "rev-parse", "--verify", "HEAD"],
            cwd=repository_root,
            env=_safe_git_environment(),
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    sha = result.stdout.strip()
    return sha if result.returncode == 0 and SHA1_RE.fullmatch(sha) else None


def read_worktree_state(repository_root: Path) -> str:
    """Observe clean/dirty state without retaining or reporting path names."""

    if not SYSTEM_GIT.is_file():
        return "unavailable"
    try:
        result = subprocess.run(
            [
                *_safe_git_prefix(),
                "status",
                "--porcelain=v1",
                "--untracked-files=normal",
                "--ignore-submodules=all",
            ],
            cwd=repository_root,
            env=_safe_git_environment(),
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return "unavailable"
    if result.returncode != 0:
        return "unavailable"
    return "dirty" if result.stdout else "clean"


def read_source_revision(repository_root: Path) -> dict[str, str | None]:
    """Scope HEAD separately from uncommitted content that is not hashed."""

    head_sha = read_code_sha(repository_root)
    worktree_state = read_worktree_state(repository_root)
    identity_scope = {
        "clean": "git_head_with_clean_status_observation",
        "dirty": "git_head_only_dirty_worktree_unhashed",
        "unavailable": "git_head_only_worktree_state_unavailable",
    }[worktree_state]
    return {
        "head_sha": head_sha,
        "worktree_state": worktree_state,
        "worktree_content_sha256": None,
        "identity_scope": identity_scope,
    }


def load_and_validate(
    registry_path: Path = DEFAULT_REGISTRY,
    *,
    repository_root: Path = REPOSITORY_ROOT,
    now: datetime | None = None,
) -> dict[str, Any]:
    root = _safe_repository_root(repository_root)
    payload = _read_registry(registry_path, repository_root=root)
    evaluated_at = now or datetime.now(timezone.utc)
    if evaluated_at.tzinfo is None:
        raise ContinuousAuditError("evaluation time must include a timezone")
    report = validate_registry(
        payload,
        repository_root=root,
        now=evaluated_at.astimezone(timezone.utc),
    )
    plan_path = root / "config" / "continuous-audit-validators.json"
    report["checks"]["validator_plan_configuration"] = validate_validator_plan(
        plan_path,
        repository_root=root,
    )
    plan_check = report["checks"]["validator_plan_configuration"]
    registry_automation = report["checks"]["automation_configuration"][
        "automation_state"
    ]
    if registry_automation == "configured_discovery_only" and (
        not str(plan_check["scheduler_state"]).startswith("configured_")
        or not str(plan_check["artifact_retention_state"]).startswith("configured_")
    ):
        raise ContinuousAuditError(
            "registry automation claim is not backed by scheduler and retention evidence"
        )
    report["limitations"].insert(
        4,
        "The validator plan and repository workflow are validated, but this run does not observe CI execution, human triage, or issue creation.",
    )
    return report


def _safe_output_directory(output_dir: Path, *, repository_root: Path) -> Path:
    if not output_dir.is_absolute():
        raise ContinuousAuditError("--output-dir must be an explicit absolute path")
    _assert_not_release_path(output_dir, "output directory")
    _assert_no_symlink_components(output_dir, "output directory")
    resolved = output_dir.resolve(strict=False)
    try:
        resolved.relative_to(repository_root)
    except ValueError:
        pass
    else:
        raise ContinuousAuditError("output directory must be outside the repository")

    if resolved.exists():
        if not resolved.is_dir():
            raise ContinuousAuditError("output directory must be a directory")
        if any(resolved.iterdir()):
            raise ContinuousAuditError("output directory must be empty")
    else:
        parent = resolved.parent
        _assert_no_symlink_components(parent, "output parent")
        if not parent.is_dir():
            raise ContinuousAuditError("output parent must already exist")
        os.mkdir(resolved, mode=0o750)
    return resolved


def _write_no_replace(path: Path, content: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(path, flags, 0o640)
    try:
        with os.fdopen(fd, "wb", closefd=False) as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        os.close(fd)


def render_markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    lines = [
        "# GlobeMind Continuous Audit Report",
        "",
        f"- Generated at: `{report['generated_at']}`",
        f"- Source cutoff: `{report['source_cutoff_at']}`",
        "- Code HEAD SHA: "
        f"`{report['source_revision']['head_sha'] or 'unavailable'}`",
        f"- Worktree state: `{report['source_revision']['worktree_state']}`",
        "- Worktree content SHA-256: "
        f"`{report['source_revision']['worktree_content_sha256'] or 'unavailable'}`",
        f"- Source identity scope: `{report['source_revision']['identity_scope']}`",
        f"- Method: `{report['method_version']}`",
        f"- Result: `{report['status']}`",
        f"- Automation: `{report['checks']['automation_configuration']['automation_state']}`",
        "",
        "## Registry summary",
        "",
        "| Status | Count |",
        "| --- | ---: |",
    ]
    for status in ALLOWED_STATUSES:
        lines.append(f"| `{status}` | {summary['statuses'][status]} |")
    lines.extend(
        [
            "",
            "## Checks",
            "",
            "| Check | Result | Detail |",
            "| --- | --- | --- |",
        ]
    )
    for name, check in report["checks"].items():
        if name == "registry_completeness":
            detail = f"{check['observed_count']}/{check['expected_count']} items"
        elif name == "registry_uniqueness":
            detail = f"{len(check['duplicate_ids'])} duplicate IDs"
        elif name == "locator_existence":
            detail = f"{check['checked_count']} checked; {len(check['missing'])} missing"
        elif name == "evidence_staleness":
            detail = f"{check['checked_count']} checked; {len(check['stale'])} stale"
        elif name == "automation_configuration":
            detail = check["automation_state"]
        else:
            detail = (
                f"{check['validator_count']} validators / "
                f"{check['domain_count']} domains; {check['execution_state']}"
            )
        lines.append(f"| `{name}` | `{check['status']}` | {detail} |")

    lines.extend(["", "## Findings", ""])
    if report["findings"]:
        for finding in report["findings"]:
            lines.append(
                f"- `{finding['severity']}` `{finding['code']}`: {finding['detail']}"
            )
    else:
        lines.append("- None.")
    lines.extend(["", "## Limitations", ""])
    lines.extend(f"- {limitation}" for limitation in report["limitations"])
    return "\n".join(lines) + "\n"


def write_reports(
    output_dir: Path,
    report: dict[str, Any],
    *,
    repository_root: Path = REPOSITORY_ROOT,
) -> tuple[Path, Path]:
    root = _safe_repository_root(repository_root)
    target = _safe_output_directory(output_dir, repository_root=root)
    json_path = target / REPORT_JSON_NAME
    markdown_path = target / REPORT_MARKDOWN_NAME
    json_content = (
        json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode("utf-8")
    markdown_content = render_markdown(report).encode("utf-8")
    _write_no_replace(json_path, json_content)
    _write_no_replace(markdown_path, markdown_content)
    directory_fd = os.open(target, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)
    return json_path, markdown_path


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repository-root",
        type=Path,
        default=REPOSITORY_ROOT,
        help="source repository root; production release paths are forbidden",
    )
    parser.add_argument(
        "--registry",
        type=Path,
        default=DEFAULT_REGISTRY,
        help="audit registry inside the repository",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        required=True,
        help="absolute, nonexistent or empty directory outside the repository",
    )
    parser.add_argument(
        "--now",
        help="optional ISO-8601 evaluation time for reproducible offline tests",
    )
    return parser.parse_args(argv)


def main(argv: Iterable[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        now = _parse_utc(args.now, "--now") if args.now else datetime.now(timezone.utc)
        root = _safe_repository_root(args.repository_root)
        registry = args.registry
        if registry == DEFAULT_REGISTRY and root != REPOSITORY_ROOT:
            registry = root / "ops" / "audit" / "registry.json"
        report = load_and_validate(registry, repository_root=root, now=now)
        json_path, markdown_path = write_reports(
            args.output_dir,
            report,
            repository_root=root,
        )
    except ContinuousAuditError as exc:
        print(json.dumps({"status": "error", "reason": str(exc)}), file=sys.stderr)
        return 2
    except FileExistsError:
        print(
            json.dumps({"status": "error", "reason": "report output already exists"}),
            file=sys.stderr,
        )
        return 2

    print(
        json.dumps(
            {
                "status": report["status"],
                "items": report["summary"]["items"],
                "automation_state": report["checks"]["automation_configuration"][
                    "automation_state"
                ],
                "json": json_path.name,
                "markdown": markdown_path.name,
            },
            sort_keys=True,
        )
    )
    return 0 if report["integrity_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
