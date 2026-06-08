#!/bin/bash
# Regenerate the bot-arena dashboard by running update.py with .env sourced.
# See ~/openclaw/docs/DASHBOARDS.md for the contract; see bot-arena.md for this specific dashboard.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ENV_FILE="/home/tonygale/openclaw/.env"
LOG_FILE="$SCRIPT_DIR/update.log"

if [ -f "$ENV_FILE" ]; then
    while IFS='=' read -r key value; do
        [[ -z "$key" || "$key" =~ ^# ]] && continue
        export "$key=$value" 2>/dev/null || true
    done < "$ENV_FILE"
fi

echo "=== bot-arena update: $(date -Iseconds) ===" >> "$LOG_FILE"
/home/tonygale/openclaw/.venv/bin/python "$SCRIPT_DIR/bot-arena_update.py" >> "$LOG_FILE" 2>&1
