# opening-backtest-compound

**What it shows:** the Opening-Power gap strategy as a **compounding equity
curve** over the most recent 4 weeks — start with the live budget ($1,000) and
reinvest each day's profit into the next day's trading, so daily returns compound.

**Who reads it:** the trader, to see what reinvesting profit (rather than a fixed
slot every day) would have done over the recent stretch.

**Data it pulls:** `logs/opening_backtest_summary.json` (written by
`opening_agent/backtest_full.py`) — read-only, no re-run. Uses each trade's
`flatten_pct` (the live EOD mode) grouped by day, last 4 weeks.

**Sizing model (A — live-faithful):** each day budget = current balance, split
into `OPENING_MAX_TRADES` fixed slots of balance/max_trades; one slot per matched
trade, unused slots sit in cash (0% that day). Day return on balance =
`Σ(trade %) / max_trades`; balance compounds. This mirrors the live auto-stage
(`budget/max_trades`), **not** full deployment.

**Knobs (env):** `OPENING_COMPOUND_WEEKS` (4), `OPENING_TRADE_BUDGET_USD` (1000),
`OPENING_MAX_TRADES` (5).

**Honest limits:** the 4-week window is the back-loaded/strong stretch of the
2-month sample, so it flatters the strategy; pre-market scan selection still only
partially modelled; sim returns ≠ future results. Output: `canvas/opening-backtest-compound.html`.
