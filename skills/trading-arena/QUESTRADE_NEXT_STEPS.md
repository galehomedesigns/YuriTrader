# Autonomous Stock Trading — Status & Resume Plan

_Last updated: 2026-05-16. Author: working session with Tony. Read this first when resuming._

---

## TL;DR

- **Crypto autonomous trading is LIVE and proven** on Kraken (real fills). Not affected by anything here.
- **Autonomous stock trading is fully BUILT and dry-run-verified** — including dollar-sized **fractional** orders (a $20 budget buys a slice of any stock, no whole-share wall).
- **It is blocked by ONE external thing:** the Questrade API token is read-only-scoped. Placing/previewing any order returns `HTTP 403 {"code":1016,"message":"Request is out of allowed OAuth scopes"}`. This is a Questrade account/app permission, not a code problem.
- **Nothing stock-side is live.** All `LIVE_STOCK_*` gates are OFF. Safe to leave indefinitely.
- **Decision for tomorrow:** pick a broker path (fix Questrade scope / pivot to an API-first broker / keep stocks manual). See [Options](#options-to-resume).

---

## Current state (what's running vs dormant)

| Thing | State |
|---|---|
| Crypto autonomous (Kraken) | **LIVE** — 4 bots, $25/trade, $65 exposure cap, −$25 daily stop. Running every 5 min, 24/7. |
| Stock autonomous (Questrade) | **BUILT, OFF.** `LIVE_STOCK_TRADING_ENABLED=false`, `LIVE_STOCK_ALLOW_ORDERS=false`, `LIVE_STOCK_TRADING_BOTS=` (empty). |
| Manual stock concierge | Untouched (`QUESTRADE_ALLOW_TRADING=true`, `MANUAL_STOCK_TRADING_ENABLED=true`) — but see ⚠️ below. |

⚠️ **The "manual concierge works" assumption is now DISPROVEN (verified 2026-05-17).** Its real order path (`place_market_order(validate=False)`) hits the same 403/1016 — confirmed by `verify_manual_questrade_scope.py`, which POSTs the manual path's exact order body to the non-placing `/orders/impact` and got `HTTP 403 {"code":1016}`. Its `validate=True` dry-run is local-only (never POSTs), which is why it *looked* like it worked. **Manual API stock trading does not work with this token.** Both manual gates are currently ON, so a real concierge Buy/Sell throws a raw 1016 at the user — consider setting `QUESTRADE_ALLOW_TRADING=false` (and/or `MANUAL_STOCK_TRADING_ENABLED=false`) until a working broker path exists.

---

## The blocker (with proof)

A real, non-placing API call (Questrade's official `/orders/impact` preview endpoint) returned:

```
POST /v1/accounts/40183640/orders/impact
→ HTTP 403  {"code":1016,"message":"Request is out of allowed OAuth scopes"}
```

- All **read** calls succeed with the same token (quotes, balances, positions, GET orders).
- Only **order placement / preview** is denied.
- Questrade restricts API order placement to **partner-tier API apps**; standard personal API apps are read-only.
- The fractional positions currently in the account (AKAN, ORCL, GLD, PLUG, META, MSFT…) were placed **by hand in the Questrade app**, not by any bot.

**Conclusion:** weeks of "it never trades" on stocks = the Questrade API integration cannot place orders with this token. Not rules, not funding, not fractional support, not our code.

---

## What's already built (done, safe, OFF)

All verified by `py_compile` + stubbed unit tests. Crypto path proven byte-for-byte unchanged.

1. **Gates** — [.env](../../.env) + [config.py](config.py): `LIVE_STOCK_TRADING_ENABLED`, `LIVE_STOCK_ALLOW_ORDERS`, `LIVE_STOCK_TRADING_BOTS`, `LIVE_STOCK_MAX_POSITION_USD`=50, `LIVE_STOCK_MAX_EXPOSURE_USD`=200, `LIVE_STOCK_DAILY_LOSS_LIMIT`=−25. All OFF/empty.
2. **Executor** — [shared/questrade_executor.py](shared/questrade_executor.py):
   - `place_fractional_market()` — decimal qty, **floored** so spend never exceeds budget; uses `/orders/impact` when validating (true non-placing server preview) and `/orders` when live.
   - `execute_arena_trade()` — **dollar-sized fractional** (no whole-share floor; $20 → a fractional slice of any stock).
   - `is_stock_trade_eligible_for_live()`, `is_market_open()` (hard 9:30–16:00 ET gate).
   - `place_market_order()` / `execute_manual_trade()` (manual concierge) **left untouched**.
3. **Routing** — [shared/paper_trader.py](shared/paper_trader.py): `_venue_for()` (crypto=`KRAKEN_PAIR_MAP`, else stock), `_try_live_stock_trade()`, venue-scoped exposure/loss (independent stock vs crypto books), fractional stock close.
4. **Kill switches** — [kill_stock_trading.sh](kill_stock_trading.sh) (autonomous stock only) and [kill_live_trading.sh](kill_live_trading.sh) (kills BOTH venues).

The only thing standing between this and a live stock trade is the Questrade order scope. If that 403 becomes a successful `/orders/impact` preview, the path is ready.

---

## Options to resume

### Option A — Fix the Questrade API scope
- **Do:** Log into Questrade → App Hub / API centre. Check whether the registered personal app can be granted a trade/`write` scope. If yes, re-authorize and re-issue the refresh token **with trade scope**, update `QUESTRADE_REFRESH_TOKEN` in `.env`, retest.
- **Then:** run the verifier (see [Commands](#commands)). Expect a JSON preview instead of 1016.
- **Effort:** minutes (if scope is available) — but **likely a dead end**: Questrade personal API is widely reported read-only; trading is partner-only.
- **Best if:** quick win is worth a try before investing elsewhere.

### Option B — Pivot to an API-first broker (the path that actually works)
Build a new executor mirroring `kraken_executor.py` / `questrade_executor.py`, plug into the same `_venue_for()` routing seam.
- **Alpaca** — API-first, native **fractional + notional** orders, free paper-trading sandbox, excellent docs. ⚠️ **Residency caveat:** Alpaca brokerage is US-persons only — if Tony is a Canadian resident, a live Alpaca *brokerage* account likely isn't available (paper sandbox still is, good for proving the path).
- **Interactive Brokers (IBKR)** — available to Canadians, has an API (Client Portal / TWS API), supports fractional shares. Heavier integration than Alpaca but the realistic **Canada-viable** live path.
- **Effort:** ~1 focused session for an Alpaca *paper* executor (proves end-to-end autonomous stock trading works); more for IBKR live.
- **Best if:** you actually want autonomous stocks to work this year. **Recommended.**

### Option C — Stocks manual, crypto autonomous only
- Accept Questrade autonomous is blocked. Bot **advises** stock entries (Telegram), Tony places them by hand in the Questrade app. Keep the proven live **crypto** path as the only autonomous money.
- **Effort:** zero (already the de-facto state).
- **Best if:** stock automation isn't worth more engineering right now.

### Option D — Investigate Questrade partner-developer API
- Research eligibility, application process, and timeline for Questrade partner API access (the tier that *can* place orders).
- **Effort:** research only; outcome uncertain; likely slow/approval-gated.
- **Best if:** you specifically want to stay on Questrade and are willing to wait.

---

## Recommended sequence (suggestion, not a commitment)

1. **5-minute check:** Option A — look in Questrade App Hub for a trade scope. If it exists, this might just work. If the app is read-only with no trade option → confirms partner-only.
2. **If A is a dead end:** Option B with **Alpaca paper** — prove the full autonomous stock loop works end-to-end risk-free (no funding, no residency issue for paper). This also de-risks before any real money or IBKR effort.
3. **If the paper loop is good and you want live stocks:** decide Alpaca-live (if eligible) vs IBKR (Canada) — that's a funding + account decision.
4. Crypto autonomous keeps running throughout; it's independent.

---

## Key facts / ground truth reference

- **Questrade account:** `40183640`. USD buying power **$26.02**, CAD $12.79, ~$236 equity in 8 pre-existing manual fractional positions (bot will never touch these — it only closes positions it has its own `arena_trades` row for).
- **Questrade API from GX10:** works directly (no tunnel — the old Hostinger-tunnel `.env` note was stale VPS cruft, removed). **Slow: ~20s/call.** A stock entry ≈ 3–4 calls ≈ 60–100s (fine inside the 5-min scan).
- **Fractional:** confirmed supported by Questrade (platform + API schema; account history shows executed decimal Market orders). Fractional = **Market orders only**. Our code is correct for it.
- **The $100 budget** Tony authorized was effectively the **Kraken/crypto** budget. Questrade is a *separate* account with only ~$26 USD free.
- **Crypto (working reference):** Kraken, `LIVE_TRADING_BOTS=trap-catcher,momentum-hunter,correlation-hunter,squeeze-breaker`, $25/$65/−25. Confirmed real fills (txids verified against the Kraken account).

---

## Commands

**Verify Questrade order scope (run after any token/scope change):**
```bash
cd /home/tonygale/openclaw && set -a && . ./.env && set +a && \
.venv/bin/python -c "
import sys; sys.path.insert(0,'skills/trading-arena')
from shared.questrade_executor import QuestradeExecutor as Q
try:
    r=Q().execute_arena_trade('AAPL','BUY',5.0)   # validate mode -> /orders/impact (NON-placing)
    print('SCOPE OK — preview:', r.get('impact'))
except Exception as e:
    print('STILL BLOCKED:', e)
"
```
`SCOPE OK` ⇒ Questrade autonomous is unblocked; proceed to the dry-run rehearsal.
`STILL BLOCKED ... 1016` ⇒ scope still missing; Option A is a dead end → go Option B/D.

**Verify the MANUAL concierge order scope (non-placing, safe any time):**
```bash
cd /home/tonygale/openclaw && set -a && . ./.env && set +a && \
.venv/bin/python skills/trading-arena/verify_manual_questrade_scope.py
# optional: --symbol MSFT --side Buy --qty 1
```
Rebuilds `place_market_order(validate=False)`'s exact whole-share body and POSTs
it to the non-placing `/orders/impact`. Exit 0 = scope OK (manual concierge would
work), 1 = `1016` blocked, 2 = inconclusive. As of 2026-05-17: **exit 1 (blocked)**.

**Go-live sequence (ONLY after scope is fixed and a market-hours dry-run looks right):**
```
# 1. Dry-run rehearsal (market hours): no real order, exercises full path
LIVE_STOCK_TRADING_ENABLED=true
LIVE_STOCK_TRADING_BOTS=momentum-hunter
LIVE_STOCK_ALLOW_ORDERS=false        # stays false = /orders/impact only
# inspect arena_trades for correct would-be qty/cost, then:
# 2. Real orders (last gate)
LIVE_STOCK_ALLOW_ORDERS=true
```

**Emergency stop:**
```bash
bash /home/tonygale/openclaw/skills/trading-arena/kill_stock_trading.sh   # autonomous stock only
bash /home/tonygale/openclaw/skills/trading-arena/kill_live_trading.sh    # BOTH crypto + stock
```

---

## Open questions for tomorrow

1. Is Tony a Canadian resident? (Determines Alpaca-live vs IBKR for Option B. Questrade + `.TO` symbols + CAD balance suggest yes.)
2. Does the Questrade personal app expose any trade/write scope at all? (Resolves Option A in 5 minutes.)
3. Is autonomous stock trading worth the engineering vs. just running crypto + manual stock advice? (Option C is free and already de-facto true.)
4. If pivoting brokers: paper-prove first (recommended) or go straight to a funded live account?

_When a direction is chosen, update [memory: questrade-autonomous-live-deferred] and this file._
