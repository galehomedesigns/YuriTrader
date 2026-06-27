# opening-sim-2026-06-26

**Purpose:** a perfect-execution simulation of the **2026-06-26 opening session** — what the
Opening-Power strategy would have made on each symbol that passed the 9:32 two-minute gate, had
every order executed cleanly (no DOM/staging failures). Shows buy-in, exact stop-loss movements,
exit, realized P&L, and the theoretical max.

**Audience:** Tony — post-mortem of the morning's missed/managed trades.

**Data:** `logs/opening_sim_2026-06-26.json`, written by
`opening_agent/sim_opening_2026-06-26.py`. That replay takes the engine's **actual armed entries/stops
from `advisory_monitor.log`** (ground truth) and drives the real `OpeningEngine` over the live
TradingView 2-min feed (stitched from `logs/session_replay_2026-06-26/` snapshots, 9:30→~12:52 ET).
Validated against the live log's stop-moves (WSE 11.62/11.65/11.72, EQX 9.86, PLTR 110.29 reproduce
exactly). Sizing = live $200/slot; Questrade commission-free; spread/slippage not modelled.

**One-off / not cron'd.** Single historical session. Regenerate with:
`python3 opening_agent/sim_opening_2026-06-26.py && python3 dashboards/opening-sim-2026-06-26_update.py`.
Output: `~/openclaw/canvas/opening-sim-2026-06-26.html`.
