#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="${GLOBEMIND_HOME:-/root/data/globemind}"
VERSION_VALUE="$(tr -d '\r\n' < "$PROJECT_DIR/VERSION")"
PYTHON_BIN="${PYTHON_BIN:-/root/data/python-runtimes/globemind-web/${VERSION_VALUE}/bin/python}"
INTERVAL_SECONDS="${INTERVAL_SECONDS:-1800}"
REFRESH_DAYS="${REFRESH_DAYS:-14}"
FORCE_REFRESH="${FORCE_REFRESH:-0}"
LOCK_FILE="${LOCK_FILE:-/tmp/globemind_opinion_scores_refresh.lock}"
LOG_DIR="${PROJECT_DIR}/logs"

export APP_ENV="${APP_ENV:-production}"
export APP_VERSION="${APP_VERSION:-opinion-scores-refresh}"
export GLOBEMIND_ENV_FILE="${GLOBEMIND_ENV_FILE:-$PROJECT_DIR/backend/api/.env}"
export GLOBEMIND_ENV_FILES="${GLOBEMIND_ENV_FILES:-$PROJECT_DIR/backend/api/.env:$PROJECT_DIR/backend/agentic_rag/.env:$PROJECT_DIR/.env}"
export GLOBEMIND_DB_PASSWORD_FILE="${GLOBEMIND_DB_PASSWORD_FILE:-/root/data/secrets/globemind/web_runtime.password}"
export GLOBEMIND_ALLOW_PRIVATE_SCRAM_TRANSPORT="${GLOBEMIND_ALLOW_PRIVATE_SCRAM_TRANSPORT:-1}"
export DB_USER="${DB_USER:-web_runtime}"
export DB_SSLMODE="${DB_SSLMODE:-disable}"
export PYTHONPATH="${PYTHONPATH:-$PROJECT_DIR/backend:$PROJECT_DIR:$PROJECT_DIR/backend/cppt}"

mkdir -p "${LOG_DIR}"
cd "${PROJECT_DIR}"

echo "[opinion-scores] loop started interval=${INTERVAL_SECONDS}s days=${REFRESH_DAYS}"

while true; do
  {
    if flock -n 9; then
      echo "[opinion-scores] $(date -Is) refresh started"
      args=(scripts/refresh_china_opinion_scores.py --days "${REFRESH_DAYS}")
      if [[ "${FORCE_REFRESH}" == "1" ]]; then
        args+=(--force)
      fi
      if "${PYTHON_BIN}" "${args[@]}"; then
        echo "[opinion-scores] $(date -Is) refresh finished"
      else
        status="$?"
        echo "[opinion-scores] $(date -Is) refresh failed status=${status}"
      fi
    else
      echo "[opinion-scores] $(date -Is) previous refresh still active; skipped"
    fi
  } 9>"${LOCK_FILE}" >> "${LOG_DIR}/opinion_scores_refresh_loop.log" 2>&1 || true

  sleep "${INTERVAL_SECONDS}"
done
