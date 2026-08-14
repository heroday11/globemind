#!/usr/bin/env bash
set -euo pipefail

ROOT="${GLOBEMIND_HOME:-/root/data/globemind}"
PY="${PYTHON_BIN:-/opt/conda/envs/Globemind_env/bin/python}"
LOG_DIR="$ROOT/logs"
LOCK_FILE="${LOCK_FILE:-/tmp/globemind_news_quality_labels.lock}"

INTERVAL_SECONDS="${INTERVAL_SECONDS:-600}"
BATCH_SIZE="${BATCH_SIZE:-50000}"
MISSING_LIMIT="${MISSING_LIMIT:-200000}"
RECENT_HOURS="${RECENT_HOURS:-240}"

mkdir -p "$LOG_DIR"
cd "$ROOT"

echo "[news-quality] loop started interval=${INTERVAL_SECONDS}s missing_limit=${MISSING_LIMIT} recent_hours=${RECENT_HOURS}"

while true; do
  {
    if flock -n 9; then
      echo "[news-quality] $(date -Is) missing-label pass started"
      "$PY" scripts/backfill_news_quality_labels.py \
        --mode missing \
        --batch-size "$BATCH_SIZE" \
        --limit "$MISSING_LIMIT"

      echo "[news-quality] $(date -Is) recent-label pass started"
      "$PY" scripts/backfill_news_quality_labels.py \
        --mode recent \
        --batch-size "$BATCH_SIZE" \
        --recent-hours "$RECENT_HOURS"

      echo "[news-quality] $(date -Is) run finished"
    else
      echo "[news-quality] $(date -Is) previous run still active; skipped"
    fi
  } 9>"$LOCK_FILE" >>"$LOG_DIR/news_quality_labels_loop.log" 2>&1 || true

  sleep "$INTERVAL_SECONDS"
done
