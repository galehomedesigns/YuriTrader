#!/bin/bash
# Arena scan — runs arena_runner.py --once via system cron
# Scans all symbols, runs all 10 bot strategies, paper trades, sends Telegram alerts
# Zero LLM usage. Runs for 30-60s then exits.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ENV_FILE="/home/tonygale/openclaw/.env"
LOG_DIR="$SCRIPT_DIR/logs"
LOG_FILE="$LOG_DIR/arena_scan.log"

mkdir -p "$LOG_DIR"

# Load environment variables
if [ -f "$ENV_FILE" ]; then
    while IFS='=' read -r key value; do
        [[ -z "$key" || "$key" =~ ^# ]] && continue
        export "$key=$value" 2>/dev/null || true
    done < "$ENV_FILE"
fi

echo "=== Arena Scan: $(date) ===" >> "$LOG_FILE"
/home/tonygale/openclaw/.venv/bin/python "$SCRIPT_DIR/arena_runner.py" --once >> "$LOG_FILE" 2>&1
echo "" >> "$LOG_FILE"

# Keep last 7 days of logs (~2000 scans)
tail -10000 "$LOG_FILE" > "$LOG_FILE.tmp" 2>/dev/null && mv "$LOG_FILE.tmp" "$LOG_FILE" || true
