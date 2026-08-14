from __future__ import annotations

import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = PROJECT_ROOT / "scripts" / "continuous_audit_trend.py"

SPEC = importlib.util.spec_from_file_location("continuous_audit_trend", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
trend = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(trend)


def _triage(
    *,
    generated_at: str,
    findings: list[dict[str, str]],
    validators: list[tuple[str, str]],
    partial: int,
) -> dict:
    return {
        "schema_version": "globemind-continuous-audit-triage-v1",
        "generated_at": generated_at,
        "policy_id": "globemind-bounded-audit-triage",
        "inputs": {
            "audit_report_sha256": "a" * 64,
            "validator_report_sha256": "b" * 64,
            "source_cutoff_at": generated_at,
            "worktree_state": "dirty",
        },
        "action_state": (
            "human_triage_required"
            if findings or any(status != "passed" for _, status in validators)
            else "no_actionable_finding_observed"
        ),
        "reason_codes": [],
        "audit_findings": findings,
        "validator_results": [
            {
                "id": validator_id,
                "domain": "QA",
                "status": status,
                "return_code": 0 if status == "passed" else 1,
                "timed_out": False,
            }
            for validator_id, status in validators
        ],
        "summary": {
            "registry_items": 130,
            "status_counts": {
                "PROVEN_CODE": 48 + (63 - partial),
                "PARTIAL": partial,
                "OBSERVED_SAMPLE": 1,
                "EXTERNAL_BLOCKED": 10,
                "NOT_STARTED_OR_UNVERIFIED": 8,
            },
            "stale_evidence_count": 0,
            "validator_failed_count": sum(
                status == "failed" for _, status in validators
            ),
            "validator_finding_count": sum(
                status == "finding" for _, status in validators
            ),
        },
        "automation_boundaries": {
            "issue_creation_state": "not_configured",
            "trend_baseline_state": "not_configured",
            "scheduler_execution_observation_state": "not_configured",
            "owner_role": "quality-security-accessibility",
            "named_person_state": "not_assigned",
            "human_triage_state": "required_for_action",
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


def test_content_free_trend_reports_findings_validator_transitions_and_deltas() -> None:
    baseline = _triage(
        generated_at="2026-08-09T07:00:00Z",
        findings=[{"code": "DIRTY", "severity": "warning"}],
        validators=[("country", "passed"), ("search", "finding")],
        partial=63,
    )
    current = _triage(
        generated_at="2026-08-10T07:00:00Z",
        findings=[
            {"code": "DIRTY", "severity": "warning"},
            {"code": "STALE", "severity": "error"},
        ],
        validators=[
            ("country", "failed"),
            ("search", "passed"),
            ("browser", "passed"),
        ],
        partial=62,
    )

    report = trend.compare_triage_reports(
        baseline,
        current,
        baseline_sha256="a" * 64,
        current_sha256="b" * 64,
        evaluated_at=datetime(2026, 8, 10, 7, 1, tzinfo=timezone.utc),
    )

    assert report["findings"] == {
        "new": [{"severity": "error", "code": "STALE"}],
        "resolved": [],
        "persisting": [{"severity": "warning", "code": "DIRTY"}],
    }
    transitions = {item["id"]: item for item in report["validators"]["transitions"]}
    assert transitions["country"]["direction"] == "regressed"
    assert transitions["search"]["direction"] == "recovered"
    assert report["validators"]["scope_added"] == ["browser"]
    assert report["registry_status_deltas"]["PROVEN_CODE"]["delta"] == 1
    assert report["registry_status_deltas"]["PARTIAL"]["delta"] == -1
    assert report["trend_claim"] == "descriptive_content_free_comparison_only"
    assert report["threshold_approval_state"] == "not_configured"
    assert report["issue_created"] is False
    assert report["human_triage_completed"] is False
    assert all(value is False for value in report["content_retention"].values())


def test_trend_rejects_retained_content_scope_and_time_drift() -> None:
    baseline = _triage(
        generated_at="2026-08-09T07:00:00Z",
        findings=[],
        validators=[("country", "passed")],
        partial=63,
    )
    current = _triage(
        generated_at="2026-08-10T07:00:00Z",
        findings=[],
        validators=[("country", "passed")],
        partial=63,
    )
    retained = dict(current)
    retained["content_retention"] = dict(current["content_retention"])
    retained["content_retention"]["validator_stdout"] = True
    with pytest.raises(trend.ContinuousAuditTrendError, match="retained content"):
        trend.compare_triage_reports(
            baseline,
            retained,
            baseline_sha256="a" * 64,
            current_sha256="b" * 64,
            evaluated_at=datetime(2026, 8, 10, 7, 1, tzinfo=timezone.utc),
        )

    reversed_time = dict(current)
    reversed_time["generated_at"] = baseline["generated_at"]
    with pytest.raises(trend.ContinuousAuditTrendError, match="must follow"):
        trend.compare_triage_reports(
            baseline,
            reversed_time,
            baseline_sha256="a" * 64,
            current_sha256="b" * 64,
            evaluated_at=datetime(2026, 8, 10, 7, 1, tzinfo=timezone.utc),
        )


def test_trend_reader_rejects_non_finite_json(tmp_path: Path) -> None:
    path = tmp_path / "triage.json"
    path.write_text(
        '{"schema_version":"globemind-continuous-audit-triage-v1","x":NaN}',
        encoding="utf-8",
    )

    with pytest.raises(trend.ContinuousAuditTrendError, match="non-finite"):
        trend._read_triage(path, "triage")


def test_trend_cli_inputs_and_outputs_are_hash_bound_external_and_no_replace(
    tmp_path: Path,
) -> None:
    baseline = _triage(
        generated_at="2026-08-09T07:00:00Z",
        findings=[],
        validators=[("country", "passed")],
        partial=63,
    )
    current = _triage(
        generated_at="2026-08-10T07:00:00Z",
        findings=[],
        validators=[("country", "passed")],
        partial=63,
    )
    baseline_path = tmp_path / "baseline.json"
    baseline_path.write_text(json.dumps(baseline), encoding="utf-8")
    current_path = tmp_path / "current.json"
    current_path.write_text(json.dumps(current), encoding="utf-8")
    loaded_baseline, baseline_sha = trend._read_triage(
        baseline_path,
        "baseline triage",
    )
    loaded_current, current_sha = trend._read_triage(current_path, "current triage")
    report = trend.compare_triage_reports(
        loaded_baseline,
        loaded_current,
        baseline_sha256=baseline_sha,
        current_sha256=current_sha,
        evaluated_at=datetime(2026, 8, 10, 7, 1, tzinfo=timezone.utc),
    )
    output = tmp_path / "trend-output"
    json_path, markdown_path = trend.write_report(output, report)
    assert json_path.is_file() and markdown_path.is_file()
    assert report["inputs"]["baseline_triage_sha256"] == baseline_sha
    with pytest.raises(trend.ContinuousAuditTrendError, match="empty"):
        trend.write_report(output, report)
    with pytest.raises(trend.ContinuousAuditTrendError, match="outside"):
        trend.write_report(PROJECT_ROOT / "trend-must-not-exist", report)
    assert not (PROJECT_ROOT / "trend-must-not-exist").exists()
