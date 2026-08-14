#!/usr/bin/env bash
set -euo pipefail

ROOT="${GLOBEMIND_HOME:-/root/data/globemind}"
PY="${PYTHON_BIN:-$ROOT/.env_torch/bin/python}"
LOG_DIR="$ROOT/logs"
DATA_ROOT="$ROOT/data/historical_news/daily"
SOURCE_MAP="${SOURCE_MAP:-$ROOT/data/source_curation/historical_wave1_targets.csv}"
MANIFEST="${MANIFEST:-$ROOT/data/source_curation/historical_source_manifest_v1_fast.csv}"
PROXY_POOL="${PROXY_POOL:-$ROOT/data/proxy_pool/proxy_pool_manifest_refreshed_20260622.json}"
LOCK_FILE="${LOCK_FILE:-/tmp/globemind_daily_news_ingest.lock}"
DAILY_USE_PROXY_POOL="${DAILY_USE_PROXY_POOL:-0}"

INTERVAL_SECONDS="${INTERVAL_SECONDS:-86400}"
LOOKBACK_DAYS="${LOOKBACK_DAYS:-3}"
FUTURE_DAYS="${FUTURE_DAYS:-1}"
DISCOVERY_WORKERS="${DISCOVERY_WORKERS:-6}"
MAX_SITEMAPS_PER_SITE="${MAX_SITEMAPS_PER_SITE:-20}"
DISCOVERY_MAX_URLS_PER_SITE="${DISCOVERY_MAX_URLS_PER_SITE:-1500}"
PRUNE_MAX_URLS_PER_SITE="${PRUNE_MAX_URLS_PER_SITE:-200}"
EXTRACT_CONCURRENCY="${EXTRACT_CONCURRENCY:-4}"
EXTRACT_MAX_PER_DOMAIN="${EXTRACT_MAX_PER_DOMAIN:-1}"
EXTRACT_MIN_PER_DOMAIN="${EXTRACT_MIN_PER_DOMAIN:-1}"
TIMEOUT="${TIMEOUT:-20}"
BASE_DELAY_MS="${BASE_DELAY_MS:-100}"
JITTER_MS="${JITTER_MS:-250}"
RETRY_LIMIT="${RETRY_LIMIT:-2}"
EXTRACT_MAX_IDLE_SEC="${EXTRACT_MAX_IDLE_SEC:-300}"
LOAD_BATCH_SIZE="${LOAD_BATCH_SIZE:-500}"

DB_HOST="${DB_HOST:-192.168.207.171}"
DB_PORT="${DB_PORT:-54333}"
DB_USER="${DB_USER:-wave1_loader}"
DB_NAME="${DB_NAME:-news}"
DB_SSLMODE="${DB_SSLMODE:-}"
ALLOW_PRIVATE_SCRAM_TRANSPORT="${GLOBEMIND_ALLOW_PRIVATE_SCRAM_TRANSPORT:-0}"
LOADER_SECRET_SOURCE="${DAILY_INGEST_DB_PASSWORD_SOURCE_FILE:-}"
ALLOW_LEGACY_DB_ROLE="${DAILY_INGEST_ALLOW_LEGACY_DB_ROLE:-0}"

[[ -n "$LOADER_SECRET_SOURCE" ]] || {
  echo "DAILY_INGEST_DB_PASSWORD_SOURCE_FILE is required" >&2
  exit 2
}
"$PY" "$ROOT/scripts/wave1_loader_migrate.py" validate-secret --path "$LOADER_SECRET_SOURCE" >/dev/null
if [[ "$DB_USER" == "wave1_loader" ]]; then
  [[ "$ALLOW_LEGACY_DB_ROLE" == "0" ]] || {
    echo "DAILY_INGEST_ALLOW_LEGACY_DB_ROLE is valid only with DB_USER=postgres" >&2
    exit 2
  }
elif [[ "$ALLOW_LEGACY_DB_ROLE" != "1" || "$DB_USER" != "postgres" ]]; then
  echo "daily ingest requires DB_USER=wave1_loader; legacy postgres requires DAILY_INGEST_ALLOW_LEGACY_DB_ROLE=1" >&2
  exit 2
fi
[[ "$DB_SSLMODE" == "verify-full" || "$DB_SSLMODE" == "require" || "$DB_SSLMODE" == "disable" ]] || {
  echo "DB_SSLMODE must be verify-full, require, or disable" >&2
  exit 2
}
[[ "$ALLOW_PRIVATE_SCRAM_TRANSPORT" == "0" || "$ALLOW_PRIVATE_SCRAM_TRANSPORT" == "1" ]] || exit 2
DB_TRANSPORT_ARGS=(--sslmode "$DB_SSLMODE")
DB_ROLE_ARGS=()
if [[ "$ALLOW_PRIVATE_SCRAM_TRANSPORT" == "1" ]]; then
  DB_TRANSPORT_ARGS+=(--allow-private-scram-transport)
fi
if [[ "$ALLOW_LEGACY_DB_ROLE" == "1" ]]; then
  DB_ROLE_ARGS+=(--allow-legacy-postgres-role)
fi
unset \
  L1_DB_PASSWORD PG_WRITE_PASSWORD DB_PASSWORD PG_PASSWORD PGPASSWORD \
  DATABASE_URL SQLALCHEMY_DATABASE_URL PGPASSFILE GLOBEMIND_DB_PASSWORD_FILE

mkdir -p "$LOG_DIR" "$DATA_ROOT"
cd "$ROOT"

run_once() {
  local run_id start_date end_date run_dir discovered pruned filtered report articles errors stats progress load_state load_heartbeat load_ready load_seal load_dead_letters load_secret load_control_dir load_control_socket prune_stats db_filter_stats
  run_id="daily_$(date +%Y%m%d_%H%M%S)"
  start_date="$(date -d "${LOOKBACK_DAYS} days ago" +%F)"
  end_date="$(date -d "${FUTURE_DAYS} days" +%F)"
  run_dir="$DATA_ROOT/$run_id"
  discovered="$run_dir/discovered_urls.jsonl"
  pruned="$run_dir/discovered_urls_pruned.jsonl"
  filtered="$run_dir/discovered_urls_new.jsonl"
  report="$run_dir/discovery_report.md"
  articles="$run_dir/articles.jsonl"
  errors="$run_dir/article_errors.jsonl"
  stats="$run_dir/extract_stats.json"
  progress="$run_dir/extract_progress.json"
  prune_stats="$run_dir/prune_stats.json"
  db_filter_stats="$run_dir/db_prefilter_stats.json"
  load_state="$run_dir/load_state.json"
  load_heartbeat="$run_dir/load_heartbeat.json"
  load_ready="$run_dir/load_ready.json"
  load_seal="$run_dir/articles.sealed.json"
  load_dead_letters="$run_dir/load_dead_letters"
  load_secret="$run_dir/.loader-db-secret"
  load_control_dir="$run_dir/loader-control"
  load_control_socket="$load_control_dir/loader.sock"

  mkdir -p "$run_dir" "$load_control_dir" "$load_dead_letters"
  chmod 700 "$load_control_dir" "$load_dead_letters"
  echo "[daily-ingest] run_id=$run_id window=${start_date}..${end_date}"

  "$PY" scripts/discover_historical_urls.py \
    --input "$MANIFEST" \
    --output "$discovered" \
    --report "$report" \
    --workers "$DISCOVERY_WORKERS" \
    --timeout "$TIMEOUT" \
    --max-sitemaps-per-site "$MAX_SITEMAPS_PER_SITE" \
    --max-urls-per-site "$DISCOVERY_MAX_URLS_PER_SITE" \
    --start-date "$start_date" \
    --end-date "$end_date"

  "$PY" scripts/prune_discovered_urls_queue.py \
    --input "$discovered" \
    --output "$pruned" \
    --stats "$prune_stats" \
    --start-date "$start_date" \
    --end-date "$end_date" \
    --require-date-signal \
    --max-urls-per-site "$PRUNE_MAX_URLS_PER_SITE"

  env \
    -u L1_DB_PASSWORD -u PG_WRITE_PASSWORD -u DB_PASSWORD -u PG_PASSWORD \
    -u PGPASSWORD -u DATABASE_URL -u SQLALCHEMY_DATABASE_URL -u PGPASSFILE \
    GLOBEMIND_DB_PASSWORD_FILE="$LOADER_SECRET_SOURCE" \
    "$PY" scripts/filter_discovered_urls_existing_db.py \
    --input "$pruned" \
    --output "$filtered" \
    --stats "$db_filter_stats" \
    --host "$DB_HOST" \
    --port "$DB_PORT" \
    --user "$DB_USER" \
    --dbname "$DB_NAME" \
    "${DB_TRANSPORT_ARGS[@]}" \
    "${DB_ROLE_ARGS[@]}"

  local extract_args=(
    scripts/adaptive_global_extractor.py
    --input "$filtered" \
    --output "$articles" \
    --errors "$errors" \
    --stats "$stats" \
    --progress-path "$progress" \
    --resume \
    --global-concurrency "$EXTRACT_CONCURRENCY" \
    --max-per-domain "$EXTRACT_MAX_PER_DOMAIN" \
    --min-per-domain "$EXTRACT_MIN_PER_DOMAIN" \
    --timeout "$TIMEOUT" \
    --base-delay-ms "$BASE_DELAY_MS" \
    --jitter-ms "$JITTER_MS" \
    --retry-limit "$RETRY_LIMIT" \
    --max-idle-sec "$EXTRACT_MAX_IDLE_SEC" \
    --shuffle
  )
  if [[ "$DAILY_USE_PROXY_POOL" == "1" ]]; then
    extract_args+=(--proxy-pool "$PROXY_POOL")
  fi
  "$PY" -u "${extract_args[@]}"

  "$PY" scripts/wave1_loader_migrate.py seal-input \
    --input "$articles" \
    --output "$load_seal" \
    --job-id daily-news-ingest \
    --run-id "$run_id"

  env \
    -u L1_DB_PASSWORD -u PG_WRITE_PASSWORD -u DB_PASSWORD -u PG_PASSWORD \
    -u PGPASSWORD -u DATABASE_URL -u SQLALCHEMY_DATABASE_URL -u PGPASSFILE \
    GLOBEMIND_DB_PASSWORD_FILE="$LOADER_SECRET_SOURCE" \
    "$PY" scripts/wave1_loader_migrate.py materialize-secret --output "$load_secret"
  local load_result=0
  env \
    -u L1_DB_PASSWORD \
    -u PG_WRITE_PASSWORD \
    -u DB_PASSWORD \
    -u PG_PASSWORD \
    -u PGPASSWORD \
    -u DATABASE_URL \
    -u SQLALCHEMY_DATABASE_URL \
    -u PGPASSFILE \
    GLOBEMIND_DB_PASSWORD_FILE="$load_secret" \
    GLOBEMIND_LOADER_INSTANCE_ID="daily-$run_id" \
    "$PY" scripts/stream_load_news_to_postgres.py \
      --input "$articles" \
      --source-map "$SOURCE_MAP" \
      --state-path "$load_state" \
      --heartbeat-path "$load_heartbeat" \
      --ready-path "$load_ready" \
      --control-socket "$load_control_socket" \
      --sealed-manifest "$load_seal" \
      --dead-letter-dir "$load_dead_letters" \
      --checkpoint-key "daily-news:$run_id:news" \
      --job-id daily-news-ingest \
      --run-id "$run_id" \
      --batch-size "$LOAD_BATCH_SIZE" \
      --host "$DB_HOST" \
      --port "$DB_PORT" \
      --user "$DB_USER" \
      --dbname "$DB_NAME" \
      "${DB_TRANSPORT_ARGS[@]}" \
      "${DB_ROLE_ARGS[@]}" \
    || load_result=$?
  rm -f "$load_secret"
  if [[ "$load_result" -ne 0 ]]; then
    return "$load_result"
  fi

  echo "[daily-ingest] run_id=$run_id finished"
}

echo "[daily-ingest] loop started interval=${INTERVAL_SECONDS}s lookback=${LOOKBACK_DAYS}d future=${FUTURE_DAYS}d"

if [[ "${RUN_ONCE:-0}" == "1" ]]; then
  {
    if flock -n 9; then
      echo "[daily-ingest] $(date -Is) one-shot run started"
      run_once
      echo "[daily-ingest] $(date -Is) one-shot run finished"
    else
      echo "[daily-ingest] $(date -Is) previous run still active; skipped"
    fi
  } 9>"$LOCK_FILE" >>"$LOG_DIR/daily_news_ingest_loop.log" 2>&1
  exit 0
fi

while true; do
  {
    if flock -n 9; then
      echo "[daily-ingest] $(date -Is) run started"
      run_once
      echo "[daily-ingest] $(date -Is) run finished"
    else
      echo "[daily-ingest] $(date -Is) previous run still active; skipped"
    fi
  } 9>"$LOCK_FILE" >>"$LOG_DIR/daily_news_ingest_loop.log" 2>&1 || true

  sleep "$INTERVAL_SECONDS"
done
