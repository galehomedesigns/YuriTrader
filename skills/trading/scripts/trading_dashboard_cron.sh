#!/bin/bash
# Trading dashboard regen — runs dashboard_gen.py inside the container.
# No Telegram post; purely mechanical HTML/data refresh.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
LOG_DIR="$SCRIPT_DIR/../logs"
LOG_FILE="$LOG_DIR/dashboard.log"
CONTAINER="openclaw-xrt9-openclaw-1"
ENV_FILE="/home/tonygale/openclaw/.env"

mkdir -p "$LOG_DIR"

if [ -f "$ENV_FILE" ]; then
    while IFS='=' read -r key value; do
        [[ -z "$key" || "$key" =~ ^# ]] && continue
        export "$key=$value" 2>/dev/null || true
    done < "$ENV_FILE"
fi

echo "=== Dashboard regen: $(date -Iseconds) ===" >> "$LOG_FILE"
/home/tonygale/openclaw/.venv/bin/python /home/tonygale/openclaw/skills/trading/scripts/dashboard_gen.py generate >> "$LOG_FILE" 2>&1
echo "" >> "$LOG_FILE"
