# opening-backtest dashboard

**Shows:** the full-strategy backtest of the Opening Power agent over the
deepest 2-min window Questrade serves (~60 days). Drives the REAL `OpeningEngine`
bar-by-bar so it scores the complete exit logic, and compares three exit
variants side by side:

- **naive** — entry → initial stop or cutoff close (the `paper_tracker.py` model)
- **flatten** — full engine, hard market-flatten at the cutoff
- **ride** — full engine, ride breakeven-protected winners past the cutoff

**Who reads it:** the operator, to judge whether the trailing/add/ride logic
adds or costs money vs. the naive model — with eyes open about what the test
can and cannot prove.

**Data source:** `logs/opening_backtest_summary.json`, written by
`opening_agent/backtest_full.py`. Read-only; independent of the live system.

**Honest limits (shown on the dashboard, not hidden):**
- The daily pre-market SCAN can't be reconstructed offline, so the rule runs
  over a fixed broad universe each session — this tests the entry/exit RULES +
  classifier gate, NOT the scan's selection/ranking edge or the news nudge.
- Window is ~60 days (2 months), not 3: Questrade caps 2-min intraday history
  at ~60–65 days. Older 2-min data isn't available from this source.
- Fills modelled at the engine's trigger prices ± slippage; the live one-click
  manual confirm latency isn't captured.

**Regenerate:** `opening-backtest_cron.sh` (sources `.env`, runs
`opening-backtest_update.py` → writes `canvas/opening-backtest.html`).
Re-run the backtest itself with `python3 opening_agent/backtest_full.py`.
