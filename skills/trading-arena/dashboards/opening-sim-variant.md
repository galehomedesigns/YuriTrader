# opening-sim-variant

**Purpose:** multi-day, tabbed simulation of the Opening-Power **variant rule set** (the
"sweet-spot" config found by the parameter sweep), with a per-day **sweet-spot vs baseline**
A/B. Each tab is one session: candlestick charts with SMA20/200, pre-market gap, location,
and entry/stop/3R-target lines, plus a summary table (gap, location, placed $, P/L $, P/L %).

**Sweet-spot rules:** TIGHT gate off at the open · location by close (>200-SMA) · **wick stop**
(one-bar low, no cap) · **3R take-profit** · breakeven at 1R · **30-min sell-off** · gap **0.5–4%**.
Baseline = live-style rules (TIGHT on, location by open, wick stop, breakeven + push-trail, 30-min).

**Audience:** Tony — strategy R&D / parameter tuning.

**Data:** `logs/opening_sim_variant.json`, written by two generators:
- `opening_agent/sim_opening_variant.py` — the live TradingView capture days (auto-discovers
  `logs/session_replay_<date>/`; currently 2026-06-26). Daily capture cron accrues more.
- `opening_agent/sim_variant_ibkr_days.py` — the last N days from the IBKR broad 231-name cache
  (`logs/backtest_cache_ibkr_broad/`), merged in as additional tabs.

The sweet-spot config came from `opening_agent/sweep_variant.py` (grid search) and was validated
with `opening_agent/backtest_variant_ab.py` (60-day A/B: sweet-spot +0.079%/trade vs baseline
+0.061%). **One-off research dashboard, not cron'd.** Regenerate:
`python3 opening_agent/sim_opening_variant.py && python3 opening_agent/sim_variant_ibkr_days.py && python3 dashboards/opening-sim-variant_update.py`.
Output: `~/openclaw/canvas/opening-sim-variant.html`.
