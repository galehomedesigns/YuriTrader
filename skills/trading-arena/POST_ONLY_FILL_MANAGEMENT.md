# Post-Only (Maker) Fill-Management — Scoping

**Status: SCOPE ONLY. Nothing here is built. `KRAKEN_POST_ONLY_CONFIRMED`
stays `false` / unset until A–F below exist and are shakedown-tested.**

## Why this matters (and its ceiling)

Measured 2026-05-18 via a real resting XRPUSD round-trip (`~/maker_test.py`):
post-only fills at **0.4000%/side on both legs → 0.80% round-trip**, vs market
**0.80%/side → 1.60% round-trip**. Post-only halves the fee floor.

Ceiling, per [[arena-no-edge-structural-2026-05-17]]: 0.80% maker RT is still
**worse** than the 0.50% that memo assumed, and still far above the ~0% TA edge
and the 0.01–0.04% spreads. **Building this does not create an edge** — it only
lowers the hurdle for a strategy that has *already* cleared the promotion gate
(none has; `trend-breakout` is the only candidate and is statistically
unproven). So this is infra-readiness work, not a profitability fix. Do not
build it speculatively; build it when a gate-clearing strategy exists, or on
explicit instruction.

## The core defect

The arena assumes **immediate fill at scan price**. A post-only order rests on
the book and may: never fill, fill partially, fill at the limit price (≠ scan
price), or be rejected outright by Kraken's `post` flag if it would cross.

- `execute_arena_trade` ([kraken_executor.py:324](shared/kraken_executor.py#L324))
  calls `place_post_only_order` and returns the instant the order is *placed* —
  it never polls `query_order`.
- `paper_trader._open_position` ([paper_trader.py:359-396](shared/paper_trader.py#L359-L396))
  then writes the arena row `status:"open"`, `entry_price`/`fill_price` = the
  **scan price**, `qty` = **requested** volume, and decrements balance — i.e.
  it books a position that may not exist. → **phantom position.** The kill
  switch and live P&L then compute against a fill that never happened.
- `_close_position` ([paper_trader.py:470-491](shared/paper_trader.py#L470-L491))
  hard-codes `place_market_order` for the Kraken close. Even in `post_only`
  mode, **exits pay taker**, so the 0.008 maker RT in `config.py` is
  unachievable — it requires *both* legs maker.
- `fees_paid` ([paper_trader.py:505](shared/paper_trader.py#L505)) is an
  *estimate* (`KRAKEN_ROUNDTRIP_FEE_PCT * qty * price`) precisely because the
  executor "never calls QueryOrders". Real fills make this exact.

## Required work (build order)

**A. Fill-or-void wrapper in the executor.** New path (e.g.
`execute_arena_trade_post_only`, or `execute_arena_trade(..., wait_fill_secs=T)`):
place post-only, poll `query_order` every ~6s up to timeout T. Outcomes:
  - `closed` → return **real** `vol_exec`, `cost`, `fee`, avg fill price.
  - `open` at T → `cancel_order`; **entry policy = abandon** (return an
    explicit "not filled" status; no position is the safe state — repricing/
    chasing is a separate project, do not bundle it).
  - `canceled`/`expired`/rejected/no `txid` → "not filled".
  - partial (`0 < vol_exec < requested` at T) → cancel remainder; the position
    = the **actual `vol_exec`**, flowed back into the arena row.

**B. Entry contract change in `paper_trader`.** Live path must consume the real
result: `entry_price`/`fill_price` = actual avg fill, `qty` = actual
`vol_exec`, and **create no arena row when status is "not filled"** (today it
always creates the row from scan price).

**C. Exit symmetry.** `_close_position` Kraken branch must place a post-only
sell via the same wrapper. **Exit policy = market-fallback** (a position MUST
close; never leave it dangling) — accept taker on the exit leg when the maker
exit times out. Log it: realized RT is then a blend and the 0.008 kill-switch
model is optimistic on that trade. Acceptable only because timeouts should be
rare on near-zero-spread majors; must be visible, never silent.

**D. Exact fee accounting.** Replace the `fees_paid` estimate with the **sum of
actual `fee`** from the entry + exit `query_order` results. Removes the
estimate for live entirely.

**E. Phantom-position reconciler.** Startup/periodic pass: for every arena row
`status=open AND paper=false`, `query_order(kraken_order_id)` + check the
Kraken balance actually holds the position. If the order is open/canceled/
rejected → **void the row** (not "close at a fake price"). The kill switch's
`cancel_all` cancels orders but does **not** void arena rows — this closes that
gap and is the structural phantom guard.

**F. Gating.** `KRAKEN_POST_ONLY_CONFIRMED` flips true only after A–E exist,
pass `validate=true`, then survive one $5 live shakedown (the role
`~/maker_test.py` already plays manually — it is the proof the maker RT is
real and the pattern works).

## Net

A–F lower the realized round-trip from 1.60% → ~0.80% for any future
gate-clearing strategy. It is necessary-not-sufficient. The config fee defaults
were corrected to the measured 0.80/0.40 %/side on 2026-05-18 (behavior-neutral
at the current `market` mode; matters only once `post_only` is live).
