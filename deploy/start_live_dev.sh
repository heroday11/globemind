#!/bin/bash
set -euo pipefail
umask 027

PROJECT_DIR="${PROJECT_DIR:-/root/data/globemind}"
HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-18090}"
WEB_WORKERS="${WEB_WORKERS:-1}"
BUILD_FRONTEND="${BUILD_FRONTEND:-0}"
PYTHON_RUNTIME_DIR="${PYTHON_RUNTIME_DIR:-/root/data/python-runtimes/globemind-web/$(tr -d '\r\n' < "$PROJECT_DIR/VERSION")}"
PYTHON_BIN="${PYTHON_BIN:-$PYTHON_RUNTIME_DIR/bin/python}"
FRONTEND_DIST="${FRONTEND_DIST:-$PROJECT_DIR/frontend/vue_project/dist}"
PID_DIR="${PID_DIR:-/root/data/web/pids}"
LOG_DIR="${LOG_DIR:-/root/data/web/logs}"
PID_FILE="${PID_FILE:-$PID_DIR/globemind_web_live_dev.pid}"
PID_META_FILE="${PID_META_FILE:-$PID_FILE.meta}"
LOG_FILE="${LOG_FILE:-$LOG_DIR/globemind_web_live_dev.log}"
LOCK_FILE="${LOCK_FILE:-$PID_FILE.lock}"

if [ ! -x "$PYTHON_BIN" ]; then
    echo "Python runtime not executable: $PYTHON_BIN" >&2
    exit 1
fi
if [[ ! "$PORT" =~ ^[0-9]+$ ]]; then
    echo "invalid PORT: $PORT" >&2
    exit 1
fi

mkdir -p "$PID_DIR" "$LOG_DIR" /root/data/web/generated-assets-dev
chmod 750 /root/data/web "$PID_DIR" "$LOG_DIR" /root/data/web/generated-assets-dev

process_start_ticks() {
    awk '{print $22}' "/proc/${1}/stat" 2>/dev/null || true
}

process_is_instance() {
    [ -s "$PID_FILE" ] && [ -s "$PID_META_FILE" ] || return 1
    local pid meta_pid meta_ticks meta_port current_ticks cmdline
    pid="$(cat "$PID_FILE" 2>/dev/null || true)"
    read -r meta_pid meta_ticks meta_port < "$PID_META_FILE" || return 1
    [[ "$pid" =~ ^[1-9][0-9]*$ ]] || return 1
    [ "$pid" = "$meta_pid" ] && [ "$meta_port" = "$PORT" ] || return 1
    kill -0 "$pid" 2>/dev/null || return 1
    current_ticks="$(process_start_ticks "$pid")"
    [ -n "$current_ticks" ] && [ "$current_ticks" = "$meta_ticks" ] || return 1
    cmdline="$(tr '\0' ' ' < "/proc/${pid}/cmdline" 2>/dev/null || true)"
    [[ "$cmdline" == *"backend/serve_prod.py"* ]]
}

write_pid_meta() {
    local pid="$1" ticks tmp
    ticks="$(process_start_ticks "$pid")"
    [ -n "$ticks" ] || return 1
    tmp="$(mktemp "${PID_META_FILE}.XXXXXX")"
    printf '%s %s %s\n' "$pid" "$ticks" "$PORT" > "$tmp"
    chmod 640 "$tmp"
    mv -f "$tmp" "$PID_META_FILE"
}

stop_service() {
    if process_is_instance; then
        local pid
        pid="$(cat "$PID_FILE")"
        kill -TERM -- "-$pid" 2>/dev/null || kill -TERM "$pid" 2>/dev/null || true
        for _ in $(seq 1 30); do
            kill -0 "$pid" 2>/dev/null || break
            sleep 1
        done
        if kill -0 "$pid" 2>/dev/null; then
            kill -KILL -- "-$pid" 2>/dev/null || kill -KILL "$pid" 2>/dev/null || true
        fi
        rm -f "$PID_FILE" "$PID_META_FILE"
        echo "live-dev stopped (PID: $pid)"
    else
        rm -f "$PID_FILE" "$PID_META_FILE"
        echo "live-dev is not running"
    fi
}

build_frontend() {
    (cd "$PROJECT_DIR/frontend/vue_project" && npm run build:main-only)
}

start_service() {
    if process_is_instance; then
        echo "live-dev is already running (PID: $(cat "$PID_FILE"))"
        return 0
    fi
    if ss -ltnH "sport = :${PORT}" | grep -q .; then
        echo "port is already occupied: $PORT" >&2
        return 1
    fi
    if [ "$BUILD_FRONTEND" = "1" ]; then
        build_frontend
    fi
    if [ ! -f "$FRONTEND_DIST/index.html" ]; then
        echo "frontend dist missing index.html: $FRONTEND_DIST" >&2
        echo "run: BUILD_FRONTEND=1 $0 start" >&2
        return 1
    fi

    : > "$LOG_FILE"
    chmod 640 "$LOG_FILE"

    export HOST PORT WEB_WORKERS PYTHONDONTWRITEBYTECODE=1
    export APP_ENV=production
    export APP_VERSION=live-dev
    export BUILD_ID="live-dev-$(date -u +%Y%m%dT%H%M%SZ)"
    export GIT_SHA="$(git -C "$PROJECT_DIR" rev-parse --short=12 HEAD 2>/dev/null || echo live-dev)"
    export FRONTEND_DIST
    export GLOBEMIND_FRONTEND_PUBLIC_ROOT="$FRONTEND_DIST"
    export GLOBEMIND_GENERATED_ASSET_ROOT=/root/data/web/generated-assets-dev
    export GLOBEMIND_ENV_FILE="$PROJECT_DIR/backend/api/.env"
    export GLOBEMIND_ENV_FILES="$PROJECT_DIR/backend/api/.env:$PROJECT_DIR/backend/agentic_rag/.env:$PROJECT_DIR/.env"
    export GLOBEMIND_DB_PASSWORD_FILE=/root/data/secrets/globemind/web_runtime.password
    export GLOBEMIND_ALLOW_PRIVATE_SCRAM_TRANSPORT=1
    export DB_USER=web_runtime
    export DB_SSLMODE=disable
    export ALLOW_RUNTIME_SCHEMA_MUTATIONS=0
    export ASSISTANT_SCHEDULE_DISABLE=1
    export PYTHONPATH="$PROJECT_DIR/backend:$PROJECT_DIR:$PROJECT_DIR/backend/cppt"

    (
        cd "$PROJECT_DIR"
        # The controller owns fd 9. Do not let the long-running web process
        # inherit that flock or every later verified restart will time out.
        exec 9>&-
        exec setsid "$PYTHON_BIN" backend/serve_prod.py >> "$LOG_FILE" 2>&1 < /dev/null
    ) &
    local pid="$!"
    printf '%s\n' "$pid" > "$PID_FILE"
    chmod 640 "$PID_FILE"
    write_pid_meta "$pid"

    for _ in $(seq 1 40); do
        if curl --noproxy '*' -fsS "http://${HOST}:${PORT}/api/health/ready" >/dev/null 2>&1; then
            echo "live-dev started: http://${HOST}:${PORT} (PID: $pid)"
            return 0
        fi
        if ! kill -0 "$pid" 2>/dev/null; then
            echo "live-dev failed to start; log: $LOG_FILE" >&2
            rm -f "$PID_FILE" "$PID_META_FILE"
            return 1
        fi
        sleep 1
    done
    echo "live-dev did not become ready; log: $LOG_FILE" >&2
    return 1
}

mkdir -p "$(dirname "$LOCK_FILE")"
exec 9>"$LOCK_FILE"
flock -w 10 9

case "${1:-start}" in
    start) start_service ;;
    stop) stop_service ;;
    restart) stop_service; start_service ;;
    build) build_frontend ;;
    *) echo "usage: $0 {start|stop|restart|build}" >&2; exit 2 ;;
esac
