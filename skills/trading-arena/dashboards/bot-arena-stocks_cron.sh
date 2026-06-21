#!/bin/bash
# Regenerate the bot-arena-stocks dashboard from the three analysis summaries.
# Read-only; independent of the live trading system.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ENV_FILE="/home/tonygale/openclaw/.env"
LOG_FILE="$SCRIPT_DIR/bot-arena-stocks_update.log"
if [ -f "$ENV_FILE" ]; then
    while IFS='=' read -r key value; do
        [[ -z "$key" || "$key" =~ ^# ]] && continue
        export "$key=$value" 2>/dev/null || true
    done < "$ENV_FILE"
fi
echo "=== bot-arena-stocks dashboard update: $(date -Iseconds) ===" >> "$LOG_FILE"
/home/tonygale/openclaw/.venv/bin/python "$SCRIPT_DIR/bot-arena-stocks_update.py" >> "$LOG_FILE" 2>&1
