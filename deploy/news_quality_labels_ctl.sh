#!/usr/bin/env bash
set -euo pipefail
umask 027

ROOT="${GLOBEMIND_HOME:-/root/data/globemind}"
LOG_DIR="$ROOT/logs"
PID_FILE="$LOG_DIR/news_quality_labels_loop.pid"
LOG_FILE="$LOG_DIR/news_quality_labels_loop.log"
LOOP_SCRIPT="$ROOT/deploy/news_quality_labels_loop.sh"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

MANAGED_LOOP_SERVICE_ID="news_quality_labels"
MANAGED_LOOP_LABEL="news quality labels"
MANAGED_LOOP_ROOT="$ROOT"
MANAGED_LOOP_PID_FILE="$PID_FILE"
MANAGED_LOOP_LOG_FILE="$LOG_FILE"
MANAGED_LOOP_LOOP_SCRIPT="$LOOP_SCRIPT"

# shellcheck source=deploy/managed_loop_ctl_lib.sh
source "$SCRIPT_DIR/managed_loop_ctl_lib.sh"
managed_loop_main "$@"
