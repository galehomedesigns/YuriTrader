#!/usr/bin/env python3
"""IBKR replay of TODAY 2026-06-25 — BOTH scenarios on the backtest vendor.

Pulls today's real IBKR 2-min RTH bars (4 D each -> full SMA200 history) for the
91 pre-market gap-qualified names and runs the REAL classifier + fill/cutoff sim.
Same vendor + methodology as GATE_AB_SWEEP_60D.md (source of the +14.1%/60d), so
this is the apples-to-apples single-day A/B.

  A AS-BUILT     : coil gate ON, rolling-arm 9:30->9:42, first 5 armed by arm time.
  B GATE-OFF+GAP : coil gate OFF, top-5 by pre-market gap, enter on the 9:30 breakout.

Sizing: equal $200 dollar slots ($1000/5), full slot (G9 add assumed filled), 3%
risk cap, $0.05 min range, +30-min cutoff (10:00 ET). Questrade commission-free
=> gross == net; spread/slippage NOT modeled. ONE day — not the 14.1%.
"""
import json, os, sys, time
from datetime import datetime, date, time as dtime
from zoneinfo import ZoneInfo

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))


def _load_env():
    for line in open("/home/tonygale/openclaw/.env"):
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
TODAY = date(2026, 6, 25)
OPEN_T = dtime(9, 30)
CFG = C.DEFAULTS
ATR_LEN = CFG["atr_len"]; MULT = CFG["tight_atr_mult"]; MODE = CFG["tight_mode"]
OFFSET = CFG["trade_offset"]
CUTOFF_MIN = int(os.environ.get("OPENING_SESSION_CUTOFF_MIN", "30"))
ARM_BARS = 7
MAX_RISK = float(os.environ.get("OPENING_MAX_RISK_PCT", "3.0"))
MIN_RANGE = float(os.environ.get("OPENING_MIN_BAR_RANGE", "0.05"))
BUDGET, MAXN = 1000.0, 5
SLOT = BUDGET / MAXN
SCRATCH = "/tmp/claude-1000/-home-tonygale-openclaw/db8b5a93-7f6a-4ab2-b328-55afc31442d5/scratchpad"
CACHE = os.path.join(SCRATCH, "today_bars_ibkr_2026-06-25.json")
SCAN = os.path.join(os.path.dirname(_HERE), "logs", "opening_scan_latest.json")
SPACING = float(os.environ.get("REPLAY_FETCH_SPACING", "10.0"))   # IBKR pacing: <=60 reqs / 10 min


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
    rows = [{"et": _et(b.date).isoformat(), "ts": _et(b.date).timestamp(), "open": float(b.open),
             "high": float(b.high), "low": float(b.low), "close": float(b.close),
             "volume": float(b.volume or 0)} for b in ch]
    rows = [r for r in rows if OPEN_T <= _et(r["et"]).time() < dtime(16, 0)]
    rows.sort(key=lambda r: r["ts"])
    return rows


def do_fetch(universe):
    cache = json.load(open(CACHE)) if os.path.exists(CACHE) else {}
    todo = [s for s in universe if s not in cache]
    if not todo:
        print(f"[fetch] all {len(universe)} cached", file=sys.stderr); return cache
    ib = IB(); ib.connect("127.0.0.1", 4001, clientId=121, timeout=25); ib.reqMarketDataType(3)
    print(f"[fetch] connected={ib.isConnected()} acct={ib.managedAccounts()} fetching {len(todo)}/{len(universe)}", file=sys.stderr)
    for n, s in enumerate(todo, 1):
        try:
            cache[s] = fetch(ib, s); ok = len(cache[s])
        except Exception as e:                                  # noqa: BLE001
            cache[s] = []; ok = f"FAIL {str(e)[:45]}"
        print(f"  [{n}/{len(todo)}] {s}: {ok}", file=sys.stderr)
        json.dump(cache, open(CACHE, "w"))
        time.sleep(SPACING)
    ib.disconnect()
    return cache


def smas_at(rows, k):
    closes = [b["close"] for b in rows[:k + 1]]
    return ind.sma(closes, 20), ind.sma(closes, 200)


def classify_k(sym, rows, k):
    smf, sms = smas_at(rows, k)
    prior = rows[max(0, k - 60):k]
    return C.classify_opening(sym, rows[k], prior, smf, sms)


def sim_long_from(bar, session):
    entry = bar["high"] + OFFSET; stop = bar["low"] - OFFSET
    cut = bar["ts"] + CUTOFF_MIN * 60
    win = [b for b in session if b["ts"] <= cut + 1]
    ent = ex = None
    for b in win:
        if b["ts"] <= bar["ts"]:
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
        ex = win[-1]["close"] if win else ent
    return {"filled": True, "entry": entry, "stop": stop, "exit": round(ex, 4),
            "pct": (ex - ent) / ent * 100, "stopped": abs(ex - stop) < 1e-9}


def open_idx(rows):
    return next((i for i, b in enumerate(rows)
                 if _et(b["et"]).date() == TODAY and _et(b["et"]).time() >= OPEN_T), None)


def main():
    scan = json.load(open(SCAN))["ranked"]
    gap = {r["symbol"]: float(r.get("pct_change") or 0) for r in scan}
    syms = [r["symbol"] for r in scan]
    bars = do_fetch(syms)
    info = {s: (bars.get(s) or [], open_idx(bars.get(s) or [])) for s in syms}

    valid_b = [s for s in syms if info[s][1] is not None and len(info[s][0]) - info[s][1] >= 2]
    valid_a = [s for s in valid_b if info[s][1] >= 200]
    no_data = [s for s in syms if info[s][1] is None or not info[s][0]]

    # A: as-built (gate ON, rolling-arm)
    a_armed = []
    for s in valid_a:
        rows, oi = info[s]
        arm_k = next((k for k in range(oi, min(oi + ARM_BARS, len(rows)))
                      if classify_k(s, rows, k).decision == "MATCH_LONG"), None)
        if arm_k is None:
            continue
        ab = rows[arm_k]; entry = ab["high"] + OFFSET; stop = ab["low"] - OFFSET
        risk = (entry - stop) / entry * 100
        if risk > MAX_RISK or (entry - stop) < MIN_RANGE:
            continue
        a_armed.append({"sym": s, "arm": _et(ab["et"]).strftime("%H:%M"), "ts": ab["ts"],
                        "risk": risk, "sim": sim_long_from(ab, rows[arm_k:arm_k + 30])})
    a_armed.sort(key=lambda d: d["ts"])
    a_take = a_armed[:MAXN]
    a_total = sum(SLOT * d["sim"]["pct"] / 100 for d in a_take if d["sim"]["filled"])

    # B: gate-off + gap-rank + 9:30 breakout
    ranked = sorted(valid_b, key=lambda s: gap.get(s, 0), reverse=True)
    b_take = []
    for s in ranked:
        if len([d for d in b_take if d.get("sim")]) >= MAXN:
            break
        rows, oi = info[s]
        b1 = rows[oi]; entry = b1["high"] + OFFSET; stop = b1["low"] - OFFSET
        risk = (entry - stop) / entry * 100
        if risk > MAX_RISK or (entry - stop) < MIN_RANGE:
            b_take.append({"sym": s, "gap": gap.get(s, 0), "risk": risk, "skip": "risk-cap"})
            continue
        b_take.append({"sym": s, "gap": gap.get(s, 0), "risk": risk, "px": b1["open"],
                       "sim": sim_long_from(b1, rows[oi:oi + 30])})
    b_total = sum(SLOT * d["sim"]["pct"] / 100 for d in b_take if d.get("sim") and d["sim"]["filled"])

    # take-it-anyway across all valid_b
    ta = 0.0; tw = tl = tf = tn = 0
    for s in valid_b:
        rows, oi = info[s]
        sm = sim_long_from(rows[oi], rows[oi:oi + 30])
        if not sm["filled"]:
            tn += 1; continue
        ta += SLOT * sm["pct"] / 100
        if sm["pct"] > 1e-6: tw += 1
        elif sm["pct"] < -1e-6: tl += 1
        else: tf += 1

    out = []; P = out.append
    P(f"\n========== TODAY {TODAY} OPENING REPLAY (IBKR 2-min RTH, backtest vendor) ==========")
    P(f"universe: {len(syms)} | usable for B (today open): {len(valid_b)} | usable for A (>=200 prior bars): {len(valid_a)} | no-data: {len(no_data)}")
    P(f"config: TIGHT mode={MODE} mult={MULT} atr_len={ATR_LEN} | risk cap {MAX_RISK}% | slot ${SLOT:.0f} | cutoff +{CUTOFF_MIN}m")
    if no_data:
        P(f"no-data: {' '.join(no_data)}")

    P(f"\n--- A: AS-BUILT (gate ON, rolling-arm 9:30-9:42, first {MAXN} armed) ---")
    if not a_take:
        P("  no names armed a MATCH_LONG under the coil gate today.")
    else:
        P(f"{'sym':6}{'arm':>6}{'risk%':>7}{'fill':>6}{'exit pct':>10}{'$ on $200':>11}")
        for d in a_take:
            sm = d["sim"]; f = "yes" if sm["filled"] else "no"
            pct = f"{sm['pct']:+.2f}%" if sm["filled"] else "-"
            dol = SLOT * sm["pct"] / 100 if sm["filled"] else 0.0
            P(f"{d['sym']:6}{d['arm']:>6}{d['risk']:>6.1f}{f:>6}{pct:>10}{dol:>+11.2f}")
    P(f"  armed+tradable: {len(a_armed)} | taken: {len(a_take)} | TOTAL on $1000: ${a_total:+.2f} ({a_total/BUDGET*100:+.2f}%)")

    P(f"\n--- B: GATE-OFF + GAP-RANK + 9:30 BREAKOUT (top {MAXN} by gap) ---")
    P(f"{'sym':6}{'gap%':>8}{'9:30 open':>11}{'risk%':>7}{'fill':>6}{'exit pct':>10}{'$ on $200':>11}")
    for d in b_take:
        if not d.get("sim"):
            P(f"{d['sym']:6}{d['gap']:>+8.1f}{'-':>11}{d['risk']:>6.1f}  SKIP {d['skip']}"); continue
        sm = d["sim"]; f = "yes" if sm["filled"] else "no"
        pct = f"{sm['pct']:+.2f}%" if sm["filled"] else "-"
        dol = SLOT * sm["pct"] / 100 if sm["filled"] else 0.0
        P(f"{d['sym']:6}{d['gap']:>+8.1f}{d['px']:>11.2f}{d['risk']:>6.1f}{f:>6}{pct:>10}{dol:>+11.2f}")
    P(f"  TOTAL on $1000: ${b_total:+.2f} ({b_total/BUDGET*100:+.2f}%)")

    P(f"\n--- TAKE-IT-ANYWAY across all {len(valid_b)} valid names (9:30 breakout, gate ignored) ---")
    P(f"  win {tw} / lose {tl} / flat {tf} / never-triggered {tn} | avg ${ta/max(1,len(valid_b)):+.2f}/name | sum ${ta:+.2f}")
    P(f"\n(ONE trading day. Gross=net on Questrade; spread/slippage NOT modeled. Full-slot = upper bound.)")
    txt = "\n".join(out)
    print(txt)
    open(os.path.join(_HERE, "REPLAY_TODAY_IBKR_2026-06-25.txt"), "w").write(txt + "\n")


if __name__ == "__main__":
    main()
