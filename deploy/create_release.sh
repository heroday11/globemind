#!/bin/bash
set -euo pipefail
umask 027
export PYTHONDONTWRITEBYTECODE=1

PROJECT_DIR="${PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
RELEASE_ROOT="${RELEASE_ROOT:-/root/data/releases/globemind}"
PYTHON_BIN="${PYTHON_BIN:-}"
FRONTEND_DEPENDENCY_MODE="${FRONTEND_DEPENDENCY_MODE:-ci}"
RUN_QUALITY_GATE="${RUN_QUALITY_GATE:-1}"
ALLOW_UNVERIFIED_RELEASE="${ALLOW_UNVERIFIED_RELEASE:-0}"
ALLOW_DIRTY_RELEASE="${ALLOW_DIRTY_RELEASE:-0}"
PRODUCTION_RELEASE="${PRODUCTION_RELEASE:-1}"
QUALITY_METADATA="${QUALITY_METADATA:-}"
PYTHON_RUNTIME_ROOT="${PYTHON_RUNTIME_ROOT:-/root/data/python-runtimes/globemind-web}"

source_version="$(tr -d '\r\n' < "$PROJECT_DIR/VERSION")"
if [ -n "${VERSION:-}" ] && [ "$VERSION" != "$source_version" ]; then
    echo "VERSION overrides are forbidden; update $PROJECT_DIR/VERSION" >&2
    exit 1
fi
VERSION="$source_version"
PYTHON_RUNTIME_DIR="${PYTHON_RUNTIME_DIR:-${PYTHON_RUNTIME_ROOT}/${VERSION}}"
PYTHON_RUNTIME_MANIFEST="${PYTHON_RUNTIME_MANIFEST:-${PYTHON_RUNTIME_DIR}/inventory/runtime.json}"
if [[ ! "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+([+-][0-9A-Za-z.-]+)?$ ]]; then
    echo "invalid VERSION: $VERSION" >&2
    exit 1
fi

BUILD_ID="${BUILD_ID:-${VERSION}-$(date -u +%Y%m%dT%H%M%SZ)}"
source_git_sha="$(git -C "$PROJECT_DIR" rev-parse HEAD)"
if [ -n "${GIT_SHA:-}" ] && [ "$GIT_SHA" != "$source_git_sha" ]; then
    echo "GIT_SHA overrides are forbidden; release identity must use the current HEAD" >&2
    exit 1
fi
GIT_SHA="$source_git_sha"
if [[ ! "$BUILD_ID" =~ ^[A-Za-z0-9._-]+$ ]] || [[ ! "$GIT_SHA" =~ ^[0-9a-fA-F]{7,64}$ ]]; then
    echo "BUILD_ID or GIT_SHA contains unsupported characters" >&2
    exit 1
fi
if [ "$FRONTEND_DEPENDENCY_MODE" != "ci" ] && [ "$FRONTEND_DEPENDENCY_MODE" != "linked" ]; then
    echo "FRONTEND_DEPENDENCY_MODE must be ci or linked" >&2
    exit 1
fi
if [ "$PRODUCTION_RELEASE" = "1" ] && [ "$FRONTEND_DEPENDENCY_MODE" != "ci" ]; then
    echo "production releases require FRONTEND_DEPENDENCY_MODE=ci" >&2
    exit 1
fi
runtime_root_resolved="$(realpath -e "$PYTHON_RUNTIME_ROOT" 2>/dev/null || true)"
runtime_dir_resolved="$(realpath -e "$PYTHON_RUNTIME_DIR" 2>/dev/null || true)"
runtime_manifest_resolved="$(realpath -e "$PYTHON_RUNTIME_MANIFEST" 2>/dev/null || true)"
if [ -z "$runtime_root_resolved" ] || [ -z "$runtime_dir_resolved" ] || \
   [ -z "$runtime_manifest_resolved" ] || [ ! -x "$runtime_dir_resolved/bin/python" ]; then
    echo "verified versioned Python runtime is required: $PYTHON_RUNTIME_DIR" >&2
    exit 1
fi
case "${runtime_dir_resolved}/" in
    "${runtime_root_resolved}/"*/) ;;
    *)
        echo "Python runtime must be below the managed runtime root: $runtime_dir_resolved" >&2
        exit 1
        ;;
esac
runtime_relative="${runtime_dir_resolved#${runtime_root_resolved}/}"
if [ "$runtime_relative" = "$runtime_dir_resolved" ] || [[ "$runtime_relative" == */* ]]; then
    echo "Python runtime must be exactly one version below the managed root" >&2
    exit 1
fi
case "${runtime_manifest_resolved}" in
    "${runtime_dir_resolved}/"*) ;;
    *)
        echo "Python runtime manifest must be inside the selected runtime" >&2
        exit 1
        ;;
esac
case "${runtime_dir_resolved}/" in
    /opt/conda/envs/*|*/.env_torch/*|*/.venv/*)
        echo "shared live Python environments cannot be used for releases" >&2
        exit 1
        ;;
esac
PYTHON_RUNTIME_DIR="$runtime_dir_resolved"
PYTHON_RUNTIME_MANIFEST="$runtime_manifest_resolved"
if [ -z "$PYTHON_BIN" ]; then
    PYTHON_BIN="$PYTHON_RUNTIME_DIR/bin/python"
fi
if [ ! -x "$PYTHON_BIN" ]; then
    echo "release tooling Python is not executable: $PYTHON_BIN" >&2
    exit 1
fi
if [ "$PRODUCTION_RELEASE" = "1" ] && \
   [ "$(realpath -e "$PYTHON_BIN")" != "$PYTHON_RUNTIME_DIR/bin/python" ]; then
    echo "production release tooling must use the attested Web role runtime" >&2
    exit 1
fi

mkdir -p "$RELEASE_ROOT"
target="$RELEASE_ROOT/$BUILD_ID"
if [ -e "$target" ]; then
    echo "release already exists: $target" >&2
    exit 1
fi

work_dir="$(mktemp -d "$RELEASE_ROOT/.${BUILD_ID}.build.XXXXXX")"
release_staging="$work_dir/$BUILD_ID"
staged_project="$work_dir/source"
frontend_output="$work_dir/frontend-dist"
mkdir "$release_staging"

cleanup() {
    chmod -R u+w "$work_dir" 2>/dev/null || true
    rm -rf "$work_dir"
}
trap cleanup EXIT

if [ -z "$QUALITY_METADATA" ]; then
    QUALITY_METADATA="$work_dir/quality-gate.json"
    if [ "$RUN_QUALITY_GATE" = "1" ]; then
        PROJECT_DIR="$PROJECT_DIR" PYTHON_BIN="$PYTHON_BIN" \
            "$PROJECT_DIR/deploy/run_quality_gate.sh" --output "$QUALITY_METADATA" >&2
    elif [ "$ALLOW_UNVERIFIED_RELEASE" = "1" ]; then
        "$PYTHON_BIN" - "$QUALITY_METADATA" <<'PY'
import json
import sys
from pathlib import Path

Path(sys.argv[1]).write_text(
    json.dumps(
        {
            "schema_version": 1,
            "status": "not_run",
            "tests": {"status": "not_run"},
            "ratchets": {"status": "not_run"},
        },
        indent=2,
        sort_keys=True,
    )
    + "\n",
    encoding="utf-8",
)
PY
    else
        echo "quality gate cannot be skipped without ALLOW_UNVERIFIED_RELEASE=1" >&2
        exit 1
    fi
fi
QUALITY_METADATA="$(realpath -e "$QUALITY_METADATA")"

"$PYTHON_BIN" "$PROJECT_DIR/deploy/release_tool.py" snapshot \
    --project "$PROJECT_DIR" --output "$work_dir/source-before.json" >/dev/null
"$PYTHON_BIN" "$PROJECT_DIR/deploy/release_tool.py" provenance \
    --project "$PROJECT_DIR" --output "$work_dir/source-provenance-before.json" >/dev/null
source_dirty="$(PROVENANCE_FILE="$work_dir/source-provenance-before.json" "$PYTHON_BIN" - <<'PY'
import json
import os
print(1 if json.load(open(os.environ["PROVENANCE_FILE"], encoding="utf-8"))["dirty"] else 0)
PY
)"
if [ "$PRODUCTION_RELEASE" = "1" ] && [ "$source_dirty" = "1" ] && [ "$ALLOW_DIRTY_RELEASE" != "1" ]; then
    echo "production release source is dirty; commit it or set ALLOW_DIRTY_RELEASE=1 explicitly" >&2
    exit 1
fi
"$PYTHON_BIN" "$PROJECT_DIR/deploy/release_tool.py" stage \
    --project "$PROJECT_DIR" --destination "$staged_project" \
    --output "$work_dir/source-staged.json" >/dev/null
"$PYTHON_BIN" "$PROJECT_DIR/deploy/release_tool.py" stage-content-bundles \
    --project "$PROJECT_DIR" --destination "$staged_project" \
    --output "$work_dir/content-bundles.json" >/dev/null

build_started_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
if [ "$FRONTEND_DEPENDENCY_MODE" = "linked" ]; then
    DEPENDENCY_SOURCE_ROOT="$PROJECT_DIR" FRONTEND_DEPENDENCY_MODE=linked \
        FRONTEND_BUDGET_OUTPUT="$work_dir/frontend-budget.json" \
        "$PROJECT_DIR/deploy/build_frontend_release.sh" \
        "$staged_project" "$frontend_output" "$BUILD_ID" >&2
else
    FRONTEND_DEPENDENCY_MODE=ci FRONTEND_BUDGET_OUTPUT="$work_dir/frontend-budget.json" \
        "$PROJECT_DIR/deploy/build_frontend_release.sh" \
        "$staged_project" "$frontend_output" "$BUILD_ID" >&2
fi
build_finished_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
"$PYTHON_BIN" "$PROJECT_DIR/deploy/release_tool.py" snapshot \
    --project "$staged_project" --output "$work_dir/source-staged-after.json" >/dev/null

assemble_args=(
    assemble
    --staged-project "$staged_project"
    --frontend-dist "$frontend_output"
    --release-dir "$release_staging"
    --quality-metadata "$QUALITY_METADATA"
    --content-bundles "$work_dir/content-bundles.json"
    --frontend-budget "$work_dir/frontend-budget.json"
    --python-runtime-dir "$PYTHON_RUNTIME_DIR"
    --python-runtime-manifest "$PYTHON_RUNTIME_MANIFEST"
    --python-runtime-root "$PYTHON_RUNTIME_ROOT"
    --output "$work_dir/assembly.json"
)
if [ "$ALLOW_UNVERIFIED_RELEASE" = "1" ]; then
    assemble_args+=(--allow-unverified)
fi
if [ "$PRODUCTION_RELEASE" = "1" ]; then
    assemble_args+=(--production)
fi
"$PYTHON_BIN" "$PROJECT_DIR/deploy/release_tool.py" "${assemble_args[@]}" >/dev/null

"$PYTHON_BIN" "$PROJECT_DIR/deploy/release_tool.py" snapshot \
    --project "$PROJECT_DIR" --output "$work_dir/source-after.json" >/dev/null
"$PYTHON_BIN" "$PROJECT_DIR/deploy/release_tool.py" provenance \
    --project "$PROJECT_DIR" --output "$work_dir/source-provenance-after.json" >/dev/null
if ! cmp -s "$work_dir/source-provenance-before.json" "$work_dir/source-provenance-after.json"; then
    echo "git provenance changed during release build" >&2
    exit 1
fi

finalize_args=(
    finalize
    --release-dir "$release_staging"
    --assembly-metadata "$work_dir/assembly.json"
    --source-before "$work_dir/source-before.json"
    --source-staged "$work_dir/source-staged.json"
    --source-staged-after "$work_dir/source-staged-after.json"
    --source-after "$work_dir/source-after.json"
    --build-id "$BUILD_ID"
    --git-sha "$GIT_SHA"
    --dependency-mode "$FRONTEND_DEPENDENCY_MODE"
    --build-started-at "$build_started_at"
    --build-finished-at "$build_finished_at"
    --provenance "$work_dir/source-provenance-before.json"
)
if [ "$source_dirty" = "1" ]; then
    finalize_args+=(--source-dirty)
    if [ "$ALLOW_DIRTY_RELEASE" = "1" ]; then
        finalize_args+=(--dirty-override)
    fi
fi
if [ "$PRODUCTION_RELEASE" = "1" ]; then
    finalize_args+=(--production)
fi
"$PYTHON_BIN" "$PROJECT_DIR/deploy/release_tool.py" "${finalize_args[@]}" >/dev/null

mv "$release_staging" "$target"
trap - EXIT
cleanup
echo "$target"
