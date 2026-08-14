#!/bin/bash
set -euo pipefail
umask 027
export PYTHONDONTWRITEBYTECODE=1

PROJECT_DIR="${PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
RUFF_BIN="${RUFF_BIN:-}"
TOOL_PYTHON_BIN="${TOOL_PYTHON_BIN:-}"
OUTPUT=""
SKIP_TESTS=0
SKIP_FRONTEND=0

while [ "$#" -gt 0 ]; do
    case "$1" in
        --output)
            [ "$#" -ge 2 ] || { echo "--output requires a path" >&2; exit 2; }
            OUTPUT="$2"
            shift 2
            ;;
        --skip-tests)
            SKIP_TESTS=1
            shift
            ;;
        --skip-frontend)
            SKIP_FRONTEND=1
            shift
            ;;
        *)
            echo "unknown argument: $1" >&2
            exit 2
            ;;
    esac
done

if [ -z "$OUTPUT" ]; then
    OUTPUT="$(mktemp /tmp/globemind-quality-gate.XXXXXX.json)"
else
    OUTPUT="$(realpath -m "$OUTPUT")"
    mkdir -p "$(dirname "$OUTPUT")"
fi

work_dir="$(mktemp -d /tmp/globemind-quality-gate.XXXXXX)"
steps_file="$work_dir/steps.tsv"
pytest_xml="$work_dir/pytest.xml"
ratchet_json="$work_dir/frontend-ratchet.json"
overall_status=0
ruff_version_output=""

resolve_executable() {
    local candidate="$1"
    local resolved
    resolved="$(command -v -- "$candidate" 2>/dev/null || true)"
    if [ -n "$resolved" ]; then
        realpath -e "$resolved" 2>/dev/null || printf '%s\n' "$resolved"
    else
        printf '%s\n' "$candidate"
    fi
}

if [ -n "$RUFF_BIN" ]; then
    ruff_tool_source="RUFF_BIN"
    ruff_tool_mode="executable"
    ruff_tool_executable="$(resolve_executable "$RUFF_BIN")"
    ruff_command=("$ruff_tool_executable")
elif [ -n "$TOOL_PYTHON_BIN" ]; then
    ruff_tool_source="TOOL_PYTHON_BIN"
    ruff_tool_mode="python_module"
    ruff_tool_executable="$(resolve_executable "$TOOL_PYTHON_BIN")"
    ruff_command=("$ruff_tool_executable" -B -m ruff)
else
    ruff_tool_source="PYTHON_BIN"
    ruff_tool_mode="python_module"
    ruff_tool_executable="$(resolve_executable "$PYTHON_BIN")"
    ruff_command=("$ruff_tool_executable" -B -m ruff)
fi

ruff_targets=(
    backend/serve_prod.py
    backend/tests/test_frontend_budget_gate.py
    backend/tests/test_static_path_security.py
    deploy/browser_smoke.py
    deploy/candidate_smoke.py
    deploy/promote_web_release.py
    deploy/release_lib.py
    deploy/release_tool.py
    deploy/verify_release.py
    deploy/web_promotion.py
    scripts/ci/check_database_consumers.py
    scripts/ci/check_feature_registry.py
    scripts/ci/check_import_boundaries.py
    scripts/ci/check_repository_hygiene.py
    scripts/ci/check_root_layout.py
    scripts/run_event_level_pipeline.py
    backend/tests/test_browser_smoke.py
    backend/tests/test_candidate_smoke.py
    backend/tests/test_ci_workflow_contract.py
    backend/tests/test_architecture_gates.py
    backend/tests/test_packaging_contract.py
    backend/tests/test_repository_hygiene.py
    backend/tests/test_runtime_control_aliases.py
    backend/tests/test_release_tooling.py
    backend/tests/test_database_consumer_inventory.py
    backend/tests/test_feature_registry.py
    backend/tests/test_root_layout.py
    backend/tests/test_web_promotion.py
    backend/api/features
    backend/api/routes/auth.py
    backend/api/routes/dashboard.py
    backend/api/routes/ops_monitor.py
    backend/tests/test_dashboard_feature.py
    backend/tests/test_database_runtime_roles.py
    backend/tests/test_feature_health.py
    backend/tests/test_identity_feature.py
    backend/tests/test_ops_runtime_catalog.py
    backend/tests/test_runtime_service_catalog.py
    backend/cc_integration.py
    backend/runtime_control
    deploy/db_role_policy.py
    deploy/db_runtime_roles.py
    scripts/runtime_control
)
ruff_targets_file="$work_dir/ruff-targets.txt"
printf '%s\n' "${ruff_targets[@]}" > "$ruff_targets_file"

cleanup() {
    rm -rf "$work_dir"
}
trap cleanup EXIT

run_step() {
    local name="$1"
    shift
    local start end duration rc
    start="$(date +%s)"
    set +e
    "$@" >"$work_dir/${name}.log" 2>&1
    rc=$?
    set -e
    end="$(date +%s)"
    duration=$((end - start))
    printf '%s\t%s\t%s\n' "$name" "$rc" "$duration" >> "$steps_file"
    if [ "$rc" -ne 0 ]; then
        overall_status=1
        echo "quality gate step failed: $name" >&2
        tail -n 80 "$work_dir/${name}.log" >&2 || true
    else
        echo "quality gate step passed: $name (${duration}s)"
    fi
}

verify_ruff_tool() {
    local output first_line
    if ! output="$("${ruff_command[@]}" --version 2>&1)"; then
        printf '%s\n' "$output"
        return 1
    fi
    first_line="${output%%$'\n'*}"
    printf '%s\n' "$output"
    if [[ ! "$first_line" =~ ^ruff[[:space:]]+[0-9]+([.][0-9]+)+ ]]; then
        return 1
    fi
    ruff_version_output="$first_line"
}

cd "$PROJECT_DIR"
project_version="$(tr -d '\n\r' < VERSION)"
project_major="${project_version%%.*}"
feature_registry_args=()
feature_registry_mode="inventory"
if [[ "$project_major" =~ ^[0-9]+$ ]] && [ "$((10#$project_major))" -ge 1 ]; then
    feature_registry_args+=(--release-ready)
    feature_registry_mode="release-ready"
fi
"$PYTHON_BIN" -B deploy/release_tool.py snapshot \
    --project "$PROJECT_DIR" --output "$work_dir/source-before.json" >/dev/null
run_step config env PYTHON_BIN="$PYTHON_BIN" bash -c '
    set -euo pipefail
    version="$(tr -d "\n\r" < VERSION)"
    [[ "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+([+-][0-9A-Za-z.-]+)?$ ]]
    bash -n deploy/create_release.sh deploy/build_frontend_release.sh deploy/start_web_prod.sh deploy/run_quality_gate.sh
    node --check deploy/check_frontend_budgets.mjs
    "$PYTHON_BIN" -B deploy/verify_release.py --help >/dev/null
'
run_step root_layout "$PYTHON_BIN" -B scripts/ci/check_root_layout.py \
    --project "$PROJECT_DIR"
if [ -f scripts/ci/check_repository_hygiene.py ] \
    && [ -f quality/data-assets-manifest.json ] \
    && [ -f quality/runtime-path-policy.json ] \
    && [ -f scripts/manifest.json ]; then
    run_step repository_hygiene "$PYTHON_BIN" -B scripts/ci/check_repository_hygiene.py \
        --project "$PROJECT_DIR"
else
    # Keep older isolated quality-gate fixtures compatible while making the
    # repository's real gate fail closed once its manifests are present.
    printf 'repository_hygiene\t0\t0\n' >> "$steps_file"
fi
run_step ruff_tool verify_ruff_tool
run_step release_lint "${ruff_command[@]}" check "${ruff_targets[@]}"
run_step import_boundaries "$PYTHON_BIN" -B scripts/ci/check_import_boundaries.py
run_step feature_registry "$PYTHON_BIN" -B scripts/ci/check_feature_registry.py \
    "${feature_registry_args[@]}"
run_step runtime_config "$PYTHON_BIN" -B scripts/ci/check_runtime_config_manifest.py
run_step database_consumers "$PYTHON_BIN" -B scripts/ci/check_database_consumers.py
run_step content_bundles "$PYTHON_BIN" -B deploy/release_tool.py content-bundle-policy \
    --project "$PROJECT_DIR" --output "$work_dir/content-bundles.json"
run_step source_secrets "$PYTHON_BIN" -B deploy/release_tool.py source-secret-scan \
    --project "$PROJECT_DIR" --output "$work_dir/source-secrets.json"

if [ "$SKIP_TESTS" -eq 0 ]; then
    read -r -a pytest_extra <<< "${QUALITY_PYTEST_ARGS:-}"
    run_step pytest env APP_ENV=test GLOBEMIND_TEST_ISOLATION=1 \
        "$PYTHON_BIN" -B -m pytest -q -m "not integration and not live_db and not gpu and not slow" \
        --junitxml="$pytest_xml" "${pytest_extra[@]}"
else
    printf 'pytest\t0\t0\n' >> "$steps_file"
fi

if [ "$SKIP_FRONTEND" -eq 0 ]; then
    run_step frontend_lint npm run lint
    run_step frontend_contracts npm test
    run_step frontend_ratchet node deploy/check_frontend_ratchet.mjs --output "$ratchet_json"
else
    printf 'frontend_lint\t0\t0\n' >> "$steps_file"
    printf 'frontend_contracts\t0\t0\n' >> "$steps_file"
    printf 'frontend_ratchet\t0\t0\n' >> "$steps_file"
fi

"$PYTHON_BIN" -B deploy/release_tool.py snapshot \
    --project "$PROJECT_DIR" --output "$work_dir/source-after.json" >/dev/null
run_step source_stability cmp "$work_dir/source-before.json" "$work_dir/source-after.json"

OUTPUT="$OUTPUT" STEPS_FILE="$steps_file" PYTEST_XML="$pytest_xml" \
RATCHET_JSON="$ratchet_json" OVERALL_STATUS="$overall_status" \
SKIP_TESTS="$SKIP_TESTS" SKIP_FRONTEND="$SKIP_FRONTEND" \
PYTHON_BIN="$PYTHON_BIN" SOURCE_BEFORE="$work_dir/source-before.json" \
SOURCE_AFTER="$work_dir/source-after.json" \
RUFF_TOOL_SOURCE="$ruff_tool_source" RUFF_TOOL_MODE="$ruff_tool_mode" \
RUFF_TOOL_EXECUTABLE="$ruff_tool_executable" RUFF_VERSION="$ruff_version_output" \
PROJECT_VERSION="$project_version" FEATURE_REGISTRY_MODE="$feature_registry_mode" \
RUFF_TARGETS_FILE="$ruff_targets_file" \
"$PYTHON_BIN" -B - <<'PY'
import json
import os
import platform
import subprocess
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path


def version(command):
    try:
        return subprocess.check_output(command, stderr=subprocess.STDOUT, text=True).strip().splitlines()[0]
    except (OSError, subprocess.CalledProcessError):
        return "unavailable"


steps = []
for line in Path(os.environ["STEPS_FILE"]).read_text(encoding="utf-8").splitlines():
    name, code, duration = line.split("\t")
    steps.append(
        {
            "name": name,
            "status": "passed" if code == "0" else "failed",
            "exit_code": int(code),
            "duration_seconds": int(duration),
        }
    )

tests = {"status": "skipped"}
pytest_xml = Path(os.environ["PYTEST_XML"])
if pytest_xml.is_file():
    root = ET.parse(pytest_xml).getroot()
    suite = root if root.tag == "testsuite" else root.find("testsuite")
    if suite is not None:
        failed_cases = []
        for case in root.iter("testcase"):
            if case.find("failure") is None and case.find("error") is None:
                continue
            class_name = case.attrib.get("classname", "").strip()
            test_name = case.attrib.get("name", "unknown").strip()
            failed_cases.append(f"{class_name}::{test_name}".strip(":"))
        tests = {
            "status": "passed" if int(suite.attrib.get("failures", 0)) + int(suite.attrib.get("errors", 0)) == 0 else "failed",
            "total": int(suite.attrib.get("tests", 0)),
            "failures": int(suite.attrib.get("failures", 0)),
            "errors": int(suite.attrib.get("errors", 0)),
            "skipped": int(suite.attrib.get("skipped", 0)),
            "duration_seconds": round(float(suite.attrib.get("time", 0)), 3),
            "failed_cases": failed_cases[:50],
        }

ratchets = {"status": "skipped"}
ratchet_path = Path(os.environ["RATCHET_JSON"])
if ratchet_path.is_file():
    ratchets = json.loads(ratchet_path.read_text(encoding="utf-8"))

source_before = json.loads(Path(os.environ["SOURCE_BEFORE"]).read_text(encoding="utf-8"))
source_after = json.loads(Path(os.environ["SOURCE_AFTER"]).read_text(encoding="utf-8"))
ruff_command = [os.environ["RUFF_TOOL_EXECUTABLE"]]
if os.environ["RUFF_TOOL_MODE"] == "python_module":
    ruff_command.extend(["-B", "-m", "ruff"])
ruff_targets = Path(os.environ["RUFF_TARGETS_FILE"]).read_text(encoding="utf-8").splitlines()
ruff_command.extend(["check", *ruff_targets])

partial = os.environ["SKIP_TESTS"] == "1" or os.environ["SKIP_FRONTEND"] == "1"
payload = {
    "schema_version": 1,
    "status": "failed" if os.environ["OVERALL_STATUS"] != "0" else ("partial" if partial else "passed"),
    "completed_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
    "steps": steps,
    "tests": tests,
    "ratchets": ratchets,
    "scope": {
        "python_tests_skipped": os.environ["SKIP_TESTS"] == "1",
        "frontend_skipped": os.environ["SKIP_FRONTEND"] == "1",
        "project_version": os.environ["PROJECT_VERSION"],
        "feature_registry_mode": os.environ["FEATURE_REGISTRY_MODE"],
    },
    "source_snapshot": source_before,
    "source_unchanged": source_before == source_after,
    "tools": {
        "python": platform.python_version(),
        "pytest": version([os.environ.get("PYTHON_BIN", "python3"), "-B", "-m", "pytest", "--version"]),
        "ruff": os.environ.get("RUFF_VERSION") or "unavailable",
        "ruff_command": {
            "argv": ruff_command,
            "executable": os.environ["RUFF_TOOL_EXECUTABLE"],
            "selection": os.environ["RUFF_TOOL_SOURCE"],
            "working_directory": str(Path.cwd()),
        },
        "node": version(["node", "--version"]),
        "npm": version(["npm", "--version"]),
    },
}
Path(os.environ["OUTPUT"]).write_text(
    json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
    encoding="utf-8",
)
PY

echo "quality gate metadata: $OUTPUT"
exit "$overall_status"
