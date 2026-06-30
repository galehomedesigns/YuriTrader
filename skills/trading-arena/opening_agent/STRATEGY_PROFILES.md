# Opening-Power strategy profiles (pre-market toggle)

One env var — `OPENING_STRATEGY_PROFILE` — picks which strategy the day's scan +
monitor run. Set it **before the open**; the cron scan (≥5:00 MDT) and the 7:32
MDT monitor each read it at startup. `baseline` is the current live behaviour,
left untouched. Defined in [profiles.py](profiles.py).

## How to switch

```bash
# show the active profile and what each one sets
python3 skills/trading-arena/opening_agent/profiles.py show

# switch (edits only the OPENING_STRATEGY_PROFILE line in .env)
python3 skills/trading-arena/opening_agent/profiles.py set sweet45
python3 skills/trading-arena/opening_agent/profiles.py set baseline
```

…or just tell me "run sweet45 today" pre-market and I'll set it. The change
takes effect on the **next** scan/monitor start, so set it before the pre-market
scan runs. Nothing else changes — the manual Send-Order confirm, budget, and
all safety behaviour are identical across profiles.

## The three profiles

| | baseline (live) | sweet30 | sweet45 |
|---|---|---|---|
| Arm gate | TIGHT/coil **on** | TIGHT **off** | TIGHT **off** |
| Location | by **open** vs SMA20/200 band | by **close** > 200-SMA | by **close** > 200-SMA |
| Upside exit | **push-trail** (ratchet, no fixed target) | **fixed 3R** target | **fixed 3R** target |
| Entry size | half + add-to-full (G9) | **full slot** at once | **full slot** at once |
| Gap band | 1–25% | **0.5–4%** | **2–4%** |
| Time-stop | 30 min | 30 min | **45 min** |
| Common to all | wick stop (one-bar low) · breakeven at 1R · long-only · $5–$300 · manual Send-Order confirm | | |

## sweet30 vs sweet45 — the only differences

Both are the same "sweet-spot" ruleset (TIGHT off · loc by close · wick stop · 3R
target · full entry). They differ in **exactly two** knobs:

- **Gap band:** sweet45 is **2–4%** (drops the smallest gappers, 0.5–2%); sweet30 is **0.5–4%**.
- **Time-stop:** sweet45 holds **45 min** (flatten ~10:15 ET); sweet30 holds **30 min** (~10:00 ET).

In the 61-day backtest A/B (compounded $1,000 / 5 slots, 2026-03-24…06-18):

| | end | total |
|---|---|---|
| sweet30 (current sweet-spot) | $1,101.80 | **+10.18%** |
| **sweet45** | **$1,190.98** | **+19.10%** (+8.9 pts) |

So sweet45 was the stronger of the two over that window — tighter gaps + a longer
hold captured more of the move. (Source: `compound45` block in the
opening-sim-variant dashboard. Honest limits: 61-day IBKR/TV backtest, grid-found,
doesn't reconstruct the live news nudge — treat the exact % as optimistic, the
ordering as the signal.)

## How it works under the hood

`profiles.apply_to_env()` is called by `run_opening_scan.py` and
`advisory_monitor.py` immediately after they load `.env` and **before** they
import the classifier/engine, so every import-time default sees the profile. The
bundle sets these knobs (all default to baseline behaviour when unset):

| knob | baseline | sweet | read by |
|---|---|---|---|
| `OPENING_REQUIRE_TIGHT` | true | false | classifier |
| `OPENING_LOC_MODE` | open_band | close_slow | classifier |
| `OPENING_EXIT_MODE` | push_trail | target_3r | engine |
| `OPENING_TARGET_RR` | 3.0 | 3.0 | engine |
| `OPENING_ENTRY_FRACTION` | 0.5 | 1.0 | engine + `_stage_entries` |
| `OPENING_SCAN_MIN/MAX_GAP_PCT` | 1 / 25 | 0.5–2 / 4 | scan (universe) |
| `OPENING_SESSION_CUTOFF_MIN` | 30 | 30 / 45 | monitor cutoff |

## Status

Built + unit-tested + **replay-validated**. `replay_validate_profile.py` drives a
captured day through BOTH the validated sim (`sim_opening_variant` sweet) and the
LIVE classifier+`OpeningEngine` under the profile, and compares picks / entry /
stop / 3R-target / trail. On 2026-06-29 (15 names armed by both): **picks 15/15,
entry+stop 15/15, 3R target 15/15, no push-trail 15/15** — i.e. the live engine
reproduces the sim's sweet-spot exactly (P&L follows from identical levels + the
same exit rules). Re-run any day:

```bash
PYTHONPATH=skills/trading-arena python3 \
  skills/trading-arena/opening_agent/replay_validate_profile.py sweet45
```

Caveat: validated on the one locally-captured replay day (the multi-day +19.1%
edge was established separately by the sim over 61 days; this confirms the live
*port* is faithful, not the edge itself). Baseline confirmed byte-for-byte
unchanged. Execution-layer fixes that ride underneath all profiles:
[SESSION_POSTMORTEM_2026-06-29.md](SESSION_POSTMORTEM_2026-06-29.md).
