# kraken

Live Kraken spot-trading state + live-trading gate status. One pane of glass for "am I actively live on Kraken right now, and what's positioned / what just closed?"

**Audience:** Tony — quick glance before opening Telegram, decision support for kill-switch timing.

**Refresh cadence:** every 30 minutes (cron). One Kraken private API call per refresh to pull USD balance; all other data is served from Supabase (cheap + fast).

**Data sources:**
- `KrakenExecutor.get_usd_balance()` — live Kraken USD balance.
- Supabase `arena_trades` filtered to `paper=eq.false` — live-trade history (open + closed today).
- `~/openclaw/.env` — reads `KRAKEN_ALLOW_TRADING`, `LIVE_TRADING_ENABLED`, `LIVE_TRADING_BOTS`, `MANUAL_MAX_EXPOSURE_USD`, `MANUAL_DAILY_LOSS_LIMIT`.
- `~/openclaw/skills/trading-arena/concierge_state.db` — pending Telegram callback count (indicates outstanding human-in-the-loop decisions).

**Output:** `~/openclaw/canvas/kraken.html`.

**What it highlights:**
- Whether the two gates (`KRAKEN_ALLOW_TRADING`, `LIVE_TRADING_ENABLED`) currently permit live orders.
- Which bots in `LIVE_TRADING_BOTS` can route to Kraken (the rest stay paper even with gates open).
- Manual exposure + daily loss vs. their configured caps.
- Open live positions and today's closed live trades, side by side with P&L.

**What it deliberately doesn't do:**
- Place orders — this is read-only. Trading commands go through the `@YuriTrade24Bot` Telegram channel (`trading-concierge` service).
- Pull Kraken `get_open_orders()` / `get_closed_orders()` every refresh — we trust `arena_trades` with `paper=false` as the write-path audit log (the `KrakenExecutor.execute_*_trade` methods write there).
