"""Opening Power trading agent — US-equity opening-range candlestick strategy.

Spec: ../../../TRADING_AGENT.md (the "open candlestick agent"). 2-minute opening
range, 20/200 SMA tight/wide states, elephant & tail power bars, location, push
exits. signal_only by default — never arms orders without explicit config.

Modules:
  classifier  — deterministic bar/state math (TRADING_AGENT.md §7.1)
  (universe, ranker, delivery, engine added in later build steps)
"""
