from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
RUNNER_PATH = PROJECT_ROOT / "scripts" / "run_continuous_audit_validators.py"
PLAN_PATH = PROJECT_ROOT / "config" / "continuous-audit-validators.json"

SPEC = importlib.util.spec_from_file_location("continuous_audit_validator_runner", RUNNER_PATH)
assert SPEC is not None and SPEC.loader is not None
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)


def _plan() -> dict:
    return runner._read_plan(PLAN_PATH, repository_root=PROJECT_ROOT)


def test_commands_are_derived_and_environment_does_not_inherit_secrets() -> None:
    plan = _plan()
    static = runner.build_validator_command(plan["validators"][0], repository_root=PROJECT_ROOT)
    pytest_command = runner.build_validator_command(plan["validators"][2], repository_root=PROJECT_ROOT)

    assert static[:2] == (str(runner.LOCKED_RUNTIME), "-B")
    assert pytest_command[:5] == (
        str(runner.LOCKED_RUNTIME),
        "-B",
        "-m",
        "pytest",
        str(PROJECT_ROOT / "backend/tests/test_search_qrels_dataset.py"),
    )
    environment = runner.clean_validator_environment(repository_root=PROJECT_ROOT)
    assert set(environment) == {"PATH", "LANG", "LC_ALL", "PYTHONDONTWRITEBYTECODE", "PYTHONPATH"}
    assert environment["PYTHONDONTWRITEBYTECODE"] == "1"
    assert "TOKEN" not in environment and "DATABASE_URL" not in environment
    assert runner.validate_python_runtime(runner.LOCKED_RUNTIME).is_file()
    with pytest.raises(runner.ValidatorRunError, match="absolute"):
        runner.validate_python_runtime(Path("python3"))
    with pytest.raises(runner.ValidatorRunError, match="release"):
        runner.validate_python_runtime(
            Path("/root/data/releases/globemind/current/python")
        )


def test_execute_validator_retains_only_bounded_output_digests(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    validator = _plan()["validators"][0]

    def fake_run(*_args: object, **kwargs: object) -> SimpleNamespace:
        assert kwargs["stdin"] is runner.subprocess.DEVNULL
        assert kwargs["env"] == runner.clean_validator_environment(repository_root=PROJECT_ROOT)
        return SimpleNamespace(returncode=0, stdout=b"safe output body", stderr=b"")

    monkeypatch.setattr(runner.subprocess, "run", fake_run)
    result = runner.execute_validator(validator, repository_root=PROJECT_ROOT)

    serialized = json.dumps(result)
    assert result["status"] == "passed"
    assert result["stdout"]["body_retained"] is False
    assert result["stdout"]["byte_count"] == len(b"safe output body")
    assert len(result["stdout"]["sha256"]) == 64
    assert "safe output body" not in serialized


def test_output_directory_is_external_empty_no_replace(tmp_path: Path) -> None:
    output = runner.prepare_output_dir(tmp_path / "evidence", repository_root=PROJECT_ROOT)
    report = runner.build_report(
        _plan(),
        [],
        started_at="2026-08-10T00:00:00Z",
    )
    assert report["execution_mode"] == "bounded_offline_plan"
    assert report["safety_boundaries"]["scheduler_declared_in_plan"] is True
    assert report["safety_boundaries"]["artifact_retention_declared_in_plan"] is True
    assert report["safety_boundaries"]["scheduler_control_initiated_by_runner"] is False
    assert (
        report["safety_boundaries"]["artifact_retention_control_initiated_by_runner"]
        is False
    )
    runner.write_report(output, report)

    assert (output / runner.REPORT_JSON_NAME).stat().st_mode & 0o777 == 0o640
    with pytest.raises(FileExistsError):
        runner.write_report(output, report)
    with pytest.raises(runner.ValidatorRunError, match="outside the repository"):
        runner.prepare_output_dir(PROJECT_ROOT / "audit-output", repository_root=PROJECT_ROOT)
    with pytest.raises(runner.ValidatorRunError, match="release"):
        runner.prepare_output_dir(
            Path("/root/data/releases/globemind/current/audit-output"),
            repository_root=PROJECT_ROOT,
        )
