#!/usr/bin/env bash
# vLLM launcher for GlobeMind.
#
# Profiles:
#   domain_judge  - short prompts + short JSON labels; highest throughput and lower KV cache pressure.
#   event_extract - L1/news 7-field extraction; short context + high concurrency on A30 24GB.
#   long_context  - manual opt-in for long-context work; consumes more KV cache.
#
# Override any setting through environment variables before starting PM2, e.g.:
#   VLLM_PROFILE=event_extract VLLM_GPU_MEMORY_UTILIZATION=0.72 pm2 restart llm-vllm --update-env
set -euo pipefail

cd "${GLOBEMIND_HOME:-/root/data/globemind}"
source /opt/conda/etc/profile.d/conda.sh
conda activate Globemind_env
mkdir -p logs

export CUDA_VISIBLE_DEVICES="${CUDA_VISIBLE_DEVICES:-0}"
export TOKENIZERS_PARALLELISM="${TOKENIZERS_PARALLELISM:-false}"
export OMP_NUM_THREADS="${OMP_NUM_THREADS:-4}"

VLLM_PROFILE="${VLLM_PROFILE:-event_extract}"
VLLM_MODEL_PATH="${VLLM_MODEL_PATH:-/root/data/models/Qwen2.5-7B-Instruct-AWQ}"
# Keep the full path for existing code and add a short alias for manual/API calls.
VLLM_SERVED_MODEL_NAME="${VLLM_SERVED_MODEL_NAME:-${VLLM_MODEL_PATH} qwen2.5-7b-awq}"
VLLM_HOST="${VLLM_HOST:-127.0.0.1}"
VLLM_PORT="${VLLM_PORT:-8004}"
VLLM_DTYPE="${VLLM_DTYPE:-half}"

case "${VLLM_PROFILE}" in
  domain_judge)
    DEFAULT_MAX_MODEL_LEN=2048
    DEFAULT_MAX_NUM_SEQS=256
    DEFAULT_MAX_NUM_BATCHED_TOKENS=8192
    DEFAULT_GPU_MEMORY_UTILIZATION=0.78
    DEFAULT_KV_CACHE_MEMORY_BYTES=4294967296
    ;;
  event_extract)
    DEFAULT_MAX_MODEL_LEN=2048
    DEFAULT_MAX_NUM_SEQS=256
    DEFAULT_MAX_NUM_BATCHED_TOKENS=8192
    DEFAULT_GPU_MEMORY_UTILIZATION=0.90
    # Current 400-char L1 extraction peaks around 1.7GiB KV at 192-way concurrency.
    # Keep ~2x headroom and avoid reserving most of A30 24GB for unused cache.
    DEFAULT_KV_CACHE_MEMORY_BYTES=4294967296
    ;;
  long_context)
    DEFAULT_MAX_MODEL_LEN=8192
    DEFAULT_MAX_NUM_SEQS=40
    DEFAULT_MAX_NUM_BATCHED_TOKENS=8192
    DEFAULT_GPU_MEMORY_UTILIZATION=0.82
    DEFAULT_KV_CACHE_MEMORY_BYTES=
    ;;
  *)
    echo "unknown VLLM_PROFILE=${VLLM_PROFILE}; expected domain_judge, event_extract, or long_context" >&2
    exit 2
    ;;
esac

VLLM_GPU_MEM_UTIL="${VLLM_GPU_MEMORY_UTILIZATION:-${DEFAULT_GPU_MEMORY_UTILIZATION}}"
VLLM_MAX_MODEL_LEN="${VLLM_MAX_MODEL_LEN:-${DEFAULT_MAX_MODEL_LEN}}"
VLLM_MAX_NUM_SEQS="${VLLM_MAX_NUM_SEQS:-${DEFAULT_MAX_NUM_SEQS}}"
VLLM_MAX_NUM_BATCHED_TOKENS="${VLLM_MAX_NUM_BATCHED_TOKENS:-${DEFAULT_MAX_NUM_BATCHED_TOKENS}}"
VLLM_ENFORCE_EAGER="${VLLM_ENFORCE_EAGER:-0}"
VLLM_ENABLE_PREFIX_CACHING="${VLLM_ENABLE_PREFIX_CACHING:-1}"
VLLM_ENABLE_CHUNKED_PREFILL="${VLLM_ENABLE_CHUNKED_PREFILL:-1}"
VLLM_KV_CACHE_DTYPE="${VLLM_KV_CACHE_DTYPE:-auto}"
VLLM_KV_CACHE_MEMORY_BYTES="${VLLM_KV_CACHE_MEMORY_BYTES:-${DEFAULT_KV_CACHE_MEMORY_BYTES:-}}"

read -r -a served_model_names <<< "${VLLM_SERVED_MODEL_NAME}"

args=(
  python -m vllm.entrypoints.openai.api_server
  --model "${VLLM_MODEL_PATH}"
  --served-model-name "${served_model_names[@]}"
  --dtype "${VLLM_DTYPE}"
  --kv-cache-dtype "${VLLM_KV_CACHE_DTYPE}"
  --gpu-memory-utilization "${VLLM_GPU_MEM_UTIL}"
  --max-model-len "${VLLM_MAX_MODEL_LEN}"
  --max-num-seqs "${VLLM_MAX_NUM_SEQS}"
  --max-num-batched-tokens "${VLLM_MAX_NUM_BATCHED_TOKENS}"
  --host "${VLLM_HOST}"
  --port "${VLLM_PORT}"
  --no-enable-log-requests
  --disable-uvicorn-access-log
  --uvicorn-log-level warning
)

if [[ -n "${VLLM_KV_CACHE_MEMORY_BYTES:-}" ]]; then
  args+=(--kv-cache-memory-bytes "${VLLM_KV_CACHE_MEMORY_BYTES}")
fi

if [[ "${VLLM_ENABLE_PREFIX_CACHING}" == "1" ]]; then
  args+=(--enable-prefix-caching)
fi

if [[ "${VLLM_ENABLE_CHUNKED_PREFILL}" == "1" ]]; then
  args+=(--enable-chunked-prefill)
fi

if [[ "${VLLM_ENFORCE_EAGER}" == "1" ]]; then
  args+=(--enforce-eager)
fi

{
  echo "[$(date -Is)] starting vLLM profile=${VLLM_PROFILE} model=${VLLM_MODEL_PATH} host=${VLLM_HOST} port=${VLLM_PORT}"
  echo "[$(date -Is)] served_model_names=${VLLM_SERVED_MODEL_NAME}"
  echo "[$(date -Is)] cuda_visible_devices=${CUDA_VISIBLE_DEVICES} dtype=${VLLM_DTYPE} max_model_len=${VLLM_MAX_MODEL_LEN} max_num_seqs=${VLLM_MAX_NUM_SEQS} max_num_batched_tokens=${VLLM_MAX_NUM_BATCHED_TOKENS} gpu_memory_utilization=${VLLM_GPU_MEM_UTIL} kv_cache_dtype=${VLLM_KV_CACHE_DTYPE}"
  printf '[%s] command:' "$(date -Is)"
  printf ' %q' "${args[@]}"
  printf '\n'
} >> logs/vllm.log

# Avoid vLLM warning about our launcher-only VLLM_* variables.
unset VLLM_PROFILE VLLM_MODEL_PATH VLLM_SERVED_MODEL_NAME VLLM_HOST VLLM_PORT VLLM_RESTART_DELAY_SEC

exec "${args[@]}" >> logs/vllm.log 2>&1
