# Opening Power — Rebuild / Tackle List

**Context:** First live opening session watched end-to-end on **2026-06-16** (US-Iran
peace / Strait-of-Hormuz reopening → broad green chip rally). The system ran, found
0 first-bar matches at 9:32, and stood down — correctly per its rules — but the
session surfaced a set of real issues to fix before the next live attempt. Items are
priority-ordered. Do NOT trade live again until P0 is resolved.

---

## P0 — Questrade broker link dropped on TradingView ✅ HEALTH CHECK DONE (live-confirm pending)

**Symptom (user-observed 2026-06-16):** the **Questrade ↔ TradingView broker
connection was lost at some point** during the session — user believes it happened
**at/just after the market open (~9:30)**, possibly during **tab switching / symbol
switching**. If this link is down, the order ticket cannot route orders to Questrade
→ **no trade can be placed or staged.** This is the single most critical dependency.

**Why it likely happened — leading hypothesis (ties to P1 below):** the bars-fetch
(`tv_bars_fetch.js`) did **not** keep a separate data tab — it ended up using the
**user's live trading tab** (the only chart tab was marked BOTH
`__OPENING_DATA_TAB__=true` AND had the order ticket). So the data reads
**switched the trading chart's symbol 20+ times** through the open. Rapidly driving
`setSymbol` on the tab that holds the broker session is the most plausible trigger
for the Questrade binding dropping (and is also just wrong — see P1).

**Other hypotheses to rule out:**
- Single-session kick — another TradingView login (phone / other browser) bumped the
  trading session. (Free-tier = one active session.)
- TV broker session simply times out / needs a periodic keep-alive or reconnect at
  the RTH session boundary (9:30).

**Fix / must-do:**
1. ✅ **Hard-isolate the data tab** (P1, done) so the trading tab's symbol is NEVER
   touched by bar reads — this alone likely removes the trigger.
2. ✅ **Questrade-link health check (done):** `tv_broker_health.js` probes the trading
   tab via CDP and reports `{connected}` from the presence of `QUESTRADE.*` broker DOM
   (the orders/positions/account tables TV only mounts while linked) + a reconnect-
   prompt check. Wired into `advisory_monitor`: a **pre-flight** probe before any
   staging + a **per-poll** probe in the live loop. On a drop it Telegrams 🚨 and
   **pauses auto-staging** (armed names still get coaching + a "place this by hand"
   note — never claims armed/staged while down); on restore it Telegrams ✅ and
   resumes. Also added to `preopen_check.py` (port-up alone didn't prove the link).
   Detector validated against the live DOM (returns connected=true, 3 QUESTRADE.* els).
3. ⏳ Confirm at the next real open WHEN it drops — the per-poll probe now logs every
   check timestamped to stderr (`[advisory] broker-health HH:MM:SS …`), so the next
   live session will pin the timing. (Only this live-observation step remains.)
4. ✅ **Re-link steps documented (done):** embedded in every down/cutoff alert
   (`RELINK_HELP`) and in `TV_TRADING_OPERATOR_GUIDE.md`.

---

## P1 — Data tab is hijacking the live trading chart ✅ FIXED

**Symptom:** the only chart tab on :9225 was flagged `__OPENING_DATA_TAB__=true` AND
held the order ticket, so bar-fetches switched the **trading** chart's symbol (seen
jumping SPCX→LUNR→… all morning). Directly suspected cause of P0.

**Root cause found:** `ensureDataTab()` adopted **any** marked chart tab (`marked[0]`)
and even `Target.closeTarget`'d marked tabs — so if the trading tab ever picked up the
`__OPENING_DATA_TAB__` marker it got *adopted as the data tab*, its id persisted, and
bar reads then drove `setSymbol` on it (and a stray close could have killed the
broker session outright).

**Fix (done):** the data tab is now **provably ours** — `tv_bars_fetch.js` stamps a
random `__OPENING_DATA_NONCE__` into the tab and persists `{targetId, nonce}` in
`logs/tv_data_tab.json` (`tv_tab.js`). On each run it **only reuses the exact
persisted id whose live nonce matches**; otherwise it creates a fresh background
`/chart/` tab. It **never adopts an unverified marked tab and never closes any tab**,
so the user's trading tab can't be grabbed or killed. `pickTradingTab()` (used by the
order/position tools) is unchanged and still excludes the persisted data-tab id —
self-heals by creating a new data tab if the old one is gone.

---

## P2 — First-bar-only scope misses setups that form on later bars ✅ FIXED

**Symptom:** `advisory_monitor` classified the single 9:30–9:32 opening bar, found 0
matches, and **returned**. But a read-only re-classification at 9:39 showed **3 valid
MATCH_LONG** (CCL, OPTX, AMPG) that had formed their power bars on bar 2–4 — never
re-evaluated. AMPG went on to lead the board (+11.6%).

**Fix (done):** `advisory_monitor.main()` now **re-classifies every unarmed candidate
on each new completed 2-min bar** through an arming window (`OPENING_ARM_WINDOW_MIN`,
default 15 → 9:45 ET), arming each name the **first** time it MATCHES (bar 1 *or*
later). No-first-bar-match no longer stands down — it sends a "watching N candidates"
notice and keeps scanning. Already-armed engines keep advancing every bar to the 20-min
cutoff. The top-N cap (`OPENING_MAX_TRADES`, default 5) is enforced at arm time (names
that match past the cap are reported, not armed). Verified with a sim: bar-1 = 0
matches → a name arms on its 2nd bar → coaching + watchlist sync fire (the AMPG case).

**Sizing decision (resolves P5 for the auto-stage path):** because names now arm
across the window, the final match count isn't known up front, so each auto-staged
entry is sized to a **fixed slot = `OPENING_TRADE_BUDGET_USD / OPENING_MAX_TRADES`**
(user-chosen 2026-06-16). Predictable per-trade size; leaves cash idle if <max fire.
Coaching + watchlist happen for every armed name regardless of the auto-stage gate.

---

## P3 — 9:32:01 cron fire is too early for the first RTH bar ✅ FIXED

**Symptom:** cron fires `advisory_monitor` at 9:32:01; the first RTH 2-min bar closes
at 9:32:00 and may not have rendered into the CDP chart yet → risk of classifying a
stale / pre-market bar.

**Fix (done):** two guards, no cron change needed.
1. **Correctness guard:** `_latest_complete(bars)` returns the most recent *closed*
   2-min bar — it drops any trailing still-forming bar (now < bar start +120s) — and
   `attempt_arm` **refuses any bar whose start < the 9:30 open** (`open_epoch`). So a
   not-yet-rendered pre-market/prior-session bar can never be classified, anywhere
   (bar-1 pass *and* the rolling loop now both dedup/step on the closed bar).
2. **Readiness gate:** before the first pass the monitor polls (≤`OPENING_BAR_WAIT_MAX_MIN`,
   default 4) until the 9:30 bar has closed and rendered for a quorum of names, then
   proceeds (logs `opening bar ready for N/M`); on timeout it proceeds anyway (later
   bars still arm via P2). Verified: stale 9:28 bar is rejected while the real 9:30 bar
   arms; forming bar is dropped. Cron stays at 9:32 — robust either way.

---

## P4 — Sell/close queue confirmation-detection is flaky (+ fractional shares) ✅ FIXED

**Symptom (2026-06-16 sell of AMPG/MBLY/AMKR/FISV):** `tv_order_queue.js` reported
"staged 1/4" and "NO confirmation after submit" on three orders, **yet all 4 actually
closed** (confirmed via positions). The confirmation-poll is unreliable — its summary
can't be trusted; only the broker positions can.

**Fix (done):**
1. **Positions are the source of truth.** `tv_order_queue.js` now snapshots the
   Questrade positions table before a close batch and **reconciles after** (`✓ flattened
   / ✗ STILL HELD` per symbol), writes `logs/opening_close_reconcile.json`, and prints
   the reconciled count. `advisory_monitor._stage_closes` runs the close queue to
   completion at the cutoff and **Telegrams the reconciled outcome** ("✅ All positions
   flattened" or "⚠️ N/M flattened — check the rest"). Validated: reconcile predicate
   unit-tested (full/partial/unchanged/fractional); live positions read works.
2. **Detection no longer lies.** The confirm-dialog poll is broadened (`send order|place
   order|confirm`, proper visibility) and lengthened (~10s); a missed dialog is no
   longer reported as failure — if the ticket was submitted the queue gives you a
   fallback window to click instead of instantly skipping, and the real result comes
   from the position reconcile.
3. **Fractional shares.** A close with a fractional qty now routes to a **MARKET** order
   (Questrade rejects fractional *limit* orders); whole-share closes keep the
   marketable-limit (which also clears OTC names that reject MARKET). Confirmed relevant
   live — the account holds fractional positions (e.g. 0.1985, 0.0009 sh).

---

## P5 — Budget sizing semantics (decision needed)

**Current:** budget = `OPENING_TRADE_BUDGET_USD` (from `.env`, no prompt in the armed
`advisory_monitor` path) is split **÷ actual number of matches** (capped at 5). So 1
match → full budget on it (this is why "$500" looked like "$500 per trade" on a
1-match morning), 5 matches → 1/5 each.

**Decision for the user:** keep **÷ actual-matches** (full deployment, concentrates
when few fire) OR switch to **fixed ÷5 = ~$100/trade** (predictable per-trade size,
leaves cash idle if <5 match). Also decide whether the armed path should *prompt* for
the budget (the prompt currently lives only in the non-scheduled `run_opening_live`).

---

## Reference artifacts from 2026-06-16
- Screenshots of all 21 watchlist charts (candles + SMA20/200) at the open:
  `logs/screenshots/2026-06-16-open/`.
- The session validated: scanner, real-time TV bars (300/symbol, 0 failures), cron
  firing, classifier, watchlist sync, news-nudge ranking — all worked. The gaps above
  are scope/timing/isolation, not the core data pipeline.

## Config in effect 2026-06-16 (for reference)
`OPENING_TV_AUTO_STAGE=true`, `OPENING_ALLOW_TRADING=false`, `OPENING_TRADE_BUDGET_USD=500`,
`OPENING_MAX_TRADES=5`, `OPENING_NEWS_FACTOR=5` (default, not pinned), `OPENING_DATA_SOURCE=tv`,
`OPENING_TV_CDP_PORT=9225`, `OPENING_SCAN_TOP_N`= (blank = no pre-bar cap),
`OPENING_TIGHT_THRESHOLD=0.005`, scan band `OPENING_SCAN_MIN/MAX_GAP_PCT=1/6`.
