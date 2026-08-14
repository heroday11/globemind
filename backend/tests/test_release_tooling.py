from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import stat
import subprocess
import sys
import sysconfig
from pathlib import Path

import pytest

DEPLOY_DIR = Path(__file__).resolve().parents[2] / "deploy"
if str(DEPLOY_DIR) not in sys.path:
    sys.path.insert(0, str(DEPLOY_DIR))

from release_lib import (  # noqa: E402
    DEPENDENCY_MANIFEST_FILES,
    LEGACY_LOCK_FILES,
    LOCK_FILES,
    PRODUCTION_QUALITY_STEPS,
    REQUIRED_RUNTIME_FILES,
    RUNTIME_CATALOG_ARTIFACT_INPUTS,
    SCHEMA_VERSION,
    V1_REQUIRED_RUNTIME_FILES,
    ReleaseError,
    _verify_hashed_python_lock,
    copy_inputs,
    copy_release_backend,
    digest_content_bundle_source,
    digest_inputs,
    is_source_input_path,
    required_runtime_files,
    runtime_catalog_artifact_references,
    scan_secrets,
    scan_source_inputs,
    sha256_file,
    stage_content_bundles,
    verify_content_bundles,
    verify_external_python_runtime,
    verify_quality_gate,
    verify_release,
    verify_release_content_bundles,
    verify_runtime_catalog_artifact_copies,
    verify_staged_content_bundles,
    write_checksums,
)

PROJECT_ROOT = DEPLOY_DIR.parent


def _write(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")


def _git(project: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(project), *args],
        check=True,
        capture_output=True,
        text=True,
    )


def _init_release_git_project(tmp_path: Path) -> Path:
    project = tmp_path / "provenance-project"
    project.mkdir()
    _git(project, "init", "-q")
    _git(project, "config", "user.name", "GlobeMind Test")
    _git(project, "config", "user.email", "test@globemind.invalid")
    _write(project / "VERSION", "1.0.0\n")
    _write(project / "backend/api/application.py", "APP = True\n")
    _write(project / ".gitignore", "backend/cppt/\n")
    _git(project, "add", "VERSION", "backend/api/application.py", ".gitignore")
    _git(project, "commit", "-qm", "fixture")
    return project


def _release_provenance(project: Path) -> dict:
    result = subprocess.run(
        [
            sys.executable,
            "-B",
            str(DEPLOY_DIR / "release_tool.py"),
            "provenance",
            "--project",
            str(project),
        ],
        check=True,
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )
    return json.loads(result.stdout)


def _complete_quality_payload(source_snapshot: dict) -> dict:
    return {
        "schema_version": 1,
        "status": "passed",
        "scope": {
            "python_tests_skipped": False,
            "frontend_skipped": False,
        },
        "tests": {
            "status": "passed",
            "failures": 0,
            "errors": 0,
        },
        "ratchets": {
            "status": "passed",
            "vue_eslint": {
                "status": "passed",
                "actual": {"errors": 0, "warnings": 0, "fatal_errors": 0},
                "maximum": {"errors": 0, "warnings": 0, "fatal_errors": 0},
            },
            "financial_typescript": {
                "status": "passed",
                "actual_errors": 0,
                "maximum_errors": 0,
            },
        },
        "steps": [
            {"name": name, "status": "passed", "exit_code": 0}
            for name in sorted(PRODUCTION_QUALITY_STEPS)
        ],
        "source_snapshot": source_snapshot,
        "source_unchanged": True,
    }


def _make_quality_gate_project(tmp_path: Path) -> Path:
    project = tmp_path / "quality-project"
    (project / "deploy").mkdir(parents=True)
    shutil.copy2(DEPLOY_DIR / "run_quality_gate.sh", project / "deploy/run_quality_gate.sh")
    shutil.copy2(
        DEPLOY_DIR / "check_frontend_budgets.mjs",
        project / "deploy/check_frontend_budgets.mjs",
    )
    _write(project / "VERSION", "0.9.3\n")
    for name in ("create_release.sh", "build_frontend_release.sh", "start_web_prod.sh"):
        _write(project / "deploy" / name, "#!/bin/bash\nset -euo pipefail\n")
    _write(
        project / "deploy/verify_release.py",
        "import argparse\nargparse.ArgumentParser().parse_args()\n",
    )
    _write(
        project / "deploy/release_tool.py",
        """\
import json
import sys
from pathlib import Path

command = sys.argv[1]
output = Path(sys.argv[sys.argv.index("--output") + 1])
if command == "snapshot":
    payload = {"algorithm": "fixture", "file_count": 1, "sha256": "a" * 64}
elif command == "source-secret-scan":
    payload = {"status": "passed", "finding_count": 0, "findings": []}
elif command in {"content-bundles", "content-bundle-policy"}:
    payload = {"schema_version": 1, "status": "passed", "bundles": []}
else:
    raise SystemExit(2)
output.write_text(json.dumps(payload) + "\\n", encoding="utf-8")
""",
    )
    for name in (
        "check_import_boundaries.py",
        "check_feature_registry.py",
        "check_runtime_config_manifest.py",
        "check_database_consumers.py",
        "check_root_layout.py",
    ):
        _write(project / "scripts/ci" / name, "raise SystemExit(0)\n")
    _write(project / "backend/tests/test_release_tooling.py", "# lint target\n")
    _write(project / "backend/tests/test_database_consumer_inventory.py", "# lint target\n")
    _write(project / "backend/tests/test_feature_registry.py", "# lint target\n")
    return project


def _run_fixture_quality_gate(
    project: Path,
    report: Path,
    *,
    extra_env: dict[str, str],
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.update(
        {
            "PROJECT_DIR": str(project),
            "PYTHON_BIN": sys.executable,
            "PYTHONDONTWRITEBYTECODE": "1",
            **extra_env,
        }
    )
    return subprocess.run(
        [
            "bash",
            str(project / "deploy/run_quality_gate.sh"),
            "--output",
            str(report),
            "--skip-tests",
            "--skip-frontend",
        ],
        cwd=project,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_quality_gate_prefers_direct_ruff_and_records_exact_command(tmp_path: Path) -> None:
    project = _make_quality_gate_project(tmp_path)
    calls = tmp_path / "ruff-calls.txt"
    ruff = tmp_path / "ruff"
    _write(
        ruff,
        f"#!/bin/sh\nprintf '%s\\n' \"$*\" >> {calls}\n"
        "if [ \"${1:-}\" = --version ]; then echo 'ruff 9.9.9'; fi\n",
    )
    ruff.chmod(0o755)
    report = tmp_path / "quality.json"

    result = _run_fixture_quality_gate(
        project,
        report,
        extra_env={
            "RUFF_BIN": str(ruff),
            "TOOL_PYTHON_BIN": str(tmp_path / "must-not-run"),
        },
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["status"] == "partial"
    assert payload["scope"]["project_version"] == "0.9.3"
    assert payload["scope"]["feature_registry_mode"] == "inventory"
    assert payload["tools"]["ruff"] == "ruff 9.9.9"
    recorded = payload["tools"]["ruff_command"]
    assert recorded["selection"] == "RUFF_BIN"
    assert recorded["executable"] == str(ruff.resolve())
    assert recorded["argv"] == [
        str(ruff.resolve()),
        "check",
        "backend/serve_prod.py",
        "backend/tests/test_frontend_budget_gate.py",
        "backend/tests/test_static_path_security.py",
        "deploy/browser_smoke.py",
        "deploy/candidate_smoke.py",
        "deploy/promote_web_release.py",
        "deploy/release_lib.py",
        "deploy/release_tool.py",
        "deploy/verify_release.py",
        "deploy/web_promotion.py",
        "scripts/ci/check_database_consumers.py",
        "scripts/ci/check_feature_registry.py",
        "scripts/ci/check_import_boundaries.py",
        "scripts/ci/check_repository_hygiene.py",
        "scripts/ci/check_root_layout.py",
        "scripts/run_event_level_pipeline.py",
        "backend/tests/test_browser_smoke.py",
        "backend/tests/test_candidate_smoke.py",
        "backend/tests/test_ci_workflow_contract.py",
        "backend/tests/test_architecture_gates.py",
        "backend/tests/test_packaging_contract.py",
        "backend/tests/test_repository_hygiene.py",
        "backend/tests/test_runtime_control_aliases.py",
        "backend/tests/test_release_tooling.py",
        "backend/tests/test_database_consumer_inventory.py",
        "backend/tests/test_feature_registry.py",
        "backend/tests/test_root_layout.py",
        "backend/tests/test_web_promotion.py",
        "backend/api/features",
        "backend/api/routes/auth.py",
        "backend/api/routes/dashboard.py",
        "backend/api/routes/ops_monitor.py",
        "backend/tests/test_dashboard_feature.py",
        "backend/tests/test_database_runtime_roles.py",
        "backend/tests/test_feature_health.py",
        "backend/tests/test_identity_feature.py",
        "backend/tests/test_ops_runtime_catalog.py",
        "backend/tests/test_runtime_service_catalog.py",
        "backend/cc_integration.py",
        "backend/runtime_control",
        "deploy/db_role_policy.py",
        "deploy/db_runtime_roles.py",
        "scripts/runtime_control",
    ]
    calls_text = calls.read_text(encoding="utf-8")
    assert "--version" in calls_text
    assert "check backend/serve_prod.py backend/tests/test_frontend_budget_gate.py" in calls_text


def test_v1_quality_gate_requires_release_ready_feature_registry(tmp_path: Path) -> None:
    project = _make_quality_gate_project(tmp_path)
    _write(project / "VERSION", "1.0.0\n")
    capture = tmp_path / "feature-args.json"
    _write(
        project / "scripts/ci/check_feature_registry.py",
        "import json, os, sys\n"
        "open(os.environ['FEATURE_ARGS_CAPTURE'], 'w', encoding='utf-8').write("
        "json.dumps(sys.argv[1:]))\n",
    )
    ruff = tmp_path / "ruff"
    _write(
        ruff,
        "#!/bin/sh\n"
        "if [ \"${1:-}\" = --version ]; then echo 'ruff 9.9.9'; fi\n",
    )
    ruff.chmod(0o755)
    report = tmp_path / "quality.json"

    result = _run_fixture_quality_gate(
        project,
        report,
        extra_env={
            "RUFF_BIN": str(ruff),
            "FEATURE_ARGS_CAPTURE": str(capture),
        },
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(capture.read_text(encoding="utf-8")) == ["--release-ready"]
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["scope"]["project_version"] == "1.0.0"
    assert payload["scope"]["feature_registry_mode"] == "release-ready"


def test_quality_gate_supports_independent_tool_python_for_ruff(tmp_path: Path) -> None:
    project = _make_quality_gate_project(tmp_path)
    tool_python = tmp_path / "tool-python"
    _write(
        tool_python,
        "#!/bin/sh\n"
        "[ \"${1:-}\" = -B ] && [ \"${2:-}\" = -m ] && [ \"${3:-}\" = ruff ] || exit 88\n"
        "shift 3\n"
        "if [ \"${1:-}\" = --version ]; then echo 'ruff 8.8.8'; fi\n",
    )
    tool_python.chmod(0o755)
    report = tmp_path / "quality.json"

    result = _run_fixture_quality_gate(
        project,
        report,
        extra_env={"RUFF_BIN": "", "TOOL_PYTHON_BIN": str(tool_python)},
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(report.read_text(encoding="utf-8"))
    recorded = payload["tools"]["ruff_command"]
    assert payload["tools"]["ruff"] == "ruff 8.8.8"
    assert recorded["selection"] == "TOOL_PYTHON_BIN"
    assert recorded["argv"][:4] == [str(tool_python.resolve()), "-B", "-m", "ruff"]


def test_quality_gate_keeps_python_bin_ruff_fallback_compatible(tmp_path: Path) -> None:
    project = _make_quality_gate_project(tmp_path)
    app_python = tmp_path / "app-python"
    _write(
        app_python,
        f"#!{sys.executable}\n"
        "import os\n"
        "import sys\n"
        "if sys.argv[1:4] == ['-B', '-m', 'ruff']:\n"
        "    if sys.argv[4:5] == ['--version']:\n"
        "        print('ruff 7.7.7')\n"
        "    raise SystemExit(0)\n"
        f"os.execv({sys.executable!r}, [{sys.executable!r}, *sys.argv[1:]])\n",
    )
    app_python.chmod(0o755)
    report = tmp_path / "quality.json"

    result = _run_fixture_quality_gate(
        project,
        report,
        extra_env={
            "PYTHON_BIN": str(app_python),
            "RUFF_BIN": "",
            "TOOL_PYTHON_BIN": "",
        },
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(report.read_text(encoding="utf-8"))
    recorded = payload["tools"]["ruff_command"]
    assert payload["tools"]["ruff"] == "ruff 7.7.7"
    assert recorded["selection"] == "PYTHON_BIN"
    assert recorded["argv"][:4] == [str(app_python.resolve()), "-B", "-m", "ruff"]


def test_quality_gate_fails_closed_when_selected_ruff_is_missing(tmp_path: Path) -> None:
    project = _make_quality_gate_project(tmp_path)
    missing = tmp_path / "missing-ruff"
    report = tmp_path / "quality.json"

    result = _run_fixture_quality_gate(
        project,
        report,
        extra_env={"RUFF_BIN": str(missing), "TOOL_PYTHON_BIN": ""},
    )

    assert result.returncode != 0
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["status"] == "failed"
    assert payload["tools"]["ruff"] == "unavailable"
    assert payload["tools"]["ruff_command"]["argv"][0] == str(missing)
    steps = {step["name"]: step for step in payload["steps"]}
    assert steps["ruff_tool"]["status"] == "failed"
    assert steps["release_lint"]["status"] == "failed"


def test_quality_gate_runs_lint_and_feature_contracts_before_frontend_ratchets() -> None:
    source = (DEPLOY_DIR / "run_quality_gate.sh").read_text(encoding="utf-8")

    lint = "run_step frontend_lint npm run lint"
    contracts = "run_step frontend_contracts npm test"
    ratchet = (
        'run_step frontend_ratchet node deploy/check_frontend_ratchet.mjs '
        '--output "$ratchet_json"'
    )
    assert lint in source
    assert contracts in source
    assert ratchet in source
    assert source.index(lint) < source.index(contracts) < source.index(ratchet)
    assert "printf 'frontend_lint\\t0\\t0\\n'" in source
    assert "printf 'frontend_contracts\\t0\\t0\\n'" in source


def test_quality_gate_runs_database_consumer_inventory_before_secret_scan() -> None:
    source = (DEPLOY_DIR / "run_quality_gate.sh").read_text(encoding="utf-8")

    inventory = (
        'run_step database_consumers "$PYTHON_BIN" -B '
        "scripts/ci/check_database_consumers.py"
    )
    secret_scan = (
        'run_step source_secrets "$PYTHON_BIN" -B deploy/release_tool.py source-secret-scan'
    )
    assert inventory in source
    assert secret_scan in source
    assert source.index(inventory) < source.index(secret_scan)
    assert "scripts/ci/check_database_consumers.py" in source
    assert "backend/tests/test_database_consumer_inventory.py" in source


def test_quality_gate_validates_external_content_policy_without_requiring_local_artifact() -> None:
    source = (DEPLOY_DIR / "run_quality_gate.sh").read_text(encoding="utf-8")

    assert "deploy/release_tool.py content-bundle-policy" in source
    assert "deploy/release_tool.py content-bundles" not in source


def test_quality_gate_validates_feature_registry_before_secret_scan() -> None:
    source = (DEPLOY_DIR / "run_quality_gate.sh").read_text(encoding="utf-8")

    registry = (
        'run_step feature_registry "$PYTHON_BIN" -B scripts/ci/check_feature_registry.py'
    )
    secret_scan = (
        'run_step source_secrets "$PYTHON_BIN" -B deploy/release_tool.py source-secret-scan'
    )
    assert registry in source
    assert source.index(registry) < source.index(secret_scan)
    assert "scripts/ci/check_feature_registry.py" in source
    assert "backend/tests/test_feature_registry.py" in source


def _production_quality_fixture() -> dict:
    return _complete_quality_payload(
        {"sha256": "a" * 64, "file_count": 10, "total_bytes": 100}
    )


def test_production_quality_attestation_accepts_complete_evidence() -> None:
    payload = _production_quality_fixture()

    verify_quality_gate(
        payload,
        production=True,
        expected_source_snapshot=payload["source_snapshot"],
    )


@pytest.mark.parametrize("missing", ("scope", "tests", "ratchets", "steps"))
def test_production_quality_attestation_rejects_missing_sections(missing: str) -> None:
    payload = _production_quality_fixture()
    del payload[missing]

    with pytest.raises(ReleaseError, match="quality gate"):
        verify_quality_gate(payload, production=True)


@pytest.mark.parametrize("field", ("python_tests_skipped", "frontend_skipped"))
def test_production_quality_attestation_rejects_skipped_scope(field: str) -> None:
    payload = _production_quality_fixture()
    payload["scope"][field] = True

    with pytest.raises(ReleaseError, match="cannot skip"):
        verify_quality_gate(payload, production=True)


def test_production_quality_attestation_rejects_forged_passing_summary() -> None:
    payload = _production_quality_fixture()
    payload["tests"]["failures"] = 1

    with pytest.raises(ReleaseError, match="tests.failures must be zero"):
        verify_quality_gate(payload, production=True)


def test_allow_unverified_cannot_bypass_production_quality_attestation() -> None:
    payload = _production_quality_fixture()
    payload["status"] = "partial"

    with pytest.raises(ReleaseError, match="require a passed quality gate"):
        verify_quality_gate(payload, production=True, allow_unverified=True)


def test_production_quality_attestation_rejects_failed_or_missing_steps() -> None:
    failed = _production_quality_fixture()
    failed["steps"][0]["status"] = "failed"
    failed["steps"][0]["exit_code"] = 1
    with pytest.raises(ReleaseError, match="step did not pass"):
        verify_quality_gate(failed, production=True)

    missing = _production_quality_fixture()
    missing["steps"] = [
        step for step in missing["steps"] if step["name"] != "database_consumers"
    ]
    with pytest.raises(ReleaseError, match="database_consumers"):
        verify_quality_gate(missing, production=True)

    duplicate = _production_quality_fixture()
    duplicate["steps"].append(dict(duplicate["steps"][0]))
    with pytest.raises(ReleaseError, match="required steps"):
        verify_quality_gate(duplicate, production=True)


@pytest.mark.parametrize(
    ("section", "actual_field", "maximum_field"),
    (
        ("financial_typescript", "actual_errors", "maximum_errors"),
        ("vue_eslint", "errors", "errors"),
    ),
)
def test_production_quality_attestation_rejects_missing_or_exceeded_ratchets(
    section: str, actual_field: str, maximum_field: str
) -> None:
    missing = _production_quality_fixture()
    ratchet = missing["ratchets"][section]
    if section == "vue_eslint":
        del ratchet["actual"][actual_field]
    else:
        del ratchet[actual_field]
    with pytest.raises(ReleaseError, match="non-negative integer"):
        verify_quality_gate(missing, production=True)

    exceeded = _production_quality_fixture()
    ratchet = exceeded["ratchets"][section]
    if section == "vue_eslint":
        ratchet["actual"][actual_field] = ratchet["maximum"][maximum_field] + 1
    else:
        ratchet[actual_field] = ratchet[maximum_field] + 1
    with pytest.raises(ReleaseError, match="exceed"):
        verify_quality_gate(exceeded, production=True)


def test_historical_schema_v3_quality_policy_is_verify_only() -> None:
    payload = _production_quality_fixture()
    historical_steps = {
        "config",
        "ruff_tool",
        "release_lint",
        "import_boundaries",
        "runtime_config",
        "source_secrets",
        "pytest",
        "frontend_ratchet",
        "source_stability",
    }
    payload["steps"] = [
        step for step in payload["steps"] if step["name"] in historical_steps
    ]

    with pytest.raises(ReleaseError, match="required steps"):
        verify_quality_gate(payload, production=True)
    verify_quality_gate(
        payload,
        production=True,
        historical_release_version="0.9.3",
    )


def test_v010_quality_policy_does_not_require_future_feature_registry_step() -> None:
    payload = _production_quality_fixture()
    payload["steps"] = [
        step
        for step in payload["steps"]
        if step["name"] not in {"content_bundles", "feature_registry"}
    ]

    with pytest.raises(ReleaseError, match="feature_registry"):
        verify_quality_gate(payload, production=True)
    verify_quality_gate(
        payload,
        production=True,
        historical_release_version="0.10.0",
    )


def test_staged_source_snapshot_excludes_generated_and_secret_files(tmp_path: Path) -> None:
    project = tmp_path / "project"
    _write(project / "VERSION", "0.9.2\n")
    _write(project / "backend/api/application.py", "APP = True\n")
    _write(project / "backend/api/.env", "SECRET=do-not-copy\n")
    _write(project / "backend/api/application.py.bak", "stale backup\n")
    _write(project / "backend/api/application.py.orig", "stale merge backup\n")
    _write(project / "backend/api/application.py.rej", "stale rejected patch\n")
    _write(project / "backend/api/.#application.py", "editor lock\n")
    _write(project / "backend/api/application.py~", "editor backup\n")
    _write(project / "backend/api/.DS_Store", "desktop metadata\n")
    _write(project / "frontend/vue_project/src/main.js", "export default 1\n")
    _write(project / "frontend/shared/displayPreferences.js", "export default 1\n")
    _write(project / "frontend/vue_project/node_modules/pkg/index.js", "generated\n")
    _write(project / "frontend/vue_project/public/fin-terminal/index.html", "stale\n")
    _write(
        project / "frontend/vue_project/public/datasets/expert-skills/catalog.json",
        "external content\n",
    )
    _write(
        project / "frontend/vue_project/public/imgs/hermes-generated/sample.png",
        "generated image\n",
    )
    _write(project / "deploy/create_release.sh", "#!/bin/bash\n")
    _write(
        project / "docs/operations/RUNTIME_SERVICE_CATALOG.md",
        "# Runtime service catalog\n",
    )
    _write(project / "requirements/roles/web.in", "fixture==1.0\n")
    _write(
        project / "requirements/roles/web.lock",
        f"fixture==1.0 --hash=sha256:{'1' * 64}\n",
    )

    before = digest_inputs(project)
    staged = tmp_path / "staged"
    copied = copy_inputs(project, staged)

    assert copied == before
    assert (staged / "backend/api/application.py").is_file()
    assert (staged / "requirements/roles/web.in").is_file()
    assert (staged / "requirements/roles/web.lock").is_file()
    assert (staged / "docs/operations/RUNTIME_SERVICE_CATALOG.md").is_file()
    assert (staged / "frontend/shared/displayPreferences.js").is_file()
    assert not (staged / "backend/api/.env").exists()
    assert not (staged / "backend/api/application.py.bak").exists()
    assert not (staged / "backend/api/application.py.orig").exists()
    assert not (staged / "backend/api/application.py.rej").exists()
    assert not (staged / "backend/api/.#application.py").exists()
    assert not (staged / "backend/api/application.py~").exists()
    assert not (staged / "backend/api/.DS_Store").exists()
    assert not (staged / "frontend/vue_project/node_modules").exists()
    assert not (staged / "frontend/vue_project/public/fin-terminal").exists()
    assert not (staged / "frontend/vue_project/public/datasets/expert-skills").exists()
    assert not (staged / "frontend/vue_project/public/imgs/hermes-generated").exists()


def test_source_input_path_scope_excludes_generated_and_unrelated_paths() -> None:
    assert is_source_input_path("backend/api/application.py")
    assert is_source_input_path("frontend/vue_project/src/main.js")
    assert is_source_input_path("frontend/shared/displayPreferences.js")
    assert is_source_input_path("docs/operations/RUNTIME_SERVICE_CATALOG.md")
    assert not is_source_input_path("backend/api/application.py.bak")
    assert not is_source_input_path("backend/api/.#application.py")
    assert not is_source_input_path("backend/api/application.py~")
    assert not is_source_input_path("frontend/vue_project/node_modules/pkg/index.js")
    assert not is_source_input_path("frontend/vue_project/public/fin-terminal/index.html")
    assert not is_source_input_path("data/runtime/checkpoint.json")
    assert not is_source_input_path("../VERSION")


def test_v1_runtime_closure_contains_only_read_only_catalog_dependencies() -> None:
    assert required_runtime_files("0.11.0") == REQUIRED_RUNTIME_FILES
    assert required_runtime_files("1.0.0") == V1_REQUIRED_RUNTIME_FILES
    assert "ops/runtime/services.json" in V1_REQUIRED_RUNTIME_FILES
    assert "scripts/runtime_control/catalog.py" in V1_REQUIRED_RUNTIME_FILES
    assert set(RUNTIME_CATALOG_ARTIFACT_INPUTS) < set(V1_REQUIRED_RUNTIME_FILES)
    assert "scripts/runtime_control/lifecycle.py" not in V1_REQUIRED_RUNTIME_FILES
    assert "scripts/runtime_control/cli.py" not in V1_REQUIRED_RUNTIME_FILES
    assert "scripts/globemind_runtime.py" not in V1_REQUIRED_RUNTIME_FILES


def test_release_backend_copy_includes_catalog_without_lifecycle_control(tmp_path: Path) -> None:
    staged = tmp_path / "staged"
    release = tmp_path / "release"
    _write(staged / "VERSION", "1.0.0\n")
    for relative in V1_REQUIRED_RUNTIME_FILES:
        source = PROJECT_ROOT / relative
        assert source.is_file(), relative
        (staged / relative).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, staged / relative)
    _write(staged / "scripts/runtime_control/lifecycle.py", "raise RuntimeError('not runtime')\n")
    _write(staged / "scripts/runtime_control/cli.py", "raise RuntimeError('not runtime')\n")
    _write(staged / "scripts/globemind_runtime.py", "raise RuntimeError('not runtime')\n")

    copy_release_backend(staged, release)

    assert all((release / relative).is_file() for relative in V1_REQUIRED_RUNTIME_FILES)
    for relative in RUNTIME_CATALOG_ARTIFACT_INPUTS:
        assert sha256_file(release / relative) == sha256_file(staged / relative)
        assert stat.S_IMODE((release / relative).stat().st_mode) & 0o111 == (
            stat.S_IMODE((staged / relative).stat().st_mode) & 0o111
        )
    assert not (release / "scripts/runtime_control/lifecycle.py").exists()
    assert not (release / "scripts/runtime_control/cli.py").exists()
    assert not (release / "scripts/globemind_runtime.py").exists()


def test_runtime_catalog_artifact_allowlist_exactly_matches_manifest_refs() -> None:
    assert runtime_catalog_artifact_references(PROJECT_ROOT) == tuple(
        sorted(RUNTIME_CATALOG_ARTIFACT_INPUTS)
    )
    assert all(is_source_input_path(path) for path in RUNTIME_CATALOG_ARTIFACT_INPUTS)


def test_release_backend_copy_rejects_missing_catalog_artifact(tmp_path: Path) -> None:
    staged = tmp_path / "staged"
    release = tmp_path / "release"
    _write(staged / "VERSION", "1.0.0\n")
    shutil.copytree(PROJECT_ROOT / "ops/runtime", staged / "ops/runtime")
    for relative in RUNTIME_CATALOG_ARTIFACT_INPUTS:
        if relative == "deploy/start_web_prod.sh":
            continue
        source = PROJECT_ROOT / relative
        target = staged / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)

    with pytest.raises(ReleaseError, match="runtime catalog artifact is unavailable"):
        copy_release_backend(staged, release)


def test_legacy_release_backend_does_not_require_or_copy_v1_catalog_artifacts(
    tmp_path: Path,
) -> None:
    staged = tmp_path / "staged"
    release = tmp_path / "release"
    _write(staged / "VERSION", "0.11.0\n")
    _write(staged / "backend/api/application.py", "APP = True\n")
    _write(staged / "deploy/start_web_prod.sh", "#!/bin/sh\n")

    copy_release_backend(staged, release)

    assert (release / "backend/api/application.py").is_file()
    assert not (release / "deploy/start_web_prod.sh").exists()


@pytest.mark.parametrize(
    "controller_path",
    (
        "${DATA_ROOT}/deploy/start_web_prod.sh",
        "${PROJECT_ROOT}/../deploy/start_web_prod.sh",
        "${PROJECT_ROOT}/deploy/./start_web_prod.sh",
        "${PROJECT_ROOT}/docs/start_web_prod.sh",
        "${PROJECT_ROOT}/deploy/not-reviewed.sh",
        "${PROJECT_ROOT}/deploy\\start_web_prod.sh",
    ),
)
def test_runtime_catalog_artifact_reference_parser_rejects_unreviewed_paths(
    tmp_path: Path,
    controller_path: str,
) -> None:
    project = tmp_path / "project"
    payload = json.loads(
        (PROJECT_ROOT / "ops/runtime/services.json").read_text(encoding="utf-8")
    )
    payload["services"][0]["controller"]["path"] = controller_path
    _write(project / "ops/runtime/services.json", json.dumps(payload) + "\n")

    with pytest.raises(ReleaseError, match="runtime catalog"):
        runtime_catalog_artifact_references(project, require_files=False)


def test_runtime_catalog_artifact_copies_reject_root_source_drift(tmp_path: Path) -> None:
    release = tmp_path / "release"
    source_bundle = release / "build-metadata/source"
    for root in (release, source_bundle):
        shutil.copytree(PROJECT_ROOT / "ops/runtime", root / "ops/runtime")
        for relative in RUNTIME_CATALOG_ARTIFACT_INPUTS:
            source = PROJECT_ROOT / relative
            target = root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)

    verify_runtime_catalog_artifact_copies(release, source_bundle)
    manifest = release / "ops/runtime/services.json"
    archived_manifest = source_bundle / "ops/runtime/services.json"
    manifest_payload = json.loads(manifest.read_text(encoding="utf-8"))
    manifest_payload["services"][0]["owner"] = "tampered-owner"
    _write(manifest, json.dumps(manifest_payload) + "\n")
    with pytest.raises(ReleaseError, match="differs from archived source"):
        verify_runtime_catalog_artifact_copies(release, source_bundle)
    shutil.copy2(archived_manifest, manifest)

    runbook = release / "docs/operations/RUNTIME_SERVICE_CATALOG.md"
    archived_runbook = source_bundle / "docs/operations/RUNTIME_SERVICE_CATALOG.md"
    runbook.chmod(runbook.stat().st_mode | stat.S_IXUSR)
    with pytest.raises(ReleaseError, match="executable mode differs"):
        verify_runtime_catalog_artifact_copies(release, source_bundle)
    runbook.chmod(archived_runbook.stat().st_mode)

    _write(release / "deploy/start_web_prod.sh", "#!/bin/sh\nexit 99\n")
    with pytest.raises(ReleaseError, match="differs from archived source"):
        verify_runtime_catalog_artifact_copies(release, source_bundle)


def test_release_root_default_runtime_catalog_projection_is_current(tmp_path: Path) -> None:
    staged = tmp_path / "staged"
    release = tmp_path / "1.0.0-fixture"
    copy_inputs(PROJECT_ROOT, staged)
    assert (staged / "docs/operations/RUNTIME_SERVICE_CATALOG.md").is_file()
    copy_release_backend(staged, release)
    script = """
import importlib
import json
from pathlib import Path

module = importlib.import_module("api.features.operations.runtime_catalog")
catalog_module = importlib.import_module("scripts.runtime_control.catalog")
manifest_module = importlib.import_module("scripts.runtime_control.manifest")
constants_module = importlib.import_module("scripts.runtime_control.constants")
redaction_module = importlib.import_module("scripts.runtime_control.redaction")

release = Path.cwd().resolve()
for imported in (
    module,
    catalog_module,
    manifest_module,
    constants_module,
    redaction_module,
):
    if not Path(imported.__file__).resolve().is_relative_to(release):
        raise SystemExit("runtime catalog module escaped release")
payload = module.load_runtime_catalog()
services = payload.get("services") or []
application_module = importlib.import_module("api.application")
auth_module = importlib.import_module("api.services.auth")
testclient_module = importlib.import_module("fastapi.testclient")
if not Path(application_module.__file__).resolve().is_relative_to(release):
    raise SystemExit("application module escaped release")
application_module.app.dependency_overrides[auth_module.get_current_user_required] = lambda: {
    "user_id": 1,
    "username": "release-catalog-test",
}
with testclient_module.TestClient(application_module.app) as client:
    response = client.get("/api/ops/runtime-catalog")
route_payload = response.json()
result = {
    "source_project_root": str(module.SOURCE_PROJECT_ROOT),
    "service_count": len(services),
    "catalog_current": payload.get("summary", {}).get("catalog_current"),
    "catalog_drifted": payload.get("summary", {}).get("catalog_drifted"),
    "all_current": all(
        item.get("catalog_status") == "current" and item.get("catalog_drift") == []
        for item in services
    ),
    "service_ids": sorted(item.get("id") for item in services),
    "all_not_authorized": all(
        item.get("lifecycle_authorization", {}).get("state") == "not-authorized"
        and item.get("lifecycle_authorization", {}).get("authorized_operations") == []
        for item in services
    ),
    "lifecycle_authorized": payload.get("summary", {}).get("lifecycle_authorized"),
    "takeover_ready": payload.get("summary", {}).get("takeover_ready"),
    "control_enabled": payload.get("control", {}).get("enabled"),
    "read_only": payload.get("read_only"),
    "process_inspection": payload.get("process_inspection"),
    "route_status": response.status_code,
    "route_service_count": len(route_payload.get("services") or []),
    "route_catalog_current": route_payload.get("summary", {}).get("catalog_current"),
    "route_catalog_drifted": route_payload.get("summary", {}).get("catalog_drifted"),
}
print(json.dumps(result, sort_keys=True))
"""
    result = subprocess.run(
        [sys.executable, "-B", "-c", script],
        cwd=release,
        env={
            **os.environ,
            "APP_ENV": "testing",
            "DB_USER": "test",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONNOUSERSITE": "1",
            "PYTHONPATH": os.pathsep.join((str(release / "backend"), str(release))),
        },
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(result.stdout.splitlines()[-1])
    assert payload == {
        "all_current": True,
        "all_not_authorized": True,
        "catalog_current": 12,
        "catalog_drifted": 0,
        "control_enabled": False,
        "lifecycle_authorized": 0,
        "process_inspection": False,
        "read_only": True,
        "route_catalog_current": 12,
        "route_catalog_drifted": 0,
        "route_service_count": 12,
        "route_status": 200,
        "service_count": 12,
        "service_ids": [
            "daily_ingest",
            "ground_images",
            "ground_refresh",
            "l1_extract",
            "l1_prep",
            "proxy_pool",
            "quality_labels",
            "tunnel",
            "vllm",
            "wave1_extractor",
            "wave1_loader",
            "web",
        ],
        "source_project_root": str(release.resolve()),
        "takeover_ready": 0,
    }

    (release / "docs/operations/RUNTIME_SERVICE_CATALOG.md").unlink()
    negative_script = """
import importlib
import json
from pathlib import Path

modules = [
    importlib.import_module("api.features.operations.runtime_catalog"),
    importlib.import_module("scripts.runtime_control.catalog"),
    importlib.import_module("scripts.runtime_control.manifest"),
    importlib.import_module("scripts.runtime_control.constants"),
    importlib.import_module("scripts.runtime_control.redaction"),
]
release = Path.cwd().resolve()
if any(not Path(module.__file__).resolve().is_relative_to(release) for module in modules):
    raise SystemExit("runtime catalog module escaped release")
payload = modules[0].load_runtime_catalog()
services = payload.get("services") or []
runbook_unavailable = all(
    any(
        item.get("role") == "runbook" and item.get("code") == "artifact-unavailable"
        for item in service.get("catalog_drift", [])
    )
    for service in services
)
print(json.dumps({
    "catalog_current": payload.get("summary", {}).get("catalog_current"),
    "catalog_drifted": payload.get("summary", {}).get("catalog_drifted"),
    "runbook_unavailable": runbook_unavailable,
    "service_count": len(services),
}, sort_keys=True))
"""
    negative = subprocess.run(
        [sys.executable, "-B", "-c", negative_script],
        cwd=release,
        env={
            **os.environ,
            "APP_ENV": "testing",
            "DB_USER": "test",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONNOUSERSITE": "1",
            "PYTHONPATH": os.pathsep.join((str(release / "backend"), str(release))),
        },
        check=True,
        capture_output=True,
        text=True,
    )
    assert json.loads(negative.stdout) == {
        "catalog_current": 0,
        "catalog_drifted": 12,
        "runbook_unavailable": True,
        "service_count": 12,
    }


def test_release_provenance_ignores_changes_outside_release_scope(tmp_path: Path) -> None:
    project = _init_release_git_project(tmp_path)
    _write(project / "data/runtime/checkpoint.json", "changing runtime evidence\n")
    _write(project / "frontend/vue_project/public/fin-terminal/index.html", "generated\n")

    payload = _release_provenance(project)

    assert payload["scope"] == "release_inputs"
    assert payload["dirty"] is False
    assert payload["git_status_entry_count"] == 0
    assert payload["untracked_or_ignored_input_count"] == 0


def test_release_provenance_detects_tracked_source_changes(tmp_path: Path) -> None:
    project = _init_release_git_project(tmp_path)
    _write(project / "backend/api/application.py", "APP = False\n")

    payload = _release_provenance(project)

    assert payload["dirty"] is True
    assert payload["git_status_entry_count"] == 1
    assert payload["git_status_entry_sample"] == ["backend/api/application.py"]
    assert payload["untracked_or_ignored_input_count"] == 0


def test_release_provenance_detects_ignored_but_included_inputs(tmp_path: Path) -> None:
    project = _init_release_git_project(tmp_path)
    _write(project / "backend/cppt/cc_bridge.py", "# required ignored runtime source\n")

    payload = _release_provenance(project)

    assert payload["dirty"] is True
    assert payload["git_status_entry_count"] == 0
    assert payload["untracked_or_ignored_input_count"] == 1
    assert payload["untracked_or_ignored_input_sample"] == ["backend/cppt/cc_bridge.py"]


def _make_content_bundle_project(tmp_path: Path) -> tuple[Path, dict]:
    project = tmp_path / "content-project"
    _write(project / "VERSION", "1.0.0\n")
    source = project / "frontend/vue_project/public/datasets/expert-skills"
    _write(source / "catalog.json", '{"skills": []}\n')
    _write(source / "selection-report.json", '{"selected": 0}\n')
    _write(source / "sources/selection-policy.md", "# Selection policy\n")
    _write(source / "a-first/nested-skill.md", "# Nested skill\n")
    digest = digest_content_bundle_source(source).as_dict()
    bundle = {
        "id": "expert-skills",
        "version": "fixture-v1",
        "source_path": "frontend/vue_project/public/datasets/expert-skills",
        "stage_path": "frontend/vue_project/public/datasets/expert-skills",
        "artifact_path": "datasets/expert-skills",
        **digest,
        "evidence": [
            "catalog.json",
            "selection-report.json",
            "sources/selection-policy.md",
        ],
    }
    _write(
        project / "ops/release/content-bundles.json",
        json.dumps({"schema_version": 1, "bundles": [bundle]}) + "\n",
    )
    return project, bundle


def test_content_bundle_is_excluded_from_source_and_attested_into_frontend(
    tmp_path: Path,
) -> None:
    project, bundle = _make_content_bundle_project(tmp_path)
    staged = tmp_path / "staged"
    copy_inputs(project, staged)
    assert not (staged / bundle["stage_path"]).exists()

    records = stage_content_bundles(project, staged)
    frontend_dist = tmp_path / "frontend-dist"
    shutil.copytree(
        staged / bundle["stage_path"],
        frontend_dist / bundle["artifact_path"],
    )

    assert records == [bundle]
    assert verify_content_bundles(project) == [bundle]
    assert verify_staged_content_bundles(staged, frontend_dist, records) == [bundle]

    release = tmp_path / "release"
    shutil.copytree(frontend_dist, release / "frontend-dist")
    verify_release_content_bundles(release, staged, records, required=True)


def test_release_content_bundle_rejects_excluded_artifact_injection(tmp_path: Path) -> None:
    project, bundle = _make_content_bundle_project(tmp_path)
    staged = tmp_path / "staged"
    copy_inputs(project, staged)
    records = stage_content_bundles(project, staged)
    release = tmp_path / "release"
    artifact = release / "frontend-dist" / bundle["artifact_path"]
    shutil.copytree(staged / bundle["stage_path"], artifact)
    _write(artifact / ".env", "FORBIDDEN=artifact-injection\n")

    with pytest.raises(ReleaseError, match="excluded file"):
        verify_release_content_bundles(release, staged, records, required=True)


def test_content_bundle_digest_drift_fails_closed(tmp_path: Path) -> None:
    project, _bundle = _make_content_bundle_project(tmp_path)
    _write(
        project / "frontend/vue_project/public/datasets/expert-skills/catalog.json",
        '{"skills": ["drift"]}\n',
    )

    with pytest.raises(ReleaseError, match="content bundle digest mismatch"):
        verify_content_bundles(project)


def test_source_snapshot_preserves_executable_intent_after_write_bits_removed(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    script = project / "deploy/create_release.sh"
    _write(project / "VERSION", "0.9.2\n")
    _write(script, "#!/bin/sh\n")
    script.chmod(0o754)
    before = digest_inputs(project)

    staged = tmp_path / "staged"
    copy_inputs(project, staged)
    (staged / "deploy/create_release.sh").chmod(0o554)

    assert digest_inputs(staged) == before


def _make_release(tmp_path: Path, *, index_html: str | None = None) -> Path:
    release = tmp_path / "0.9.2-test"
    _write(release / "VERSION", "0.9.2\n")
    for runtime_file in REQUIRED_RUNTIME_FILES:
        _write(release / runtime_file, f"# runtime fixture: {runtime_file}\n")
    _write(
        release / "frontend-dist/index.html",
        index_html
        or '<link rel="stylesheet" href="/assets/app.css"><script src="/assets/app.js"></script>',
    )
    _write(release / "frontend-dist/assets/app.css", "body{}\n")
    _write(release / "frontend-dist/assets/app.js", "console.log('ok')\n")
    _write(
        release / "frontend-dist/fin-terminal/index.html",
        '<script src="/fin-terminal/assets/terminal.js"></script>',
    )
    _write(release / "frontend-dist/fin-terminal/assets/terminal.js", "console.log('ok')\n")
    source_bundle = release / "build-metadata/source"
    _write(source_bundle / "VERSION", "0.9.2\n")
    for runtime_file in REQUIRED_RUNTIME_FILES:
        _write(source_bundle / runtime_file, f"# source fixture: {runtime_file}\n")
    role_input_content = "fixture-package==1.0\n"
    role_lock_content = f"fixture-package==1.0 \\\n    --hash=sha256:{'1' * 64}\n"
    _write(source_bundle / "requirements/roles/web.in", role_input_content)
    _write(source_bundle / "requirements/roles/web.lock", role_lock_content)
    source_snapshot = digest_inputs(source_bundle).as_dict()
    quality_path = release / "build-metadata/quality-gate.json"
    _write(
        quality_path,
        json.dumps(_complete_quality_payload(source_snapshot)) + "\n",
    )
    lock_records = []
    for lock_name in LOCK_FILES:
        lock_path = release / "build-metadata/lockfiles" / lock_name
        _write(
            lock_path,
            role_lock_content
            if lock_name == "requirements/roles/web.lock"
            else '{"lockfileVersion": 3}\n',
        )
        lock_records.append(
            {
                "path": lock_name,
                "artifact_path": lock_path.relative_to(release).as_posix(),
                "sha256": sha256_file(lock_path),
            }
        )
    dependency_records = []
    for dependency_name in DEPENDENCY_MANIFEST_FILES:
        dependency_path = release / "build-metadata/dependency-manifests" / dependency_name
        _write(
            dependency_path,
            role_input_content
            if dependency_name == "requirements/roles/web.in"
            else "dependency==1.0\n",
        )
        dependency_records.append(
            {
                "path": dependency_name,
                "artifact_path": dependency_path.relative_to(release).as_posix(),
                "sha256": sha256_file(dependency_path),
            }
        )

    runtime_root = release / "build-metadata/python-runtime"
    freeze_path = runtime_root / "pip-freeze.txt"
    pip_check_path = runtime_root / "pip-check.txt"
    import_closure_path = runtime_root / "import-closure.json"
    tests_path = runtime_root / "pytest-web.log"
    _write(freeze_path, "fixture-package==1.0\n")
    _write(pip_check_path, "No broken requirements found.\n")
    closure_payload = {
        "schema_version": 1,
        "python": platform.python_version(),
        "critical_imports": ["api.application", "serve_prod"],
        "loaded_module_count": 2,
        "distributions": {"fixture-package": "1.0"},
    }
    closure_payload["closure_sha256"] = hashlib.sha256(
        json.dumps(closure_payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    _write(import_closure_path, json.dumps(closure_payload, sort_keys=True) + "\n")
    _write(tests_path, "1 passed in 0.01s\n")
    fingerprints = {
        "build_input_fingerprint": "b" * 64,
        "pip_freeze_sha256": sha256_file(freeze_path),
        "import_closure_sha256": sha256_file(import_closure_path),
        "pytest_log_sha256": sha256_file(tests_path),
    }
    runtime_fingerprint = hashlib.sha256(
        json.dumps(fingerprints, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    python_metadata = {
        "version": platform.python_version(),
        "implementation": platform.python_implementation(),
        "platform": sysconfig.get_platform(),
        "machine": platform.machine(),
        "soabi": sysconfig.get_config_var("SOABI"),
    }
    validation = {
        "pip_check": "pass",
        "critical_imports": "pass",
        "pytest_web": "pass",
    }
    runtime_payload = {
        "schema_version": 1,
        "role": "web",
        "version": "0.9.2",
        "install_prefix": "/root/data/python-runtimes/globemind-web/0.9.2",
        **fingerprints,
        "runtime_fingerprint": runtime_fingerprint,
        "python": python_metadata,
        "lock_sha256": sha256_file(
            release / "build-metadata/lockfiles/requirements/roles/web.lock"
        ),
        "validation": validation,
    }
    runtime_manifest_path = runtime_root / "runtime.json"
    _write(runtime_manifest_path, json.dumps(runtime_payload, sort_keys=True) + "\n")
    evidence = {
        "pip_freeze": freeze_path,
        "pip_check": pip_check_path,
        "import_closure": import_closure_path,
        "tests": tests_path,
    }
    runtime_attestation = {
        "role": "web",
        "version": "0.9.2",
        "role_input": {
            "path": "requirements/roles/web.in",
            "sha256": sha256_file(source_bundle / "requirements/roles/web.in"),
        },
        "lock": {
            "path": "requirements/roles/web.lock",
            "sha256": runtime_payload["lock_sha256"],
        },
        "runtime_manifest": {
            "schema_version": 1,
            "artifact_path": "build-metadata/python-runtime/runtime.json",
            "sha256": sha256_file(runtime_manifest_path),
        },
        "build_input_fingerprint": fingerprints["build_input_fingerprint"],
        "runtime_fingerprint": runtime_fingerprint,
        "python": {
            **python_metadata,
            "executable_sha256": "e" * 64,
            "pip_freeze_sha256": fingerprints["pip_freeze_sha256"],
        },
        "evidence": {
            name: {
                "artifact_path": path.relative_to(release).as_posix(),
                "sha256": sha256_file(path),
                "status": "passed",
            }
            for name, path in evidence.items()
        },
        "validation": validation,
    }

    artifact = write_checksums(release)
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "version": "0.9.2",
        "build_id": "0.9.2-test",
        "git_sha": "a" * 40,
        "source_dirty": False,
        "backend_entry": "backend/serve_prod.py",
        "frontend_dist": "frontend-dist",
        "source": {
            "snapshot": source_snapshot,
            "dirty": False,
            "dirty_override": False,
            "bundle_path": "build-metadata/source",
            "bundle_snapshot": source_snapshot,
            "provenance": {"dirty": False, "head": "a" * 40},
        },
        "dependency_locks": lock_records,
        "dependency_manifests": dependency_records,
        "python_runtime": runtime_attestation,
        "build": {
            "source_unchanged": True,
            "staged_source_unchanged": True,
            "frontend": {"dependency_mode": "ci"},
        },
        "quality_gate": {
            "status": "passed",
            "artifact_path": quality_path.relative_to(release).as_posix(),
            "sha256": sha256_file(quality_path),
        },
        "artifact": {
            "manifest": "SHA256SUMS",
            "manifest_sha256": artifact.sha256,
            "file_count": artifact.file_count,
            "total_bytes": artifact.total_bytes,
        },
    }
    _write(release / "release.json", json.dumps(manifest) + "\n")
    for path in sorted(release.rglob("*"), reverse=True):
        path.chmod(0o555 if path.is_dir() else 0o444)
    release.chmod(0o555)
    return release


def _make_mutable(release: Path) -> dict:
    release.chmod(0o755)
    for path in release.rglob("*"):
        path.chmod(0o755 if path.is_dir() else 0o644)
    return json.loads((release / "release.json").read_text(encoding="utf-8"))


def _reseal_release(release: Path, manifest: dict) -> None:
    artifact = write_checksums(release)
    manifest["artifact"] = {
        "manifest": "SHA256SUMS",
        "manifest_sha256": artifact.sha256,
        "file_count": artifact.file_count,
        "total_bytes": artifact.total_bytes,
    }
    _write(release / "release.json", json.dumps(manifest) + "\n")
    for path in sorted(release.rglob("*"), reverse=True):
        path.chmod(0o555 if path.is_dir() else 0o444)
    release.chmod(0o555)


def test_release_verifier_recomputes_manifest_and_frontend_assets(tmp_path: Path) -> None:
    release = _make_release(tmp_path)

    manifest = verify_release(
        release,
        expected_version="0.9.2",
        expected_build_id="0.9.2-test",
        expected_git_sha="a" * 40,
        production=True,
    )

    assert manifest["artifact"]["file_count"] > 0


def test_release_verifier_rejects_tampered_artifact(tmp_path: Path) -> None:
    release = _make_release(tmp_path)
    artifact = release / "frontend-dist/assets/app.js"
    artifact.chmod(0o644)
    artifact.write_text("tampered\n", encoding="utf-8")
    artifact.chmod(0o444)

    with pytest.raises(ReleaseError, match="checksum mismatch"):
        verify_release(release, production=True)


def test_release_verifier_rejects_resealed_unhashed_web_lock(tmp_path: Path) -> None:
    release = _make_release(tmp_path)
    manifest = _make_mutable(release)
    replacement = "fixture-package==9.9\n"
    lock_artifact = release / "build-metadata/lockfiles/requirements/roles/web.lock"
    source_lock = release / "build-metadata/source/requirements/roles/web.lock"
    _write(lock_artifact, replacement)
    _write(source_lock, replacement)
    replacement_sha = sha256_file(lock_artifact)
    next(
        item
        for item in manifest["dependency_locks"]
        if item["path"] == "requirements/roles/web.lock"
    )["sha256"] = replacement_sha
    manifest["python_runtime"]["lock"]["sha256"] = replacement_sha
    runtime_manifest_path = release / "build-metadata/python-runtime/runtime.json"
    runtime_manifest = json.loads(runtime_manifest_path.read_text(encoding="utf-8"))
    runtime_manifest["lock_sha256"] = replacement_sha
    _write(runtime_manifest_path, json.dumps(runtime_manifest) + "\n")
    manifest["python_runtime"]["runtime_manifest"]["sha256"] = sha256_file(runtime_manifest_path)
    _reseal_release(release, manifest)

    with pytest.raises(ReleaseError, match="unhashed or unpinned"):
        verify_release(release, production=True)


def test_web_lock_accepts_hashed_https_direct_artifact(tmp_path: Path) -> None:
    lock = tmp_path / "web.lock"
    _write(
        lock,
        "torch @ https://download.example.test/torch-2.10.0-cp311.whl \\\n"
        f"    --hash=sha256:{'a' * 64}\n",
    )

    _verify_hashed_python_lock(lock)


@pytest.mark.parametrize(
    "requirement",
    (
        "torch @ http://download.example.test/torch.whl",
        "torch @ git+https://example.test/torch.git@main",
        "torch @ https://user:secret@example.test/torch.whl",
    ),
)
def test_web_lock_rejects_untrusted_direct_artifact(
    tmp_path: Path,
    requirement: str,
) -> None:
    lock = tmp_path / "web.lock"
    _write(lock, f"{requirement} --hash=sha256:{'a' * 64}\n")

    with pytest.raises(ReleaseError, match="unhashed or unpinned"):
        _verify_hashed_python_lock(lock)


def test_release_verifier_rejects_python_abi_mismatch(tmp_path: Path) -> None:
    release = _make_release(tmp_path)
    manifest_path = release / "release.json"
    manifest_path.chmod(0o644)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["python_runtime"]["python"]["soabi"] = "cpython-incompatible-abi"
    manifest_path.write_text(json.dumps(manifest) + "\n", encoding="utf-8")
    manifest_path.chmod(0o444)

    with pytest.raises(ReleaseError, match="ABI differs"):
        verify_release(release, production=True)


def test_release_verifier_rejects_schema_downgrade_without_legacy_gate(
    tmp_path: Path,
) -> None:
    release = _make_release(tmp_path)
    manifest_path = release / "release.json"
    manifest_path.chmod(0o644)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["schema_version"] = 2
    manifest_path.write_text(json.dumps(manifest) + "\n", encoding="utf-8")
    manifest_path.chmod(0o444)

    with pytest.raises(ReleaseError, match="explicit legacy rollback"):
        verify_release(release, production=True)


def test_schema_v2_requires_explicit_legacy_gate_and_remains_fully_verified(
    tmp_path: Path,
) -> None:
    release = _make_release(tmp_path)
    manifest = _make_mutable(release)
    shutil.rmtree(release / "build-metadata/python-runtime")
    for item in manifest["dependency_locks"]:
        (release / item["artifact_path"]).unlink()
    (release / "build-metadata/dependency-manifests/requirements/roles/web.in").unlink()
    manifest["schema_version"] = 2
    manifest.pop("python_runtime")
    manifest["dependency_locks"] = []
    for lock_name in LEGACY_LOCK_FILES:
        lock_path = release / "build-metadata/lockfiles" / lock_name
        _write(lock_path, '{"lockfileVersion": 3}\n')
        manifest["dependency_locks"].append(
            {
                "path": lock_name,
                "artifact_path": lock_path.relative_to(release).as_posix(),
                "sha256": sha256_file(lock_path),
            }
        )
    manifest["dependency_manifests"] = [
        item
        for item in manifest["dependency_manifests"]
        if item["path"] != "requirements/roles/web.in"
    ]
    _reseal_release(release, manifest)

    with pytest.raises(ReleaseError, match="explicit legacy rollback"):
        verify_release(release, production=True)

    verified = verify_release(release, production=True, allow_legacy=True)
    assert verified["schema_version"] == 2


def test_schema_v1_requires_legacy_gate_and_still_checks_full_artifact(
    tmp_path: Path,
) -> None:
    release = _make_release(tmp_path)
    _make_mutable(release)
    artifact = write_checksums(release)
    legacy_manifest = {
        "schema_version": 1,
        "version": "0.9.2",
        "build_id": "0.9.2-test",
        "git_sha": "a" * 40,
        "source_dirty": False,
        "backend_entry": "backend/serve_prod.py",
        "frontend_dist": "frontend-dist",
        "artifact_manifest_sha256": artifact.sha256,
    }
    _write(release / "release.json", json.dumps(legacy_manifest) + "\n")
    for path in sorted(release.rglob("*"), reverse=True):
        path.chmod(0o555 if path.is_dir() else 0o444)
    release.chmod(0o555)

    with pytest.raises(ReleaseError, match="legacy rollback"):
        verify_release(release, production=True)
    assert verify_release(release, production=True, allow_legacy=True)["schema_version"] == 1

    artifact_path = release / "frontend-dist/assets/app.js"
    artifact_path.chmod(0o644)
    artifact_path.write_text("tampered\n", encoding="utf-8")
    artifact_path.chmod(0o444)
    with pytest.raises(ReleaseError, match="checksum mismatch"):
        verify_release(release, production=True, allow_legacy=True)


def test_release_verifier_rejects_unknown_schema_even_with_legacy_gate(
    tmp_path: Path,
) -> None:
    release = _make_release(tmp_path)
    manifest_path = release / "release.json"
    manifest_path.chmod(0o644)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["schema_version"] = 99
    manifest_path.write_text(json.dumps(manifest) + "\n", encoding="utf-8")
    manifest_path.chmod(0o444)

    with pytest.raises(ReleaseError, match="unsupported release schema"):
        verify_release(release, production=True, allow_legacy=True)


def test_external_runtime_rejects_wrong_manifest_before_execution(tmp_path: Path) -> None:
    release = _make_release(tmp_path)
    runtime_root = tmp_path / "python-runtimes/globemind-web"
    runtime_dir = runtime_root / "0.9.2"
    shutil.copytree(
        release / "build-metadata/python-runtime",
        runtime_dir / "inventory",
    )
    runtime_manifest = runtime_dir / "inventory/runtime.json"
    payload = json.loads(runtime_manifest.read_text(encoding="utf-8"))
    payload["runtime_fingerprint"] = "f" * 64
    runtime_manifest.write_text(json.dumps(payload) + "\n", encoding="utf-8")

    with pytest.raises(ReleaseError, match="manifest differs"):
        verify_external_python_runtime(
            release,
            runtime_dir,
            allowed_runtime_root=runtime_root,
            production=True,
        )


def test_external_runtime_rejects_symlinked_manifest_before_execution(tmp_path: Path) -> None:
    release = _make_release(tmp_path)
    runtime_root = tmp_path / "python-runtimes/globemind-web"
    runtime_dir = runtime_root / "0.9.2"
    inventory = runtime_dir / "inventory"
    alternate_inventory = runtime_dir / "alternate-inventory"
    shutil.copytree(release / "build-metadata/python-runtime", alternate_inventory)
    inventory.mkdir(parents=True)
    (inventory / "runtime.json").symlink_to(
        alternate_inventory / "runtime.json"
    )

    with pytest.raises(ReleaseError, match="manifest must not be a symlink"):
        verify_external_python_runtime(
            release,
            runtime_dir,
            allowed_runtime_root=runtime_root,
            production=True,
        )


def test_external_runtime_rejects_shared_live_environment_path(tmp_path: Path) -> None:
    release = _make_release(tmp_path)
    shared_root = tmp_path / "opt/conda/envs"
    shared_runtime = shared_root / "Globemind_env"
    shared_runtime.mkdir(parents=True)

    with pytest.raises(ReleaseError, match="shared live Python"):
        verify_external_python_runtime(
            release,
            shared_runtime,
            allowed_runtime_root=shared_root,
            production=True,
        )


def test_release_verifier_rejects_extra_artifact(tmp_path: Path) -> None:
    release = _make_release(tmp_path)
    release.chmod(0o755)
    extra = release / "unexpected.txt"
    extra.write_text("not attested\n", encoding="utf-8")
    extra.chmod(0o444)
    release.chmod(0o555)

    with pytest.raises(ReleaseError, match="file set mismatch"):
        verify_release(release, production=True)


def test_release_verifier_rejects_attested_python_cache_artifact(tmp_path: Path) -> None:
    release = _make_release(tmp_path)
    manifest = _make_mutable(release)
    cache = release / "backend/api/__pycache__/application.cpython-312.pyc"
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_bytes(b"attested cache fixture\n")
    relative = cache.relative_to(release).as_posix()
    checksum_path = release / "SHA256SUMS"
    checksum_path.write_text(
        checksum_path.read_text(encoding="utf-8")
        + f"{sha256_file(cache)}  {relative}\n",
        encoding="utf-8",
    )
    manifest["artifact"] = {
        "manifest": "SHA256SUMS",
        "manifest_sha256": sha256_file(checksum_path),
        "file_count": manifest["artifact"]["file_count"] + 1,
        "total_bytes": manifest["artifact"]["total_bytes"] + cache.stat().st_size,
    }
    _write(release / "release.json", json.dumps(manifest) + "\n")
    for path in sorted(release.rglob("*"), reverse=True):
        path.chmod(0o555 if path.is_dir() else 0o444)
    release.chmod(0o555)

    with pytest.raises(ReleaseError, match="forbidden cache artifact"):
        verify_release(release, production=True)


def test_release_verifier_rejects_writable_artifact(tmp_path: Path) -> None:
    release = _make_release(tmp_path)
    artifact = release / "frontend-dist/assets/app.js"
    artifact.chmod(0o644)

    with pytest.raises(ReleaseError, match="release path is writable"):
        verify_release(release, production=True)


def test_release_verifier_rejects_dirty_production_without_override(tmp_path: Path) -> None:
    release = _make_release(tmp_path)
    manifest_path = release / "release.json"
    manifest_path.chmod(0o644)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["source_dirty"] = True
    manifest["source"]["dirty"] = True
    manifest["source"]["provenance"]["dirty"] = True
    manifest_path.write_text(json.dumps(manifest) + "\n", encoding="utf-8")
    manifest_path.chmod(0o444)

    with pytest.raises(ReleaseError, match="explicit override"):
        verify_release(release, production=True)


def test_release_verifier_rejects_external_entry_asset(tmp_path: Path) -> None:
    release = _make_release(
        tmp_path,
        index_html='<script src="https://attacker.invalid/runtime.js"></script>',
    )

    with pytest.raises(ReleaseError, match="external frontend asset"):
        verify_release(release, production=True)


def test_secret_scan_reports_credentialed_database_url(tmp_path: Path) -> None:
    fixture_url = "postgresql" + "://admin:real-password@db/app"
    _write(tmp_path / "config.txt", f"DATABASE_URL={fixture_url}\n")

    assert scan_secrets(tmp_path) == [{"path": "config.txt", "kind": "credentialed_database_url"}]


def test_secret_scan_allowlist_requires_exact_vendored_content(tmp_path: Path) -> None:
    relative = "frontend/vue_project/public/datasets/expert-skills/skills/vendor/reference.md"
    fixture_url = "postgresql" + "://demo:example-password@db/example"
    vendored = tmp_path / relative
    _write(vendored, fixture_url + "\n")
    allowlist = {
        "schema_version": 1,
        "entries": [
            {
                "path": relative,
                "kind": "credentialed_database_url",
                "sha256": sha256_file(vendored),
                "reason": "Vendored documentation fixture with a non-production example credential.",
            }
        ],
    }
    _write(
        tmp_path / "quality/secret-scan-allowlist.json",
        json.dumps(allowlist) + "\n",
    )

    assert scan_secrets(tmp_path) == []

    vendored.write_text(fixture_url + "/changed\n", encoding="utf-8")
    findings = scan_secrets(tmp_path)
    assert {finding["kind"].split(":", 1)[0] for finding in findings} == {
        "credentialed_database_url",
        "stale_allowlist",
    }

    vendored.unlink()
    assert scan_secrets(tmp_path) == [
        {"path": relative, "kind": "stale_allowlist:credentialed_database_url"}
    ]


def test_source_secret_scan_leaves_excluded_content_allowlist_to_bundle_gate(
    tmp_path: Path,
) -> None:
    relative = "frontend/vue_project/public/datasets/expert-skills/skills/vendor/reference.md"
    fixture_url = "postgresql" + "://demo:example-password@db/example"
    vendored = tmp_path / relative
    _write(vendored, fixture_url + "\n")
    _write(tmp_path / "backend/api/application.py", "APP = True\n")
    _write(
        tmp_path / "quality/secret-scan-allowlist.json",
        json.dumps(
            {
                "schema_version": 1,
                "entries": [
                    {
                        "path": relative,
                        "kind": "credentialed_database_url",
                        "sha256": sha256_file(vendored),
                        "reason": "Vendored documentation fixture verified by the content bundle gate.",
                    }
                ],
            }
        )
        + "\n",
    )

    assert scan_source_inputs(tmp_path) == []


def _python_cache_artifacts(root: Path) -> list[Path]:
    return [
        path.relative_to(root)
        for path in root.rglob("*")
        if "__pycache__" in path.relative_to(root).parts or path.suffix in {".pyc", ".pyo"}
    ]


def test_release_entrypoints_disable_bytecode_without_caller_environment(tmp_path: Path) -> None:
    tools = tmp_path / "archived-tools"
    tools.mkdir()
    for name in ("release_lib.py", "release_tool.py", "verify_release.py"):
        shutil.copy2(DEPLOY_DIR / name, tools / name)

    project = tmp_path / "project"
    _write(project / "VERSION", "0.9.2\n")
    snapshot = tmp_path / "snapshot.json"
    release = _make_release(tmp_path / "release-fixture")
    env = os.environ.copy()
    env.pop("PYTHONDONTWRITEBYTECODE", None)
    env.pop("PYTHONPYCACHEPREFIX", None)

    release_tool = subprocess.run(
        [
            sys.executable,
            str(tools / "release_tool.py"),
            "snapshot",
            "--project",
            str(project),
            "--output",
            str(snapshot),
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )
    verifier = subprocess.run(
        [sys.executable, str(tools / "verify_release.py"), str(release), "--production"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert release_tool.returncode == 0, release_tool.stderr
    assert verifier.returncode == 0, verifier.stderr
    assert _python_cache_artifacts(tools) == []
    assert _python_cache_artifacts(project) == []
    assert _python_cache_artifacts(release) == []


def test_production_launcher_disables_release_bytecode_writes() -> None:
    launcher = (DEPLOY_DIR / "start_web_prod.sh").read_text(encoding="utf-8")
    creator = (DEPLOY_DIR / "create_release.sh").read_text(encoding="utf-8")

    assert "export PYTHONDONTWRITEBYTECODE=1" in launcher
    assert "export PYTHONDONTWRITEBYTECODE=1" in creator
    assert "export HOST PORT WEB_WORKERS PYTHONDONTWRITEBYTECODE" in launcher
    assert "DB_POOL_TIMEOUT PGOPTIONS APP_ENV" in launcher


def test_production_launcher_does_not_leak_promotion_lock_to_web_master() -> None:
    launcher = (DEPLOY_DIR / "start_web_prod.sh").read_text(encoding="utf-8")

    assert "exec {inherited_promotion_lock_fd}>&-" in launcher
    assert launcher.index("exec {inherited_promotion_lock_fd}>&-") < launcher.index(
        'exec setsid "$PYTHON_BIN" backend/serve_prod.py'
    )


def test_production_launcher_accepts_empty_regular_promotion_lock_file() -> None:
    launcher = (DEPLOY_DIR / "start_web_prod.sh").read_text(encoding="utf-8")

    assert '"regular empty file"' in launcher


def test_production_launcher_uses_only_attested_role_runtime() -> None:
    launcher = (DEPLOY_DIR / "start_web_prod.sh").read_text(encoding="utf-8")
    creator = (DEPLOY_DIR / "create_release.sh").read_text(encoding="utf-8")

    assert "conda activate" not in launcher
    assert 'PYTHON_BIN="/opt/conda/envs/' not in launcher
    assert 'exec setsid "$PYTHON_BIN" backend/serve_prod.py' in launcher
    assert "--python-runtime-manifest" in launcher
    assert "verified versioned Python runtime is required" in creator
    assert "/opt/conda/envs/Globemind_env" not in creator
    assert "production release tooling must use the attested Web role runtime" in creator


def test_production_launcher_uses_one_full_verifier_per_release_schema() -> None:
    launcher = (DEPLOY_DIR / "start_web_prod.sh").read_text(encoding="utf-8")

    schema_v3_start = launcher.index("        3)")
    legacy_start = launcher.index("        2|1)", schema_v3_start)
    schema_switch_end = launcher.index("        *)", legacy_start)
    schema_v3_block = launcher[schema_v3_start:legacy_start]
    legacy_block = launcher[legacy_start:schema_switch_end]

    assert schema_v3_block.count("deploy/verify_release.py") == 1
    assert "--python-runtime-dir" in schema_v3_block
    assert legacy_block.count("deploy/verify_release.py") == 1
    assert "ALLOW_LEGACY_RELEASE=1" in legacy_block
    assert "sha256sum --quiet -c SHA256SUMS" not in legacy_block
