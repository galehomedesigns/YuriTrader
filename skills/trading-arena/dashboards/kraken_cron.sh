#!/bin/bash
# Regenerate the kraken dashboard with .env sourced for Kraken API keys + Supabase creds.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ENV_FILE="/home/tonygale/openclaw/.env"
LOG_FILE="$SCRIPT_DIR/kraken_update.log"

if [ -f "$ENV_FILE" ]; then
    while IFS='=' read -r key value; do
        [[ -z "$key" || "$key" =~ ^# ]] && continue
        export "$key=$value" 2>/dev/null || true
    done < "$ENV_FILE"
fi

echo "=== kraken update: $(date -Iseconds) ===" >> "$LOG_FILE"
/home/tonygale/openclaw/.venv/bin/python "$SCRIPT_DIR/kraken_update.py" >> "$LOG_FILE" 2>&1
