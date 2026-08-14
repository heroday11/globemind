#!/bin/bash
set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-/root/data/globemind}"
cd "$PROJECT_DIR"

BUILD_FRONTEND="${BUILD_FRONTEND:-1}" exec "$PROJECT_DIR/deploy/start_live_dev.sh" restart
