#!/usr/bin/env python3
"""Opening Power — LIVE morning orchestrator (3 phases, cron-driven, ET).

  confirm  (~9:25)  run the pre-market scan, send the candidates to YuriStocks,
                    and ASK for today's budget. Nothing trades without your reply.
  execute  (~9:32)  read your confirmed budget; classify each candidate's first
                    2-min bar; for every MATCH, even-split the budget and place
                    live bracket orders (entry stop + protective stop).
  cutoff   (~9:50)  flatten everything (cancel unfilled + market-close fills).

Your 9:25 amount is authoritative, capped only to the account's available buying
power. LONG-ONLY by default (a cash account can't short). Places real orders ONLY
when OPENING_ALLOW_TRADING=true; otherwise it's a validate-only dry run.

    run_opening_live.py confirm | execute | cutoff   [--force] [--no-send]
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone
from zoneinfo import ZoneInfo


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
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from opening_agent import universe, ranker, live_executor, tv_watchlist
from opening_agent import classifier as C
from opening_agent.run_opening_scan import send_message

STATE = os.environ.get(
    "OPENING_LIVE_STATE",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                 "logs", "opening_live_state.json"))
ALLOW_SHORTS = os.environ.get("OPENING_ALLOW_SHORTS", "false").lower() == "true"
CANDIDATE_N = int(os.environ.get("OPENING_CANDIDATE_N", "10"))


def _today():
    return datetime.now(ZoneInfo("America/New_York")).date().isoformat()


def _read_state():
    try:
        return json.load(open(STATE))
    except (OSError, ValueError):
        return {}


def _write_state(s):
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    json.dump(s, open(STATE, "w"), default=str)


# ── phase: confirm (9:25) ────────────────────────────────────────────────────
def phase_confirm(send=True):
    ranked = ranker.rank(universe.scan(), top_n=CANDIDATE_N)
    cands = [r["symbol"] for r in ranked]
    armed = live_executor._armed()
    # Only open the budget-capture window when the agent can actually trade.
    _write_state({"date": _today(),
                  "phase": "awaiting_budget" if armed else "notify_only",
                  "candidates": cands, "budget": None})
    if not armed:
        # Not armed/funded: the pre-market notification is handled by
        # run_opening_scan — don't prompt for a budget we can't use.
        print("[confirm] not armed — notify-only, no budget prompt. candidates:", cands)
        return
    lines = ["🔔 <b>Opening Power — pre-market</b> (LIVE, armed)",
             f"<i>{_today()} — market opens in ~5 min</i>", "",
             "Top candidates from the scan:",
             "  " + (", ".join(cands[:CANDIDATE_N]) if cands else "(none qualifying)"),
             "",
             "<b>Reply with the $ amount to trade today</b> (e.g. <code>80</code>).",
             "Split evenly across whichever pass the 9:30 first-bar rule. "
             "No reply = no trading today. Long-only on this account."]
    if send:
        send_message("\n".join(lines))
    print("[confirm] armed — budget requested. candidates:", cands)


# ── phase: execute (9:32) ────────────────────────────────────────────────────
def _find_matches(candidates):
    matches = []
    for sym in candidates:
        bars = universe._fetch_2min_bars(sym, count=210)
        if len(bars) < 200:
            continue
        closes = [b["close"] for b in bars]
        import shared.indicators as ind
        smf, sms = ind.sma(closes, 20), ind.sma(closes, 200)
        bar1, prior = bars[-1], bars[:-1]
        v = C.classify_opening(sym, bar1, prior, smf, sms)
        if v.decision == "MATCH_LONG":
            matches.append({"symbol": sym, "side": "long",
                            "entry": C.entry_level_long(bar1),
                            "stop": C.stop_level_long(bar1), "price": closes[-1]})
        elif v.decision == "MATCH_SHORT" and ALLOW_SHORTS:
            matches.append({"symbol": sym, "side": "short",
                            "entry": C.entry_level_short(bar1),
                            "stop": C.stop_level_short(bar1), "price": closes[-1]})
    return matches


def _available_buying_power():
    try:
        from shared.ibkr_executor import IBKRExecutor
        bal = IBKRExecutor().get_balance()
        return max(0.0, list(bal.values())[0]["available_funds"])
    except Exception as e:                                # noqa: BLE001
        print(f"[execute] buying-power read failed: {e}", file=sys.stderr)
        return None


def _fmt_matches_only(matches, armed, budget):
    lines = ["🎯 <b>Opening Power — first-bar MATCHES</b> (9:32 ET)",
             "<i>Passed the rule: TIGHT state + power bar + matching location.</i>", ""]
    for m in matches:
        lines.append(f"  • <b>{m['symbol']}</b> — {m['side'].upper()}  "
                     f"entry {m['entry']:.2f} / stop {m['stop']:.2f}")
    if not armed:
        lines.append("\n<i>Notification only — agent not armed/funded; no orders placed.</i>")
    elif not budget:
        lines.append("\n<i>No budget reply received — no orders placed.</i>")
    return "\n".join(lines)


def phase_execute(send=True):
    st = _read_state()
    # Candidates from the 9:25 confirm; if that didn't run, scan fresh now.
    if st.get("date") == _today() and st.get("candidates"):
        candidates = st["candidates"]
    else:
        candidates = [r["symbol"] for r in ranker.rank(universe.scan(), top_n=CANDIDATE_N)]
        st = {"date": _today(), "candidates": candidates}

    matches = _find_matches(candidates)

    # ALWAYS notify what matched the criteria — independent of trading/arming.
    if not matches:
        if send:
            send_message("⚪ <b>Opening Power</b> — no first-bar MATCH among today's "
                         "candidates. Nothing qualifies.")
        _write_state({**st, "phase": "notified", "matches": [], "placed": []})
        print("[execute] no matches."); return

    # Narrow the TradingView watchlist down to just the first-bar matches at the
    # open (replaces the pre-market top-10 that run_opening_scan put there).
    # Non-fatal; auto-skips if TRADINGVIEW_SESSIONID is unset or disabled.
    if send and os.environ.get("OPENING_TV_WATCHLIST", "1") not in ("0", "false", ""):
        try:
            tv_watchlist.sync([m["symbol"] for m in matches], label="MATCHES")
        except Exception as e:  # noqa: BLE001
            print(f"[execute] TV watchlist sync skipped: {e}", file=sys.stderr)

    armed = live_executor._armed()
    budget = st.get("budget")

    # Notify-only when not armed or no budget — message already conveys the matches.
    if not armed or not budget or float(budget) <= 0:
        if send:
            send_message(_fmt_matches_only(matches, armed, budget))
        _write_state({**st, "phase": "notified", "matches": matches, "placed": []})
        print(f"[execute] matches={len(matches)} — NOTIFY ONLY (armed={armed}, budget={budget})")
        return

    # Armed + budget → place live bracket orders.
    bp = _available_buying_power()
    if bp is not None and float(budget) > bp:
        budget = bp
        print(f"[execute] budget capped to buying power ${bp:.2f}")
    allocs = live_executor.plan_allocations(matches, budget)
    ex = live_executor.LiveExecutor()
    placed = [ex.place_bracket(a) for a in allocs if a.get("shares", 0) >= 1]
    try:
        ex.disconnect()
    except Exception:
        pass
    _write_state({**st, "phase": "executed", "matches": matches, "placed": placed})
    if send:
        send_message(_fmt_execute(placed, budget, armed))
    print(f"[execute] matches={len(matches)} placed={sum(1 for p in placed if p.get('placed'))} armed={armed}")


def _fmt_execute(placed, budget, armed):
    head = ("🟢 <b>Opening Power — orders placed (LIVE)</b>" if armed
            else "🧪 <b>Opening Power — DRY RUN</b> (OPENING_ALLOW_TRADING off)")
    lines = [head, f"<i>budget ${float(budget):.2f}, even split</i>", ""]
    for p in placed:
        if p.get("placed") or p.get("validated"):
            lines.append(f"  • <b>{p['symbol']}</b> {p['side']} {p['shares']} sh "
                         f"entry@{p['entry_stop']} stop@{p['protective_stop']} "
                         f"(${p['notional']})")
        else:
            lines.append(f"  • {p['symbol']}: skipped — {p.get('reason')}")
    lines.append("\n<i>Flatten at the 20-min cutoff. /kill to abort.</i>")
    return "\n".join(lines)


# ── phase: cutoff (9:50) ─────────────────────────────────────────────────────
def phase_cutoff(send=True):
    if not live_executor._armed():
        print("[cutoff] not armed — nothing was traded, nothing to flatten.")
        return
    ex = live_executor.LiveExecutor()
    try:
        res = ex.flatten_all()
    finally:
        try:
            ex.disconnect()
        except Exception:
            pass
    st = _read_state(); _write_state({**st, "phase": "flat"})
    if send:
        if res.get("transmitted"):
            n = len(res.get("closed_positions", []))
            send_message(f"🏁 <b>Opening Power — flat</b>. Cancelled open orders, "
                         f"closed {n} position(s). Done for today.")
        else:
            send_message("🏁 <b>Opening Power</b> — cutoff (dry run, nothing to flatten).")
    print("[cutoff]", res)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("phase", choices=["confirm", "execute", "cutoff"])
    ap.add_argument("--no-send", action="store_true")
    a = ap.parse_args()
    send = not a.no_send
    {"confirm": phase_confirm, "execute": phase_execute, "cutoff": phase_cutoff}[a.phase](send=send)


if __name__ == "__main__":
    main()
