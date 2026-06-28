#!/usr/bin/env python3
"""family_bot.py — SEPARATE Telegram bot for the family remote-confirm pilot.

Sends a per-order Approve/Skip card for every pending order a follower staged, and
on Approve logs the bid to Mongo (Telegram.userBids via mongo_bids). DRY: nothing is
ever sent to a broker — Approve just records the bid and marks the order approved.

Isolation: its OWN BotFather token (TELEGRAM_FAMILY_BOT_TOKEN) and per-user chat ids;
it never reuses stock_concierge's token/chat guard. Chats are logged to
Telegram.telegramChat via the shared mongo_telegram writer (consistent with the
other 3 daemons).

Modes:
  send          — send Approve/Skip cards for all status=="pending" orders
  poll          — long-poll getUpdates and handle Approve/Skip callbacks
  run           — send once, then poll (the pilot daemon)
  sim-approve   — TOKEN-LESS: print each pending card and auto-Approve it (so the
                  Mongo logging path is testable before a real bot token exists)

Usage: family_bot.py <mode> [--user pilot] [--state <state_dir>]
"""
import argparse
import glob
import json
import os
import sys
import time
import urllib.parse
import urllib.request
import urllib.error

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.normpath(os.path.join(HERE, "..", "..", "..", "state"))
sys.path.insert(0, os.path.join(HERE, "..", "..", "shared"))   # skills/shared
import mongo_bids
try:
    import mongo_telegram
except Exception:
    mongo_telegram = None

BOT = "family_bot"


def load_env(path="/home/tonygale/openclaw/.env"):
    """Populate os.environ from .env (without overwriting already-set vars)."""
    try:
        for line in open(path):
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    except OSError:
        pass


# --------------------------------- Telegram ----------------------------------

def _api_base():
    return f"https://api.telegram.org/bot{os.environ.get('TELEGRAM_FAMILY_BOT_TOKEN','')}"


def tg(method, params=None, timeout=60):
    url = f"{_api_base()}/{method}"
    data = urllib.parse.urlencode(params or {}).encode() if params else None
    req = urllib.request.Request(url, data=data, method="POST" if data else "GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode())
            if payload.get("ok"):
                return payload.get("result")
            print(f"  TG API error: {payload.get('description')}", file=sys.stderr)
    except Exception as e:
        print(f"  TG error: {e}", file=sys.stderr)
    return None


def send_card(order):
    text = card_text(order)
    kb = [[{"text": "✅ Approve", "callback_data": f"A|{order['order_id']}"},
           {"text": "❌ Skip", "callback_data": f"S|{order['order_id']}"}]]
    res = tg("sendMessage", {"chat_id": order["chat_id"], "text": text, "parse_mode": "HTML",
                             "reply_markup": json.dumps({"inline_keyboard": kb})})
    if mongo_telegram:
        mongo_telegram.log_outbound(BOT, res, text=text, chat_id=order["chat_id"])
    return res


def card_text(o):
    return (f"⚡ <b>{o['symbol']}</b> — BUY {o['qty']} @ ${o['price']} STOP "
            f"(stop ${o['stop']})\n"
            f"slot ${o['slot_usd']:.0f} · notional ${o['notional_usd']:.2f} · DRY (no live send)\n"
            f"<i>Approve to record your bid, or Skip.</i>")


# ----------------------------- pending-order I/O ------------------------------

def pending_dir(user):
    return os.path.join(STATE, "remote_confirm", user, "pending")


def iter_pending(user, status=None):
    for p in sorted(glob.glob(os.path.join(pending_dir(user), "*.json"))):
        try:
            o = json.load(open(p))
        except Exception:
            continue
        if status is None or o.get("status") == status:
            yield p, o


def find_order(order_id):
    user = order_id.split(":", 1)[0]
    path = os.path.join(pending_dir(user), order_id.replace(":", "_") + ".json")
    if os.path.exists(path):
        return path, json.load(open(path))
    return None, None


def save_order(path, o):
    with open(path, "w") as f:
        json.dump(o, f, indent=2)


# -------------------------------- decisions ----------------------------------

def approve(order, via="telegram"):
    """Record the bid (Mongo) and mark the order approved. DRY — no broker send."""
    order["status"] = "approved"
    order["approved_via"] = via
    mongo_bids.log_bid(order["user"], order, action="approve")
    return order


def skip(order, via="telegram"):
    order["status"] = "skipped"
    order["approved_via"] = via
    mongo_bids.log_bid(order["user"], order, action="skip")  # amount fields still recorded, action=skip
    return order


# ----------------------------------- modes -----------------------------------

def mode_send(user):
    n = 0
    for path, o in iter_pending(user, status="pending"):
        res = send_card(o)
        if res:
            o["status"] = "sent"; o["sent_msg_id"] = res.get("message_id"); save_order(path, o)
            n += 1
            print(f"[family_bot] sent card: {o['symbol']} ({o['order_id']})")
    print(f"[family_bot] sent {n} card(s) for {user}")


def mode_poll(user, once=False):
    offset = None
    print("[family_bot] polling for Approve/Skip ... (Ctrl-C to stop)")
    while True:
        ups = tg("getUpdates", {"timeout": 25, **({"offset": offset} if offset else {})}, timeout=35) or []
        for up in ups:
            offset = up["update_id"] + 1
            if mongo_telegram:
                mongo_telegram.log_inbound(BOT, up)
            cq = up.get("callback_query")
            if not cq:
                continue
            data = cq.get("data", "")
            tg("answerCallbackQuery", {"callback_query_id": cq["id"], "text": "Got it"})
            if "|" not in data:
                continue
            act, oid = data.split("|", 1)
            path, o = find_order(oid)
            if not o:
                continue
            o = approve(o) if act == "A" else skip(o)
            save_order(path, o)
            verb = "APPROVED ✅ — bid recorded" if act == "A" else "Skipped ❌"
            msg = cq.get("message", {})
            tg("editMessageText", {"chat_id": msg.get("chat", {}).get("id"),
                                   "message_id": msg.get("message_id"),
                                   "text": card_text(o) + f"\n\n<b>{verb}</b>", "parse_mode": "HTML"})
            print(f"[family_bot] {o['symbol']}: {o['status']}")
        if once:
            break
        time.sleep(0.5)


def mode_sim_approve(user):
    """Token-less: print each pending card and auto-Approve, exercising the Mongo path."""
    n = 0
    for path, o in iter_pending(user):
        if o.get("status") in ("approved", "skipped"):
            continue
        print("─" * 48)
        print(card_text(o).replace("<b>", "").replace("</b>", "").replace("<i>", "").replace("</i>", ""))
        o = approve(o, via="sim")
        save_order(path, o)
        print(f"  -> APPROVED, bid logged to Mongo (order_id={o['order_id']})")
        n += 1
    print(f"[family_bot] sim-approved {n} order(s) for {user}")


def main():
    load_env()
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["send", "poll", "run", "sim-approve"])
    ap.add_argument("--user", default="pilot")
    a = ap.parse_args()
    if a.mode in ("send", "poll", "run") and not os.environ.get("TELEGRAM_FAMILY_BOT_TOKEN"):
        sys.exit("TELEGRAM_FAMILY_BOT_TOKEN not set — create a BotFather token, or use 'sim-approve'.")
    if a.mode == "send":
        mode_send(a.user)
    elif a.mode == "poll":
        mode_poll(a.user)
    elif a.mode == "run":
        mode_send(a.user); mode_poll(a.user)
    elif a.mode == "sim-approve":
        mode_sim_approve(a.user)


if __name__ == "__main__":
    main()
