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
from opening_agent import profiles as PROF
import shared.indicators as IND

ET = ZoneInfo("America/New_York")
OPEN_T, CLOSE_T = dtime(9, 30), dtime(16, 0)
WIN_END = dtime(10, 20)            # chart display window
SELLOFF_MIN = 30                   # SWEET SPOT: 30-minute sell-off (10:00)  [legacy default]
RR_TARGET = 3.0                    # SWEET SPOT: 3R target (let winners run)  [legacy default]
SLOT = 200.0
RISK_USD = 6.0                     # risk-sizing variant: cap $-risk/trade (= 3% of the slot)
OFFSET = C.DEFAULTS["trade_offset"]
OUT = os.path.join(HERE, "..", "logs", "opening_sim_variant.json")
REPLAY_GLOB = os.path.join(HERE, "..", "logs", "session_replay_*")
GAP_MIN, GAP_MAX = 1.0, 6.0    # pre-market funnel band (positive gap, not over-extended)

# Per-day strategy profile: a captured session is simulated under the PROFILE that was
# live that morning (profiles.py). Days NOT listed here keep the legacy sweet-spot rules
# above (30-min · gap 1-6 · no trend-align) so their tabs stay byte-for-byte unchanged.
# Only the SWEET column follows the profile; the baseline column stays the live-style
# reference so the per-day A/B is on the profile's candidate universe.
PROFILE_BY_DAY = {
    "2026-06-30": "sweet45ta",   # live profile that morning (OPENING_STRATEGY_PROFILE)
}

def _sweet_params(profile_name):
    """Resolve the sweet-column sim knobs for a strategy profile → dict. Unknown/None
    profile → the legacy sweet-spot (30-min · gap 1-6 · 3R · no trend-align)."""
    ov = PROF.PROFILES.get((profile_name or "").strip().lower()) or {}
    if not ov:
        return {"selloff": SELLOFF_MIN, "gmin": GAP_MIN, "gmax": GAP_MAX,
                "rr": RR_TARGET, "ta": False}
    return {"selloff": int(ov.get("OPENING_SESSION_CUTOFF_MIN", SELLOFF_MIN)),
            "gmin": float(ov.get("OPENING_SCAN_MIN_GAP_PCT", GAP_MIN)),
            "gmax": float(ov.get("OPENING_SCAN_MAX_GAP_PCT", GAP_MAX)),
            "rr": float(ov.get("OPENING_TARGET_RR", RR_TARGET)),
            "ta": str(ov.get("OPENING_REQUIRE_TREND_ALIGN", "false")).lower() == "true"}

def _rules_label(profile_name, sp):
    """Honest one-line rule string for the per-day sweet panel header (shown verbatim
    on the tab so the day never mislabels the rules it was simulated under)."""
    band = f" · gap {sp['gmin']:g}–{sp['gmax']:g}%"
    ta = " · trend-align (SMA20>200)" if sp["ta"] else ""
    return (f"{profile_name} (TIGHT off · loc by close · wick stop · "
            f"{sp['rr']:g}R target · {sp['selloff']}-min{band}{ta})")

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

def arm_variant(full, sym, day, mode="sweet", trend_align=False):
    """First opening bar (9:30→9:44) that arms. sweet: bullish power bar closing above
    the 200-SMA (no TIGHT). baseline: classifier MATCH_LONG (TIGHT on, loc by open).
    trend_align (sweet/base_simarm only): also require SMA20>SMA200 at the arm bar."""
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
            if trend_align and not (smf is not None and smf > sms):
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

def simulate(full, sym, tv, day, mode="sweet", selloff_min=SELLOFF_MIN,
             rr_target=RR_TARGET, trend_align=False):
    armed = arm_variant(full, sym, day, mode, trend_align=trend_align)
    if not armed:
        return {"symbol": sym, "tv": tv, "armed": False}
    ai, entry, stop, info = armed
    risk = round(entry - stop, 4)
    target = round(entry + rr_target * risk, 4) if mode == "sweet" else None
    shares = max(1, math.floor(SLOT / entry))
    selloff_dt = datetime.combine(day, OPEN_T, ET) + timedelta(minutes=selloff_min)

    tl = []
    in_pos = False; cur_stop = stop; filled = 0; entry_dt = None; exit_rec = None
    be_done = False; sess_high = None; prev_low = None
    for i, b in enumerate(full):
        dt = _et(b["time"])
        if dt.date() != day or not (OPEN_T <= dt.time() <= WIN_END): continue
        smf, sms = smas_at(full, i)
        ev = []
        if dt.strftime("%H:%M") == info["arm_t"]:
            ev.append(f"ARMED ${entry:.2f}, stop ${stop:.2f}" + (f", target ${target:.2f} ({rr_target:g}R)" if target else " (trail)"))
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
                                "reason": f"{selloff_min}-min sell-off", "qty": filled}
                    ev.append(f"{selloff_min}-MIN SELL-OFF ${b['close']:.2f}"); in_pos = False
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
            "selloff_min": selloff_min,
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

def risk_resize_rows(rows):
    """Re-size each baseline row to risk <= $RISK_USD per trade, capped at one SLOT — the
    "size DOWN instead of skip/full-slot" option. Sizing is linear, so realized P&L /
    cost / held scale by new_shares/old_shares; per-trade ret% is unchanged. Pure + additive."""
    out = []
    for r in rows:
        rps = r.get("risk_per_share") or 0.0; old = r.get("shares") or 0; entry = r.get("entry") or 0.0
        nr = dict(r)
        if rps > 0 and old > 0 and entry > 0:
            new = max(1, min(math.floor(RISK_USD / rps), math.floor(SLOT / entry)))
            f = new / old
            nr["shares"] = new
            nr["position_cost"] = round(new * entry, 2)
            nr["realized_pl"] = round((r.get("realized_pl") or 0.0) * f, 2)
            if r.get("held_to_1020_pl") is not None:
                nr["held_to_1020_pl"] = round(r["held_to_1020_pl"] * f, 2)
            nr["sized_down"] = new < old
        else:
            nr["sized_down"] = False
        out.append(nr)
    return out

def risksize_panel(base_rows):
    rr = risk_resize_rows(base_rows)
    p = panel(rr)
    p["totals"]["n_sized_down"] = sum(1 for r in rr if r.get("sized_down"))
    return p

def _compute_day(dstr, snap_dir):
    """Simulate one captured session. The SWEET column follows that day's strategy
    profile (PROFILE_BY_DAY → legacy sweet-spot if unlisted); the baseline / sim-arm
    columns stay the live-style reference (30-min, no trend-align) so the A/B is that
    profile's candidate universe judged by both rule sets."""
    day = date.fromisoformat(dstr)
    prof = PROFILE_BY_DAY.get(dstr)
    sp = _sweet_params(prof)
    full_by = stitch(snap_dir)
    sweet = []; base = []; simarm = []
    for tv, full in sorted(full_by.items()):
        if not full: continue
        sym = tv.split(":")[-1]
        pclose, topen, gap = premarket_gap(full, day)
        if gap is None or not (sp["gmin"] <= gap <= sp["gmax"]): continue
        if topen and (topen > MAX_PRICE or topen < MIN_PRICE): continue   # match live: $5–$300 only
        specs = (("sweet", sweet, dict(selloff_min=sp["selloff"], rr_target=sp["rr"], trend_align=sp["ta"])),
                 ("baseline", base, {}), ("base_simarm", simarm, {}))
        for mode, bucket, kw in specs:
            r = simulate(full, sym, tv, day, mode, **kw)
            if r.get("armed") and r.get("exit"):
                r["premarket_gap_pct"] = round(gap, 2)
                r["prev_close"] = round(pclose, 2) if pclose else None
                r["today_open"] = round(topen, 2) if topen else None
                bucket.append(r)
    d = {"day": dstr, "source": "TradingView 2-min (live capture)",
         "sweet": panel(sweet), "baseline": panel(base), "base_simarm": panel(simarm),
         "risksize": risksize_panel(base)}
    if prof:
        d["profile"] = prof
        d["sweet_rules"] = _rules_label(prof, sp)
    return d


def main():
    # Reuse already-rendered live-capture days verbatim (their tabs are locked); only
    # (re)compute a profiled day or a brand-new capture day.
    prior = {}
    if os.path.exists(OUT):
        try:
            for d in json.load(open(OUT)).get("days", []):
                if str(d.get("source", "")).startswith("TradingView"):
                    prior[d["day"]] = d
        except Exception:
            pass
    days_out = []
    for dstr, snap_dir in sorted(discover_days().items(), reverse=True):
        if dstr not in PROFILE_BY_DAY and dstr in prior:
            days_out.append(prior[dstr])
        else:
            days_out.append(_compute_day(dstr, snap_dir))
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
