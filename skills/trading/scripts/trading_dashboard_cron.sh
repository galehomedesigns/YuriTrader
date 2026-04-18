#!/bin/bash
# Trading dashboard regen — runs dashboard_gen.py inside the container.
# No Telegram post; purely mechanical HTML/data refresh.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
LOG_DIR="$SCRIPT_DIR/../logs"
LOG_FILE="$LOG_DIR/dashboard.log"
CONTAINER="openclaw-xrt9-openclaw-1"

mkdir -p "$LOG_DIR"

echo "=== Dashboard regen: $(date -Iseconds) ===" >> "$LOG_FILE"
docker exec "$CONTAINER" python3 /data/skills/trading/scripts/dashboard_gen.py generate >> "$LOG_FILE" 2>&1
echo "" >> "$LOG_FILE"
