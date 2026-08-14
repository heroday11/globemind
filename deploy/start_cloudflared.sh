#!/bin/bash
# Cloudflared 启动脚本（用于容器环境，无 systemd）
# 由 cron @reboot 触发

set -euo pipefail
umask 077

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"
}

log "Starting cloudflared tunnel..."

# 确保 credentials 文件存在
if [ ! -f /root/data/cloudflared/credentials.json ]; then
    log "ERROR: credentials.json not found at /root/data/cloudflared/"
    exit 1
fi

# 确保 cert.pem 在标准位置
if [ ! -f /root/.cloudflared/cert.pem ]; then
    mkdir -p /root/.cloudflared
    cp /root/data/cloudflared/cert.pem /root/.cloudflared/cert.pem
    chmod 600 /root/.cloudflared/cert.pem
fi

CLOUDFLARED_BIN="${CLOUDFLARED_BIN:-/root/data/globemind/deploy/cloudflared}"
if [ -z "$CLOUDFLARED_BIN" ]; then
    for candidate in "/usr/local/bin/cloudflared" "/root/data/globemind/deploy/cloudflared" "$(command -v cloudflared 2>/dev/null || true)"; do
        if [ -n "$candidate" ] && [ -x "$candidate" ]; then
            CLOUDFLARED_BIN="$candidate"
            break
        fi
    done
fi

if [ -z "$CLOUDFLARED_BIN" ]; then
    log "ERROR: cloudflared binary not found"
    exit 1
fi
if [ ! -x "$CLOUDFLARED_BIN" ]; then
    log "ERROR: cloudflared binary is not executable: $CLOUDFLARED_BIN"
    exit 1
fi

CLOUDFLARED_PROTOCOL="${CLOUDFLARED_PROTOCOL:-http2}"
CLOUDFLARED_CONFIG="${CLOUDFLARED_CONFIG:-/root/data/cloudflared/config.yml}"
CLOUDFLARED_LOG="${CLOUDFLARED_LOG:-/root/data/cloudflared/tunnel-v092.log}"
CLOUDFLARED_METRICS="${CLOUDFLARED_METRICS:-127.0.0.1:20242}"
CLOUDFLARED_PID_FILE="${CLOUDFLARED_PID_FILE:-/root/data/cloudflared/cloudflared-v092.pid}"
CLOUDFLARED_PID_META_FILE="${CLOUDFLARED_PID_META_FILE:-${CLOUDFLARED_PID_FILE}.meta}"
CLOUDFLARED_LOCK_FILE="${CLOUDFLARED_LOCK_FILE:-${CLOUDFLARED_PID_FILE}.lock}"
log "Using cloudflared binary: $CLOUDFLARED_BIN"

if [[ ! "$CLOUDFLARED_METRICS" =~ ^127\.0\.0\.1:([0-9]{1,5})$ ]] \
    || [ "${BASH_REMATCH[1]}" -lt 1 ] \
    || [ "${BASH_REMATCH[1]}" -gt 65535 ]; then
    log "ERROR: metrics endpoint must be a valid loopback address: $CLOUDFLARED_METRICS"
    exit 1
fi

process_start_ticks() {
    local pid="$1"
    awk '{print $22}' "/proc/${pid}/stat" 2>/dev/null || true
}

process_is_declared_tunnel() {
    [ -s "$CLOUDFLARED_PID_FILE" ] && [ -s "$CLOUDFLARED_PID_META_FILE" ] || return 1
    local pid meta_pid meta_ticks meta_kind current_ticks cmdline
    pid="$(cat "$CLOUDFLARED_PID_FILE" 2>/dev/null || true)"
    read -r meta_pid meta_ticks meta_kind < "$CLOUDFLARED_PID_META_FILE" || return 1
    [[ "$pid" =~ ^[1-9][0-9]*$ ]] || return 1
    [ "$pid" = "$meta_pid" ] && [ "$meta_kind" = "cloudflared" ] || return 1
    kill -0 "$pid" 2>/dev/null || return 1
    current_ticks="$(process_start_ticks "$pid")"
    [ -n "$current_ticks" ] && [ "$current_ticks" = "$meta_ticks" ] || return 1
    cmdline="$(tr '\0' ' ' < "/proc/${pid}/cmdline" 2>/dev/null || true)"
    [[ "$cmdline" == *"cloudflared"* && "$cmdline" == *"tunnel run"* ]]
}

write_identity_file() {
    local path="$1" value="$2" tmp
    mkdir -p "$(dirname "$path")"
    tmp="$(mktemp "${path}.XXXXXX")"
    printf '%s\n' "$value" > "$tmp"
    chmod 600 "$tmp"
    mv -f "$tmp" "$path"
}

mkdir -p "$(dirname "$CLOUDFLARED_LOCK_FILE")"
exec 9> "$CLOUDFLARED_LOCK_FILE"
if ! flock -w 10 9; then
    log "ERROR: another cloudflared management operation is in progress"
    exit 1
fi

if process_is_declared_tunnel; then
    log "cloudflared tunnel is already running (PID: $(cat "$CLOUDFLARED_PID_FILE"))"
    exit 0
fi

metrics_port="${CLOUDFLARED_METRICS##*:}"
if ss -ltnH "sport = :${metrics_port}" | grep -q .; then
    log "ERROR: metrics port is already occupied and the declared tunnel identity could not be verified"
    exit 1
fi

start_ticks="$(process_start_ticks "$$")"
if [ -z "$start_ticks" ]; then
    log "ERROR: cannot read launcher process start ticks"
    exit 1
fi

cleanup_failed_identity() {
    local exit_code="$?"
    trap - EXIT
    if [ "$(cat "$CLOUDFLARED_PID_FILE" 2>/dev/null || true)" = "$$" ]; then
        rm -f "$CLOUDFLARED_PID_FILE" "$CLOUDFLARED_PID_META_FILE"
    fi
    exit "$exit_code"
}

trap cleanup_failed_identity EXIT
write_identity_file "$CLOUDFLARED_PID_FILE" "$$"
write_identity_file "$CLOUDFLARED_PID_META_FILE" "$$ $start_ticks cloudflared"

# 启动 cloudflared
exec "$CLOUDFLARED_BIN" --config "$CLOUDFLARED_CONFIG" --no-autoupdate \
    --protocol "$CLOUDFLARED_PROTOCOL" --metrics "$CLOUDFLARED_METRICS" \
    tunnel run >> "$CLOUDFLARED_LOG" 2>&1
