#!/usr/bin/env python3
"""LLM Advisor — SHADOW MODE runner.

Design: ../LLM_ADVISOR_DESIGN.md §8 (rollout ladder, step 1).

Runs the LLM signal advisor against the SAME opportunities the live buy_watcher
sees, then LOGS what it *would* have done — vetoed, approved, ranked, capped —
next to what the bots said. It changes NOTHING: it places no trades, sends no
alerts, and does not touch buy_watcher / the executor's gates. Pure read + log.

Purpose: collect a window of real decisions so you can judge whether the
(deliberately de-calibrated) abliterated model's filtering is sane BEFORE it ever
gates a trade. Compare its vetoes/approvals against actual bot outcomes.

Run manually or on a cron separate from buy_watcher:
    .venv/bin/python skills/trading-arena/concierge/advisor_shadow.py --asset crypto --top 8

Output: human summary to stdout + one JSON line per run to the shadow audit log
(ADVISOR_SHADOW_LOG, default skills/trading-arena/logs/advisor_shadow.jsonl).
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone


def _load_env():
    env_file = "/home/tonygale/openclaw/.env"
    if not os.path.exists(env_file):
        return
    with open(env_file) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            if key and value:
                os.environ.setdefault(key, value)


_load_env()
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from concierge import advisor
from shared import llm_advisor
from shared import llm_advisor_caps
from shared import news_feed
from shared import advisor_path_state

# buy_watcher's alert threshold — the bot baseline we compare the LLM against.
BOT_ALERT_MIN_FIRING = int(os.environ.get("BUY_WATCHER_MIN_FIRING", "3"))
# USD notional ceiling per opportunity (the LLM may only reduce this).
ADVISOR_SIGNAL_MAX_USD = float(os.environ.get("ADVISOR_PER_TRADE_MAX_USD", "50"))
SHADOW_LOG = os.environ.get(
    "ADVISOR_SHADOW_LOG",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                 "logs", "advisor_shadow.jsonl"),
)


def opportunity_to_signal(opp):
    """Map a get_top_opportunity() dict onto the llm_advisor Signal contract.

    `qty` is the USD-notional CEILING (the human's max buy button); the LLM can
    only veto or reduce it. symbol/side come only from here, never the model.
    """
    return {
        "id": opp["symbol"],          # one signal per symbol per run
        "symbol": opp["symbol"],
        "side": "buy",                # buy_watcher only emits entries
        "qty": ADVISOR_SIGNAL_MAX_USD,
        "bot_score": opp.get("firing_count", 0),
        "context": {                  # TRUSTED numeric facts from our own APIs
            "price": opp.get("price"),
            "day_change_pct": opp.get("day_change_pct"),
            "firing_bots": opp.get("firing_bots"),
            "indicators": opp.get("indicators"),
            "levels": opp.get("levels"),
        },
    }


def fetch_untrusted_context(symbol):
    """External/untrusted data (news) for `symbol`, via Finnhub. Fenced +
    sanitised downstream; bounded by the subtract-only validator. Returns [] on
    any failure (no key, network, rate-limit) — see shared/news_feed.py."""
    return news_feed.fetch_news(symbol)


def run(asset_class="crypto", top_n=8):
    opps = advisor.get_top_opportunity(top_n=top_n, asset_class=asset_class)
    if not opps or (isinstance(opps[0], dict) and "error" in opps[0]):
        msg = opps[0].get("error") if opps else "no opportunities"
        print(f"[shadow] no candidates ({asset_class}): {msg}")
        return

    # SUBTRACT-ONLY AT THE SET LEVEL: the advisor may only prune signals the bots
    # actually FIRED (firing >= the buy_watcher alert threshold). Sub-threshold
    # candidates are NOT signals — feeding them would let the LLM originate a
    # trade the bots never produced. They never reach the advisor.
    actionable = [o for o in opps if o.get("firing_count", 0) >= BOT_ALERT_MIN_FIRING]
    if not actionable:
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "asset": asset_class, "model": llm_advisor.ADVISOR_MODEL,
            "n_candidates": len(opps), "n_actionable": 0, "candidates": [],
            "top_firing": max((o.get("firing_count", 0) for o in opps), default=0),
        }
        _append_log(record)
        print(f"[shadow] {asset_class} — 0 bot-fired signals "
              f"(top firing={record['top_firing']}/9, threshold {BOT_ALERT_MIN_FIRING}); "
              f"nothing for the advisor to filter. (NO trades, NO alerts)")
        print(f"[shadow] logged -> {SHADOW_LOG}")
        return

    signals = [opportunity_to_signal(o) for o in actionable]
    untrusted = []
    for s in signals:
        untrusted.extend(fetch_untrusted_context(s["symbol"]))

    # The LLM advisor: veto/approve/rank (fails closed → [] on any error).
    approved = llm_advisor.advise(signals, untrusted_context=untrusted)
    approved_ids = {a["id"]: a for a in approved}

    # Deterministic caps, fed from the advisor PATH's own ledger (its own
    # positions/P&L/trades — not the bots' broker positions). 0/0/0 until the
    # path executes, which is correct for shadow, not a placeholder.
    open_pos, realized_pnl, trades_today = advisor_path_state.get_state()
    capped = llm_advisor_caps.apply_caps(
        approved, open_positions=open_pos, daily_realized_pnl=realized_pnl,
        trades_today=trades_today,
    )
    allowed_ids = {a["id"] for a in capped["allowed"]}
    rejected_by_caps = {r["id"]: r["reason"] for r in capped["rejected"]}

    candidates = []
    for o in actionable:
        sym = o["symbol"]
        firing = o.get("firing_count", 0)
        bot_alert = firing >= BOT_ALERT_MIN_FIRING
        llm_v = approved_ids.get(sym)
        candidates.append({
            "symbol": sym,
            "firing_count": firing,
            "bot_would_alert": bot_alert,
            "llm_decision": "approve" if llm_v else "veto",
            "llm_rank": llm_v.get("rank") if llm_v else None,
            "llm_qty": llm_v.get("qty") if llm_v else None,
            "caps_result": ("allowed" if sym in allowed_ids
                            else f"rejected:{rejected_by_caps.get(sym, 'vetoed_upstream')}"),
            # The headline disagreement we care about: bot says trade, LLM vetoes.
            "llm_overrides_bot": bot_alert and not llm_v,
        })

    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "asset": asset_class,
        "model": llm_advisor.ADVISOR_MODEL,
        "breaker_tripped": capped["breaker_tripped"],
        "n_candidates": len(opps),
        "n_actionable": len(actionable),
        "candidates": candidates,
    }
    _append_log(record)
    _print_summary(record)


def _append_log(record):
    try:
        os.makedirs(os.path.dirname(SHADOW_LOG), exist_ok=True)
        with open(SHADOW_LOG, "a") as f:
            f.write(json.dumps(record, default=str) + "\n")
    except OSError as e:
        print(f"[shadow] log write failed: {e}", file=sys.stderr)


def _print_summary(record):
    print(f"[shadow] {record['asset']} — {record['n_candidates']} candidates "
          f"via {record['model']}  (NO trades, NO alerts)")
    for c in record["candidates"]:
        flag = "  <-- LLM VETOES a bot-alert signal" if c["llm_overrides_bot"] else ""
        print(f"  {c['symbol']:<10} firing={c['firing_count']} "
              f"bot_alert={c['bot_would_alert']!s:<5} "
              f"llm={c['llm_decision']:<7} caps={c['caps_result']}{flag}")
    print(f"[shadow] logged -> {SHADOW_LOG}")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--asset", default="crypto", choices=["crypto", "stock"])
    p.add_argument("--top", type=int, default=8)
    args = p.parse_args()
    run(asset_class=args.asset, top_n=args.top)


if __name__ == "__main__":
    main()
