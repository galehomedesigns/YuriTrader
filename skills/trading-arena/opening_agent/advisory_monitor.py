#!/usr/bin/env python3
"""Live MANUAL-ASSIST advisory — Yuri coaches you through each trade by hand.

After the open, for every stock whose first 2-min bar passes the rule, this
watches each subsequent 2-min bar for 20 minutes and Telegrams you exactly what to
do MANUALLY: when to ENTER, where to set the STOP, when to move the stop up (each
push), when to ADD, and when to CLOSE at the cutoff. It places NO orders — you
trade in TradingView. (Same R2-R7 logic as engine.py, rendered as advice.)

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
from opening_agent import tv_bars
from opening_agent import tv_watchlist
from opening_agent.engine import OpeningEngine
from opening_agent.run_opening_scan import send_message
import shared.indicators as _ind

ET = ZoneInfo("America/New_York")
CUTOFF_MIN = int(os.environ.get("OPENING_SESSION_CUTOFF_MIN", "30"))
# EOD behaviour at the cutoff: 'flatten' = market-close everything (default, safe).
# 'ride' = let a breakeven-PROTECTED winner keep trailing the stop past the clock
# (capture more of a continued move); losers/scratch still flatten at the cutoff,
# and a hard +OPENING_RIDE_MAX_MIN backstop flattens any remainder.
EOD_MODE = os.environ.get("OPENING_EOD_MODE", "flatten").lower()
RIDE_MAX_MIN = int(os.environ.get("OPENING_RIDE_MAX_MIN", "30"))
POLL_SEC = int(os.environ.get("ADVISORY_POLL_SEC", "45"))

RELINK_HELP = ("<i>Re-link: on the laptop TradingView trading tab, open the bottom "
               "Trading Panel → broker dropdown → reconnect <b>Questrade</b> (re-enter "
               "the login if prompted). Keep that tab the only TradingView session.</i>")

BAR_SECONDS = 120          # 2-min opening bars (date = bar START epoch)


def _latest_complete(bars, now_epoch=None):
    """(bar, prior_bars) for the most recent CLOSED 2-min bar. TradingView appends
    the still-forming realtime bar as the last element during RTH; this drops any
    trailing bar whose close time (start + 120s) is still in the future, so we
    never classify on a half-formed bar (P3). Returns (None, []) if nothing has
    closed yet."""
    if not bars:
        return None, []
    if now_epoch is None:
        now_epoch = datetime.now(ET).timestamp()
    idx = len(bars) - 1
    while idx >= 0 and now_epoch < bars[idx]["date"] + BAR_SECONDS:
        idx -= 1
    if idx < 0:
        return None, []
    return bars[idx], bars[:idx]


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


def _stage_entries(subset, tag):
    """Build entry buy-stop orders (+ attached protective stop) for a batch of
    NEWLY-armed names and spawn the CDP queue runner that stages each for manual
    confirmation. Each name is sized to a FIXED slot = OPENING_TRADE_BUDGET_USD /
    OPENING_MAX_TRADES, so per-trade size is identical whether a name arms on the
    first bar or a later one (names arm across the 9:30-9:45 window, so the final
    match count isn't known up front — a fixed slot is the only consistent size).
    Stages only - the human clicks Send Order on every one. `tag` keeps the order
    file unique so overlapping batches don't clobber each other."""
    budget = float(os.environ.get("OPENING_TRADE_BUDGET_USD", "0") or 0)
    if budget <= 0:
        print("[advisory] OPENING_TRADE_BUDGET_USD not set - not staging", file=sys.stderr); return
    max_trades = int(os.environ.get("OPENING_MAX_TRADES", "5"))
    per = budget / max_trades                        # fixed per-trade slot
    syms = list(subset.keys())
    orders = []
    skipped = []                                     # (symbol, reason) for transparency
    for s in syms:
        entry, stop = subset[s].get("entry"), subset[s].get("stop")
        if not entry or entry <= 0:
            skipped.append((s, "no entry level"))
            print(f"[advisory] {s}: no entry level - skipped", file=sys.stderr)
            continue
        qty = int(per // entry)                       # whole shares affordable per slot
        if qty < 1:
            reason = f"${per:.2f}/slot < 1 share @ ${entry:.2f}"
            skipped.append((s, reason))
            print(f"[advisory] {s}: {reason} - skipped", file=sys.stderr)
            continue
        # Risk cap: skip if bar-1 risk exceeds the max allowed %
        bar_spread = entry - stop
        max_risk = float(os.environ.get("OPENING_MAX_RISK_PCT", "3.0"))
        risk_pct = bar_spread / entry * 100 if entry > 0 else 0
        if risk_pct > max_risk:
            reason = f"risk {risk_pct:.1f}% > {max_risk}% cap (entry ${entry:.2f}, stop ${stop:.2f})"
            skipped.append((s, reason))
            print(f"[advisory] {s}: {reason} - skipped", file=sys.stderr)
            continue
        # Min bar range: skip if entry-stop spread is too narrow (noise breakout)
        min_range = float(os.environ.get("OPENING_MIN_BAR_RANGE", "0.05"))
        if bar_spread < min_range:
            reason = f"bar-1 range ${bar_spread:.2f} < ${min_range:.2f} min"
            skipped.append((s, reason))
            print(f"[advisory] {s}: {reason} - skipped", file=sys.stderr)
            continue
        orders.append({"symbol": s, "side": "buy", "type": "stop",
                       "price": round(entry, 2), "qty": qty, "stop": round(stop, 2)})
    skip_line = ("\n\n<b>Skipped ({}):</b>\n".format(len(skipped))
                 + "\n".join(f"  • {s} — {r}" for s, r in skipped)) if skipped else ""
    if not orders:
        print("[advisory] no affordable orders to stage", file=sys.stderr)
        send_message(f"⚪ <b>No orders staged</b> — all {len(syms)} match(es) priced out of "
                     f"the ${per:.2f} slot.{skip_line}")
        return
    here = os.path.dirname(os.path.abspath(__file__))
    of = os.path.join(os.path.dirname(here), "logs", f"opening_orders_{tag}.json")
    with open(of, "w") as f:
        json.dump(orders, f)
    port = os.environ.get("OPENING_TV_CDP_PORT", "9225")
    qjs = os.path.join(here, "tv_order_queue.js")
    send_message(f"⚡ <b>Staging {len(orders)} order(s) to TradingView</b> — review each "
                 "confirmation on your laptop and click <b>Send Order</b> (or Cancel to skip)."
                 + skip_line)
    # Non-blocking: the queue runner handles confirmations while we keep coaching.
    subprocess.Popen(["node", qjs, "--port", str(port), "--orders-file", of])
    print(f"[advisory] spawned queue runner: {len(orders)} orders on CDP port {port}")


def _held_longs():
    """Set of symbols currently held LONG on Questrade (via tv_positions.js), used
    by ride-mode to detect when a resting stop has filled. None on read error."""
    here = os.path.dirname(os.path.abspath(__file__))
    port = os.environ.get("OPENING_TV_CDP_PORT", "9225")
    try:
        out = subprocess.run(["node", os.path.join(here, "tv_positions.js"), "--port", str(port)],
                             capture_output=True, text=True, timeout=30)
        return {p["symbol"].upper() for p in json.loads(out.stdout or "[]")
                if p.get("side") == "long" and (p.get("qty", 0) or 0) > 0}
    except Exception:                                           # noqa: BLE001
        return None


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
    # Closes happen at the cutoff (coaching is done), so run the queue to completion
    # and report the POSITION-RECONCILED outcome — the dialog poll can't be trusted,
    # only the broker positions can (P4). Generous timeout: the queue waits for your
    # manual Send Order on each (~100s/order) before reconciling.
    rec_file = os.path.join(os.path.dirname(here), "logs", "opening_close_reconcile.json")
    try:
        os.remove(rec_file)
    except OSError:
        pass
    try:
        subprocess.run(["node", qjs, "--port", str(port), "--orders-file", of],
                       timeout=len(orders) * 130 + 60)
    except subprocess.TimeoutExpired:
        print("[advisory] close queue timed out", file=sys.stderr)
    except Exception as e:                                       # noqa: BLE001
        print(f"[advisory] close queue error: {e}", file=sys.stderr)
    # Report what ACTUALLY flattened, read back from the Questrade positions table.
    try:
        rec = json.load(open(rec_file))
        lines = "\n".join(
            f"  {'✅' if r['flattened'] else '⚠️'} {r['symbol']}: {r['before']} → {r['after']}"
            + ("" if r["flattened"] else " <b>STILL HELD</b>")
            for r in rec.get("rows", []))
        all_flat = rec.get("flattened") == rec.get("total")
        head = ("✅ <b>All positions flattened</b>" if all_flat
                else f"⚠️ <b>{rec.get('flattened')}/{rec.get('total')} flattened — check the rest</b>")
        send_message(f"{head} (reconciled from Questrade positions):\n{lines}")
    except Exception as e:                                       # noqa: BLE001
        send_message("⚠️ <b>Couldn't auto-reconcile the closes</b> — verify your Questrade "
                     "positions are flat by hand.")
        print(f"[advisory] close reconcile read failed: {e}", file=sys.stderr)
    print(f"[advisory] close queue done for {len(orders)} positions on port {port}")


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


# ── Broker-link health (P0) ──────────────────────────────────────────────────
def _broker_health():
    """Check the Questrade<->TradingView broker link on the trading tab via CDP.
    Returns (ok: bool, detail: str). Anything that isn't a confirmed connection —
    including an unreachable tunnel/tab — is reported ok=False (we can't route
    orders, so we must not claim the link is up)."""
    here = os.path.dirname(os.path.abspath(__file__))
    port = os.environ.get("OPENING_TV_CDP_PORT", "9225")
    try:
        out = subprocess.run(["node", os.path.join(here, "tv_broker_health.js"), "--port", str(port)],
                             capture_output=True, text=True, timeout=30)
        data = json.loads(out.stdout or "{}")
        return bool(data.get("connected")), (data.get("detail") or "unknown")
    except Exception as e:                                        # noqa: BLE001
        return False, f"health check unreachable: {e}"


# ── Live monitor ─────────────────────────────────────────────────────────────
def _candidates():
    """Reuse the freshest pre-market scan (cached by run_opening_scan ~9:25) so we
    don't run a second scan. Falls back to [] -> main() rescans."""
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
    # Fallback rescan if the cache is missing — keep parity with run_opening_scan:
    # no pre-bar cap by default (evaluate the full pre-qualified set on the 9:30 bar).
    _top_n = os.environ.get("OPENING_SCAN_TOP_N", "").strip()
    cands = _candidates() or [r["symbol"] for r in __import__(
        "opening_agent.ranker", fromlist=["rank"]).rank(
            U.scan(limit_movers=50), top_n=int(_top_n) if _top_n else None)]

    max_trades = int(os.environ.get("OPENING_MAX_TRADES", "5"))
    # Arm new names on ANY completed bar inside this window from the open, not just
    # the first bar — good setups routinely form their power bar on bar 2-4 (P2).
    arm_window_min = int(os.environ.get("OPENING_ARM_WINDOW_MIN", "15"))
    auto_stage = os.environ.get("OPENING_TV_AUTO_STAGE", "").lower() == "true"
    watchlist_on = os.environ.get("OPENING_TV_WATCHLIST", "1") not in ("0", "false", "")

    open_time = datetime.now(ET).replace(hour=9, minute=30, second=0, microsecond=0)
    open_epoch = int(open_time.timestamp())   # first RTH 2-min bar's START time
    cutoff_time = open_time + timedelta(minutes=CUTOFF_MIN)
    arm_cutoff = open_time + timedelta(minutes=arm_window_min)

    book = {}          # sym -> {"eng","entry","stop","advices"}   (armed names)
    last_ts = {}       # sym -> ts of the latest bar we've already evaluated
    insufficient = []  # (sym, reason) names that never had enough history
    capped = []        # names that MATCHED on bar 1 but were past the top-N cap
    intro_sent = [False]
    broker = {"ok": None}   # None=unchecked, True/False=last known Questrade link state

    def check_broker(reason):
        """P0 guard: probe the Questrade<->TradingView link on the trading tab.
        Logs every probe (timestamped, to pin WHEN it drops across 9:29->9:33) and
        Telegrams on any state change. Only runs when we're staging via CDP; with
        auto-stage off there's no live order link to police, so it's a no-op."""
        if not auto_stage:
            return True
        ok, detail = _broker_health()
        prev = broker["ok"]
        ts = datetime.now(ET).strftime("%H:%M:%S")
        print(f"[advisory] broker-health {ts} ({reason}): connected={ok} — {detail}", file=sys.stderr)
        if ok and prev is False:
            send_message(f"✅ <b>Broker link restored</b> ({ts} ET) — Questrade reconnected. "
                         "Auto-staging re-enabled.")
        elif not ok and prev is not False:                       # first-seen down, or up->down
            send_message(f"🚨 <b>BROKER LINK DOWN</b> ({ts} ET) — Questrade↔TradingView is not "
                         f"connected ({detail}). Orders can't route; <b>auto-staging is PAUSED</b> "
                         f"until it's back.\n\n{RELINK_HELP}")
        broker["ok"] = ok
        return ok

    def attempt_arm(sym, bars):
        """Classify the latest CLOSED bar as a candidate power bar. Refuses a still-
        forming bar and any bar older than today's 9:30 open (a stale/not-yet-
        rendered pre-market bar — P3), so we never arm off the wrong bar. On
        MATCH_LONG build an armed engine and return (rec, 'MATCH_LONG'); else
        (None, reason)."""
        bar1, prior = _latest_complete(bars)
        if bar1 is None:
            return None, "no closed bar yet"
        if bar1["date"] < open_epoch:
            return None, "pre-open bar (not rendered yet)"
        series = prior + [bar1]
        if len(series) < 200:
            return None, f"insufficient closed bars ({len(series)}/200)"
        closes = [b["close"] for b in series]
        smf, sms = _ind.sma(closes, 20), _ind.sma(closes, 200)
        eng, advices = arm(sym, bar1, prior, smf, sms)
        if eng is None:
            return None, C.classify_opening(sym, bar1, prior, smf, sms).decision
        return {"eng": eng, "entry": C.entry_level_long(bar1),
                "stop": C.stop_level_long(bar1), "advices": advices}, "MATCH_LONG"

    def announce_and_stage(newly, tag):
        """Coach + watchlist-sync + (optionally) auto-stage a batch of names that
        just armed. `newly` is a sym list already inserted into `book`."""
        first = not intro_sent[0]
        intro_sent[0] = True
        fired = [a for s in newly for a in book[s]["advices"]]
        header = ("🎯 <b>Opening Power — these passed the 2-min test (LONG):</b>\n"
                  if first else
                  "🎯 <b>New setup armed (later-bar match, LONG):</b>\n")
        send_message(header + "\n".join(fired)
                     + ("\n\n<i>Manual mode: I'll tell you when to enter, move stops, "
                        "add, and close. Place the orders yourself.</i>" if first else ""))
        # Re-sync the TradingView watchlist to the full armed set so the chart list
        # matches what we're coaching (replace-entirely; non-fatal).
        if watchlist_on:
            try:
                tv_watchlist.sync(list(book.keys()), label="MATCHES")
            except Exception as e:                               # noqa: BLE001
                print(f"[advisory] TV watchlist sync skipped: {e}", file=sys.stderr)
        # Auto-stage entry orders (+ attached protective stop) for rapid MANUAL
        # confirmation on the laptop. Stages only — YOU click Send Order on each.
        # Refuse to stage if the broker link is down (P0): orders can't route, so
        # alert + tell the user to place these by hand instead of silently failing.
        if auto_stage:
            if broker["ok"] is False:
                send_message(f"⏸️ <b>{', '.join(newly)}</b> armed but <b>NOT staged</b> — Questrade "
                             f"link is down. Place these manually once reconnected.\n\n{RELINK_HELP}")
            else:
                try:
                    _stage_entries({s: book[s] for s in newly}, tag)
                except Exception as e:                           # noqa: BLE001
                    print(f"[advisory] order staging skipped: {e}", file=sys.stderr)

    # Pre-flight broker check (P0): know the link state BEFORE we try to stage, so
    # the first announce can warn instead of silently failing to route orders.
    check_broker("preflight")

    # ── P3: wait for the first RTH 2-min bar to CLOSE and render before classifying.
    # The cron fires at 9:32:00 ET, exactly when the 9:30–9:32 bar closes; it may not
    # have rendered into the data tab yet. attempt_arm already refuses any bar older
    # than the open, but we briefly poll so the opening bar is actually present for a
    # quorum of names before the first pass (avoids a wasted all-skip pass at 9:32).
    bar_wait_max = int(os.environ.get("OPENING_BAR_WAIT_MAX_MIN", "4"))
    bar_wait_poll = int(os.environ.get("OPENING_BAR_WAIT_POLL_SEC", "10"))
    wait_deadline = open_time + timedelta(minutes=bar_wait_max)
    bars_by_sym = {}
    while True:
        bars_by_sym = tv_bars.fetch_bars(cands, min_bars=200)
        now = datetime.now(ET)
        ready = [s for s in cands
                 if (_latest_complete(bars_by_sym.get(s, []))[0] or {}).get("date", 0) >= open_epoch]
        if now >= open_time + timedelta(minutes=2) and len(ready) >= max(1, len(cands) // 2):
            print(f"[advisory] opening bar ready for {len(ready)}/{len(cands)} at {now:%H:%M:%S} ET", file=sys.stderr)
            break
        if now >= wait_deadline:
            send_message("⚠️ <b>Opening Power</b> — the first 2-min bar hadn't fully rendered by "
                         f"{now:%H:%M} ET; proceeding with whatever has closed (later bars still arm).")
            print(f"[advisory] opening-bar wait timed out at {now:%H:%M:%S} ET ({len(ready)}/{len(cands)} ready)", file=sys.stderr)
            break
        time.sleep(bar_wait_poll)

    # ── Bar-1 pass: arm matches on the opening bar, in rank order, up to the cap.
    newly = []
    for sym in cands:
        bars = bars_by_sym.get(sym, [])
        if len(bars) < 200:
            insufficient.append((sym, f"insufficient bars ({len(bars)}/200)"))
            continue
        cb, _ = _latest_complete(bars)
        last_ts[sym] = cb["date"] if cb else None
        rec, _decision = attempt_arm(sym, bars)
        if rec is None:
            continue                          # no match yet — watched for later bars
        if len(book) >= max_trades:
            capped.append(sym)
            continue
        book[sym] = rec
        newly.append(sym)

    # Everything with enough history that hasn't armed is watched for a later bar.
    watching = [s for s in cands if s in last_ts and s not in book and s not in capped]

    if newly:
        announce_and_stage(newly, "bar1")
    else:
        send_message("🟡 <b>Opening Power</b> — no first-bar match at the open. "
                     f"Watching {len(watching)} candidate(s) for a setup through "
                     f"{arm_cutoff.strftime('%H:%M')} ET (setups often form after bar 1).")
    ctx = []
    if newly and watching:
        ctx.append(f"👀 Also watching {len(watching)} more for a later-bar setup "
                   f"(through {arm_cutoff.strftime('%H:%M')} ET).")
    if capped:
        ctx.append(f"Top-{max_trades} cap hit — not arming: {', '.join(capped)}.")
    if ctx:
        send_message("\n".join(ctx))

    # ── Live loop: advance armed engines AND keep arming new names each new bar.
    round_idx = 0
    while datetime.now(ET) < cutoff_time:
        time.sleep(POLL_SEC)
        round_idx += 1
        # P0: re-probe the broker link every poll (alerts on any drop/restore).
        check_broker("loop")
        arming_open = (datetime.now(ET) < arm_cutoff and len(book) < max_trades
                       and bool(watching))
        if not book and not arming_open:
            break                              # nothing armed and nothing left to arm
        fetch_syms = list(book.keys()) + (list(watching) if arming_open else [])
        loop_bars = tv_bars.fetch_bars(fetch_syms, min_bars=200)

        # (a) advance already-armed engines on their newly-completed bars.
        for sym in list(book.keys()):
            newest, _ = _latest_complete(loop_bars.get(sym, []))
            if newest is None:
                continue
            if last_ts.get(sym) == newest.get("date"):
                continue                       # no new completed bar yet
            last_ts[sym] = newest.get("date")
            for t in book[sym]["eng"].on_bar(newest, complete=True):
                send_message(advice(t))
                rule = getattr(t, "rule", None)
                # G16 = trailing stop-move; G10 = take-profit (push 2). Both staged
                # as one-click bracket modifies (same arming gate; non-fatal).
                # Skip the CDP stage if the broker link is down — the coaching
                # message still goes out so the user can act by hand.
                if auto_stage and broker["ok"] is not False and rule == "G16":
                    try:
                        _stage_stop_move(sym, t.price)
                    except Exception as e:                       # noqa: BLE001
                        print(f"[advisory] stop-move staging skipped: {e}", file=sys.stderr)
                elif auto_stage and broker["ok"] is not False and rule == "G10":
                    try:
                        _stage_take_profit(sym, t.price)
                    except Exception as e:                       # noqa: BLE001
                        print(f"[advisory] take-profit staging skipped: {e}", file=sys.stderr)

        # (b) try to arm NEW names on their newly-completed bars (P2 core).
        if arming_open:
            armed_now = []
            for sym in list(watching):
                if len(book) >= max_trades:
                    break
                bars = loop_bars.get(sym, [])
                newest, _ = _latest_complete(bars)
                if newest is None:
                    continue
                if last_ts.get(sym) == newest.get("date"):
                    continue                   # no new completed bar since last check
                last_ts[sym] = newest.get("date")
                rec, _decision = attempt_arm(sym, bars)
                if rec is None:
                    continue                   # still no match — re-check next bar
                book[sym] = rec
                watching.remove(sym)
                armed_now.append(sym)
            if armed_now:
                announce_and_stage(armed_now, f"r{round_idx}")

    if not book:
        ins = ("\n\n<b>Never had enough history ({}):</b>\n".format(len(insufficient))
               + "\n".join(f"  • {s} — {r}" for s, r in insufficient)) if insufficient else ""
        send_message("⚪ <b>Opening Power</b> — no candidate matched the 2-min rule "
                     f"through {arm_cutoff.strftime('%H:%M')} ET. Nothing traded today." + ins)
        return

    # Cutoff. flatten mode (default): market-close everything in-position. ride
    # mode: flatten only NON-protected positions; let a breakeven-PROTECTED winner
    # keep riding its trailing stop past the clock (its resting stop already locks
    # >= breakeven, so the worst case is a breakeven exit and the upside is open).
    def _protected(eng):
        return (eng.state in (IN_HALF, IN_FULL) and eng.filled > 0
                and eng.stop_price is not None and eng.entry_price is not None
                and ((eng.side > 0 and eng.stop_price >= eng.entry_price)
                     or (eng.side < 0 and eng.stop_price <= eng.entry_price)))

    riding, close_syms = [], []
    for sym, rec in book.items():
        if EOD_MODE == "ride" and _protected(rec["eng"]):
            riding.append(sym)
            continue
        for t in rec["eng"].on_cutoff():
            send_message(advice(t))
            if getattr(t, "rule", None) == "G1":
                close_syms.append(sym)
    # Stage one-click market-sell closes for the non-riding set, cross-checked
    # against the real Questrade positions (never sell what isn't held).
    if close_syms and auto_stage:
        if not check_broker("cutoff"):
            send_message(f"🚨 <b>Cutoff — CLOSE {', '.join(close_syms)} NOW, by hand</b> — the "
                         f"Questrade link is down so I can't stage the sells.\n\n{RELINK_HELP}")
        else:
            try:
                _stage_closes(close_syms)
            except Exception as e:                               # noqa: BLE001
                print(f"[advisory] close staging skipped: {e}", file=sys.stderr)

    # ── Ride loop (ride mode): keep trailing the stop on protected winners past
    # the cutoff until the resting stop fills (position gone) or the hard
    # +RIDE_MAX_MIN backstop, then flatten any remainder.
    if riding:
        send_message(f"🏇 <b>Riding {', '.join(riding)}</b> past the {CUTOFF_MIN}-min cutoff — "
                     f"breakeven-protected, trailing the stop. Exits on stop fill or by "
                     f"+{RIDE_MAX_MIN} min, whichever comes first.")
        ride_deadline = cutoff_time + timedelta(minutes=RIDE_MAX_MIN)
        while riding and datetime.now(ET) < ride_deadline:
            time.sleep(POLL_SEC)
            check_broker("ride")
            held = _held_longs()
            rbars = tv_bars.fetch_bars(riding, min_bars=200)
            for sym in list(riding):
                if held is not None and sym.upper() not in held:
                    send_message(f"✅ <b>{sym}</b> — stop filled / position closed. Done riding.")
                    riding.remove(sym)
                    continue
                newest, _ = _latest_complete(rbars.get(sym, []))
                if newest is None or last_ts.get(sym) == newest.get("date"):
                    continue
                last_ts[sym] = newest.get("date")
                for t in book[sym]["eng"].on_bar(newest, complete=True):
                    send_message(advice(t))
                    rule = getattr(t, "rule", None)
                    if auto_stage and broker["ok"] is not False and rule == "G16":
                        try:
                            _stage_stop_move(sym, t.price)        # trail the resting stop up
                        except Exception as e:                    # noqa: BLE001
                            print(f"[advisory] ride stop-move skipped: {e}", file=sys.stderr)
                    elif rule in ("G7", "G1") and sym in riding:  # engine says stopped/flat
                        riding.remove(sym)
        if riding:                                                # backstop reached
            send_message(f"🏁 <b>Ride backstop (+{RIDE_MAX_MIN}m) — flattening {', '.join(riding)} now.</b>")
            if auto_stage:
                if check_broker("ride-backstop"):
                    try:
                        _stage_closes(riding)
                    except Exception as e:                        # noqa: BLE001
                        print(f"[advisory] ride backstop close skipped: {e}", file=sys.stderr)
                else:
                    send_message(f"🚨 CLOSE {', '.join(riding)} NOW by hand — link down.\n\n{RELINK_HELP}")


if __name__ == "__main__":
    main()
