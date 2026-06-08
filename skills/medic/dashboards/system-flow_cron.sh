#!/bin/bash
# Regenerate the system-flow dashboard. Needs XDG_RUNTIME_DIR so `systemctl --user` works from cron.
set -euo pipefail

# systemctl --user can't reach the per-user bus in a cron env without this.
# Same reason medic_cron.sh exports it — cron inherits a minimal env.
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
LOG_FILE="$SCRIPT_DIR/system-flow_update.log"

echo "=== system-flow update: $(date -Iseconds) ===" >> "$LOG_FILE"
/home/tonygale/openclaw/.venv/bin/python "$SCRIPT_DIR/system-flow_update.py" >> "$LOG_FILE" 2>&1
