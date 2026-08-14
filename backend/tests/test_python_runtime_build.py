from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ROLE_INPUT = PROJECT_ROOT / "requirements" / "roles" / "web.in"
ROLE_LOCK = PROJECT_ROOT / "requirements" / "roles" / "web.lock"
LOCK_METADATA = PROJECT_ROOT / "requirements" / "roles" / "web.lock.metadata.json"
BUILD_SCRIPT = PROJECT_ROOT / "deploy" / "build_python_runtime.sh"
VERSION_FILE = PROJECT_ROOT / "VERSION"
PROMOTED_RUNTIME = os.environ.get("GLOBEMIND_TEST_PROMOTED_RUNTIME", "").strip()


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _requirement_names(text: str) -> set[str]:
    names: set[str] = set()
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", "--")) or line.startswith(" "):
            continue
        match = re.match(r"([A-Za-z0-9_.-]+)(?:\[.*?\])?(?:==| @ )", line)
        if match:
            names.add(match.group(1).lower().replace("_", "-"))
    return names


def test_web_role_dependency_boundary_is_explicit() -> None:
    role_input = ROLE_INPUT.read_text(encoding="utf-8")
    names = _requirement_names(role_input)
    required = {
        "fastapi",
        "sqlalchemy",
        "psycopg2-binary",
        "pymilvus",
        "torch",
        "sentence-transformers",
        "transformers",
        "pillow",
        "pymupdf",
        "python-pptx",
        "cairosvg",
        "reportlab",
        "svglib",
        "requests",
        "beautifulsoup4",
    }
    assert required <= names
    assert {"gliner", "vllm"}.isdisjoint(names)
    assert "torch @ https://download.pytorch.org/whl/cpu/torch-2.10.0%2Bcpu" in role_input
    assert "transformers==5.1.0" in role_input


def test_web_role_lock_is_exact_hashed_and_source_bound() -> None:
    lock = ROLE_LOCK.read_text(encoding="utf-8")
    metadata = json.loads(LOCK_METADATA.read_text(encoding="utf-8"))
    assert lock.count("--hash=sha256:") >= 50
    assert not re.search(r"(?m)^--(?:extra-index-url|find-links|trusted-host)(?:[ =]|$)", lock)
    assert not re.search(r"(?m)^[a-zA-Z0-9_.-]+(?:\[.*?\])?(?<![=<>~!])$", lock)
    assert {"gliner", "vllm"}.isdisjoint(_requirement_names(lock))
    assert "torch @ https://download.pytorch.org/whl/cpu/torch-2.10.0%2Bcpu" in lock
    assert "transformers==5.1.0" in lock
    assert metadata["resolver"] == "pip-tools backtracking"
    assert metadata["pip_tools_version"] == "7.5.2"
    assert metadata["python"] == "3.11.15"
    assert metadata["platform"] == "linux-x86_64"
    assert metadata["machine"] == "x86_64"
    assert metadata["index_urls"] == ["https://pypi.org/simple"]
    assert len(metadata["direct_artifacts"]) == 1
    assert metadata["input_sha256"] == _sha256(ROLE_INPUT)
    assert metadata["lock_sha256"] == _sha256(ROLE_LOCK)


def test_runtime_builder_has_immutable_promotion_guards() -> None:
    script = BUILD_SCRIPT.read_text(encoding="utf-8")
    required_fragments = (
        "flock -w 30",
        "--require-hashes",
        "--no-deps",
        "--only-binary=:all:",
        "PIP_CONFIG_FILE=/dev/null",
        "-m pip --isolated install",
        "same version has a different build fingerprint; refusing overwrite",
        "environment_freeze_hash",
        "live Web environment changed during build",
        "pipeline environment changed during build",
        "relocate_console_scripts",
        'grep -IqF "$staging" "$staging/pyvenv.cfg"',
        'mv -T --no-clobber -- "$STAGING_DIR" "$TARGET_DIR"',
        'PROMOTED_IDENTITY="$target_identity"',
        'current_identity="$(path_identity "$TARGET_DIR" || true)"',
        "validate_runtime_links",
        'find "$runtime_dir" -xdev ! -type l -perm /022',
        "pytest -p no:cacheprovider",
    )
    for fragment in required_fragments:
        assert fragment in script
    move = script.index('mv -T --no-clobber -- "$STAGING_DIR" "$TARGET_DIR"')
    assert move < script.rindex("PROMOTED=1")
    subprocess.run(["bash", "-n", str(BUILD_SCRIPT)], check=True, cwd=PROJECT_ROOT)


def _source_builder(
    script: Path = BUILD_SCRIPT,
    *,
    runtime_version: str | None = None,
) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment["SOURCE_PYTHON"] = sys.executable
    environment.pop("RUNTIME_VERSION", None)
    if runtime_version is not None:
        environment["RUNTIME_VERSION"] = runtime_version
    return subprocess.run(
        [
            "bash",
            "-c",
            'source "$1"; printf "%s\\n%s\\n" "$RUNTIME_VERSION" "$TARGET_DIR"',
            "runtime-version-test",
            str(script),
        ],
        cwd=PROJECT_ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )


def test_runtime_builder_defaults_to_validated_project_version() -> None:
    expected = VERSION_FILE.read_text(encoding="ascii").strip()

    result = _source_builder()

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [
        expected,
        f"/root/data/python-runtimes/globemind-web/{expected}",
    ]


def test_runtime_builder_allows_an_explicit_valid_version_override() -> None:
    result = _source_builder(runtime_version="9.8.7-rc.1")

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [
        "9.8.7-rc.1",
        "/root/data/python-runtimes/globemind-web/9.8.7-rc.1",
    ]


@pytest.mark.parametrize(
    "runtime_version",
    ["", "0.10", "v0.10.0", "0.10.0/escape", " 0.10.0"],
)
def test_runtime_builder_rejects_invalid_explicit_versions(
    runtime_version: str,
) -> None:
    result = _source_builder(runtime_version=runtime_version)

    assert result.returncode != 0
    assert "invalid runtime version from RUNTIME_VERSION" in result.stderr


@pytest.mark.parametrize(
    "contents",
    ["", "0.10\n", "v0.10.0\n", "0.10.0\nextra\n", " 0.10.0\n"],
)
def test_runtime_builder_rejects_malformed_project_version(
    tmp_path: Path,
    contents: str,
) -> None:
    project = tmp_path / "project"
    copied_builder = project / "deploy" / BUILD_SCRIPT.name
    copied_builder.parent.mkdir(parents=True)
    shutil.copy2(BUILD_SCRIPT, copied_builder)
    (project / "VERSION").write_text(contents, encoding="ascii")

    result = _source_builder(copied_builder)

    assert result.returncode != 0
    assert "VERSION" in result.stderr


def test_web_runtime_gate_covers_v1_architecture_contracts() -> None:
    script = BUILD_SCRIPT.read_text(encoding="utf-8")
    expected_tests = {
        "backend/tests/test_database_consumer_inventory.py",
        "backend/tests/test_database_engine_consolidation.py",
        "backend/tests/test_dashboard_feature.py",
        "backend/tests/test_feature_health.py",
        "backend/tests/test_graph_briefing_feature.py",
        "backend/tests/test_identity_feature.py",
        "backend/tests/test_identity_security_boundary.py",
        "backend/tests/test_legacy_endpoint_retirement.py",
        "backend/tests/test_ops_runtime_catalog.py",
        "backend/tests/test_runtime_service_catalog.py",
        "backend/tests/test_story_graph_feature_boundary.py",
        "backend/tests/test_v11_search_feature.py",
    }

    assert all(test_path in script for test_path in expected_tests)


def _run_promotion(candidate: Path, target: Path, body: str) -> subprocess.CompletedProcess[str]:
    script = f"""
set -euo pipefail
source "$1"
STAGING_DIR="$2"
TARGET_DIR="$3"
PROMOTED=0
PROMOTED_IDENTITY=""
trap cleanup EXIT INT TERM
{body}
"""
    return subprocess.run(
        ["bash", "-c", script, "promotion-test", str(BUILD_SCRIPT), str(candidate), str(target)],
        check=False,
        text=True,
        capture_output=True,
        cwd=PROJECT_ROOT,
    )


def test_runtime_promotion_does_not_overwrite_racing_target(tmp_path: Path) -> None:
    candidate = tmp_path / "candidate"
    target = tmp_path / "runtime"
    candidate.mkdir()
    (candidate / "candidate-marker").write_text("candidate", encoding="utf-8")

    result = _run_promotion(
        candidate,
        target,
        """
mv() {
    mkdir "$TARGET_DIR"
    printf external > "$TARGET_DIR/external-marker"
    command mv "$@"
}
promote_candidate
""",
    )

    assert result.returncode != 0
    assert (target / "external-marker").read_text(encoding="utf-8") == "external"
    assert not candidate.exists()


def test_runtime_cleanup_preserves_replaced_promoted_target(tmp_path: Path) -> None:
    candidate = tmp_path / "candidate"
    target = tmp_path / "runtime"
    displaced = tmp_path / "displaced-candidate"
    candidate.mkdir()

    result = _run_promotion(
        candidate,
        target,
        f"""
promote_candidate
mv -T -- "$TARGET_DIR" "{displaced}"
mkdir "$TARGET_DIR"
printf external > "$TARGET_DIR/external-marker"
exit 23
""",
    )

    assert result.returncode == 23
    assert (target / "external-marker").read_text(encoding="utf-8") == "external"
    assert displaced.is_dir()


def test_runtime_promotion_rejects_dangling_symlink_target(tmp_path: Path) -> None:
    candidate = tmp_path / "candidate"
    target = tmp_path / "runtime"
    candidate.mkdir()
    target.symlink_to(tmp_path / "missing-external-target")

    result = _run_promotion(candidate, target, "promote_candidate")

    assert result.returncode != 0
    assert target.is_symlink()
    assert not candidate.exists()


def _run_link_validation(runtime: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "bash",
            "-c",
            'source "$1"; validate_runtime_links "$2"',
            "link-validation-test",
            str(BUILD_SCRIPT),
            str(runtime),
        ],
        check=False,
        text=True,
        capture_output=True,
        cwd=PROJECT_ROOT,
        env={**os.environ, "SOURCE_PYTHON": sys.executable},
    )


def test_runtime_link_gate_allows_internal_relative_link(tmp_path: Path) -> None:
    runtime = tmp_path / "runtime"
    (runtime / "lib").mkdir(parents=True)
    (runtime / "lib64").symlink_to("lib", target_is_directory=True)

    result = _run_link_validation(runtime)

    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("link_kind", ["absolute", "dangling", "escaping"])
def test_runtime_link_gate_rejects_unsafe_links(tmp_path: Path, link_kind: str) -> None:
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    link = runtime / "unsafe"
    if link_kind == "absolute":
        link.symlink_to(outside, target_is_directory=True)
    elif link_kind == "dangling":
        link.symlink_to("missing", target_is_directory=True)
    else:
        link.symlink_to("../outside", target_is_directory=True)

    result = _run_link_validation(runtime)

    assert result.returncode != 0


def test_promoted_runtime_inventory_when_present() -> None:
    if not PROMOTED_RUNTIME:
        pytest.skip("set GLOBEMIND_TEST_PROMOTED_RUNTIME for external runtime evidence")
    runtime_dir = Path(PROMOTED_RUNTIME)
    assert runtime_dir.is_absolute()
    inventory_path = runtime_dir / "inventory" / "runtime.json"
    freeze_path = runtime_dir / "inventory" / "pip-freeze.txt"
    closure_path = runtime_dir / "inventory" / "import-closure.json"
    assert inventory_path.is_file()
    inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
    closure = json.loads(closure_path.read_text(encoding="utf-8"))
    assert inventory["role"] == "web"
    assert inventory["version"] == "0.9.3"
    assert inventory["validation"] == {
        "critical_imports": "pass",
        "pip_check": "pass",
        "pytest_web": "pass",
    }
    assert inventory["pip_freeze_sha256"] == _sha256(freeze_path)
    assert inventory["import_closure_sha256"] == _sha256(closure_path)
    assert {"torch", "transformers", "sentence_transformers"} <= set(
        closure["critical_imports"]
    )
    writable = [
        path
        for path in runtime_dir.rglob("*")
        if not path.is_symlink() and path.stat().st_mode & 0o022
    ]
    assert writable == []
    assert os.access(runtime_dir / "bin" / "python", os.X_OK)
