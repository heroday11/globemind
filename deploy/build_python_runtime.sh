#!/usr/bin/env bash
set -euo pipefail
umask 027
export PYTHONDONTWRITEBYTECODE=1

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"

log() {
    printf '[python-runtime] %s\n' "$*" >&2
}

die() {
    log "ERROR: $*"
    exit 1
}

VERSION_FILE="${PROJECT_ROOT}/VERSION"
RUNTIME_VERSION_RE='^[0-9]+\.[0-9]+\.[0-9]+([-+][0-9A-Za-z.-]+)?$'

validate_runtime_version() {
    local version="$1" source="$2" quoted
    if [[ ! "$version" =~ $RUNTIME_VERSION_RE ]]; then
        printf -v quoted '%q' "$version"
        die "invalid runtime version from ${source}: ${quoted}"
    fi
}

read_project_version() {
    local lines=()
    [ -f "$VERSION_FILE" ] || die "VERSION file is missing: $VERSION_FILE"
    mapfile -t lines < "$VERSION_FILE" || die "cannot read VERSION: $VERSION_FILE"
    [ "${#lines[@]}" -eq 1 ] \
        || die "VERSION must contain exactly one line: $VERSION_FILE"
    validate_runtime_version "${lines[0]}" "$VERSION_FILE"
    printf '%s\n' "${lines[0]}"
}

ROLE="web"
if [ "${RUNTIME_VERSION+x}" = "x" ]; then
    validate_runtime_version "$RUNTIME_VERSION" "RUNTIME_VERSION"
else
    RUNTIME_VERSION="$(read_project_version)"
fi
RUNTIME_ROOT="${RUNTIME_ROOT:-/root/data/python-runtimes/globemind-web}"
TARGET_DIR="${RUNTIME_ROOT}/${RUNTIME_VERSION}"
SOURCE_PYTHON="${SOURCE_PYTHON:-/opt/conda/envs/Globemind_env/bin/python}"
TORCH_PIPELINE_PYTHON="${TORCH_PIPELINE_PYTHON:-${PROJECT_ROOT}/.env_torch/bin/python}"
INPUT_FILE="${PROJECT_ROOT}/requirements/roles/web.in"
LOCK_FILE="${PROJECT_ROOT}/requirements/roles/web.lock"
LOCK_METADATA_FILE="${PROJECT_ROOT}/requirements/roles/web.lock.metadata.json"
BUILD_TOOLS_VERSION="7.5.2"
BUILD_TOOLS_ROOT="${BUILD_TOOLS_ROOT:-/root/data/python-runtimes/.build-tools/pip-tools-${BUILD_TOOLS_VERSION}}"
BUILD_LOCK="${RUNTIME_ROOT}/.build.lock"
SCHEMA_VERSION=1
EXPECTED_PYTHON_VERSION="${EXPECTED_PYTHON_VERSION:-3.11.15}"
RUN_TESTS="${RUN_TESTS:-1}"
STAGING_DIR=""
PROMOTED=0
PROMOTED_IDENTITY=""
TEMP_LOCK_FILE=""

WEB_TESTS=(
    backend/tests/test_web_runtime_role.py
    backend/tests/test_api_auth_boundaries.py
    backend/tests/test_auth_identity_security.py
    backend/tests/test_cc_security.py
    backend/tests/test_database_consumer_inventory.py
    backend/tests/test_database_engine_consolidation.py
    backend/tests/test_db_runtime_config.py
    backend/tests/test_dashboard_feature.py
    backend/tests/test_environment_bootstrap.py
    backend/tests/test_feature_health.py
    backend/tests/test_financial_store_concurrency.py
    backend/tests/test_graph_briefing_feature.py
    backend/tests/test_health_and_schedule.py
    backend/tests/test_http_security.py
    backend/tests/test_identity_security_boundary.py
    backend/tests/test_internal_proxy_hardening.py
    backend/tests/test_identity_feature.py
    backend/tests/test_legacy_auth_env_sanitizer.py
    backend/tests/test_legacy_endpoint_retirement.py
    backend/tests/test_opinion_api.py
    backend/tests/test_ops_monitor_heartbeat.py
    backend/tests/test_ops_monitor_security.py
    backend/tests/test_ops_runtime_catalog.py
    backend/tests/test_runtime_security.py
    backend/tests/test_runtime_service_catalog.py
    backend/tests/test_static_path_security.py
    backend/tests/test_story_graph_feature_boundary.py
    backend/tests/test_story_image_backfill.py
    backend/tests/test_user_api_key_security.py
    backend/tests/test_v11_search_feature.py
    backend/tests/test_workspace_upload_limits.py
)

path_lexists() {
    [ -e "$1" ] || [ -L "$1" ]
}

path_identity() {
    stat -c '%d:%i' -- "$1" 2>/dev/null
}

without_proxy() {
    env -u ALL_PROXY -u all_proxy -u HTTP_PROXY -u http_proxy \
        -u HTTPS_PROXY -u https_proxy -u PIP_PROXY \
        -u PIP_INDEX_URL -u PIP_EXTRA_INDEX_URL -u PIP_TRUSTED_HOST \
        PIP_CONFIG_FILE=/dev/null "$@"
}

cleanup() {
    local exit_code="$?"
    trap - EXIT INT TERM
    if [ -n "$STAGING_DIR" ] && [ -d "$STAGING_DIR" ]; then
        rm -rf -- "$STAGING_DIR"
    fi
    if [ -n "$TEMP_LOCK_FILE" ]; then
        rm -f -- "$TEMP_LOCK_FILE"
    fi
    if [ "$PROMOTED" = "1" ] && [ "$exit_code" -ne 0 ]; then
        local current_identity=""
        current_identity="$(path_identity "$TARGET_DIR" || true)"
        if [ ! -L "$TARGET_DIR" ] && [ -d "$TARGET_DIR" ] \
            && [ -n "$PROMOTED_IDENTITY" ] \
            && [ "$current_identity" = "$PROMOTED_IDENTITY" ]; then
            rm -rf -- "$TARGET_DIR"
        else
            log "leaving target untouched because promoted identity changed: $TARGET_DIR"
        fi
    fi
    exit "$exit_code"
}
trap cleanup EXIT INT TERM

validate_source_python() {
    [ -x "$SOURCE_PYTHON" ] || die "source Python is not executable: $SOURCE_PYTHON"
    "$SOURCE_PYTHON" - "$EXPECTED_PYTHON_VERSION" <<'PY'
import platform
import sys

expected = sys.argv[1]
actual = platform.python_version()
if actual != expected:
    raise SystemExit(f"source Python must be {expected}, got {actual}")
if sys.platform != "linux" or platform.machine() != "x86_64":
    raise SystemExit(f"unsupported platform: {sys.platform}/{platform.machine()}")
PY
}

require_source_files() {
    validate_source_python
    [ -f "$INPUT_FILE" ] || die "role input is missing: $INPUT_FILE"
    [ -f "$LOCK_FILE" ] || die "hashed role lock is missing: $LOCK_FILE"
}

sha256_file() {
    sha256sum "$1" | awk '{print $1}'
}

environment_freeze_hash() {
    local python_bin="$1"
    if [ ! -x "$python_bin" ]; then
        printf 'absent\n'
        return
    fi
    PYTHONDONTWRITEBYTECODE=1 "$python_bin" -m pip --isolated freeze --all 2>/dev/null \
        | LC_ALL=C sort | sha256sum | awk '{print $1}'
}

input_fingerprint() {
    require_source_files
    BUILDER_SHA="$(sha256_file "${BASH_SOURCE[0]}")" \
    INPUT_SHA="$(sha256_file "$INPUT_FILE")" \
    LOCK_SHA="$(sha256_file "$LOCK_FILE")" \
    SOURCE_SHA="$(sha256_file "$SOURCE_PYTHON")" \
    ROLE="$ROLE" VERSION="$RUNTIME_VERSION" SCHEMA_VERSION="$SCHEMA_VERSION" \
    "$SOURCE_PYTHON" - <<'PY'
import hashlib
import json
import os
import platform
import sys
import sysconfig

payload = {
    "schema_version": int(os.environ["SCHEMA_VERSION"]),
    "role": os.environ["ROLE"],
    "version": os.environ["VERSION"],
    "builder_sha256": os.environ["BUILDER_SHA"],
    "input_sha256": os.environ["INPUT_SHA"],
    "lock_sha256": os.environ["LOCK_SHA"],
    "source_python_sha256": os.environ["SOURCE_SHA"],
    "python": platform.python_version(),
    "implementation": platform.python_implementation(),
    "platform": sysconfig.get_platform(),
    "machine": platform.machine(),
    "soabi": sysconfig.get_config_var("SOABI"),
}
encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
print(hashlib.sha256(encoded).hexdigest())
PY
}

write_lock_metadata() {
    local output="$1"
    INPUT_FILE="$INPUT_FILE" LOCK_FILE="$output" PIP_TOOLS_VERSION="$BUILD_TOOLS_VERSION" \
    "$SOURCE_PYTHON" - "$LOCK_METADATA_FILE" <<'PY'
import hashlib
import json
import os
import platform
import sys
import sysconfig
from datetime import datetime, timezone
from pathlib import Path

input_path = Path(os.environ["INPUT_FILE"])
lock_path = Path(os.environ["LOCK_FILE"])
target = Path(sys.argv[1])
payload = {
    "schema_version": 1,
    "role": "web",
    "generated_at": datetime.now(timezone.utc).isoformat(),
    "resolver": "pip-tools backtracking",
    "pip_tools_version": os.environ["PIP_TOOLS_VERSION"],
    "python": platform.python_version(),
    "implementation": platform.python_implementation(),
    "platform": sysconfig.get_platform(),
    "machine": platform.machine(),
    "index_urls": ["https://pypi.org/simple"],
    "direct_artifacts": [
        "https://download.pytorch.org/whl/cpu/torch-2.10.0%2Bcpu-cp311-cp311-manylinux_2_28_x86_64.whl"
    ],
    "input_sha256": hashlib.sha256(input_path.read_bytes()).hexdigest(),
    "lock_sha256": hashlib.sha256(lock_path.read_bytes()).hexdigest(),
    "command": (
        "pip-compile --resolver=backtracking --generate-hashes --allow-unsafe "
        "--index-url=https://pypi.org/simple"
    ),
}
temporary = target.with_name(target.name + ".tmp")
temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
temporary.chmod(0o640)
temporary.replace(target)
PY
}

lock_requirements() {
    validate_source_python
    [ -f "$INPUT_FILE" ] || die "role input is missing: $INPUT_FILE"
    mkdir -p "$(dirname "$BUILD_TOOLS_ROOT")" "$(dirname "$LOCK_METADATA_FILE")"
    chmod 750 "$(dirname "$BUILD_TOOLS_ROOT")"
    if [ ! -x "$BUILD_TOOLS_ROOT/bin/python" ]; then
        "$SOURCE_PYTHON" -m venv --copies "$BUILD_TOOLS_ROOT"
    fi
    without_proxy "$BUILD_TOOLS_ROOT/bin/python" -m pip --isolated install \
        --disable-pip-version-check --no-input --index-url https://pypi.org/simple \
        "pip-tools==${BUILD_TOOLS_VERSION}"
    "$BUILD_TOOLS_ROOT/bin/python" -m pip --isolated check
    TEMP_LOCK_FILE="${LOCK_FILE}.tmp.$$"
    rm -f "$TEMP_LOCK_FILE"
    export CUSTOM_COMPILE_COMMAND="deploy/build_python_runtime.sh lock (pip-tools ${BUILD_TOOLS_VERSION}, backtracking resolver, CPython ${EXPECTED_PYTHON_VERSION}, Linux x86_64)"
    without_proxy "$BUILD_TOOLS_ROOT/bin/pip-compile" "$INPUT_FILE" \
        --output-file "$TEMP_LOCK_FILE" \
        --resolver=backtracking \
        --generate-hashes \
        --allow-unsafe \
        --index-url https://pypi.org/simple \
        --newline=lf \
        --annotation-style=line
    unset CUSTOM_COMPILE_COMMAND
    if grep -Eq '^--(extra-index-url|find-links|trusted-host)([ =]|$)' "$TEMP_LOCK_FILE"; then
        die "generated lock contains a host-level package source override"
    fi
    chmod 640 "$TEMP_LOCK_FILE"
    mv -f "$TEMP_LOCK_FILE" "$LOCK_FILE"
    TEMP_LOCK_FILE=""
    write_lock_metadata "$LOCK_FILE"
    log "wrote hashed lock: $LOCK_FILE"
}

safe_import_closure() {
    local runtime_python="$1" output="$2" validation_root="$3"
    mkdir -p "$validation_root/frontend/assets" "$validation_root/home" "$validation_root/workspace"
    printf '<!doctype html><html><body>runtime-validation</body></html>\n' \
        > "$validation_root/frontend/index.html"
    APP_ENV=test GLOBEMIND_TESTING=1 PYTHON_DOTENV_DISABLED=1 \
    GLOBEMIND_ENV_FILE=/nonexistent GLOBEMIND_ENV_FILES= \
    DATABASE_URL=postgresql://test@127.0.0.1:1/test \
    DB_HOST=127.0.0.1 DB_PORT=1 DB_USER=test DB_PASSWORD= DB_NAME=test \
    FRONTEND_DIST="$validation_root/frontend" \
    GLOBEMIND_TEST_ROOT="$validation_root" GLOBEMIND_ROOT="$validation_root/project" \
    GLOBEMIND_WORKSPACE_ROOT="$validation_root/workspace" \
    ASSISTANT_SCHEDULE_DISABLE=1 HOME="$validation_root/home" \
    CLAUDE_CONFIG_DIR="$validation_root/claude" \
    PYTHONPATH="${PROJECT_ROOT}/backend:${PROJECT_ROOT}" PYTHONDONTWRITEBYTECODE=1 \
    "$runtime_python" - "$output" <<'PY'
from __future__ import annotations

import contextlib
import hashlib
import importlib
import importlib.metadata as metadata
import io
import json
import platform
import sys
from io import BytesIO
from pathlib import Path

critical = (
    "fastapi", "uvicorn", "httpx", "pydantic", "sqlalchemy", "psycopg2",
    "jwt", "bcrypt", "cryptography", "numpy", "pymilvus", "yaml", "openai",
    "anthropic", "requests", "bs4", "PIL", "fitz", "pptx", "cairosvg",
    "reportlab", "svglib", "torch", "transformers", "sentence_transformers",
    "agentic_rag.ingestion.embedder",
)
before = set(sys.modules)
with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
    for name in critical:
        importlib.import_module(name)
    importlib.import_module("api.application")
    importlib.import_module("serve_prod")
    import cairosvg
    import fitz
    from bs4 import BeautifulSoup
    from PIL import Image
    from pptx import Presentation
    from reportlab.pdfgen.canvas import Canvas
    from svglib.svglib import svg2rlg

    svg = b'<svg xmlns="http://www.w3.org/2000/svg" width="8" height="8"><rect width="8" height="8" fill="#336699"/></svg>'
    png = cairosvg.svg2png(bytestring=svg)
    with Image.open(BytesIO(png)) as image:
        assert image.size == (8, 8)
    pdf = BytesIO()
    Canvas(pdf).save()
    assert pdf.getvalue().startswith(b"%PDF")
    drawing = svg2rlg(BytesIO(svg))
    assert drawing is not None
    presentation = Presentation()
    presentation.slides.add_slide(presentation.slide_layouts[6])
    deck = BytesIO()
    presentation.save(deck)
    assert deck.getvalue().startswith(b"PK")
    document = fitz.open()
    document.new_page()
    assert document.page_count == 1
    document.close()
    assert BeautifulSoup("<p>ok</p>", "html.parser").p.text == "ok"
loaded = sorted(set(sys.modules) - before)
package_map = metadata.packages_distributions()
distributions: dict[str, str] = {}
for module_name in loaded:
    top = module_name.split(".", 1)[0]
    for distribution in package_map.get(top, []):
        try:
            distributions[distribution] = metadata.version(distribution)
        except metadata.PackageNotFoundError:
            pass
installed = {dist.metadata["Name"]: dist.version for dist in metadata.distributions() if dist.metadata["Name"]}
for forbidden in ("gliner", "vllm"):
    try:
        metadata.version(forbidden)
    except metadata.PackageNotFoundError:
        continue
    raise SystemExit(f"forbidden role distribution installed: {forbidden}")
if any(name.lower().startswith("nvidia-") for name in installed):
    raise SystemExit("CPU Web role unexpectedly contains NVIDIA runtime packages")
transformers_parts = tuple(int(part) for part in metadata.version("transformers").split(".")[:2])
if transformers_parts >= (5, 2):
    raise SystemExit("transformers must remain below 5.2")
payload = {
    "schema_version": 1,
    "python": platform.python_version(),
    "critical_imports": list(critical),
    "loaded_module_count": len(loaded),
    "capability_smoke": ["html", "image", "pdf", "pptx", "svg"],
    "distributions": dict(sorted(distributions.items(), key=lambda item: item[0].lower())),
}
encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
payload["closure_sha256"] = hashlib.sha256(encoded).hexdigest()
Path(sys.argv[1]).write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
PY
}

run_web_tests() {
    local runtime_python="$1" output="$2"
    if [ "$RUN_TESTS" != "1" ]; then
        printf 'tests explicitly skipped with RUN_TESTS=%s\n' "$RUN_TESTS" > "$output"
        return
    fi
    (
        cd "$PROJECT_ROOT"
        PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="${PROJECT_ROOT}/backend:${PROJECT_ROOT}" \
            "$runtime_python" -m pytest -p no:cacheprovider -q \
            -m 'not live_db and not gpu' "${WEB_TESTS[@]}"
    ) 2>&1 | tee "$output"
}

write_runtime_inventory() {
    local runtime_python="$1" inventory_dir="$2" build_input_fingerprint="$3"
    local freeze_file="$inventory_dir/pip-freeze.txt"
    PYTHONDONTWRITEBYTECODE=1 "$runtime_python" -m pip --isolated freeze --all \
        | LC_ALL=C sort > "$freeze_file"
    chmod 640 "$freeze_file"
    local freeze_sha closure_sha tests_sha
    freeze_sha="$(sha256_file "$freeze_file")"
    closure_sha="$(sha256_file "$inventory_dir/import-closure.json")"
    tests_sha="$(sha256_file "$inventory_dir/pytest-web.log")"
    BUILD_INPUT_FINGERPRINT="$build_input_fingerprint" FREEZE_SHA="$freeze_sha" \
    CLOSURE_SHA="$closure_sha" TESTS_SHA="$tests_sha" TARGET_DIR="$TARGET_DIR" \
    ROLE="$ROLE" VERSION="$RUNTIME_VERSION" LOCK_SHA="$(sha256_file "$LOCK_FILE")" \
    SOURCE_PYTHON="$SOURCE_PYTHON" SOURCE_SHA="$(sha256_file "$SOURCE_PYTHON")" \
    RUN_TESTS="$RUN_TESTS" \
    "$runtime_python" - "$inventory_dir/runtime.json" <<'PY'
import hashlib
import json
import os
import platform
import sys
import sysconfig
from datetime import datetime, timezone
from pathlib import Path

fingerprint_payload = {
    "build_input_fingerprint": os.environ["BUILD_INPUT_FINGERPRINT"],
    "pip_freeze_sha256": os.environ["FREEZE_SHA"],
    "import_closure_sha256": os.environ["CLOSURE_SHA"],
    "pytest_log_sha256": os.environ["TESTS_SHA"],
}
runtime_fingerprint = hashlib.sha256(
    json.dumps(fingerprint_payload, sort_keys=True, separators=(",", ":")).encode()
).hexdigest()
payload = {
    "schema_version": 1,
    "role": os.environ["ROLE"],
    "version": os.environ["VERSION"],
    "created_at": datetime.now(timezone.utc).isoformat(),
    "install_prefix": os.environ["TARGET_DIR"],
    "build_input_fingerprint": os.environ["BUILD_INPUT_FINGERPRINT"],
    "runtime_fingerprint": runtime_fingerprint,
    "python": {
        "version": platform.python_version(),
        "implementation": platform.python_implementation(),
        "platform": sysconfig.get_platform(),
        "machine": platform.machine(),
        "source_executable": os.environ["SOURCE_PYTHON"],
        "source_sha256": os.environ["SOURCE_SHA"],
        "base_prefix": sys.base_prefix,
    },
    "lock_sha256": os.environ["LOCK_SHA"],
    "pip_freeze_sha256": os.environ["FREEZE_SHA"],
    "import_closure_sha256": os.environ["CLOSURE_SHA"],
    "pytest_log_sha256": os.environ["TESTS_SHA"],
    "validation": {
        "pip_check": "pass",
        "critical_imports": "pass",
        "pytest_web": "pass" if os.environ.get("RUN_TESTS", "1") == "1" else "skipped",
    },
    "excluded_distributions": ["gliner", "vllm"],
}
Path(sys.argv[1]).write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
PY
    chmod 640 "$inventory_dir/runtime.json" "$inventory_dir/import-closure.json" \
        "$inventory_dir/pytest-web.log" "$inventory_dir/pip-check.txt"
}

validate_runtime_links() {
    local runtime_dir="$1"
    "$SOURCE_PYTHON" - "$runtime_dir" <<'PY'
from __future__ import annotations

import os
import sys
from pathlib import Path

root_path = Path(sys.argv[1])
if root_path.is_symlink():
    raise SystemExit(f"runtime root must not be a symlink: {root_path}")
root = root_path.resolve(strict=True)
violations: list[str] = []
for directory, directories, files in os.walk(root, followlinks=False):
    base = Path(directory)
    for name in (*directories, *files):
        link = base / name
        if not link.is_symlink():
            continue
        raw_target = Path(os.readlink(link))
        if raw_target.is_absolute():
            violations.append(f"absolute symlink: {link} -> {raw_target}")
            continue
        try:
            resolved = link.resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            violations.append(f"invalid symlink: {link} ({exc})")
            continue
        if not resolved.is_relative_to(root):
            violations.append(f"escaping symlink: {link} -> {raw_target}")
if violations:
    raise SystemExit("\n".join(violations))
PY
}

verify_runtime() {
    local runtime_dir="$1" expected_input="$2"
    [ -d "$runtime_dir" ] || die "runtime directory is missing: $runtime_dir"
    [ -x "$runtime_dir/bin/python" ] || die "runtime Python is missing: $runtime_dir/bin/python"
    [ -f "$runtime_dir/inventory/runtime.json" ] || die "runtime inventory is missing"
    local recorded
    recorded="$($SOURCE_PYTHON - "$runtime_dir/inventory/runtime.json" <<'PY'
import json
import sys
from pathlib import Path
print(json.loads(Path(sys.argv[1]).read_text())["build_input_fingerprint"])
PY
)"
    [ "$recorded" = "$expected_input" ] || die \
        "same version has a different build fingerprint; refusing overwrite"
    PYTHONDONTWRITEBYTECODE=1 "$runtime_dir/bin/python" -m pip --isolated check >/dev/null
    local freeze_tmp
    freeze_tmp="$(mktemp)"
    PYTHONDONTWRITEBYTECODE=1 "$runtime_dir/bin/python" -m pip --isolated freeze --all \
        | LC_ALL=C sort > "$freeze_tmp"
    local expected_freeze actual_freeze
    expected_freeze="$($SOURCE_PYTHON - "$runtime_dir/inventory/runtime.json" <<'PY'
import json,sys
print(json.load(open(sys.argv[1]))["pip_freeze_sha256"])
PY
)"
    actual_freeze="$(sha256_file "$freeze_tmp")"
    rm -f "$freeze_tmp"
    [ "$expected_freeze" = "$actual_freeze" ] || die "installed distribution fingerprint changed"
    validate_runtime_links "$runtime_dir"
    if find "$runtime_dir" -xdev ! -type l -perm /022 -print -quit | grep -q .; then
        die "runtime contains group/world-writable paths"
    fi
}

relocate_console_scripts() {
    local staging="$1" target="$2"
    local file
    while IFS= read -r -d '' file; do
        if grep -IqF "$staging" "$file"; then
            sed -i "s#${staging//\#/\\#}#${target//\#/\\#}#g" "$file"
        fi
    done < <(find "$staging/bin" -maxdepth 1 -type f -print0)
    if grep -IqF "$staging" "$staging/pyvenv.cfg"; then
        sed -i "s#${staging//\#/\\#}#${target//\#/\\#}#g" "$staging/pyvenv.cfg"
    fi
}

promote_candidate() {
    local candidate_identity target_identity
    [ -d "$STAGING_DIR" ] && [ ! -L "$STAGING_DIR" ] \
        || die "candidate is not a real directory: $STAGING_DIR"
    candidate_identity="$(path_identity "$STAGING_DIR")" \
        || die "cannot identify candidate: $STAGING_DIR"
    if path_lexists "$TARGET_DIR"; then
        die "runtime target appeared during build; refusing overwrite: $TARGET_DIR"
    fi
    if ! mv -T --no-clobber -- "$STAGING_DIR" "$TARGET_DIR"; then
        die "atomic runtime promotion failed: $TARGET_DIR"
    fi
    if path_lexists "$STAGING_DIR"; then
        die "runtime target won a promotion race; candidate was not moved"
    fi
    if [ -L "$TARGET_DIR" ] || [ ! -d "$TARGET_DIR" ]; then
        die "promoted target is not the candidate directory: $TARGET_DIR"
    fi
    target_identity="$(path_identity "$TARGET_DIR")" \
        || die "cannot identify promoted target: $TARGET_DIR"
    if [ "$target_identity" != "$candidate_identity" ]; then
        die "promoted target identity does not match the candidate"
    fi
    PROMOTED_IDENTITY="$target_identity"
    PROMOTED=1
    STAGING_DIR=""
}

build_runtime() {
    require_source_files
    mkdir -p "$RUNTIME_ROOT"
    chmod 750 "$RUNTIME_ROOT"
    exec 9> "$BUILD_LOCK"
    chmod 640 "$BUILD_LOCK"
    flock -w 30 9 || die "another Web runtime build is in progress"

    local desired source_before pipeline_before source_after pipeline_after
    desired="$(input_fingerprint)"
    if path_lexists "$TARGET_DIR"; then
        [ ! -L "$TARGET_DIR" ] || die "runtime target must not be a symlink: $TARGET_DIR"
        [ -d "$TARGET_DIR" ] || die "runtime target is not a directory: $TARGET_DIR"
        verify_runtime "$TARGET_DIR" "$desired"
        printf '%s\n' "$TARGET_DIR"
        return
    fi

    source_before="$(environment_freeze_hash "$SOURCE_PYTHON")"
    pipeline_before="$(environment_freeze_hash "$TORCH_PIPELINE_PYTHON")"
    STAGING_DIR="$(mktemp -d "${RUNTIME_ROOT}/.${RUNTIME_VERSION}.build.XXXXXX")"
    log "creating isolated candidate: $STAGING_DIR"
    "$SOURCE_PYTHON" -m venv --copies "$STAGING_DIR"
    without_proxy "$STAGING_DIR/bin/python" -m pip --isolated install \
        --disable-pip-version-check --no-input --require-hashes --no-deps \
        --only-binary=:all: --index-url https://pypi.org/simple -r "$LOCK_FILE"
    "$STAGING_DIR/bin/python" -m pip --isolated check \
        | tee "$STAGING_DIR/pip-check.txt"

    mkdir -p "$STAGING_DIR/inventory"
    mv "$STAGING_DIR/pip-check.txt" "$STAGING_DIR/inventory/pip-check.txt"
    local validation_root="${STAGING_DIR}/.validation"
    safe_import_closure "$STAGING_DIR/bin/python" \
        "$STAGING_DIR/inventory/import-closure.json" "$validation_root"
    run_web_tests "$STAGING_DIR/bin/python" "$STAGING_DIR/inventory/pytest-web.log"
    rm -rf "$validation_root"
    RUN_TESTS="$RUN_TESTS" write_runtime_inventory \
        "$STAGING_DIR/bin/python" "$STAGING_DIR/inventory" "$desired"

    source_after="$(environment_freeze_hash "$SOURCE_PYTHON")"
    pipeline_after="$(environment_freeze_hash "$TORCH_PIPELINE_PYTHON")"
    [ "$source_before" = "$source_after" ] || die "live Web environment changed during build"
    [ "$pipeline_before" = "$pipeline_after" ] || die "pipeline environment changed during build"

    relocate_console_scripts "$STAGING_DIR" "$TARGET_DIR"
    chmod -R go-w "$STAGING_DIR"
    verify_runtime "$STAGING_DIR" "$desired"
    promote_candidate
    verify_runtime "$TARGET_DIR" "$desired"
    "$TARGET_DIR/bin/pip" --version >/dev/null
    "$TARGET_DIR/bin/uvicorn" --version >/dev/null
    PROMOTED=0
    printf '%s\n' "$TARGET_DIR"
}

main() {
    trap cleanup EXIT INT TERM
    local command="${1:-build}"
    case "$command" in
        build)
            build_runtime
            ;;
        fingerprint)
            input_fingerprint
            ;;
        lock)
            mkdir -p "$RUNTIME_ROOT"
            exec 9> "$BUILD_LOCK"
            flock -w 30 9 || die "another Web runtime operation is in progress"
            lock_requirements
            ;;
        verify)
            require_source_files
            verify_runtime "${2:-$TARGET_DIR}" "$(input_fingerprint)"
            printf '%s\n' "${2:-$TARGET_DIR}"
            ;;
        *)
            printf 'usage: %s {build|fingerprint|lock|verify [runtime-dir]}\n' "$0" >&2
            exit 64
            ;;
    esac
}

if [ "${BASH_SOURCE[0]}" = "$0" ]; then
    main "$@"
fi
