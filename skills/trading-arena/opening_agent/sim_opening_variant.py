#!/usr/bin/env python3
"""VARIANT opening-strategy simulation — the rule changes Tony asked for, applied
to each captured session_replay day (currently only 2026-06-26). Writes
logs/opening_sim_variant.json (multi-day) for the tabbed dashboard.

Variant rules vs the live engine:
  - TIGHT/coil gate: OFF at the open (pre-market selection only).
  - Location: judged by the bar's CLOSE, and loosened — bullish = close above the
    200-SMA (does NOT require being above the 20). "Positive position / rising into
    the 20" qualifies.
  - Stop: the one-bar low, but CAPPED at 1.5% below entry (wide opening bars get a
    tighter stop → passes the risk budget and sizes properly).
  - Exit: 2:1 reward:risk target (entry + 2R), breakeven stop at +1R, otherwise
    HOLD to the 20-minute sell-off (9:50) — no aggressive per-bar trailing.
  - Sizing: full $200 slot at the breakout (no half/G9-add).
Selection is the live pre-market gap funnel; candidates per day come from ARMED below.
Data: TradingView 2-min feed stitched from session_replay snapshots. Commission-free.
"""
import os, json, glob, math
from datetime import datetime, date, time as dtime, timedelta
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))
import sys; sys.path.insert(0, os.path.dirname(HERE))
def _load_env():
    for line in open("/home/tonygale/openclaw/.env"):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            if k and v: os.environ.setdefault(k, v)
_load_env()
from opening_agent import classifier as C
import shared.indicators as IND

ET = ZoneInfo("America/New_York")
OPEN_T, CLOSE_T = dtime(9, 30), dtime(16, 0)
WIN_END = dtime(10, 20)            # chart display window
SELLOFF_MIN = 30                   # SWEET SPOT: 30-minute sell-off (10:00)
RR_TARGET = 3.0                    # SWEET SPOT: 3R target (let winners run)
SLOT = 200.0
OFFSET = C.DEFAULTS["trade_offset"]
OUT = os.path.join(HERE, "..", "logs", "opening_sim_variant.json")
REPLAY_GLOB = os.path.join(HERE, "..", "logs", "session_replay_*")
GAP_MIN, GAP_MAX = 1.0, 6.0    # pre-market funnel band (positive gap, not over-extended)

def discover_days():
    """Auto-find captured session days (logs/session_replay_<YYYY-MM-DD>); candidates
    = every captured symbol whose gap is in the funnel band AND the variant arms it.
    So new capture days appear as tabs automatically — no hardcoded lists."""
    import re
    out = {}
    for d in sorted(glob.glob(REPLAY_GLOB)):
        m = re.search(r"session_replay_(\d{4}-\d{2}-\d{2})$", d)
        if m:
            out[m.group(1)] = d
    return out

def _et(ts): return datetime.fromtimestamp(ts, ET)

def stitch(snap_dir):
    by = {}
    for f in sorted(glob.glob(os.path.join(snap_dir, "bars_*.json"))):
        try: d = json.load(open(f))
        except Exception: continue
        for r in d.get("results", []):
            m = by.setdefault(r["symbol"], {})
            for b in (r.get("bars") or []):
                m[b["time"]] = b
    return {sym: [m[t] for t in sorted(m)] for sym, m in by.items()}

def smas_at(full, i):
    closes = [x["close"] for x in full[:i + 1]]
    return IND.sma(closes, 20), IND.sma(closes, 200)

def premarket_gap(full, day):
    """(prev RTH close, today open, gap%) from the stitched series."""
    today = [b for b in full if _et(b["time"]).date() == day]
    prev = [b for b in full if _et(b["time"]).date() < day]
    if not today: return None, None, None
    topen = next((b for b in today if _et(b["time"]).time() >= OPEN_T), today[0])["open"]
    pclose = prev[-1]["close"] if prev else None
    gap = (topen - pclose) / pclose * 100 if pclose else None
    return pclose, topen, gap

def arm_variant(full, sym, day, mode="sweet"):
    """First opening bar (9:30→9:44) that arms. sweet: bullish power bar closing above
    the 200-SMA (no TIGHT). baseline: classifier MATCH_LONG (TIGHT on, loc by open)."""
    for i, b in enumerate(full):
        dt = _et(b["time"])
        if dt.date() != day or not (OPEN_T <= dt.time() <= dtime(9, 44)): continue
        smf, sms = smas_at(full, i)
        if sms is None: continue
        if mode == "baseline":
            if C.classify_opening("S", b, full[max(0, i - 30):i], smf, sms).decision != "MATCH_LONG":
                continue
        else:
            if not (C.bar_signal(b, full[max(0, i - 30):i], C.DEFAULTS) > 0 and b["close"] > sms):
                continue
        entry = round(b["high"] + OFFSET, 2); stop = round(b["low"] - OFFSET, 2)
        loc = "above-both" if b["close"] > max(smf, sms) else \
              ("above-200/below-20" if b["close"] > sms else "below-200")
        return i, entry, stop, {"arm_t": dt.strftime("%H:%M"), "sma20": round(smf, 2),
                                "sma200": round(sms, 2), "loc": loc}
    return None

def _arm_variant_dead(full, sym, day):
    """(unused) old single-mode arm."""
    for i, b in enumerate(full):
        dt = _et(b["time"])
        if dt.date() != day or not (OPEN_T <= dt.time() <= dtime(9, 44)): continue
        prior = full[:i]
        smf, sms = smas_at(full, i)
        if sms is None: continue
        sig = C.bar_signal(b, prior, C.DEFAULTS)
        bull = (b["close"] > sms)               # loosened bullish location, by close
        if sig > 0 and bull:
            entry = round(b["high"] + OFFSET, 2)
            stop = round(b["low"] - OFFSET, 2)          # SWEET SPOT: wick stop (one-bar low), no cap
            loc = "above-both" if b["close"] > max(smf, sms) else \
                  ("above-200/below-20" if b["close"] > sms else "below-200")
            return i, entry, stop, {"arm_t": dt.strftime("%H:%M"), "sma20": round(smf, 2),
                                    "sma200": round(sms, 2), "loc": loc}
    return None

def simulate(full, sym, tv, day, mode="sweet"):
    armed = arm_variant(full, sym, day, mode)
    if not armed:
        return {"symbol": sym, "tv": tv, "armed": False}
    ai, entry, stop, info = armed
    risk = round(entry - stop, 4)
    target = round(entry + RR_TARGET * risk, 4) if mode == "sweet" else None
    shares = max(1, math.floor(SLOT / entry))
    selloff_dt = datetime.combine(day, OPEN_T, ET) + timedelta(minutes=SELLOFF_MIN)

    tl = []
    in_pos = False; cur_stop = stop; filled = 0; entry_dt = None; exit_rec = None
    be_done = False; sess_high = None; prev_low = None
    for i, b in enumerate(full):
        dt = _et(b["time"])
        if dt.date() != day or not (OPEN_T <= dt.time() <= WIN_END): continue
        smf, sms = smas_at(full, i)
        ev = []
        if dt.strftime("%H:%M") == info["arm_t"]:
            ev.append(f"ARMED ${entry:.2f}, stop ${stop:.2f}" + (f", target ${target:.2f} (3R)" if target else " (trail)"))
        if not in_pos and exit_rec is None and i > ai and b["high"] >= entry:
            in_pos = True; filled = shares; entry_dt = dt; prev_low = b["low"]
            ev.append(f"ENTRY {shares} @ ${entry:.2f}")
        if in_pos:
            sess_high = b["high"] if sess_high is None else max(sess_high, b["high"])
            if target and b["high"] >= target:
                exit_rec = {"time": dt.strftime("%H:%M"), "price": target, "reason": "3R target hit", "qty": filled}
                ev.append(f"TARGET exit ${target:.2f}"); in_pos = False
            elif b["low"] <= cur_stop:
                exit_rec = {"time": dt.strftime("%H:%M"), "price": cur_stop,
                            "reason": ("breakeven stop" if be_done else "protective stop") + " hit", "qty": filled}
                ev.append(f"STOP exit ${cur_stop:.2f}"); in_pos = False
            else:
                if not be_done and b["high"] >= entry + risk:
                    cur_stop = entry; be_done = True; ev.append(f"+1R → breakeven ${entry:.2f}")
                if mode in ("baseline", "base_simarm") and be_done and prev_low is not None and round(prev_low - OFFSET, 4) > cur_stop:
                    cur_stop = round(prev_low - OFFSET, 4); ev.append(f"trail stop → ${cur_stop:.2f}")
                if dt >= selloff_dt:
                    exit_rec = {"time": dt.strftime("%H:%M"), "price": round(b["close"], 4),
                                "reason": f"{SELLOFF_MIN}-min sell-off", "qty": filled}
                    ev.append(f"{SELLOFF_MIN}-MIN SELL-OFF ${b['close']:.2f}"); in_pos = False
            prev_low = b["low"]
        tl.append({"t": dt.strftime("%H:%M"), "o": b["open"], "h": b["high"], "l": b["low"], "c": b["close"],
                   "sma20": round(smf, 3) if smf else None, "sma200": round(sms, 3) if sms else None,
                   "shares": filled if (in_pos or (exit_rec and exit_rec["time"] == dt.strftime("%H:%M"))) else 0,
                   "stop": round(cur_stop, 4) if (in_pos or dt.strftime("%H:%M") >= info["arm_t"]) else None,
                   "target": target, "event": "; ".join(ev),
                   "mtm": round((b["close"] - entry) * filled, 2) if in_pos else None,
                   "if_held": round((b["close"] - entry) * shares, 2) if entry_dt else None})
        if exit_rec and not in_pos and exit_rec["time"] == dt.strftime("%H:%M"):
            filled = 0
    if not exit_rec:
        return {"symbol": sym, "tv": tv, "armed": True, "exit": None}
    realized = round((exit_rec["price"] - entry) * shares, 2)
    held = round((tl[-1]["c"] - entry) * shares, 2) if (tl and entry_dt) else None
    pos_cost = round(shares * entry, 2)
    return {"symbol": sym, "tv": tv, "armed": True, "mode": mode, **info,
            "entry": entry, "stop": stop, "target": target, "risk_per_share": risk,
            "risk_pct": round(risk / entry * 100, 2), "shares": shares, "position_cost": pos_cost,
            "exit": exit_rec, "realized_pl": realized, "held_to_1020_pl": held,
            "session_high": round(sess_high, 4) if sess_high else None, "timeline": tl}

MAX_PRICE = 300.0    # remove high-priced stocks (>$300) from the dashboard
MIN_PRICE = 5.0      # match the LIVE scanner floor (OPENING_MIN_PRICE default $5)
TOP_PER_DAY = 8

def panel(rows):
    pcts = [r["realized_pl"] / r["position_cost"] * 100 for r in rows if r.get("position_cost")]
    disp = sorted(rows, key=lambda r: -(r.get("realized_pl") or 0))[:TOP_PER_DAY]
    picks = [{"sym": r["symbol"], "gap_pct": r.get("premarket_gap_pct"), "arm_t": r.get("arm_t"),
              "ret_pct": round(r["realized_pl"] / r["position_cost"] * 100, 3)}
             for r in rows if r.get("position_cost")]
    return {"totals": {"realized_pl": round(sum(r["realized_pl"] for r in rows), 2),
                       "avg_pct": round(sum(pcts) / len(pcts), 3) if pcts else 0,
                       "names": len(rows), "shown": len(disp),
                       "held_to_1020_pl": round(sum((r["held_to_1020_pl"] or 0) for r in rows), 2)},
            "rows": disp, "picks": picks}

def main():
    days_out = []
    for dstr, snap_dir in sorted(discover_days().items(), reverse=True):
        day = date.fromisoformat(dstr)
        full_by = stitch(snap_dir)
        sweet = []; base = []; simarm = []
        for tv, full in sorted(full_by.items()):
            if not full: continue
            sym = tv.split(":")[-1]
            pclose, topen, gap = premarket_gap(full, day)
            if gap is None or not (GAP_MIN <= gap <= GAP_MAX): continue
            if topen and (topen > MAX_PRICE or topen < MIN_PRICE): continue   # match live: $5–$300 only
            for mode, bucket in (("sweet", sweet), ("baseline", base), ("base_simarm", simarm)):
                r = simulate(full, sym, tv, day, mode)
                if r.get("armed") and r.get("exit"):
                    r["premarket_gap_pct"] = round(gap, 2)
                    r["prev_close"] = round(pclose, 2) if pclose else None
                    r["today_open"] = round(topen, 2) if topen else None
                    bucket.append(r)
        days_out.append({"day": dstr, "source": "TradingView 2-min (live capture)",
                         "sweet": panel(sweet), "baseline": panel(base), "base_simarm": panel(simarm)})
    summary = {
        "generated_at": datetime.now(ET).isoformat(),
        "window": "09:30–10:20 ET",
        "config": {"slot_usd": SLOT, "selloff_min": SELLOFF_MIN,
                   "rr_target": RR_TARGET, "gap_band": [GAP_MIN, GAP_MAX],
                   "rules": "SWEET SPOT: TIGHT off · location by close (>200) · WICK stop (one-bar low) · 3R target · breakeven at 1R · 30-min sell-off · gap 0.5-4%"},
        "days": days_out,
    }
    json.dump(summary, open(OUT, "w"), indent=2, default=str)
    print(json.dumps({"days": [{"day": d["day"], "sweet$": d["sweet"]["totals"]["realized_pl"],
                                "base$": d["baseline"]["totals"]["realized_pl"]} for d in days_out]}, indent=2))

if __name__ == "__main__":
    main()
