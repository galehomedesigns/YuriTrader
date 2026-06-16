#!/usr/bin/env python3
"""Pre-open go/no-go check for the Opening Power live (CDP) trading path.

Runs ~1 hour before the open and Telegrams ONE consolidated status so nothing is
discovered at 9:32. Checks the three things that actually stop a live morning:

  1. IBKR quote feed authenticated  — real API managedAccounts/NetLiquidation call
     (not just a port being open). This is the only IBKR use now: market data for
     the classifier. A wedged "Connecting to server" session fails this.
  2. 2FA push pending               — if the gateway is NOT authenticated, reads the
     gateway container log to tell you WHY: a Second Factor dialog waiting for your
     tap (approve it on IBKR Mobile) vs. just down (needs a recreate).
  3. CDP order path up (port 9225)  — the laptop trading Chrome + reverse tunnel,
     the ONLY browser that routes orders to Questrade. Down = no orders can stage.
     (Start it with laptop/start_trading_browser.ps1.)

Reports green or a red action-list. Read-only — never recreates or trades.

    preopen_check.py            # check + Telegram
    preopen_check.py --print    # check + stdout only (no Telegram)
"""
import os
import signal
import socket
import subprocess
import sys
from datetime import datetime
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
PORT = int(os.environ.get("IBKR_PORT", "4001"))
CDP_PORT = int(os.environ.get("OPENING_TV_CDP_PORT", "9225"))


def _et():
    return datetime.now(ZoneInfo("America/New_York"))


class _Timeout(Exception):
    pass


def gateway_authenticated():
    """True iff the live session delivers managedAccounts + NetLiquidation within
    ~20s — the same functional probe the healthcheck uses."""
    signal.signal(signal.SIGALRM, lambda *_: (_ for _ in ()).throw(_Timeout()))
    signal.alarm(22)
    ib = None
    try:
        from ib_async import IB, util
        util.patchAsyncio()
        ib = IB()
        ib.connect(os.environ.get("IBKR_HOST", "127.0.0.1"), PORT,
                   clientId=int(os.environ.get("PREOPEN_CHECK_CLIENT_ID", "93")),
                   timeout=15)
        accts = ib.managedAccounts()
        summ = {v.tag: v.value for v in ib.accountSummary(accts[0])} if accts else {}
        return bool(accts) and summ.get("NetLiquidation") is not None
    except Exception:                          # noqa: BLE001 — any failure = unhealthy
        return False
    finally:
        signal.alarm(0)
        try:
            if ib:
                ib.disconnect()
        except Exception:
            pass


def twofa_pending():
    """True if the most recent 'Second Factor Authentication; event=Opened' in the
    gateway log is newer than the most recent 'Login has completed' — i.e. a push
    is waiting for your tap. None if the log can't be read."""
    try:
        out = subprocess.run(["docker", "logs", "--tail", "600", "ib-gateway"],
                             capture_output=True, text=True, timeout=20)
        lines = (out.stdout + "\n" + out.stderr).splitlines()
    except Exception:                          # noqa: BLE001
        return None
    last_open_i = last_done_i = -1
    for i, ln in enumerate(lines):
        if "Second Factor Authentication" in ln and "event=Opened" in ln:
            last_open_i = i
        elif "Login has completed" in ln:
            last_done_i = i
    if last_open_i == -1:
        return False
    return last_open_i > last_done_i


def port_up(port):
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=3):
            return True
    except OSError:
        return False


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
    auth = gateway_authenticated()
    cdp = port_up(CDP_PORT)

    problems = []
    oks = []

    if auth:
        oks.append("✅ IBKR quote feed authenticated (classifier can read bars)")
    else:
        pend = twofa_pending()
        if pend is True:
            problems.append("🔴 IBKR gateway NOT logged in — a <b>2FA push is waiting</b>. "
                            "Approve it on <b>IBKR Mobile</b> now.")
        elif pend is False:
            problems.append("🔴 IBKR gateway NOT logged in and no 2FA pending — it likely "
                            "needs a recreate (no quote feed = no trades).")
        else:  # None — couldn't read the log
            problems.append("🔴 IBKR gateway NOT delivering data (couldn't read the gateway "
                            "log to tell if a 2FA is pending — check IBKR Mobile / the gateway).")

    if cdp:
        oks.append(f"✅ CDP order path up (laptop Chrome on :{CDP_PORT})")
    else:
        problems.append(f"🔴 CDP order path DOWN (:{CDP_PORT}) — orders can't stage. "
                        "Start <b>start_trading_browser.ps1</b> on the laptop and confirm "
                        "TradingView is open, Questrade connected, sole TV login.")

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
    print(f"[preopen] {et:%H:%M ET} auth={auth} cdp={cdp} problems={len(problems)}")


if __name__ == "__main__":
    main()
