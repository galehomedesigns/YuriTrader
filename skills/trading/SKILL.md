---
name: trading
description: Stock trading intelligence — market data storage, news/social scanning, trend analysis, price alerts, and dashboard generation. Triggers on requests about market monitoring, trading alerts, stock trends, news scanning, Truth Social, or the trading dashboard.
---

# Trading Intelligence Skill

Multi-script skill for the stock trading intelligence pipeline. Provides market data storage, news/social media scanning, trend analysis, alert management, and dashboard generation.

## Scripts

```bash
# Market data — snapshots, alerts, watchlist
python3 {baseDir}/scripts/market_data.py snapshot              # Store current portfolio snapshot in Supabase
python3 {baseDir}/scripts/market_data.py check-alerts           # Check price alert thresholds
python3 {baseDir}/scripts/market_data.py watchlist              # Show watchlist symbols
python3 {baseDir}/scripts/market_data.py add-watch TSLA         # Add symbol to watchlist
python3 {baseDir}/scripts/market_data.py remove-watch TSLA      # Remove from watchlist
python3 {baseDir}/scripts/market_data.py set-alert ENB.TO above 60.00  # Create price alert
python3 {baseDir}/scripts/market_data.py history ENB.TO 30      # Historical snapshots (N days)

# News scanner — financial RSS feeds
python3 {baseDir}/scripts/news_scanner.py fetch                 # Fetch new articles from all RSS sources
python3 {baseDir}/scripts/news_scanner.py sources               # List configured news sources

# Social scanner — Truth Social + political news
python3 {baseDir}/scripts/social_scanner.py truth-social        # Fetch recent Trump posts
python3 {baseDir}/scripts/social_scanner.py news-headlines      # Fetch political/policy headlines
python3 {baseDir}/scripts/social_scanner.py check-new           # Only items since last check

# Alert engine — threshold checking + notifications
python3 {baseDir}/scripts/alert_engine.py check                 # Run all alert checks
python3 {baseDir}/scripts/alert_engine.py summary               # Current alert status

# Auto trader — autonomous day-trading
python3 {baseDir}/scripts/auto_trader.py evaluate               # Full buy + sell evaluation
python3 {baseDir}/scripts/auto_trader.py sell-check             # Quick sell/cover check (5 min)
python3 {baseDir}/scripts/auto_trader.py positions              # Open auto-trade positions
python3 {baseDir}/scripts/auto_trader.py history 7              # Closed trades (last N days)
python3 {baseDir}/scripts/auto_trader.py pause "reason"         # Pause auto-trading
python3 {baseDir}/scripts/auto_trader.py resume                 # Resume auto-trading
python3 {baseDir}/scripts/auto_trader.py status                 # System status

# Dashboard
python3 {baseDir}/scripts/dashboard_gen.py generate             # Regenerate trading.html
```

## Supabase Tables

- `market_snapshots` — Price snapshots for trend analysis
- `price_alerts` — Custom alert thresholds set by Tony
- `news_events` — Financial news from RSS feeds
- `social_signals` — Truth Social + political signals
- `trend_signals` — Per-symbol trend signals (BULLISH/BEARISH/NEUTRAL) + RSI-14
- `trading_config` — Watchlist, thresholds, source config
- `auto_trades` — Auto-trade positions (open and closed)
- `trade_audit` — Full audit log of every auto-trade action
- `trading_rules` — Configurable buy/sell/short flags and risk limits

## Environment Variables

- `SUPABASE_URL`, `SUPABASE_SERVICE_KEY` — Database access
- `QUESTRADE_REFRESH_TOKEN` — Brokerage API auth
- `FIRECRAWL_API_KEY` — Web scraping for Truth Social
