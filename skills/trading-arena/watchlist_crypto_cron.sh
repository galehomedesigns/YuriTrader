#!/bin/bash
# Crypto-only dynamic watchlist refresh — runs 24/7 (crypto momentum, and the
# Kraken notices it mirrors, fire overnight/weekends when the stock-hours
# watchlist_cron.sh does NOT run). Keeps arena_watchlist fresh for the 24/7
# arena_scan_crypto.sh instead of it scanning a stale weekday snapshot.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ENV_FILE="/home/tonygale/openclaw/.env"
LOG_FILE="$SCRIPT_DIR/logs/watchlist_crypto.log"

mkdir -p "$SCRIPT_DIR/logs"

# Load environment variables (skip lines with spaces in values)
if [ -f "$ENV_FILE" ]; then
    while IFS='=' read -r key value; do
        [[ -z "$key" || "$key" =~ ^# ]] && continue
        export "$key=$value" 2>/dev/null || true
    done < "$ENV_FILE"
fi

echo "=== Crypto watchlist refresh: $(date) ===" >> "$LOG_FILE"
/home/tonygale/openclaw/.venv/bin/python "$SCRIPT_DIR/overseer/dynamic_watchlist.py" --crypto-only >> "$LOG_FILE" 2>&1
echo "" >> "$LOG_FILE"

tail -3000 "$LOG_FILE" > "$LOG_FILE.tmp" 2>/dev/null && mv "$LOG_FILE.tmp" "$LOG_FILE" || true
