---
name: trading-arena
description: 10 parallel trading bots competing with different strategies. Paper trading on stocks + crypto. Use when asked about bot arena, trading strategies, or leaderboard.
---

# Trading Arena

10 bots, 10 strategies, one leaderboard. Paper trading to find the best approach.

## Commands

```bash
python3 /data/skills/trading-arena/arena_runner.py --once         # Run one scan cycle
python3 /data/skills/trading-arena/arena_runner.py --leaderboard  # Show P&L rankings
python3 /data/skills/trading-arena/arena_runner.py --status       # Show bot statuses
python3 /data/skills/trading-arena/arena_runner.py               # Continuous loop
```

## The 10 Bots

| Bot | Strategy | Focus |
|-----|----------|-------|
| Momentum Hunter | Momentum Breakout | Volume surges + bullish signals |
| The Reverter | Mean Reversion | Oversold/overbought reversals |
| Nano Sniper | EMA Scalping | Tiny profits, high frequency |
| Trend Rider | Pullback Following | Buy dips in uptrends |
| Squeeze Breaker | Bollinger Squeeze | Low-vol breakouts |
| Flag Rider | Flag Patterns | Bull/bear flag breakouts |
| Trap Catcher | False Breakouts | Contrarian reversal plays |
| Volume Whisperer | VWAP + OBV | Institutional volume signals |
| Correlation Hunter | Pairs Trading | BTC/ETH, SPY/QQQ spread |
| News Sniper | Sentiment Scalping | News-driven moves |

## Database Tables

- `arena_trades` — All paper trades (open + closed)
- `arena_signals` — Every signal generated
- `arena_balances` — Per-bot balance and stats
