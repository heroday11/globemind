#!/usr/bin/env bash
set -euo pipefail

ROOT="${GLOBEMIND_HOME:-/root/data/globemind}"
PY="${PYTHON_BIN:-$ROOT/.env_torch/bin/python}"
LOG_DIR="$ROOT/logs"
JOB_DIR="$ROOT/data/historical_news/jobs/wave1_1y_prod_20260621"
SHARD_DIR="${SHARD_DIR:-$JOB_DIR/wave1_remaining_shards}"
SHARD_COUNT="${SHARD_COUNT:-2}"
SHARD_CONCURRENCY="${SHARD_CONCURRENCY:-4}"
MAX_PER_DOMAIN="${MAX_PER_DOMAIN:-2}"
MIN_PER_DOMAIN="${MIN_PER_DOMAIN:-1}"
TIMEOUT="${TIMEOUT:-18}"
BASE_DELAY_MS="${BASE_DELAY_MS:-50}"
JITTER_MS="${JITTER_MS:-250}"
RETRY_LIMIT="${RETRY_LIMIT:-3}"
MAX_RUNTIME_SEC="${MAX_RUNTIME_SEC:-21600}"
RESTART_DELAY_SEC="${RESTART_DELAY_SEC:-45}"
PROXY_POOL="${PROXY_POOL:-$ROOT/data/proxy_pool/proxy_pool_manifest_optimized_20260621.json}"

mkdir -p "$LOG_DIR"

build() {
  cd "$ROOT"
  "$PY" scripts/build_wave1_remaining_shards.py \
    --shards "$SHARD_COUNT" \
    --output-dir "$SHARD_DIR" \
    --overwrite
}

pid_file() {
  echo "$LOG_DIR/wave1_sharded_remaining_extract_shard${1}.pid"
}

log_file() {
  echo "$LOG_DIR/wave1_sharded_remaining_extract_shard${1}.log"
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
    echo "shard $shard already running pid=$(cat "$(pid_file "$shard")")"
    return 0
  fi
  local input="$SHARD_DIR/inputs/shard_${shard}.jsonl"
  if [[ ! -f "$input" ]]; then
    echo "missing shard input: $input; run: $0 build" >&2
    return 1
  fi
  local out_dir="$SHARD_DIR/outputs/shard_${shard}"
  mkdir -p "$out_dir"
  local stop_file="$LOG_DIR/wave1_sharded_remaining_extract_shard${shard}.stop"
  rm -f "$stop_file"
  cd "$ROOT"
  env \
    GLOBEMIND_HOME="$ROOT" \
    PYTHON_BIN="$PY" \
    INPUT="$input" \
    OUTPUT="$out_dir/articles.jsonl" \
    ERRORS="$out_dir/errors.jsonl" \
    STATS="$out_dir/stats.json" \
    PROGRESS="$out_dir/progress.json" \
    STOP_FILE="$stop_file" \
    STATE_FILE="$LOG_DIR/wave1_sharded_remaining_extract_shard${shard}_supervisor.json" \
    PROXY_POOL="$PROXY_POOL" \
    GLOBAL_CONCURRENCY="$SHARD_CONCURRENCY" \
    MAX_PER_DOMAIN="$MAX_PER_DOMAIN" \
    MIN_PER_DOMAIN="$MIN_PER_DOMAIN" \
    TIMEOUT="$TIMEOUT" \
    BASE_DELAY_MS="$BASE_DELAY_MS" \
    JITTER_MS="$JITTER_MS" \
    RETRY_LIMIT="$RETRY_LIMIT" \
    MAX_RUNTIME_SEC="$MAX_RUNTIME_SEC" \
    RESTART_DELAY_SEC="$RESTART_DELAY_SEC" \
    setsid "$ROOT/deploy/wave1_remaining_extract_loop.sh" >>"$(log_file "$shard")" 2>&1 &
  echo $! >"$(pid_file "$shard")"
  echo "started shard $shard pid=$(cat "$(pid_file "$shard")") log=$(log_file "$shard")"
}

start() {
  if [[ ! -f "$SHARD_DIR/manifest.json" ]]; then
    build
  fi
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
    echo "shard $shard not running"
    return 0
  fi
  local pid
  pid="$(cat "$pidfile")"
  touch "$LOG_DIR/wave1_sharded_remaining_extract_shard${shard}.stop"
  kill -TERM -- "-$pid" 2>/dev/null || kill "$pid" 2>/dev/null || true
  for _ in {1..30}; do
    if ! kill -0 "$pid" 2>/dev/null; then
      rm -f "$pidfile" "$LOG_DIR/wave1_sharded_remaining_extract_shard${shard}.stop"
      echo "stopped shard $shard"
      return 0
    fi
    sleep 1
  done
  kill -KILL -- "-$pid" 2>/dev/null || kill -9 "$pid" 2>/dev/null || true
  rm -f "$pidfile" "$LOG_DIR/wave1_sharded_remaining_extract_shard${shard}.stop"
  echo "killed shard $shard"
}

stop() {
  for ((shard=0; shard<SHARD_COUNT; shard++)); do
    stop_one "$shard"
  done
}

status() {
  "$PY" - "$SHARD_DIR" "$LOG_DIR" "$SHARD_COUNT" <<'PY'
import json
import os
import sys
import time
from pathlib import Path

shard_dir = Path(sys.argv[1])
log_dir = Path(sys.argv[2])
count = int(sys.argv[3])
total = {"processed": 0, "successes": 0, "failures": 0, "rows_remaining": 0, "active_tasks": 0}
for shard in range(count):
    pid_path = log_dir / f"wave1_sharded_remaining_extract_shard{shard}.pid"
    pid = pid_path.read_text().strip() if pid_path.exists() else ""
    running = bool(pid and Path(f"/proc/{pid}").exists())
    progress_path = shard_dir / "outputs" / f"shard_{shard}" / "progress.json"
    print(f"shard {shard}: {'running' if running else 'stopped'} pid={pid or '-'}")
    if progress_path.exists():
        data = json.loads(progress_path.read_text(encoding="utf-8"))
        print("  updated_at:", data.get("updated_at"))
        for key in ("processed", "successes", "failures", "rows_remaining", "successes_per_min", "running", "active_tasks"):
            print(f"  {key}: {data.get(key)}")
        for key in total:
            total[key] += int(data.get(key) or 0)
print("total:", total)
manifest = shard_dir / "manifest.json"
if manifest.exists():
    data = json.loads(manifest.read_text(encoding="utf-8"))
    print("manifest_remaining_rows:", data.get("remaining_rows_written"))
    print("top_domains:", data.get("top_domains", [])[:10])
PY
}

case "${1:-status}" in
  build) build ;;
  start) start ;;
  stop) stop ;;
  restart) stop; start ;;
  status) status ;;
  *)
    echo "usage: $0 {build|start|stop|restart|status}" >&2
    exit 2
    ;;
esac
