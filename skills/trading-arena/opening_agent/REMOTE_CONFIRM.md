# Remote order confirmation (Telegram ✅ tap → send)

A second, parallel way to confirm a staged **entry** order: tap ✅ Approve in
Telegram and the order sends — for "phone, not at the laptop." The manual
TradingView **Send Order** click stays live in parallel; whichever acts first
wins. **Entries only** (fixed-price buy-stops); the cutoff closes stay
laptop-only. **Off by default** (`OPENING_REMOTE_CONFIRM=false`).

## How it flows
1. At ~9:32 the monitor stages each entry, writes a sidecar
   (`logs/opening_confirm/<order_id>.json`, status `pending`, with a per-staging
   nonce), and sends one **card per order** with ✅ Approve / ❌ Skip.
2. You tap **✅ Approve** → the card asks **"SEND this order?"** (2-tap guard) →
   tap **✅ Yes, SEND** → the sidecar flips to `approve`.
3. The CDP runner (already waiting at the TV ticket) sees `approve`, does a
   **pre-click readback** — it reads the live ticket and requires the exact
   `<SIDE> <QTY> <SYM>` to be present — and only then clicks Send Order. It
   writes `sent` and Telegram-confirms. A readback mismatch is **refused** (use
   the laptop).
4. ❌ Skip → the runner cancels that dialog. No tap within the timeout
   (default 5 min) → the runner cancels and moves on (never leaves an armed
   dialog, never breaks the queue).

## Safety
- **Two taps**, and the second shows the exact order.
- The runner is the **only** thing that clicks; the tap just sets intent. The
  **readback** is what guarantees correctness (taps = intent, readback =
  correctness).
- Per-staging **nonce**: a stale card from an earlier staging is refused.
- Atomic sidecar writes; the runner never crashes on a partial/missing file.
- The laptop Send Order is unchanged and remains the fallback.

## Components
- `skills/shared/opening_confirm.py` — sidecar IPC, card text/keyboards, the
  nonce-checked 2-tap `handle_callback`. Zero import-time side effects.
- `advisory_monitor._stage_entries` — stamps order_id+nonce, writes sidecars,
  sends cards, passes `--remote-confirm`/`--timeout-ms` (when enabled).
- `stock_concierge.handle_update` — early `OPN|` branch routes taps to
  `opening_confirm.handle_callback` (no new bot/poller — the concierge already
  long-polls this token; a 2nd poller would 409).
- `tv_order_queue.js` — `--remote-confirm` polls the sidecar in the wait loop;
  `CLICK_CONFIRM_FN` (readback + click), `CLICK_CANCEL_FN`; `--no-click` test mode.

## Tested (this build)
- **T1** — 2-tap state machine: single tap never sends; stale nonce refused;
  skip / revert / nonce-guarded writes correct.
- **T3** — readback/click-target: clicks only on exact symbol+side+qty; refuses
  wrong qty/symbol/side; `--no-click` reads back but doesn't click; cancel works.
- **T2-lite** — cross-language IPC: concierge (Python) write → runner (Node) read,
  nonce gate holds.

## Enable + finish testing (do pre-market on a quiet day, NOT near the open)
1. `OPENING_REMOTE_CONFIRM=true` in `.env`.
2. `systemctl --user restart stock-concierge.service` (loads the `OPN|` branch).
3. **T2 (live, no transmit):** stage one entry and run the runner with
   `--remote-confirm --no-click`; tap Approve→Yes on the phone; confirm the log
   shows `✓ WOULD CLICK … readback matches` and it **Cancels** (nothing sent).
4. **T4:** repeat, tap **Skip** (dialog cancels + advances); and let one **expire**
   (dialog cancels + continues, queue not broken).
5. Only after T2–T4: **one supervised live order** — watch the laptop, tap from
   the phone, ready to Cancel on the laptop.

To turn off: `OPENING_REMOTE_CONFIRM=false` (+ restart concierge). Behaviour
reverts to laptop-only Send Order with zero other changes.
