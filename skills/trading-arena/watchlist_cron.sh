#!/bin/bash
# Dynamic watchlist scanner — runs every 2 hours during market hours
# Refreshes top 20 movers (stocks + crypto), saves to Supabase, sends Telegram

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ENV_FILE="/home/tonygale/openclaw/.env"
LOG_FILE="$SCRIPT_DIR/logs/watchlist.log"

mkdir -p "$SCRIPT_DIR/logs"

# Load environment variables (skip lines with spaces in values)
if [ -f "$ENV_FILE" ]; then
    while IFS='=' read -r key value; do
        [[ -z "$key" || "$key" =~ ^# ]] && continue
        export "$key=$value" 2>/dev/null || true
    done < "$ENV_FILE"
fi

echo "=== Watchlist refresh: $(date) ===" >> "$LOG_FILE"
/home/tonygale/openclaw/.venv/bin/python "$SCRIPT_DIR/overseer/dynamic_watchlist.py" >> "$LOG_FILE" 2>&1
echo "" >> "$LOG_FILE"

tail -3000 "$LOG_FILE" > "$LOG_FILE.tmp" 2>/dev/null && mv "$LOG_FILE.tmp" "$LOG_FILE" || true
