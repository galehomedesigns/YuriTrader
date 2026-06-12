#!/usr/bin/env python3
"""One-shot: is the live account permissioned to trade Canadian (TSX) stocks?

Uses IBKR's NON-TRANSMITTING whatIfOrder (no real order is placed). Runs each
morning until it gets a clean answer from a healthy gateway, Telegrams the result
to YuriStocks, then writes a done-flag and stops. Delete the flag to re-run:
    skills/trading-arena/logs/canada_permission_checked.flag

Why it matters: a CAD account can trade CAD-denominated TSX stocks with no 2,500
-CAD currency/margin wall — so if this is permissioned, the agent can be
retargeted to Canadian stocks and trade the existing ~$100 now.
"""
import os
import signal
import sys

DONE_FLAG = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "logs", "canada_permission_checked.flag")


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


class _Timeout(Exception):
    pass


def _whatif(ib, sym, ccy, primary):
    """Return (tradable: bool|None, detail). None = couldn't determine."""
    from ib_async import Stock, MarketOrder
    try:
        c = Stock(sym, "SMART", ccy, primaryExchange=primary) if primary \
            else Stock(sym, "SMART", ccy)
        if not ib.qualifyContracts(c):
            return None, "contract not found"
        st = ib.whatIfOrder(c, MarketOrder("BUY", 1))
        init = getattr(st, "initMarginChange", None)
        warn = (getattr(st, "warningText", "") or "").strip()
        bad = init in (None, "") or str(init).startswith("1.7976")
        return (not bad), (warn or f"initMargin={init}")
    except Exception as e:                     # noqa: BLE001 — error usually = not permitted
        return False, str(e)[:120]


def main():
    if os.path.exists(DONE_FLAG):
        print("[canada] already checked — skipping."); return

    signal.signal(signal.SIGALRM, lambda *_: (_ for _ in ()).throw(_Timeout()))
    signal.alarm(40)
    ib = None
    try:
        from ib_async import IB, util
        util.patchAsyncio()
        ib = IB()
        ib.connect(os.environ.get("IBKR_HOST", "127.0.0.1"),
                   int(os.environ.get("IBKR_PORT", "4001")),
                   clientId=int(os.environ.get("CANADA_CHECK_CLIENT_ID", "63")),
                   timeout=15)
        accts = ib.managedAccounts()
        summ = {v.tag: v.value for v in ib.accountSummary(accts[0])} if accts else {}
        if not accts or summ.get("NetLiquidation") is None:
            print("[canada] gateway not delivering data — retry next run."); return
        ca = {s: _whatif(ib, s, "CAD", "TSE") for s in ("RY", "ENB")}
        us = _whatif(ib, "F", "USD", None)
        signal.alarm(0)
    except _Timeout:
        print("[canada] gateway wedged (timeout) — retry next run."); return
    except Exception as e:                     # noqa: BLE001
        print(f"[canada] error: {e} — retry next run."); return
    finally:
        signal.alarm(0)
        try:
            if ib:
                ib.disconnect()
        except Exception:
            pass

    ca_ok = any(v[0] for v in ca.values())
    lines = ["🇨🇦 <b>Canadian trading permission check</b>", ""]
    for s, (ok, detail) in ca.items():
        mark = "✅" if ok else ("❓" if ok is None else "❌")
        lines.append(f"  {mark} {s}.TO — {detail}")
    lines.append(f"\n🇺🇸 F (US contrast): {'accepted' if us[0] else 'blocked/uncertain'} "
                 f"— {us[1]}")
    if ca_ok:
        lines.append("\n<b>TSX trading looks ACTIVE.</b> We can retarget the agent to "
                     "Canadian stocks and trade your ~$100 (no 2,500-CAD wall). Reply if "
                     "you want me to build that. <i>(what-if check — a real $1 test order "
                     "gives 100% certainty if you want it.)</i>")
    else:
        lines.append("\n<b>TSX trading NOT confirmed</b> by the what-if check. May need a "
                     "real test order to be sure, or the permission isn't active yet.")
    _tg("\n".join(lines))
    os.makedirs(os.path.dirname(DONE_FLAG), exist_ok=True)
    open(DONE_FLAG, "w").write("checked\n")
    print("[canada] result sent; done-flag written.")


if __name__ == "__main__":
    main()
