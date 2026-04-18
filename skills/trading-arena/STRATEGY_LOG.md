# Trading Arena Strategy Log

> Complete history of how the trading bot strategies were developed, why each
> design decision was made, and what the system tests at every layer.
>
> **Last updated:** 2026-04-12

---

## 1. Why We Built This

**Goal:** Build a paper-trading system where multiple bots compete using
different trading strategies, learn from real market data, and surface which
strategy combinations actually make money. Eventually graduate the winners
to live trading.

**Constraints:**
- 4GB RAM VPS — every workload must be lightweight
- No paid LLM APIs — use local Ollama on the GX10 GPU server only
- Stay paper until win rate > 55% and expectancy is positive over 100+ trades
- Notify Tony on Telegram for every meaningful event

---

## 2. The Three-Layer Architecture

```
┌─────────────────────────────────────────────────┐
│  LAYER 1 — Dynamic Watchlist (every 2 hours)    │
│  Picks top 20 movers from 65 symbols by:        │
│  |change%| × log(volume)                        │
│  No hardcoded "always-on" symbols.              │
└─────────────────────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────┐
│  LAYER 2 — Arena (every 5 minutes)              │
│  10 bots, each with a different TAY combination │
│  Scans the dynamic watchlist                    │
│  Paper trades on Supabase                       │
│  Telegram alert on every buy/sell               │
└─────────────────────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────┐
│  LAYER 3 — Overseer (3x daily)                  │
│  9:00 AM   — pre-market game plan               │
│  4:30 PM   — daily autopsy + TAY analytics      │
│  Friday 6PM — weekly super-prompt               │
│  Uses GX10 'quick' (qwen3.5:35b) model          │
└─────────────────────────────────────────────────┘
```

---

## 3. The TAY Framework

Every bot follows the same **one-rule structure**, but each bot defines its
own variables for the three components:

| Letter | Question | Examples |
|--------|----------|----------|
| **T** (Trend) | Which way is the market going? | 50 EMA, 200 SMA, ADX, market structure (HH/HL) |
| **A** (Area of Value) | Has price reached a meaningful level? | S/R, MA pullback, trendline, BB bands |
| **Y** (Entry trigger) | Is there confirmation to enter NOW? | Candlestick pattern, breakout, false break |

**Rule:** ALL THREE components must pass for a buy signal. This is "one rule
with three dependencies" — high conviction, fewer trades, higher quality.

**Why this design?**
- Discretionary rule-based traders use this approach (TAY/MAY/MAEE frameworks)
- 96 of 96 high-confidence YouTube strategies follow some variation of it
- It forces bots to wait for confluence rather than firing on single signals
- Each bot tests a different combination → the arena reveals which combinations win

---

## 4. The Source Data: 96 YouTube Strategies

We scraped, transcribed, and extracted **98 trading strategies** from YouTube
videos using our `youtube-strategy` pipeline. After filtering to confidence
score ≥ 3, we kept **96 high-confidence strategies**.

**Pipeline (one-time, already done):**
1. `channel_scanner.py` — enumerates videos from YouTube channels
2. `transcript_fetcher.py` — downloads transcripts via yt-dlp
3. `strategy_analyzer.py` — extracts strategies via LLM (Gemini → Ollama fallback)
4. Stored in Supabase `yt_strategies` table

**Strategies by type:**

| Type | Count |
|------|-------|
| Trend following | 26 |
| Price action | 17 |
| Breakout | 10 |
| Swing | 4 |
| Mean reversion | 3 |
| Reversal | 3 |
| Position / momentum / counter-trend | 5 |
| Multi-component (combinations) | 28 |

**Confidence breakdown:**
- Score 4 (specific, well-defined rules): **69 strategies**
- Score 3 (general but usable): 27 strategies
- Score 2 (vague mentions): 2 strategies (excluded)

---

## 5. The Synthesis: STRATEGY_DIGEST.md

**One-time analysis** ran on the GX10 `quick` model (qwen3.5:35b) reading all
96 strategies and producing a digest at `STRATEGY_DIGEST.md`.

**Most common TREND definitions:**

| Component | Strategy Count |
|-----------|----------------|
| Market Structure (HH/HL or LH/LL) | 14 |
| 200-period MA filter | 8 |
| Higher timeframe structure | 7 |
| Trendlines/channels | 7 |
| Range bound | 3 |

**Most common AREA OF VALUE definitions:**

| Component | Strategy Count |
|-----------|----------------|
| **Horizontal Support/Resistance** | **47** ← dominant |
| Moving averages (50/200/20) | 29 |
| Swing highs/lows | 15 |
| Trendlines | 10 |
| Range boundaries | 4 |

**Most common ENTRY TRIGGER definitions:**

| Component | Strategy Count |
|-----------|----------------|
| **Candlestick reversal patterns** (Hammer, Engulfing) | **31** ← dominant |
| Breakout of structure/level | 21 |
| False break/rejection | 9 |
| Price action weakness/momentum | 7 |
| Indicator triggers (RSI, Stoch, MA cross) | 4 |

**Top 5 indicators used overall:**

1. Support/Resistance & Market Structure — 47 strategies
2. Moving Averages (20/50/100/200) — 35 strategies
3. Candlestick Patterns — 31 strategies
4. **ATR (Average True Range)** — 23 strategies (used for stops)
5. Trendlines — 10 strategies

**Key insights from the digest:**
- **Confluence is critical** — the highest-frequency setup is S/R + MA together
- **Candlesticks drive entries** — over 30% of strategies use specific patterns rather than indicator crosses
- **ATR is the standard for stops** — 23 strategies use volatility-adjusted stops, not fixed %
- **Trend bias is king** — 22 strategies filter direction with 200 MA or HH/HL structure
- **False breakouts are high-value** — 9 strategies specifically target trap liquidity

---

## 6. How We Use the Digest (NOT Averaging)

**The numbers don't sum to 96 because each strategy uses MULTIPLE components.**

Example: A strategy might use both a 200 MA filter AND market structure for trend.
That strategy gets counted in BOTH buckets.

So the digest tells us **which components are popular**, not **which combinations**.

**Wrong approach:** Build one mega-bot that uses all the popular components.
- Just averages ideas without testing whether the combination works
- No way to know which specific T+A+Y combination is the winning one

**Right approach (what we did):** Build different bots, each testing a
**different combination** of T/A/Y components. The arena reveals which
combinations actually win. The overseer tracks which T/A/Y components
contribute to wins via the new `tay_components` log.

---

## 7. The 10 Bots and Their TAY Combinations

### Bots Upgraded with S/R + Candlesticks (2026-04-12)

| Bot | Trend (T) | Area of Value (A) | Trigger (Y) |
|-----|-----------|-------------------|-------------|
| **Trend Rider v2** | 200 MA filter + EMA21 > EMA50 | S/R level + 50 EMA confluence | Hammer or Bullish Engulfing |
| **The Reverter v2** | Range bound (ADX < 20) | Horizontal support OR BB lower band | Bullish Engulfing or Hammer |
| **Squeeze Breaker v2** | BB bandwidth squeeze (<0.03) | Horizontal resistance OR BB upper | Volume surge + MACD bullish |

### Bots Using Original TAY (Different Combinations)

| Bot | Trend (T) | Area of Value (A) | Trigger (Y) |
|-----|-----------|-------------------|-------------|
| **Momentum Hunter** | Price > EMA50 + ADX > 20 | +1% intraday move | Vol > 2x + RSI 50-75 + MACD↑ |
| **Nano Sniper** | EMA hierarchy 8>21>50>200 | Above VWAP | Vol > 1.5x + RSI 45-75 + MACD↑ |
| **Flag Rider** | +2% impulse pole + volume | 5-bar tight consolidation flag | Breakout above flag + VWAP + MACD↑ |
| **Trap Catcher** | Weakening trend (ADX < 30) | RSI reverting from extreme | Vol declining + MACD fading |
| **Volume Whisperer** | OBV trending up + above EMA21 | Above VWAP | Vol > 1.5x + RSI 40-70 + MACD↑ |
| **News Sniper** | >3% intraday move | Vol > 2x institutional | RSI 30-70 + VWAP + MACD agree |
| **Correlation Hunter** | (custom — pair correlation) | Z-score > 2 | Pair divergence reversion |

**Note:** Correlation Hunter uses a custom `should_enter()` because it needs
to look up pair partners. It's the only bot that bypasses the default TAY rule.

---

## 8. Same Indicator, Different Roles

This is the key design insight. The S/R level detection produces a list of
horizontal levels. **Three different bots use those same levels in three
different ways:**

| Bot | What it does with S/R |
|-----|----------------------|
| **Trend Rider** | Uses S/R as a **pullback level in an uptrend** — buy when price retraces to support during a strong uptrend |
| **The Reverter** | Uses S/R as a **bounce level in a range** — buy at support in a non-trending market |
| **Squeeze Breaker** | Uses S/R as a **breakout level** — buy when a Bollinger squeeze breaks above resistance |

This is the diversity that makes the arena meaningful. If one bot wins, we
learn which **role** that S/R level plays in winning trades — not just that
"S/R is good."

---

## 9. Risk Management

### Position Sizing
- $50 max per trade (5% of $1,000 starting balance per bot)
- Max 3 concurrent positions per bot
- Bot daily loss limit: -$30 (-3%)
- Global daily loss limit: -$500 (-5%)
- Global max positions: 20

### Stop Loss Approach
- **Trend Rider v2** uses **ATR-based stops** (1x ATR loss, 2x ATR take profit = 2:1 R:R)
- The other bots use fixed % stops (1-2% loss, 1-3% take profit)
- The arena tests whether ATR stops outperform fixed stops over time

---

## 10. The Dynamic Watchlist

**File:** `overseer/dynamic_watchlist.py`

Refreshes every 2 hours during market hours. Pulls from a 65-symbol scan universe:

**Stocks (50):** AAPL, MSFT, NVDA, GOOGL, META, AMZN, TSLA, AMD, NFLX, AVGO,
ORCL, CRM, ADBE, CSCO, QCOM, INTC, IBM, MU, PANW, PLTR, SPY, QQQ, IWM, DIA,
VTI, ARKK, XLF, XLE, XLK, XLV, JPM, BAC, WMT, JNJ, PG, DIS, HD, V, MA, COIN,
GME, AMC, BB, RIVN, LCID, NIO, MARA, RIOT, SOFI, F

**Crypto (15):** BTC, ETH, SOL, XRP, ADA, DOGE, DOT, AVAX, MATIC, LINK, UNI,
ATOM, LTC, BCH, FIL (all USD pairs via Kraken)

**Ranking formula:** `|change%| × log10(volume_usd)`

The top 20 by score are saved to `arena_watchlist` Supabase table. The arena
reads from this table on every scan. If a stock isn't moving today, it doesn't
make the list.

**No hardcoded "always-on" symbols.** Liquid symbols like SPY/QQQ/BTC will
naturally appear when they're moving and drop out when they're not.

---

## 11. TradingView Auto-Switching

**File:** `overseer/tv_focus.py` + `overseer/tv_switch_symbol.js`

Every 30 minutes, reads the #1 opportunity from the watchlist and switches
the headless Chromium TradingView chart to that symbol via CDP. The Yuri
Auto-Trader Pine strategy then runs on whatever symbol is currently being
focused.

**Tech:** Python wrapper + Node.js helper using the chrome-remote-interface
library that's already installed for the TradingView MCP. Calls
`chart.setSymbol(symbol, {})` via CDP.

---

## 12. The TAY Component Feedback Loop

**File:** `overseer/tay_analytics.py`

This is the data-driven feedback loop. After every closed trade, the
`tay_components` JSONB column on `arena_trades` records which T/A/Y reasons
fired. The analytics script groups trades by component and reports:

- Which trends (T) are winning?
- Which areas of value (A) are winning?
- Which triggers (Y) are winning?

**Example output:**

```
Trend (T) Performance:
  200_ma_filter: 12 trades, 8 wins (66.7%), $+18.50 total

Area of Value (A) Performance:
  horizontal_support: 8 trades, 6 wins (75%), $+22.40 total
  ma_pullback: 4 trades, 1 win (25%), $-3.90 total

Trigger (Y) Performance:
  hammer_candle: 5 trades, 4 wins (80%), $+15.20 total
  engulfing_candle: 3 trades, 1 win (33%), $-2.10 total
```

This runs as part of the daily autopsy at 4:30 PM ET and sends a Telegram
summary. After ~50-100 trades, real data emerges showing which T/A/Y
components actually make money.

**The result:** We can compare the YouTube digest predictions (e.g., "S/R is
the most popular A component") against arena reality (e.g., "S/R is also the
highest-winning A component"). When predictions hold, we double down. When
they don't, we adjust.

---

## 13. File Map

### Indicators & Data
- `shared/indicators.py` — All technical indicator math (RSI, MACD, BB, EMA, VWAP, OBV, ADX, ATR, Z-score, S/R, candlesticks)
- `shared/market_scanner.py` — Pulls market data from Finnhub (stocks) and Kraken (crypto), computes all indicators, reads dynamic watchlist
- `shared/paper_trader.py` — Supabase-backed paper trading with Telegram alerts and TAY component logging
- `shared/base_bot.py` — Abstract base class with the TAY framework

### Bots
- `bots/momentum_hunter.py` — Momentum breakout (TAY v1)
- `bots/the_reverter.py` — Mean reversion with S/R + engulfing (TAY v2)
- `bots/nano_sniper.py` — EMA scalping (TAY v1)
- `bots/trend_rider.py` — Pullback trading with S/R + candlestick + ATR stops (TAY v2)
- `bots/squeeze_breaker.py` — Volatility breakout with horizontal R (TAY v2)
- `bots/flag_rider.py` — Flag pattern (TAY v1)
- `bots/trap_catcher.py` — False breakout reversal (TAY v1)
- `bots/volume_whisperer.py` — VWAP + OBV institutional flow (TAY v1)
- `bots/correlation_hunter.py` — Pairs trading (custom logic, not TAY)
- `bots/news_sniper.py` — News-driven scalping (TAY v1)

### Overseer
- `overseer/dynamic_watchlist.py` — Top-20 movers scanner
- `overseer/tv_focus.py` + `tv_switch_symbol.js` — TradingView auto-switcher
- `overseer/extract_unified_rule.py` — One-time YouTube strategy digester (already run)
- `overseer/tay_analytics.py` — Component performance analyzer
- `overseer/game_plan.py` — Pre-market bot assignments
- `overseer/autopsy.py` — Post-market trade analysis
- `overseer/super_prompt.py` — Weekly improvement focus
- `overseer/restrictions.py` — Auto-pause underperforming bots
- `overseer/analytics.py` — General performance analytics

### Cron Wrappers
- `arena_scan.sh` — Wraps `arena_runner.py --once`
- `watchlist_cron.sh` — Wraps `dynamic_watchlist.py`
- `tv_focus_cron.sh` — Wraps `tv_focus.py`
- `overseer_cron.sh` — Wraps all overseer tools (game_plan, autopsy, super_prompt, etc.)

### Reference Documents
- `STRATEGY_DIGEST.md` — Output of YouTube strategy synthesis (one-time, ~96 strategies → top patterns)
- `STRATEGY_LOG.md` — This file (process history and design rationale)
- `TAY_ANALYTICS.md` — Generated by `tay_analytics.py` (refreshed daily, shows component performance)

### Dashboard
- `data/.openclaw/canvas/bot-arena.html` — Live web dashboard at `https://187-77-193-40.sslip.io/bot-arena.html`

---

## 14. Cron Schedule

| Time (ET) | Job | LLM? |
|-----------|-----|------|
| Every 5 min, 9:30 AM-4 PM Mon-Fri | Arena scan | No |
| Every 2hr during market hours | Dynamic watchlist refresh | No |
| Every 30min during market hours | TradingView focus switch | No |
| 9:00 AM Mon-Fri | Overseer game plan | GX10 `quick` |
| 4:30 PM Mon-Fri | Overseer daily autopsy + TAY analytics | GX10 `quick` |
| Friday 6:00 PM | Overseer weekly super-prompt | GX10 `quick` |

All run as system crontab entries (not OpenClaw agentTurn) to avoid loading
the LLM on the VPS for trivial tasks.

---

## 15. RAM Budget

| Component | RAM |
|-----------|-----|
| OpenClaw container (always on) | ~800MB |
| Headless Chromium TradingView (systemd) | ~500MB |
| Caddy + Tailscale + system | ~400MB |
| Buffer | ~300MB |
| **Available for trading** | ~1.1GB |
| Arena scan peak (during 30-60s run) | ~80MB |
| Watchlist refresh | ~30MB |
| TV focus switcher | ~30MB |
| **Peak total during market hours** | ~2.8GB / 4GB |

---

## 16. Telegram Notifications

- **Watchlist refresh** (every 2hr): Top 20 with change% + score
- **TV focus switch** (every 30min): "Now monitoring SYMBOL"
- **Bot buy** (real-time): "🟢 Bot BOUGHT SYMBOL @ $X — TAY: trend | value | trigger"
- **Bot sell** (real-time): "🔴 Bot SOLD SYMBOL @ $X — P&L: $X (X%) — exit reason"
- **Game plan** (9 AM): Pre-market analysis from overseer
- **Daily autopsy** (4:30 PM): Trade summary + TAY component winners
- **Weekly super-prompt** (Friday 6 PM): Per-bot improvement recommendations

---

## 16c. Human-in-the-Loop Trading Concierge (Added 2026-04-12)

The concierge is a separate Telegram bot (`@YuriTrade24Bot`) that lets Tony
request trade recommendations and execute them with a button tap. This is
**human-in-the-loop** — Tony makes every real-money decision; the overseer
is a research assistant.

### Why Manual Instead of Autonomous?

Tony wanted tighter control over the first live-money experiment. Autonomous
arena bots firing on 5-minute cron cycles is fine for paper trading but feels
risky for real money without more track record. The concierge solves this:
- Every trade requires Tony's explicit button tap
- The TAY framework does the research, Tony does the judgment
- Both modes coexist — autonomous arena stays paper, concierge goes live
- Uses the same `kraken_executor` safety layer so nothing is new below the surface

### The Conversation Flow

```
Tony → /best
Bot  → "🎯 #1 OPPORTUNITY — ETH/USD
        Price: $3,567
        Analysis: [GX10 writeup]
        Entry / Stop / Target
        [Buy $10] [Buy $25] [Buy $50] [Skip]"
Tony → [taps Buy $25]
Bot  → "✅ BOUGHT 0.007 ETH @ $3,567 — Order #ABC"
...
Bot  → "⚠ ETH SELL SIGNAL — P&L +3.2%
        [Sell Now] [Hold 30m]"
Tony → [taps Sell Now]
Bot  → "✅ SOLD @ $3,681 — P&L +$0.79 (+3.2%)"
```

### Architecture

**Three components:**

1. **`concierge/trading_concierge.py`** — systemd service, Telegram long-polling
   - Listens for Tony's messages on the new bot token
   - Routes commands: `/best`, `/positions`, `/balance`, `/history`, `/kill`
   - Handles inline button callbacks
   - Executes trades via `kraken_executor.execute_manual_trade()`

2. **`concierge/advisor.py`** — the analysis engine
   - Fetches live crypto data from Kraken (BTC/ETH/SOL/XRP/ADA/DOGE)
   - Runs `get_tay_components()` for every bot on every symbol
   - Ranks by number of bots agreeing (firing) + partial matches
   - Calls GX10 `quick` model for a human-readable 3-4 sentence analysis
   - Computes ATR-based entry/stop/target levels
   - Returns the #1 opportunity with full context

3. **`concierge/position_watcher.py`** — cron every 5 min
   - Queries open manual positions from Supabase
   - For each, runs `should_exit()` on every bot (multi-bot consensus)
   - If any bot fires an exit signal → sends Telegram alert with Sell/Hold buttons
   - Mute/cooldown logic prevents alert spam

**State store (`concierge/state.py`):**
- SQLite DB `concierge_state.db` tracks pending button actions
- Each button has a unique short callback_data
- When tapped, state is consumed (one-time use)
- Alert cooldowns muted per position for 30 min after "Hold"

### The Three Gates

The concierge introduces a THIRD gate for manual trading:

| KRAKEN_ALLOW | LIVE_TRADING_ENABLED (bots) | MANUAL_TRADING_ENABLED (concierge) | Result |
|---|---|---|---|
| false | * | * | All paper |
| true | false | false | Paper + dry-run Kraken calls |
| true | false | **true** | **Concierge live, bots paper** ← recommended |
| true | true | false | Bots live, concierge paper |
| true | true | true | Both live |

Tony's target state: row 3. Arena bots keep learning (paper), concierge lets
Tony trade with research assistance.

### Manual Trading Limits

| Setting | Value |
|---------|-------|
| Max position per trade | $25 |
| Max total exposure | $50 |
| Daily loss limit | -$10 |
| Button options | $10, $25, $50, Skip |

These are independent of the arena bot limits (bots use $5/$10/-$3). The
kill switch closes both gates instantly.

### New Telegram Bot

A separate bot (`@YuriTrade24Bot`, ID `8799870369`) was created via @BotFather
because OpenClaw's Yuri bot is already polling — two processes can't poll the
same token. Both bots can live in the same chat. Clean separation:
- **Yuri** → general assistant
- **YuriTrader** → trading concierge

### Files Added / Modified (this update)

| File | Purpose |
|------|---------|
| `concierge/__init__.py` | Package marker |
| `concierge/advisor.py` | Trade recommendation engine (TAY + GX10) |
| `concierge/trading_concierge.py` | Telegram long-polling service |
| `concierge/position_watcher.py` | Exit signal monitor (cron) |
| `concierge/state.py` | SQLite state store for button callbacks |
| `shared/kraken_executor.py` | Added `execute_manual_trade()` |
| `config.py` | Added `MANUAL_*` settings |
| `.env` | Added `TELEGRAM_TRADER_BOT_TOKEN` + `MANUAL_*` (defaults OFF) |
| `/etc/systemd/system/trading-concierge.service` | systemd unit |
| `arena_trades` table | Added `manual_trade BOOLEAN` column |

### Test Phases

**Phase A — Advisor standalone:** ✅ PASSED
- `python3 advisor.py --top 1` returns a ranked opportunity
- GX10 produces a sensible analysis
- Correctly detected current market (strong downtrend, 0 bots firing) and said "don't trade"

**Phase B — Telegram outbound:** ⏳ AWAITING CHAT START
- Requires Tony to tap START on `@YuriTrade24Bot`
- After that: concierge service starts, sends boot message, responds to `/help`

**Phase C — Validate-only buy button:** ⏳ Will run after Phase B
- `MANUAL_TRADING_ENABLED=true`, `KRAKEN_ALLOW_TRADING=false`
- Tap "Buy $10" → Kraken dry-run response
- No real order placed

**Phase D — Real $10-25 trade:** ⏳ Will run after Phase C
- Both gates open
- Tap buy → real Kraken order
- Position watcher monitors
- Exit signal → Tony taps Sell → real Kraken sell

### Commands Supported

| Command | Aliases | What it does |
|---------|---------|--------------|
| `/start`, `/help` | `hi`, `?` | Show help |
| `/best` | `best trade`, `recommend` | Run advisor |
| `/positions` | `holding`, `open` | List open manual positions + P&L |
| `/balance` | `status`, `account` | Kraken balance + exposure |
| `/history` | `closed`, `today` | Today's closed manual trades |
| `/kill` | `panic`, `emergency` | Emergency stop |

---

## 16b. Live Trading on Kraken (Added 2026-04-12)

The system now supports **live crypto trading** on Kraken for **one bot**
(The Reverter) with strict guardrails. All other 9 bots remain paper-only.

### Why The Reverter

It's the only crypto-profitable bot in the historical leaderboard:
- 9 closed crypto trades, 6 wins (66.7% win rate), +$0.43 P&L
- Mean reversion strategy fits choppy 24/7 crypto markets
- Conservative by design (only fires at oversold support in range-bound markets)

**Why NOT Correlation Hunter** (the all-time leader at +$3.75):
- All 63 of its trades were on stock pairs (SPY/QQQ/AAPL/MSFT) — never fired on crypto
- Kraken doesn't trade stocks
- Its P&L is irrelevant to the Kraken live test

### Risk Profile

| Constraint | Value |
|-----------|-------|
| Max position size per trade | **$5.00** |
| Max concurrent live positions | **1** |
| Max total live exposure | **$10.00** |
| Daily loss limit (live) | **-$3.00** (auto-disables for the day) |
| Bots eligible for live | **The Reverter only** |
| Asset types eligible | **Crypto only** (Kraken pairs) |
| Default state | **OFF** (`LIVE_TRADING_ENABLED=false` in `.env`) |

### Double-Gate Safety

Two independent gates must BOTH be true for any real order:

1. **`KRAKEN_ALLOW_TRADING=true`** (server-side gate, used by `kraken_executor`)
2. **`LIVE_TRADING_ENABLED=true`** (config gate)

If either is false → trades route to paper. If a bug enables one accidentally,
the other still blocks the order. This mirrors the kraken-mcp design.

### Pre-Trade Checks (in `kraken_executor.execute_arena_trade`)

ALL must pass before a Kraken order is placed:

1. `LIVE_TRADING_ENABLED=true` env var
2. Bot ID is in `LIVE_TRADING_BOTS` env list
3. Symbol is in `KRAKEN_PAIR_MAP` (crypto only)
4. Position size USD ≤ `LIVE_MAX_POSITION_USD` ($5)
5. Computed volume ≥ Kraken `ordermin` for the pair (~$2-5 depending on pair)
6. Total live exposure (open positions × entry price) ≤ `LIVE_MAX_EXPOSURE_USD` ($10)
7. Live daily P&L > `LIVE_DAILY_LOSS_LIMIT` (-$3)
8. Kraken account has sufficient USD balance

If any check fails → fall back to paper trade, log the reason in Telegram.
We always know what happened.

### Files Added / Modified

| File | Purpose |
|------|---------|
| `shared/kraken_executor.py` | NEW — Self-contained Python Kraken client (stdlib only) |
| `shared/paper_trader.py` | MODIFIED — Routes to Kraken when eligible, falls back to paper otherwise |
| `config.py` | MODIFIED — Added LIVE_TRADING_* config |
| `kill_live_trading.sh` | NEW — Emergency kill switch (closes both gates + cancels all orders) |
| `.env` | MODIFIED — Added live trading env vars (defaults OFF) |
| `arena_trades` table | SCHEMA — Added `kraken_order_id`, `fill_price`, `fees_paid` columns |

### Test Phases

**Phase A (Wiring test, no real Kraken calls):** ✅ PASSED
- Run arena with `LIVE_TRADING_ENABLED=false`
- Confirm all 10 bots still paper trade
- Confirm no Kraken calls in the logs

**Phase B (Validate-only, Kraken validates but doesn't place):** ✅ PASSED
- Direct call to `place_market_order(validate=True)` succeeds
- Kraken returns dry-run response with order description
- No real order placed

**Phase C (Real $5 trade):** ⏳ NOT YET RUN
- Requires Tony to fund Kraken with USD (currently $0 USD, only BTC/CAD)
- Set both `KRAKEN_ALLOW_TRADING=true` and `LIVE_TRADING_ENABLED=true`
- Wait for The Reverter to fire on a crypto symbol
- Verify fill, P&L, and exit order all work end-to-end

### Telegram Alerts You'll Receive

| Event | Format |
|-------|--------|
| Live entry | "💰 LIVE: The Reverter BUY ETH/USD @ $3567 — Volume: 0.0014 — Order: ABC123" |
| Live entry (dry-run) | "💰 DRY-RUN: The Reverter BUY ETH/USD @ $3567 — Order: validate-only" |
| Live exit | "💰 The Reverter LIVE CLOSED ETH/USD @ $3611 — P&L: +$0.06 (+1.2%)" |
| Live blocked | "⚠ The Reverter wanted LIVE BUY ETH/USD but blocked: insufficient USD" |
| Live close failed | "⚠ The Reverter LIVE CLOSE FAILED for ETH/USD — MANUAL INTERVENTION REQUIRED" |
| Kill switch fired | "🛑 LIVE TRADING KILLED — manual stop" |

### Kill Switch

If anything goes wrong:

```bash
/docker/openclaw-xrt9/data/skills/trading-arena/kill_live_trading.sh
```

This:
1. Sets `LIVE_TRADING_ENABLED=false` in `.env`
2. Sets `KRAKEN_ALLOW_TRADING=false` in `.env`
3. Calls Kraken `CancelAll` to cancel all open orders
4. Sends Telegram alert

Better to kill and investigate than risk more trades while debugging.

### Honest Risk Assessment

- The Reverter has only **9 historical crypto trades** — too few to be confident in its edge
- **Fees** on $5 trades are 0.5-1% per round trip — bot needs to clear 1.5%+ per trade to be profitable
- **Slippage** on Kraken's smaller crypto pairs (ADA, DOGE) can be wide
- Kraken account is currently **$0 USD** — must be funded before live trading can fire

**Realistic best-case week 1:** Prove the wiring works, $0-2 profit.
**Realistic worst-case week 1:** Lose $3-5 to fees and slippage before the daily limit kicks in.

The point is **not to make money in week 1**. It's to prove the end-to-end
live trading path works so we can graduate more bots once they earn it.

---

## 17. The Iteration Plan

1. **Phase 1 (current):** Run 3 upgraded bots + 7 original TAY bots on paper.
   Collect 50-100 trades per bot. Let TAY analytics show which T/A/Y
   components win.

2. **Phase 2 (after baseline):** Upgrade more bots based on TAY analytics
   findings. If S/R + hammer is winning, more bots should use that combination
   in different ways. If a bot consistently loses, the overseer's restrictions
   tool will auto-pause it.

3. **Phase 3 (data-validated):** Add more YouTube strategies if patterns
   emerge that aren't yet covered. We have the pipeline but don't need more
   videos right now — 96 is plenty.

4. **Phase 4 (live trading):** Once a bot or combination shows >55% win rate
   and positive expectancy over 100+ trades, graduate it to live trading
   through Questrade (stocks) or Kraken (crypto). Start with $9-50 per trade.

---

## 18. Why This Matters

Most algorithmic trading bots fail because:
1. They're built on a single backtested idea that breaks in live markets
2. They don't have a way to learn from real performance
3. They can't compete different ideas against each other
4. They use too many parameters and overfit to historical data

This system avoids those failure modes by:
1. **Diverse strategies** — 10 bots testing different combinations, not one mega-bot
2. **Real-time feedback** — TAY analytics shows what works after every closed trade
3. **Discipline enforced by structure** — TAY framework requires confluence, not single signals
4. **Tied to liquid moving symbols** — dynamic watchlist focuses on actual opportunities

The expected outcome is NOT that all 10 bots make money. The expected outcome
is that **2-3 bots find an edge** and the others get pruned or upgraded based
on the data. That's how the system improves over time.
