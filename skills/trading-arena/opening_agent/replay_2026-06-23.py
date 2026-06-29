#!/usr/bin/env python3
"""One-off COUNTERFACTUAL replay of the 2026-06-23 opening session.

Today's live session traded NOTHING (deliberately off; long path also logged
'0/19 ready'). This reconstructs "what would have happened if every order had
worked" by pulling today's real IBKR 2-min bars (full history -> proper SMA200),
running the REAL classifier on the 9:30 bar, and simulating fills + the 30-min
cutoff exit. Long = the 'confirm the orders' TV path; Short = the liquid experiment.

Faithful to: classifier.classify_opening, entry/stop level fns, the 3% risk cap +
slot sizing (long), 1-share/$500 cap (short), and the 10:00 ET cutoff flatten.
NOT modeled: long half-entry G9 add, stop-ratchet/ride (engine extras) -> noted.
"""
import json, os, sys, time
from datetime import datetime, date, time as dtime, timedelta
from zoneinfo import ZoneInfo

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))                 # skills/trading-arena
from opening_agent import classifier as C
import shared.indicators as ind
from ib_async import IB, Stock

ET = ZoneInfo("America/New_York")
TODAY = date(2026, 6, 23)
OPEN_T = dtime(9, 30)
CUTOFF_MIN = 30                                            # OPENING_SESSION_CUTOFF_MIN default

LONG_WL = "ZETA,GRRR,EPC,INFQ,ICCM,IBM,WMT,ACN,NOW,OCTV,KO,MSFT,CRM,CAG,NFLX,QBTS,T,VZ,ADBE".split(",")
SHORT_U = "NVDA,PLTR,AMD,AVGO,MU,INTC,TSM,ORCL,COIN,SMCI,CRM,MRVL,GEV,ADBE,CSCO,MARA,AMAT,QCOM,NBIS,VRT".split(",")

# long sizing assumptions (disarmed now; documented live config = $1000/5 slots)
LONG_BUDGET, LONG_MAX = 1000.0, 5
LONG_PER = LONG_BUDGET / LONG_MAX
SHORT_LIMIT_BUFFER = 5 / 10000.0


def _et(b_date):
    if isinstance(b_date, datetime):
        dt = b_date
    else:
        s = str(b_date)
        try:
            dt = datetime.strptime(s.split(" US/")[0].strip(), "%Y%m%d %H:%M:%S")
        except ValueError:
            dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ET)
    return dt.astimezone(ET)


def fetch(ib, sym):
    c = Stock(sym, "SMART", "USD")
    try:
        ib.qualifyContracts(c)
        chunk = ib.reqHistoricalData(c, endDateTime="", durationStr="4 D",
                                     barSizeSetting="2 mins", whatToShow="TRADES",
                                     useRTH=True, formatDate=1, timeout=90)
    except Exception as e:                                  # noqa: BLE001
        print(f"  [{sym}] fetch FAIL: {e}", file=sys.stderr)
        return []
    rows = []
    for b in chunk:
        dt = _et(b.date)
        if OPEN_T <= dt.time() < dtime(16, 0):
            rows.append({"et": dt, "open": float(b.open), "high": float(b.high),
                         "low": float(b.low), "close": float(b.close),
                         "volume": float(b.volume or 0)})
    rows.sort(key=lambda r: r["et"])
    return rows


def classify(sym, rows):
    oi = next((i for i, b in enumerate(rows)
               if b["et"].date() == TODAY and b["et"].time() >= OPEN_T), None)
    if oi is None:
        return None, None, "no 9:30 bar today", None
    if oi < 200:
        return None, None, f"only {oi} priors (<200, SMA200 unavailable)", None
    closes = [b["close"] for b in rows[:oi + 1]]
    prior = rows[max(0, oi - 60):oi]
    v = C.classify_opening(sym, rows[oi], prior, ind.sma(closes, 20), ind.sma(closes, 200))
    return v, rows[oi], v.reason, oi


def sim_long(bar1, session):
    entry = C.entry_level_long(bar1); stop = C.stop_level_long(bar1)
    cut = bar1["et"].timestamp() + CUTOFF_MIN * 60
    win = [b for b in session if b["et"].timestamp() <= cut + 1]
    ent = ex = None
    for b in win[1:]:
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
            "pct": (ex - ent) / ent * 100, "stopped": ex == stop}


def sim_short(bar1, session):
    entry = C.entry_level_short(bar1); stop = C.stop_level_short(bar1)
    limit = round(entry * (1 - SHORT_LIMIT_BUFFER), 2)
    cut = bar1["et"].timestamp() + CUTOFF_MIN * 60
    win = [b for b in session if b["et"].timestamp() <= cut + 1]
    ent = ex = None
    for b in win[1:]:
        if ent is None:
            if b["low"] <= entry:                          # breakdown trigger touched
                ent = limit if b["low"] <= limit else entry  # limit fills only if reached
            continue
        if b["high"] >= stop:
            ex = stop; break
    if ent is None:
        return {"filled": False, "entry": entry, "stop": stop, "limit": limit}
    if ex is None:
        ex = win[-1]["close"]
    return {"filled": True, "entry_trig": entry, "fill": round(ent, 4), "stop": stop,
            "exit": round(ex, 4), "pct": (ent - ex) / ent * 100, "stopped": ex == stop}


def main():
    ib = IB()
    ib.connect("127.0.0.1", 4001, clientId=91, timeout=25)
    ib.reqMarketDataType(3)
    syms = list(dict.fromkeys(LONG_WL + SHORT_U))
    print(f"connected={ib.isConnected()} acct={ib.managedAccounts()} | fetching {len(syms)} syms", file=sys.stderr)
    bars = {}
    for i, s in enumerate(syms, 1):
        bars[s] = fetch(ib, s)
        print(f"  [{i}/{len(syms)}] {s}: {len(bars[s])} bars", file=sys.stderr)
        time.sleep(1.5)
    ib.disconnect()

    out = {"long": [], "short": [], "long_pnl": 0.0, "short_pnl": 0.0}

    print("\n===== LONG (Power Opening — 'confirm the orders' path) =====")
    for s in LONG_WL:
        v, bar1, reason, oi = classify(s, bars.get(s, []))
        if v is None:
            print(f"  {s:6} —  NO DATA: {reason}"); continue
        if v.decision != "MATCH_LONG":
            print(f"  {s:6} {v.decision:11} {v.state:6}/{v.location:7} — {reason}"); continue
        entry, stop = C.entry_level_long(bar1), C.stop_level_long(bar1)
        risk = (entry - stop) / entry * 100
        slot_qty = int(LONG_PER // entry)
        qty = max(1, slot_qty // 2)
        sim = sim_long(bar1, bars[s][oi:oi + 25])
        capped = " RISK-CAPPED(>3%)" if risk > 3.0 else ""
        line = f"  {s:6} MATCH_LONG  entry {entry:.2f} stop {stop:.2f} risk {risk:.1f}%{capped} qty~{qty}"
        if capped:
            print(line + " -> SKIPPED"); continue
        if not sim["filled"]:
            print(line + " -> entry never triggered (no fill, $0)"); continue
        pnl = qty * (sim["exit"] - sim["entry"])
        out["long_pnl"] += pnl
        out["long"].append((s, qty, sim, pnl))
        print(line + f" -> {'STOPPED' if sim['stopped'] else 'cutoff'} exit {sim['exit']:.2f} "
              f"({sim['pct']:+.2f}%) = ${pnl:+.2f}")

    print("\n===== SHORT (liquid experiment — 1 share each) =====")
    for s in SHORT_U:
        v, bar1, reason, oi = classify(s, bars.get(s, []))
        if v is None:
            print(f"  {s:6} —  NO DATA: {reason}"); continue
        if v.decision != "MATCH_SHORT":
            print(f"  {s:6} {v.decision:11} {v.state:6}/{v.location:7} — {reason}"); continue
        sim = sim_short(bar1, bars[s][oi:oi + 25])
        if not sim["filled"]:
            print(f"  {s:6} MATCH_SHORT trig {sim['entry']:.2f} -> entry never triggered (no fill, $0)")
            continue
        pnl = 1 * (sim["fill"] - sim["exit"])
        out["short_pnl"] += pnl
        out["short"].append((s, sim, pnl))
        print(f"  {s:6} MATCH_SHORT fill {sim['fill']:.2f} -> {'STOPPED' if sim['stopped'] else 'cutoff'} "
              f"exit {sim['exit']:.2f} ({sim['pct']:+.2f}%) = ${pnl:+.2f}")

    print("\n===== TOTALS =====")
    print(f"  LONG  matches filled: {len(out['long'])}  net P&L: ${out['long_pnl']:+.2f}  "
          f"(budget ${LONG_BUDGET:.0f}, half-slot entries; G9 add NOT modeled)")
    print(f"  SHORT matches filled: {len(out['short'])}  net P&L: ${out['short_pnl']:+.2f}  "
          f"(1 share each)")
    print(f"  COMBINED: ${out['long_pnl'] + out['short_pnl']:+.2f}  (gross of commissions/slippage)")


if __name__ == "__main__":
    main()
