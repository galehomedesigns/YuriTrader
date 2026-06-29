#!/usr/bin/env python3
"""Pre-open go/no-go check for the Opening Power live (CDP) trading path.

Runs ~1 hour before the open and Telegrams ONE consolidated status so nothing is
discovered at 9:32. IBKR is gone — the feed and orders are all TradingView now, so
this checks the three things that actually stop a live morning:

  1. CDP order path up (port 9225) — the laptop trading Chrome + reverse tunnel,
     the ONLY browser that routes orders to Questrade. Down = no orders can stage.
     (Start it with laptop/start_trading_browser.ps1.)
  2. TradingView real-time bars     — pulls live 2-min bars for a liquid test name
     off the data tab via CDP. Confirms the upgraded TV feed is serving the
     classifier (the data tab + real-time data both work).
  3. TradingView screener           — the pre-market mover list (public endpoint).

Reports green or a red action-list. Read-only — never trades.

    preopen_check.py            # check + Telegram
    preopen_check.py --print    # check + stdout only (no Telegram)
"""
import os
import socket
import sys
from datetime import datetime
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
CDP_PORT = int(os.environ.get("OPENING_TV_CDP_PORT", "9225"))
TEST_SYMBOL = os.environ.get("OPENING_PREOPEN_TEST_SYMBOL", "NASDAQ:AAPL")


def _et():
    return datetime.now(ZoneInfo("America/New_York"))


def port_up(port):
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=3):
            return True
    except OSError:
        return False


def tv_bars_ok():
    """(ok, detail) — can we pull real-time 2-min bars off the TV data tab?"""
    try:
        from opening_agent import tv_bars
        bars = tv_bars.fetch_one(TEST_SYMBOL, min_bars=200)
        if len(bars) >= 200:
            return True, f"{len(bars)} bars, last {bars[-1]['close']}"
        return False, f"only {len(bars)} bars (<200)"
    except Exception as e:                         # noqa: BLE001
        return False, str(e)[:80]


def broker_ok():
    """(ok, detail) — is the Questrade<->TradingView broker link actually connected
    on the trading tab? Port-up only proves the browser/tunnel is reachable; this
    proves orders can route (the link that dropped at the open 2026-06-16)."""
    import json
    import subprocess
    here = os.path.dirname(os.path.abspath(__file__))
    try:
        out = subprocess.run(["node", os.path.join(here, "tv_broker_health.js"), "--port", str(CDP_PORT)],
                             capture_output=True, text=True, timeout=30)
        data = json.loads(out.stdout or "{}")
        return bool(data.get("connected")), (data.get("detail") or "unknown")
    except Exception as e:                             # noqa: BLE001
        return False, str(e)[:80]


def stage_selftest_ok():
    """(ok, detail) — CANARY: can the order-staging DOM path actually LOCATE the
    order-type tab + stop-loss section + submit button right now? Runs the same
    finders staging uses (never submits). Catches the cold-ticket innerText drop /
    a TradingView ticket-DOM change BEFORE 9:30, instead of when a live setup fires
    (the recurring failure that cost trades 2026-06-22)."""
    import json
    import subprocess
    here = os.path.dirname(os.path.abspath(__file__))
    try:
        out = subprocess.run(["node", os.path.join(here, "tv_stage_selftest.js"), "--port", str(CDP_PORT)],
                             capture_output=True, text=True, timeout=45)
        lines = [l for l in (out.stdout or "").splitlines() if l.strip().startswith("{")]
        data = json.loads(lines[-1]) if lines else {}
        return bool(data.get("ok")), (data.get("detail") or "unknown")
    except Exception as e:                             # noqa: BLE001
        return False, str(e)[:80]


def screener_ok():
    """(ok, detail) — does the TradingView pre-market scanner respond?"""
    try:
        from opening_agent import tv_screener
        rows = tv_screener.movers(limit=3)
        return (len(rows) > 0), f"{len(rows)} movers"
    except Exception as e:                         # noqa: BLE001
        return False, str(e)[:80]


def ibkr_ok():
    """(ok, detail) or (None, _) if disabled — is the IBKR auto-exec path ready?
    Runs the isolated canary as a SUBPROCESS with a timeout, so a stuck gateway (e.g.
    a competing login froze it at 'Existing session detected') can't hang this check;
    the timeout becomes an unhealthy verdict. Only meaningful when OPENING_IBKR_EXEC=1."""
    if os.environ.get("OPENING_IBKR_EXEC", "0").lower() not in ("1", "true", "yes"):
        return None, "disabled"
    import json
    import subprocess
    here = os.path.dirname(os.path.abspath(__file__))
    script = os.path.join(os.path.dirname(here), "ibkr_exec", "health_check.py")
    try:
        out = subprocess.run([sys.executable, script], capture_output=True, text=True, timeout=35)
        lines = [l for l in (out.stdout or "").splitlines() if l.strip().startswith("{")]
        data = json.loads(lines[-1]) if lines else {}
        return bool(data.get("ok")), (data.get("detail") or "no result")
    except subprocess.TimeoutExpired:
        return False, ("gateway not responding (likely stuck on a competing session) — "
                       "restart ib-gateway (needs a 2FA tap)")
    except Exception as e:                         # noqa: BLE001
        return False, str(e)[:90]


def _tg(msg):
    import urllib.parse
    import urllib.request
    tok = os.environ.get("TELEGRAM_STOCK_BOT_TOKEN", "")
    if not tok:
        print(msg); return
    data = urllib.parse.urlencode({
        "chat_id": os.environ.get("TELEGRAM_CHAT_ID", "6545739863"),
        "text": msg, "parse_mode": "HTML"}).encode()
    try:
        urllib.request.urlopen(urllib.request.Request(
            f"https://api.telegram.org/bot{tok}/sendMessage", data=data), timeout=20)
    except Exception:
        pass


def main():
    send = "--print" not in sys.argv
    et = _et()
    cdp = port_up(CDP_PORT)

    problems, oks = [], []

    if cdp:
        oks.append(f"✅ CDP order path up (laptop Chrome on :{CDP_PORT})")
    else:
        problems.append(f"🔴 CDP order path DOWN (:{CDP_PORT}) — orders can't stage AND the "
                        "bar feed can't read. Start <b>start_trading_browser.ps1</b> on the "
                        "laptop; confirm TradingView is open, Questrade connected, sole TV login.")

    # The bar feed needs the CDP browser, so only test it if the port is up.
    if cdp:
        ok, detail = tv_bars_ok()
        (oks if ok else problems).append(
            ("✅ TradingView real-time bars OK" if ok else "🔴 TradingView bars FAILED") + f" ({detail})")

        bok, bdetail = broker_ok()
        if bok:
            oks.append(f"✅ Questrade broker link connected ({bdetail})")
        else:
            problems.append(f"🔴 Questrade broker link DOWN ({bdetail}) — orders can't route. On the "
                            "laptop TradingView tab, open the bottom Trading Panel → broker dropdown → "
                            "reconnect <b>Questrade</b>, and keep that tab the sole TV login.")

        # Order-staging canary — only meaningful with the broker connected (the
        # ticket needs a live broker to render the order-type tabs). Catches the
        # cold-ticket finder failure before the open instead of at 9:32.
        if bok:
            stok, stdetail = stage_selftest_ok()
            if stok:
                oks.append(f"✅ Order-staging path verified ({stdetail})")
            else:
                problems.append(f"🔴 Order-staging path BROKEN ({stdetail}) — a real setup would FAIL to "
                                "stage at 9:30. Re-warm the trading tab (reopen the chart + order ticket) "
                                "and re-run this check; if it persists, the TradingView ticket DOM changed "
                                "and tv_order*.js finders need updating.")

    sok, sdetail = screener_ok()
    (oks if sok else problems).append(
        ("✅ TradingView screener OK" if sok else "🔴 TradingView screener FAILED") + f" ({sdetail})")

    # IBKR auto-exec path (independent of CDP/Questrade). Only reported when enabled.
    iok, idetail = ibkr_ok()
    if iok is True:
        oks.append(f"✅ IBKR auto-exec ready ({idetail})")
    elif iok is False:
        problems.append(f"🔴 IBKR auto-exec DOWN ({idetail}) — autonomous orders WON'T place. "
                        "Make sure the gateway holds the SOLE session (close TV↔IBKR and the "
                        "IBKR app), and restart ib-gateway if it's stuck (needs a 2FA tap).")

    head = f"🌅 <b>Opening Power pre-open check</b> — {et:%H:%M ET}"
    if problems:
        body = "\n".join(problems) + ("\n\n" + "\n".join(oks) if oks else "")
        msg = f"{head}\n\n<b>ACTION NEEDED:</b>\n{body}"
    else:
        msg = f"{head}\n\n<b>All green — ready for the open.</b>\n" + "\n".join(oks)
    if send:
        _tg(msg)
    else:
        print(msg)
    print(f"[preopen] {et:%H:%M ET} cdp={cdp} problems={len(problems)}")


if __name__ == "__main__":
    main()
