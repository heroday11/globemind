#!/usr/bin/env python3
"""Compare two content-free continuous-audit triage receipts offline."""

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
TRIAGE_SCHEMA_VERSION = "globemind-continuous-audit-triage-v1"
TREND_SCHEMA_VERSION = "globemind-continuous-audit-trend-v1"
MAX_INPUT_BYTES = 4 * 1024 * 1024
REPORT_JSON_NAME = "continuous-audit-trend.json"
REPORT_MARKDOWN_NAME = "continuous-audit-trend.md"
_VALIDATOR_STATUS_RANK = {"passed": 0, "finding": 1, "failed": 2}


class ContinuousAuditTrendError(RuntimeError):
    """The trend inputs or output boundary cannot be trusted."""


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for key, value in pairs:
        if key in output:
            raise ContinuousAuditTrendError(f"duplicate JSON key: {key!r}")
        output[key] = value
    return output


def _assert_safe_path(path: Path, field: str) -> None:
    if not path.is_absolute():
        raise ContinuousAuditTrendError(f"{field} path must be absolute")
    normalized = Path(os.path.abspath(os.path.normpath(path)))
    if normalized == FORBIDDEN_RELEASE_ROOT or FORBIDDEN_RELEASE_ROOT in normalized.parents:
        raise ContinuousAuditTrendError(f"{field} cannot use a production release")
    probe = Path(normalized.anchor)
    for part in normalized.parts[1:]:
        probe /= part
        if probe.is_symlink():
            raise ContinuousAuditTrendError(f"{field} cannot contain symlinks")


def _read_triage(path: Path, field: str) -> tuple[dict[str, Any], str]:
    _assert_safe_path(path, field)
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError as exc:
        raise ContinuousAuditTrendError(f"{field} is unavailable") from exc
    try:
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            before = os.fstat(descriptor)
            if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
                raise ContinuousAuditTrendError(f"{field} must be a single-link file")
            if before.st_size <= 0 or before.st_size > MAX_INPUT_BYTES:
                raise ContinuousAuditTrendError(f"{field} exceeds its byte boundary")
            raw = handle.read(MAX_INPUT_BYTES + 1)
            after = os.fstat(descriptor)
        try:
            path_after = path.stat()
        except OSError as exc:
            raise ContinuousAuditTrendError(f"{field} changed while being read") from exc
        before_identity = (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
        )
        after_identity = (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
        )
        path_identity = (
            path_after.st_dev,
            path_after.st_ino,
            path_after.st_size,
            path_after.st_mtime_ns,
        )
        if before_identity != after_identity or after_identity != path_identity:
            raise ContinuousAuditTrendError(f"{field} changed while being read")
        if len(raw) != before.st_size or len(raw) > MAX_INPUT_BYTES:
            raise ContinuousAuditTrendError(f"{field} exceeds its byte boundary")
    except OSError as exc:
        raise ContinuousAuditTrendError(f"{field} is unavailable") from exc
    finally:
        os.close(descriptor)
    try:
        payload = json.loads(
            raw,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=lambda value: (_ for _ in ()).throw(
                ContinuousAuditTrendError(
                    f"{field} contains non-finite JSON number: {value}"
                )
            ),
        )
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ContinuousAuditTrendError(f"{field} is not strict JSON") from exc
    if not isinstance(payload, dict):
        raise ContinuousAuditTrendError(f"{field} root must be an object")
    _validate_triage(payload, field)
    return payload, hashlib.sha256(raw).hexdigest()


def _parse_time(value: Any, field: str) -> datetime:
    if not isinstance(value, str):
        raise ContinuousAuditTrendError(f"{field} must be an ISO timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ContinuousAuditTrendError(f"{field} must be an ISO timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ContinuousAuditTrendError(f"{field} must include a timezone")
    return parsed.astimezone(timezone.utc)


def _safe_count(value: Any, field: str) -> int:
    if type(value) is not int or value < 0:
        raise ContinuousAuditTrendError(f"{field} must be a non-negative integer")
    return value


def _validate_triage(report: Mapping[str, Any], field: str) -> None:
    if report.get("schema_version") != TRIAGE_SCHEMA_VERSION:
        raise ContinuousAuditTrendError(f"{field} schema is invalid")
    _parse_time(report.get("generated_at"), f"{field} generated_at")
    if report.get("action_state") not in {
        "human_triage_required",
        "no_actionable_finding_observed",
    }:
        raise ContinuousAuditTrendError(f"{field} action state is invalid")
    retention = report.get("content_retention")
    if not isinstance(retention, dict) or not retention or any(
        value is not False for value in retention.values()
    ):
        raise ContinuousAuditTrendError(f"{field} retained content")
    boundaries = report.get("automation_boundaries")
    if (
        not isinstance(boundaries, dict)
        or boundaries.get("issue_created") is not False
        or boundaries.get("external_message_sent") is not False
        or boundaries.get("candidate_or_production_acceptance") != "not_performed"
    ):
        raise ContinuousAuditTrendError(f"{field} automation boundary is invalid")
    findings = report.get("audit_findings")
    if not isinstance(findings, list) or len(findings) > 256:
        raise ContinuousAuditTrendError(f"{field} findings are invalid")
    finding_keys: set[tuple[str, str]] = set()
    for finding in findings:
        if (
            not isinstance(finding, dict)
            or set(finding) != {"code", "severity"}
            or not isinstance(finding.get("code"), str)
            or not finding["code"]
            or finding.get("severity") not in {"warning", "error"}
        ):
            raise ContinuousAuditTrendError(f"{field} finding identity is invalid")
        key = (finding["severity"], finding["code"])
        if key in finding_keys:
            raise ContinuousAuditTrendError(f"{field} findings contain duplicates")
        finding_keys.add(key)
    validators = report.get("validator_results")
    if not isinstance(validators, list) or len(validators) > 32:
        raise ContinuousAuditTrendError(f"{field} validators are invalid")
    validator_ids: set[str] = set()
    for validator in validators:
        if (
            not isinstance(validator, dict)
            or not isinstance(validator.get("id"), str)
            or not validator["id"]
            or validator.get("status") not in _VALIDATOR_STATUS_RANK
            or validator["id"] in validator_ids
        ):
            raise ContinuousAuditTrendError(f"{field} validator identity is invalid")
        validator_ids.add(validator["id"])
    summary = report.get("summary")
    if not isinstance(summary, dict) or not isinstance(summary.get("status_counts"), dict):
        raise ContinuousAuditTrendError(f"{field} summary is invalid")
    _safe_count(summary.get("registry_items"), f"{field} registry_items")
    for status, count in summary["status_counts"].items():
        if not isinstance(status, str) or not status:
            raise ContinuousAuditTrendError(f"{field} status identity is invalid")
        _safe_count(count, f"{field} status {status}")


def compare_triage_reports(
    baseline: Mapping[str, Any],
    current: Mapping[str, Any],
    *,
    baseline_sha256: str,
    current_sha256: str,
    evaluated_at: datetime,
) -> dict[str, Any]:
    """Produce only descriptive transitions; no threshold or issue action is inferred."""

    _validate_triage(baseline, "baseline triage")
    _validate_triage(current, "current triage")
    if evaluated_at.tzinfo is None or evaluated_at.utcoffset() is None:
        raise ContinuousAuditTrendError("evaluated_at must include a timezone")
    baseline_at = _parse_time(baseline["generated_at"], "baseline generated_at")
    current_at = _parse_time(current["generated_at"], "current generated_at")
    evaluated_utc = evaluated_at.astimezone(timezone.utc)
    if baseline_at >= current_at:
        raise ContinuousAuditTrendError("current triage must follow baseline triage")
    if current_at > evaluated_utc:
        raise ContinuousAuditTrendError("current triage cannot be in the future")
    baseline_items = baseline["summary"]["registry_items"]
    current_items = current["summary"]["registry_items"]
    if baseline_items != current_items:
        raise ContinuousAuditTrendError("registry item scope changed")

    baseline_findings = {
        (item["severity"], item["code"]) for item in baseline["audit_findings"]
    }
    current_findings = {
        (item["severity"], item["code"]) for item in current["audit_findings"]
    }

    def finding_records(values: set[tuple[str, str]]) -> list[dict[str, str]]:
        return [
            {"severity": severity, "code": code}
            for severity, code in sorted(values)
        ]

    baseline_validators = {
        item["id"]: item for item in baseline["validator_results"]
    }
    current_validators = {item["id"]: item for item in current["validator_results"]}
    transitions: list[dict[str, str]] = []
    for validator_id in sorted(set(baseline_validators) & set(current_validators)):
        before = baseline_validators[validator_id]["status"]
        after = current_validators[validator_id]["status"]
        if _VALIDATOR_STATUS_RANK[after] > _VALIDATOR_STATUS_RANK[before]:
            direction = "regressed"
        elif _VALIDATOR_STATUS_RANK[after] < _VALIDATOR_STATUS_RANK[before]:
            direction = "recovered"
        else:
            direction = "unchanged"
        transitions.append(
            {
                "id": validator_id,
                "baseline_status": before,
                "current_status": after,
                "direction": direction,
            }
        )
    baseline_counts = baseline["summary"]["status_counts"]
    current_counts = current["summary"]["status_counts"]
    status_deltas = {
        status: {
            "baseline": baseline_counts.get(status, 0),
            "current": current_counts.get(status, 0),
            "delta": current_counts.get(status, 0) - baseline_counts.get(status, 0),
        }
        for status in sorted(set(baseline_counts) | set(current_counts))
    }
    return {
        "schema_version": TREND_SCHEMA_VERSION,
        "evaluated_at": evaluated_utc.isoformat().replace("+00:00", "Z"),
        "inputs": {
            "baseline_triage_sha256": baseline_sha256,
            "current_triage_sha256": current_sha256,
            "baseline_generated_at": baseline["generated_at"],
            "current_generated_at": current["generated_at"],
            "registry_items": current_items,
        },
        "action_state_transition": {
            "baseline": baseline["action_state"],
            "current": current["action_state"],
        },
        "findings": {
            "new": finding_records(current_findings - baseline_findings),
            "resolved": finding_records(baseline_findings - current_findings),
            "persisting": finding_records(baseline_findings & current_findings),
        },
        "validators": {
            "scope_added": sorted(set(current_validators) - set(baseline_validators)),
            "scope_removed": sorted(set(baseline_validators) - set(current_validators)),
            "transitions": transitions,
        },
        "registry_status_deltas": status_deltas,
        "trend_claim": "descriptive_content_free_comparison_only",
        "threshold_approval_state": "not_configured",
        "issue_created": False,
        "external_message_sent": False,
        "human_triage_completed": False,
        "candidate_or_production_acceptance": "not_performed",
        "content_retention": {
            "finding_details": False,
            "validator_output": False,
            "article_bodies": False,
            "personal_information": False,
            "secrets": False,
        },
    }


def _prepare_output(path: Path) -> Path:
    _assert_safe_path(path, "output directory")
    resolved = path.resolve(strict=False)
    try:
        resolved.relative_to(REPOSITORY_ROOT.resolve())
    except ValueError:
        pass
    else:
        raise ContinuousAuditTrendError("output directory must be outside repository")
    if resolved.exists():
        if not resolved.is_dir() or any(resolved.iterdir()):
            raise ContinuousAuditTrendError("output directory must be empty")
    else:
        if not resolved.parent.is_dir():
            raise ContinuousAuditTrendError("output parent must exist")
        resolved.mkdir(mode=0o750)
    return resolved


def _write_new(path: Path, body: bytes) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o640)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(body)
        handle.flush()
        os.fsync(handle.fileno())


def write_report(output_dir: Path, report: Mapping[str, Any]) -> tuple[Path, Path]:
    output = _prepare_output(output_dir)
    json_path = output / REPORT_JSON_NAME
    markdown_path = output / REPORT_MARKDOWN_NAME
    json_body = (json.dumps(report, indent=2, sort_keys=True) + "\n").encode()
    markdown_body = (
        "# Continuous audit trend\n\n"
        f"- New findings: `{len(report['findings']['new'])}`\n"
        f"- Resolved findings: `{len(report['findings']['resolved'])}`\n"
        f"- Validator regressions: `{sum(item['direction'] == 'regressed' for item in report['validators']['transitions'])}`\n"
        "- Trend claim: `descriptive_content_free_comparison_only`\n"
        "- Issue created: `false`\n"
    ).encode()
    _write_new(json_path, json_body)
    try:
        _write_new(markdown_path, markdown_body)
    except BaseException:
        json_path.unlink(missing_ok=True)
        raise
    return json_path, markdown_path


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-triage", required=True, type=Path)
    parser.add_argument("--current-triage", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    return parser.parse_args(argv)


def main(argv: Iterable[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        baseline, baseline_sha = _read_triage(args.baseline_triage, "baseline triage")
        current, current_sha = _read_triage(args.current_triage, "current triage")
        report = compare_triage_reports(
            baseline,
            current,
            baseline_sha256=baseline_sha,
            current_sha256=current_sha,
            evaluated_at=datetime.now(timezone.utc),
        )
        json_path, markdown_path = write_report(args.output_dir, report)
    except (ContinuousAuditTrendError, OSError) as exc:
        print(json.dumps({"status": "error", "reason": str(exc)}), file=sys.stderr)
        return 2
    print(
        json.dumps(
            {"status": "completed", "json": json_path.name, "markdown": markdown_path.name},
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
