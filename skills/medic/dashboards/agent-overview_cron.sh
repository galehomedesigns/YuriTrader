#!/bin/bash
# Regenerate canvas/agent-overview.html from the template + data in
# agent-overview_update.py. Not currently scheduled — run on demand whenever
# model assignments change. See agent-overview.md for the contract.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ENV_FILE="/home/tonygale/openclaw/.env"
LOG_FILE="$SCRIPT_DIR/agent-overview_update.log"

if [ -f "$ENV_FILE" ]; then
    while IFS='=' read -r key value; do
        [[ -z "$key" || "$key" =~ ^# ]] && continue
        export "$key=$value" 2>/dev/null || true
    done < "$ENV_FILE"
fi

echo "=== Agent overview regen: $(date -Iseconds) ===" >> "$LOG_FILE"
/home/tonygale/openclaw/.venv/bin/python "$SCRIPT_DIR/agent-overview_update.py" >> "$LOG_FILE" 2>&1
echo "" >> "$LOG_FILE"
