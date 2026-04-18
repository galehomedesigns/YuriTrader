#!/bin/bash
# TradingView focus switcher — runs every 30 min during market hours
# Switches the headless Chromium chart to the top opportunity from watchlist

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ENV_FILE="/docker/openclaw-xrt9/.env"
LOG_FILE="$SCRIPT_DIR/logs/tv_focus.log"

mkdir -p "$SCRIPT_DIR/logs"

if [ -f "$ENV_FILE" ]; then
    while IFS='=' read -r key value; do
        [[ -z "$key" || "$key" =~ ^# ]] && continue
        export "$key=$value" 2>/dev/null || true
    done < "$ENV_FILE"
fi

echo "=== TV focus: $(date) ===" >> "$LOG_FILE"
python3 "$SCRIPT_DIR/overseer/tv_focus.py" >> "$LOG_FILE" 2>&1
echo "" >> "$LOG_FILE"

tail -2000 "$LOG_FILE" > "$LOG_FILE.tmp" 2>/dev/null && mv "$LOG_FILE.tmp" "$LOG_FILE" || true
