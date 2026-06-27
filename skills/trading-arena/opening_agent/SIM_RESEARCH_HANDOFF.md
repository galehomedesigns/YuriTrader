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

## 3b. The REAL whole-market picks (Telegram tab) — THE DECISIVE RESULT
`sim_telegram.py` parses the live scan's actual Telegram funnel (`logs/opening_scan_cron.log`) and
replays it. We **pulled the real picks' 2-min bars from IBKR** (`ibkr_history/backfill.py` →
`logs/telegram_cache/`, 219 symbols, gateway was up) so coverage is now ~100% (489/491), not 7%.

**Result over 12 scan-days (6/11–6/26), 491 real picks, 202 passed the 2-min test:**
- Compounded $1000/5 (first-5-by-arm): **$939 — −6.1%.** The strategy **LOSES on the real
  small-cap funnel** — vs the 231-large-cap proxy's +17.6%. **The proxy badly overstated; the real
  picks lose.** Win rate 25.7%.
- **The 2-minute gate IS strongly validated:** picks that passed it averaged **−0.43%/trade** vs
  **−3.23%** for the ones that failed (traded anyway). The gate avoids the disasters (−3.2% losers),
  but even gated, the small-cap gappers bleed.

**Why proxy(+17.6%) ≠ real(−6.1%):** the live funnel is small-cap gappers with violent, mean-reverting
opens that whipsaw a breakout strategy; the 231-name proxy is liquid large/mid-caps with orderly opens.
**The strategy works on liquid names, not on the small-cap gappers the live scan actually picks.** That's
the biggest finding of the whole thread. Tools: `sim_telegram.py`, `sim_telegram_analysis.py`. Tab:
"📨 Telegram (real picks)". Forward capture still matters (only 12 days / 202 trades so far).

## 3c. Price floor + market-regime gate (THE FIX FOR THE REAL PICKS)
Two levers tested on the real Telegram picks (45-min config), compounded $1000/5, in `sim_telegram.py`
→ tabs **"💧 $15 floor"** and **"🌊 $15 + regime gate"**:

| Scenario (real picks, 45-min) | Baseline | New-sim |
|---|---|---|
| raw (no floor) | — | **−6.1%** |
| **≥$15 floor** (+$300 cap) | −3.9% | −1.8% |
| **≥$15 floor + SPY regime gate** | −1.5% | **+1.3%** ← first positive on real picks |

- **Regime gate** = only trade a day when the **SPY** tape is up over the first 14 min (9:30→9:44 return
  &gt; 0) — info known by **arm time** (~9:44), so it's live-implementable. Of 12 scan-days, **7 qualified**.
  Tested SPY/QQQ/IWM as the proxy; SPY won (sweet +1.34% vs IWM +1.26% vs QQQ +0.31%) — code auto-picks
  the best and labels it.
- **Together** the price floor (drop sub-$15 illiquid gappers) + regime gate (skip down-tape days) flip the
  real small-cap funnel from a −6.1% loser to a small new-sim **winner**. Each lever alone is not enough;
  the combination is what crosses zero. Still a **small** edge over 12 days / 32 trades — directional, not
  validated; the regime gate is the long-sought flat-period fix but needs forward data to confirm.

## 3d. Arm-gate swap on baseline (isolates the entry lever) — tabs 🔁 / 🟢
Keep the **baseline exit** (push-trail, no target) and change ONLY the arm gate, current config
(gap 0.5–4 / 30-min), 61-day proxy window, compounded $1000/5:

PRICE FLOOR: the proxy backtest now applies the **live $5 floor** ($5–$300, matches `OPENING_MIN_PRICE`
default) — `MIN_PRICE` in both `sim_variant_ibkr_days.py` and `sim_opening_variant.py`. Removing the 17
sub-$5 penny names (CAN $0.34, WKSP, AMC…) lifted the sim variants (they were net drags). The $15 floor
in §3c is **research-only on the real Telegram picks — NOT adopted; live stays $5.**

| Arm gate (baseline push-trail exit, $5–$300) | **Recent month** (5/22–6/24) | Full 61d (3/24–6/18) |
|---|---|---|
| baseline TIGHT arm (as-is) | +6.5% | +4.3% |
| **sim arm** (power bar + close>SMA200, no TIGHT) | **+10.8%** | **+7.7%** |
| first green bar (arm only if 09:30 closes up, any size) | +2.8% ⚠ | +8.5% |
| *new-sim (sim arm + 3R target exit), reference* | +13.0% | +10.2% |

**Two windows tell different stories — robustness matters.** The **sim arm gate** (power bar + >200-SMA)
beats baseline in BOTH windows (+1.7 / +3.6 pts) — the durable arm-gate improvement. The **"first green
bar"** gate (any green 09:30 bar, no size/SMA filter) is **NOT robust**: it wins big over 61d (+7.4 vs
+2.9) only because the flat Mar–Apr first half punished TIGHT's selectivity; over a normal trending month
it over-trades junk and **loses** to baseline (+4.0 vs +8.0). The 61-day-only read overstated it — exactly
the regime-dependence caveat in §4. New-sim's edge is the **3R exit** layered on the sim arm gate (best in
both). Modes in `sim_one`: `base_simarm`, `firstgreen`; JSON blocks `armgate` (full 61d) + `armgate_recent`
(month). Dashboard: 🚀 Summary (improved arm) shows base_simarm over the month; 🔁/🟢 show the full window.

## 3e. Loss-minimization study (2026-06-27) — exit cuts DON'T work, one filter does
Exhaustive search for ways to cut the downside (`loss_min.py`, `loss_flags.py`, `loss_regime.py`;
new-sim, both windows). **Headline: you cannot cut losses without cutting profit more** — winners and
losers are nearly indistinguishable at entry; the +EV needs the volatile movers to run to 3R.
- **Loser profile:** ~81% of losers go GREEN (avg +0.66% peak) then round-trip to −0.67%; breakeven
  never engages because +1R is a ~3% move vs ~0.66% peaks.
- **Exit cuts tested** (BE@+0.3–1.0% abs, BE@0.33–0.5R, MAE caps 1.5/2%, no-progress 3/5-bar,
  lock-profit): every one shrinks the loss pool but shrinks the compound MORE. None beat current.
  "Lock +0.2%" hits 63–69% win but lower compound (scratches would-be winners).
- **Selection flags** (late arm, gap size, risk width, extension, location): the "obvious" cuts —
  wide-risk (flagAvg +1.2/+2.1%, win 63–75%), already-extended, big-gap — are the **WINNERS**; cutting
  them craters compound. Helpers gap<1.5%/risk<1.5% **flip between windows = overfit.**
- **Day-level regime gate** (skip down-tape days, SPY/QQQ/IWM): **hurts the proxy** (−1.6 to −7.4) —
  it was a small-cap-funnel fix, not a liquid-proxy lever.
- **✅ The one robust winner — "above-both" arm filter:** require the arm bar to close **> 20-SMA AND
  > 200-SMA** (new-sim currently only checks 200). Removes the one consistently-negative subset
  (sub-20-SMA entries, ~20–24% win). **Full +10.2%→+12.7% (+2.5pts), recent +13.0%→+13.0% (unchanged) —
  NEVER hurts.** Implemented as `sim_one(req20=True)` (inert by default). Shown in the 🛡 Loss filter
  tab; **NOT adopted into the baseline** (user chose comparison-tab only).

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
2. **Market-regime gate** — ✅ DONE (see §3c): SPY 9:30→9:44 up-gate + $15 floor flips the real picks
   to +1.3% (new-sim). Small but the first positive config on real data. Confirm on forward capture.
3. **Decision**: whether to adopt gap-2–4 / 45-min as the live config, or wait for forward data.
   It is NOT live-armed; nothing changed in the live path beyond the committed DOM fix.
