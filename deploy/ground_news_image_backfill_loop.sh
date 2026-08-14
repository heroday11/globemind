#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="${GLOBEMIND_HOME:-/root/data/globemind}"
PYTHON_BIN="${PYTHON_BIN:-/opt/conda/envs/Globemind_env/bin/python}"
INTERVAL_SECONDS="${INTERVAL_SECONDS:-1800}"
CLUSTER_LIMIT="${CLUSTER_LIMIT:-500}"
NEWS_PER_CLUSTER="${NEWS_PER_CLUSTER:-6}"
WORKERS="${WORKERS:-12}"
TIMEOUT="${TIMEOUT:-8}"
L1_RUN_ID="${L1_RUN_ID:-fast_l1_v2}"
L15_RUN_ID="${L15_RUN_ID:-fast_l15_v1}"
LOCK_FILE="${LOCK_FILE:-/tmp/globemind_ground_news_image_backfill.lock}"
LOG_DIR="${PROJECT_DIR}/logs"

mkdir -p "${LOG_DIR}"
cd "${PROJECT_DIR}"

echo "[ground-news-images] loop started interval=${INTERVAL_SECONDS}s cluster_limit=${CLUSTER_LIMIT}"

while true; do
  {
    if flock -n 9; then
      echo "[ground-news-images] $(date -Is) run started"
      "${PYTHON_BIN}" scripts/backfill_story_images.py \
        --l1-run-id "${L1_RUN_ID}" \
        --l15-run-id "${L15_RUN_ID}" \
        --cluster-limit "${CLUSTER_LIMIT}" \
        --news-per-cluster "${NEWS_PER_CLUSTER}" \
        --workers "${WORKERS}" \
        --timeout "${TIMEOUT}"
      echo "[ground-news-images] $(date -Is) run finished"
    else
      echo "[ground-news-images] $(date -Is) previous run still active; skipped"
    fi
  } 9>"${LOCK_FILE}" >> "${LOG_DIR}/ground_news_image_backfill_loop.log" 2>&1 || true

  sleep "${INTERVAL_SECONDS}"
done
