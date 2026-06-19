#!/usr/bin/env python3
"""Opening Power — STANDALONE paper forward-tracker + rough backtest.

FULLY INDEPENDENT of the live CDP/order-staging system:
  - places NO orders, touches NO TradingView panel, NO Questrade account writes,
    NO IBKR connection, and does NOT depend on OPENING_TV_AUTO_STAGE.
  - reads ONLY market data (Questrade candles, read-only) + the morning's scan
    cache (read-only) to know the universe.
  - writes ONLY to its own log (logs/paper_track.jsonl + paper_track_summary.json).
  - reuses the SAME strategy logic (classifier + entry/stop levels) so it scores
    the real strategy — but cannot affect the live system in any way.

Model: entry = first bar that takes out bar1's high (the buy-stop), exit = initial
protective stop OR the 20-min cutoff close (ignores trailing/adds = conservative).
$0 commission (Questrade) + a configurable slippage assumption (so it's honest).

Usage:
  python3 paper_tracker.py                 # forward: score the latest session for
                                           #   today's scan-cache candidates, log it
  python3 paper_tracker.py --no-send       # same, no Telegram
  python3 paper_tracker.py --backtest --days 5 [--symbols AMD,GOOGL,...]
                                           # rough sanity check over recent sessions
Env (own knobs, NOT the live trading flags):
  PAPER_SLIPPAGE_PCT (default 0.0010)  PAPER_POSITION_USD (default 500)
"""
import argparse
import json
import os
import sys
from datetime import datetime, time
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
import shared.indicators as ind

ET = ZoneInfo("America/New_York")
SLIP = float(os.environ.get("PAPER_SLIPPAGE_PCT", "0.0010"))
POS_USD = float(os.environ.get("PAPER_POSITION_USD", "500"))
_HERE = os.path.dirname(os.path.abspath(__file__))
_LOGS = os.path.join(os.path.dirname(_HERE), "logs")
TRACK = os.path.join(_LOGS, "paper_track.jsonl")
SUMMARY = os.path.join(_LOGS, "paper_track_summary.json")
OPEN_T = time(9, 30)
CUTOFF_MIN = int(os.environ.get("OPENING_SESSION_CUTOFF_MIN", "30"))


def candles_ts(symbol, count=1300):
    """Timestamped 2-min candles via Questrade (read-only). Returns oldest->newest
    list of {et: datetime(ET), open, high, low, close, volume}."""
    from shared.questrade_executor import QuestradeExecutor
    raw = QuestradeExecutor().get_candles(symbol, interval="TwoMinutes", count=count)
    out = []
    for c in raw or []:
        try:
            t = c.get("end") or c.get("start")
            dt = datetime.fromisoformat(t.replace("Z", "+00:00")).astimezone(ET)
            out.append({"et": dt, "open": float(c["open"]), "high": float(c["high"]),
                        "low": float(c["low"]), "close": float(c["close"]),
                        "volume": float(c.get("volume", 0) or 0)})
        except Exception:                       # noqa: BLE001
            continue
    return out


def sessions(cs):
    """Group candles into trading-day sessions. Returns {date: [bars that day]} and
    the opening bar index (first bar with ET time >= 9:30) per day."""
    days = {}
    for b in cs:
        days.setdefault(b["et"].date(), []).append(b)
    return days


def _open_idx(day_bars):
    for i, b in enumerate(day_bars):
        if b["et"].time() >= OPEN_T:
            return i
    return None


def simulate_session(symbol, all_bars, day):
    """Score one session. all_bars = full timestamped series; day = the session
    date. Returns an outcome dict (or None if not enough data)."""
    day_bars = [b for b in all_bars if b["et"].date() == day]
    oi = _open_idx(day_bars)
    if oi is None:
        return None
    bar1 = day_bars[oi]
    # 200+ prior bars = everything before bar1 across the whole series
    prior = [b for b in all_bars if b["et"] < bar1["et"]]
    if len(prior) < 200:
        return {"symbol": symbol, "date": str(day), "skip": "insufficient_history"}
    closes = [b["close"] for b in prior] + [bar1["close"]]
    smf, sms = ind.sma(closes, 20), ind.sma(closes, 200)
    v = C.classify_opening(symbol, bar1, prior, smf, sms)
    if v.decision != "MATCH_LONG":             # long-only (matches live default)
        return {"symbol": symbol, "date": str(day), "match": False, "decision": v.decision}
    entry = C.entry_level_long(bar1)
    stop = C.stop_level_long(bar1)
    # session follow bars (bar1 .. cutoff) within the 20-min window
    cutoff_et = datetime.combine(day, OPEN_T, ET).timestamp() + CUTOFF_MIN * 60
    follow = [b for b in day_bars[oi:] if b["et"].timestamp() <= cutoff_et + 1]
    entered, entry_fill, exit_fill, reason = False, None, None, None
    for b in follow[1:]:
        if not entered:
            if C.takeout_long(bar1, b):
                entered, entry_fill = True, entry * (1 + SLIP)
                continue
        else:
            if b["low"] <= stop:
                exit_fill, reason = stop * (1 - SLIP), "stop"
                break
    if not entered:
        return {"symbol": symbol, "date": str(day), "match": True, "triggered": False,
                "entry": round(entry, 2), "stop": round(stop, 2)}
    if exit_fill is None:                       # rode to cutoff
        exit_fill, reason = follow[-1]["close"] * (1 - SLIP), "cutoff"
    qty = int(POS_USD // entry_fill)
    pct = (exit_fill - entry_fill) / entry_fill * 100
    pnl = (exit_fill - entry_fill) * qty        # $0 commission (Questrade)
    return {"symbol": symbol, "date": str(day), "match": True, "triggered": True,
            "reason": reason, "entry": round(entry_fill, 4), "exit": round(exit_fill, 4),
            "qty": qty, "pct": round(pct, 3), "pnl": round(pnl, 2)}


def _candidates_from_cache():
    cache = os.environ.get("OPENING_SCAN_CACHE", os.path.join(_LOGS, "opening_scan_latest.json"))
    try:
        return [r["symbol"] for r in json.load(open(cache)).get("ranked", [])]
    except (OSError, ValueError, KeyError):
        return []


def _log(row):
    os.makedirs(_LOGS, exist_ok=True)
    with open(TRACK, "a") as f:
        f.write(json.dumps(row) + "\n")


def _scorecard(rows, header):
    traded = [r for r in rows if r.get("triggered")]
    wins = [r for r in traded if r["pnl"] > 0]
    tot = sum(r["pnl"] for r in traded)
    pct = sum(r["pct"] for r in traded)
    lines = [header,
             f"signals: {sum(1 for r in rows if r.get('match'))}  triggered: {len(traded)}  "
             f"no-trigger: {sum(1 for r in rows if r.get('match') and not r.get('triggered'))}",
             f"win rate: {(100*len(wins)/len(traded)) if traded else 0:.0f}%  "
             f"net paper P&L: ${tot:+.2f}  (sum pct {pct:+.2f}%, ${POS_USD:.0f}/pos, $0 comm, {SLIP*100:.2f}% slip)"]
    for r in traded:
        lines.append(f"  • {r['symbol']} {r['date']}: {r['reason']} {r['pct']:+.2f}% (${r['pnl']:+.2f})")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--backtest", action="store_true")
    ap.add_argument("--days", type=int, default=5)
    ap.add_argument("--symbols", default="")
    ap.add_argument("--no-send", action="store_true")
    a = ap.parse_args()

    syms = [s.strip().upper() for s in a.symbols.split(",") if s.strip()] or _candidates_from_cache()
    if not syms:
        print("[paper] no candidates (empty scan cache and no --symbols)"); return

    rows = []
    if a.backtest:
        for s in syms:
            # Questrade candles include extended hours (~450 2-min bars/session),
            # so request enough to cover the requested days + a 200-bar prior buffer.
            cs = candles_ts(s, count=max(2000, (a.days + 2) * 460))
            days = sorted(sessions(cs).keys())[-a.days:]
            for d in days:
                r = simulate_session(s, cs, d)
                if r and not r.get("skip"):
                    rows.append(r); _log({**r, "mode": "backtest"})
        print(_scorecard(rows, f"📝 PAPER backtest — last {a.days} sessions, {len(syms)} symbols"))
    else:
        for s in syms:
            cs = candles_ts(s, count=300)
            days = sorted(sessions(cs).keys())
            if not days:
                continue
            r = simulate_session(s, cs, days[-1])      # most recent session
            if r and not r.get("skip"):
                rows.append(r); _log({**r, "mode": "forward"})
        card = _scorecard(rows, "📝 PAPER forward — latest session")
        print(card)
        if not a.no_send:
            try:
                from opening_agent.run_opening_scan import send_message
                send_message("📝 <b>Opening Power — PAPER tracker</b> (independent, no orders)\n" + card)
            except Exception as e:                      # noqa: BLE001
                print(f"[paper] telegram skipped: {e}", file=sys.stderr)


if __name__ == "__main__":
    main()
