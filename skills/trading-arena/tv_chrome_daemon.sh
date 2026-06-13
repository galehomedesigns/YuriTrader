#!/bin/bash
# Ensure a headless Chromium is running on CDP port 9222 with the TradingView
# chart open, reusing the logged-in profile seeded by setup_tv_login.sh.
#
# Idempotent: if CDP :9222 already answers, it does nothing. Otherwise it
# launches headless Chromium (no Xvfb needed) detached and waits for the port.
#
# This is the piece tv_focus.py / tv_switch_symbol.js depend on. Run it from
# tv_focus_cron.sh (which now calls it) or directly:
#     ./tv_chrome_daemon.sh
#
# NOTE: a user-data-dir can only be used by one Chromium at a time, so do not
# run this while setup_tv_login.sh (the interactive VNC login) is active.

set -euo pipefail

CDP_HOST="127.0.0.1"
CDP_PORT="9222"
USER_DATA_DIR="/home/tonygale/openclaw/state/browser/openclaw/user-data"
CHROME_BIN="/home/tonygale/.cache/ms-playwright/chromium-1208/chrome-linux/chrome"
START_URL="https://www.tradingview.com/chart/"
LOG_FILE="$(cd "$(dirname "$0")" && pwd)/logs/tv_chrome.log"

mkdir -p "$(dirname "$LOG_FILE")"

cdp_up() {
    curl -s --max-time 3 "http://${CDP_HOST}:${CDP_PORT}/json/version" >/dev/null 2>&1
}

if cdp_up; then
    echo "CDP already up on ${CDP_HOST}:${CDP_PORT}"
    exit 0
fi

if [ ! -x "$CHROME_BIN" ]; then
    echo "Chromium not found at $CHROME_BIN" >&2
    exit 1
fi

echo "=== launching headless Chromium on :${CDP_PORT}: $(date) ===" >> "$LOG_FILE"
setsid "$CHROME_BIN" \
    --headless=new \
    --no-sandbox \
    --disable-gpu \
    --remote-debugging-address="$CDP_HOST" \
    --remote-debugging-port="$CDP_PORT" \
    --user-data-dir="$USER_DATA_DIR" \
    "$START_URL" >> "$LOG_FILE" 2>&1 &
disown || true

# Wait up to ~20s for the port to come alive.
for _ in $(seq 1 20); do
    if cdp_up; then
        echo "CDP ready on ${CDP_HOST}:${CDP_PORT}"
        exit 0
    fi
    sleep 1
done

echo "Chromium launched but CDP did not come up on ${CDP_HOST}:${CDP_PORT} within 20s — see $LOG_FILE" >&2
exit 1
