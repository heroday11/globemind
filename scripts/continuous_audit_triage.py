#!/usr/bin/env python3
"""Create a bounded, content-free triage receipt from one offline audit run."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN_RELEASE_ROOT = Path("/root/data/releases/globemind")
DEFAULT_POLICY = REPOSITORY_ROOT / "config" / "continuous-audit-triage-policy.json"
REPORT_SCHEMA_VERSION = "globemind-continuous-audit-triage-v1"
POLICY_SCHEMA_VERSION = "globemind-continuous-audit-triage-policy-v1"
AUDIT_SCHEMA_VERSION = "globemind-continuous-audit-report-v2"
VALIDATOR_SCHEMA_VERSION = "globemind-continuous-audit-validator-run-v1"
MAX_INPUT_BYTES = 4 * 1024 * 1024
MAX_POLICY_BYTES = 256 * 1024
REPORT_JSON_NAME = "continuous-audit-triage.json"
REPORT_MARKDOWN_NAME = "continuous-audit-triage.md"


class ContinuousAuditTriageError(RuntimeError):
    """The triage inputs or output boundary cannot be trusted."""


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for key, value in pairs:
        if key in output:
            raise ContinuousAuditTriageError(f"duplicate JSON key: {key!r}")
        output[key] = value
    return output


def _assert_not_release(path: Path, field: str) -> None:
    candidate = Path(os.path.abspath(os.path.normpath(path)))
    if candidate == FORBIDDEN_RELEASE_ROOT or FORBIDDEN_RELEASE_ROOT in candidate.parents:
        raise ContinuousAuditTriageError(f"{field} cannot use a production release")


def _assert_no_symlink_components(path: Path, field: str) -> None:
    absolute = path if path.is_absolute() else Path.cwd() / path
    probe = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        probe /= part
        if probe.is_symlink():
            raise ContinuousAuditTriageError(f"{field} cannot contain symlinks")


def _read_json(path: Path, *, maximum: int, field: str) -> tuple[dict[str, Any], str]:
    if not path.is_absolute():
        raise ContinuousAuditTriageError(f"{field} path must be absolute")
    _assert_not_release(path, field)
    _assert_no_symlink_components(path, field)
    try:
        metadata = path.stat()
    except OSError as exc:
        raise ContinuousAuditTriageError(f"{field} is unavailable") from exc
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
        raise ContinuousAuditTriageError(f"{field} must be a single-link file")
    if metadata.st_size <= 0 or metadata.st_size > maximum:
        raise ContinuousAuditTriageError(f"{field} exceeds its byte boundary")
    try:
        raw = path.read_bytes()
        after = path.stat()
    except OSError as exc:
        raise ContinuousAuditTriageError(f"{field} could not be read") from exc
    if (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_size,
        metadata.st_mtime_ns,
    ) != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
        raise ContinuousAuditTriageError(f"{field} changed while being read")
    try:
        payload = json.loads(
            raw,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=lambda value: (_ for _ in ()).throw(
                ContinuousAuditTriageError(
                    f"{field} contains non-finite JSON number: {value}"
                )
            ),
        )
    except ContinuousAuditTriageError:
        raise
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ContinuousAuditTriageError(f"{field} is not strict JSON") from exc
    if not isinstance(payload, dict):
        raise ContinuousAuditTriageError(f"{field} root must be an object")
    return payload, hashlib.sha256(raw).hexdigest()


def _exact_keys(value: Mapping[str, Any], expected: set[str], field: str) -> None:
    if set(value) != expected:
        raise ContinuousAuditTriageError(f"{field} schema keys are invalid")


def _load_policy(path: Path) -> dict[str, Any]:
    payload, _ = _read_json(path, maximum=MAX_POLICY_BYTES, field="triage policy")
    _exact_keys(
        payload,
        {"schema_version", "policy_id", "thresholds", "ownership", "integrations"},
        "triage policy",
    )
    if payload["schema_version"] != POLICY_SCHEMA_VERSION:
        raise ContinuousAuditTriageError("triage policy schema_version is invalid")
    thresholds = payload["thresholds"]
    ownership = payload["ownership"]
    integrations = payload["integrations"]
    if not isinstance(thresholds, dict) or thresholds != {
        "audit_integrity_required": True,
        "validator_failure_limit": 0,
        "validator_finding_limit": 0,
        "stale_evidence_limit": 0,
    }:
        raise ContinuousAuditTriageError("triage thresholds are not the fail-closed set")
    if not isinstance(ownership, dict) or ownership != {
        "owner_role": "quality-security-accessibility",
        "named_person_state": "not_assigned",
        "human_triage_state": "required_for_action",
    }:
        raise ContinuousAuditTriageError("triage ownership boundary is invalid")
    if not isinstance(integrations, dict) or integrations != {
        "issue_creation_state": "not_configured",
        "trend_baseline_state": "not_configured",
        "scheduler_execution_observation_state": "not_configured",
    }:
        raise ContinuousAuditTriageError("triage integration boundary is invalid")
    return payload


def _safe_int(value: Any, field: str) -> int:
    if type(value) is not int or value < 0:
        raise ContinuousAuditTriageError(f"{field} must be a non-negative integer")
    return value


def build_triage_report(
    audit: Mapping[str, Any],
    validator: Mapping[str, Any],
    policy: Mapping[str, Any],
    *,
    audit_sha256: str,
    validator_sha256: str,
    generated_at: datetime,
) -> dict[str, Any]:
    """Validate content-free summaries and return a human-triage receipt."""

    if generated_at.tzinfo is None or generated_at.utcoffset() is None:
        raise ContinuousAuditTriageError("generated_at must include a timezone")
    if audit.get("report_schema_version") != AUDIT_SCHEMA_VERSION:
        raise ContinuousAuditTriageError("audit report schema is invalid")
    if validator.get("schema_version") != VALIDATOR_SCHEMA_VERSION:
        raise ContinuousAuditTriageError("validator report schema is invalid")
    if audit.get("content_retention") != {
        "article_bodies": False,
        "personal_information": False,
        "secrets": False,
    }:
        raise ContinuousAuditTriageError("audit report retention boundary is invalid")
    results = validator.get("results")
    if not isinstance(results, list) or len(results) > 32:
        raise ContinuousAuditTriageError("validator results are invalid")
    validator_records: list[dict[str, Any]] = []
    for index, raw in enumerate(results):
        if not isinstance(raw, dict):
            raise ContinuousAuditTriageError("validator result must be an object")
        stdout = raw.get("stdout")
        stderr = raw.get("stderr")
        if (
            not isinstance(stdout, dict)
            or not isinstance(stderr, dict)
            or stdout.get("body_retained") is not False
            or stderr.get("body_retained") is not False
        ):
            raise ContinuousAuditTriageError(
                f"validator result {index} retained output bodies"
            )
        status = raw.get("status")
        if status not in {"passed", "finding", "failed"}:
            raise ContinuousAuditTriageError("validator result status is invalid")
        validator_records.append(
            {
                "id": str(raw.get("id", ""))[:80],
                "domain": str(raw.get("domain", ""))[:8],
                "status": status,
                "return_code": raw.get("return_code")
                if type(raw.get("return_code")) is int
                else None,
                "timed_out": raw.get("timed_out") is True,
            }
        )

    audit_findings = audit.get("findings")
    if not isinstance(audit_findings, list) or len(audit_findings) > 256:
        raise ContinuousAuditTriageError("audit findings are invalid")
    finding_records: list[dict[str, str]] = []
    for raw in audit_findings:
        if not isinstance(raw, dict):
            raise ContinuousAuditTriageError("audit finding must be an object")
        code = raw.get("code")
        severity = raw.get("severity")
        if not isinstance(code, str) or not code or severity not in {"warning", "error"}:
            raise ContinuousAuditTriageError("audit finding identity is invalid")
        finding_records.append({"code": code[:120], "severity": severity})

    checks = audit.get("checks")
    summary = audit.get("summary")
    if not isinstance(checks, dict) or not isinstance(summary, dict):
        raise ContinuousAuditTriageError("audit report summary is invalid")
    stale = checks.get("evidence_staleness", {}).get("stale")
    statuses = summary.get("statuses")
    if not isinstance(stale, list) or not isinstance(statuses, dict):
        raise ContinuousAuditTriageError("audit staleness or status summary is invalid")
    status_counts = {
        str(key): _safe_int(value, f"audit status {key}")
        for key, value in statuses.items()
    }
    failed_count = sum(item["status"] == "failed" for item in validator_records)
    validator_finding_count = sum(
        item["status"] == "finding" for item in validator_records
    )
    reasons: list[str] = []
    if audit.get("integrity_passed") is not True:
        reasons.append("AUDIT_INTEGRITY_FAILED")
    if finding_records:
        reasons.append("AUDIT_FINDINGS_PRESENT")
    if stale:
        reasons.append("STALE_EVIDENCE_PRESENT")
    if failed_count:
        reasons.append("VALIDATOR_FAILURE_PRESENT")
    if validator_finding_count:
        reasons.append("VALIDATOR_FINDING_PRESENT")
    action_state = (
        "human_triage_required" if reasons else "no_actionable_finding_observed"
    )
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "generated_at": generated_at.astimezone(timezone.utc)
        .isoformat()
        .replace("+00:00", "Z"),
        "policy_id": policy["policy_id"],
        "inputs": {
            "audit_report_sha256": audit_sha256,
            "validator_report_sha256": validator_sha256,
            "source_cutoff_at": audit.get("source_cutoff_at"),
            "worktree_state": audit.get("source_revision", {}).get("worktree_state"),
        },
        "action_state": action_state,
        "reason_codes": reasons,
        "audit_findings": sorted(
            finding_records,
            key=lambda item: (item["severity"], item["code"]),
        ),
        "validator_results": validator_records,
        "summary": {
            "registry_items": _safe_int(summary.get("items"), "registry item count"),
            "status_counts": status_counts,
            "stale_evidence_count": len(stale),
            "validator_failed_count": failed_count,
            "validator_finding_count": validator_finding_count,
        },
        "automation_boundaries": {
            **policy["integrations"],
            **policy["ownership"],
            "issue_created": False,
            "external_message_sent": False,
            "trend_state": "baseline_unavailable",
            "candidate_or_production_acceptance": "not_performed",
        },
        "content_retention": {
            "audit_finding_details": False,
            "validator_stdout": False,
            "validator_stderr": False,
            "article_bodies": False,
            "personal_information": False,
            "secrets": False,
        },
    }


def _prepare_output(path: Path) -> Path:
    if not path.is_absolute():
        raise ContinuousAuditTriageError("output directory must be absolute")
    _assert_not_release(path, "output directory")
    _assert_no_symlink_components(path, "output directory")
    resolved = path.resolve(strict=False)
    try:
        resolved.relative_to(REPOSITORY_ROOT.resolve())
    except ValueError:
        pass
    else:
        raise ContinuousAuditTriageError("output directory must be outside the repository")
    if resolved.exists():
        if not resolved.is_dir() or any(resolved.iterdir()):
            raise ContinuousAuditTriageError("output directory must be empty")
    else:
        if not resolved.parent.is_dir():
            raise ContinuousAuditTriageError("output parent must exist")
        resolved.mkdir(mode=0o750)
    return resolved


def _write_new(path: Path, body: bytes) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o640)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        try:
            os.close(descriptor)
        except OSError:
            pass
        raise


def write_report(output_dir: Path, report: Mapping[str, Any]) -> tuple[Path, Path]:
    output = _prepare_output(output_dir)
    json_path = output / REPORT_JSON_NAME
    markdown_path = output / REPORT_MARKDOWN_NAME
    json_body = (json.dumps(report, indent=2, sort_keys=True) + "\n").encode()
    markdown = (
        "# Continuous audit triage\n\n"
        f"- Action state: `{report['action_state']}`\n"
        f"- Reason codes: `{', '.join(report['reason_codes']) or 'none'}`\n"
        f"- Registry items: `{report['summary']['registry_items']}`\n"
        f"- Validator failures: `{report['summary']['validator_failed_count']}`\n"
        f"- Trend state: `{report['automation_boundaries']['trend_state']}`\n"
        "- Issue created: `false`\n"
    ).encode()
    _write_new(json_path, json_body)
    try:
        _write_new(markdown_path, markdown)
    except BaseException:
        json_path.unlink(missing_ok=True)
        raise
    return json_path, markdown_path


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit-report", required=True, type=Path)
    parser.add_argument("--validator-report", required=True, type=Path)
    parser.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    parser.add_argument("--output-dir", required=True, type=Path)
    return parser.parse_args(argv)


def main(argv: Iterable[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        audit, audit_sha = _read_json(
            args.audit_report,
            maximum=MAX_INPUT_BYTES,
            field="audit report",
        )
        validator, validator_sha = _read_json(
            args.validator_report,
            maximum=MAX_INPUT_BYTES,
            field="validator report",
        )
        policy = _load_policy(args.policy.resolve(strict=True))
        report = build_triage_report(
            audit,
            validator,
            policy,
            audit_sha256=audit_sha,
            validator_sha256=validator_sha,
            generated_at=datetime.now(timezone.utc),
        )
        json_path, markdown_path = write_report(args.output_dir, report)
    except (ContinuousAuditTriageError, OSError) as exc:
        print(json.dumps({"status": "error", "reason": str(exc)}), file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "status": "completed",
                "action_state": report["action_state"],
                "json": json_path.name,
                "markdown": markdown_path.name,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
