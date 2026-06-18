#!/usr/bin/env python3
"""Opening Power — pre-market scan runner + YuriStocks delivery.

Runs the funnel (movers -> 2-min deep-eval -> rank), formats the top 10
best->worst match, pushes it to the YuriStocks Telegram bot, and caches the
result so the on-demand /opening command can return the freshest list without
re-scanning inside the daemon.

signal_only: this NEVER places an order. It is analysis + notification.

Schedule: hourly across the pre-market window (gated in-code to ET, like
buy_watcher). On-demand: /opening in stock_concierge reads the cache this writes.

    .venv/bin/python skills/trading-arena/opening_agent/run_opening_scan.py
    flags: --force (ignore the pre-market window)  --no-send (print only)
"""
import argparse
import json
import os
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from zoneinfo import ZoneInfo


def _load_env():
    p = "/home/tonygale/openclaw/.env"
    if not os.path.exists(p):
        return
    for line in open(p):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            if k and v:
                os.environ.setdefault(k, v)


_load_env()
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from opening_agent import universe, ranker, tv_watchlist

STOCK_BOT_TOKEN = os.environ.get("TELEGRAM_STOCK_BOT_TOKEN", "")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "6545739863")
CACHE = os.environ.get(
    "OPENING_SCAN_CACHE",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                 "logs", "opening_scan_latest.json"),
)
# Pre-market window (ET), inclusive start .. exclusive end. Spec: scan 9:00, final
# pass 9:29; user wants hourly from earlier. Default 07:00-09:30 ET, weekdays.
WIN_START = int(os.environ.get("OPENING_WINDOW_START_ET", "7"))
WIN_END_H = int(os.environ.get("OPENING_WINDOW_END_HOUR_ET", "9"))
WIN_END_M = int(os.environ.get("OPENING_WINDOW_END_MIN_ET", "30"))
# Total $ you intend to deploy across the morning's matches (even-split, whole
# shares — see live_executor.plan_allocations). Used only to FLAG qualifiers the
# budget can't buy a single share of, so a pricey lone qualifier isn't silently
# un-tradeable. Does not gate anything.
TRADE_BUDGET = float(os.environ.get("OPENING_TRADE_BUDGET_USD", "500") or 500)


def in_premarket_window():
    now = datetime.now(ZoneInfo("America/New_York"))
    if now.weekday() >= 5:
        return False
    mins = now.hour * 60 + now.minute
    return WIN_START * 60 <= mins < WIN_END_H * 60 + WIN_END_M


def _tg_send_one(text):
    data = urllib.parse.urlencode({
        "chat_id": CHAT_ID, "text": text, "parse_mode": "HTML",
        "disable_web_page_preview": "true",
    }).encode()
    url = f"https://api.telegram.org/bot{STOCK_BOT_TOKEN}/sendMessage"
    try:
        with urllib.request.urlopen(urllib.request.Request(url, data=data), timeout=30) as r:
            return json.loads(r.read()).get("ok", False)
    except Exception as e:                   # noqa: BLE001
        print(f"  [opening] TG send error: {e}", file=sys.stderr)
        return False


def _chunk(text, limit=3900):
    """Split on newline boundaries so each chunk stays under Telegram's 4096
    cap with complete (HTML-valid) lines. The funnel digest routinely exceeds
    the cap once the universe is large, which silently 400s a single send."""
    chunks, cur = [], ""
    for line in text.split("\n"):
        if cur and len(cur) + 1 + len(line) > limit:
            chunks.append(cur)
            cur = line
        else:
            cur = f"{cur}\n{line}" if cur else line
    if cur:
        chunks.append(cur)
    return chunks


def send_message(text):
    if not STOCK_BOT_TOKEN:
        print("  [opening] no TELEGRAM_STOCK_BOT_TOKEN — skipping send", file=sys.stderr)
        return False
    return all(_tg_send_one(c) for c in _chunk(text))


def _emoji(direction):
    return {"LONG": "🟢", "SHORT": "🔴", "WATCH": "🟡"}.get(direction, "⚪")


def format_message(ranked, et_now):
    if not ranked:
        return ("📊 <b>Opening Power — pre-market</b>\n"
                f"<i>{et_now}</i>\n\nNo qualifying setups right now "
                "(no directional movers in a TIGHT state).")
    def _unaffordable(r):
        p = r.get("price") or 0
        return p > 0 and TRADE_BUDGET < p

    # Coiled qualifiers (the ones the 9:30 first-bar rule can actually act on).
    # If EVERY one of them costs more than the whole budget, the morning would
    # stage nothing — call that out loudly at the top so it isn't a silent no-op.
    coiled = [r for r in ranked if r.get("state") in ("TIGHT", "MATCH")]
    lines = [f"📊 <b>Opening Power — Top {len(ranked)}</b>  <i>{et_now} ET</i>",
             "<i>Best→worst match. signal_only — no orders.</i>", ""]
    if coiled and all(_unaffordable(r) for r in coiled):
        cheapest = min((r.get("price") or 0) for r in coiled)
        lines.append(
            f"⚠️ <b>Budget too small:</b> all {len(coiled)} coiled qualifier(s) "
            f"cost more than your ${TRADE_BUDGET:.0f} budget "
            f"(cheapest ${cheapest:.2f}/share). None will be tradeable — raise "
            f"OPENING_TRADE_BUDGET_USD or expect no order.\n")
    for r in ranked:
        k = r["kpis"]
        kbits = []
        if k.get("rsi") is not None:
            kbits.append(f"RSI {k['rsi']:.0f}")
        if k.get("adx") is not None:
            kbits.append(f"ADX {k['adx']:.0f}")
        if k.get("rvol") is not None:
            kbits.append(f"RVOL {k['rvol']:.1f}x")
        nadj = r.get("news_adj", 0)
        news_bit = f"  📰 {nadj:+.0f}" if nadj else ""
        aff_bit = (f"  ⚠️ ${TRADE_BUDGET:.0f}&lt;1 share @ ${r['price']:.2f}"
                   if _unaffordable(r) else "")
        lines.append(
            f"{_emoji(r['direction'])} <b>#{r['rank']} {r['symbol']}</b> "
            f"— <b>{r['score']}</b>  ({r['direction']}){aff_bit}\n"
            f"   {r['state']}/{r['location']}  tight {r['tightness']}  "
            f"gap {r['pct_change']:+.1f}%  {'·'.join(r['power'])}\n"
            f"   {' · '.join(kbits) if kbits else 'KPIs n/a'}{news_bit}"
        )
    lines.append("\n<i>/opening for the latest anytime.</i>")
    return "\n".join(lines)


def run(force=False, send=True):
    et_now = datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m-%d %H:%M")
    if not force and not in_premarket_window():
        print(f"[opening] outside pre-market window ({et_now} ET) — skipping.")
        return
    candidates = universe.scan()
    # No pre-2-min-bar cap by default: stage the FULL pre-qualified universe so the
    # 9:30 first-bar rule (advisory_monitor) decides — don't trim before the bar.
    # OPENING_SCAN_TOP_N can still cap the displayed/cached list if ever wanted.
    _top_n = os.environ.get("OPENING_SCAN_TOP_N", "").strip()
    # News nudge (bounded, NON-governing): a fresh company-specific catalyst gets a
    # slightly higher priority, a bearish flag slightly lower — capped at
    # ±OPENING_NEWS_FACTOR points (default 5). Set 0 to disable. Fail-safe to neutral.
    news_factor = float(os.environ.get("OPENING_NEWS_FACTOR", "5") or 0)
    news = {}
    if news_factor:
        try:
            from opening_agent import news_sentiment
            news = news_sentiment.batch([c.symbol for c in candidates])
        except Exception as e:
            print(f"[opening] news sentiment skipped: {e}")
    ranked = ranker.rank(candidates, top_n=int(_top_n) if _top_n else None,
                         news=news, news_factor=news_factor)

    record = {"ts_utc": datetime.now(timezone.utc).isoformat(), "et": et_now,
              "ranked": ranked}
    try:
        os.makedirs(os.path.dirname(CACHE), exist_ok=True)
        with open(CACHE, "w") as f:
            json.dump(record, f, default=str)
        # Daily watchlist CSV — refreshed each run, TWS-importable (File ->
        # Import -> Watchlist). One row per candidate, best->worst.
        wl = os.path.join(os.path.dirname(CACHE), "opening_watchlist.csv")
        with open(wl, "w") as f:
            f.write("Symbol,Direction,Score,State,Gap%,Updated\n")
            for r in ranked:
                f.write(f"{r['symbol']},{r['direction']},{r['score']},"
                        f"{r['state']},{r.get('pct_change', 0)},{et_now}\n")
    except OSError as e:
        print(f"[opening] cache/watchlist write failed: {e}", file=sys.stderr)

    msg = format_message(ranked, et_now)
    print(msg)
    if send:
        ok = send_message(msg)
        print(f"[opening] sent={ok} | cached -> {CACHE}")
        # Mirror the ranked list into the user's (single, free-tier) TradingView
        # watchlist so it syncs to their devices. Replace-entirely per the
        # 2026-06-13 decision. Non-fatal: never let a watchlist hiccup break the
        # scan/send. Auto-skips if TRADINGVIEW_SESSIONID is unset or disabled.
        if ranked and os.environ.get("OPENING_TV_WATCHLIST", "1") not in ("0", "false", ""):
            try:
                tv_watchlist.sync([r["symbol"] for r in ranked])
            except Exception as e:  # noqa: BLE001
                print(f"[opening] TV watchlist sync skipped: {e}", file=sys.stderr)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="ignore the pre-market window")
    ap.add_argument("--no-send", action="store_true", help="print only, no Telegram")
    a = ap.parse_args()
    run(force=a.force, send=not a.no_send)


if __name__ == "__main__":
    main()
