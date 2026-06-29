#!/usr/bin/env python3
"""Replay v2 — 2026-06-23 LONG watchlist, with the CORRECT live config.

Fixes vs v1: (1) loads .env so the TIGHT gate is ATR-normalized (live), not the
code-default flat-0.25%. (2) ROLLING ARM: re-classifies each completed 2-min bar
across the 9:30-9:44 window (OPENING_ARM_WINDOW_MIN=15), like advisory_monitor —
not just the 9:30 bar. (3) reports the "void margin" (how far each NO_PLAY sat from
the TIGHT line) and a "take-it-anyway" P&L (enter every name on its 9:30 breakout,
ignoring the gate) to show whether the gate rejected winners.
"""
import json, os, sys, time
from datetime import datetime, date, time as dtime
from zoneinfo import ZoneInfo

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))


def _load_env():
    p = "/home/tonygale/openclaw/.env"
    for line in open(p):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            if k and v:
                os.environ.setdefault(k, v)


_load_env()
from opening_agent import classifier as C
import shared.indicators as ind
from ib_async import IB, Stock

ET = ZoneInfo("America/New_York")
TODAY = date(2026, 6, 23)
OPEN_T = dtime(9, 30)
CFG = C.DEFAULTS
ATR_LEN = CFG["atr_len"]; MULT = CFG["tight_atr_mult"]; MODE = CFG["tight_mode"]
OFFSET = CFG["trade_offset"]
CUTOFF_MIN = 30
ARM_BARS = 7            # 9:30,9:32,...,9:42 complete within the 15-min (9:45) arm window
MAX_RISK = 3.0; MIN_RANGE = 0.05
LONG_BUDGET, LONG_MAX = 1000.0, 5
PER = LONG_BUDGET / LONG_MAX

LONG_WL = "ZETA,GRRR,EPC,INFQ,ICCM,IBM,WMT,ACN,NOW,OCTV,KO,MSFT,CRM,CAG,NFLX,QBTS,T,VZ,ADBE".split(",")


def _et(b):
    dt = b if isinstance(b, datetime) else datetime.fromisoformat(str(b))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ET)
    return dt.astimezone(ET)


def fetch(ib, sym):
    c = Stock(sym, "SMART", "USD")
    ib.qualifyContracts(c)
    ch = ib.reqHistoricalData(c, endDateTime="", durationStr="4 D", barSizeSetting="2 mins",
                              whatToShow="TRADES", useRTH=True, formatDate=1, timeout=90)
    rows = [{"et": _et(b.date), "date": _et(b.date).timestamp(), "open": float(b.open),
             "high": float(b.high), "low": float(b.low), "close": float(b.close),
             "volume": float(b.volume or 0)} for b in ch]
    rows = [r for r in rows if OPEN_T <= r["et"].time() < dtime(16, 0)]
    rows.sort(key=lambda r: r["et"])
    return rows


def smas_at(rows, k):
    closes = [b["close"] for b in rows[:k + 1]]
    return ind.sma(closes, 20), ind.sma(closes, 200)


def tight_ratio(rows, k):
    """How far bar k sits from the TIGHT line. ratio<=1 => TIGHT (atr mode)."""
    smf, sms = smas_at(rows, k)
    if smf is None or sms is None:
        return None
    prior = rows[max(0, k - 60):k]
    atr = C.atr(prior + [rows[k]], ATR_LEN)
    if not atr:
        return None
    sep = abs(smf - sms)
    return (sep / atr) / MULT if MODE == "atr" else (sep / rows[k]["open"]) / CFG["tight_threshold"]


def classify_k(sym, rows, k):
    smf, sms = smas_at(rows, k)
    prior = rows[max(0, k - 60):k]
    return C.classify_opening(sym, rows[k], prior, smf, sms)


def sim_long_from(bar, session):
    entry = bar["high"] + OFFSET; stop = bar["low"] - OFFSET
    cut = bar["date"] + CUTOFF_MIN * 60
    win = [b for b in session if b["date"] <= cut + 1]
    ent = ex = None
    for b in win:
        if b["date"] <= bar["date"]:
            continue
        if ent is None:
            if b["high"] >= entry:
                ent = entry
            continue
        if b["low"] <= stop:
            ex = stop; break
    if ent is None:
        return {"filled": False, "entry": entry, "stop": stop}
    if ex is None:
        ex = win[-1]["close"]
    return {"filled": True, "entry": entry, "stop": stop, "exit": round(ex, 4),
            "pct": (ex - ent) / ent * 100, "stopped": abs(ex - stop) < 1e-9}


def main():
    ib = IB(); ib.connect("127.0.0.1", 4001, clientId=93, timeout=25); ib.reqMarketDataType(3)
    print(f"connected={ib.isConnected()} | TIGHT mode={MODE} mult={MULT} atr_len={ATR_LEN} "
          f"| arm window {ARM_BARS} bars (9:30-9:42) | cutoff +{CUTOFF_MIN}m", file=sys.stderr)
    bars = {}
    for s in LONG_WL:
        try:
            bars[s] = fetch(ib, s)
        except Exception as e:
            print(f"  {s} FAIL {e}", file=sys.stderr); bars[s] = []
        time.sleep(1.2)
    ib.disconnect()

    print(f"\n{'sym':6}{'bar1 state':>11}{'tight_ratio':>13}{'bar1 verdict':>14}"
          f"{'rolling-arm (9:30-9:42)':>26}{'take-anyway P&L/sh':>20}")
    print("-" * 92)
    armed, take_win, take_lose, take_flat, take_nofill = [], 0, 0, 0, 0
    take_pnl = 0.0
    for s in LONG_WL:
        rows = bars.get(s) or []
        oi = next((i for i, b in enumerate(rows)
                   if b["et"].date() == TODAY and b["et"].time() >= OPEN_T), None)
        if oi is None or oi < 200:
            print(f"{s:6}{'NO DATA':>11}"); continue
        v1 = classify_k(s, rows, oi)
        ratio = tight_ratio(rows, oi)
        rstr = f"{ratio:.2f}" if ratio is not None else "  -"

        # rolling arm: first MATCH_LONG on bars oi..oi+ARM_BARS-1
        arm_k = None
        for k in range(oi, min(oi + ARM_BARS, len(rows))):
            if classify_k(s, rows, k).decision == "MATCH_LONG":
                arm_k = k; break
        if arm_k is not None:
            ab = rows[arm_k]; entry = ab["high"] + OFFSET; stop = ab["low"] - OFFSET
            risk = (entry - stop) / entry * 100
            tag = rows[arm_k]["et"].strftime("%H:%M")
            sim = sim_long_from(ab, rows[arm_k:arm_k + 25])
            capped = risk > MAX_RISK or (entry - stop) < MIN_RANGE
            if capped:
                arm_str = f"ARM {tag} risk{risk:.1f}%CAP"
            elif not sim["filled"]:
                arm_str = f"ARM {tag} no-trigger"
            else:
                pnl = max(1, int(PER // entry) // 2) * (sim["exit"] - sim["entry"])
                armed.append((s, tag, sim, pnl, risk))
                arm_str = f"ARM {tag} {'STOP' if sim['stopped'] else 'cut'} {sim['pct']:+.2f}%"
        else:
            arm_str = "—"

        # take-it-anyway: long on the 9:30 breakout regardless of gate
        ta = sim_long_from(rows[oi], rows[oi:oi + 25])
        if not ta["filled"]:
            take_nofill += 1; ta_str = "no-trigger $0"
        else:
            qty = max(1, int(PER // (rows[oi]["high"] + OFFSET)) // 2)
            p = ta["exit"] - ta["entry"]; take_pnl += p
            if p > 1e-6: take_win += 1
            elif p < -1e-6: take_lose += 1
            else: take_flat += 1
            ta_str = f"{'STOP' if ta['stopped'] else 'cut'} {ta['pct']:+.2f}% ${p:+.2f}"

        print(f"{s:6}{v1.state:>11}{rstr:>13}{v1.decision:>14}{arm_str:>26}{ta_str:>20}")

    print("\n===== ANSWERS =====")
    n_void = sum(1 for s in LONG_WL if classify_k(s, bars[s], next(i for i,b in enumerate(bars[s]) if b['et'].date()==TODAY and b['et'].time()>=OPEN_T)).decision != "MATCH_LONG") if all(bars.get(s) for s in LONG_WL) else "?"
    print(f"  rolling-arm matches (any bar 9:30-9:42): {len([1 for s in LONG_WL for _ in [0]])>0 and ''}")
    print(f"  names that ARM + would FILL a trade: {len(armed)}")
    for s, tag, sim, pnl, risk in armed:
        print(f"     {s} armed {tag}: exit {sim['exit']:.2f} {sim['pct']:+.2f}% -> ${pnl:+.2f}/half-slot (risk {risk:.1f}%)")
    print(f"\n  TAKE-IT-ANYWAY (all 19 long names entered on 9:30 breakout, gate ignored):")
    print(f"     filled & WIN: {take_win}   filled & LOSE: {take_lose}   flat: {take_flat}   never-triggered: {take_nofill}")
    print(f"     total per-share P&L if you'd taken every breakout: ${take_pnl:+.2f}")


if __name__ == "__main__":
    main()
