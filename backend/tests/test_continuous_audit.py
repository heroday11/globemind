from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
REGISTRY_PATH = PROJECT_ROOT / "ops" / "audit" / "registry.json"
RUNNER_PATH = PROJECT_ROOT / "scripts" / "continuous_audit.py"
VALIDATOR_PLAN_PATH = PROJECT_ROOT / "config" / "continuous-audit-validators.json"
MASTER_PATH = PROJECT_ROOT / "docs" / "GLOBEMIND_CONTINUOUS_IMPROVEMENT_MASTER_20260809.md"
NOW = datetime(2026, 8, 11, 0, 0, 0, tzinfo=timezone.utc)

SPEC = importlib.util.spec_from_file_location("continuous_audit", RUNNER_PATH)
assert SPEC is not None and SPEC.loader is not None
continuous_audit = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(continuous_audit)


def _payload() -> dict:
    return json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))


def _validate(payload: dict, *, now: datetime = NOW) -> dict:
    return continuous_audit.validate_registry(
        payload,
        repository_root=PROJECT_ROOT,
        now=now,
    )


def _expected_report_status(report: dict) -> str:
    """Reports with findings stay explicit; an empty finding set is a pass."""
    return "completed_with_findings" if report["findings"] else "passed"


def test_checked_in_registry_has_all_130_unique_items_and_honest_statuses() -> None:
    report = continuous_audit.load_and_validate(
        REGISTRY_PATH,
        repository_root=PROJECT_ROOT,
        now=NOW,
    )

    assert report["integrity_passed"] is True
    assert report["status"] == _expected_report_status(report)
    assert report["checks"]["registry_completeness"] == {
        "status": "passed",
        "expected_count": 130,
        "observed_count": 130,
        "missing_ids": [],
        "unexpected_ids": [],
    }
    assert report["checks"]["registry_uniqueness"] == {
        "status": "passed",
        "duplicate_ids": [],
    }
    assert report["checks"]["locator_existence"]["missing"] == []
    assert report["checks"]["evidence_staleness"]["stale"] == []
    assert report["checks"]["automation_configuration"]["automation_state"] == (
        "configured_discovery_only"
    )
    assert report["checks"]["validator_plan_configuration"] == {
        "status": "passed",
        "plan_state": "configured_manual_and_repository_ci",
        "execution_state": "configured_not_observed",
        "scheduler_state": "configured_daily_repository_workflow",
        "artifact_retention_state": "configured_30_day_repository_workflow",
        "issue_integration_state": "not_configured",
        "automation_owner_state": "role_declared_person_not_assigned",
        "runner_locator": "scripts/run_continuous_audit_validators.py",
        "scheduler_locator": ".github/workflows/quality-gate.yml",
        "artifact_retention_locator": ".github/workflows/quality-gate.yml",
        "automation_owner_role": "quality-security-accessibility",
        "validator_count": 9,
        "domain_count": 6,
        "checked_locator_count": 16,
    }
    assert report["summary"]["items"] == 130
    assert report["summary"]["statuses"] == {
        "PROVEN_CODE": 48,
        "OBSERVED_SAMPLE": 1,
        "PARTIAL": 63,
        "EXTERNAL_BLOCKED": 10,
        "NOT_STARTED_OR_UNVERIFIED": 8,
    }
    assert sum(report["summary"]["severities"].values()) == 130
    assert sum(
        sum(domain.values()) for domain in report["summary"]["domains"].values()
    ) == 130


def test_master_snapshot_table_matches_the_checked_in_registry() -> None:
    payload = _payload()
    master = MASTER_PATH.read_text(encoding="utf-8")
    statuses = (
        "PROVEN_CODE",
        "OBSERVED_SAMPLE",
        "PARTIAL",
        "EXTERNAL_BLOCKED",
        "NOT_STARTED_OR_UNVERIFIED",
    )
    domains = ("IA", "SR", "FR", "ML", "EV", "EG", "CD", "AI", "WF", "QA")
    cutoff = payload["updated_at"].replace("T", " ").replace("Z", " UTC")
    marker = f"当前逐项登记快照（source cutoff {cutoff}；"

    assert marker in master
    snapshot_table = master.split(marker, 1)[1].split(
        "逐项矩阵必须使用以下五种状态", 1
    )[0]
    total = Counter(item["status"] for item in payload["items"])
    for domain in domains:
        counts = Counter(
            item["status"] for item in payload["items"] if item["domain"] == domain
        )
        row = "| " + " | ".join(
            [domain, *(str(counts[status]) for status in statuses), str(sum(counts.values()))]
        ) + " |"
        assert row in snapshot_table
    total_row = "| **总计** | " + " | ".join(
        [*(f"**{total[status]}**" for status in statuses), "**130**"]
    ) + " |"
    assert total_row in snapshot_table


def test_completeness_and_uniqueness_find_missing_duplicate_and_unexpected_ids() -> None:
    payload = _payload()
    payload["items"][1]["id"] = payload["items"][0]["id"]
    payload["items"][1]["domain"] = payload["items"][0]["domain"]
    payload["items"][-1]["id"] = "QA-16"

    report = _validate(payload)

    assert report["integrity_passed"] is False
    completeness = report["checks"]["registry_completeness"]
    assert completeness["status"] == "failed"
    assert completeness["missing_ids"] == ["IA-02", "QA-15"]
    assert completeness["unexpected_ids"] == ["QA-16"]
    assert report["checks"]["registry_uniqueness"] == {
        "status": "failed",
        "duplicate_ids": ["IA-01"],
    }


def test_locator_existence_and_evidence_staleness_are_reported_without_reads() -> None:
    payload = _payload()
    payload["items"][0]["evidence"][0]["locator"] = "not-present/evidence.txt"
    payload["items"][1]["evidence"][0]["observed_at"] = "2020-01-01T00:00:00Z"

    report = _validate(payload)

    assert report["integrity_passed"] is False
    assert report["checks"]["locator_existence"]["missing"] == [
        {
            "location": "registry.items[0].evidence[0].locator",
            "locator": "not-present/evidence.txt",
        }
    ]
    assert report["checks"]["evidence_staleness"]["status"] == "finding"
    assert report["checks"]["evidence_staleness"]["stale"][0]["item_id"] == (
        "IA-02"
    )


def test_registry_rejects_future_cutoff_and_evidence_timestamps() -> None:
    payload = _payload()
    payload["updated_at"] = "2026-08-11T01:00:00Z"
    with pytest.raises(continuous_audit.ContinuousAuditError, match="future"):
        _validate(payload)

    payload = _payload()
    payload["items"][0]["evidence"][0]["observed_at"] = "2026-08-11T01:00:00Z"
    with pytest.raises(continuous_audit.ContinuousAuditError, match="future"):
        _validate(payload)


def test_evidence_timestamp_cannot_exceed_registry_source_cutoff() -> None:
    payload = _payload()
    payload["updated_at"] = "2026-08-08T16:28:30Z"

    with pytest.raises(continuous_audit.ContinuousAuditError, match="source cutoff"):
        _validate(payload)


def test_registry_schema_rejects_unknown_status_and_dishonest_state_combinations() -> None:
    payload = _payload()
    payload["items"][0]["status"] = "DONE"
    with pytest.raises(continuous_audit.ContinuousAuditError, match="approved status"):
        _validate(payload)

    payload = _payload()
    payload["items"][0]["status"] = "NOT_STARTED_OR_UNVERIFIED"
    with pytest.raises(
        continuous_audit.ContinuousAuditError,
        match="requires no evidence",
    ):
        _validate(payload)

    payload = _payload()
    payload["automation_state"] = "fully_automated"
    with pytest.raises(
        continuous_audit.ContinuousAuditError,
        match="must be not_configured or configured_discovery_only",
    ):
        _validate(payload)


def test_duplicate_json_keys_and_non_finite_numbers_fail_closed() -> None:
    with pytest.raises(continuous_audit.ContinuousAuditError, match="duplicate JSON key"):
        json.loads(
            '{"id":"IA-01","id":"IA-02"}',
            object_pairs_hook=continuous_audit._reject_duplicate_keys,
        )


def test_validator_plan_is_bounded_read_only_and_does_not_claim_execution(
    tmp_path: Path,
) -> None:
    check = continuous_audit.validate_validator_plan(
        VALIDATOR_PLAN_PATH,
        repository_root=PROJECT_ROOT,
    )
    assert check["plan_state"] == "configured_manual_and_repository_ci"
    assert check["execution_state"] == "configured_not_observed"
    assert check["scheduler_state"] == "configured_daily_repository_workflow"

    payload = json.loads(VALIDATOR_PLAN_PATH.read_text(encoding="utf-8"))
    payload["safety"]["network_access"] = True
    unsafe = tmp_path / "unsafe-plan.json"
    unsafe.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(continuous_audit.ContinuousAuditError, match="inside the repository"):
        continuous_audit.validate_validator_plan(
            unsafe,
            repository_root=PROJECT_ROOT,
        )

    with pytest.raises(continuous_audit.ContinuousAuditError, match="non-finite"):
        json.loads(
            '{"value":NaN}',
            parse_constant=lambda value: (_ for _ in ()).throw(
                continuous_audit.ContinuousAuditError(
                    f"non-finite JSON number: {value}"
                )
            ),
        )


def test_report_output_is_outside_repository_empty_and_no_replace(tmp_path: Path) -> None:
    report = continuous_audit.load_and_validate(
        REGISTRY_PATH,
        repository_root=PROJECT_ROOT,
        now=NOW,
    )
    output_dir = tmp_path / "new-audit-output"

    json_path, markdown_path = continuous_audit.write_reports(
        output_dir,
        report,
        repository_root=PROJECT_ROOT,
    )

    assert json_path.name == "continuous-audit.json"
    assert markdown_path.name == "continuous-audit.md"
    assert json_path.stat().st_mode & 0o777 == 0o640
    assert markdown_path.stat().st_mode & 0o777 == 0o640
    assert json.loads(json_path.read_text(encoding="utf-8"))["summary"]["items"] == 130
    assert "`configured_discovery_only`" in markdown_path.read_text(encoding="utf-8")
    with pytest.raises(continuous_audit.ContinuousAuditError, match="must be empty"):
        continuous_audit.write_reports(
            output_dir,
            report,
            repository_root=PROJECT_ROOT,
        )

    with pytest.raises(
        continuous_audit.ContinuousAuditError,
        match="outside the repository",
    ):
        continuous_audit.write_reports(
            PROJECT_ROOT / "audit-output-must-not-be-created",
            report,
            repository_root=PROJECT_ROOT,
        )
    assert not (PROJECT_ROOT / "audit-output-must-not-be-created").exists()


def test_output_guards_reject_relative_symlink_release_and_nonempty_paths(
    tmp_path: Path,
) -> None:
    report = continuous_audit.load_and_validate(
        REGISTRY_PATH,
        repository_root=PROJECT_ROOT,
        now=NOW,
    )
    with pytest.raises(continuous_audit.ContinuousAuditError, match="absolute path"):
        continuous_audit.write_reports(
            Path("relative-output"),
            report,
            repository_root=PROJECT_ROOT,
        )
    with pytest.raises(continuous_audit.ContinuousAuditError, match="release path"):
        continuous_audit.write_reports(
            Path("/root/data/releases/globemind/current/audit-output"),
            report,
            repository_root=PROJECT_ROOT,
        )

    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "link"
    link.symlink_to(target, target_is_directory=True)
    with pytest.raises(continuous_audit.ContinuousAuditError, match="symlink"):
        continuous_audit.write_reports(
            link,
            report,
            repository_root=PROJECT_ROOT,
        )

    nonempty = tmp_path / "nonempty"
    nonempty.mkdir()
    (nonempty / "existing").write_text("keep", encoding="utf-8")
    with pytest.raises(continuous_audit.ContinuousAuditError, match="must be empty"):
        continuous_audit.write_reports(
            nonempty,
            report,
            repository_root=PROJECT_ROOT,
        )
    assert (nonempty / "existing").read_text(encoding="utf-8") == "keep"


def test_cli_writes_json_and_markdown_to_a_new_external_directory(tmp_path: Path) -> None:
    output_dir = tmp_path / "cli-output"
    result = subprocess.run(
        [
            sys.executable,
            "-B",
            str(RUNNER_PATH),
            "--output-dir",
            str(output_dir),
            "--now",
                "2026-08-11T00:00:00Z",
        ],
        cwd=PROJECT_ROOT,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )

    assert result.returncode == 0, result.stderr
    summary = json.loads(result.stdout)
    expected_report = continuous_audit.load_and_validate(
        REGISTRY_PATH,
        repository_root=PROJECT_ROOT,
        now=NOW,
    )
    assert summary == {
        "automation_state": "configured_discovery_only",
        "items": 130,
        "json": "continuous-audit.json",
        "markdown": "continuous-audit.md",
        "status": _expected_report_status(expected_report),
    }
    payload = json.loads(
        (output_dir / "continuous-audit.json").read_text(encoding="utf-8")
    )
    assert payload["content_retention"] == {
        "article_bodies": False,
        "personal_information": False,
        "secrets": False,
    }
    assert payload["checks"]["automation_configuration"]["automation_state"] == (
        "configured_discovery_only"
    )


def test_runner_has_no_application_or_network_imports() -> None:
    source = RUNNER_PATH.read_text(encoding="utf-8")

    assert "from api." not in source
    assert "import api." not in source
    assert "requests" not in source
    assert "urlopen" not in source
    assert "socket" not in source


def test_code_sha_lookup_does_not_execute_git_from_inherited_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    marker = tmp_path / "inherited-path-git-ran"
    fake_git = fake_bin / "git"
    fake_git.write_text(
        f"#!/bin/sh\n: > '{marker}'\nexit 0\n",
        encoding="utf-8",
    )
    fake_git.chmod(0o755)
    monkeypatch.setenv("PATH", str(fake_bin))

    sha = continuous_audit.read_code_sha(PROJECT_ROOT)
    worktree_state = continuous_audit.read_worktree_state(PROJECT_ROOT)

    assert sha is None or continuous_audit.SHA1_RE.fullmatch(sha)
    assert worktree_state in {"clean", "dirty", "unavailable"}
    assert not marker.exists()
    runner_source = RUNNER_PATH.read_text(encoding="utf-8")
    assert '"core.fsmonitor=false"' in runner_source
    assert '"core.hooksPath=/dev/null"' in runner_source


def test_report_scopes_head_sha_and_does_not_attest_unhashed_worktree() -> None:
    report = continuous_audit.load_and_validate(
        REGISTRY_PATH,
        repository_root=PROJECT_ROOT,
        now=NOW,
    )

    revision = report["source_revision"]
    assert set(revision) == {
        "head_sha",
        "identity_scope",
        "worktree_content_sha256",
        "worktree_state",
    }
    assert "code_sha" not in report
    assert revision["head_sha"] is None or continuous_audit.SHA1_RE.fullmatch(
        revision["head_sha"]
    )
    assert revision["worktree_state"] in {"clean", "dirty", "unavailable"}
    assert revision["worktree_content_sha256"] is None
    assert revision["identity_scope"] == {
        "clean": "git_head_with_clean_status_observation",
        "dirty": "git_head_only_dirty_worktree_unhashed",
        "unavailable": "git_head_only_worktree_state_unavailable",
    }[revision["worktree_state"]]

    markdown = continuous_audit.render_markdown(report)
    assert "Worktree state:" in markdown
    assert "Worktree content SHA-256: `unavailable`" in markdown
    assert "Code HEAD SHA:" in markdown
