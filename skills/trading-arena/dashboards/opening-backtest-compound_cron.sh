#!/bin/bash
# Regenerate the opening-backtest-compound dashboard from logs/opening_backtest_summary.json.
# Read-only on the summary; independent of the live trading system. (The backtest
# itself — opening_agent/backtest_full.py — is run separately, not by this cron.)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ENV_FILE="/home/tonygale/openclaw/.env"
LOG_FILE="$SCRIPT_DIR/opening-backtest-compound_update.log"

if [ -f "$ENV_FILE" ]; then
    while IFS='=' read -r key value; do
        [[ -z "$key" || "$key" =~ ^# ]] && continue
        export "$key=$value" 2>/dev/null || true
    done < "$ENV_FILE"
fi

echo "=== opening-backtest-compound dashboard update: $(date -Iseconds) ===" >> "$LOG_FILE"
/home/tonygale/openclaw/.venv/bin/python "$SCRIPT_DIR/opening-backtest-compound_update.py" >> "$LOG_FILE" 2>&1
