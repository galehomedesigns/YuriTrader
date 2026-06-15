#!/usr/bin/env python3
"""Live MANUAL-ASSIST advisory — Yuri coaches you through each trade by hand.

After the open, for every stock whose first 2-min bar passes the rule, this
watches each subsequent 2-min bar for 20 minutes and Telegrams you exactly what to
do MANUALLY: when to ENTER, where to set the STOP, when to move the stop up (each
push), when to ADD, and when to CLOSE at the cutoff. It places NO orders — you
trade in TWS / IBKR Mobile. (Same R2-R7 logic as engine.py, rendered as advice.)

Launched ~9:32 ET (after bar 1 completes); runs to the 20-min cutoff, then exits.
"""
import json
import os
import subprocess
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


def _stage_orders(book):
    """Build entry buy-stop orders (+ attached protective stop), sized by an even
    split of OPENING_TRADE_BUDGET_USD across the matches, write them to a file,
    and spawn the CDP queue runner that stages each for manual confirmation on
    the laptop. Stages only - the human clicks Send Order on every one."""
    budget = float(os.environ.get("OPENING_TRADE_BUDGET_USD", "0") or 0)
    if budget <= 0:
        print("[advisory] OPENING_TRADE_BUDGET_USD not set - not staging", file=sys.stderr); return
    syms = list(book.keys())
    per = budget / len(syms)
    orders = []
    for s in syms:
        entry, stop = book[s].get("entry"), book[s].get("stop")
        if not entry or entry <= 0:
            continue
        qty = int(per // entry)                      # whole shares affordable per slot
        if qty < 1:
            print(f"[advisory] {s}: ${per:.2f}/slot < 1 share @ {entry:.2f} - skipped", file=sys.stderr)
            continue
        orders.append({"symbol": s, "side": "buy", "type": "stop",
                       "price": round(entry, 2), "qty": qty, "stop": round(stop, 2)})
    if not orders:
        print("[advisory] no affordable orders to stage", file=sys.stderr); return
    here = os.path.dirname(os.path.abspath(__file__))
    of = os.path.join(os.path.dirname(here), "logs", "opening_orders.json")
    with open(of, "w") as f:
        json.dump(orders, f)
    port = os.environ.get("OPENING_TV_CDP_PORT", "9225")
    qjs = os.path.join(here, "tv_order_queue.js")
    send_message(f"⚡ <b>Staging {len(orders)} order(s) to TradingView</b> — review each "
                 "confirmation on your laptop and click <b>Send Order</b> (or Cancel to skip).")
    # Non-blocking: the queue runner handles confirmations while we keep coaching.
    subprocess.Popen(["node", qjs, "--port", str(port), "--orders-file", of])
    print(f"[advisory] spawned queue runner: {len(orders)} orders on CDP port {port}")


def _stage_closes(close_syms):
    """Stage market-SELL closes for today's open positions for one-click confirm.
    Cross-checks the real Questrade positions table: only stages a close for a
    symbol that actually shows a long position, sized to the held qty. Never a
    naked sell, and never touches a symbol we didn't trade today."""
    here = os.path.dirname(os.path.abspath(__file__))
    port = os.environ.get("OPENING_TV_CDP_PORT", "9225")
    try:
        out = subprocess.run(["node", os.path.join(here, "tv_positions.js"), "--port", str(port)],
                             capture_output=True, text=True, timeout=30)
        positions = {p["symbol"].upper(): p for p in json.loads(out.stdout or "[]")}
    except Exception as e:                                       # noqa: BLE001
        print(f"[advisory] could not read positions - NOT staging closes: {e}", file=sys.stderr); return
    orders = []
    for s in close_syms:
        pos = positions.get(s.upper())
        if not pos or pos.get("side") != "long" or not (pos.get("qty", 0) > 0):
            print(f"[advisory] {s}: no matching long position - skip close", file=sys.stderr); continue
        orders.append({"symbol": s, "side": "sell", "type": "close", "qty": pos["qty"]})
    if not orders:
        print("[advisory] no open positions to close", file=sys.stderr); return
    of = os.path.join(os.path.dirname(here), "logs", "opening_close_orders.json")
    with open(of, "w") as f:
        json.dump(orders, f)
    qjs = os.path.join(here, "tv_order_queue.js")
    send_message(f"🏁 <b>Cutoff — staging {len(orders)} CLOSE order(s)</b> (market sell, "
                 "sized to your held shares). Confirm each on your laptop to flatten.")
    subprocess.Popen(["node", qjs, "--port", str(port), "--orders-file", of])
    print(f"[advisory] spawned close queue for {len(orders)} positions on port {port}")


def _stage_stop_move(sym, new_stop):
    """Stage an in-place reprice of the resting protective stop (no cancel/gap)
    for one symbol; the user clicks Confirm in the Modify dialog. Non-blocking."""
    here = os.path.dirname(os.path.abspath(__file__))
    port = os.environ.get("OPENING_TV_CDP_PORT", "9225")
    of = os.path.join(os.path.dirname(here), "logs", f"opening_modify_{sym}.json")
    with open(of, "w") as f:
        json.dump([{"action": "modify-stop", "symbol": sym, "price": round(new_stop, 2)}], f)
    qjs = os.path.join(here, "tv_order_queue.js")
    send_message(f"🔼 <b>Stop-move staged: {sym} → {new_stop:.2f}</b> — click Confirm on your laptop.")
    subprocess.Popen(["node", qjs, "--port", str(port), "--orders-file", of])
    print(f"[advisory] spawned stop-move for {sym} -> {new_stop}")


def _stage_take_profit(sym, tp_price):
    """Stage adding a take-profit to the resting bracket (OCO with the stop) for
    one symbol; the user clicks Confirm in the Modify dialog. Non-blocking."""
    here = os.path.dirname(os.path.abspath(__file__))
    port = os.environ.get("OPENING_TV_CDP_PORT", "9225")
    of = os.path.join(os.path.dirname(here), "logs", f"opening_tp_{sym}.json")
    with open(of, "w") as f:
        json.dump([{"action": "modify-tp", "symbol": sym, "take_profit": round(tp_price, 2)}], f)
    qjs = os.path.join(here, "tv_order_queue.js")
    send_message(f"💰 <b>Take-profit staged: {sym} → {tp_price:.2f}</b> — click Confirm on your laptop.")
    subprocess.Popen(["node", qjs, "--port", str(port), "--orders-file", of])
    print(f"[advisory] spawned take-profit for {sym} -> {tp_price}")


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
            book[sym] = {"eng": eng, "last_ts": bars[-1].get("date"),
                         "entry": C.entry_level_long(bar1), "stop": C.stop_level_long(bar1)}
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

    # Auto-stage entry orders (+ attached protective stop) into the TradingView
    # order panel for rapid MANUAL confirmation on the laptop. Stages only - YOU
    # click Send Order on each. Off by default; enable OPENING_TV_AUTO_STAGE=true
    # (and have the laptop trading Chrome + tunnel up). Non-fatal.
    if os.environ.get("OPENING_TV_AUTO_STAGE", "").lower() == "true":
        try:
            _stage_orders(book)
        except Exception as e:                                   # noqa: BLE001
            print(f"[advisory] order staging skipped: {e}", file=sys.stderr)

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
            for t in rec["eng"].on_bar(newest, complete=True):
                send_message(advice(t))
                armed = os.environ.get("OPENING_TV_AUTO_STAGE", "").lower() == "true"
                rule = getattr(t, "rule", None)
                # G16 = trailing stop-move; G10 = take-profit (push 2). Both staged
                # as one-click bracket modifies (same arming gate; non-fatal).
                if armed and rule == "G16":
                    try:
                        _stage_stop_move(sym, t.price)
                    except Exception as e:                       # noqa: BLE001
                        print(f"[advisory] stop-move staging skipped: {e}", file=sys.stderr)
                elif armed and rule == "G10":
                    try:
                        _stage_take_profit(sym, t.price)
                    except Exception as e:                       # noqa: BLE001
                        print(f"[advisory] take-profit staging skipped: {e}", file=sys.stderr)

    # Cutoff: send close advice AND collect symbols the engine considers
    # in-position (it emits a G1 "close" ticket only when the entry filled).
    close_syms = []
    for sym, rec in book.items():
        for t in rec["eng"].on_cutoff():
            send_message(advice(t))
            if getattr(t, "rule", None) == "G1":
                close_syms.append(sym)
    # Stage one-click market-sell closes for those, cross-checked against the
    # real Questrade positions (never sell what isn't held). Same arming gate.
    if close_syms and os.environ.get("OPENING_TV_AUTO_STAGE", "").lower() == "true":
        try:
            _stage_closes(close_syms)
        except Exception as e:                                   # noqa: BLE001
            print(f"[advisory] close staging skipped: {e}", file=sys.stderr)
    ib.disconnect()


if __name__ == "__main__":
    main()
