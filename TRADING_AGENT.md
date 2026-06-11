<!--
File: TRADING_AGENT.md
Purpose: Operating specification for the "Opening Power" trading agent
         (a.k.a. the "open candlestick agent").
Source:  Candles r2.pdf (2-minute opening-range candlestick strategy —
         tight/wide MA states, elephant bars, tail bars, red-bar takeouts).
Used by: skills/trading-arena/opening_agent/ (classifier, universe, ranker,
         delivery, engine) + YuriStocks Telegram delivery.
Scope:   First 20 minutes of the US equity regular session ONLY.
Version: 1.1 - 2026-06-10 (preset trade_offset, pre-market investigator,
         push-ratchet stops, mathematical bar classification)
-->

# Opening Power Trading Agent — Operating Specification

This document defines exactly how the agent operates: its data requirements, the
roles it runs, the governing rules it can never break, the decision flow from
pre-market to flat, and the configurable parameters that quantify the strategy.
The host app supplies live market data and order execution; this spec supplies
the brain.

---

## 1. Mission

Trade the first 20 minutes of the regular session on a pre-qualified watchlist
using 2-minute bars, a 20-period SMA, and a 200-period SMA. Enter only when a
**power bar** (elephant or tail bar) opens in the **right location** relative to
a **tight/narrow MA state**, ride the institutional follow-through, scale in
once, scale out on three pushes, and be flat by the session cutoff. When no
setup appears, the correct action is **no action**.

## 2. The Five Tools (strategy foundation)

| # | Tool | Definition |
|---|------|------------|
| 1 | Capital base | $50,000 account (configurable) |
| 2 | Timeframe | 2-minute OHLCV bars |
| 3 | Fast MA | 20-period simple moving average on 2-min bars |
| 4 | Power MA | 200-period simple moving average on 2-min bars |
| 5 | Structure | "Tight picture of power" — the tight/narrow MA state |

### 2.1 Market states

The market has only two states and flows narrow -> wide -> narrow forever:

- **Tight / narrow state** — 20 SMA and 200 SMA are close together. This is where
  the **best opportunities** live: explosions out of narrow states produce flow,
  follow-through, and trend. From a narrow state the agent trades **with** the
  direction of the break.
- **Wide state** — the 20 SMA has separated far from the 200 SMA. Second-best
  opportunity: the agent becomes a **contrarian**, expecting reversion toward
  narrow. Never trade momentum continuation from an already-wide state.

Getting the state right first removes ~85% of trading problems.

### 2.2 Location

- First bar of the day opening **above** a narrow state = bullish location,
  ~87% historical odds of upside follow-through.
- First bar opening **below** a narrow state = bearish location, ~87% odds of
  downside follow-through.
- The 13% failure case is handled exclusively by the stop (Rule G7).

### 2.3 Power bars (the signal vocabulary)

- **Bull Elephant Bar** — solid green bar with a body conspicuously larger/taller
  than recent green bars. Institutions putting money IN.
- **Bear Elephant Bar** — the red mirror. Money coming OUT.
- **Bottoming Tail Bar** — long lower tail, small body at top of range (body color
  irrelevant). Bullish: market rejected the low.
- **Topping Tail Bar** — long upper tail, small body at bottom of range. Bearish.
- **Little Red/Green Bar Takeout** — after entry, the first small counter-color bar
  whose high (long)/low (short) gets traded through. Used for adds, and valid as a
  standalone delayed entry (Section 5, Mismatch).

## 3. Agent Roles

Seven roles in a fixed pipeline. The Risk Governor and Session Clock override all
others at all times.

### R1 — Pre-Market Investigator (Trade Qualifier)
- **When:** pre-market, finishing just before the open (`premarket_scan_start`
  ~9:00 ET; final pass ~9:29 ET).
- **Job:** investigate which symbols fall within the trading criteria —
  1. **State test** (TIGHT vs WIDE via 20/200 SMA, `tight_threshold`).
  2. **Gap investigation** (indicated open vs prior close; beyond `max_gap_atr`
     = opens already WIDE = disqualified, G6).
  3. **Liquidity screen** (`min_price`, `min_avg_volume`).
  4. **Rank and select** the survivors; tag each with its narrow-state band.
- **Output:** `focusList[]` with a logged disqualification reason for every drop.

### R2 — Opening Bar Classifier
- Classify the first 2-min bar (type + location + gap). Output: MATCH(long),
  MATCH(short), MISMATCH, or NO-PLAY. **No orders while bar 1 is still forming.**

### R3 — Entry Trigger
- The instant bar 1 completes: long = stop-buy at `high + trade_offset`; the moment
  any later bar trades through, entry fires **intra-bar** with the **initial half**.
  Short mirrors at `low - trade_offset`.

### R4 — Risk Governor (overrides everything)
- Initial stop at bar-1 opposite extreme -/+ `trade_offset` — a one-bar loss,
  never widened. Push-ratchet: stop only moves in the trade's favor with each push.
  Per-trade risk cap `risk_per_trade`. Loss -> flatten, no re-entry that day.

### R5 — Position Builder (the Add)
- Only while in profit. First counter-color bar; if a same-direction bar removes it
  (`trade_offset` past its extreme), add the second half. Max `max_adds`. Never full
  size on the initial entry.

### R6 — Profit Taker (Three Pushes)
- Push 1: let it build. Push 2: rest limit orders ahead at the push-3 area.
  Push 3: orders execute, scale out. Stops ratchet under every push.

### R7 — Session Clock
- Window: bar-1-complete until `session_cutoff_min` (default 20). At cutoff: cancel
  unfilled orders, flatten per `eod_mode`. No entries outside the window.

## 4. Governing Rules (non-negotiable)

| # | Rule |
|---|------|
| G1 | Trade only the first 20 minutes after the open. |
| G2 | No order while the first 2-minute bar is still forming. |
| G3 | Only TIGHT symbols may be traded (WIDE = contrarian-watch only). |
| G4 | Direction must MATCH: positive bar + positive location -> long only; mirror for short. Never fight the location. |
| G5 | Entry is exactly `trade_offset` beyond bar 1's extreme, triggered intra-bar. |
| G6 | Gap too far = already wide = no breakout play. |
| G7 | Protective stop at `trade_offset` beyond bar 1's opposite extreme; one-bar loss; never widened. |
| G8 | Size derives from stop distance and `risk_per_trade`. Initial entry half size. |
| G9 | Add only to winners, via first counter-color bar takeout, max 2; a second consecutive counter-color bar cancels the add. |
| G10 | Take profits into the third push with resting orders placed during the second push. |
| G11 | MISMATCH = WAIT (removal play, Section 5); otherwise nothing-trade. |
| G12 | When nothing qualifies, do nothing. Flat is a position. |
| G13 | One losing exit per symbol per day; no revenge re-entries. |
| G14 | Log every decision with timestamp, bar data, and the rule that produced it. |
| G15 | Operate in the configured execution mode (`signal_only` vs `auto_execute`); signal_only never transmits orders. |
| G16 | The stop ratchets `trade_offset` below the latest pause low (long) with each completed push; never lowered. |
| G17 | All classification is computed mathematically from OHLCV (Section 7.1), never from chart images. |
| G18 | No symbol enters the focus list without the full R1 investigation; no mid-session additions. |

## 5. Mismatch Scenario (the patience play)

Narrow state, bullish location, but bar 1 is **red**: do nothing, mark its high. If
a green bar **removes** it (`trade_offset` past the high) -> that is the entry, stop
under the pair's low. If a second/third red bar prints -> nothing-trade. Mirror for
a green bar in a bearish location.

## 6. Decision Flow

```
PRE_MARKET -> R1 scan -> focusList (TIGHT only)
OPEN (bar 1 forming) -> R2 observe only, NO ORDERS
BAR 1 COMPLETE -> MATCH -> ARMED | MISMATCH -> WAITING | NO-PLAY -> STAND_DOWN
ARMED -> trigger -> IN_TRADE_HALF (stop per G7) | cutoff -> STAND_DOWN
IN_TRADE -> stop -> FLAT (done, G13) | add -> IN_TRADE_FULL | push2 -> rest orders |
            push3 -> scale out -> FLAT | cutoff -> flatten -> FLAT
FLAT/STAND_DOWN -> write journal (G14)
```

## 7. Quantitative Parameters

| Key | Default | Meaning |
|-----|---------|---------|
| `capital_base` | 50000 | Account equity (USD) |
| `bar_interval` | 2m | Bar size |
| `fast_ma` / `slow_ma` | 20 / 200 | SMA periods |
| `tight_threshold` * | 0.25% of price | Max 20/200 SMA separation to call TIGHT |
| `elephant_body_mult` * | >= 2.0x avg body of prior 20 bars, body >= 70% of range | Elephant qualification |
| `tail_ratio` * | tail >= 2.0x body; opposite tail <= 0.25x range | Tail-bar qualification |
| `small_bar_max` * | body <= 0.5x avg body of prior 5 bars | "Little" counter-color bar |
| `max_gap_atr` * | open > 1.5x ATR20 from prior close = too far | Gap disqualifier (G6) |
| `trade_offset` | preset by trader (source teaches $0.01) | Offset for ALL trigger levels |
| `premarket_scan_start` | 9:00 ET | When R1 begins |
| `min_price` / `min_avg_volume` * | $5 / 500k | R1 liquidity screen |
| `initial_fraction` | 0.5 | Fraction of full size on first entry |
| `max_adds` | 2 | Maximum scale-ins |
| `risk_per_trade` * | 1.0% of equity | Hard per-trade risk cap |
| `max_concurrent` * | 2 | Max simultaneous open symbols |
| `session_cutoff_min` | 20 | Minutes after open to stop trading |
| `push_count` | 3 | Pushes before full exit |
| `eod_mode` | flatten | Action at cutoff with open position |
| `execution_mode` | signal_only | `signal_only` or `auto_execute` (G15) |

### 7.1 Bar Classification Mathematics (no images — G17)

```
body       = |close - open|
range      = high - low
upper_tail = high - max(open, close)
lower_tail = min(open, close) - low
green      = close > open        red = close < open
avgBody(n) = mean(body) over the prior n completed bars
sep        = |SMA20 - SMA200|
```

| Pattern | Test |
|---|---|
| TIGHT | sep / price <= `tight_threshold` |
| WIDE | not TIGHT; direction = sign(SMA20 - SMA200) |
| Positive location | bar 1 opens above max(SMA20, SMA200) of a TIGHT pair |
| Negative location | bar 1 opens below min(SMA20, SMA200) of a TIGHT pair |
| Bull elephant | green AND body >= 2.0x avgBody(20) AND body >= 0.7x range |
| Bear elephant | red AND body >= 2.0x avgBody(20) AND body >= 0.7x range |
| Bottoming tail | lower_tail >= 2.0x body AND upper_tail <= 0.25x range |
| Topping tail | upper_tail >= 2.0x body AND lower_tail <= 0.25x range |
| Small bar | body <= 0.5x avgBody(5) |
| Takeout | later bar trades >= barHigh + `trade_offset` (long) / <= barLow - `trade_offset` (short) |
| Pause | a completed bar fails to make a new trade-high (long) |
| Push | a new trade-high after >= 1 pause bar -> push += 1; ratchet stop (G16) |

## 8. Live-Data & Execution API Requirements

1. Historical bars: >= 200 x 2-min bars per symbol before the open.
1a. Pre-market quotes for gap measurement + liquidity screen.
2. Streaming: real-time 2-min bars (or tick the agent aggregates) with intra-bar
   high/low — entries/adds trigger intra-bar, not on close (G5).
3. Exchange-synced clock for 9:30:00 open and 9:32:00 bar-1-complete.
4. Orders (auto_execute): stop, stop-limit, limit, market; OCO preferred.
5. Account equity / buying-power for the Risk Governor.

## 9. Journal (G14)

One JSON record per symbol per day: pre-market state, bar-1 OHLCV + classification,
verdict, trigger/fill prices+times, stop, adds, pushes, exit fills, realized P&L,
and the rule behind each action.

---

> **Disclaimer:** Encodes a discretionary teaching strategy into mechanical rules
> for research/automation. Not financial advice; the "87%" figures are from the
> source material and unverified. Use `signal_only` until validated on your own
> data and broker.
