# bot-arena

Per-bot performance + strategy definitions for the 10-bot paper-trading arena.

**Audience:** Tony — daily check of how each bot is doing, strategy recap.
**Refresh cadence:** every 30 minutes during market hours (cron).
**Data sources:**
- Supabase `arena_balances` — leaderboard per bot (balance, win rate, total PnL).
- Supabase `arena_trades` — closed-trade history (drives cumulative P&L line charts).
- `~/openclaw/skills/trading-arena/bots/*.py` — module docstrings for strategy description + entry / exit rules.

**Output:** `~/openclaw/canvas/bot-arena.html` (served via `dashboards.service` on :8090, proxied through Caddy basic_auth on :8091, fronted by Tailscale Funnel on :8443).

**What the chart shows:** cumulative realized P&L across the last 50 closed trades per bot, in chronological order. Positive slope = winning; flat = no recent closed trades; negative slope = drawdown.

**Definition cards** underneath are authored-by-code: they parse each bot module's top-of-file docstring. To change what shows up, edit the bot's docstring — keep the shape `Bot Name — Label.\n\nOne-line description.\nEntry: …\nExit: …` and this dashboard picks it up on next run.
