#!/bin/bash
# Web 生产服务启动脚本（一体部署） — 在服务器上运行
#
# 使用方法:
#   bash /root/data/start_web_prod.sh          # 启动
#   bash /root/data/start_web_prod.sh stop     # 停止
#   bash /root/data/start_web_prod.sh restart  # 重启

set -euo pipefail
umask 027

PROJECT_DIR="${PROJECT_DIR:-/root/data/globemind}"
INSTANCE="${INSTANCE:-production}"
HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-8088}"
APP_ENV="${APP_ENV:-production}"
APP_VERSION="${APP_VERSION:-}"
GIT_SHA="${GIT_SHA:-}"
BUILD_ID="${BUILD_ID:-}"
WEB_WORKERS="${WEB_WORKERS:-4}"
export PYTHONDONTWRITEBYTECODE=1
WARMUP_ROUNDS="${WARMUP_ROUNDS:-$((WEB_WORKERS * 3))}"
STOP_TIMEOUT_SEC="${STOP_TIMEOUT_SEC:-30}"
DB_POOL_SIZE="${DB_POOL_SIZE:-3}"
DB_MAX_OVERFLOW="${DB_MAX_OVERFLOW:-2}"
DB_POOL_TIMEOUT="${DB_POOL_TIMEOUT:-30}"
PGOPTIONS="${PGOPTIONS:--c max_parallel_workers_per_gather=0}"
WEB_RUNTIME_DIR="/root/data/web"
PID_DIR="${WEB_RUNTIME_DIR}/pids"
LOG_DIR="${WEB_RUNTIME_DIR}/logs"
if [ "$INSTANCE" = "production" ]; then
    PID_FILE="${PID_DIR}/globemind_web_prod.pid"
    LOG_FILE="${LOG_DIR}/globemind_web_prod.log"
else
    PID_FILE="${PID_DIR}/globemind_web_${INSTANCE}.pid"
    LOG_FILE="${LOG_DIR}/globemind_web_${INSTANCE}.log"
fi
PID_META_FILE="${PID_FILE}.meta"
LOCK_FILE="${PID_FILE}.lock"
LEGACY_PID_LINK="/root/data/web_prod.pid"
LEGACY_LOG_LINK="/root/data/web_prod.log"
VERIFY_PYTHON="${VERIFY_PYTHON:-/usr/bin/python3}"
PYTHON_BIN=""
PYTHON_RUNTIME_ROOT="${PYTHON_RUNTIME_ROOT:-/root/data/python-runtimes/globemind-web}"
PYTHON_RUNTIME_DIR="${PYTHON_RUNTIME_DIR:-}"
PYTHON_RUNTIME_MANIFEST="${PYTHON_RUNTIME_MANIFEST:-}"
LEGACY_PYTHON_BIN="${LEGACY_PYTHON_BIN:-}"
FRONTEND_DIST="${FRONTEND_DIST:-}"
RELEASE_ROOT="${RELEASE_ROOT:-/root/data/releases/globemind}"
PROMOTION_LOCK_FILE="${RELEASE_ROOT}/.promotion.lock"
PROMOTION_LOCK_FD="${GLOBEMIND_PROMOTION_LOCK_FD:-}"
GLOBEMIND_ENV_FILE="${GLOBEMIND_ENV_FILE:-$PROJECT_DIR/backend/api/.env}"
GLOBEMIND_ENV_FILES="${GLOBEMIND_ENV_FILES:-}"
GLOBEMIND_GENERATED_ASSET_ROOT="${GLOBEMIND_GENERATED_ASSET_ROOT:-/root/data/web/generated-assets}"
GLOBEMIND_FRONTEND_PUBLIC_ROOT="${GLOBEMIND_FRONTEND_PUBLIC_ROOT:-}"
HERMES_IMAGE_SCRIPT="${HERMES_IMAGE_SCRIPT:-}"
ALLOW_LEGACY_RELEASE="${ALLOW_LEGACY_RELEASE:-0}"
RELEASE_DIR=""

if [ -z "$GLOBEMIND_ENV_FILES" ]; then
    for candidate in \
        "$PROJECT_DIR/backend/api/.env" \
        "$PROJECT_DIR/backend/agentic_rag/.env" \
        "$PROJECT_DIR/.env"
    do
        if [ -f "$candidate" ]; then
            if [ -n "$GLOBEMIND_ENV_FILES" ]; then
                GLOBEMIND_ENV_FILES+=":"
            fi
            GLOBEMIND_ENV_FILES+="$candidate"
        fi
    done
fi

cd "$PROJECT_DIR"

if [[ ! "$INSTANCE" =~ ^[A-Za-z0-9._-]+$ ]] || [[ ! "$PORT" =~ ^[0-9]+$ ]]; then
    echo "INSTANCE or PORT contains unsupported characters"
    exit 1
fi
if [ "$APP_ENV" != "production" ]; then
    echo "生产启动脚本要求 APP_ENV=production，拒绝当前值: $APP_ENV"
    exit 1
fi
if [ ! -x "$VERIFY_PYTHON" ]; then
    echo "发布校验 Python 不可执行: $VERIFY_PYTHON"
    exit 1
fi

prepare_runtime_paths() {
    mkdir -p "$PID_DIR" "$LOG_DIR"
    chmod 750 "$WEB_RUNTIME_DIR" "$PID_DIR" "$LOG_DIR"
}

validate_promotion_lock_fd() {
    local fd="$1" actual expected metadata file_type owner mode links
    [[ "$fd" =~ ^[0-9]+$ ]] && [ "$fd" -ge 3 ] || {
        echo "生产晋级锁描述符无效"
        return 1
    }
    [ -e "/proc/$$/fd/$fd" ] || {
        echo "生产晋级锁描述符未继承: $fd"
        return 1
    }
    actual="$(readlink -f "/proc/$$/fd/$fd" 2>/dev/null || true)"
    expected="$(realpath -m "$PROMOTION_LOCK_FILE")"
    [ "$actual" = "$expected" ] || {
        echo "生产晋级锁描述符指向非受管路径"
        return 1
    }
    metadata="$(stat -Lc '%F|%u|%a|%h' "/proc/$$/fd/$fd" 2>/dev/null || true)"
    IFS='|' read -r file_type owner mode links <<< "$metadata"
    { [ "$file_type" = "regular file" ] || [ "$file_type" = "regular empty file" ]; } && \
        [ "$owner" = "$(id -u)" ] && \
        [[ "$mode" =~ ^[0-7]+$ ]] && [ $((8#$mode & 022)) -eq 0 ] && \
        [ "$links" = "1" ] || {
        echo "生产晋级锁所有权或权限不安全"
        return 1
    }
    if ! flock -n "$fd"; then
        echo "继承的描述符未持有生产晋级锁"
        return 1
    fi
}

acquire_promotion_lock() {
    [ "$INSTANCE" = "production" ] || return 0
    local release_root_resolved release_root_owner release_root_mode
    release_root_resolved="$(realpath -e "$RELEASE_ROOT" 2>/dev/null || true)"
    [ -n "$release_root_resolved" ] && [ -d "$release_root_resolved" ] || {
        echo "生产发布根目录不存在: $RELEASE_ROOT"
        return 1
    }
    release_root_owner="$(stat -Lc '%u' "$release_root_resolved")"
    release_root_mode="$(stat -Lc '%a' "$release_root_resolved")"
    [ "$release_root_owner" = "$(id -u)" ] && \
        [ $((8#$release_root_mode & 022)) -eq 0 ] || {
        echo "生产发布根目录所有权或权限不安全: $release_root_resolved"
        return 1
    }
    RELEASE_ROOT="$release_root_resolved"
    PROMOTION_LOCK_FILE="${RELEASE_ROOT}/.promotion.lock"

    if [ -n "$PROMOTION_LOCK_FD" ]; then
        validate_promotion_lock_fd "$PROMOTION_LOCK_FD"
        return
    fi
    if [ -L "$PROMOTION_LOCK_FILE" ]; then
        echo "生产晋级锁不能是符号链接: $PROMOTION_LOCK_FILE"
        return 1
    fi
    exec 8>>"$PROMOTION_LOCK_FILE"
    chmod 600 "$PROMOTION_LOCK_FILE"
    if ! flock -w 10 8; then
        echo "另一个生产晋级或直接启停操作正在进行"
        return 1
    fi
    PROMOTION_LOCK_FD=8
    validate_promotion_lock_fd "$PROMOTION_LOCK_FD"
}

update_legacy_links() {
    if [ "$INSTANCE" = "production" ]; then
        ln -sfn "$PID_FILE" "$LEGACY_PID_LINK"
        ln -sfn "$LOG_FILE" "$LEGACY_LOG_LINK"
    fi
}

normalize_canonical_log() {
    local tmp link_target
    case "$LOG_FILE" in
        "$LOG_DIR"/*) ;;
        *)
            echo "日志路径不在受管目录内，拒绝规范化: $LOG_FILE"
            return 1
            ;;
    esac
    if [ -L "$LOG_FILE" ]; then
        link_target="$(readlink "$LOG_FILE")"
        tmp="$(mktemp "${LOG_DIR}/.${INSTANCE}.log.XXXXXX")"
        : > "$tmp"
        chmod 640 "$tmp"
        # Rename replaces only the link itself. The historical target is retained.
        mv -fT "$tmp" "$LOG_FILE"
        echo "已将历史日志链接规范化为常规文件，原目标保持不变: $link_target"
    elif [ -e "$LOG_FILE" ] && [ ! -f "$LOG_FILE" ]; then
        echo "日志路径不是常规文件，拒绝启动: $LOG_FILE"
        return 1
    elif [ ! -e "$LOG_FILE" ]; then
        tmp="$(mktemp "${LOG_DIR}/.${INSTANCE}.log.XXXXXX")"
        : > "$tmp"
        chmod 640 "$tmp"
        mv -fT "$tmp" "$LOG_FILE"
    else
        chmod 640 "$LOG_FILE"
    fi
}

process_start_ticks() {
    local pid="$1"
    awk '{print $22}' "/proc/${pid}/stat" 2>/dev/null || true
}

process_is_instance() {
    [ -s "$PID_FILE" ] && [ -s "$PID_META_FILE" ] || return 1
    local pid meta_pid meta_ticks meta_port meta_instance current_ticks cmdline
    pid="$(cat "$PID_FILE" 2>/dev/null || true)"
    read -r meta_pid meta_ticks meta_port meta_instance < "$PID_META_FILE" || return 1
    [[ "$pid" =~ ^[0-9]+$ ]] || return 1
    [ "$pid" = "$meta_pid" ] && [ "$meta_port" = "$PORT" ] && [ "$meta_instance" = "$INSTANCE" ] || return 1
    kill -0 "$pid" 2>/dev/null || return 1
    current_ticks="$(process_start_ticks "$pid")"
    [ -n "$current_ticks" ] && [ "$current_ticks" = "$meta_ticks" ] || return 1
    cmdline="$(tr '\0' ' ' < "/proc/${pid}/cmdline" 2>/dev/null || true)"
    [[ "$cmdline" == *"backend/serve_prod.py"* ]] || return 1
}

write_pid_identity() {
    local pid="$1" ticks tmp
    ticks="$(process_start_ticks "$pid")"
    [ -n "$ticks" ] || return 1
    tmp="$(mktemp "${PID_META_FILE}.XXXXXX")"
    printf '%s %s %s %s\n' "$pid" "$ticks" "$PORT" "$INSTANCE" > "$tmp"
    chmod 640 "$tmp"
    mv -f "$tmp" "$PID_META_FILE"
}

clear_pid_identity() {
    rm -f "$PID_FILE" "$PID_META_FILE"
}

stop_verified_process() {
    local pid deadline
    if ! process_is_instance; then
        return 1
    fi
    pid="$(cat "$PID_FILE")"
    kill -TERM -- "-$pid" 2>/dev/null || kill -TERM "$pid" 2>/dev/null || true
    deadline=$((SECONDS + STOP_TIMEOUT_SEC))
    while kill -0 "$pid" 2>/dev/null && [ "$SECONDS" -lt "$deadline" ]; do
        sleep 1
    done
    if kill -0 "$pid" 2>/dev/null; then
        echo "服务未在 ${STOP_TIMEOUT_SEC}s 内退出，终止已核验的进程组 (PID: $pid)"
        kill -KILL -- "-$pid" 2>/dev/null || kill -KILL "$pid" 2>/dev/null || true
        for _ in $(seq 1 50); do
            kill -0 "$pid" 2>/dev/null || break
            sleep 0.1
        done
    fi
    clear_pid_identity
    return 0
}

validate_frontend_release() {
    if [ -z "$FRONTEND_DIST" ]; then
        echo "必须显式指定不可变 FRONTEND_DIST"
        return 1
    fi
    local resolved release_root_resolved
    resolved="$(realpath -e "$FRONTEND_DIST" 2>/dev/null || true)"
    release_root_resolved="$(realpath -e "$RELEASE_ROOT" 2>/dev/null || true)"
    if [ -z "$resolved" ] || [ -z "$release_root_resolved" ]; then
        echo "前端发布目录不存在: $FRONTEND_DIST"
        return 1
    fi
    case "${resolved}/" in
        "${release_root_resolved}/"*/frontend-dist/) ;;
        *)
            echo "生产前端必须位于 ${release_root_resolved}/<build>/frontend-dist: $resolved"
            return 1
            ;;
    esac
    FRONTEND_DIST="$resolved"
    RELEASE_DIR="$(dirname "$FRONTEND_DIST")"
    local env_path env_mode env_owner expected_owner
    local -a environment_files
    expected_owner="$(id -u)"
    if [ -z "$GLOBEMIND_ENV_FILES" ]; then
        echo "没有可用的生产配置文件"
        return 1
    fi
    IFS=':' read -r -a environment_files <<< "$GLOBEMIND_ENV_FILES"
    for env_path in "${environment_files[@]}" "$GLOBEMIND_ENV_FILE"; do
        if [ ! -f "$env_path" ]; then
            echo "生产配置文件不存在: $env_path"
            return 1
        fi
        env_mode="$(stat -c '%a' "$env_path")"
        env_owner="$(stat -c '%u' "$env_path")"
        if [ $((8#$env_mode & 077)) -ne 0 ] || [ "$env_owner" != "$expected_owner" ]; then
            echo "生产配置文件必须为当前用户所有且禁止 group/other 访问: $env_path mode=$env_mode owner=$env_owner"
            return 1
        fi
    done
    if [ ! -f "$RELEASE_DIR/release.json" ] || [ ! -f "$RELEASE_DIR/SHA256SUMS" ] || [ ! -f "$RELEASE_DIR/backend/serve_prod.py" ]; then
        echo "发布缺少 release.json、SHA256SUMS 或后端入口: $RELEASE_DIR"
        return 1
    fi
    local manifest_version manifest_build_id manifest_git_sha manifest_schema manifest_runtime_version
    read -r manifest_version manifest_build_id manifest_git_sha manifest_schema manifest_runtime_version < <(
        "$VERIFY_PYTHON" - "$RELEASE_DIR/release.json" <<'PY'
import json
import sys
from pathlib import Path

manifest = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
values = (
    str(manifest.get("version") or ""),
    str(manifest.get("build_id") or ""),
    str(manifest.get("git_sha") or ""),
    str(manifest.get("schema_version") or ""),
)
if any(not value or any(char.isspace() for char in value) for value in values):
    raise SystemExit("release manifest identity is invalid")
runtime_version = str((manifest.get("python_runtime") or {}).get("version") or "-")
if any(char.isspace() for char in runtime_version):
    raise SystemExit("release Python runtime identity is invalid")
print(*values, runtime_version)
PY
    )
    if [ -n "$APP_VERSION" ] && [ "$APP_VERSION" != "$manifest_version" ]; then
        echo "发布版本不匹配: expected=$APP_VERSION actual=$manifest_version"
        return 1
    fi
    if [ -n "$BUILD_ID" ] && [ "$BUILD_ID" != "$manifest_build_id" ]; then
        echo "发布 build id 不匹配: expected=$BUILD_ID actual=$manifest_build_id"
        return 1
    fi
    if [ -n "$GIT_SHA" ] && [ "$GIT_SHA" != "$manifest_git_sha" ]; then
        echo "发布 git sha 不匹配: expected=$GIT_SHA actual=$manifest_git_sha"
        return 1
    fi
    APP_VERSION="$manifest_version"
    BUILD_ID="$manifest_build_id"
    GIT_SHA="$manifest_git_sha"
    GLOBEMIND_FRONTEND_PUBLIC_ROOT="$FRONTEND_DIST"
    local expected_image_script generated_root_resolved
    expected_image_script="$RELEASE_DIR/backend/cppt/ppt-master/skills/ppt-master/scripts/image_gen.py"
    if [ -n "$HERMES_IMAGE_SCRIPT" ] && [ "$(realpath -m "$HERMES_IMAGE_SCRIPT")" != "$expected_image_script" ]; then
        echo "HERMES_IMAGE_SCRIPT 必须来自当前不可变发布: $expected_image_script"
        return 1
    fi
    HERMES_IMAGE_SCRIPT="$expected_image_script"
    if [ ! -f "$HERMES_IMAGE_SCRIPT" ]; then
        echo "发布缺少图片生成运行时代码: $HERMES_IMAGE_SCRIPT"
        return 1
    fi
    generated_root_resolved="$(realpath -m "$GLOBEMIND_GENERATED_ASSET_ROOT")"
    case "${generated_root_resolved}/" in
        "${RELEASE_DIR}/"*)
            echo "生成资产目录不得位于不可变发布内: $generated_root_resolved"
            return 1
            ;;
    esac
    GLOBEMIND_GENERATED_ASSET_ROOT="$generated_root_resolved"
    mkdir -p "$GLOBEMIND_GENERATED_ASSET_ROOT/imgs/hermes-generated"
    chmod 750 "$GLOBEMIND_GENERATED_ASSET_ROOT" \
        "$GLOBEMIND_GENERATED_ASSET_ROOT/imgs" \
        "$GLOBEMIND_GENERATED_ASSET_ROOT/imgs/hermes-generated"
    case "$manifest_schema" in
        3)
            if [ "$manifest_runtime_version" = "-" ]; then
                echo "schema v3 发布缺少 Python runtime 版本证明"
                return 1
            fi
            if [ -z "$PYTHON_RUNTIME_DIR" ]; then
                PYTHON_RUNTIME_DIR="${PYTHON_RUNTIME_ROOT}/${manifest_runtime_version}"
            fi
            if [ -z "$PYTHON_RUNTIME_MANIFEST" ]; then
                PYTHON_RUNTIME_MANIFEST="${PYTHON_RUNTIME_DIR}/inventory/runtime.json"
            fi
            if ! "$VERIFY_PYTHON" "$PROJECT_DIR/deploy/verify_release.py" "$RELEASE_DIR" \
                --production --expected-version "$APP_VERSION" \
                --expected-build-id "$BUILD_ID" --expected-git-sha "$GIT_SHA" \
                --python-runtime-dir "$PYTHON_RUNTIME_DIR" \
                --python-runtime-manifest "$PYTHON_RUNTIME_MANIFEST" \
                --python-runtime-root "$PYTHON_RUNTIME_ROOT"; then
                echo "schema v3 发布或 Python runtime 独立校验失败: $RELEASE_DIR"
                return 1
            fi
            PYTHON_RUNTIME_DIR="$(realpath -e "$PYTHON_RUNTIME_DIR")"
            PYTHON_RUNTIME_MANIFEST="$(realpath -e "$PYTHON_RUNTIME_MANIFEST")"
            PYTHON_BIN="$PYTHON_RUNTIME_DIR/bin/python"
            return 0
            ;;
        2|1)
            if [ "$ALLOW_LEGACY_RELEASE" != "1" ]; then
                echo "schema v${manifest_schema} 发布仅允许显式 ALLOW_LEGACY_RELEASE=1 的紧急回滚"
                return 1
            fi
            if [ -z "$LEGACY_PYTHON_BIN" ] || [ ! -x "$LEGACY_PYTHON_BIN" ]; then
                echo "legacy 回滚必须显式提供 LEGACY_PYTHON_BIN"
                return 1
            fi
            if ! ALLOW_LEGACY_RELEASE=1 "$VERIFY_PYTHON" \
                "$PROJECT_DIR/deploy/verify_release.py" "$RELEASE_DIR" \
                --production --expected-version "$APP_VERSION" \
                --expected-build-id "$BUILD_ID" --expected-git-sha "$GIT_SHA"; then
                echo "legacy schema v${manifest_schema} 发布全量校验失败: $RELEASE_DIR"
                return 1
            fi
            PYTHON_BIN="$(realpath -e "$LEGACY_PYTHON_BIN")"
            return 0
            ;;
        *)
            echo "不支持的 release manifest schema: $manifest_schema"
            return 1
            ;;
    esac
}

start_service() {
    prepare_runtime_paths
    if process_is_instance; then
        echo "服务已在运行中 (PID: $(cat $PID_FILE))"
        return
    fi
    if [ -e "$PID_FILE" ] || [ -e "$PID_META_FILE" ]; then
        echo "清理无法验证的陈旧实例元数据，不会向其中 PID 发送信号"
        clear_pid_identity
    fi

    echo "校验不可变发布和角色化 Python runtime..."
    if ! validate_frontend_release; then
        return 1
    fi
    if [ -z "$PYTHON_BIN" ] || [ ! -x "$PYTHON_BIN" ]; then
        echo "发布校验未选择有效 Python runtime"
        return 1
    fi

    if ss -ltnH "sport = :${PORT}" | grep -q .; then
        echo "端口已被占用，拒绝启动: $PORT"
        return 1
    fi

    if ! normalize_canonical_log; then
        return 1
    fi
    update_legacy_links

    echo "启动生产服务 (端口 $PORT)..."
    : > "$LOG_FILE"
    export HOST PORT WEB_WORKERS PYTHONDONTWRITEBYTECODE DB_POOL_SIZE DB_MAX_OVERFLOW DB_POOL_TIMEOUT PGOPTIONS APP_ENV APP_VERSION GIT_SHA BUILD_ID FRONTEND_DIST GLOBEMIND_ENV_FILE GLOBEMIND_ENV_FILES GLOBEMIND_GENERATED_ASSET_ROOT GLOBEMIND_FRONTEND_PUBLIC_ROOT HERMES_IMAGE_SCRIPT RELEASE_DIR PYTHON_RUNTIME_DIR PYTHON_RUNTIME_MANIFEST
    (
        cd "$RELEASE_DIR"
        export PYTHONPATH="$RELEASE_DIR/backend:$RELEASE_DIR"
        # The controller owns the promotion lock, never the long-lived Web
        # master. Keeping this descriptor open in the child would make every
        # later promotion fail even after the controller exits.
        if [[ "$PROMOTION_LOCK_FD" =~ ^[0-9]+$ ]] && [ "$PROMOTION_LOCK_FD" -ge 3 ]; then
            inherited_promotion_lock_fd="$PROMOTION_LOCK_FD"
            exec {inherited_promotion_lock_fd}>&-
        fi
        exec setsid "$PYTHON_BIN" backend/serve_prod.py >> "$LOG_FILE" 2>&1 < /dev/null 9>&-
    ) &
    service_pid="$!"
    printf '%s\n' "$service_pid" > "$PID_FILE"
    chmod 640 "$PID_FILE"
    if ! write_pid_identity "$service_pid"; then
        kill -TERM -- "-$service_pid" 2>/dev/null || true
        clear_pid_identity
        return 1
    fi
    sleep 3

    if kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
        ready=0
        for _ in $(seq 1 30); do
            health_json="$(curl -fsS "http://127.0.0.1:${PORT}/api/health/ready" 2>/dev/null || true)"
            if [ -n "$health_json" ] && HEALTH_JSON="$health_json" EXPECTED_BUILD_ID="$BUILD_ID" "$PYTHON_BIN" - <<'PY'
import json
import os
payload = json.loads(os.environ["HEALTH_JSON"])
raise SystemExit(0 if payload.get("ready") is True and payload.get("release", {}).get("build_id") == os.environ["EXPECTED_BUILD_ID"] else 1)
PY
            then
                ready=1
                break
            fi
            sleep 1
        done
        if [ "$ready" -ne 1 ]; then
            echo "服务未通过 readiness，停止候选进程并保留日志: $LOG_FILE"
            stop_verified_process || true
            return 1
        fi
        echo "服务已启动 (PID: $(cat $PID_FILE))"
        (
            sleep 2
            for _ in $(seq 1 "$WARMUP_ROUNDS"); do
                (
                    curl -fsS "http://127.0.0.1:${PORT}/" >/dev/null 2>&1 || true
                    curl -fsS "http://127.0.0.1:${PORT}/api/dashboard/search/options" >/dev/null 2>&1 || true
                    curl -fsS "http://127.0.0.1:${PORT}/api/dashboard/stats" >/dev/null 2>&1 || true
                    curl -fsS "http://127.0.0.1:${PORT}/api/story-graph/ground-news/list?page_size=24&min_articles=2&include_first_detail=true" >/dev/null 2>&1 || true
                    curl -fsS "http://127.0.0.1:${PORT}/api/opinion/quality" >/dev/null 2>&1 || true
                    curl -fsS "http://127.0.0.1:${PORT}/api/financial/dashboard" >/dev/null 2>&1 || true
                ) &
            done
            wait
        ) 9>&- &
    else
        echo "服务启动失败，查看日志: tail -f $LOG_FILE"
        clear_pid_identity
        return 1
    fi
}

stop_service() {
    prepare_runtime_paths
    if process_is_instance; then
        PID="$(cat "$PID_FILE")"
        stop_verified_process
        echo "服务已停止 (PID: $PID)"
    elif [ -e "$PID_FILE" ] || [ -e "$PID_META_FILE" ]; then
        echo "实例 PID 元数据无法验证；为避免误杀，不发送信号，仅清理陈旧记录"
        clear_pid_identity
    else
        echo "服务未运行（无实例 PID 文件: $PID_FILE）"
    fi
}

prepare_runtime_paths
acquire_promotion_lock
exec 9>"$LOCK_FILE"
if ! flock -w 10 9; then
    echo "另一个 ${INSTANCE} 实例管理操作正在进行"
    exit 1
fi

case "${1:-start}" in
    start)   start_service ;;
    stop)    stop_service ;;
    restart) stop_service; sleep 1; start_service ;;
    *)       echo "用法: $0 {start|stop|restart}"; exit 2 ;;
esac
