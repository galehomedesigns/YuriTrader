#!/usr/bin/env bash
# run_pilot.sh — Phase 0 pilot end-to-end, DRY, on a replay date.
#   producer (broadcast) -> follower (size + stage DRY) -> family_bot (confirm + log bid)
# By default uses the TOKEN-LESS sim-approve so it runs with no BotFather token.
# Pass a date as $1 (default 2026-06-24) and optionally CONFIRM=send/run to use a real bot.
set -euo pipefail
cd "$(dirname "$0")"

DATE="${1:-2026-06-24}"
USER_ID="${USER_ID:-pilot}"
CONFIRM="${CONFIRM:-sim-approve}"   # sim-approve | send | run
PY="/home/tonygale/openclaw/.venv/bin/python"

echo "== producer (replay $DATE) =="
$PY producer.py --date "$DATE"

echo "== follower ($USER_ID) =="
$PY follower.py --user "$USER_ID" --date "$DATE"

echo "== confirm ($CONFIRM) =="
$PY family_bot.py "$CONFIRM" --user "$USER_ID"

echo "== fee report =="
$PY fee_report.py --user "$USER_ID"
