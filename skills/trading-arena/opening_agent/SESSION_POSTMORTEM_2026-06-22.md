# Opening-Power session post-mortem — 2026-06-22 (Mon)

First live-watched 9:30 open with auto-staging on. **Net outcome: no agent-staged
order completed a real fill; the Questrade broker session dropped mid-session; and
the symbols the operator expected did not pass the agent gate.** Details below,
each traced to ground truth (live watcher captures + logs). Times are ET.

> Caveat on sources: `logs/advisory_monitor.log` has **no date stamps and
> interleaves multiple days**. Only items confirmed via the live real-time watcher
> during the session, or via the most-recent log tail, are attributed to today.
> Prior-day blocks (e.g. BTQ/EOSE/MNTS/SMR/MP/FRMI staging, "47/47"/"50/50") are
> NOT today and are excluded.

## 1. Timeline (today, verified)

| Time | Event | Source |
|---|---|---|
| ~08:00–08:21 | Premarket scan ran; 64 candidates; TV watchlist synced | scan log |
| pre-open | Operator clicked Connect → broker verified `connected:true`; 20/200 SMA added to 2-min chart, saved as layout **"Opening Power"** | broker_health, CDP |
| 09:32:03 | advisory_monitor preflight: **broker connected=True** | watcher |
| 09:33:20 | "opening bar ready for **63/64**" | watcher |
| 09:33 | **USAR** MATCH_LONG → auto-stage `buy 3 @ 25.15 (SL 24.55)` → **`ORDER-TYPE TAB NOT FOUND: stop`** → **staged 0/1** | watcher |
| 09:33 | **ASST, KEEL, RIOT, BIRD** matched but **skipped on 3% risk cap** (3.4 / 7.0 / 3.3 / 10.8%) | watcher |
| ~09:51 | Manual USAR re-stage → **staged 1/1** ("Buy 3 USAR @ 25.15 STOP"); confirmation reported `closed - advancing` (**never verified sent**) | queue runner |
| 09:51:13 | broker still connected=True | log tail |
| **09:52:05** | **broker DROPPED**: `no QUESTRADE broker DOM (panel closed or link down)` — stays down thereafter | log tail |
| ~09:52–09:53 | Manual USAR sell-close attempt → `ORDER-TYPE TAB NOT FOUND: limit` → **staged 0/1**; positions read `[]` (**post-drop, unreliable**) | queue runner |
| 09:55 | :9225 watchdog exits — **tunnel stayed UP the entire session** (`final_state=up`, zero drops) | watchdog |

Positions snapshot **at 09:51** (last read while broker was up): USAR 7, FANCF 107,
BTQ 1, PDYN 1, Q 0.1985 (dust), LIXT 0.0009 (dust). **Whether any of these are real
holdings vs. fills, and their true state now, is UNKNOWN** — see §4.

## 2. Why none of the confirmations came true

Every agent auto-stage failed to produce a confirmed order, from **DOM-read flakiness
on the TradingView order ticket** (not the strategy, not the tunnel):

- `ORDER-TYPE TAB NOT FOUND: stop|limit | visible: ["","",...]` — the order-type
  tabs are found as elements but their **innerText comes back empty**. This is the
  documented *"Windows Chrome intermittently drops chars from innerText via CDP"*
  failure (see comment in `tv_order.js`). The finder matches tab labels by text, so
  empty text ⇒ "not found".
- Same root cause family: `SUBMIT BUTTON NOT FOUND`, `STOP-LOSS SECTION NOT FOUND`
  (seen on prior days too).
- It is **intermittent** — the *same* USAR order failed at 09:33 but staged cleanly
  on the ~09:51 retry; EOSE staged while BTQ failed in the same batch on a prior day.
- The manual USAR re-stage *did* stage (1/1) but the confirmation dialog closed
  before a verified human Send — so even that one is **not confirmed sent**.

**Fix direction:** the order-type tab / submit / stop-loss finders should match on
**`data-name` attributes** (robust, like the broker-health check does) instead of
`innerText`, and/or wait+retry until tab labels render before declaring "not found".

## 3. Watchlist re-classification at the 9:32 bar (the operator's core question)

Re-ran the **real classifier** over all 64 watchlist symbols on the settled 2-min
bars (script `/tmp/reclassify_932.py`, bars via the data tabs). **bar1 = 09:30 bar.**

- **MATCH_LONG (3):** ASST, RIOT, SBET — all TIGHT + above.
- **MISMATCH (3):** POET, VRT, WULF (TIGHT but direction/location disagree).
- **NO_PLAY (57):** everything else — the dominant reason is **WIDE** (the 20/200
  SMA band is not coiled). Only ~9 of 64 were TIGHT at all.
- **SMCI = WIDE/NO_PLAY. BMNR = WIDE/NO_PLAY.** The names the operator believed
  "passed" did **not** pass the gate — they gapped/looked strong but the SMA band
  was wide and (per premarket scan) printed bearish topping-tails. This matches the
  live run, which also never matched them.

**Key finding — verdicts are data-timing sensitive.** The live 9:33 run matched
**USAR + 4 risk-capped** (USAR, ASST, KEEL, RIOT, BIRD); this 10:01 re-fetch matches
**ASST, RIOT, SBET** (USAR/KEEL/BIRD now NO_PLAY). Same classifier — the *bars*
differ: intraday 2-min bars and the resulting SMA20/200 separation shift between a
real-time fetch and a later re-fetch, and **TIGHT vs WIDE sits on a knife-edge** for
many names, flipping the verdict. ASST and RIOT matched in *both* passes.
(Caveat: the `power` tags weren't captured in the re-run table — attribute access
returned blank; decision/state/location are authoritative.)

**Implication:** to audit "what passed at 9:32" reliably, the agent should snapshot
the exact bar series + SMA values it used at decision time. A post-hoc re-fetch is
not guaranteed to reproduce the live decision.

## 4. Why Questrade dropped (root cause)

Probed the live trading tab at 10:01 (still on USAR):

- **`[data-name="QUESTRADE.*"]` elements: 0** — the Questrade broker DOM is genuinely
  absent (and no "Questrade" text node exists, so it is *not* the innerText-drop bug;
  the broker-health check uses attribute selectors, which are reliable).
- Chart **order panel is present** (`buy-order-button`, `sell-order-button`,
  `order-panel`) — tab + tunnel healthy.
- **Smoking gun:** TradingView toast DOM nodes named **`account-deletion-initiated`**
  and **`account-deletion-cancelled`** are present.

Conclusion: the **TradingView ↔ Questrade broker session was torn down ~09:52** (TV
fired broker-account-removal toasts; the Questrade panel vanished, likely leaving the
built-in/paper panel). This was **not** the CDP tunnel (watchdog proved :9225 up all
session) and **not** a read glitch. TradingView is known to drop the Questrade broker
session periodically, needing a manual re-Connect/re-auth.

**Consequence:** every order/position read **after 09:52** (incl. the "account is
flat" reading) reflects a **disconnected/paper** state, **not** the real Questrade
account. The "flat" conclusion stated mid-session was premature and is retracted.

## 5. Open items / unresolved

1. **TRUE Questrade state unknown.** Reconnect Questrade in the TV broker panel, then
   re-read positions/orders AND verify directly via Questrade (the REST API is
   read-only but can *read* portfolio/orders/history) to learn: did the manual USAR
   re-stage actually send? Are USAR 7 / FANCF 107 real holdings or fills? Reconcile
   against execution history.
2. **Order-ticket staging reliability** — fix the innerText-based finders (§2).
3. **Broker-session resilience** — detect the account-deletion toast / auto-alert on
   it; consider a faster reconnect prompt.
4. **Decision auditability** — snapshot bars+SMAs at arm time (§3).

## 6. Telegram stream reconciliation (operator-forwarded, today)

The full bot Telegram stream was recovered and reconciles cleanly:

- **5 names armed** (🎯 "passed the 2-min test"): ASST, KEEL, RIOT, BIRD, USAR at
  buy-stops $15.48 / $7.11 / $29.37 / $6.89 / $25.15.
- **Only USAR auto-staged** ("Staging 1 order"); ASST/KEEL/RIOT/BIRD were **risk-capped**
  → operator placed those 4 **by hand** ("I did the other 4").
- **SBET + HPE passed but top-5 capped** ("not arming: SBET, HPE") — corroborates the
  re-classification (SBET independently flagged MATCH).
- Broker-down 🚨 alert fired at **09:52:05** matching §4 to the second; final cutoff
  alert told operator to **close by hand** because the link was down.

**CRITICAL caveat:** the entire post-arm stream — "🟢 you're IN", "🔼 move stop",
"➕ ADD", "💰 take-profit", "🛑 STOPPED OUT", "🏁 CLOSE" — is the **engine's model off
the live bars, NOT confirmed broker fills.** The agent had no confirmed USAR fill
(auto-stage failed) and **cannot see the 4 manual orders at all**, so those coaching
lines are what *should* happen if manual fills tracked the model. Every "Stop-move
staged… click Confirm" after 09:52 could not actually stage (broker down + DOM bug).
Treat the stream as guidance, not an account ledger.

**Verdict instability corroborated:** live matched 7 (5 armed + SBET/HPE capped);
10:01 re-run matched 3 (ASST/RIOT/SBET). KEEL/BIRD/USAR/HPE flipped to NO_PLAY on
re-fetch — the TIGHT/WIDE knife-edge (§3). ASST/RIOT/SBET stable.

**Also: Telegram delivery was lossy** — `TG send error: HTTP Error 400` in advisory +
scan logs (HTML/length); some alerts never reached the phone.

## 7. FIXES APPLIED (2026-06-22, post-session)

Root cause of the recurring staging failure: the order-type tabs (Market/Limit/Stop)
carry **no `data-name` and no `aria-label`** (verified via live DOM probe) — matchable
only by text — and on a cold ticket the Windows Chrome returns **empty `innerText`**
over CDP. The "char-drop-tolerant" fix added data-name/aria-label fallbacks that don't
exist on these tabs, so it stayed 100% innerText-dependent → failed cold, worked warm.

- **🅱 `textContent` matching** in `tv_order.js` + `tv_order_queue.js`
  (`findOrderTypeTab` + `findSection`/stop-loss). textContent is layout-independent
  and stays populated when innerText drops. Retry budget 3→6. node --check passes.
- **🅲 Telegram resilience** (`run_opening_scan.py`): `_tg_send_one` now falls back to
  **plain text on any HTML/400 failure** (tags stripped) so alerts never silently
  drop. `_chunk` (length) was already present; this covers the HTML-validity failures.
- **🅰 Pre-open canary** (`tv_stage_selftest.js` + wired into `preopen_check.py`):
  at 8:30 + 9:15 ET (broker-connected only), opens the ticket and verifies the Stop
  tab + Stop-loss section + Submit button are locatable — **never submits**. Alerts
  🔴 if the staging DOM path is broken, so it's caught before 9:30, not at 9:32.
  Smoke-tested live: `ok:true`, all four checks pass.

- **🅳 Real-time manual-fallback** (`tv_order_queue.js`): on a stage miss it now
  **re-warms + retries once**, and if it still can't fill the ticket it **instantly
  Telegrams the EXACT manual order** (`⚠️ STAGE FAILED — place BY HAND now: BUY 3 USAR
  STOP @ 25.15, STOP-LOSS 24.55`). Broker rejections also alert. A staging failure can
  no longer silently lose a setup — worst case you place it by hand in seconds.

**Better-primary investigation:** probed for a programmatic TradingView broker/order
API on the page — **none is exposed** (`window.TradingViewApi` has no broker/trade
methods; widget has none). So driving the broker object instead of the ticket UI is
not viable on tradingview.com. **The only path that fully removes the browser/DOM
layer is an order-scoped Questrade REST token** (place orders directly) — currently
blocked by the read-only token; a re-authorization with trade permission is the real
structural fix. Open decision for the operator.

**Still to verify:** a full broker-connected canary run + the next live 9:30 open
(the cold-ticket condition). Reconnect Questrade and the 8:30/9:15 canary will report.

## 8. What worked

- CDP tunnel (:9225) stable all session (watchdog: zero drops).
- Pre-open prep: watchlist sync, 20/200 SMA band saved, broker preflight = connected.
- Risk cap correctly rejected wide-stop names (ASST/KEEL/RIOT/BIRD).
- The strategy gate correctly rejected SMCI/BMNR (WIDE) — no false signal.
