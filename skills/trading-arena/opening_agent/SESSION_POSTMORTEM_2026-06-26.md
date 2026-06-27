# Session Postmortem — 2026-06-26 (Opening Power, live)

**Date:** Fri 2026-06-26 · **Account:** Questrade (via TradingView broker panel, CDP :9225) · **Mode:** live, manual-confirm (no auto-send)
**Headline:** First *full* session with completed live trades. The **strategy fired correctly on every name**; the **order-staging plumbing broke after the first fill**. Net trading impact ~breakeven gross; the value of the day is diagnostic.

All times below are **ET**. Broker/log raw stamps are machine-local **MDT (ET−2)** — converted here. Order IDs are from the Questrade orders panel (`tv_inspect_orders.js`).

---

## 1. Outcome — ground truth (broker order panel)

| Symbol | Entry | Exit | Qty | Gross P&L | Notes |
|---|---|---|---|---|---|
| **WSE** | Buy Stop @ **11.6515** @ 9:32:53 | Sell Stop (stop 11.81) filled **11.77** @ 9:49:31 | 8 | **+$0.95 (+1.02%)** | Only clean end-to-end trade. Trailed stop did its job. Add (+9) never filled → stayed 8. |
| **EQX** | Buy Stop @ **9.8283** @ 9:38:32 | Sell Stop (stop 9.84) filled **9.8442** @ 9:47:46 | 10 | **+$0.16 (~0%)** | Entry filled but **stop never attached** (DOM bug). The "protective" stop placed at 9.69 transmitted ~9.84 and executed instantly — closed the position rather than resting. |
| **NOW** | matched gate, **staged** Buy Stop @ 95 (SL 94.16) | — | — | — | Staging failed (DOM bug). Never placed. |
| **PLTR** | **staged** Buy Stop @ 110.95 (SL 109.91) | — | — | — | Staging failed (DOM bug). Never placed. |
| **MSFT** | **staged** | — | — | — | Staging failed (DOM bug). Never placed. |

> **Both positions are flat as of EOD** (positions panel rowCount 0 for these names).
> **Gross is ground truth; NET is not computed** — depends on the Questrade commission schedule. With these tiny share counts, per-trade minimum commissions likely make both round-trips **net-negative**. Do not report a net figure until the commission schedule is confirmed (per the no-hallucinated-data rule).

---

## 2. Timeline (ET)

- **08:24–09:15** — Pre-flight: DOM/tunnel/Questrade checks, data tabs closed, watchlist synced. Ready confirmed.
- **09:32:53** — **WSE Buy Stop 8 filled @ 11.6515**; Sell-Stop 8 @ 11.41 GTC working. First-ever completed agent order. ✅ Clean.
- **09:38:32** — **EQX Buy Stop 10 filled @ 9.8283** — but its stop **never attached** → naked long.
- **09:39–09:42** — PLTR & MSFT tickets pre-filled but **staging failed**; NOW matched the gate but **failed to stage**. User saw the PLTR ticket and asked if it triggered (it had not).
- **09:47:46** — Attempt to protect EQX with a plain Sell-Stop @ 9.69 → **~9.84 transmitted, executed instantly @ 9.8442**, closing EQX (~breakeven). User clicked Send (manual confirm held).
- **09:49:31** — **WSE trailed stop hit @ 11.77** (stop had trailed to 11.81). WSE closed +1.02% gross.
- **~09:58–10:03** — **Network/DNS failure**: `Temporary failure in name resolution` (Telegram sends failed) + `tv_broker_health.js timed out 30s → connected=False` at the cutoff. Broker-health went dark.
- **~10:00 onward** — User disengaged from live monitoring; session pivoted to gap-band sweep research. (WSE/EQX outcomes recovered later from the broker panel, not from live chat.)

---

## 3. Root causes

### RC-1 — DOM bug back: `STOP-LOSS SECTION NOT FOUND` (the headline)
The bracket-staging path could not locate the stop-loss section in the TradingView order ticket, so it aborted staging. It broke **NOW, PLTR, MSFT, and EQX's stop** — **only WSE (the very first order of the day) got through clean.** Same failure class seen on 2026-06-22.
- **Effect:** strategy signal was correct on every name; execution failed on everything after the first.
- **Critical secondary effect:** EQX's *entry* (a plain buy-stop) still filled while its *stop-loss* silently did not attach → an **unprotected naked long**. The entry and protection are not atomic.

### RC-2 — Order-ticket price snap-back (trust issue)
On the EQX protective stop, the ticket on screen showed **9.69**, the user confirmed it, yet **~9.84 reached the broker** and triggered instantly. **What you confirm ≠ what transmits.** This undermines the core safety model (manual confirm of a visible price). Likely the ticket re-read the live market price at/after Send rather than honoring the staged stop value.

### Contributing — infra outage
DNS name-resolution failed ~9:58–10:03 ET, killing Telegram alerts and broker-health polling right in the cutoff window. Independent of the order bugs but removes the alerting safety net exactly when it's needed.

---

## 4. What worked
- **Strategy / funnel:** 16/16 candidates got opening bars; the TIGHT/breakout gate selected real movers (WSE, EQX, NOW, PLTR, MSFT all legitimate matches). Signal quality is **not** the problem.
- **Manual-send safety held:** every order was confirmed by the user; nothing auto-sent. (Resolves the open question from the first-fill memory.)
- **WSE trailing stop:** entered, trailed up (11.41 → … → 11.81), and exited green automatically.
- **Bar capture:** full-session 2-min OHLC saved to `logs/session_replay_2026-06-26/` (9:45→16:00 ET) for replay/simulation.

---

## 5. Still missing / to verify
- **Net P&L** — needs the Questrade commission schedule for the account before any net number is published.
- **Why the WSE add (+9) didn't fill** — reconcile shows ordered 9 / position stayed 8; was it never sent, rejected, or non-marketable?
- **Telegram HTTP 400** (separate from the later DNS failure) — early-session alert failures need their own look.

---

## 6. Action items (fix list, priority order)
1. **✅ DONE 2026-06-26 — Fix RC-1 (DOM stop-loss section).** Rewrote the stop-loss attach in `tv_order_queue.js` (live path) + mirrored to `tv_order.js`, and hardened the pre-open canary `tv_stage_selftest.js`. Changes:
   - **Switch-anchored finder** — locate the SL toggle by `role=switch` whose *closest labeled ancestor* is "stop loss". (The old shortest-label walk failed intermittently; a naive ancestor-contains match grabbed the **Take-profit** switch because it shares an outer container — verified live and fixed.)
   - **Correct SL price input** — first decimal input *following* the SL switch in document order (true layout: entry, qty, TPsw, TP-input, SLsw, SL-input).
   - **Triple verification** before allowing send: readback ≈ intended, stop on the correct side of entry, within 25% band (kills the ticks-mode/unit disaster too).
   - **Neutralize-on-failure** — on ANY stop failure, blank the qty (`qty→0`) so a stopless entry is **not sendable**. This is the fix for *how the EQX naked long actually happened* (code aborted but left a populated stopless ticket; the user sent it by hand).
   - **`--dry` mode** added to `tv_order_queue.js` (full path, never submits, neutralizes after) for pre-open testing.
   - **Tested live (market closed, dry):** canary green incl. `stopLossAttachable`; 10/10 stability; SELL/short path; both negative cases (wrong-side, out-of-band) correctly rejected + neutralized; and the morning's exact sequence **WSE→EQX→NOW replays 3/3 verified** (EQX was the casualty).
   - Still TODO at next real open: watch one live 9:30 fire to confirm end-to-end.
2. **Fix RC-2 (price snap).** The staged stop price must be the price transmitted; verify the ticket doesn't re-read live market on Send. Add a post-send read-back assert (transmitted price == staged price) and alert on mismatch.
3. **Harden infra:** make broker-health/Telegram resilient to transient DNS (retry/backoff, local fallback alert); don't let a name-resolution blip flip `connected=False` at cutoff.
4. **Capture net P&L pipeline:** pull the commission schedule and compute true net per trade automatically into the session record.

---

*Sources: Questrade orders panel (`tv_inspect_orders.js`, order IDs on file), `logs/advisory_monitor.log`, `logs/opening_*` order intents, `logs/session_replay_2026-06-26/`, and the live session chat transcript (d9ceb622).*
