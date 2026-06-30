#!/usr/bin/env python3
"""Replay-validate a strategy PROFILE: drive a captured day through BOTH the
validated sim (sim_opening_variant sweet) and the LIVE classifier+OpeningEngine
under that profile, and confirm they agree on picks, entry/stop/3R-target, and P&L.

Run:  PYTHONPATH=skills/trading-arena python3 .../replay_validate_profile.py [sweet45]
Sets the profile env BEFORE importing classifier/engine (like the live entry points).
"""
import os
import sys
from datetime import date, time as dtime

sys.path.insert(0, "/home/tonygale/openclaw/skills/trading-arena")
from opening_agent import profiles
PROFILE = sys.argv[1] if len(sys.argv) > 1 else "sweet45"
profiles.apply_to_env(PROFILE, verbose=False)

from opening_agent import classifier as C            # AFTER apply → sweet DEFAULTS
from opening_agent.engine import OpeningEngine
import opening_agent.sim_opening_variant as SIM

OFFSET = C.DEFAULTS["trade_offset"]
RR = float(os.environ["OPENING_TARGET_RR"])
SLOT = SIM.SLOT
CUTOFF_MIN = int(os.environ["OPENING_SESSION_CUTOFF_MIN"])
ARM_END = dtime(9, 44)
OPEN_T = dtime(9, 30)


def rolling_live_arm(full, sym, day):
    """advisory_monitor's rolling arm, using the LIVE classifier under this profile:
    first 2-min bar in 9:30–9:44 that classifies MATCH_LONG arms the trade."""
    for i, b in enumerate(full):
        dt = SIM._et(b["time"])
        if dt.date() != day or not (OPEN_T <= dt.time() <= ARM_END):
            continue
        smf, sms = SIM.smas_at(full, i)
        if sms is None:
            continue
        v = C.classify_opening(sym, b, full[max(0, i - 30):i], smf, sms)
        if v.decision == "MATCH_LONG":
            return i, round(b["high"] + OFFSET, 2), round(b["low"] - OFFSET, 2)
    return None


def live_engine_levels(full, sym, day, arm_i):
    """Drive the REAL OpeningEngine from the arm bar: confirm the entry stop, the
    protective stop, and (sweet) the 3R target it emits, and that NO push-trail add
    (G9) fires. Returns (entry, stop, target, rules_seen)."""
    b1 = full[arm_i]
    smf, sms = SIM.smas_at(full, arm_i)
    eng = OpeningEngine(sym, account_equity=50000.0)
    rules = set()
    for t in eng.on_bar1(b1, full[max(0, arm_i - 30):arm_i], smf, sms):
        rules.add(t.rule)
    entry, stop, target = eng.entry_price, eng.stop_price, None
    # walk subsequent bars to trigger the fill (+ collect the target ticket)
    for j in range(arm_i + 1, len(full)):
        dt = SIM._et(full[j]["time"])
        if dt.date() != day:
            break
        for t in eng.on_bar(full[j], complete=True):
            rules.add(t.rule)
            if t.rule == "G10":
                target = t.price
        if eng.filled > 0 and target is not None:
            break
    return entry, stop, target, rules, eng.filled > 0


def sim_pnl(full, tv, sym, day):
    r = SIM.simulate(full, sym, tv, day, mode="sweet")
    if not (r.get("armed") and r.get("exit")):
        return None
    return r


def main():
    days = SIM.discover_days()
    if not days:
        print("no captured replay days found"); return
    dstr = sorted(days)[-1]
    day = date.fromisoformat(dstr)
    full_by = SIM.stitch(days[dstr])
    print(f"=== replay validation: profile={PROFILE}  day={dstr}  "
          f"(RR={RR}, cutoff={CUTOFF_MIN}m, {len(full_by)} symbols captured) ===\n")

    n_arm_both = n_level_ok = n_target_ok = n_no_trail = n_pnl_rows = 0
    mism = []
    print(f"{'sym':6} {'sim_arm':>7} {'live_arm':>8} {'entry✓':>7} {'stop✓':>6} {'tgt✓':>5} {'no-trail':>8} {'sim P&L':>8}")
    for tv, full in sorted(full_by.items()):
        if not full:
            continue
        sym = tv.split(":")[-1]
        sim_arm = SIM.arm_variant(full, sym, day, "sweet")     # (i, entry, stop, meta)
        live_arm = rolling_live_arm(full, sym, day)
        if not sim_arm and not live_arm:
            continue
        sa = sim_arm[0] if sim_arm else None
        la = live_arm[0] if live_arm else None
        arm_match = (sa == la)
        line = f"{sym:6} {str(sa):>7} {str(la):>8}"
        if sim_arm and live_arm:
            n_arm_both += 1
            e_ok = (sim_arm[1] == live_arm[1])
            s_ok = (sim_arm[2] == live_arm[2])
            entry, stop, target, rules, live_filled = live_engine_levels(full, sym, day, live_arm[0])
            no_trail = ("G9" not in rules)            # sweet must NOT push-trail/add
            sp = sim_pnl(full, tv, sym, day)          # sim row only if it armed AND filled+exited
            sim_filled = sp is not None
            sim_target = sp.get("target") if sp else None
            pnl = sp["realized_pl"] if sp else None
            # target parity is only meaningful if BOTH filled; no-fill on BOTH = agreement.
            if live_filled and sim_filled:
                t_ok = (target is not None and sim_target is not None and abs(target - sim_target) <= 0.03)
                tcell = "Y" if t_ok else "N"
            elif (not live_filled) and (not sim_filled):
                t_ok = True; tcell = "—"               # neither broke out — both correctly no-fill
            else:
                t_ok = False; tcell = "FILL?"          # one filled, other didn't — real divergence
            n_level_ok += int(e_ok and s_ok); n_target_ok += int(t_ok); n_no_trail += int(no_trail)
            if pnl is not None:
                n_pnl_rows += 1
            line += f" {('Y' if e_ok else 'N'):>7} {('Y' if s_ok else 'N'):>6} {tcell:>5} {('Y' if no_trail else 'N'):>8} {('' if pnl is None else f'{pnl:+.2f}'):>8}"
            if not (arm_match and e_ok and s_ok and t_ok and no_trail):
                mism.append((sym, f"liveTgt={target} simTgt={sim_target}", f"liveFill={live_filled} simFill={sim_filled}"))
        else:
            line += f"  {'ARM MISMATCH (one side only)':>40}"
            mism.append((sym, sim_arm, live_arm, None, None, None))
        print(line)

    print(f"\nSUMMARY (profile {PROFILE}, {dstr}):")
    print(f"  armed by BOTH sim & live : {n_arm_both}")
    print(f"  entry+stop levels match  : {n_level_ok}/{n_arm_both}")
    print(f"  3R target correct (engine): {n_target_ok}/{n_arm_both}")
    print(f"  no push-trail/add (sweet) : {n_no_trail}/{n_arm_both}")
    print(f"  sim P&L computed for      : {n_pnl_rows}")
    if mism:
        print(f"\n  MISMATCHES ({len(mism)}):")
        for m in mism:
            print("   ", m[0], "sim", m[1], "live", m[2], "tgt", m[3], "exp", m[4])
    else:
        print("\n  ✅ live classifier+engine reproduce the sim's sweet picks & levels exactly.")


if __name__ == "__main__":
    main()
