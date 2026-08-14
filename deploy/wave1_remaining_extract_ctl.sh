#!/usr/bin/env bash
set -euo pipefail

ROOT="${GLOBEMIND_HOME:-/root/data/globemind}"
PY="${PYTHON_BIN:-$ROOT/.env_torch/bin/python}"
LOG_DIR="$ROOT/logs"
PID_FILE="$LOG_DIR/wave1_remaining_extract.pid"
LOG_FILE="$LOG_DIR/wave1_remaining_extract.log"
STOP_FILE="$LOG_DIR/wave1_remaining_extract.stop"
JOB_DIR="$ROOT/data/historical_news/jobs/wave1_1y_prod_20260621"
INPUT="$JOB_DIR/wave1_discovered_urls_merged_pruned.jsonl"
OUTPUT="$JOB_DIR/wave1_articles_merged.jsonl"
ERRORS="$JOB_DIR/wave1_articles_merged_errors.jsonl"
STATS="$JOB_DIR/wave1_articles_merged_stats.json"
PROGRESS="$JOB_DIR/wave1_articles_merged_progress.json"
PROXY_POOL="${PROXY_POOL:-$ROOT/data/proxy_pool/proxy_pool_manifest_optimized_20260621.json}"

GLOBAL_CONCURRENCY="${GLOBAL_CONCURRENCY:-6}"
MAX_PER_DOMAIN="${MAX_PER_DOMAIN:-2}"
MIN_PER_DOMAIN="${MIN_PER_DOMAIN:-1}"
TIMEOUT="${TIMEOUT:-18}"
BASE_DELAY_MS="${BASE_DELAY_MS:-50}"
JITTER_MS="${JITTER_MS:-250}"
RETRY_LIMIT="${RETRY_LIMIT:-3}"
MAX_RUNTIME_SEC="${MAX_RUNTIME_SEC:-21600}"
RESTART_DELAY_SEC="${RESTART_DELAY_SEC:-45}"

mkdir -p "$LOG_DIR"

is_running() {
  [[ -f "$PID_FILE" ]] || return 1
  local pid
  pid="$(cat "$PID_FILE" 2>/dev/null || true)"
  [[ -n "$pid" ]] || return 1
  kill -0 "$pid" 2>/dev/null
}

start() {
  if is_running; then
    echo "wave1 remaining extractor already running pid=$(cat "$PID_FILE")"
    return 0
  fi
  if [[ ! -f "$INPUT" ]]; then
    echo "missing input: $INPUT" >&2
    return 1
  fi
  if [[ ! -x "$PY" ]]; then
    echo "missing python: $PY" >&2
    return 1
  fi

  cd "$ROOT"
  rm -f "$STOP_FILE"
  env \
    GLOBEMIND_HOME="$ROOT" \
    PYTHON_BIN="$PY" \
    PROXY_POOL="$PROXY_POOL" \
    GLOBAL_CONCURRENCY="$GLOBAL_CONCURRENCY" \
    MAX_PER_DOMAIN="$MAX_PER_DOMAIN" \
    MIN_PER_DOMAIN="$MIN_PER_DOMAIN" \
    TIMEOUT="$TIMEOUT" \
    BASE_DELAY_MS="$BASE_DELAY_MS" \
    JITTER_MS="$JITTER_MS" \
    RETRY_LIMIT="$RETRY_LIMIT" \
    MAX_RUNTIME_SEC="$MAX_RUNTIME_SEC" \
    RESTART_DELAY_SEC="$RESTART_DELAY_SEC" \
    setsid "$ROOT/deploy/wave1_remaining_extract_loop.sh" >>"$LOG_FILE" 2>&1 &
  echo $! >"$PID_FILE"
  echo "started wave1 remaining extractor supervisor pid=$(cat "$PID_FILE") log=$LOG_FILE"
}

stop() {
  if ! is_running; then
    echo "wave1 remaining extractor not running"
    rm -f "$PID_FILE"
    return 0
  fi
  local pid
  pid="$(cat "$PID_FILE")"
  touch "$STOP_FILE"
  kill -TERM -- "-$pid" 2>/dev/null || kill "$pid" 2>/dev/null || true
  pkill -TERM -f "scripts/adaptive_global_extractor.py .*wave1_discovered_urls_merged_pruned.jsonl" 2>/dev/null || true
  for _ in {1..30}; do
    if ! kill -0 "$pid" 2>/dev/null && ! pgrep -f "scripts/adaptive_global_extractor.py .*wave1_discovered_urls_merged_pruned.jsonl" >/dev/null 2>&1; then
      rm -f "$PID_FILE"
      rm -f "$STOP_FILE"
      echo "stopped wave1 remaining extractor"
      return 0
    fi
    sleep 1
  done
  kill -KILL -- "-$pid" 2>/dev/null || kill -9 "$pid" 2>/dev/null || true
  pkill -KILL -f "scripts/adaptive_global_extractor.py .*wave1_discovered_urls_merged_pruned.jsonl" 2>/dev/null || true
  rm -f "$PID_FILE"
  rm -f "$STOP_FILE"
  echo "killed wave1 remaining extractor"
}

status() {
  if is_running; then
    echo "wave1 remaining extractor: running pid=$(cat "$PID_FILE") log=$LOG_FILE"
  else
    echo "wave1 remaining extractor: stopped log=$LOG_FILE"
  fi
  if [[ -f "$PROGRESS" ]]; then
    "$PY" - <<PY
import json
from pathlib import Path
p = Path("$PROGRESS")
data = json.loads(p.read_text())
for key in ("updated_at", "input", "rows", "processed", "successes", "failures", "rows_remaining", "completion_rate", "successes_per_min", "running", "active_tasks"):
    print(f"{key}: {data.get(key)}")
PY
  fi
}

logs() {
  tail -n "${1:-80}" "$LOG_FILE" 2>/dev/null || true
}

case "${1:-status}" in
  start) start ;;
  stop) stop ;;
  restart) stop; start ;;
  status) status ;;
  logs) logs "${2:-80}" ;;
  follow) tail -F "$LOG_FILE" ;;
  *)
    echo "usage: $0 {start|stop|restart|status|logs|follow}" >&2
    exit 2
    ;;
esac
