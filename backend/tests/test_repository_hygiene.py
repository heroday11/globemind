from pathlib import Path

from scripts.ci.check_repository_hygiene import (
    check_data_assets,
    check_runtime_path_policy,
    check_scripts_manifest,
    tracked_paths,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_repository_hygiene_manifests_cover_current_source_tree() -> None:
    paths = tracked_paths(PROJECT_ROOT)
    assert check_data_assets(PROJECT_ROOT, paths) == []
    assert check_scripts_manifest(PROJECT_ROOT, paths) == []
    assert check_runtime_path_policy(PROJECT_ROOT) == []


def test_local_runtime_data_is_explicitly_non_git() -> None:
    manifest = (PROJECT_ROOT / "quality/data-assets-manifest.json").read_text(encoding="utf-8")
    assert '"size_statement":' in manifest
    assert '"expected_size_gb"' not in manifest
    assert '"tracked_in_git": false' in manifest


def test_quality_gate_runs_repository_hygiene_as_a_reported_step() -> None:
    source = (PROJECT_ROOT / "deploy/run_quality_gate.sh").read_text(encoding="utf-8")
    assert (
        'run_step repository_hygiene "$PYTHON_BIN" -B '
        'scripts/ci/check_repository_hygiene.py'
    ) in source
    assert "repository_hygiene\\t0\\t0" in source
