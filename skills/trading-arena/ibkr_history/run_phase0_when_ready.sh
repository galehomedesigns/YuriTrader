#!/bin/bash
# Phase 0 auto-kickoff: wait for the IBKR 2yr backfill to finish, then run the
# continuous-intraday arena backtest on the FULL cache and (optionally) ping
# Telegram with the leaderboard. Detached/idempotent — safe to launch once.
set -uo pipefail

ROOT=/home/tonygale/openclaw
PY="$ROOT/.venv/bin/python"
BACKFILL_LOG=/tmp/ibkr_backfill.log
OUT=/tmp/arena_phase0_full.log
SUMMARY="$ROOT/skills/trading-arena/logs/arena_intraday_summary.json"

# load .env for TELEGRAM_* (best-effort; never order-trade — read-only backtest)
if [ -f "$ROOT/.env" ]; then
  while IFS='=' read -r k v; do
    [[ -z "$k" || "$k" =~ ^# ]] && continue
    export "$k=$v" 2>/dev/null || true
  done < "$ROOT/.env"
fi

echo "[phase0-kickoff] $(date -Iseconds) waiting for IBKR backfill to finish..." >> "$OUT"
# wait while the backfill process is alive (poll every 60s)
while pgrep -f 'ibkr_history/backfill.py' >/dev/null 2>&1; do sleep 60; done
echo "[phase0-kickoff] $(date -Iseconds) backfill ended; running Phase 0 on full cache" >> "$OUT"
grep -q '\[ibkr-backfill\] DONE' "$BACKFILL_LOG" 2>/dev/null \
  && echo "[phase0-kickoff] backfill completed cleanly" >> "$OUT" \
  || echo "[phase0-kickoff] WARNING: no clean DONE marker — running on whatever cached" >> "$OUT"

# run the full Phase 0 backtest (unbounded — heavy batch job)
"$PY" "$ROOT/skills/trading-arena/ibkr_history/arena_intraday_backtest.py" >> "$OUT" 2>&1
echo "[phase0-kickoff] $(date -Iseconds) PHASE0_DONE" >> "$OUT"

# best-effort Telegram ping with the top of the leaderboard (no-op if no creds)
"$PY" - "$SUMMARY" >> "$OUT" 2>&1 <<'PY'
import json, os, sys, urllib.request, urllib.parse
try:
    d = json.load(open(sys.argv[1]))
except Exception:
    sys.exit(0)
tok = os.environ.get("TELEGRAM_BOT_TOKEN") or os.environ.get("TELEGRAM_TOKEN")
chat = os.environ.get("TELEGRAM_CHAT_ID")
robust = [r for r in d.get("results", []) if r.get("robust")]
top = sorted(d.get("results", []), key=lambda r: -r["all"]["end_balance"])[:5]
lines = [f"🤖 Phase 0 arena backtest done — {d['window']['sessions']} sessions, "
         f"{d['coverage']['symbols']} symbols ({d['window']['start']}→{d['window']['end']})",
         f"Robust (positive both halves): {len(robust)}/{len(d.get('results',[]))}"]
for r in top:
    a = r["all"]
    lines.append(f"  {'✅' if r['robust'] else '·'} {r['bot']}: {a['return_pct']:+.1f}% "
                 f"({a['trades']} trades, IS {r['is']['return_pct']:+.1f}/OOS {r['oos']['return_pct']:+.1f})")
msg = "\n".join(lines)
print(msg)
if tok and chat:
    try:
        data = urllib.parse.urlencode({"chat_id": chat, "text": msg}).encode()
        urllib.request.urlopen(f"https://api.telegram.org/bot{tok}/sendMessage", data=data, timeout=15)
    except Exception as e:
        print("telegram send failed:", e)
PY
