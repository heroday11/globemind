#!/usr/bin/env bash
# Lightweight persistent vLLM service controller for environments without systemd.
set -euo pipefail

GLOBEMIND_HOME="${GLOBEMIND_HOME:-/root/data/globemind}"
RUN_DIR="${GLOBEMIND_HOME}/logs"
START_SCRIPT="${GLOBEMIND_HOME}/deploy/start_llm.sh"
SUPERVISOR_PID_FILE="${RUN_DIR}/vllm_service_supervisor.pid"
SERVICE_LOG="${RUN_DIR}/vllm_service_supervisor.log"
VLLM_HOST="${VLLM_HOST:-127.0.0.1}"
VLLM_PORT="${VLLM_PORT:-8004}"
HEALTH_URL="http://${VLLM_HOST}:${VLLM_PORT}/v1/models"

mkdir -p "${RUN_DIR}"

is_pid_alive() {
  local pid="${1:-}"
  [[ -n "${pid}" ]] && kill -0 "${pid}" 2>/dev/null
}

supervisor_pid() {
  if [[ -f "${SUPERVISOR_PID_FILE}" ]]; then
    tr -d '[:space:]' < "${SUPERVISOR_PID_FILE}"
  fi
}

is_supervisor_alive() {
  local pid
  pid="$(supervisor_pid)"
  is_pid_alive "${pid}"
}

api_ready() {
  curl -fsS --max-time 2 "${HEALTH_URL}" >/dev/null 2>&1
}

port_busy() {
  ss -ltnp 2>/dev/null | grep -q ":${VLLM_PORT} "
}

wait_ready() {
  local timeout="${VLLM_START_TIMEOUT_SEC:-600}"
  local start_ts now elapsed
  start_ts="$(date +%s)"
  while true; do
    if api_ready; then
      echo "vLLM API ready: ${HEALTH_URL}"
      return 0
    fi
    now="$(date +%s)"
    elapsed=$((now - start_ts))
    if (( elapsed >= timeout )); then
      echo "vLLM did not become ready within ${timeout}s" >&2
      tail -n 80 "${RUN_DIR}/vllm.log" "${SERVICE_LOG}" 2>/dev/null || true
      return 1
    fi
    sleep 2
  done
}

start_service() {
  if is_supervisor_alive; then
    echo "vLLM supervisor already running: pid=$(supervisor_pid)"
    wait_ready
    return 0
  fi
  if port_busy; then
    echo "port ${VLLM_PORT} is already busy; refusing to start duplicate vLLM" >&2
    ss -ltnp | grep ":${VLLM_PORT} " >&2 || true
    return 1
  fi
  if [[ ! -x "${START_SCRIPT}" ]]; then
    echo "start script not executable: ${START_SCRIPT}" >&2
    return 1
  fi

  export GLOBEMIND_HOME
  export VLLM_PROFILE="${VLLM_PROFILE:-event_extract}"
  export VLLM_MODEL_PATH="${VLLM_MODEL_PATH:-/root/data/models/Qwen2.5-7B-Instruct-AWQ}"
  export VLLM_SERVED_MODEL_NAME="${VLLM_SERVED_MODEL_NAME:-${VLLM_MODEL_PATH} qwen2.5-7b-awq}"
  export VLLM_HOST
  export VLLM_PORT
  export VLLM_RESTART_DELAY_SEC="${VLLM_RESTART_DELAY_SEC:-5}"

  {
    echo "[$(date -Is)] ctl start profile=${VLLM_PROFILE} model=${VLLM_MODEL_PATH} host=${VLLM_HOST} port=${VLLM_PORT}"
  } >> "${SERVICE_LOG}"

  cd "${GLOBEMIND_HOME}"
  setsid bash -c '
    set -u
    while true; do
      echo "[$(date -Is)] supervisor launching ${0}"
      "${0}"
      rc=$?
      echo "[$(date -Is)] vLLM exited rc=${rc}; restart in ${VLLM_RESTART_DELAY_SEC:-5}s"
      sleep "${VLLM_RESTART_DELAY_SEC:-5}"
    done
  ' "${START_SCRIPT}" >> "${SERVICE_LOG}" 2>&1 &

  local pid=$!
  echo "${pid}" > "${SUPERVISOR_PID_FILE}"
  echo "vLLM supervisor started: pid=${pid}"
  wait_ready
}

stop_service() {
  local pid
  pid="$(supervisor_pid)"
  if is_pid_alive "${pid}"; then
    echo "stopping vLLM supervisor process group: pid=${pid}"
    kill -TERM -- "-${pid}" 2>/dev/null || kill -TERM "${pid}" 2>/dev/null || true
    for _ in $(seq 1 30); do
      if ! is_pid_alive "${pid}"; then
        break
      fi
      sleep 1
    done
    if is_pid_alive "${pid}"; then
      echo "force killing vLLM supervisor process group: pid=${pid}"
      kill -KILL -- "-${pid}" 2>/dev/null || kill -KILL "${pid}" 2>/dev/null || true
    fi
  else
    echo "vLLM supervisor is not running"
  fi
  rm -f "${SUPERVISOR_PID_FILE}"
  pkill -TERM -f "vllm.entrypoints.openai.api_server.*Qwen2.5-7B-Instruct-AWQ" 2>/dev/null || true
}

status_service() {
  local pid
  pid="$(supervisor_pid)"
  if is_pid_alive "${pid}"; then
    echo "supervisor: running pid=${pid}"
  else
    echo "supervisor: stopped"
  fi
  if api_ready; then
    echo "api: ready ${HEALTH_URL}"
    curl -fsS --max-time 5 "${HEALTH_URL}" || true
    echo
  else
    echo "api: not ready ${HEALTH_URL}"
  fi
  ss -ltnp 2>/dev/null | grep ":${VLLM_PORT} " || true
  nvidia-smi --query-gpu=name,memory.used,memory.total,utilization.gpu,temperature.gpu --format=csv,noheader,nounits || true
}

test_request() {
  curl -fsS --max-time 60 \
    -H "Content-Type: application/json" \
    -X POST "http://${VLLM_HOST}:${VLLM_PORT}/v1/chat/completions" \
    -d '{
      "model": "qwen2.5-7b-awq",
      "messages": [
        {"role": "system", "content": "Only output compact JSON."},
        {"role": "user", "content": "Extract event fields from: US and China held trade talks in Geneva. Return {\"domain\",\"event_type\",\"initiator\",\"target\"}."}
      ],
      "temperature": 0,
      "max_tokens": 80
    }'
  echo
}

case "${1:-status}" in
  start)
    start_service
    ;;
  stop)
    stop_service
    ;;
  restart)
    stop_service
    start_service
    ;;
  status)
    status_service
    ;;
  test)
    test_request
    ;;
  logs)
    tail -n "${TAIL_LINES:-120}" "${SERVICE_LOG}" "${RUN_DIR}/vllm.log" 2>/dev/null || true
    ;;
  follow)
    tail -f "${SERVICE_LOG}" "${RUN_DIR}/vllm.log"
    ;;
  *)
    echo "usage: $0 {start|stop|restart|status|test|logs|follow}" >&2
    exit 2
    ;;
esac
