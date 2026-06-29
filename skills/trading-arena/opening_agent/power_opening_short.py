#!/usr/bin/env python3
"""Power Opening SHORT — separate live strategy (short side of the opening-range).

Distinct from the long Power Opening: own $500 budget, own flags, own universe
(top-20 most liquid), own Telegram label. Bar DATA from the TV real-time feed
(tv_bars); ORDER EXECUTION via the isolated IBKR API (ibkr_exec) — NOT TradingView.

Edge (2yr IBKR backtest): short the opening-range breakdown on liquid names with
limit entries nets ~+0.13-0.15%/trade. This is the small live validation of it.

Modes:
  (default)   place  — at ~9:34 ET classify the 9:30 bar, place SHORT stop-limit
                       brackets (1 share each, capped at $500 notional, liquidity order)
  --flatten   cutoff — cancel our resting orders + buy-to-cover our shorts

Flags (.env): POS_SHORT_EXEC=1 enables; POS_SHORT_ALLOW_TRADING=1 = LIVE (else SHADOW).
"""
import argparse
import json
import os
import sys
from datetime import datetime, time
from zoneinfo import ZoneInfo

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))                 # skills/trading-arena
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "ibkr_exec"))


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
from opening_agent import classifier as C                  # noqa: E402
from opening_agent import tv_bars                           # noqa: E402
from opening_agent.run_opening_scan import send_message     # noqa: E402
import shared.indicators as ind                             # noqa: E402

ET = ZoneInfo("America/New_York")
OPEN_T = time(9, 30)
LOGS = os.path.join(os.path.dirname(_HERE), "logs")
STATE = os.path.join(LOGS, "pos_short_state.json")

UNIVERSE = [s.strip().upper() for s in os.environ.get(
    "POS_SHORT_UNIVERSE",
    "NVDA,PLTR,AMD,AVGO,MU,INTC,TSM,ORCL,COIN,SMCI,CRM,MRVL,GEV,ADBE,CSCO,MARA,AMAT,QCOM,NBIS,VRT"
).split(",") if s.strip()]
BUDGET = float(os.environ.get("POS_SHORT_BUDGET_USD", "500"))
MAX_TRADES = int(os.environ.get("POS_SHORT_MAX_TRADES", "5"))
LIMIT_BUFFER = float(os.environ.get("POS_SHORT_LIMIT_BUFFER_BPS", "5")) / 10000.0
CLIENT_ID = int(os.environ.get("POS_SHORT_CLIENT_ID", "95"))


def _enabled():
    return os.environ.get("POS_SHORT_EXEC", "0").lower() in ("1", "true", "yes")


def _live():
    return os.environ.get("POS_SHORT_ALLOW_TRADING", "0").lower() in ("1", "true", "yes")


def _bar1_and_smas(bars):
    """bars: tv_bars dicts (open/high/low/close/date-epoch), oldest->newest. Returns
    (bar1, sma20, sma200) where bar1 = today's first >=9:30 bar with >=200 prior."""
    today = datetime.now(ET).date()
    rows = [{**b, "et": datetime.fromtimestamp(b["date"], ET)} for b in bars]
    oi = next((i for i, b in enumerate(rows)
               if b["et"].date() == today and b["et"].time() >= OPEN_T), None)
    if oi is None or oi < 200:
        return None, None, None, None
    closes = [b["close"] for b in rows[:oi + 1]]
    prior = rows[max(0, oi - 60):oi]                         # classifier looks back ~20
    return rows[oi], prior, ind.sma(closes, 20), ind.sma(closes, 200)


# ── place mode ────────────────────────────────────────────────────────────────
def place():
    if not _enabled():
        print("[pos-short] POS_SHORT_EXEC not set — disabled"); return
    bars_map = tv_bars.fetch_bars(UNIVERSE, min_bars=210)
    picks, cum = [], 0.0
    for sym in UNIVERSE:                                    # universe is liquidity-ordered
        bars = bars_map.get(sym) or []
        if len(bars) < 201:
            continue
        bar1, prior, smf, sms = _bar1_and_smas(bars)
        if bar1 is None:
            continue
        if C.classify_opening(sym, bar1, prior, smf, sms).decision != "MATCH_SHORT":
            continue
        entry_stop = C.entry_level_short(bar1)               # bar1 low - offset (breakdown)
        protective = C.stop_level_short(bar1)                # bar1 high + offset
        entry_limit = round(entry_stop * (1 - LIMIT_BUFFER), 2)
        px = bar1["close"]
        if cum + px > BUDGET or len(picks) >= MAX_TRADES:    # 1 share each, cap notional
            continue
        cum += px
        picks.append({"symbol": sym, "qty": 1, "entry_stop": round(entry_stop, 2),
                      "entry_limit": entry_limit, "stop": round(protective, 2),
                      "ref": round(px, 2)})

    et = datetime.now(ET)
    if not picks:
        send_message(f"🩳 <b>Power Opening Short</b> — {et:%H:%M ET}\nNo MATCH_SHORT setups "
                     f"in the top-{len(UNIVERSE)} liquid universe today. No trades.")
        print("[pos-short] no short setups"); return

    lines = [f"  • <b>{p['symbol']}</b> SELL-STOP-LMT trig {p['entry_stop']}/lim {p['entry_limit']} "
             f"→ cover-stop {p['stop']}  (1 sh, ref {p['ref']})" for p in picks]
    head = "🟢 LIVE" if _live() else "🧪 SHADOW"
    send_message(f"🩳 <b>Power Opening Short — {head}</b> — {et:%H:%M ET}\n"
                 f"Shorting the breakdown on {len(picks)} liquid name(s) "
                 f"(${cum:.0f}/${BUDGET:.0f}):\n" + "\n".join(lines)
                 + ("\n\n<i>Stop-limit entries — may not fill on fast gaps (that's the test). "
                    "Flatten at the 30-min cutoff.</i>" if _live() else
                    "\n\n<i>SHADOW — nothing sent.</i>"))

    if not _live():
        print(f"[pos-short] SHADOW — {len(picks)} would-short"); return

    from executor import IBKRExecutor
    ex = IBKRExecutor(client_id=CLIENT_ID)
    placed = []
    try:
        ex.connect()
        for p in picks:
            try:
                r = ex.place_short_bracket(p["symbol"], p["qty"], p["entry_stop"],
                                           p["entry_limit"], p["stop"])
                placed.append(p["symbol"])
                send_message(f"🩳 IBKR short staged: <b>{p['symbol']}</b> "
                             f"({r['parent_status']}/{r['child_status']})")
            except Exception as e:                           # noqa: BLE001
                send_message(f"🔴 Power Opening Short — FAILED {p['symbol']}: {e}")
    finally:
        ex.disconnect()
    json.dump({"date": str(et.date()), "symbols": placed}, open(STATE, "w"))
    print(f"[pos-short] placed {len(placed)} shorts: {placed}")


# ── flatten mode (cutoff) ───────────────────────────────────────────────────────
def flatten():
    if not _enabled() or not _live():
        print("[pos-short] flatten skipped (disabled or shadow)"); return
    try:
        st = json.load(open(STATE))
    except Exception:                                        # noqa: BLE001
        st = {"symbols": []}
    if st.get("date") != str(datetime.now(ET).date()):
        print("[pos-short] state not for today — nothing to flatten"); return
    syms = set(st.get("symbols", []))
    from executor import IBKRExecutor
    ex = IBKRExecutor(client_id=CLIENT_ID)
    msgs = []
    try:
        ex.connect()
        # cancel our resting orders (SELL entries + their protective BUY stops) for our syms
        for t in ex.ib.reqAllOpenOrders():
            if t.contract.symbol in syms:
                try:
                    ex.ib.cancelOrder(t.order)
                except Exception:                            # noqa: BLE001
                    pass
        ex.ib.sleep(1.5)
        # buy-to-cover any short positions in our symbols
        for sym, pos in ex.positions():
            if sym in syms and pos < 0:
                stt = ex.cover(sym, -pos)
                msgs.append(f"🏁 covered SHORT {sym} x{int(-pos)}: {stt}")
        if not msgs:
            msgs.append("🏁 cancelled resting short orders; no short positions to cover")
    except Exception as e:                                   # noqa: BLE001
        msgs.append(f"🔴 Power Opening Short flatten failed: {e} — check by hand")
    finally:
        ex.disconnect()
    send_message("🩳 <b>Power Opening Short — cutoff flatten</b>\n" + "\n".join(msgs))
    print(f"[pos-short] flatten: {msgs}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--flatten", action="store_true", help="cutoff: cancel + buy-to-cover")
    a = ap.parse_args()
    (flatten if a.flatten else place)()


if __name__ == "__main__":
    main()
