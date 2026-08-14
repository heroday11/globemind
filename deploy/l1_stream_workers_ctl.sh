#!/usr/bin/env bash
set -euo pipefail

ROOT="/root/data/globemind"
PY="/opt/conda/envs/Globemind_env/bin/python"
LOG_DIR="$ROOT/logs"

PREP_PID="$LOG_DIR/l1_prep_worker.pid"
PREP_LOG="$LOG_DIR/l1_prep_worker.log"
EXTRACT_PID="$LOG_DIR/l1_extract_worker.pid"
EXTRACT_LOG="$LOG_DIR/l1_extract_worker.log"

mkdir -p "$LOG_DIR"

is_running() {
  local pid_file="$1"
  [[ -f "$pid_file" ]] || return 1
  local pid
  pid="$(cat "$pid_file" 2>/dev/null || true)"
  [[ -n "$pid" ]] || return 1
  kill -0 "$pid" 2>/dev/null
}

start_one() {
  local name="$1"
  local pid_file="$2"
  local log_file="$3"
  shift 3

  if is_running "$pid_file"; then
    echo "$name already running pid=$(cat "$pid_file")"
    return 0
  fi

  local cmd restart_delay
  printf -v cmd '%q ' "$@"
  restart_delay="${L1_WORKER_RESTART_DELAY_SEC:-10}"

  cd "$ROOT"
  setsid bash -c "
    set -u
    while true; do
      echo \"[\$(date -Is)] supervisor launching ${name}: ${cmd}\"
      ${cmd}
      rc=\$?
      echo \"[\$(date -Is)] ${name} exited rc=\${rc}; restart in ${restart_delay}s\"
      sleep \"${restart_delay}\"
    done
  " >>"$log_file" 2>&1 &
  echo $! >"$pid_file"
  echo "started $name supervisor pid=$(cat "$pid_file") log=$log_file"
}

stop_one() {
  local name="$1"
  local pid_file="$2"
  if ! is_running "$pid_file"; then
    echo "$name not running"
    rm -f "$pid_file"
    return 0
  fi
  local pid
  pid="$(cat "$pid_file")"
  kill -TERM -- "-$pid" 2>/dev/null || kill "$pid" 2>/dev/null || true
  for _ in {1..20}; do
    if ! kill -0 "$pid" 2>/dev/null; then
      rm -f "$pid_file"
      echo "stopped $name"
      return 0
    fi
    sleep 0.5
  done
  kill -KILL -- "-$pid" 2>/dev/null || kill -9 "$pid" 2>/dev/null || true
  rm -f "$pid_file"
  echo "killed $name"
}

status_one() {
  local name="$1"
  local pid_file="$2"
  local log_file="$3"
  if is_running "$pid_file"; then
    echo "$name: running supervisor pid=$(cat "$pid_file") log=$log_file"
  else
    echo "$name: stopped log=$log_file"
  fi
}

start_all() {
  start_one \
    "l1-prep" "$PREP_PID" "$PREP_LOG" \
    "$PY" -u scripts/stream_l1_event_features.py \
      --dbname news \
      --mode prep \
      --batch-size 20000 \
      --target-end 2030-12-31 \
      --poll-sec 1800 \
      --log-every 20000

  start_one \
    "l1-extract" "$EXTRACT_PID" "$EXTRACT_LOG" \
    "$PY" -u scripts/stream_l1_event_features.py \
      --dbname news \
      --mode extract \
      --batch-size 1024 \
      --target-end 2030-12-31 \
      --poll-sec 1800 \
      --event-concurrency 96 \
      --domain-gate-threshold 0.30 \
      --log-every 1024
}

stop_all() {
  stop_one "l1-extract" "$EXTRACT_PID"
  stop_one "l1-prep" "$PREP_PID"
}

status_all() {
  status_one "l1-prep" "$PREP_PID" "$PREP_LOG"
  status_one "l1-extract" "$EXTRACT_PID" "$EXTRACT_LOG"
}

case "${1:-status}" in
  start)
    start_all
    ;;
  stop)
    stop_all
    ;;
  restart)
    stop_all
    start_all
    ;;
  status)
    status_all
    ;;
  logs)
    echo "== prep =="
    tail -n "${2:-80}" "$PREP_LOG" 2>/dev/null || true
    echo "== extract =="
    tail -n "${2:-80}" "$EXTRACT_LOG" 2>/dev/null || true
    ;;
  follow)
    tail -F "$PREP_LOG" "$EXTRACT_LOG"
    ;;
  *)
    echo "usage: $0 {start|stop|restart|status|logs|follow}" >&2
    exit 2
    ;;
esac
