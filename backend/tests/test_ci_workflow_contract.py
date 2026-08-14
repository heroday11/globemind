from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_PATH = PROJECT_ROOT / ".github/workflows/quality-gate.yml"


def _workflow() -> dict:
    return yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))


def _workflow_commands(workflow: dict) -> str:
    jobs = workflow.get("jobs", {})
    return "\n".join(
        step["run"]
        for job in jobs.values()
        for step in job.get("steps", [])
        if isinstance(step.get("run"), str)
    )


def test_ci_runs_full_quality_gate_and_release_tooling_contracts() -> None:
    commands = _workflow_commands(_workflow())

    assert 'deploy/run_quality_gate.sh --output "$RUNNER_TEMP/quality-gate.json"' in commands
    assert "backend/tests/test_release_tooling.py" in commands
    assert "backend/tests/test_ci_workflow_contract.py" in commands
    assert "python -B scripts/continuous_audit.py" in commands
    assert "python -B scripts/run_continuous_audit_validators.py" in commands
    assert "python -B scripts/continuous_audit_triage.py" in commands
    assert '--python-runtime "$pythonLocation/bin/python"' in commands


def test_ci_schedules_bounded_audit_and_retains_only_declared_artifacts() -> None:
    workflow = _workflow()
    triggers = workflow.get("on", workflow.get(True))
    assert triggers["schedule"] == [{"cron": "17 2 * * *"}]
    assert workflow["permissions"] == {"contents": "read"}
    upload = next(
        step
        for step in workflow["jobs"]["verify"]["steps"]
        if step.get("uses") == "actions/upload-artifact@v6"
    )
    assert upload["if"] == "always()"
    assert upload["with"]["retention-days"] == 30
    assert upload["with"]["if-no-files-found"] == "error"
    paths = set(upload["with"]["path"].splitlines())
    assert paths == {
        "${{ runner.temp }}/quality-gate.json",
        "${{ runner.temp }}/continuous-audit-registry",
        "${{ runner.temp }}/continuous-audit-validators",
        "${{ runner.temp }}/continuous-audit-triage",
    }


def test_ci_does_not_create_or_verify_a_production_release() -> None:
    workflow = _workflow()
    commands = _workflow_commands(workflow)

    assert "deploy/create_release.sh" not in commands
    assert "deploy/verify_release.py" not in commands
    assert "--production" not in commands
    for job in workflow.get("jobs", {}).values():
        assert job.get("env", {}).get("PYTHON_BIN") not in {"python", "python3"}
        for step in job.get("steps", []):
            assert step.get("env", {}).get("PYTHON_BIN") not in {"python", "python3"}
