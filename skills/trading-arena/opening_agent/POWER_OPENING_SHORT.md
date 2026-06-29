# Power Opening Short

A **separate** strategy from the long Power Opening: the **short side** of the
opening-range, on the most-liquid names, with **limit entries**. Built 2026-06-22 as a
small live validation of an edge found in the 2-yr IBKR backtest.

> **Status:** ARMED LIVE (small). `$500`, 1 share per name. Disarm with
> `POS_SHORT_ALLOW_TRADING=0`. This is an **experiment**, not a proven money-maker —
> see [Validation & honest caveats](#validation--honest-caveats).

---

## Why this exists

Backtesting the full Power Opening strategy on 2 years of IBKR 2-min data showed the
**long side has no edge** (gross ≈ flat, net negative after costs). But the **short
side is gross-positive and robust**, and the edge **concentrates in the most liquid
names** — where execution is also cheapest. The entire net loss was **execution cost**,
not signal. So the scoped edge is: **short + liquid names + cheap (limit) execution.**

| Backtest (2yr IBKR, naive exit) | Gross/trade | IS half | OOS half | Symbols + |
|---|---|---|---|---|
| Short, all 72 names | +0.094% | +0.135 | +0.056 | 41/71 |
| Short, **top-40 liquid** | **+0.209%** | +0.255 | +0.157 | 24/40 |
| Short, **top-20 liquid** | +0.186% | +0.228 | +0.149 | 12/20 |

Break-even execution ≈ **9–10 bps/side**; our limit cap + fees ≈ **3–5 bps/side** → net
~**+0.10 to +0.15%/trade** *if the limit orders fill*.

---

## The signal

At **9:34 ET** classify the first 2-min bar (**bar-1**, 9:30–9:32) for each name in the
universe. A name is `MATCH_SHORT` only when three things agree on bar-1 (the real
`classifier.classify_opening`, same gate as the long strategy):

1. **TIGHT** — the 20- & 200-period SMAs are coiled together (compressed market).
2. **Location below** — bar-1 opens *below* the SMA band.
3. **Bearish power bar** — bar-1 is a bear elephant (large down body) or a topping tail.

Intuition: fade **failed morning strength** — short the breakdown continuation of a
coiled, weakly-located stock that just printed a bearish bar.

---

## The order structure (per name)

A 2-leg IBKR bracket. Worked example, bar-1 low **100.00** / high **102.00**:

```
   102.01  ← BUY STOP   (protective stop-loss)        child leg (held until entry fills)
   .............................  bar-1 high 102.00
                                  [ bar-1 range ]
   .............................  bar-1 low   100.00
    99.99  ← SELL STOP trigger (breakdown)         ┐  parent leg
    99.94  ← SELL LIMIT floor (buffer below)       ┘  (SELL STOP-LIMIT)
```

- **Parent — SELL STOP-LIMIT (entry):** rests until price falls to `entry_stop`
  (`bar1.low − offset`). Then becomes a **sell limit** at `entry_limit`
  (`entry_stop × (1 − buffer)`, default **5 bps** below) — fills at that price *or
  better*, never worse.
- **Child — BUY STOP (protection):** at `protective` (`bar1.high + offset`), inactive
  until the entry fills, then activates. Buys-to-cover at market if price rises to it.

### Why stop-LIMIT (the crux)
A plain **stop-market** short fills at whatever the market gives — you **pay** the
spread/impact open-ended (~0.05–0.10%/side), which is what made the strategy lose. The
**stop-limit caps** your worst fill at the buffer. The trade-off: if price **gaps
through** the limit, you **don't fill** (adverse selection on the fastest drops). The
buffer (`POS_SHORT_LIMIT_BUFFER_BPS`) is the knob — tighter = cheaper but fills less.

---

## Sizing & exits

- **$500 budget, 1 share per name, capped at $500 total notional**, liquidity-ordered,
  max 5 names. (At $100/slot you can't buy 1 share of NVDA/AVGO — 1-share-each lets it
  short the mega-caps at trivial risk, which is right for a validation test.)
- **Per-trade exit:** the protective buy-stop.
- **Session exit:** at **10:02 ET** the `--flatten` run cancels unfilled entries and
  buys-to-cover any open shorts (≤ ~30-min hold, matches the backtest).

---

## Universe (top-20 most liquid, by median bar $-volume)

`NVDA, PLTR, AMD, AVGO, MU, INTC, TSM, ORCL, COIN, SMCI, CRM, MRVL, GEV, ADBE, CSCO,
MARA, AMAT, QCOM, NBIS, VRT`

Override with `POS_SHORT_UNIVERSE` (comma-separated). Recompute liquidity from the cache
if the universe drifts.

---

## Implementation

| Piece | Where |
|---|---|
| Runner (place + `--flatten`) | `opening_agent/power_opening_short.py` |
| Bar **data** | `opening_agent/tv_bars` (TV real-time feed) |
| Order **execution** | `ibkr_exec/executor.py` → `place_short_bracket()`, `cover()` |
| State (today's symbols) | `logs/pos_short_state.json` |
| Crons (Mountain TZ) | `34 7` place (9:34 ET) · `2 8` flatten (10:02 ET) |

**Separation:** data comes from the TV feed, but **execution is the isolated IBKR API**
(not TradingView order staging). Own module, own flags, own `$500` budget, own clientId
(95), own 🩳 Telegram label. The long Power Opening is untouched.

### Config (`.env`)
```
POS_SHORT_EXEC=1              # enable the strategy
POS_SHORT_ALLOW_TRADING=1     # 1 = LIVE, 0 = SHADOW (log only, no IBKR connect)
POS_SHORT_BUDGET_USD=500
POS_SHORT_MAX_TRADES=5
POS_SHORT_LIMIT_BUFFER_BPS=5  # limit set this far below the breakdown trigger
POS_SHORT_CLIENT_ID=95
# POS_SHORT_UNIVERSE=...       # optional override
```

---

## Operations

- **Sole IBKR session:** keep TradingView **off** IBKR and stay out of the IBKR mobile
  app during ~9:25–10:05 — a competing login kicks the gateway (it then auto-reclaims
  via `ExistingSessionDetectedAction=primary`, but a restart needs a **2FA tap**).
- **Same account as the long strategy.** If both are armed, both fire (short $500 +
  long $1,000). For a clean test of *just* the short edge, disarm the long
  (`OPENING_IBKR_ALLOW_TRADING=0`).
- **Disarm short:** `POS_SHORT_ALLOW_TRADING=0` (cron reloads `.env` each run).
- **Shadow first:** set `POS_SHORT_ALLOW_TRADING=0` to log what it *would* short without
  sending.

---

## Validation & honest caveats

- The edge was found by searching the **same 2-yr window** it's tested on. The
  out-of-sample *half* held up (reassuring), but it's still in-period.
- Backtest used a **naive exit** and assumed fills; it does **not** model stop-limit
  **non-fills** (adverse selection) — the open question.
- **What the live test measures:** the real **fill rate** and **slippage** on the
  stop-limit entries. That's the make-or-break number we couldn't get offline.
- Treat it as an experiment. Edge is **thin** (~0.1–0.15%/trade) and execution-sensitive.

---

_Related: `SESSION_POSTMORTEM_2026-06-22.md`, the long-strategy IBKR executor
(`ibkr_exec/`), and the backtest tooling (`backtest_long_short.py`,
`backtest_short_exec.py`)._
