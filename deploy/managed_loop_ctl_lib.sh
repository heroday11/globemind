#!/usr/bin/env bash

# Shared lifecycle primitives for setsid-managed shell loops. Callers must set
# the MANAGED_LOOP_* identity and path variables before sourcing this file.

MANAGED_LOOP_META_FILE="${MANAGED_LOOP_META_FILE:-${MANAGED_LOOP_PID_FILE}.meta}"
MANAGED_LOOP_CONTROL_LOCK="${MANAGED_LOOP_CONTROL_LOCK:-${MANAGED_LOOP_PID_FILE}.ctl.lock}"
MANAGED_LOOP_PROC_ROOT="${MANAGED_LOOP_PROC_ROOT:-/proc}"
MANAGED_LOOP_START_TIMEOUT_SEC="${MANAGED_LOOP_START_TIMEOUT_SEC:-5}"
MANAGED_LOOP_START_STABILITY_SEC="${MANAGED_LOOP_START_STABILITY_SEC:-1}"
MANAGED_LOOP_STOP_TIMEOUT_SEC="${MANAGED_LOOP_STOP_TIMEOUT_SEC:-30}"
MANAGED_LOOP_KILL_TIMEOUT_SEC="${MANAGED_LOOP_KILL_TIMEOUT_SEC:-2}"
MANAGED_LOOP_LOCK_TIMEOUT_SEC="${MANAGED_LOOP_LOCK_TIMEOUT_SEC:-10}"

managed_loop_validate_config() {
  local root loop expected_exe
  [[ "${MANAGED_LOOP_SERVICE_ID:-}" =~ ^[A-Za-z0-9._-]+$ ]] || return 2
  [[ -n "${MANAGED_LOOP_LABEL:-}" ]] || return 2
  [[ -n "${MANAGED_LOOP_ROOT:-}" && -n "${MANAGED_LOOP_PID_FILE:-}" ]] || return 2
  [[ -n "${MANAGED_LOOP_LOG_FILE:-}" && -n "${MANAGED_LOOP_LOOP_SCRIPT:-}" ]] || return 2
  [[ "$MANAGED_LOOP_START_TIMEOUT_SEC" =~ ^[1-9][0-9]*$ ]] || return 2
  [[ "$MANAGED_LOOP_STOP_TIMEOUT_SEC" =~ ^[1-9][0-9]*$ ]] || return 2
  [[ "$MANAGED_LOOP_KILL_TIMEOUT_SEC" =~ ^[1-9][0-9]*$ ]] || return 2
  [[ "$MANAGED_LOOP_LOCK_TIMEOUT_SEC" =~ ^[1-9][0-9]*$ ]] || return 2
  [[ "$MANAGED_LOOP_START_STABILITY_SEC" =~ ^[0-9]+([.][0-9]+)?$ ]] || return 2
  [[ -d "$MANAGED_LOOP_PROC_ROOT" ]] || return 2
  command -v setsid >/dev/null 2>&1 || return 2
  command -v flock >/dev/null 2>&1 || return 2

  root="$(realpath -e "$MANAGED_LOOP_ROOT" 2>/dev/null || true)"
  loop="$(realpath -e "$MANAGED_LOOP_LOOP_SCRIPT" 2>/dev/null || true)"
  expected_exe="$(realpath -e "$(command -v bash)" 2>/dev/null || true)"
  [[ -n "$root" && -n "$loop" && -n "$expected_exe" && -x "$loop" ]] || return 2
  case "$loop" in
    "$root"/deploy/*) ;;
    *) return 2 ;;
  esac
  case "$MANAGED_LOOP_PID_FILE" in
    "$root"/logs/*) ;;
    *) return 2 ;;
  esac
  [[ "$MANAGED_LOOP_META_FILE" == "${MANAGED_LOOP_PID_FILE}.meta" ]] || return 2
  [[ "$MANAGED_LOOP_CONTROL_LOCK" == "${MANAGED_LOOP_PID_FILE}.ctl.lock" ]] || return 2
  MANAGED_LOOP_ROOT="$root"
  MANAGED_LOOP_LOOP_SCRIPT="$loop"
  MANAGED_LOOP_EXPECTED_EXE="$expected_exe"
}

managed_loop_prepare_paths() {
  local log_dir
  log_dir="$(dirname "$MANAGED_LOOP_PID_FILE")"
  [[ ! -L "$log_dir" ]] || return 2
  mkdir -p "$log_dir"
  [[ -d "$log_dir" && ! -L "$log_dir" ]] || return 2
  if [[ -e "$MANAGED_LOOP_CONTROL_LOCK" ]]; then
    [[ -f "$MANAGED_LOOP_CONTROL_LOCK" && ! -L "$MANAGED_LOOP_CONTROL_LOCK" ]] || return 2
  fi
}

managed_loop_read_pid_file() {
  local raw
  MANAGED_LOOP_PID=""
  [[ -f "$MANAGED_LOOP_PID_FILE" && ! -L "$MANAGED_LOOP_PID_FILE" ]] || return 1
  raw="$(<"$MANAGED_LOOP_PID_FILE")" || return 1
  [[ "$raw" =~ ^[1-9][0-9]*$ ]] || return 1
  MANAGED_LOOP_PID="$raw"
}

managed_loop_read_meta() {
  local raw extra
  MANAGED_LOOP_META_PID=""
  MANAGED_LOOP_META_TICKS=""
  MANAGED_LOOP_META_PGID=""
  MANAGED_LOOP_META_SID=""
  MANAGED_LOOP_META_SERVICE=""
  [[ -f "$MANAGED_LOOP_META_FILE" && ! -L "$MANAGED_LOOP_META_FILE" ]] || return 1
  raw="$(<"$MANAGED_LOOP_META_FILE")" || return 1
  [[ "$raw" != *$'\n'* ]] || return 1
  read -r \
    MANAGED_LOOP_META_PID MANAGED_LOOP_META_TICKS MANAGED_LOOP_META_PGID \
    MANAGED_LOOP_META_SID MANAGED_LOOP_META_SERVICE extra <<<"$raw"
  [[ "$MANAGED_LOOP_META_PID" =~ ^[1-9][0-9]*$ ]] || return 1
  [[ "$MANAGED_LOOP_META_TICKS" =~ ^[1-9][0-9]*$ ]] || return 1
  [[ "$MANAGED_LOOP_META_PGID" =~ ^[1-9][0-9]*$ ]] || return 1
  [[ "$MANAGED_LOOP_META_SID" =~ ^[1-9][0-9]*$ ]] || return 1
  [[ "$MANAGED_LOOP_META_SERVICE" =~ ^[A-Za-z0-9._-]+$ && -z "$extra" ]] || return 1
}

managed_loop_read_process() {
  local pid="$1" stat_line tail
  local -a fields
  MANAGED_LOOP_CURRENT_STATE=""
  MANAGED_LOOP_CURRENT_TICKS=""
  MANAGED_LOOP_CURRENT_PGID=""
  MANAGED_LOOP_CURRENT_SID=""
  [[ "$pid" =~ ^[1-9][0-9]*$ ]] || return 1
  IFS= read -r stat_line <"$MANAGED_LOOP_PROC_ROOT/$pid/stat" 2>/dev/null || return 1
  [[ "$stat_line" == *") "* ]] || return 1
  tail="${stat_line##*) }"
  read -r -a fields <<<"$tail"
  [[ "${#fields[@]}" -gt 19 ]] || return 1
  MANAGED_LOOP_CURRENT_STATE="${fields[0]}"
  MANAGED_LOOP_CURRENT_PGID="${fields[2]}"
  MANAGED_LOOP_CURRENT_SID="${fields[3]}"
  MANAGED_LOOP_CURRENT_TICKS="${fields[19]}"
  [[ "$MANAGED_LOOP_CURRENT_STATE" != "Z" ]] || return 1
  [[ "$MANAGED_LOOP_CURRENT_STATE" != "X" && "$MANAGED_LOOP_CURRENT_STATE" != "x" ]] || return 1
  [[ "$MANAGED_LOOP_CURRENT_TICKS" =~ ^[1-9][0-9]*$ ]] || return 1
  [[ "$MANAGED_LOOP_CURRENT_PGID" =~ ^[1-9][0-9]*$ ]] || return 1
  [[ "$MANAGED_LOOP_CURRENT_SID" =~ ^[1-9][0-9]*$ ]] || return 1
}

managed_loop_cmdline_matches_exact_loop() {
  local pid="$1" argument argument_index=0
  [[ -r "$MANAGED_LOOP_PROC_ROOT/$pid/cmdline" ]] || return 2
  while IFS= read -r -d '' argument; do
    case "$argument_index" in
      0) ;;
      1) [[ "$argument" == "$MANAGED_LOOP_LOOP_SCRIPT" ]] || return 1 ;;
      *) return 1 ;;
    esac
    argument_index=$((argument_index + 1))
  done <"$MANAGED_LOOP_PROC_ROOT/$pid/cmdline"
  [[ "$argument_index" == "2" ]] || return 2
}

managed_loop_process_matches_workload() {
  local pid="$1" executable cwd
  managed_loop_read_process "$pid" || return 2
  executable="$(readlink -f "$MANAGED_LOOP_PROC_ROOT/$pid/exe" 2>/dev/null || true)"
  cwd="$(readlink -f "$MANAGED_LOOP_PROC_ROOT/$pid/cwd" 2>/dev/null || true)"
  [[ -n "$executable" && -n "$cwd" ]] || return 2
  [[ "$executable" == "$MANAGED_LOOP_EXPECTED_EXE" ]] || return 1
  [[ "$cwd" == "$MANAGED_LOOP_ROOT" ]] || return 1
  managed_loop_cmdline_matches_exact_loop "$pid"
}

managed_loop_process_matches_expected() {
  local pid="$1"
  managed_loop_process_matches_workload "$pid" || return 1
  [[ "$MANAGED_LOOP_CURRENT_PGID" == "$pid" && "$MANAGED_LOOP_CURRENT_SID" == "$pid" ]]
}

managed_loop_ticks_mismatch_status() {
  local pid="$1" workload_status
  if managed_loop_process_matches_workload "$pid"; then
    MANAGED_LOOP_OBS_STATUS="unverified"
    return 0
  else
    workload_status="$?"
  fi
  if [[ "$workload_status" == "1" ]]; then
    MANAGED_LOOP_OBS_STATUS="stale"
  else
    MANAGED_LOOP_OBS_STATUS="unverified"
  fi
}

managed_loop_verify_instance() {
  local pid meta_pid meta_ticks meta_pgid meta_sid meta_service
  managed_loop_read_pid_file || return 1
  pid="$MANAGED_LOOP_PID"
  managed_loop_read_meta || return 1
  meta_pid="$MANAGED_LOOP_META_PID"
  meta_ticks="$MANAGED_LOOP_META_TICKS"
  meta_pgid="$MANAGED_LOOP_META_PGID"
  meta_sid="$MANAGED_LOOP_META_SID"
  meta_service="$MANAGED_LOOP_META_SERVICE"
  [[ "$pid" == "$meta_pid" && "$meta_service" == "$MANAGED_LOOP_SERVICE_ID" ]] || return 1
  [[ "$meta_pgid" == "$pid" && "$meta_sid" == "$pid" ]] || return 1
  managed_loop_process_matches_expected "$pid" || return 1
  [[ "$MANAGED_LOOP_CURRENT_TICKS" == "$meta_ticks" ]] || return 1
  [[ "$MANAGED_LOOP_CURRENT_PGID" == "$meta_pgid" ]] || return 1
  [[ "$MANAGED_LOOP_CURRENT_SID" == "$meta_sid" ]] || return 1
  MANAGED_LOOP_VERIFIED_PID="$pid"
  MANAGED_LOOP_VERIFIED_TICKS="$meta_ticks"
}

managed_loop_observe() {
  local has_records=0 pid_valid=0 meta_valid=0 pid="" meta_pid="" meta_ticks=""
  MANAGED_LOOP_OBS_STATUS="stopped"
  MANAGED_LOOP_OBS_PID=""
  [[ -e "$MANAGED_LOOP_PID_FILE" || -L "$MANAGED_LOOP_PID_FILE" ]] && has_records=1
  [[ -e "$MANAGED_LOOP_META_FILE" || -L "$MANAGED_LOOP_META_FILE" ]] && has_records=1
  if managed_loop_read_pid_file; then
    pid_valid=1
    pid="$MANAGED_LOOP_PID"
  fi
  if managed_loop_read_meta; then
    meta_valid=1
    meta_pid="$MANAGED_LOOP_META_PID"
    meta_ticks="$MANAGED_LOOP_META_TICKS"
  fi
  if [[ "$pid_valid" == "1" && "$meta_valid" == "1" && "$pid" == "$meta_pid" ]]; then
    MANAGED_LOOP_OBS_PID="$pid"
    if ! managed_loop_read_process "$pid"; then
      if [[ -e "$MANAGED_LOOP_PROC_ROOT/$pid/stat" ]]; then
        MANAGED_LOOP_OBS_STATUS="unverified"
      else
        MANAGED_LOOP_OBS_STATUS="stale"
      fi
    elif [[ "$MANAGED_LOOP_CURRENT_TICKS" != "$meta_ticks" ]]; then
      managed_loop_ticks_mismatch_status "$pid"
    elif managed_loop_verify_instance; then
      MANAGED_LOOP_OBS_STATUS="verified"
    else
      MANAGED_LOOP_OBS_STATUS="unverified"
    fi
    return 0
  fi
  if [[ "$pid_valid" == "1" ]]; then
    if managed_loop_read_process "$pid" || [[ -e "$MANAGED_LOOP_PROC_ROOT/$pid/stat" ]]; then
      MANAGED_LOOP_OBS_STATUS="unverified"
      MANAGED_LOOP_OBS_PID="$pid"
      return 0
    fi
  fi
  if [[ "$meta_valid" == "1" ]]; then
    MANAGED_LOOP_OBS_PID="$meta_pid"
    if managed_loop_read_process "$meta_pid"; then
      if [[ "$MANAGED_LOOP_CURRENT_TICKS" == "$meta_ticks" ]]; then
        MANAGED_LOOP_OBS_STATUS="unverified"
        return 0
      fi
      managed_loop_ticks_mismatch_status "$meta_pid"
      return 0
    elif [[ -e "$MANAGED_LOOP_PROC_ROOT/$meta_pid/stat" ]]; then
      MANAGED_LOOP_OBS_STATUS="unverified"
      return 0
    fi
  fi
  if [[ "$has_records" == "1" && ("$pid_valid" == "1" || "$meta_valid" == "1") ]]; then
    MANAGED_LOOP_OBS_STATUS="stale"
  elif [[ "$has_records" == "1" ]]; then
    MANAGED_LOOP_OBS_STATUS="unverified"
  fi
}

managed_loop_atomic_identity_write() {
  local pid="$1" expected_ticks="$2" meta_tmp pid_tmp directory
  managed_loop_process_matches_expected "$pid" || return 1
  [[ "$MANAGED_LOOP_CURRENT_TICKS" == "$expected_ticks" ]] || return 1
  directory="$(dirname "$MANAGED_LOOP_PID_FILE")"
  meta_tmp="$(mktemp "$directory/.${MANAGED_LOOP_SERVICE_ID}.meta.XXXXXX")" || return 1
  pid_tmp="$(mktemp "$directory/.${MANAGED_LOOP_SERVICE_ID}.pid.XXXXXX")" || {
    rm -f -- "$meta_tmp"
    return 1
  }
  # Metadata is published first; the PID rename is the commit point. Readers
  # require both records to agree before treating the process as managed.
  if ! printf '%s %s %s %s %s\n' \
    "$pid" "$MANAGED_LOOP_CURRENT_TICKS" "$MANAGED_LOOP_CURRENT_PGID" \
    "$MANAGED_LOOP_CURRENT_SID" "$MANAGED_LOOP_SERVICE_ID" >"$meta_tmp" ||
    ! printf '%s\n' "$pid" >"$pid_tmp" ||
    ! chmod 0640 "$meta_tmp" "$pid_tmp" ||
    ! mv -fT "$meta_tmp" "$MANAGED_LOOP_META_FILE" ||
    ! mv -fT "$pid_tmp" "$MANAGED_LOOP_PID_FILE"
  then
    rm -f -- "$meta_tmp" "$pid_tmp"
    return 1
  fi
  return 0
}

managed_loop_clear_identity() {
  rm -f -- "$MANAGED_LOOP_PID_FILE" "$MANAGED_LOOP_META_FILE"
}

managed_loop_records_match() {
  local expected_pid="$1" expected_ticks="$2"
  if managed_loop_read_pid_file && [[ "$MANAGED_LOOP_PID" != "$expected_pid" ]]; then
    return 1
  fi
  managed_loop_read_meta || return 1
  [[ "$MANAGED_LOOP_META_PID" == "$expected_pid" ]] || return 1
  [[ "$MANAGED_LOOP_META_TICKS" == "$expected_ticks" ]] || return 1
  [[ "$MANAGED_LOOP_META_PGID" == "$expected_pid" ]] || return 1
  [[ "$MANAGED_LOOP_META_SID" == "$expected_pid" ]] || return 1
  [[ "$MANAGED_LOOP_META_SERVICE" == "$MANAGED_LOOP_SERVICE_ID" ]]
}

managed_loop_identity_is_dead() {
  local pid ticks
  managed_loop_read_pid_file || return 1
  pid="$MANAGED_LOOP_PID"
  managed_loop_read_meta || return 1
  [[ "$MANAGED_LOOP_META_PID" == "$pid" ]] || return 1
  [[ "$MANAGED_LOOP_META_PGID" == "$pid" && "$MANAGED_LOOP_META_SID" == "$pid" ]] || return 1
  [[ "$MANAGED_LOOP_META_SERVICE" == "$MANAGED_LOOP_SERVICE_ID" ]] || return 1
  ticks="$MANAGED_LOOP_META_TICKS"
  if ! managed_loop_read_process "$pid"; then
    [[ ! -e "$MANAGED_LOOP_PROC_ROOT/$pid/stat" ]]
    return
  fi
  [[ "$MANAGED_LOOP_CURRENT_TICKS" != "$ticks" ]]
}

managed_loop_wait_identity_dead() {
  local deadline
  deadline=$((SECONDS + MANAGED_LOOP_KILL_TIMEOUT_SEC))
  while [[ "$SECONDS" -lt "$deadline" ]]; do
    managed_loop_identity_is_dead && return 0
    sleep 0.1
  done
  managed_loop_identity_is_dead
}

managed_loop_wait_for_expected() {
  local pid="$1" expected_ticks="$2" deadline
  deadline=$((SECONDS + MANAGED_LOOP_START_TIMEOUT_SEC))
  while [[ "$SECONDS" -le "$deadline" ]]; do
    if managed_loop_process_matches_expected "$pid" &&
      [[ "$MANAGED_LOOP_CURRENT_TICKS" == "$expected_ticks" ]]
    then
      return 0
    fi
    [[ -e "$MANAGED_LOOP_PROC_ROOT/$pid/stat" ]] || return 1
    sleep 0.05
  done
  return 1
}

managed_loop_capture_birth() {
  local pid="$1" deadline
  deadline=$((SECONDS + MANAGED_LOOP_START_TIMEOUT_SEC))
  while [[ "$SECONDS" -le "$deadline" ]]; do
    if managed_loop_read_process "$pid"; then
      MANAGED_LOOP_BIRTH_TICKS="$MANAGED_LOOP_CURRENT_TICKS"
      return 0
    fi
    [[ -e "$MANAGED_LOOP_PROC_ROOT/$pid/stat" ]] || return 1
    sleep 0.01
  done
  return 1
}

managed_loop_verify_fresh() {
  local pid="$1" ticks="$2"
  managed_loop_process_matches_expected "$pid" || return 1
  [[ "$MANAGED_LOOP_CURRENT_TICKS" == "$ticks" ]]
}

managed_loop_fresh_identity_is_dead() {
  local pid="$1" ticks="$2"
  if ! managed_loop_read_process "$pid"; then
    [[ ! -e "$MANAGED_LOOP_PROC_ROOT/$pid/stat" ]]
    return
  fi
  [[ "$MANAGED_LOOP_CURRENT_TICKS" != "$ticks" ]]
}

managed_loop_wait_fresh_identity_dead() {
  local pid="$1" ticks="$2" deadline
  deadline=$((SECONDS + MANAGED_LOOP_KILL_TIMEOUT_SEC))
  while [[ "$SECONDS" -lt "$deadline" ]]; do
    managed_loop_fresh_identity_is_dead "$pid" "$ticks" && return 0
    sleep 0.1
  done
  managed_loop_fresh_identity_is_dead "$pid" "$ticks"
}

managed_loop_cleanup_fresh() {
  local pid="$1" ticks="$2" deadline
  if managed_loop_verify_fresh "$pid" "$ticks"; then
    kill -TERM -- "-$pid" 2>/dev/null || true
    deadline=$((SECONDS + MANAGED_LOOP_KILL_TIMEOUT_SEC))
    while managed_loop_verify_fresh "$pid" "$ticks" && [[ "$SECONDS" -lt "$deadline" ]]; do
      sleep 0.1
    done
    if managed_loop_verify_fresh "$pid" "$ticks"; then
      kill -KILL -- "-$pid" 2>/dev/null || true
      deadline=$((SECONDS + MANAGED_LOOP_KILL_TIMEOUT_SEC))
      while managed_loop_verify_fresh "$pid" "$ticks" && [[ "$SECONDS" -lt "$deadline" ]]; do
        sleep 0.1
      done
    fi
  fi
  if ! managed_loop_wait_fresh_identity_dead "$pid" "$ticks"; then
    return 1
  fi
  if managed_loop_records_match "$pid" "$ticks"; then
    managed_loop_clear_identity
  fi
  return 0
}

managed_loop_start() {
  local candidate_pid candidate_ticks
  managed_loop_observe
  case "$MANAGED_LOOP_OBS_STATUS" in
    verified)
      echo "$MANAGED_LOOP_LABEL already running pid=$MANAGED_LOOP_OBS_PID"
      return 0
      ;;
    unverified)
      echo "$MANAGED_LOOP_LABEL has unverified live metadata; refusing automatic takeover" >&2
      return 3
      ;;
    stale)
      echo "clearing stale $MANAGED_LOOP_LABEL metadata; no signal sent"
      managed_loop_clear_identity
      ;;
  esac

  (
    cd "$MANAGED_LOOP_ROOT"
    exec setsid "$MANAGED_LOOP_LOOP_SCRIPT" >/dev/null 2>&1 < /dev/null 9>&-
  ) &
  candidate_pid="$!"
  if ! managed_loop_capture_birth "$candidate_pid"; then
    wait "$candidate_pid" 2>/dev/null || true
    echo "$MANAGED_LOOP_LABEL failed before verified startup" >&2
    return 1
  fi
  candidate_ticks="$MANAGED_LOOP_BIRTH_TICKS"
  if ! managed_loop_wait_for_expected "$candidate_pid" "$candidate_ticks"; then
    if managed_loop_read_process "$candidate_pid"; then
      echo "$MANAGED_LOOP_LABEL never reached verified startup; live candidate was not signaled" >&2
    else
      wait "$candidate_pid" 2>/dev/null || true
      echo "$MANAGED_LOOP_LABEL failed before verified startup" >&2
    fi
    return 1
  fi
  if ! managed_loop_atomic_identity_write "$candidate_pid" "$candidate_ticks"; then
    if managed_loop_cleanup_fresh "$candidate_pid" "$candidate_ticks"; then
      echo "$MANAGED_LOOP_LABEL identity could not be recorded; verified candidate was cleaned up" >&2
    else
      echo "$MANAGED_LOOP_LABEL identity could not be recorded; candidate death is unproven" >&2
    fi
    return 1
  fi
  sleep "$MANAGED_LOOP_START_STABILITY_SEC"
  if ! managed_loop_verify_instance; then
    if managed_loop_cleanup_fresh "$candidate_pid" "$candidate_ticks"; then
      echo "$MANAGED_LOOP_LABEL failed startup stability verification; candidate cleaned up" >&2
    else
      echo "$MANAGED_LOOP_LABEL failed startup stability verification; candidate death is unproven" >&2
    fi
    return 1
  fi
  echo "started $MANAGED_LOOP_LABEL pid=$candidate_pid log=$MANAGED_LOOP_LOG_FILE"
}

managed_loop_signal_verified() {
  local signal_name="$1" pid
  managed_loop_verify_instance || return 1
  pid="$MANAGED_LOOP_VERIFIED_PID"
  kill "-$signal_name" -- "-$pid" 2>/dev/null
}

managed_loop_stop() {
  local pid deadline killed=0
  managed_loop_observe
  case "$MANAGED_LOOP_OBS_STATUS" in
    stopped)
      echo "$MANAGED_LOOP_LABEL not running"
      return 0
      ;;
    stale)
      managed_loop_clear_identity
      echo "$MANAGED_LOOP_LABEL not running; cleared stale metadata without a signal"
      return 0
      ;;
    unverified)
      echo "$MANAGED_LOOP_LABEL identity is unverified; refusing to signal pid=${MANAGED_LOOP_OBS_PID:-unknown}" >&2
      return 3
      ;;
  esac

  pid="$MANAGED_LOOP_VERIFIED_PID"
  if ! managed_loop_signal_verified TERM; then
    if managed_loop_identity_is_dead; then
      managed_loop_clear_identity
      echo "stopped $MANAGED_LOOP_LABEL"
      return 0
    fi
    echo "$MANAGED_LOOP_LABEL identity changed before TERM; no signal sent" >&2
    return 3
  fi
  deadline=$((SECONDS + MANAGED_LOOP_STOP_TIMEOUT_SEC))
  while managed_loop_verify_instance && [[ "$SECONDS" -lt "$deadline" ]]; do
    sleep 0.2
  done
  if managed_loop_verify_instance; then
    if ! managed_loop_signal_verified KILL; then
      echo "$MANAGED_LOOP_LABEL identity changed before KILL; metadata retained" >&2
      return 3
    fi
    killed=1
    deadline=$((SECONDS + MANAGED_LOOP_KILL_TIMEOUT_SEC))
    while managed_loop_verify_instance && [[ "$SECONDS" -lt "$deadline" ]]; do
      sleep 0.1
    done
  fi
  if managed_loop_verify_instance; then
    echo "$MANAGED_LOOP_LABEL did not exit after bounded waits; metadata retained" >&2
    return 1
  fi
  if ! managed_loop_wait_identity_dead; then
    echo "$MANAGED_LOOP_LABEL death is unproven; metadata retained" >&2
    return 3
  fi
  managed_loop_clear_identity
  if [[ "$killed" == "1" ]]; then
    echo "killed $MANAGED_LOOP_LABEL"
  else
    echo "stopped $MANAGED_LOOP_LABEL"
  fi
}

managed_loop_status() {
  managed_loop_observe
  case "$MANAGED_LOOP_OBS_STATUS" in
    verified)
      echo "$MANAGED_LOOP_LABEL: running pid=$MANAGED_LOOP_OBS_PID log=$MANAGED_LOOP_LOG_FILE"
      ;;
    unverified)
      echo "$MANAGED_LOOP_LABEL: unverified live metadata pid=$MANAGED_LOOP_OBS_PID log=$MANAGED_LOOP_LOG_FILE"
      ;;
    stale)
      echo "$MANAGED_LOOP_LABEL: stale metadata log=$MANAGED_LOOP_LOG_FILE"
      ;;
    *)
      echo "$MANAGED_LOOP_LABEL: stopped log=$MANAGED_LOOP_LOG_FILE"
      ;;
  esac
}

managed_loop_acquire_lock() {
  [[ ! -L "$MANAGED_LOOP_CONTROL_LOCK" ]] || return 2
  exec 9>"$MANAGED_LOOP_CONTROL_LOCK"
  chmod 0640 "$MANAGED_LOOP_CONTROL_LOCK"
  flock -w "$MANAGED_LOOP_LOCK_TIMEOUT_SEC" 9
}

managed_loop_main() {
  local operation="${1:-status}"
  if ! managed_loop_validate_config; then
    echo "$MANAGED_LOOP_LABEL controller configuration is invalid" >&2
    return 2
  fi
  case "$operation" in
    start|stop|restart)
      managed_loop_prepare_paths || {
        echo "$MANAGED_LOOP_LABEL runtime paths are invalid" >&2
        return 2
      }
      if ! managed_loop_acquire_lock; then
        echo "$MANAGED_LOOP_LABEL controller lock is busy" >&2
        return 1
      fi
      ;;
  esac
  case "$operation" in
    start) managed_loop_start ;;
    stop) managed_loop_stop ;;
    restart) managed_loop_stop && managed_loop_start ;;
    status) managed_loop_status ;;
    logs) tail -n "${2:-80}" "$MANAGED_LOOP_LOG_FILE" 2>/dev/null || true ;;
    follow) tail -F "$MANAGED_LOOP_LOG_FILE" ;;
    *)
      echo "usage: $0 {start|stop|restart|status|logs|follow}" >&2
      return 2
      ;;
  esac
}
