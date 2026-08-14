#!/bin/bash
set -euo pipefail
umask 027

if [ "$#" -ne 3 ]; then
    echo "usage: $0 <staged-project> <output-dir> <build-id>" >&2
    exit 2
fi

STAGED_PROJECT="$(realpath -e "$1")"
OUTPUT_DIR="$(realpath -m "$2")"
BUILD_ID="$3"
DEPENDENCY_MODE="${FRONTEND_DEPENDENCY_MODE:-ci}"
DEPENDENCY_SOURCE_ROOT="${DEPENDENCY_SOURCE_ROOT:-}"
FRONTEND_BUDGET_OUTPUT="${FRONTEND_BUDGET_OUTPUT:-$OUTPUT_DIR/.frontend-budget.json}"

case "${OUTPUT_DIR}/" in
    "${STAGED_PROJECT}/"*)
        echo "frontend output must be outside staged source: $OUTPUT_DIR" >&2
        exit 1
        ;;
esac
if [ -e "$OUTPUT_DIR" ]; then
    echo "frontend output must not already exist: $OUTPUT_DIR" >&2
    exit 1
fi
if [[ ! "$BUILD_ID" =~ ^[A-Za-z0-9._-]+$ ]]; then
    echo "build id contains unsupported characters: $BUILD_ID" >&2
    exit 1
fi
if [ "$DEPENDENCY_MODE" != "ci" ] && [ "$DEPENDENCY_MODE" != "linked" ]; then
    echo "FRONTEND_DEPENDENCY_MODE must be ci or linked" >&2
    exit 1
fi

while IFS='=' read -r name _; do
    case "$name" in
        VITE_BUILD_ID|VITE_OUT_DIR) ;;
        VITE_*)
            echo "unexpected VITE_* variable in release environment: $name" >&2
            exit 1
            ;;
    esac
done < <(env)

vue_dir="$STAGED_PROJECT/frontend/vue_project"
financial_dir="$STAGED_PROJECT/frontend/financial-terminal"
for required in "$vue_dir/package-lock.json" "$financial_dir/package-lock.json"; do
    if [ ! -f "$required" ]; then
        echo "missing npm lock file: $required" >&2
        exit 1
    fi
done

cleanup_links() {
    if [ "$DEPENDENCY_MODE" = "linked" ]; then
        rm -f "$vue_dir/node_modules" "$financial_dir/node_modules"
    fi
}
trap cleanup_links EXIT

if [ "$DEPENDENCY_MODE" = "ci" ]; then
    npm ci --no-audit --no-fund --prefix "$vue_dir"
    npm ci --no-audit --no-fund --prefix "$financial_dir"
else
    if [ -z "$DEPENDENCY_SOURCE_ROOT" ]; then
        echo "DEPENDENCY_SOURCE_ROOT is required for linked dependency mode" >&2
        exit 1
    fi
    dependency_root="$(realpath -e "$DEPENDENCY_SOURCE_ROOT")"
    for relative in frontend/vue_project frontend/financial-terminal; do
        source_modules="$dependency_root/$relative/node_modules"
        target_modules="$STAGED_PROJECT/$relative/node_modules"
        if [ ! -d "$source_modules" ]; then
            echo "linked dependency directory does not exist: $source_modules" >&2
            exit 1
        fi
        ln -s "$source_modules" "$target_modules"
    done
fi

mkdir -p "$(dirname "$OUTPUT_DIR")"
NODE_ENV=production VITE_BUILD_ID="$BUILD_ID" \
GLOBEMIND_FRONTEND_OUT_DIR="$OUTPUT_DIR" npm run build --prefix "$vue_dir"

test -f "$OUTPUT_DIR/index.html"
test -f "$OUTPUT_DIR/fin-terminal/index.html"
node "$STAGED_PROJECT/deploy/check_frontend_budgets.mjs" \
    --dist "$OUTPUT_DIR" \
    --config "$STAGED_PROJECT/quality/frontend-budgets.json" \
    --output "$FRONTEND_BUDGET_OUTPUT" >/dev/null
if [ "$FRONTEND_BUDGET_OUTPUT" = "$OUTPUT_DIR/.frontend-budget.json" ]; then
    rm -f "$FRONTEND_BUDGET_OUTPUT"
fi
echo "$OUTPUT_DIR"
