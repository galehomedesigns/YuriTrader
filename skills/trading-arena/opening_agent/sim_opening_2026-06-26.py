#!/usr/bin/env python3
"""Faithful replay of the 2026-06-26 opening session — "if everything had executed
perfectly". Writes logs/opening_sim_2026-06-26.json for the opening-sim dashboard.

Methodology (per feedback_opening_replay_contract):
- Arming entries/stops are taken from the LIVE engine's actual decisions in
  advisory_monitor.log (ground truth) — NOT re-derived, so the gate verdict and
  arming bar are exactly what fired this morning.
- Bars are the real TradingView 2-min feed the live system saw, STITCHED across
  all session_replay_2026-06-26 snapshots (union by timestamp, 9:30->~12:52 ET;
  late snapshots truncated at the 16:00 shutdown).
- Each armed symbol is driven through the REAL OpeningEngine (entry takeout ->
  half fill -> protective stop -> 1R breakeven ratchet -> push-ratchet stops ->
  G9 add-to-full -> +30min cutoff flatten). Stop movements/exits are the engine's.
- Sizing = live $-slots: $1000/5 = $200/slot, half-slot fills then G9 adds to full.
- Commission-free (Questrade) so gross == net; spread/slippage NOT modelled.
"""
import json, os, glob, math
from datetime import datetime, date, time as dtime
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))
import sys; sys.path.insert(0, os.path.dirname(HERE))

def _load_env():
    for line in open("/home/tonygale/openclaw/.env"):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            if k and v:
                os.environ.setdefault(k, v)
_load_env()

from opening_agent import classifier as C
from opening_agent import engine as E
import shared.indicators as IND


def stitch_full():
    """All 2-min bars per tv-symbol, NO date/RTH filter (needed for SMA history)."""
    import glob as _glob
    by = {}
    for f in sorted(_glob.glob(os.path.join(SNAP_DIR, "bars_*.json"))):
        try:
            d = json.load(open(f))
        except Exception:
            continue
        for r in d.get("results", []):
            m = by.setdefault(r["symbol"], {})
            for b in (r.get("bars") or []):
                m[b["time"]] = b
    return {sym: [m[t] for t in sorted(m)] for sym, m in by.items()}


def gate_trail(tvsym, sym, full):
    """Run the REAL classifier on each opening bar (9:30→9:44) until it MATCHES —
    shows why a name armed when it did (the rolling-arm gate verdicts)."""
    allb = full.get(tvsym, [])
    out = []
    for i, b in enumerate(allb):
        dt = _et(b["time"])
        if dt.date() != DAY or not (dtime(9, 30) <= dt.time() <= dtime(9, 44)):
            continue
        prior = allb[:i]
        closes = [x["close"] for x in prior + [b]]
        smf, sms = IND.sma(closes, 20), IND.sma(closes, 200)
        v = C.classify_opening(sym, b, prior, smf, sms)
        out.append({"t": dt.strftime("%H:%M"), "range": round(b["high"] - b["low"], 4),
                    "decision": v.decision, "reason": v.reason})
        if v.decision in ("MATCH_LONG", "MATCH_SHORT"):
            break   # armed on the first match — that's the signal bar
    return out


class SimEngine(E.OpeningEngine):
    """Replay engine with a realistic stop-FILL rule: a protective stop fills only
    if price trades THROUGH the level (strict inequality, half-tick epsilon), not
    on an exact-penny touch. The live TV feed resolved knife-edge touches as
    no-fill (e.g. PLTR 9:44 low == stop 109.91 → survived, ratcheted to 110.29 per
    advisory_monitor.log). Everything else is the stock engine."""
    EPS = 0.0001

    def _manage_stop(self, bar):
        stopped = (bar["low"] < self.stop_price - self.EPS if self.side > 0
                   else bar["high"] > self.stop_price + self.EPS)
        if stopped:
            self.state = E.FLAT
            self._log(f"stop hit @{self.stop_price}", "G13")
            return [E.OrderTicket(self.symbol, "SELL" if self.side > 0 else "BUY",
                                  "MKT", self.filled, self.stop_price,
                                  "stop hit — flatten, no re-entry", "G7")]
        # breakeven ratchet at 1R (inlined from base so the base's <= stop check
        # never overrides our strict fill rule on an exact-penny touch)
        out = []
        if self.entry_price and self.stop_price and self.side > 0:
            risk = self.entry_price - self.stop_price
            if risk > 0 and bar["high"] >= self.entry_price + risk \
                    and self.stop_price < self.entry_price:
                self.stop_price = self.entry_price
                self._log(f"breakeven stop @{self.entry_price} (1R reached)", "G16")
                out.append(E.OrderTicket(self.symbol, "SELL", "STP", self.filled,
                                         self.entry_price,
                                         "breakeven stop — 1R profit reached", "G16"))
        return out

ET = ZoneInfo("America/New_York")
DAY = date(2026, 6, 26)
OPEN_T, CLOSE_T = dtime(9, 30), dtime(16, 0)
CUTOFF_MIN = 20   # sell-off at the 20-minute mark (9:50 ET) — original-setup criterion
BUDGET = float(os.environ.get("OPENING_TRADE_BUDGET_USD", "1000"))
MAXN = int(os.environ.get("OPENING_MAX_TRADES", "5"))
SLOT = BUDGET / MAXN
OFFSET = C.DEFAULTS["trade_offset"]
SNAP_DIR = os.path.join(HERE, "..", "logs", "session_replay_2026-06-26")
OUT = os.path.join(HERE, "..", "logs", "opening_sim_2026-06-26.json")

# The engine's actual armed decisions this morning (from advisory_monitor.log).
# symbol -> (tv_symbol, entry, stop). affordable=False => passed gate but slot < 1 share.
ARMED = {
    "WSE":  ("NASDAQ:WSE",  11.65, 11.41, True),
    "EQX":  ("AMEX:EQX",     9.83,  9.69, True),
    "PLTR": ("NASDAQ:PLTR", 110.95, 109.91, True),
    "NOW":  ("NYSE:NOW",    95.00, 94.16, True),
    "MSFT": ("NASDAQ:MSFT", None,  None,  False),  # armed but $200 slot < 1 share @ $357.90
}

def _et(ts):
    return datetime.fromtimestamp(ts, ET)

def stitch_bars():
    """Union of all snapshot bars per tv-symbol, 6/26 RTH only, sorted."""
    by_sym = {}
    for f in sorted(glob.glob(os.path.join(SNAP_DIR, "bars_*.json"))):
        try:
            d = json.load(open(f))
        except Exception:
            continue
        for r in d.get("results", []):
            m = by_sym.setdefault(r["symbol"], {})
            for b in (r.get("bars") or []):
                m[b["time"]] = b
    out = {}
    for sym, m in by_sym.items():
        bars = []
        for t in sorted(m):
            dt = _et(t)
            if dt.date() == DAY and OPEN_T <= dt.time() <= CLOSE_T:
                b = dict(m[t]); b["dt"] = dt
                bars.append(b)
        out[sym] = bars
    return out

def find_arming_bar(bars, entry, stop):
    """The signal bar whose high+off==entry and low-off==stop (closest match)."""
    want_hi, want_lo = entry - OFFSET, stop + OFFSET
    best, berr, bi = None, 1e9, -1
    for i, b in enumerate(bars):
        err = abs(b["high"] - want_hi) + abs(b["low"] - want_lo)
        if err < berr:
            best, berr, bi = b, err, i
    return bi, best, berr

def simulate(sym, tvsym, entry, stop, bars):
    """Drive the real engine from the armed state; record every order ticket."""
    ai, abar, aerr = find_arming_bar(bars, entry, stop)
    full = max(1, math.floor(SLOT / entry))
    eng = SimEngine(sym)
    eng.side, eng.state, eng.bar1 = 1, E.ARMED, abar
    eng.entry_price, eng.stop_price, eng.shares = entry, stop, full
    from datetime import timedelta
    cutoff_dt = datetime.combine(DAY, OPEN_T, ET) + timedelta(minutes=CUTOFF_MIN)  # 10:00 ET

    fills = []          # (qty, price, why)
    stop_moves = []     # (time_str, price, reason)
    tp_orders = []
    exit_rec = None
    sess_high_after = None
    entered_at = None
    timeline = []       # per-2-min-bar state for the dashboard (9:30 -> 10:20)
    win_end = (datetime.combine(DAY, OPEN_T, ET) + timedelta(minutes=50)).time()  # 10:20 ET

    def avg_cost_now():
        q = sum(x[0] for x in fills); return (sum(x[0] * x[1] for x in fills) / q) if q else None

    def row(b, evs):
        ac = avg_cost_now()
        held = eng.filled
        c = b["close"]
        mtm = round((c - ac) * held, 2) if (ac and held) else (0.0 if ac else None)
        if_held = round((c - entry) * full, 2) if entered_at is not None else None
        return {"t": b["dt"].strftime("%H:%M"), "o": b["open"], "h": b["high"],
                "l": b["low"], "c": c,
                "state": {E.ARMED: "ARMED", E.IN_HALF: "IN ½", E.IN_FULL: "IN full",
                          E.FLAT: "FLAT"}.get(eng.state, "—"),
                "shares": held, "stop": round(eng.stop_price, 4) if (held or eng.state == E.ARMED) else None,
                "event": "; ".join(evs), "mtm": mtm, "if_held": if_held,
                "chg_vs_entry": round(c - entry, 4)}

    # context: candidate bars before it armed (still "scanning")
    for j in range(ai):
        b = bars[j]
        if b["dt"].time() > win_end:
            break
        timeline.append({"t": b["dt"].strftime("%H:%M"), "o": b["open"], "h": b["high"],
                         "l": b["low"], "c": b["close"], "state": "scan", "shares": 0,
                         "stop": None, "event": "candidate — classifying 2-min bar",
                         "mtm": None, "if_held": None, "chg_vs_entry": None})
    # the arming (signal) bar
    timeline.append({"t": abar["dt"].strftime("%H:%M"), "o": abar["open"], "h": abar["high"],
                     "l": abar["low"], "c": abar["close"], "state": "ARMED", "shares": 0,
                     "stop": round(stop, 4),
                     "event": f"gate MATCH → rest buy-stop ${entry:.2f}, protective stop ${stop:.2f}",
                     "mtm": None, "if_held": None, "chg_vs_entry": round(abar["close"] - entry, 4)})

    for b in bars[ai + 1:]:
        if b["dt"].time() > win_end:
            break
        before_filled = eng.filled
        evs = []
        tickets = eng.on_bar(b, complete=True)
        t = b["dt"].strftime("%H:%M")
        if eng.filled > before_filled and entered_at is None:
            entered_at = b["dt"]
        if entered_at is not None:
            sess_high_after = b["high"] if sess_high_after is None else max(sess_high_after, b["high"])
        for tk in tickets:
            if tk.rule == "G7" and "protective" in tk.reason:
                fills.append((tk.qty, entry, "entry (half slot) @ breakout"))
                evs.append(f"ENTRY fill {tk.qty} @ ${entry:.2f}; protective stop ${eng.stop_price:.2f}")
            elif tk.rule == "G9":
                fills.append((tk.qty, round(tk.price, 4), "G9 add to full slot"))
                evs.append(f"G9 ADD {tk.qty} @ ${tk.price:.2f} → full slot")
            elif tk.order_type == "STP" and tk.rule == "G16":
                stop_moves.append((t, round(tk.price, 4), tk.reason))
                evs.append(f"stop → ${tk.price:.2f} ({tk.reason})")
            elif tk.order_type == "LMT" and tk.rule == "G10":
                tp_orders.append((t, round(tk.price, 4), tk.reason))
                evs.append(f"take-profit rest @ ${tk.price:.2f}")
            elif tk.order_type == "MKT" and ("stop hit" in tk.reason):
                exit_rec = {"time": t, "price": round(eng.stop_price, 4),
                            "reason": "trailing/protective stop hit", "qty": tk.qty}
                evs.append(f"STOP EXECUTED — exit {tk.qty} @ ${eng.stop_price:.2f}")
        cut = False
        if exit_rec is None and b["dt"] >= cutoff_dt and eng.state in (E.IN_HALF, E.IN_FULL):
            ct = eng.on_cutoff()
            if ct:
                exit_rec = {"time": t, "price": round(b["close"], 4),
                            "reason": f"sell-off at {CUTOFF_MIN}-min mark", "qty": ct[0].qty}
                evs.append(f"{CUTOFF_MIN}-MIN SELL-OFF — exit {ct[0].qty} @ ${b['close']:.2f}")
                cut = True
        timeline.append(row(b, evs))
        # keep logging post-exit bars (FLAT) up to 10:20 so the missed move is visible

    last = bars[-1]
    if exit_rec is None and eng.filled > 0:
        exit_rec = {"time": last["dt"].strftime("%H:%M"),
                    "price": round(last["close"], 4),
                    "reason": "end of available data (no stop/cutoff hit)", "qty": eng.filled}

    cost = sum(q * p for q, p, _ in fills)
    qty_total = sum(q for q, _, _ in fills)
    exit_px = exit_rec["price"] if exit_rec else None
    realized = (exit_px * qty_total - cost) if exit_px is not None else 0.0
    # theoretical ceiling: full position exited at the post-entry session high
    max_exit = sess_high_after
    max_pl = ((max_exit - entry) * qty_total) if (max_exit and qty_total) else 0.0
    # counterfactual: full position still held at the last in-window (10:20) bar
    last_in_win = timeline[-1]["c"] if timeline else None
    held_to_1020 = round((last_in_win - entry) * qty_total, 2) if (last_in_win and qty_total) else None

    return {
        "timeline": timeline,
        "held_to_1020_pl": held_to_1020,
        "symbol": sym, "tv": tvsym,
        "arming_bar_time": abar["dt"].strftime("%H:%M") if abar else None,
        "arming_match_err": round(aerr, 4),
        "entry": entry, "init_stop": stop,
        "risk_per_share": round(entry - stop, 4),
        "full_slot_shares": full,
        "fills": [{"qty": q, "price": p, "why": w} for q, p, w in fills],
        "qty_total": qty_total,
        "avg_cost": round(cost / qty_total, 4) if qty_total else None,
        "stop_moves": [{"time": t, "price": p, "reason": r} for t, p, r in stop_moves],
        "tp_orders": [{"time": t, "price": p, "reason": r} for t, p, r in tp_orders],
        "exit": exit_rec,
        "realized_pl": round(realized, 2),
        "realized_pct": round(realized / cost * 100, 2) if cost else None,
        "session_high_after_entry": round(max_exit, 4) if max_exit else None,
        "max_possible_pl": round(max_pl, 2),
        "left_on_table": round(max_pl - realized, 2),
    }

def main():
    stitched = stitch_bars()
    full = stitch_full()
    rows = []
    for sym, (tv, entry, stop, afford) in ARMED.items():
        bars = stitched.get(tv, [])
        trail = gate_trail(tv, sym, full)
        if not afford or entry is None:
            rows.append({"symbol": sym, "tv": tv, "affordable": False,
                         "note": "passed 2-min gate (armed) but $200 slot < 1 share — not sized",
                         "gate_trail": trail, "bars": len(bars)})
            continue
        r = simulate(sym, tv, entry, stop, bars)
        r["affordable"] = True
        r["gate_trail"] = trail
        r["bars_available"] = len(bars)
        r["last_bar_time"] = bars[-1]["dt"].strftime("%H:%M") if bars else None
        rows.append(r)

    aff = [r for r in rows if r.get("affordable")]
    realized_total = sum(r.get("realized_pl", 0) for r in aff)
    max_total = sum(r.get("max_possible_pl", 0) for r in aff)
    held_total = sum(r.get("held_to_1020_pl") or 0 for r in aff)
    summary = {
        "generated_at": datetime.now(ET).isoformat(),
        "day": DAY.isoformat(),
        "window": "09:30–10:20 ET (first 50 minutes — the opening-strategy window)",
        "data_source": "TradingView 2-min feed (live this morning), stitched across session_replay snapshots",
        "config": {"budget_usd": BUDGET, "max_trades": MAXN, "slot_usd": SLOT,
                   "cutoff_min": CUTOFF_MIN, "tight_mode": C.DEFAULTS["tight_mode"],
                   "offset": OFFSET, "commission": "Questrade commission-free (gross==net)"},
        "totals": {"realized_pl": round(realized_total, 2),
                   "max_possible_pl": round(max_total, 2),
                   "left_on_table": round(max_total - realized_total, 2),
                   "held_to_1020_pl": round(held_total, 2)},
        "rows": rows,
    }
    json.dump(summary, open(OUT, "w"), indent=2, default=str)
    print(json.dumps(summary, indent=2, default=str))

if __name__ == "__main__":
    main()
