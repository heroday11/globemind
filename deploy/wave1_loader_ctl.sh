#!/usr/bin/env bash
set -euo pipefail
umask 077

ROOT="${GLOBEMIND_HOME:-/root/data/globemind}"
PY="${PYTHON_BIN:-$ROOT/.env_torch/bin/python}"
CONTROL_PY="${WAVE1_LOADER_CONTROL_PYTHON:-$(command -v python3)}"
HELPER="$ROOT/scripts/wave1_loader_migrate.py"
RUNTIME_DIR="${WAVE1_LOADER_RUNTIME_DIR:-/root/data/runtime/globemind/wave1_loader}"
LOG_DIR="${WAVE1_LOADER_LOG_DIR:-/root/data/runtime/globemind/logs}"
PID_FILE="$RUNTIME_DIR/wave1_loader.pid"
META_FILE="${PID_FILE}.meta"
READY_FILE="${PID_FILE}.ready"
HEARTBEAT_FILE="${PID_FILE}.heartbeat"
CONTROL_SOCKET="${PID_FILE}.sock"
LOCK_FILE="${PID_FILE}.lock"
SECRET_FILE="${PID_FILE}.db-secret"
LOADER_SECRET_SOURCE="${WAVE1_LOADER_DB_PASSWORD_SOURCE_FILE:-}"
LOG_FILE="$LOG_DIR/wave1_loader.log"
LEGACY_PID_FILE="${WAVE1_LOADER_LEGACY_PID_FILE:-$ROOT/logs/wave1_loader.pid}"
JOB_DIR="${WAVE1_LOADER_JOB_DIR:-$ROOT/data/historical_news/jobs/wave1_1y_prod_20260621}"
INPUT="${WAVE1_LOADER_INPUT:-$JOB_DIR/wave1_articles_merged.jsonl}"
STATE="${WAVE1_LOADER_STATE:-$JOB_DIR/news_loader_state.json}"
SEALED_MANIFEST="${WAVE1_LOADER_SEALED_MANIFEST:-$JOB_DIR/wave1_articles_merged.sealed.json}"
DEAD_LETTER_DIR="${WAVE1_LOADER_DEAD_LETTER_DIR:-$JOB_DIR/news_loader_dead_letters}"
PROC_ROOT="${WAVE1_LOADER_PROC_ROOT:-/proc}"
INSTANCE_NAME="${WAVE1_LOADER_INSTANCE:-wave1_loader}"
JOB_ID="${WAVE1_LOADER_JOB_ID:-wave1_1y_prod_20260621}"
RUN_ID="${WAVE1_LOADER_RUN_ID:-wave1_1y_prod_20260621}"
CHECKPOINT_KEY="${WAVE1_LOADER_CHECKPOINT_KEY:-wave1:wave1_1y_prod_20260621:news}"
CODE_VERSION="${WAVE1_LOADER_CODE_VERSION:-}"

DB_HOST="${DB_HOST:-192.168.207.171}"
DB_PORT="${DB_PORT:-54333}"
DB_USER="${DB_USER:-wave1_loader}"
DB_NAME="${DB_NAME:-news}"
DB_SSLMODE="${DB_SSLMODE:-}"
ALLOW_PRIVATE_SCRAM_TRANSPORT="${GLOBEMIND_ALLOW_PRIVATE_SCRAM_TRANSPORT:-0}"
ALLOW_LEGACY_DB_ROLE="${WAVE1_LOADER_ALLOW_LEGACY_DB_ROLE:-0}"
BATCH_SIZE="${BATCH_SIZE:-200}"
POLL_SEC="${POLL_SEC:-15}"
HEARTBEAT_SEC="${WAVE1_LOADER_HEARTBEAT_SEC:-30}"
CONNECT_TIMEOUT_SEC="${WAVE1_LOADER_CONNECT_TIMEOUT_SEC:-10}"
STATEMENT_TIMEOUT_MS="${WAVE1_LOADER_STATEMENT_TIMEOUT_MS:-30000}"
DB_LOCK_TIMEOUT_MS="${WAVE1_LOADER_DB_LOCK_TIMEOUT_MS:-5000}"
START_TIMEOUT_SEC="${WAVE1_LOADER_START_TIMEOUT_SEC:-120}"
READY_STABILITY_SEC="${WAVE1_LOADER_READY_STABILITY_SEC:-2}"
COMPLETION_GRACE_SEC="${WAVE1_LOADER_COMPLETION_GRACE_SEC:-5}"
STOP_TIMEOUT_SEC="${WAVE1_LOADER_STOP_TIMEOUT_SEC:-60}"
LOCK_TIMEOUT_SEC="${WAVE1_LOADER_LOCK_TIMEOUT_SEC:-10}"

prepare_runtime_paths() {
  [[ ! -L "$RUNTIME_DIR" ]] || {
    echo "wave1 loader runtime directory must not be a symlink" >&2
    return 2
  }
  mkdir -p "$RUNTIME_DIR" "$LOG_DIR" "$DEAD_LETTER_DIR"
  chmod 700 "$RUNTIME_DIR"
  [[ "$(stat -c '%u' "$RUNTIME_DIR")" == "$(id -u)" ]] || {
    echo "wave1 loader runtime directory owner mismatch" >&2
    return 2
  }
  touch "$LOG_FILE"
  chmod 640 "$LOG_FILE"
  chmod 700 "$DEAD_LETTER_DIR"
}

validate_configuration() {
  [[ "$INSTANCE_NAME" =~ ^[A-Za-z0-9._-]+$ ]] || return 2
  [[ "$JOB_ID" =~ ^[A-Za-z0-9._:/-]+$ ]] || return 2
  [[ "$RUN_ID" =~ ^[A-Za-z0-9._:/-]+$ ]] || return 2
  [[ "$CHECKPOINT_KEY" =~ ^[A-Za-z0-9._:/-]+$ ]] || return 2
  [[ "$DB_PORT" =~ ^[0-9]+$ && "$BATCH_SIZE" =~ ^[1-9][0-9]*$ ]] || return 2
  for value in \
    "$CONNECT_TIMEOUT_SEC" "$STATEMENT_TIMEOUT_MS" "$DB_LOCK_TIMEOUT_MS" \
    "$START_TIMEOUT_SEC" "$STOP_TIMEOUT_SEC"
  do
    [[ "$value" =~ ^[0-9]+$ ]] || return 2
  done
  for value in \
    "$HEARTBEAT_SEC" "$READY_STABILITY_SEC" "$COMPLETION_GRACE_SEC" "$LOCK_TIMEOUT_SEC"
  do
    [[ "$value" =~ ^[0-9]+([.][0-9]+)?$ ]] || return 2
  done
  [[ -x "$PY" && -x "$CONTROL_PY" && -f "$HELPER" ]] || return 2
  [[ "$ALLOW_LEGACY_DB_ROLE" == "0" || "$ALLOW_LEGACY_DB_ROLE" == "1" ]] || return 2
  [[ "$DB_SSLMODE" == "verify-full" || "$DB_SSLMODE" == "require" || "$DB_SSLMODE" == "disable" ]] || return 2
  [[ "$ALLOW_PRIVATE_SCRAM_TRANSPORT" == "0" || "$ALLOW_PRIVATE_SCRAM_TRANSPORT" == "1" ]] || return 2
}

validate_runtime_database_role() {
  if [[ "$DB_USER" == "wave1_loader" ]]; then
    if [[ "$ALLOW_LEGACY_DB_ROLE" != "0" ]]; then
      echo "WAVE1_LOADER_ALLOW_LEGACY_DB_ROLE is valid only with DB_USER=postgres" >&2
      return 2
    fi
    return 0
  fi
  if [[ "$ALLOW_LEGACY_DB_ROLE" == "1" && "$DB_USER" == "postgres" ]]; then
    echo "warning: explicitly using the legacy postgres loader role for rollback" >&2
    return 0
  fi
  echo "wave1 loader start requires DB_USER=wave1_loader; legacy postgres requires WAVE1_LOADER_ALLOW_LEGACY_DB_ROLE=1" >&2
  return 2
}

atomic_write_pid() {
  local pid="$1"
  "$CONTROL_PY" - "$PID_FILE" "$pid" <<'PY'
import os
import sys
import tempfile
from pathlib import Path

path = Path(sys.argv[1])
descriptor, temporary_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
temporary = Path(temporary_name)
try:
    os.fchmod(descriptor, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        descriptor = -1
        handle.write(sys.argv[2] + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)
    directory = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
finally:
    if descriptor >= 0:
        os.close(descriptor)
    temporary.unlink(missing_ok=True)
PY
}

pid_file_value() {
  local value
  [[ -f "$PID_FILE" ]] || return 1
  value="$(tr -d '[:space:]' < "$PID_FILE" 2>/dev/null || true)"
  [[ "$value" =~ ^[1-9][0-9]*$ ]] || return 1
  printf '%s\n' "$value"
}

meta_matches_pid_file() {
  local pid
  pid="$(pid_file_value)" || return 1
  "$CONTROL_PY" - "$META_FILE" "$pid" <<'PY'
import json
import sys

try:
    payload = json.loads(open(sys.argv[1], encoding="utf-8").read())
    actual = int(payload["identity"]["pid"])
except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError):
    raise SystemExit(1)
raise SystemExit(0 if actual == int(sys.argv[2]) else 1)
PY
}

runtime_verified() {
  meta_matches_pid_file || return 1
  "$CONTROL_PY" "$HELPER" verify-runtime \
    --meta "$META_FILE" --ready "$READY_FILE" --proc-root "$PROC_ROOT" >/dev/null
  "$CONTROL_PY" "$HELPER" socket-control \
    --meta "$META_FILE" \
    --socket "$CONTROL_SOCKET" \
    --command status \
    --proc-root "$PROC_ROOT" \
    >/dev/null
}

meta_verified() {
  meta_matches_pid_file || return 1
  "$CONTROL_PY" "$HELPER" verify-runtime \
    --meta "$META_FILE" --proc-root "$PROC_ROOT" >/dev/null
}

runtime_ready_verified() {
  meta_matches_pid_file || return 1
  "$CONTROL_PY" "$HELPER" verify-runtime \
    --meta "$META_FILE" \
    --ready "$READY_FILE" \
    --require-ready-status ready \
    --proc-root "$PROC_ROOT" \
    >/dev/null
}

pid_candidate_alive() {
  local pid
  pid="$(pid_file_value)" || return 1
  "$CONTROL_PY" "$HELPER" pid-alive --pid "$pid" --proc-root "$PROC_ROOT" >/dev/null 2>&1
}

metadata_identity_dead() {
  [[ -s "$META_FILE" ]] || return 1
  "$CONTROL_PY" "$HELPER" runtime-dead \
    --meta "$META_FILE" --proc-root "$PROC_ROOT" >/dev/null 2>&1
}

incomplete_pid_record_is_dead() {
  local pid
  [[ ! -e "$META_FILE" ]] || return 1
  pid="$(pid_file_value)" || return 1
  [[ ! -e "$PROC_ROOT/$pid/stat" ]]
}

legacy_pid_alive() {
  local pid
  [[ "$LEGACY_PID_FILE" != "$PID_FILE" ]] || return 1
  pid="$(tr -d '[:space:]' < "$LEGACY_PID_FILE" 2>/dev/null || true)"
  [[ "$pid" =~ ^[1-9][0-9]*$ ]] || return 1
  "$CONTROL_PY" "$HELPER" pid-alive --pid "$pid" --proc-root "$PROC_ROOT" >/dev/null 2>&1
}

metadata_exists() {
  [[ -e "$PID_FILE" || -e "$META_FILE" || -e "$READY_FILE" || -e "$CONTROL_SOCKET" ]]
}

clear_identity() {
  rm -f "$PID_FILE" "$META_FILE" "$READY_FILE" "$HEARTBEAT_FILE" "$CONTROL_SOCKET"
}

new_instance_id() {
  "$CONTROL_PY" - "$INSTANCE_NAME" <<'PY'
import secrets
import sys
import time

print(f"{sys.argv[1]}-{time.time_ns()}-{secrets.token_hex(8)}")
PY
}

materialize_secret() {
  [[ -n "$LOADER_SECRET_SOURCE" ]] || {
    echo "WAVE1_LOADER_DB_PASSWORD_SOURCE_FILE is required" >&2
    return 2
  }
  "$CONTROL_PY" "$HELPER" validate-secret --path "$LOADER_SECRET_SOURCE" >/dev/null
  env \
    -u L1_DB_PASSWORD -u PG_WRITE_PASSWORD -u DB_PASSWORD -u PG_PASSWORD \
    -u PGPASSWORD -u DATABASE_URL -u SQLALCHEMY_DATABASE_URL -u PGPASSFILE \
    GLOBEMIND_DB_PASSWORD_FILE="$LOADER_SECRET_SOURCE" \
    "$CONTROL_PY" "$HELPER" materialize-secret --output "$SECRET_FILE" >/dev/null
  "$CONTROL_PY" "$HELPER" validate-secret --path "$SECRET_FILE" >/dev/null
}

scrub_password_environment() {
  unset \
    L1_DB_PASSWORD PG_WRITE_PASSWORD DB_PASSWORD PG_PASSWORD PGPASSWORD \
    DATABASE_URL SQLALCHEMY_DATABASE_URL PGPASSFILE GLOBEMIND_DB_PASSWORD_FILE
  export GLOBEMIND_DB_PASSWORD_FILE="$SECRET_FILE"
}

wait_for_ready() {
  local deadline=$((SECONDS + START_TIMEOUT_SEC))
  while [[ "$SECONDS" -lt "$deadline" ]]; do
    if [[ -s "$READY_FILE" && -S "$CONTROL_SOCKET" ]]; then
      "$CONTROL_PY" "$HELPER" attach-runtime-socket \
        --meta "$META_FILE" \
        --ready "$READY_FILE" \
        --socket "$CONTROL_SOCKET" \
        --proc-root "$PROC_ROOT" \
        >/dev/null 2>&1 \
        || true
    fi
    if runtime_ready_verified >/dev/null 2>&1; then
      sleep "$READY_STABILITY_SEC"
      runtime_ready_verified >/dev/null 2>&1 \
        && runtime_verified >/dev/null 2>&1
      return $?
    fi
    if ! meta_verified >/dev/null 2>&1; then
      return 1
    fi
    sleep 0.25
  done
  return 1
}

stop_fresh_child() {
  local deadline
  meta_verified >/dev/null 2>&1 || return 0
  deadline=$((SECONDS + STOP_TIMEOUT_SEC))
  while meta_verified >/dev/null 2>&1 \
    && [[ "$SECONDS" -lt "$deadline" ]]
  do
    if [[ -s "$READY_FILE" && -S "$CONTROL_SOCKET" ]]; then
      "$CONTROL_PY" "$HELPER" attach-runtime-socket \
        --meta "$META_FILE" \
        --ready "$READY_FILE" \
        --socket "$CONTROL_SOCKET" \
        --proc-root "$PROC_ROOT" \
        >/dev/null 2>&1 \
        || true
      "$CONTROL_PY" "$HELPER" socket-control \
        --meta "$META_FILE" \
        --socket "$CONTROL_SOCKET" \
        --command stop \
        --proc-root "$PROC_ROOT" \
        >/dev/null 2>&1 \
        || true
    fi
    sleep 0.25
  done
  meta_verified >/dev/null 2>&1 && return 1
}

start() {
  validate_runtime_database_role || return $?
  if runtime_verified; then
    echo "wave1 loader already running pid=$(pid_file_value)"
    return 0
  fi
  if legacy_pid_alive; then
    echo "legacy wave1 loader has no authenticated control socket; refusing automatic takeover" >&2
    return 3
  fi
  if metadata_exists; then
    if metadata_identity_dead; then
      echo "clearing metadata for a strongly verified dead loader; no signal sent"
      clear_identity
    elif incomplete_pid_record_is_dead; then
      echo "clearing an incomplete PID record whose process is absent; no signal sent"
      clear_identity
    elif pid_candidate_alive; then
      echo "wave1 loader metadata cannot be strongly verified; refusing to start or signal any PID" >&2
      return 3
    else
      echo "wave1 loader death is unproven; retaining metadata and refusing takeover" >&2
      return 3
    fi
  fi

  materialize_secret
  local instance_id pid expected_exe
  local -a loader_args
  instance_id="$(new_instance_id)"
  expected_exe="$(realpath -e "$PY")"
  loader_args=(
    scripts/stream_load_news_to_postgres.py
    --input "$INPUT"
    --state-path "$STATE"
    --heartbeat-path "$HEARTBEAT_FILE"
    --ready-path "$READY_FILE"
    --control-socket "$CONTROL_SOCKET"
    --sealed-manifest "$SEALED_MANIFEST"
    --dead-letter-dir "$DEAD_LETTER_DIR"
    --checkpoint-key "$CHECKPOINT_KEY"
    --job-id "$JOB_ID"
    --run-id "$RUN_ID"
    --poll-sec "$POLL_SEC"
    --heartbeat-sec "$HEARTBEAT_SEC"
    --completion-grace-sec "$COMPLETION_GRACE_SEC"
    --batch-size "$BATCH_SIZE"
    --host "$DB_HOST"
    --port "$DB_PORT"
    --user "$DB_USER"
    --dbname "$DB_NAME"
    --sslmode "$DB_SSLMODE"
    --connect-timeout-sec "$CONNECT_TIMEOUT_SEC"
    --statement-timeout-ms "$STATEMENT_TIMEOUT_MS"
    --lock-timeout-ms "$DB_LOCK_TIMEOUT_MS"
  )
  if [[ "$ALLOW_PRIVATE_SCRAM_TRANSPORT" == "1" ]]; then
    loader_args+=(--allow-private-scram-transport)
  fi
  if [[ "$ALLOW_LEGACY_DB_ROLE" == "1" ]]; then
    loader_args+=(--allow-legacy-postgres-role)
  fi
  if [[ -n "$CODE_VERSION" ]]; then
    loader_args+=(--code-version "$CODE_VERSION")
  fi
  rm -f "$READY_FILE" "$HEARTBEAT_FILE" "$CONTROL_SOCKET"
  (
    cd "$ROOT"
    scrub_password_environment
    export GLOBEMIND_LOADER_INSTANCE_ID="$instance_id"
    exec setsid "$PY" -u "${loader_args[@]}" \
      >>"$LOG_FILE" 2>&1 < /dev/null 9>&-
  ) &
  pid="$!"
  atomic_write_pid "$pid"

  local identity_written=0
  for _ in $(seq 1 40); do
    if "$CONTROL_PY" "$HELPER" write-runtime-meta \
      --pid "$pid" \
      --proc-root "$PROC_ROOT" \
      --instance-name "$INSTANCE_NAME" \
      --instance-id "$instance_id" \
      --output "$META_FILE" \
      >/dev/null 2>&1
    then
      identity_written=1
      break
    fi
    sleep 0.05
  done
  if [[ "$identity_written" != "1" ]]; then
    echo "wave1 loader exited before any process identity could be captured" >&2
    echo "retaining the unverified PID record; no signal was sent" >&2
    return 1
  fi

  identity_written=0
  for _ in $(seq 1 60); do
    if "$CONTROL_PY" "$HELPER" write-runtime-meta \
      --pid "$pid" \
      --proc-root "$PROC_ROOT" \
      --instance-name "$INSTANCE_NAME" \
      --instance-id "$instance_id" \
      --previous-meta "$META_FILE" \
      --expected-exe "$expected_exe" \
      --expected-cwd "$ROOT" \
      --require-session-leader \
      --output "$META_FILE" \
      >/dev/null 2>&1
    then
      identity_written=1
      break
    fi
    sleep 0.05
  done
  if [[ "$identity_written" != "1" ]]; then
    echo "wave1 loader exited before strong identity could be established" >&2
    if "$CONTROL_PY" "$HELPER" write-runtime-meta \
      --pid "$pid" \
      --proc-root "$PROC_ROOT" \
      --instance-name "$INSTANCE_NAME" \
      --instance-id "$instance_id" \
      --previous-meta "$META_FILE" \
      --output "$META_FILE" \
      >/dev/null 2>&1 \
      && stop_fresh_child
    then
      clear_identity
    else
      echo "startup child identity cannot be safely terminated; retaining metadata" >&2
    fi
    return 1
  fi
  if ! wait_for_ready; then
    echo "wave1 loader failed preflight/readiness; requesting authenticated socket stop when available" >&2
    if stop_fresh_child; then
      clear_identity
    else
      echo "fresh child remains alive; retaining identity metadata" >&2
    fi
    return 1
  fi
  echo "started wave1 loader pid=$pid log=$LOG_FILE"
}

stop_verified_process() {
  runtime_verified || return 1
  local deadline
  "$CONTROL_PY" "$HELPER" socket-control \
    --meta "$META_FILE" \
    --socket "$CONTROL_SOCKET" \
    --command stop \
    --proc-root "$PROC_ROOT" \
    >/dev/null
  deadline=$((SECONDS + STOP_TIMEOUT_SEC))
  while meta_verified >/dev/null 2>&1 \
    && [[ "$SECONDS" -lt "$deadline" ]]
  do
    sleep 0.5
  done
  if meta_verified >/dev/null 2>&1; then
    echo "wave1 loader did not stop within ${STOP_TIMEOUT_SEC}s; fail closed without a signal" >&2
    echo "the verified identity metadata and control socket evidence were retained" >&2
    return 1
  fi
  clear_identity
}

stop() {
  if runtime_verified; then
    local pid
    pid="$(pid_file_value)"
    stop_verified_process
    echo "stopped wave1 loader pid=$pid"
    return 0
  fi
  if metadata_exists; then
    if metadata_identity_dead; then
      clear_identity
      echo "wave1 loader is not running; cleared strongly dead metadata without a signal"
      return 0
    elif incomplete_pid_record_is_dead; then
      clear_identity
      echo "wave1 loader is not running; cleared an absent incomplete PID record"
      return 0
    elif pid_candidate_alive; then
      echo "wave1 loader identity mismatch; refusing to signal any PID" >&2
      return 3
    fi
    echo "wave1 loader death is unproven; retained metadata without a signal" >&2
    return 3
  fi
  echo "wave1 loader is not running"
}

status() {
  if runtime_verified; then
    echo "wave1 loader: running pid=$(pid_file_value) log=$LOG_FILE"
  elif metadata_exists; then
    echo "wave1 loader: unverified metadata (no signal will be sent) log=$LOG_FILE"
  else
    echo "wave1 loader: stopped log=$LOG_FILE"
  fi
  if [[ -f "$STATE" ]]; then
    "$CONTROL_PY" - "$STATE" <<'PY'
import json
import sys
from pathlib import Path

data = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
input_data = data.get("input") or {}
counters = data.get("counters") or {}
for key, value in (
    ("updated_at", data.get("updated_at")),
    ("last_progress_at", data.get("last_progress_at")),
    ("offset", input_data.get("offset")),
    ("seen", counters.get("seen")),
    ("inserted", counters.get("inserted")),
    ("duplicate", counters.get("duplicate")),
    ("invalid", counters.get("invalid")),
    ("quality_rejected", counters.get("quality_rejected")),
    ("completed", data.get("completed")),
):
    print(f"{key}: {value}")
PY
  fi
}

logs() {
  tail -n "${1:-80}" "$LOG_FILE" 2>/dev/null || true
}

main() {
  local command="${1:-status}"
  validate_configuration || {
    echo "wave1 loader configuration is invalid" >&2
    return 2
  }
  case "$command" in
    start|stop|restart)
      prepare_runtime_paths
      exec 9>"$LOCK_FILE"
      chmod 600 "$LOCK_FILE"
      flock -w "$LOCK_TIMEOUT_SEC" 9 || {
        echo "another wave1 loader management operation is in progress" >&2
        return 1
      }
      ;;
  esac
  case "$command" in
    start) start ;;
    stop) stop ;;
    restart) stop; sleep 1; start ;;
    status) status ;;
    logs) logs "${2:-80}" ;;
    follow) tail -F "$LOG_FILE" ;;
    *) echo "usage: $0 {start|stop|restart|status|logs|follow}" >&2; return 2 ;;
  esac
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
