# Switching from ADVISORY (manual) → AUTOMATIC execution

Keep this for when the strategy is proven AND the IBKR account is funded to
**≥ 2,500 CAD** (clears the US currency/margin wall). Until then the agent runs in
**advisory mode** (Telegram coaching; you place orders by hand).

## Current state (advisory mode)
The morning crons (in `crontab -l`) are:
```
0,30 5,6 * * 1-5   run_opening_scan.py          # ET 7:00/7:30/8:00/8:30 pre-market scans
0    7   * * 1-5   run_opening_scan.py          # ET 9:00
25   7   * * 1-5   run_opening_scan.py          # ET 9:25 (final pre-open)
32   7   * * 1-5   advisory_monitor.py          # ET 9:32 live manual-assist coach -> 9:50
*/10 4-7 * * 1-5   gateway_healthcheck.py       # gateway self-heal
```
The auto-execute path (`run_opening_live.py confirm/execute/cutoff`) is REMOVED
from cron while in advisory mode.

## To turn ON automatic execution (real orders), do ALL of:
1. Fund the IBKR account to **≥ 2,500 CAD** (US trading unlocked).
2. Switch the gateway to live + approve 2FA (already done if `.env` is live).
3. `.env`: set `OPENING_ALLOW_TRADING=true`.
4. Add these three cron lines (server = Mountain Time; ET = MT+2):
```
25 7 * * 1-5 /home/tonygale/openclaw/.venv/bin/python /home/tonygale/openclaw/skills/trading-arena/opening_agent/run_opening_live.py confirm  >> /home/tonygale/openclaw/skills/trading-arena/logs/opening_live_cron.log 2>&1
32 7 * * 1-5 /home/tonygale/openclaw/.venv/bin/python /home/tonygale/openclaw/skills/trading-arena/opening_agent/run_opening_live.py execute  >> /home/tonygale/openclaw/skills/trading-arena/logs/opening_live_cron.log 2>&1
50 7 * * 1-5 /home/tonygale/openclaw/.venv/bin/python /home/tonygale/openclaw/skills/trading-arena/opening_agent/run_opening_live.py cutoff   >> /home/tonygale/openclaw/skills/trading-arena/logs/opening_live_cron.log 2>&1
```
5. Decide whether to KEEP the advisory coach (`advisory_monitor.py` at 9:32) running
   alongside (you'd get both the orders AND the play-by-play), or remove it.

Belt-and-suspenders: even with all crons live, NO real order transmits unless
`OPENING_ALLOW_TRADING=true` (code-enforced in `live_executor.py`). So the steps
above are the only way to arm. (The one-time smoke-test pre-gate was retired
2026-06-15 after repeated live-config runs — `OPENING_ALLOW_TRADING` is now the
sole arming switch.)

Timestamped crontab snapshots live in this folder (`crontab_YYYYMMDD_HHMM.bak`).
