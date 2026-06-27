# Opening-Power simulation research — handoff (2026-06-26 → 27)

Pick-up doc for the strategy-research thread. Everything here is **research, signal-only,
NOT live-armed.** Validation is in-sample on a proxy universe — directional, not a promise.

## 1. What got done first (committed)
- **DOM stop-loss bug FIXED** (`7ea5081`) — the `STOP-LOSS SECTION NOT FOUND` failure that
  broke staging on 2026-06-22 & 26. Switch-anchored finder + verify + neutralize-on-fail
  (no-naked-long). Dry-tested 3/3 on the morning replay. See `SESSION_POSTMORTEM_2026-06-26.md`.
- **First completed live fill** 2026-06-26 (WSE +1.02%); manual-send confirmed (no auto-send).

## 2. The strategy comparison (the meat)
Three rule sets, all on the IBKR broad 231-name cache (`logs/backtest_cache_ibkr_broad/`),
gap-funnel candidates, stocks ≤$300, compounded $1000 / 5 equal slots / first-5-by-arm / daily.

- **BASELINE** = live rules: TIGHT gate on, location by open, wick stop, breakeven@1R +
  **push-trail**, 30-min sell-off.
- **NEW-SIM (sweet-spot)** = TIGHT off, location by close (>200-SMA), wick stop, breakeven@1R,
  **fixed 3R target** (no trail), 30-min.
- **LIVE-ENGINE** = the real OpeningEngine: half-fill + **G9 ADD** (scale up) + native push-ratchet.

### Key results (61-day populated window, 2026-03-24..06-18 unless noted)
| Config | Compounded $1000 → | Note |
|---|---|---|
| Baseline (current 0.5–4 / 30-min) | $1,029 (+2.9%) | |
| New-sim (current 0.5–4 / 30-min) | $1,082 (+8.2%) | |
| **Baseline + 45-min config (gap 2–4 / 45-min)** | **$1,084 (+8.4%)** | +5.5 pts |
| **New-sim + 45-min config (gap 2–4 / 45-min)** | **$1,176 (+17.6%)** | +9.4 pts ← best |
| Live-engine (add + push), 2-min | +2.7% | worst — add buys high, trail cuts winners |
| 5-min bars (resampled) | +2–4% all | timeframe washes out the edge |

### The improved "45min setup" (the keeper)
**gap 2–4% · 3R · 45-min hold · wick stop · loc-by-close.** Beats the current config on both
baseline and new-sim over the broad window. Levers that mattered (from `scenario_gaps.py` sweep):
1. **Gap 2–4%** (mid gaps; drop tiny <2% and big >4%).
2. **Hold 45–60 min** (not 30) — biggest single lever.
3. R target 2–3R ≈ equal.

## 3. Dead ends (tested, don't re-chase)
- **Live-engine add + push-trail** → net negative vs new-sim. More machinery = worse.
- **5-minute bars** → wash out the edge; 2-min is materially better.
- **Relative-volume filter** (`rvol_test.py`) → lifts the flat half slightly (−0.5%→+1.3% at RVOL≥1)
  but costs more in trends; **net worse**. Not a fix.
- **Original variant** (gap 1–6 / 1.5%-cap stop / 2R / 20-min) → loses out-of-sample (−52%/60d).

## 3b. The REAL whole-market picks (Telegram tab) — the decisive reality check
`sim_telegram.py` parses the live scan's actual Telegram funnel from `logs/opening_scan_cron.log`
and replays the picks we have bars for. Result over **12 scan-days (6/11–6/26), 491 real picks**:
only **34 are replayable** (~7% — the rest are small-caps like BMNR/KEEL/HIMS/BTQ never captured),
and that slice nets **−0.11% (flat)**. **So the proxy's +17.6% does NOT transfer to the real picks.**
Shown in the dashboard's "📨 Telegram (real picks)" tab. Bottom line: the 231-cache analysis is a
proxy; the real funnel trades different names we can't yet verify. Forward capture is the only fix.

## 4. The honest caveat (READ THIS)
- **Regime-dependent.** Profit concentrates in trending stretches; the *first half* of the window
  was flat-to-negative for **every** config. No gap/stop/target/RVOL knob fixed it.
- **Most recent ~3 weeks**: the 45-min edge narrows to ~even with current (the same flat-period issue).
- **6-month $10k projection** (compounded, $2,000/slot): new-sim ~$10,400–$11,700 (+4% to +17%),
  baseline ~$9,450–$10,590. Best case ≈ +$1,700; before slippage/fills/live-ranker differences.

## 5. Dashboards (canvas/, served at gx10-087b…:8443, auth tony/decades2026)
- `opening-sim-variant.html` — **main one.** Tabs: 📈 Summary (compounded) · ⚡ 45min setup ·
  per-day baseline-vs-new-sim candle splits (SMA20/200, gap, location, entry/stop/3R).
- `opening-sim-liveengine.html` — 3-way (baseline/new-sim/live-engine), 2-min.
- `opening-sim-5min.html` — 3-way, 5-min (resampled — flagged).
- `opening-sim-improved.html` — improved config ± RVOL filter.
- `opening-sim-2026-06-26.html` — single-day candle, the actual 6/26 session.

## 6. Tools (opening_agent/)
sims: `sim_opening_2026-06-26.py`, `sim_opening_variant.py`, `sim_variant_ibkr_days.py`,
`sim_multi.py`, `sim_improved.py` · sweeps: `sweep_variant.py`, `scenario_gaps.py`, `rvol_test.py`,
`backtest_variant_ab.py` · capture: `session_capture.py` (cron 7:28 MDT = 9:28 ET weekdays,
self-stops 12:00 ET → `logs/session_replay_<date>/`). Regenerate the variant dashboard:
`python3 opening_agent/sim_opening_variant.py && python3 opening_agent/sim_variant_ibkr_days.py && python3 dashboards/opening-sim-variant_update.py`

## 7. Next session — where to start
1. **Forward-capture validation** — the daily 2-min capture cron is live; in ~2–3 weeks real
   funnel data accrues. Validate the 45-min config on THAT (live ranker + real names), not the proxy.
2. **Market-regime gate** — the one unexplored lever for the flat period: only trade when the
   broad tape / index is actually trending at the open. Most likely fix for the dead stretches.
3. **Decision**: whether to adopt gap-2–4 / 45-min as the live config, or wait for forward data.
   It is NOT live-armed; nothing changed in the live path beyond the committed DOM fix.
