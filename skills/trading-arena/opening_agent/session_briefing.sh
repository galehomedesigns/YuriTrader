#!/usr/bin/env bash
# SessionStart briefing for the Opening-Power trading work. Prints the current
# focus + live tunnel/broker/market state so a fresh Claude session is oriented
# immediately (no need to re-explain where we left off).
#
# Wired as a SessionStart hook (see .claude/settings.local.json). Every probe is
# timeout-bounded so this can NEVER hang or fail a session start.
ROOT=/home/tonygale/openclaw
AGENT="$ROOT/skills/trading-arena/opening_agent"
PORT=9225

ET=$(TZ=America/New_York date '+%Y-%m-%d %H:%M %a' 2>/dev/null)
ETDATE=$(TZ=America/New_York date '+%Y-%m-%d' 2>/dev/null)
ETDOW=$(TZ=America/New_York date '+%u' 2>/dev/null)   # 1=Mon .. 7=Sun
ETHM=$(TZ=America/New_York date '+%H%M' 2>/dev/null)

# 2026 NYSE/Nasdaq full-day holidays (US equities). Update yearly.
declare -A HOL=(
 [2026-01-01]="New Year's Day" [2026-01-19]="MLK Day" [2026-02-16]="Presidents' Day"
 [2026-04-03]="Good Friday" [2026-05-25]="Memorial Day" [2026-06-19]="Juneteenth"
 [2026-07-03]="Independence Day (obs)" [2026-09-07]="Labor Day"
 [2026-11-26]="Thanksgiving" [2026-12-25]="Christmas"
)
if [[ -n "${HOL[$ETDATE]:-}" ]]; then MKT="CLOSED — ${HOL[$ETDATE]} (holiday)"
elif (( ${ETDOW:-1} >= 6 )); then MKT="CLOSED — weekend"
elif (( 10#${ETHM:-0000} >= 930 && 10#${ETHM:-0000} < 1600 )); then MKT="OPEN"
else MKT="closed — outside 9:30–16:00 ET"; fi

# CDP tunnel to the laptop trading Chrome
if [[ "$(curl -s -o /dev/null -w '%{http_code}' --max-time 3 http://127.0.0.1:$PORT/json/version 2>/dev/null)" == "200" ]]; then
  TUN="UP"
else
  TUN="DOWN"
fi

# Questrade broker link (only probe if the tunnel answers)
if [[ "$TUN" == "UP" ]]; then
  BH=$(timeout 8 node "$AGENT/tv_broker_health.js" --port $PORT 2>/dev/null)
  if   echo "$BH" | grep -q '"connected":true';  then BROKER="CONNECTED"
  elif echo "$BH" | grep -q '"connected":false'; then BROKER="DISCONNECTED (click Connect in TV)"
  else BROKER="unknown"; fi
else
  BROKER="n/a (tunnel down)"
fi

COMMIT=$(git -C "$ROOT" log -1 --format='%h %s' 2>/dev/null)

cat <<EOF
=== Opening-Power session briefing ===
FOCUS          : Opening-Power agent — US-equity opening-range strategy.
                 Live path = TradingView + Questrade via CDP tunnel :9225 (IBKR retired).
Time (ET)      : ${ET:-unknown}
US market      : $MKT
CDP tunnel     : $TUN   (laptop trading Chrome on :9225)
Questrade link : $BROKER
Last commit    : ${COMMIT:-unknown}
Resume tips    : tunnel DOWN -> run start_trading_browser.ps1 on the laptop;
                 broker DISCONNECTED -> click Connect in the TV broker panel.
=== end briefing ===
EOF
exit 0
