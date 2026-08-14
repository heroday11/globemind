#!/usr/bin/env bash
set -euo pipefail

ROOT="${GLOBEMIND_HOME:-/root/data/globemind}"
PY="${PYTHON_BIN:-$ROOT/.env_torch/bin/python}"
LOG_DIR="$ROOT/logs"
JOB_DIR="$ROOT/data/historical_news/jobs/wave1_1y_prod_20260621"
SHARD_DIR="${SHARD_DIR:-$JOB_DIR/wave1_remaining_shards}"
SHARD_COUNT="${SHARD_COUNT:-2}"

DB_HOST="${DB_HOST:-192.168.207.171}"
DB_PORT="${DB_PORT:-54333}"
DB_USER="${DB_USER:-postgres}"
DB_NAME="${DB_NAME:-news}"
BATCH_SIZE="${BATCH_SIZE:-200}"
POLL_SEC="${POLL_SEC:-15}"

mkdir -p "$LOG_DIR"

pid_file() {
  echo "$LOG_DIR/wave1_sharded_loader_shard${1}.pid"
}

log_file() {
  echo "$LOG_DIR/wave1_sharded_loader_shard${1}.log"
}

is_running() {
  local pidfile
  pidfile="$(pid_file "$1")"
  [[ -f "$pidfile" ]] || return 1
  local pid
  pid="$(cat "$pidfile" 2>/dev/null || true)"
  [[ -n "$pid" ]] || return 1
  kill -0 "$pid" 2>/dev/null
}

start_one() {
  local shard="$1"
  if is_running "$shard"; then
    echo "loader shard $shard already running pid=$(cat "$(pid_file "$shard")")"
    return 0
  fi
  local input="$SHARD_DIR/outputs/shard_${shard}/articles.jsonl"
  local state="$SHARD_DIR/loader_state_shard_${shard}.json"
  mkdir -p "$(dirname "$input")"
  cd "$ROOT"
  setsid "$PY" -u scripts/stream_load_news_to_postgres.py \
    --input "$input" \
    --state-path "$state" \
    --poll-sec "$POLL_SEC" \
    --batch-size "$BATCH_SIZE" \
    --host "$DB_HOST" \
    --port "$DB_PORT" \
    --user "$DB_USER" \
    --dbname "$DB_NAME" \
    >>"$(log_file "$shard")" 2>&1 &
  echo $! >"$(pid_file "$shard")"
  echo "started loader shard $shard pid=$(cat "$(pid_file "$shard")") log=$(log_file "$shard")"
}

start() {
  for ((shard=0; shard<SHARD_COUNT; shard++)); do
    start_one "$shard"
  done
}

stop_one() {
  local shard="$1"
  local pidfile
  pidfile="$(pid_file "$shard")"
  if ! is_running "$shard"; then
    rm -f "$pidfile"
    echo "loader shard $shard not running"
    return 0
  fi
  local pid
  pid="$(cat "$pidfile")"
  kill -TERM -- "-$pid" 2>/dev/null || kill "$pid" 2>/dev/null || true
  for _ in {1..20}; do
    if ! kill -0 "$pid" 2>/dev/null; then
      rm -f "$pidfile"
      echo "stopped loader shard $shard"
      return 0
    fi
    sleep 0.5
  done
  kill -KILL -- "-$pid" 2>/dev/null || kill -9 "$pid" 2>/dev/null || true
  rm -f "$pidfile"
  echo "killed loader shard $shard"
}

stop() {
  for ((shard=0; shard<SHARD_COUNT; shard++)); do
    stop_one "$shard"
  done
}

status() {
  "$PY" - "$SHARD_DIR" "$LOG_DIR" "$SHARD_COUNT" <<'PY'
import datetime
import json
import sys
from pathlib import Path

shard_dir = Path(sys.argv[1])
log_dir = Path(sys.argv[2])
count = int(sys.argv[3])
total = {"seen": 0, "inserted": 0, "skipped": 0, "quality_skipped": 0}
for shard in range(count):
    pid_path = log_dir / f"wave1_sharded_loader_shard{shard}.pid"
    pid = pid_path.read_text().strip() if pid_path.exists() else ""
    running = bool(pid and Path(f"/proc/{pid}").exists())
    state_path = shard_dir / f"loader_state_shard_{shard}.json"
    print(f"loader shard {shard}: {'running' if running else 'stopped'} pid={pid or '-'}")
    if state_path.exists():
        data = json.loads(state_path.read_text(encoding="utf-8"))
        updated = data.get("updated_at")
        if updated:
            print("  updated_at:", datetime.datetime.fromtimestamp(float(updated)).isoformat())
        for key in ("seen", "inserted", "skipped", "quality_skipped", "offset", "input"):
            print(f"  {key}: {data.get(key)}")
        for key in total:
            total[key] += int(data.get(key) or 0)
print("total:", total)
PY
}

case "${1:-status}" in
  start) start ;;
  stop) stop ;;
  restart) stop; start ;;
  status) status ;;
  *)
    echo "usage: $0 {start|stop|restart|status}" >&2
    exit 2
    ;;
esac
