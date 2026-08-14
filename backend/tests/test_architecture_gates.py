from __future__ import annotations

import importlib.util
import json
import sys
from copy import deepcopy
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _load_script(name: str, relative_path: str):
    path = ROOT / relative_path
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


boundaries = _load_script(
    "globemind_check_import_boundaries",
    "scripts/ci/check_import_boundaries.py",
)
runtime_config = _load_script(
    "globemind_check_runtime_config_manifest",
    "scripts/ci/check_runtime_config_manifest.py",
)


def _write(root: Path, relative_path: str, content: str) -> None:
    path = root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_boundary_scanner_covers_backend_frontend_and_pipeline(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "backend/api/core/bad.py",
        "from api.services.search_service import search\n",
    )
    _write(
        tmp_path,
        "core_pipeline/bad.py",
        "from scripts.workflow_orchestrator import run\n",
    )
    _write(
        tmp_path,
        "backend/api/routes/bad.py",
        "import os\n"
        "from os import getenv as read_env\n"
        "from dotenv import load_dotenv\n"
        "VALUE = os.getenv('NEW_SETTING')\n"
        "ALIAS = read_env('ALIASED_SETTING')\n",
    )
    _write(
        tmp_path,
        "frontend/vue_project/src/components/Bad.vue",
        "<script setup>\nimport Page from '@/views/Page.vue'\n</script>\n",
    )

    counts = boundaries.counts_by_rule_and_path(boundaries.scan_repository(tmp_path))

    assert counts[boundaries.RULE_CORE_TO_SERVICES] == {"backend/api/core/bad.py": 1}
    assert counts[boundaries.RULE_PIPELINE_TO_SCRIPTS] == {"core_pipeline/bad.py": 1}
    assert counts[boundaries.RULE_ROUTE_DOTENV] == {"backend/api/routes/bad.py": 1}
    assert counts[boundaries.RULE_DIRECT_ENV] == {"backend/api/routes/bad.py": 2}
    assert counts[boundaries.RULE_SHARED_TO_VIEWS] == {
        "frontend/vue_project/src/components/Bad.vue": 1
    }


def test_new_architecture_modules_are_also_governed(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "backend/features/search/application.py",
        "from os import environ\nVALUE = environ.get('SEARCH_LIMIT')\n",
    )
    _write(
        tmp_path,
        "backend/platform/config/settings.py",
        "import os\nVALUE = os.getenv('CENTRAL_SETTING')\n",
    )
    _write(
        tmp_path,
        "frontend/vue_project/src/shared/theme.js",
        "import '@/views/Page.vue'\n",
    )

    counts = boundaries.counts_by_rule_and_path(boundaries.scan_repository(tmp_path))

    assert counts[boundaries.RULE_DIRECT_ENV] == {
        "backend/features/search/application.py": 1
    }
    assert counts[boundaries.RULE_SHARED_TO_VIEWS] == {
        "frontend/vue_project/src/shared/theme.js": 1
    }


def test_feature_callers_must_use_public_entry_points(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "backend/api/routes/bad.py",
        "from api.features.story_graph.contracts import StoryNode\n",
    )
    _write(
        tmp_path,
        "backend/api/routes/good.py",
        "from api.features.story_graph import StoryNode\n",
    )
    _write(
        tmp_path,
        "backend/api/routes/submodule_bypass.py",
        "from api.features.story_graph import contracts\n",
    )
    _write(
        tmp_path,
        "backend/api/features/story_graph/contracts.py",
        "class StoryNode:\n    pass\n",
    )
    _write(
        tmp_path,
        "backend/api/features/story_graph/internal.py",
        "from api.features.story_graph.contracts import StoryNode\n"
        "from api.features.search.private import Query\n",
    )
    _write(
        tmp_path,
        "frontend/vue_project/src/views/Bad.vue",
        "<script setup>\nimport { format } from '@/features/ground-news/presentation.js'\n</script>\n",
    )
    _write(
        tmp_path,
        "frontend/vue_project/src/views/Good.vue",
        "<script setup>\nimport { format } from '@/features/ground-news/index.js'\n</script>\n",
    )
    _write(
        tmp_path,
        "frontend/vue_project/src/features/ground-news/internal.js",
        "import { local } from './presentation.js'\n"
        "import { privateValue } from '@/features/search/private.js'\n",
    )

    counts = boundaries.counts_by_rule_and_path(boundaries.scan_repository(tmp_path))

    assert counts[boundaries.RULE_BACKEND_FEATURE_PUBLIC_API] == {
        "backend/api/features/story_graph/internal.py": 1,
        "backend/api/routes/bad.py": 1,
        "backend/api/routes/submodule_bypass.py": 1,
    }
    assert counts[boundaries.RULE_FRONTEND_FEATURE_PUBLIC_API] == {
        "frontend/vue_project/src/features/ground-news/internal.js": 1,
        "frontend/vue_project/src/views/Bad.vue": 1,
    }


def test_ratchet_allows_reduction_but_rejects_growth_and_movement(tmp_path: Path) -> None:
    source = "import os\nVALUE = os.getenv('OLD_SETTING')\n"
    _write(tmp_path, "backend/api/routes/legacy.py", source)
    baseline_violations = boundaries.scan_repository(tmp_path)
    baseline = boundaries.baseline_payload(baseline_violations)["rules"]

    _write(tmp_path, "backend/api/routes/legacy.py", "VALUE = 'centralized'\n")
    reduced = boundaries.compare_to_baseline(boundaries.scan_repository(tmp_path), baseline)
    assert reduced.regression_free
    assert not reduced.passed
    assert reduced.resolved_debt[boundaries.RULE_DIRECT_ENV] == {
        "backend/api/routes/legacy.py": 1
    }

    _write(tmp_path, "backend/api/routes/new_file.py", source)
    moved = boundaries.compare_to_baseline(boundaries.scan_repository(tmp_path), baseline)
    assert not moved.passed
    assert moved.new_debt[boundaries.RULE_DIRECT_ENV] == {
        "backend/api/routes/new_file.py": 1
    }

    _write(
        tmp_path,
        "backend/api/routes/legacy.py",
        source + "OTHER = os.getenv('ANOTHER_SETTING')\n",
    )
    grown = boundaries.compare_to_baseline(boundaries.scan_repository(tmp_path), baseline)
    assert not grown.passed
    assert grown.new_debt[boundaries.RULE_DIRECT_ENV][
        "backend/api/routes/legacy.py"
    ] == 1


def test_baseline_round_trip_is_deterministic(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "backend/api/core/bad.py",
        "from api.services.auth import authenticate\n",
    )
    violations = boundaries.scan_repository(tmp_path)
    baseline_path = tmp_path / "quality" / "baseline.json"

    boundaries.write_baseline(baseline_path, violations)

    assert boundaries.load_baseline(baseline_path) == boundaries.counts_by_rule_and_path(
        violations
    )
    first = baseline_path.read_text(encoding="utf-8")
    boundaries.write_baseline(baseline_path, violations)
    assert baseline_path.read_text(encoding="utf-8") == first


def test_baseline_update_can_only_lower_existing_debt(tmp_path: Path) -> None:
    baseline_path = tmp_path / "quality" / "baseline.json"
    source = "import os\nVALUE = os.getenv('SETTING')\n"
    _write(tmp_path, "backend/api/routes/legacy.py", source)
    boundaries.write_baseline(baseline_path, boundaries.scan_repository(tmp_path))

    _write(tmp_path, "backend/api/routes/legacy.py", "VALUE = 'centralized'\n")
    boundaries.write_baseline(baseline_path, boundaries.scan_repository(tmp_path))
    assert boundaries.load_baseline(baseline_path)[boundaries.RULE_DIRECT_ENV] == {}

    _write(tmp_path, "backend/api/routes/legacy.py", source)
    with pytest.raises(boundaries.BoundaryCheckError, match="refusing to increase"):
        boundaries.write_baseline(baseline_path, boundaries.scan_repository(tmp_path))


def test_runtime_config_manifest_is_complete_and_valid() -> None:
    summary = runtime_config.load_and_validate(
        ROOT / "config" / "runtime" / "env-manifest.json"
    )

    assert summary["services"] >= 10
    assert summary["variables"] >= 50
    assert set(summary["scope_counts"]) >= {
        "web",
        "database",
        "security",
        "ai",
        "pipeline",
    }


def test_entity_governance_runtime_storage_and_hmac_are_explicit() -> None:
    payload = json.loads(
        (ROOT / "config" / "runtime" / "env-manifest.json").read_text(
            encoding="utf-8"
        )
    )
    variables = {item["name"]: item for item in payload["variables"]}

    storage = variables["ENTITY_GOVERNANCE_ROOT"]
    assert storage["sensitivity"] == "internal"
    assert storage["services"] == ["web-api"]
    assert storage["default_policy"] == {
        "mode": "safe_default",
        "value": "/root/data/web/entity-governance",
    }

    hmac_key = variables["ENTITY_GOVERNANCE_HMAC_KEY"]
    assert hmac_key["scope"] == "security"
    assert hmac_key["sensitivity"] == "secret"
    assert hmac_key["default_policy"] == {"mode": "secret_required"}
    assert "dual-key verification are not implemented" in hmac_key["description"]


def test_runtime_config_manifest_rejects_embedded_secret_defaults() -> None:
    payload = json.loads(
        (ROOT / "config" / "runtime" / "env-manifest.json").read_text(encoding="utf-8")
    )
    invalid = deepcopy(payload)
    secret = next(item for item in invalid["variables"] if item["sensitivity"] == "secret")
    secret["default_policy"]["value"] = "must-not-be-here"

    with pytest.raises(runtime_config.ManifestError, match="must never embed"):
        runtime_config.validate_manifest(invalid)
