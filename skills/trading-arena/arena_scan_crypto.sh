#!/bin/bash
# Crypto-only arena scan — runs arena_runner.py --once --crypto-only.
# Intended for a 24/7 cron entry (crypto trades around the clock).
# Skips finnhub/twelvedata fetches to conserve stock-API quota.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ENV_FILE="/home/tonygale/openclaw/.env"
LOG_DIR="$SCRIPT_DIR/logs"
LOG_FILE="$LOG_DIR/arena_scan_crypto.log"

mkdir -p "$LOG_DIR"

if [ -f "$ENV_FILE" ]; then
    while IFS='=' read -r key value; do
        [[ -z "$key" || "$key" =~ ^# ]] && continue
        export "$key=$value" 2>/dev/null || true
    done < "$ENV_FILE"
fi

echo "=== Arena Crypto Scan: $(date) ===" >> "$LOG_FILE"
/home/tonygale/openclaw/.venv/bin/python "$SCRIPT_DIR/arena_runner.py" --once --crypto-only >> "$LOG_FILE" 2>&1
echo "" >> "$LOG_FILE"

tail -10000 "$LOG_FILE" > "$LOG_FILE.tmp" 2>/dev/null && mv "$LOG_FILE.tmp" "$LOG_FILE" || true
