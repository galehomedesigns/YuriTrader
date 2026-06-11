#!/usr/bin/env python3
"""Auto-healthcheck for the IB Gateway live session.

The gnzsnz gateway sometimes wedges on "Connecting to server..." after IBKR's
overnight/daily reset — it accepts the socket but never delivers account data, so
the scanner hangs. This probes the session functionally (can it pull account data
within ~20s?); if it's wedged on two consecutive strikes, it recreates the gateway
and pings YuriStocks to approve the fresh 2FA (the only step that can't be
automated — IBKR requires your tap).

Guards against restart-spam:
  - cooldown: no second recreate within GATEWAY_HC_COOLDOWN_MIN (default 12 min)
  - auto-restart skip: stands down ~06:55-07:15 ET so it doesn't collide with the
    gnzsnz daily auto-restart.

    gateway_healthcheck.py            # probe; recreate if wedged
    gateway_healthcheck.py --check    # probe + print only, never recreate
"""
import os
import signal
import subprocess
import sys
import time
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
COOLDOWN_MIN = float(os.environ.get("GATEWAY_HC_COOLDOWN_MIN", "12"))
TS_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "logs", "gateway_last_recreate.ts")


def _et():
    return datetime.now(ZoneInfo("America/New_York"))


class _Timeout(Exception):
    pass


def probe():
    """True iff the live session delivers managedAccounts + NetLiquidation within
    ~20s. A wedged 'Connecting to server' session fails this."""
    signal.signal(signal.SIGALRM, lambda *_: (_ for _ in ()).throw(_Timeout()))
    signal.alarm(22)
    ib = None
    try:
        from ib_async import IB, util
        util.patchAsyncio()
        ib = IB()
        ib.connect(os.environ.get("IBKR_HOST", "127.0.0.1"), PORT,
                   clientId=int(os.environ.get("GATEWAY_HC_CLIENT_ID", "29")),
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


def _recently_recreated():
    try:
        return (time.time() - float(open(TS_FILE).read().strip())) < COOLDOWN_MIN * 60
    except (OSError, ValueError):
        return False


def _recreate():
    subprocess.run(
        ["docker", "compose", "--env-file", "/home/tonygale/openclaw/.env",
         "up", "-d", "--force-recreate"],
        cwd="/home/tonygale/openclaw/infra/ib-gateway",
        capture_output=True, timeout=120)
    os.makedirs(os.path.dirname(TS_FILE), exist_ok=True)
    open(TS_FILE, "w").write(str(time.time()))


def _tg(msg):
    import json
    import urllib.parse
    import urllib.request
    tok = os.environ.get("TELEGRAM_STOCK_BOT_TOKEN", "")
    if not tok:
        return
    data = urllib.parse.urlencode({
        "chat_id": os.environ.get("TELEGRAM_CHAT_ID", "6545739863"),
        "text": msg, "parse_mode": "HTML"}).encode()
    try:
        urllib.request.urlopen(urllib.request.Request(
            f"https://api.telegram.org/bot{tok}/sendMessage", data=data), timeout=20)
    except Exception:
        pass


def main():
    check_only = "--check" in sys.argv
    et = _et()
    if (et.hour == 6 and et.minute >= 55) or (et.hour == 7 and et.minute <= 15):
        print("[hc] in daily auto-restart window — standing down."); return

    healthy = probe()
    if not healthy:
        time.sleep(20)
        healthy = probe()                      # second strike filters transient blips
    if healthy:
        print(f"[hc] {et:%H:%M ET} healthy."); return

    if check_only:
        print(f"[hc] {et:%H:%M ET} WEDGED (--check: not recreating)."); return
    if _recently_recreated():
        print(f"[hc] {et:%H:%M ET} wedged but within cooldown — skipping."); return

    print(f"[hc] {et:%H:%M ET} WEDGED — recreating gateway.")
    _recreate()
    _tg("⚠️ <b>IB Gateway wedged</b> — auto-recreated. <b>Approve the 2FA push</b> "
        "on IBKR Mobile so the scans and trading can reconnect.")


if __name__ == "__main__":
    main()
