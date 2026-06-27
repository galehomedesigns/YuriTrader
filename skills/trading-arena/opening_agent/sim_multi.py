#!/usr/bin/env python3
"""Generate the 3-way comparison dashboards (BASELINE vs NEW-SIM vs LIVE-ENGINE),
on 2-min and 5-min bars, from the IBKR broad 231-name cache.

LIVE-ENGINE = the real OpeningEngine: classifier arm (TIGHT on, loc by open) +
half-fill + G9 ADD (scale to full as it climbs) + native push-ratchet trail +
breakeven + 30-min cutoff. This is the piece the other two sims dropped.

5-min bars are RESAMPLED from the 2-min cache (2-min buckets aggregated into
5-min clock buckets) — approximate, not native 5-min ticks. Flagged in the UI.

Writes logs/opening_sim_multi_2min.json and logs/opening_sim_multi_5min.json.
"""
import os, sys, json, math, importlib.util
from datetime import datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import sim_variant_ibkr_days as V          # reuse load/sim_one/panel/constants
spec = importlib.util.spec_from_file_location("m626", os.path.join(HERE, "sim_opening_2026-06-26.py"))
M = importlib.util.module_from_spec(spec); spec.loader.exec_module(M)
SimEngine, E, C = M.SimEngine, M.E, M.C

N_DAYS = 22; SLOTS = 5; CAPITAL = 1000.0

def resample(bars, minutes):
    """2-min bars -> N-min clock buckets (OHLC aggregate). minutes==2 -> passthrough."""
    if minutes <= 2: return bars
    out = []; cur = None; key = None
    for b in bars:
        dt = b["dt"]
        k = (dt.date(), dt.hour, (dt.minute // minutes) * minutes)
        if k != key:
            if cur: out.append(cur)
            cur = {"dt": dt.replace(minute=(dt.minute // minutes) * minutes, second=0, microsecond=0),
                   "open": b["open"], "high": b["high"], "low": b["low"], "close": b["close"]}
            key = k
        else:
            cur["high"] = max(cur["high"], b["high"]); cur["low"] = min(cur["low"], b["low"]); cur["close"] = b["close"]
    if cur: out.append(cur)
    return out

def load_tf(path, tf):
    bars, _, _, _ = V.load(path)               # 2-min bars with dt
    bars = resample(bars, tf)
    closes = [x["close"] for x in bars]
    s20, s200 = V.roll(closes, 20), V.roll(closes, 200)
    from collections import defaultdict
    byday = defaultdict(list)
    for i, x in enumerate(bars): byday[x["dt"].date()].append(i)
    return bars, s20, s200, byday

def liveengine_one(bars, s20, s200, idxs, day, sym):
    """Real OpeningEngine: classifier arm + half-fill + G9 add + push-ratchet + cutoff."""
    arm = None
    for i in idxs:
        if bars[i]["dt"].time() > V.ARM_END: break
        if s200[i] is None: continue
        if C.classify_opening("S", bars[i], bars[max(0, i - 30):i], s20[i], s200[i]).decision == "MATCH_LONG":
            arm = i; break
    if arm is None: return None
    entry = round(bars[arm]["high"] + V.OFFSET, 2); stop = round(bars[arm]["low"] - V.OFFSET, 2)
    if stop >= entry: return None
    shares = max(2, math.floor(V.SLOT / entry))           # need >=2 for half + add
    eng = SimEngine(sym)                                   # default cfg -> adds on (max_adds=2)
    eng.side, eng.state, eng.bar1 = 1, E.ARMED, bars[arm]
    eng.entry_price, eng.stop_price, eng.shares = entry, stop, shares
    selloff = datetime.combine(day, V.OPEN_T, V.ET) + timedelta(minutes=V.SELLOFF_MIN)
    fills = []; exitrec = None; tl = []; ent_dt = None; sesshi = None
    for i in idxs:
        if i <= arm: continue
        b = bars[i]; t = b["dt"]
        if t.time() > V.WIN_END: break
        ev = []; before = eng.filled
        for tk in eng.on_bar(b, complete=True):
            if tk.rule == "G7" and "protective" in tk.reason:
                fills.append((tk.qty, entry)); ev.append(f"ENTRY {tk.qty} @ ${entry:.2f}")
            elif tk.rule == "G9":
                fills.append((tk.qty, round(tk.price, 4))); ev.append(f"ADD {tk.qty} @ ${tk.price:.2f}")
            elif tk.order_type == "STP" and tk.rule == "G16":
                ev.append(f"push stop → ${tk.price:.2f}")
            elif tk.order_type == "MKT" and "stop hit" in tk.reason:
                exitrec = {"time": t.strftime("%H:%M"), "price": round(eng.stop_price, 4), "reason": "stop/push-trail hit", "qty": eng.filled}; ev.append("STOP exit")
        if eng.filled > before and ent_dt is None: ent_dt = t
        if ent_dt: sesshi = b["high"] if sesshi is None else max(sesshi, b["high"])
        tl.append({"t": t.strftime("%H:%M"), "o": b["open"], "h": b["high"], "l": b["low"], "c": b["close"],
                   "sma20": round(s20[i], 3) if s20[i] else None, "sma200": round(s200[i], 3) if s200[i] else None,
                   "shares": eng.filled, "stop": round(eng.stop_price, 4), "target": None, "event": "; ".join(ev),
                   "mtm": None, "if_held": None})
        if eng.state == E.FLAT: break
        if t >= selloff and eng.state in (E.IN_HALF, E.IN_FULL):
            ct = eng.on_cutoff()
            if ct: exitrec = {"time": t.strftime("%H:%M"), "price": round(b["close"], 4), "reason": "30-min sell-off", "qty": ct[0].qty}
            break
    if not exitrec or not fills: return None
    qty = sum(q for q, _ in fills); cost = sum(q * p for q, p in fills)
    realized = round(exitrec["price"] * qty - cost, 2)
    avg = cost / qty if qty else entry
    return {"symbol": sym, "tv": sym, "armed": True, "mode": "liveengine", "arm_t": bars[arm]["dt"].strftime("%H:%M"),
            "sma20": round(s20[arm], 2), "sma200": round(s200[arm], 2), "loc": "above-both",
            "entry": entry, "stop": stop, "target": None, "risk_per_share": round(entry - stop, 4),
            "risk_pct": round((entry - stop) / entry * 100, 2), "shares": qty, "position_cost": round(cost, 2),
            "exit": exitrec, "realized_pl": realized, "held_to_1020_pl": round((tl[-1]["c"] - avg) * qty, 2) if tl else None,
            "session_high": round(sesshi, 4) if sesshi else None, "timeline": tl,
            "avg_cost": round(avg, 4)}

SIM = {"baseline": lambda *a: V.sim_one(*a, "baseline"),
       "sweet":    lambda *a: V.sim_one(*a, "sweet"),
       "liveengine": liveengine_one}
SETUPS = [{"key": "baseline", "label": "⚙ BASELINE — live rules (TIGHT on · push-trail)", "klass": "base"},
          {"key": "sweet", "label": "★ NEW SIM — sweet-spot (3R target · no trail)", "klass": "sweet"},
          {"key": "liveengine", "label": "🔧 LIVE ENGINE — half+G9 ADD + push-ratchet", "klass": "live"}]

def compound(days, key):
    cur = CAPITAL; curve = []
    for d in sorted(days, key=lambda x: x["day"]):
        picks = sorted(d.get(key, {}).get("picks", []), key=lambda p: (p.get("arm_t") or "99:99"))[:SLOTS]
        slot = cur / SLOTS
        rows = [{"sym": p["sym"], "gap_pct": p.get("gap_pct"), "ret_pct": p["ret_pct"],
                 "ret_usd": round(slot * p["ret_pct"] / 100, 2)} for p in picks]
        day_pl = round(sum(x["ret_usd"] for x in rows), 2)
        cur = round(cur + day_pl, 2)
        curve.append({"day": d["day"], "slot": round(slot, 2), "n": len(rows), "picks": rows,
                      "day_pl": day_pl, "day_ret_pct": round(day_pl / (cur - day_pl) * 100, 3) if (cur - day_pl) else 0,
                      "capital": cur})
    return {"start": CAPITAL, "end": cur, "total_pct": round((cur / CAPITAL - 1) * 100, 2), "curve": curve}

def build(tf):
    syms = {}
    import glob
    for p in glob.glob(os.path.join(V.CACHE, "*.json")):
        try: syms[os.path.basename(p)[:-5]] = load_tf(p, tf)
        except Exception: pass
    all_days = sorted({d for (_, _, _, bd) in syms.values() for d in bd})
    last = all_days[-N_DAYS:]
    days_out = []
    for day in last:
        buckets = {s["key"]: [] for s in SETUPS}
        for s, (bars, s20, s200, byday) in syms.items():
            if day not in byday: continue
            dates = sorted(byday); pos = dates.index(day)
            if pos == 0: continue
            idxs = byday[day]
            if len(idxs) < 6: continue
            pclose = bars[byday[dates[pos - 1]][-1]]["close"]; o = bars[idxs[0]]["open"]
            gap = (o - pclose) / pclose * 100
            if not (V.GAP_MIN <= gap <= V.GAP_MAX) or o > V.MAX_PRICE: continue
            for k in buckets:
                r = SIM[k](bars, s20, s200, idxs, day, s)
                if r:
                    r["premarket_gap_pct"] = round(gap, 2); r["prev_close"] = round(pclose, 2); r["today_open"] = round(o, 2)
                    buckets[k].append(r)
        day_rec = {"day": day.isoformat(), "source": f"IBKR {tf}-min (broad 231-name cache)"}
        for k in buckets: day_rec[k] = V.panel(buckets[k])
        days_out.append(day_rec)
    days_out.sort(key=lambda d: d["day"], reverse=True)
    return {"generated_at": datetime.now(V.ET).isoformat(),
            "title": f"Opening Power — 3-way ({tf}-min bars)",
            "subtitle": f"BASELINE vs NEW-SIM vs LIVE-ENGINE on {tf}-min bars" + (" (resampled from 2-min)" if tf > 2 else ""),
            "timeframe_min": tf, "setups": SETUPS, "capital": CAPITAL, "slots": SLOTS,
            "days": days_out, "compound": {s["key"]: compound(days_out, s["key"]) for s in SETUPS}}

def main():
    for tf, name in ((2, "2min"), (5, "5min")):
        out = os.path.join(HERE, "..", "logs", f"opening_sim_multi_{name}.json")
        data = build(tf)
        json.dump(data, open(out, "w"), indent=2, default=str)
        c = data["compound"]
        print(f"[{tf}-min] days={len(data['days'])}  " +
              " | ".join(f"{k}: ${c[k]['end']:.0f} ({c[k]['total_pct']:+.1f}%)" for k in c))

if __name__ == "__main__":
    main()
