# trading

Tony's daily trading intelligence — live market snapshots, active alerts, auto-trade status, news & sentiment — all for the Questrade-side equities watchlist.

**Audience:** Tony — quick glance before/during market hours to see price moves, news impact, alerts, and any open auto-trade positions.

**Refresh cadence:** every 30 minutes (cron). Light Supabase queries, no external API calls.

**Data sources** (all Supabase):
- `market_snapshots` — deduped to latest per symbol.
- `price_alerts` where `enabled=true`.
- `trend_signals` — deduped to latest signal per symbol (BULLISH/BEARISH/NEUTRAL).
- `news_events` — last 15 fetched.
- `social_signals` where `market_relevant=true` — last 15 fetched.
- `trading_config.watchlist` — the list of tracked symbols.
- `auto_trades` — open + closed-today positions, aggregate daily P&L.
- `trading_rules.risk_limits` — `auto_trading_paused` flag.

**Output:** `~/openclaw/canvas/trading.html`.

**Supersedes:** `skills/trading/scripts/dashboard_gen.py` (the legacy generator's per-row HTML string-concat approach). The wrapper `trading_dashboard_cron.sh` in crontab is a no-op on GX10 — it was written to `docker exec` into the (now gone) OpenClaw container. Safe to remove once this migration is verified.
