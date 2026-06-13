#!/usr/bin/env python3
"""Live MANUAL-ASSIST advisory — Yuri coaches you through each trade by hand.

After the open, for every stock whose first 2-min bar passes the rule, this
watches each subsequent 2-min bar for 20 minutes and Telegrams you exactly what to
do MANUALLY: when to ENTER, where to set the STOP, when to move the stop up (each
push), when to ADD, and when to CLOSE at the cutoff. It places NO orders — you
trade in TWS / IBKR Mobile. (Same R2-R7 logic as engine.py, rendered as advice.)

Launched ~9:32 ET (after bar 1 completes); runs to the 20-min cutoff, then exits.
"""
import os
import sys
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _load_env():
    p = "/home/tonygale/openclaw/.env"
    if os.path.exists(p):
        for line in open(p):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                if k and v:
                    os.environ.setdefault(k, v)


_load_env()
from opening_agent import classifier as C
from opening_agent import universe as U
from opening_agent import tv_watchlist
from opening_agent.engine import OpeningEngine
from opening_agent.run_opening_scan import send_message
import shared.indicators as _ind

ET = ZoneInfo("America/New_York")
CUTOFF_MIN = int(os.environ.get("OPENING_SESSION_CUTOFF_MIN", "20"))
POLL_SEC = int(os.environ.get("ADVISORY_POLL_SEC", "45"))


# ── Engine ticket → human instruction ────────────────────────────────────────
def advice(t):
    s, px = t.symbol, t.price
    if t.rule == "G5":
        return (f"🎯 <b>{s}</b> — set a <b>BUY-STOP at ${px:.2f}</b>. You're long the "
                f"moment price trades through it.")
    if t.rule == "G7" and "stop hit" in t.reason:
        return (f"🛑 <b>{s}</b> — <b>STOPPED OUT</b> near ${px:.2f}. Close it. Done for "
                f"the day on {s} (no re-entry).")
    if t.rule == "G7":
        return f"🟢 <b>{s}</b> — you're IN. Put your <b>STOP-LOSS at ${px:.2f}</b> now."
    if t.rule == "G16":
        return f"🔼 <b>{s}</b> — push made. <b>Move your stop up to ${px:.2f}.</b>"
    if t.rule == "G9":
        return f"➕ <b>{s}</b> — small pullback got taken out. <b>ADD here (~${px:.2f}).</b>"
    if t.rule == "G10":
        return f"💰 <b>{s}</b> — push 2. Set a <b>take-profit limit at ${px:.2f}.</b>"
    if t.rule == "G1":
        return f"🏁 <b>{s}</b> — 20 minutes up. <b>CLOSE your position now.</b>"
    return f"<b>{s}</b> {t.rule}: {t.reason}"


# ── Pure step helpers (testable without IBKR) ────────────────────────────────
def arm(sym, bar1, prior, smf, sms):
    """Classify bar 1; if MATCH, build an armed engine and return (engine, advices).
    Returns (None, []) when it's not a long MATCH."""
    v = C.classify_opening(sym, bar1, prior, smf, sms)
    if v.decision != "MATCH_LONG":      # long-only (account can't short)
        return None, []
    eng = OpeningEngine(sym)
    tickets = eng.on_bar1(bar1, prior, smf, sms)
    return eng, [advice(t) for t in tickets]


def step(eng, bar):
    return [advice(t) for t in eng.on_bar(bar, complete=True)]


def cutoff(eng):
    return [advice(t) for t in eng.on_cutoff()]


# ── Live monitor ─────────────────────────────────────────────────────────────
def _candidates():
    """Reuse the freshest pre-market scan (cached by run_opening_scan ~9:25) so we
    don't run a second IBKR scan. Falls back to [] -> main() rescans."""
    import json
    cache = os.environ.get(
        "OPENING_SCAN_CACHE",
        os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "logs", "opening_scan_latest.json"))
    try:
        rec = json.load(open(cache))
        return [r["symbol"] for r in rec.get("ranked", [])]
    except (OSError, ValueError, KeyError):
        return []


def main():
    from ib_async import IB, util
    util.patchAsyncio()
    ib = IB()
    ib.connect(os.environ.get("IBKR_HOST", "127.0.0.1"),
               int(os.environ.get("IBKR_PORT", "4001")),
               clientId=int(os.environ.get("ADVISORY_CLIENT_ID", "27")), timeout=20)
    ib.reqMarketDataType(3)

    cands = _candidates() or [r["symbol"] for r in __import__(
        "opening_agent.ranker", fromlist=["rank"]).rank(U.scan(limit_movers=50), top_n=10)]

    # Arm: classify bar 1 (the latest completed bar at launch) for each candidate.
    book = {}     # sym -> {"eng":..., "last_ts":...}
    fired = []
    skipped = []  # (sym, reason) for candidates that didn't match
    for sym in cands:
        try:
            bars = U._ibkr_2min_bars(ib, sym)
        except Exception as e:
            skipped.append((sym, f"IBKR data error: {e}"))
            continue
        if len(bars) < 200:
            skipped.append((sym, f"insufficient bars ({len(bars)}/200)"))
            continue
        closes = [b["close"] for b in bars]
        smf, sms = _ind.sma(closes, 20), _ind.sma(closes, 200)
        bar1, prior = bars[-1], bars[:-1]
        # Classify for transparency even if not MATCH_LONG
        v = C.classify_opening(sym, bar1, prior, smf, sms)
        eng, advices = arm(sym, bar1, prior, smf, sms)
        if eng is not None:
            book[sym] = {"eng": eng, "last_ts": bars[-1].get("date")}
            fired += advices
        else:
            skipped.append((sym, v.decision))

    if not book:
        skip_lines = "\n".join(f"  • {s} — {r}" for s, r in skipped) if skipped else ""
        send_message("⚪ <b>Opening Power</b> — no stock passed the 9:30 first-bar "
                     "rule. Nothing to trade today."
                     + (f"\n\n<b>Candidates checked ({len(skipped)}):</b>\n{skip_lines}" if skip_lines else ""))
        ib.disconnect(); return

    skip_lines = "\n".join(f"  • {s} — {r}" for s, r in skipped) if skipped else ""
    send_message("🎯 <b>Opening Power — these passed the 2-min test (LONG):</b>\n"
                 + "\n".join(fired)
                 + (f"\n\n<b>Did not pass ({len(skipped)}):</b>\n{skip_lines}" if skip_lines else "")
                 + "\n\n<i>Manual mode: I'll tell you when to enter, move stops, add, "
                 "and close. Place the orders yourself.</i>")

    # Narrow the TradingView watchlist to the names that passed the first-bar
    # rule, so the chart list matches what we're coaching. Non-fatal; auto-skips
    # if TRADINGVIEW_SESSIONID is unset or OPENING_TV_WATCHLIST is disabled.
    if os.environ.get("OPENING_TV_WATCHLIST", "1") not in ("0", "false", ""):
        try:
            tv_watchlist.sync(list(book.keys()), label="MATCHES")
        except Exception as e:                                   # noqa: BLE001
            print(f"[advisory] TV watchlist sync skipped: {e}", file=sys.stderr)

    # Loop each new 2-min bar until the cutoff.
    open_time = datetime.now(ET).replace(hour=9, minute=30, second=0, microsecond=0)
    cutoff_time = open_time + timedelta(minutes=CUTOFF_MIN)
    while datetime.now(ET) < cutoff_time:
        time.sleep(POLL_SEC)
        for sym, rec in list(book.items()):
            try:
                bars = U._ibkr_2min_bars(ib, sym)
            except Exception:
                continue
            if not bars:
                continue
            newest = bars[-1]
            if rec["last_ts"] is not None and newest.get("date") == rec["last_ts"]:
                continue                       # no new completed bar yet
            rec["last_ts"] = newest.get("date")
            msgs = step(rec["eng"], newest)
            for m in msgs:
                send_message(m)

    # Cutoff: close anything still open.
    for sym, rec in book.items():
        for m in cutoff(rec["eng"]):
            send_message(m)
    ib.disconnect()


if __name__ == "__main__":
    main()
