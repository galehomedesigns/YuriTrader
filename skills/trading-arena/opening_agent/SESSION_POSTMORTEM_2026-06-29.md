# Session Postmortem — 2026-06-29 (Opening Power, live)

**Date:** Mon 2026-06-29 · **Account:** Questrade (via TradingView broker panel, CDP :9225) · **Mode:** live, manual-confirm (no auto-send)
**Headline:** The strategy matched correctly on the live opening bar and the **stop-loss DOM bug did NOT recur** (the 06-26 fix held). The losses this session were all one root cause: **the queue stages "blind" — it treats the confirm dialog closing as success and never checks whether the order actually reached the broker.** That single gap explains the lost SMR/GILT bids, the phantom OUST/SMR trailing, and the un-sent ASTS close.

All times below are **ET**. Raw log/file stamps are machine-local **MDT (ET−2)** — converted here.

---

## 1. Outcome — ground truth

Bar-1 matches that became staged entries (`logs/opening_orders_bar1.json`): **ASTS, GILT, SMR**. Two more matched but were risk-capped: **OUST, VRDN**. Premarket #1 (**NVDA**, SHORT) was filtered (long-only cash account).

| Symbol | Matched | Staged entry | Result (ground truth) | Why |
|---|---|---|---|---|
| **ASTS** | ✓ live bar | Buy Stop @ 78.01 (SL 75.97) | **FILLED, held 2 sh.** Close (Sell 2 @ 76.82 LIMIT) **never sent** → still held at 10:01 | Close confirm dialog never appeared; reconcile reported `ASTS 2→2 STILL HELD` |
| **SMR** | ✓ live bar | Buy Stop @ 10.47 (SL 10.23) | **LOST** — never live at broker | Confirm dialog closed but order never rested; later stop-move found *"no resting QUEUED bracket order"* |
| **GILT** | ✓ live bar | Buy Stop @ 12.65 (SL 12.46) | **No position.** Indeterminate from logs: un-sent, or sent-but-never-triggered (no breakout) | No position and no trail attempts ever fired for it |
| **OUST** | ✓ live bar | — (risk 6.1% > 3% cap) | **No bid.** Then phantom-managed: stop ratcheted 46.25→50.61 + TP 52.67, all failed | Risk-cap skip at entry, but stayed in the book → trailed a position that never existed |
| **VRDN** | ✓ live bar | — (risk 3.4% > 3% cap) | **No bid** | Same risk-cap skip |
| **NVDA** | ✓ (#1, SHORT) | — | **No bid** | Long-only cash account filters shorts ([advisory_monitor.py:105](advisory_monitor.py#L105)) |

Positions at ~10:00 (`opening_close_reconcile.json` + queue `positions before`): `{ASTS:2, …prior-day holdings}`. **Only ASTS filled this session, and its exit failed.** (BTQ/PDYN/USAR/BIRD on the book are stale, un-flattened positions from prior sessions — a separate accumulation problem.)

---

## 2. Timeline (ET)

- **09:32:51** — Opening bars ready for 115/116 names; classifier matched ASTS, GILT, SMR, OUST, VRDN on the live bar.
- **09:32:52** — Bar-1 queue stages ASTS/GILT/SMR. ASTS & GILT each **fail their first staging attempt** with `ORDER-TYPE MISMATCH` (ASTS shows "77.72 SELL"; GILT shows "…LIMIT"); both recover on the one retry. SMR stages clean. All three log `confirmation closed - advancing`.
- **09:32–09:52** — Only ASTS actually goes live (filled, 2 sh). SMR & GILT never rest at the broker, but the queue believed all three succeeded (`queue done: staged 3/3`).
- **09:38–09:52** — Engine fires trail/TP rules for OUST (never entered) and SMR (never sent). Every `modify … stop` / `take-profit` returns **"no resting QUEUED bracket order"** and a manual-fallback alert.
- **~10:00–10:01** — Cutoff close. CDP/broker link flapping (`no QUESTRADE broker DOM`). ASTS close (Sell 2 @ 76.82 LIMIT) clicked submit but **confirm dialog never appeared** → not sent. Reconcile reads positions and reports `ASTS 2→2 STILL HELD`.

---

## 3. Root causes

### RC-1 — Stage-and-forget: the queue never verifies the order reached the broker (the headline)
After clicking the ticket's Buy button, the queue waits for the "Send Order" confirm dialog to **close**, then logs `confirmation closed - advancing` and moves on ([tv_order_queue.js:405-420](tv_order_queue.js#L405-L420)). But the dialog closes the same way whether you click **Send Order** (sent) **or** Cancel / it gets dismissed (not sent). There was **no re-read of the broker orders/positions table** to confirm the order actually rested.
- **Effect:** SMR and GILT were recorded as staged-success but never went live. The failure only surfaced minutes later, indirectly, when a trail-stop couldn't find the order.

### RC-2 — Trailing/TP staging is fill-blind (the phantom OUST/SMR management)
The `book` of names to manage is populated at **arm time**, not fill time ([advisory_monitor.py:416](advisory_monitor.py#L416)). The risk-cap skip ([advisory_monitor.py:182-188](advisory_monitor.py#L182-L188)) only skips *placing* the order — it leaves the name in the book. And `_stage_stop_move` / `_stage_take_profit` never read positions before acting (unlike `_stage_add`, which does). So OUST (risk-capped, never entered) and SMR (never sent) got trailed as if held → a stream of `no resting QUEUED bracket order` failures and noise.

### RC-3 — The ASTS close couldn't send, and isn't retried
At the 10:00 cutoff (broker link flapping), the close ticket's submit click did not raise a Send Order dialog — STAGE_FN returned `confirm dialog not detected (will reconcile via positions)` ([tv_order_queue.js:307](tv_order_queue.js#L307)). The position-reconcile correctly **detected and Telegram-reported** `STILL HELD` ([advisory_monitor.py:281-291](advisory_monitor.py#L281-L291)) — so it was *not* silent — but the close was **not retried**, and the queue's own actionable "SELL by hand" fallback only fires on the *retry-failed* path, not this one.

### Contributing — racy ticket setup
`ORDER-TYPE MISMATCH` on the ASTS/GILT first attempts ([tv_order_queue.js:290-296](tv_order_queue.js#L290-L296)): the Stop tab / Buy side hadn't settled before the verify ran. The guard worked (refused the wrong-type ticket; retry recovered both) — but a second hiccup would have dropped them.

---

## 4. What worked
- **Strategy / funnel:** 115/116 candidates got opening bars; the gate matched real movers. Signal quality is not the problem.
- **The 06-26 stop-loss DOM fix held:** ASTS/GILT/SMR all show the new verified-readback path (`SL switch found`, `SL input set … readback`, `VERIFIED readback=…`). `STOP-LOSS SECTION NOT FOUND` did **not** recur.
- **The order-type-mismatch guard** caught two wrong-state tickets and the retry recovered both.
- **Close reconcile** read back real positions and correctly flagged ASTS as still held.

---

## 5. Fixes implemented (this session)

### FIX-1 (RC-1) — Post-batch entry verification + alert  ·  `tv_order_queue.js`
After the batch, read the broker **once** and confirm each staged **entry** is actually resting (`QUEUED`/`WORKING`) or already filled (a position). Anything missing → the Send Order was missed/cancelled → fire a Telegram alert and record `verified:false`; also written to `logs/opening_entry_verify.json` and surfaced in the run summary (`entries live N/M`).
- New `READ_ORDERS_FN` (mirrors `READ_POSITIONS_FN`); verification block before `sock.close()`; non-fatal; skipped in `--dry`.
- **Unit-tested** on today's exact case (mock broker state): ASTS=filled→ok, GILT=queued→ok, SMR=absent→**alert**; the ASTS *close* is correctly excluded from entry verification. ✅

### FIX-2 (RC-2) — Fill-gate the trailing/TP staging  ·  `advisory_monitor.py`
`_stage_stop_move` and `_stage_take_profit` now call `_held_longs()` and **skip** if the symbol isn't actually held (`None` on read error → fail **open**, so a real winner never loses protection over a transient glitch). This is the same fill-awareness `_stage_add` already had. Phantom OUST/SMR trailing can no longer fire.

### Not yet done (proposed follow-ups)
- **RC-3:** retry the close (or re-frame the `STILL HELD` reconcile message as an actionable "SELL N by hand now"). The close already alerts, but doesn't retry.
- **Stale-position accumulation:** BTQ/PDYN/USAR/BIRD never got flattened on prior days — closes need a same-day sweep that actually confirms flat.
- **Racy ticket setup (contributing):** add a settle-wait/re-assert on the order-type tab before the verify, so the first attempt succeeds instead of relying on the retry.

---

## 6. One-line lesson
The agent was **staging blind and managing blind** — it didn't know which of its tickets actually became live orders. FIX-1 gives it eyes on entries; FIX-2 stops it acting on positions it doesn't hold. Verify-after-send is now the contract.
