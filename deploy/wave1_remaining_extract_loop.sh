#!/usr/bin/env bash
set -euo pipefail

ROOT="${GLOBEMIND_HOME:-/root/data/globemind}"
PY="${PYTHON_BIN:-$ROOT/.env_torch/bin/python}"
LOG_DIR="$ROOT/logs"
JOB_DIR="$ROOT/data/historical_news/jobs/wave1_1y_prod_20260621"
INPUT="${INPUT:-$JOB_DIR/wave1_discovered_urls_merged_pruned.jsonl}"
OUTPUT="${OUTPUT:-$JOB_DIR/wave1_articles_merged.jsonl}"
ERRORS="${ERRORS:-$JOB_DIR/wave1_articles_merged_errors.jsonl}"
STATS="${STATS:-$JOB_DIR/wave1_articles_merged_stats.json}"
PROGRESS="${PROGRESS:-$JOB_DIR/wave1_articles_merged_progress.json}"
PROXY_POOL="${PROXY_POOL:-$ROOT/data/proxy_pool/proxy_pool_manifest_optimized_20260621.json}"
STOP_FILE="${STOP_FILE:-$LOG_DIR/wave1_remaining_extract.stop}"
STATE_FILE="${STATE_FILE:-$LOG_DIR/wave1_remaining_extract_supervisor.json}"

GLOBAL_CONCURRENCY="${GLOBAL_CONCURRENCY:-6}"
MAX_PER_DOMAIN="${MAX_PER_DOMAIN:-2}"
MIN_PER_DOMAIN="${MIN_PER_DOMAIN:-1}"
TIMEOUT="${TIMEOUT:-18}"
BASE_DELAY_MS="${BASE_DELAY_MS:-50}"
JITTER_MS="${JITTER_MS:-250}"
RETRY_LIMIT="${RETRY_LIMIT:-3}"
MAX_RUNTIME_SEC="${MAX_RUNTIME_SEC:-21600}"
RESTART_DELAY_SEC="${RESTART_DELAY_SEC:-45}"
PROXY_FAILURE_THRESHOLD="${PROXY_FAILURE_THRESHOLD:-2}"
PROXY_BASE_COOLDOWN_SEC="${PROXY_BASE_COOLDOWN_SEC:-300}"
PROXY_MAX_COOLDOWN_SEC="${PROXY_MAX_COOLDOWN_SEC:-3600}"

mkdir -p "$LOG_DIR"
cd "$ROOT"

write_state() {
  local status="$1"
  local attempt="$2"
  local rc="$3"
  local note="${4:-}"
  "$PY" - "$STATE_FILE" "$status" "$attempt" "$rc" "$note" <<'PY'
import json
import sys
from datetime import datetime, timezone
path, status, attempt, rc, note = sys.argv[1:6]
payload = {
    "updated_at": datetime.now(timezone.utc).isoformat(),
    "status": status,
    "attempt": int(attempt),
    "last_exit_code": int(rc),
    "note": note,
}
with open(path, "w", encoding="utf-8") as fh:
    json.dump(payload, fh, ensure_ascii=False, indent=2)
    fh.write("\n")
PY
}

remaining_rows() {
  "$PY" - "$PROGRESS" <<'PY'
import json
import sys
from pathlib import Path
p = Path(sys.argv[1])
if not p.exists():
    print("unknown")
    raise SystemExit
try:
    data = json.loads(p.read_text(encoding="utf-8"))
except Exception:
    print("unknown")
    raise SystemExit
print(int(data.get("rows_remaining") or 0))
PY
}

attempt=0
echo "=== $(date -Is) Wave1 remaining extractor supervisor started ==="
echo "config: proxy_pool=$PROXY_POOL global_concurrency=$GLOBAL_CONCURRENCY max_per_domain=$MAX_PER_DOMAIN timeout=$TIMEOUT retry_limit=$RETRY_LIMIT max_runtime_sec=$MAX_RUNTIME_SEC"

while true; do
  if [[ -f "$STOP_FILE" ]]; then
    write_state "stopped" "$attempt" 0 "stop file present"
    echo "=== $(date -Is) supervisor stop requested ==="
    exit 0
  fi

  remaining="$(remaining_rows || true)"
  if [[ "$remaining" != "unknown" && "$remaining" -le 0 ]]; then
    write_state "completed" "$attempt" 0 "no rows remaining"
    echo "=== $(date -Is) supervisor completed: rows_remaining=$remaining ==="
    exit 0
  fi

  attempt=$((attempt + 1))
  write_state "running" "$attempt" 0 "starting extractor"
  echo "=== $(date -Is) extractor attempt=$attempt starting rows_remaining=$remaining ==="

  set +e
  "$PY" -u scripts/adaptive_global_extractor.py \
    --input "$INPUT" \
    --output "$OUTPUT" \
    --errors "$ERRORS" \
    --stats "$STATS" \
    --progress-path "$PROGRESS" \
    --resume \
    --global-concurrency "$GLOBAL_CONCURRENCY" \
    --max-per-domain "$MAX_PER_DOMAIN" \
    --min-per-domain "$MIN_PER_DOMAIN" \
    --timeout "$TIMEOUT" \
    --base-delay-ms "$BASE_DELAY_MS" \
    --jitter-ms "$JITTER_MS" \
    --retry-limit "$RETRY_LIMIT" \
    --proxy-pool "$PROXY_POOL" \
    --proxy-failure-threshold "$PROXY_FAILURE_THRESHOLD" \
    --proxy-base-cooldown-sec "$PROXY_BASE_COOLDOWN_SEC" \
    --proxy-max-cooldown-sec "$PROXY_MAX_COOLDOWN_SEC" \
    --max-runtime-sec "$MAX_RUNTIME_SEC" \
    --shuffle
  rc=$?
  set -e

  remaining_after="$(remaining_rows || true)"
  echo "=== $(date -Is) extractor attempt=$attempt exited rc=$rc rows_remaining=$remaining_after ==="

  if [[ -f "$STOP_FILE" ]]; then
    write_state "stopped" "$attempt" "$rc" "stop requested after extractor exit"
    exit 0
  fi
  if [[ "$remaining_after" != "unknown" && "$remaining_after" -le 0 ]]; then
    write_state "completed" "$attempt" "$rc" "no rows remaining after extractor exit"
    exit 0
  fi

  write_state "restarting" "$attempt" "$rc" "sleeping before restart"
  sleep "$RESTART_DELAY_SEC"
done
