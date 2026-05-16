#!/bin/bash
# One-time interactive TradingView login on headless GX10.
#
# Spins up Xvfb + x11vnc bound to loopback, launches Chromium against the
# persistent OpenClaw profile, then waits. Connect from a laptop via:
#     ssh -L 5900:localhost:5900 tonygale@gx10-087b
#     <any VNC client> -> localhost:5900
# Log in, close the Chromium window. Session cookie persists in the
# user-data-dir for future headless CDP runs on :9222.
#
# Ctrl-C (or Chromium exit) tears down Xvfb/x11vnc cleanly.

set -euo pipefail

DISPLAY_NUM=":99"
VNC_PORT="5900"
USER_DATA_DIR="/home/tonygale/openclaw/state/browser/openclaw/user-data"
CHROME_BIN="/home/tonygale/.cache/ms-playwright/chromium-1208/chrome-linux/chrome"
START_URL="${1:-https://www.tradingview.com/chart/}"

for bin in Xvfb x11vnc; do
    if ! command -v "$bin" >/dev/null 2>&1; then
        echo "Missing $bin. Install with: sudo apt install -y xvfb x11vnc" >&2
        exit 1
    fi
done

if [ ! -x "$CHROME_BIN" ]; then
    echo "Chromium not found at $CHROME_BIN" >&2
    exit 1
fi

XVFB_PID=""
VNC_PID=""
CHROME_PID=""

cleanup() {
    set +e
    [ -n "$CHROME_PID" ] && kill "$CHROME_PID" 2>/dev/null
    [ -n "$VNC_PID" ] && kill "$VNC_PID" 2>/dev/null
    [ -n "$XVFB_PID" ] && kill "$XVFB_PID" 2>/dev/null
    wait 2>/dev/null
    echo "Torn down Xvfb/x11vnc/chromium."
}
trap cleanup EXIT INT TERM

if ss -tln 2>/dev/null | grep -q ":$VNC_PORT "; then
    echo "Port $VNC_PORT already in use. Kill the stale listener first." >&2
    exit 1
fi

echo "Starting Xvfb on $DISPLAY_NUM..."
Xvfb "$DISPLAY_NUM" -screen 0 1920x1080x24 >/dev/null 2>&1 &
XVFB_PID=$!
sleep 1

echo "Starting x11vnc on 127.0.0.1:$VNC_PORT (no auth, loopback only)..."
x11vnc -display "$DISPLAY_NUM" -rfbport "$VNC_PORT" -localhost -nopw -quiet -forever >/dev/null 2>&1 &
VNC_PID=$!
sleep 1

echo "Launching Chromium against $START_URL..."
DISPLAY="$DISPLAY_NUM" "$CHROME_BIN" \
    --no-sandbox \
    --user-data-dir="$USER_DATA_DIR" \
    "$START_URL" &
CHROME_PID=$!

cat <<EOF

Ready. From your laptop:
    ssh -L $VNC_PORT:localhost:$VNC_PORT tonygale@gx10-087b
Then point a VNC client at localhost:$VNC_PORT (no password).
Log in, close the Chromium window to tear everything down.

EOF

wait "$CHROME_PID"
