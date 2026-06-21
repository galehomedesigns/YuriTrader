# bot-arena-stocks dashboard

**Shows:** the 12 trading-arena bots run on the 60-day stock OPENING window
(gap-qualified universe + 9:30→cutoff), $1,000 each, same live config — plus the
indicator edge search and filter combinations on the same gap-qualified setups.

Three panels:
1. **Bot survivorship** — each bot ($1,000 start, min(5%,$50)/trade, compounding),
   variant A (native exits) vs B (entry + standard opening stop/cutoff): ending
   balance, return, win%, max drawdown, daily-loss-limit days, survived flag.
2. **Indicator edge** — which opening-bar indicator predicts the 20-min outcome
   among gap-qualified setups, with in-sample vs out-of-sample lift. `robust` =
   beats baseline in BOTH halves (the honest bar).
3. **Filter combinations** — stacked filters, IS vs OOS.

**Who reads it:** the operator, to judge whether the bots or any indicator add
edge on top of the proven pre-market gap selection.

**Data:** `logs/bot_arena_stocks_summary.json`, `logs/indicator_edge_summary.json`,
`logs/combo_edge_summary.json` — written by `opening_agent/bot_arena_stocks.py`,
`indicator_edge.py`, `_phase3_combos.py`. Read-only; cached candles; no network.

**Honest limits (shown on the banner):** ~2 months / ONE regime — the baseline
itself is IS-negative / OOS-positive, so most apparent edges just ride the
favorable second half. Pre-market volume + ranking proxied. Suggestive, not proven.

**Regenerate:** `bot-arena-stocks_cron.sh` → `bot-arena-stocks_update.py` →
`canvas/bot-arena-stocks.html`. Re-run the analyses with the three scripts above.
