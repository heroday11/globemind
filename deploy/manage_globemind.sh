#!/usr/bin/env bash
set -euo pipefail

GLOBEMIND_HOME="${GLOBEMIND_HOME:-/root/data/globemind}"
cd "$GLOBEMIND_HOME"
mkdir -p "$GLOBEMIND_HOME/logs" "$GLOBEMIND_HOME/tmp"

PM2_BIN="${PM2_BIN:-$(command -v pm2 || true)}"
if [ -z "$PM2_BIN" ] && [ -x "/usr/local/bin/pm2" ]; then
  PM2_BIN="/usr/local/bin/pm2"
fi
if [ -z "$PM2_BIN" ]; then
  echo "[ERROR] pm2 not found in PATH"
  exit 1
fi

wait_http_ok() {
  local url="$1"
  local timeout_sec="${2:-180}"
  local waited=0
  until curl -fsS "$url" >/dev/null 2>&1; do
    sleep 2
    waited=$((waited + 2))
    if [ "$waited" -ge "$timeout_sec" ]; then
      echo "[ERROR] timeout waiting for $url"
      return 1
    fi
  done
  echo "[OK] $url"
}

wait_gpu_mem_stable() {
  local samples="${1:-3}"
  local interval_sec="${2:-6}"
  local prev=""
  echo "[INFO] sampling GPU memory.used (${samples}x, interval ${interval_sec}s)"
  for i in $(seq 1 "$samples"); do
    cur=$(nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits | head -n1 | tr -d ' ')
    echo "[INFO] memory.used sample $i: ${cur} MiB"
    if [ -n "$prev" ] && [ "$cur" = "$prev" ] && [ "$i" -ge 2 ]; then
      echo "[INFO] consecutive identical reading; treating as stable"
      return 0
    fi
    prev="$cur"
    if [ "$i" -lt "$samples" ]; then
      sleep "$interval_sec"
    fi
  done
}

start_all() {
  echo "[INFO] starting unified-models (BGE-M3 + GLiNER, translate -> :8004), GLOBEMIND_HOME=$GLOBEMIND_HOME"
  export GLOBEMIND_HOME
  "$PM2_BIN" delete unified-models llm-vllm llm-translator >/dev/null 2>&1 || true
  "$PM2_BIN" start ecosystem.config.js --only unified-models
  wait_http_ok "http://127.0.0.1:8001/healthz" 300
  echo "[INFO] waiting for unified-models VRAM to settle before vLLM"
  sleep 10
  wait_gpu_mem_stable 3 6
  echo "[INFO] starting llm-vllm (Qwen3.5-9B vLLM on :8004, see start_llm.sh)"
  "$PM2_BIN" start ecosystem.config.js --only llm-vllm
  sleep 8
  "$PM2_BIN" status
  nvidia-smi --query-gpu=memory.total,memory.used,memory.free,utilization.gpu --format=csv,noheader,nounits
}

stop_all() {
  "$PM2_BIN" stop unified-models llm-vllm llm-translator >/dev/null 2>&1 || true
  "$PM2_BIN" delete unified-models llm-vllm llm-translator >/dev/null 2>&1 || true
  pkill -f "vllm.entrypoints.openai.api_server" >/dev/null 2>&1 || true
  pkill -f "api_services.unified_models_service" >/dev/null 2>&1 || true
  pkill -f "unified_models_service:app" >/dev/null 2>&1 || true
  sleep 2
  nvidia-smi --query-gpu=memory.total,memory.used,memory.free,utilization.gpu --format=csv,noheader,nounits
}

restart_all() {
  stop_all || true
  start_all
}

status_all() {
  "$PM2_BIN" status || true
  nvidia-smi
}

ACTION="${1:-}"
if [ -z "$ACTION" ]; then
  echo "GlobeMind service manager (unified-models :8001, llm-vllm :8004)"
  echo "1) start"
  echo "2) stop"
  echo "3) status"
  echo "4) restart"
  read -r -p "Select action [1-4]: " choice
  case "$choice" in
    1) ACTION="start" ;;
    2) ACTION="stop" ;;
    3) ACTION="status" ;;
    4) ACTION="restart" ;;
    *) echo "Invalid choice"; exit 1 ;;
  esac
fi

case "$ACTION" in
  start)
    start_all
    ;;
  stop)
    stop_all
    ;;
  restart)
    restart_all
    ;;
  status)
    status_all
    ;;
  *)
    echo "Usage: $0 {start|stop|restart|status}"
    exit 1
    ;;
esac
