from __future__ import annotations

import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
RUNNER_PATH = PROJECT_ROOT / "scripts" / "continuous_audit_triage.py"
POLICY_PATH = PROJECT_ROOT / "config" / "continuous-audit-triage-policy.json"

SPEC = importlib.util.spec_from_file_location("continuous_audit_triage", RUNNER_PATH)
assert SPEC is not None and SPEC.loader is not None
triage = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(triage)


def _audit(*, findings: list[dict[str, str]] | None = None) -> dict:
    return {
        "report_schema_version": "globemind-continuous-audit-report-v2",
        "integrity_passed": True,
        "source_cutoff_at": "2026-08-10T06:22:00Z",
        "source_revision": {"worktree_state": "dirty"},
        "content_retention": {
            "article_bodies": False,
            "personal_information": False,
            "secrets": False,
        },
        "findings": findings or [],
        "checks": {"evidence_staleness": {"stale": []}},
        "summary": {
            "items": 130,
            "statuses": {
                "PROVEN_CODE": 48,
                "OBSERVED_SAMPLE": 1,
                "PARTIAL": 63,
                "EXTERNAL_BLOCKED": 10,
                "NOT_STARTED_OR_UNVERIFIED": 8,
            },
        },
    }


def _validator(*, status: str = "passed", body_retained: bool = False) -> dict:
    digest = {
        "body_retained": body_retained,
        "byte_count": 0,
        "captured_byte_count": 0,
        "sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        "truncated": False,
    }
    return {
        "schema_version": "globemind-continuous-audit-validator-run-v1",
        "results": [
            {
                "id": "country-data-contract",
                "domain": "CD",
                "status": status,
                "return_code": 0 if status == "passed" else 1,
                "timed_out": False,
                "stdout": digest,
                "stderr": digest,
            }
        ],
    }


def _policy() -> dict:
    return triage._load_policy(POLICY_PATH)


def test_triage_requires_human_action_but_never_creates_issue_or_retains_bodies() -> None:
    report = triage.build_triage_report(
        _audit(
            findings=[
                {
                    "code": "AUDIT_DIRTY_WORKTREE_UNATTESTED",
                    "severity": "warning",
                    "detail": "must not be retained",
                }
            ]
        ),
        _validator(),
        _policy(),
        audit_sha256="a" * 64,
        validator_sha256="b" * 64,
        generated_at=datetime(2026, 8, 10, 7, tzinfo=timezone.utc),
    )

    assert report["action_state"] == "human_triage_required"
    assert report["reason_codes"] == ["AUDIT_FINDINGS_PRESENT"]
    assert report["audit_findings"] == [
        {"code": "AUDIT_DIRTY_WORKTREE_UNATTESTED", "severity": "warning"}
    ]
    assert report["automation_boundaries"]["issue_created"] is False
    assert report["automation_boundaries"]["issue_creation_state"] == "not_configured"
    assert report["automation_boundaries"]["trend_state"] == "baseline_unavailable"
    assert all(value is False for value in report["content_retention"].values())
    assert "must not be retained" not in json.dumps(report)


def test_triage_distinguishes_no_finding_from_validator_failure() -> None:
    clean = triage.build_triage_report(
        _audit(),
        _validator(),
        _policy(),
        audit_sha256="a" * 64,
        validator_sha256="b" * 64,
        generated_at=datetime(2026, 8, 10, 7, tzinfo=timezone.utc),
    )
    assert clean["action_state"] == "no_actionable_finding_observed"
    assert clean["reason_codes"] == []
    assert clean["automation_boundaries"]["candidate_or_production_acceptance"] == (
        "not_performed"
    )

    failed = triage.build_triage_report(
        _audit(),
        _validator(status="failed"),
        _policy(),
        audit_sha256="a" * 64,
        validator_sha256="b" * 64,
        generated_at=datetime(2026, 8, 10, 7, tzinfo=timezone.utc),
    )
    assert failed["action_state"] == "human_triage_required"
    assert failed["reason_codes"] == ["VALIDATOR_FAILURE_PRESENT"]


def test_triage_fails_closed_on_retained_validator_output() -> None:
    with pytest.raises(triage.ContinuousAuditTriageError, match="retained output"):
        triage.build_triage_report(
            _audit(),
            _validator(body_retained=True),
            _policy(),
            audit_sha256="a" * 64,
            validator_sha256="b" * 64,
            generated_at=datetime(2026, 8, 10, 7, tzinfo=timezone.utc),
        )


def test_triage_output_is_external_empty_and_no_replace(tmp_path: Path) -> None:
    report = triage.build_triage_report(
        _audit(),
        _validator(),
        _policy(),
        audit_sha256="a" * 64,
        validator_sha256="b" * 64,
        generated_at=datetime(2026, 8, 10, 7, tzinfo=timezone.utc),
    )
    output = tmp_path / "triage"
    json_path, markdown_path = triage.write_report(output, report)
    assert json_path.stat().st_mode & 0o777 == 0o640
    assert markdown_path.stat().st_mode & 0o777 == 0o640
    assert "Issue created: `false`" in markdown_path.read_text(encoding="utf-8")
    with pytest.raises(triage.ContinuousAuditTriageError, match="empty"):
        triage.write_report(output, report)
    with pytest.raises(triage.ContinuousAuditTriageError, match="outside"):
        triage.write_report(PROJECT_ROOT / "triage-must-not-exist", report)
    assert not (PROJECT_ROOT / "triage-must-not-exist").exists()
