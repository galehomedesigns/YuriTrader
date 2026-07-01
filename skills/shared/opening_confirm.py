"""Opening-agent REMOTE order confirmation — Telegram ✅ tap → the CDP runner sends.

A second, parallel confirm path beside the manual TradingView "Send Order" click:
tap ✅ Approve in Telegram and the staged ENTRY order sends (for "phone, no laptop").
Whichever path acts first wins; the laptop Send Order stays as the fallback.

IPC = one sidecar file per order: logs/opening_confirm/<order_id>.json
  {status, nonce, symbol, side, qty, entry, stop, ts, status_ts}
  status: pending → approve | skip | sent | mismatch | timeout
- The concierge (on a tap) sets approve/skip here (this module's handle_callback).
- The CDP runner (tv_order_queue.js) reads it, does a PRE-CLICK READBACK of the live
  ticket, and only then clicks Send Order; it writes sent/mismatch back.

Safety: a tap is INTENT; the runner's readback is CORRECTNESS. Two taps are required
(Approve → "Yes, SEND"), each card carries the per-staging NONCE, and a stale card
(nonce mismatch) is refused. Entries-only v1 (fixed-price buy-stops). Gated by
OPENING_REMOTE_CONFIRM (default off). ZERO import-time side effects — safe to import
into the long-running concierge.
"""
import datetime
import json
import os
import secrets
import sys
import time
import urllib.parse
import urllib.request

# logs/opening_confirm under skills/trading-arena (matches advisory_monitor's logs dir)
_SKILLS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIRM_DIR = os.path.join(_SKILLS, "trading-arena", "logs", "opening_confirm")


# ── sidecar IPC (atomic) ──────────────────────────────────────────────────────
def path(order_id):
    return os.path.join(CONFIRM_DIR, f"{order_id}.json")


def read(order_id):
    """Return the sidecar dict, or None on any error (missing / mid-write / corrupt)."""
    try:
        with open(path(order_id)) as f:
            return json.load(f)
    except Exception:
        return None


def write(order_id, data):
    """Atomic write (temp + os.replace) so a reader never sees a half-file."""
    os.makedirs(CONFIRM_DIR, exist_ok=True)
    p = path(order_id)
    tmp = f"{p}.{os.getpid()}.tmp"
    with open(tmp, "w") as f:
        json.dump(data, f)
    os.replace(tmp, p)


def update_status(order_id, status, expect_nonce=None, extra=None):
    """Set status iff the sidecar exists and (optionally) the nonce matches. Returns
    the new dict, or None if missing/stale (so a stale card can't flip a fresh order)."""
    d = read(order_id)
    if d is None:
        return None
    if expect_nonce is not None and str(d.get("nonce")) != str(expect_nonce):
        return None
    d["status"] = status
    d["status_ts"] = time.time()
    if extra:
        d.update(extra)
    write(order_id, d)
    return d


# ── identity ──────────────────────────────────────────────────────────────────
def ticker(sym):
    return str(sym).split(":")[-1].upper()


def make_order_id(symbol, index, day=None):
    d = (day or datetime.date.today()).strftime("%Y%m%d")
    return f"{d}-{ticker(symbol)}-{index}"


def make_nonce():
    return secrets.token_hex(4)


def enabled():
    return os.environ.get("OPENING_REMOTE_CONFIRM", "false").lower() == "true"


# ── shared entry-order gate (single source of truth for EVERY staging path) ────
def entry_gate_params():
    """(max_risk_pct, min_bar_range) for the entry gate — env-driven; defaults match
    advisory_monitor's historical inline cap."""
    return (float(os.environ.get("OPENING_MAX_RISK_PCT", "3.0")),
            float(os.environ.get("OPENING_MIN_BAR_RANGE", "0.05")))


def validate_entry(entry, stop):
    """Order-level gate enforced by EVERY path that can mint a sendable entry card —
    the auto-arm path (advisory_monitor._stage_entries) AND any direct/manual/remote
    stage() call. Returns (ok, reason). Long buy-stop entries only (stop below entry).
    Co-locates the risk cap with the thing that creates the confirmable order, so a
    card can never be minted for an over-cap order regardless of caller."""
    if not entry or entry <= 0:
        return False, "no entry level"
    if stop is None:
        return False, "no stop level"
    spread = entry - stop
    if spread <= 0:
        return False, f"stop ${stop:.2f} not below entry ${entry:.2f}"
    max_risk, min_range = entry_gate_params()
    risk_pct = spread / entry * 100
    if risk_pct > max_risk:
        return False, f"risk {risk_pct:.1f}% > {max_risk}% cap (entry ${entry:.2f}, stop ${stop:.2f})"
    if spread < min_range:
        return False, f"bar range ${spread:.2f} < ${min_range:.2f} min"
    return True, None


# ── Telegram (read token at CALL time; never at import) ───────────────────────
def _token():
    return os.environ.get("TELEGRAM_STOCK_BOT_TOKEN", "")


def _chat():
    return os.environ.get("TELEGRAM_CHAT_ID", "6545739863")


def _api(method, params):
    tok = _token()
    if not tok:
        return None
    try:
        data = urllib.parse.urlencode(params).encode()
        req = urllib.request.Request(f"https://api.telegram.org/bot{tok}/{method}", data=data)
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.load(r)
    except Exception as e:                                            # noqa: BLE001
        print(f"[opening_confirm] {method} failed: {e}", file=sys.stderr)
        return None


def edit_message(chat_id, message_id, text, keyboard=None):
    p = {"chat_id": chat_id, "message_id": message_id, "text": text, "parse_mode": "HTML"}
    if keyboard is not None:
        p["reply_markup"] = json.dumps({"inline_keyboard": keyboard})
    return _api("editMessageText", p)


# ── card text + keyboards ─────────────────────────────────────────────────────
_HEAD = {
    "pending":  "⚡ <b>Confirm entry</b>",
    "confirm":  "⚠️ <b>SEND this order?</b>",
    "approve":  "✅ <b>Approved — sending on the laptop…</b>",
    "skip":     "❌ <b>Skipped</b>",
    "sent":     "✅ <b>SENT</b>",
    "timeout":  "⌛ <b>Expired</b>",
    "mismatch": "⛔ <b>Refused — readback mismatch</b>",
    "blocked":  "⛔ <b>Blocked — fails risk gate, not stageable</b>",
}


def card_text(order, stage):
    sym = ticker(order["symbol"]); side = str(order["side"]).upper()
    qty = order["qty"]; entry = order.get("entry", order.get("price")); stop = order.get("stop")
    return (f"{_HEAD.get(stage, '')}\n<b>{side} {qty} {sym}</b> stop @ {entry}"
            + (f" · SL {stop}" if stop is not None else ""))


def kb_approve(oid, nonce):
    return [[{"text": "✅ Approve", "callback_data": f"OPN|A|{oid}|{nonce}"},
             {"text": "❌ Skip", "callback_data": f"OPN|S|{oid}|{nonce}"}]]


def kb_confirm(oid, nonce):
    return [[{"text": "✅ Yes, SEND", "callback_data": f"OPN|Y|{oid}|{nonce}"},
             {"text": "⬅️ No", "callback_data": f"OPN|N|{oid}|{nonce}"}]]


# ── stager entry point ────────────────────────────────────────────────────────
def stage(order):
    """Write the pending sidecar + send one confirm card for an ENTRY order. Needs
    order[order_id]+[nonce]. GATED: refuses (no sidecar, no tappable buttons) if the
    order fails validate_entry — so a manual/remote stage can't bypass the risk cap
    even though it doesn't go through advisory_monitor._stage_entries."""
    oid, nonce = order["order_id"], order["nonce"]
    ok, reason = validate_entry(order.get("price"), order.get("stop"))
    if not ok:
        print(f"[opening_confirm] stage REFUSED {order.get('symbol')}: {reason}", file=sys.stderr)
        sl = order.get("stop")
        txt = (f"{_HEAD['blocked']}\n<b>{str(order.get('side', '')).upper()} "
               f"{order.get('qty')} {ticker(order['symbol'])}</b> stop @ {order.get('price')}"
               + (f" · SL {sl}" if sl is not None else "")
               + f"\n<i>{reason}</i>")
        return _api("sendMessage", {"chat_id": _chat(), "parse_mode": "HTML", "text": txt})
    write(oid, {"status": "pending", "nonce": nonce,
                "symbol": order["symbol"], "side": order["side"], "qty": order["qty"],
                "entry": order.get("price"), "stop": order.get("stop"), "ts": time.time()})
    return _api("sendMessage", {"chat_id": _chat(), "parse_mode": "HTML",
                                "text": card_text(order, "pending"),
                                "reply_markup": json.dumps({"inline_keyboard": kb_approve(oid, nonce)})})


# ── concierge callback (2-tap, nonce-checked) ─────────────────────────────────
def handle_callback(cb_data, cq, _edit=None):
    """Route an OPN| callback. cb_data = OPN|<A|S|Y|N>|<order_id>|<nonce>. Edits the card
    in place and sets the sidecar status the runner polls. `_edit` overridable for tests."""
    edit = _edit or edit_message
    parts = cb_data.split("|")
    if len(parts) != 4 or parts[0] != "OPN":
        return "ignored"
    _, act, oid, nonce = parts
    msg = cq.get("message", {})
    chat_id = msg.get("chat", {}).get("id")
    mid = msg.get("message_id")
    d = read(oid)
    if d is None or str(d.get("nonce")) != str(nonce):
        edit(chat_id, mid, "⌛ <b>Expired</b> — this card is stale (re-staged or already used).")
        return "stale"
    if act == "A":            # first tap → show the exact order, ask to confirm
        edit(chat_id, mid, card_text(d, "confirm"), kb_confirm(oid, nonce))
        return "confirm"
    if act == "Y":            # second tap → approve; the runner sends after readback
        update_status(oid, "approve", expect_nonce=nonce)
        edit(chat_id, mid, card_text(d, "approve"))
        return "approve"
    if act == "S":            # skip → runner cancels the dialog
        update_status(oid, "skip", expect_nonce=nonce)
        edit(chat_id, mid, card_text(d, "skip"))
        return "skip"
    if act == "N":            # back out → restore the first card
        edit(chat_id, mid, card_text(d, "pending"), kb_approve(oid, nonce))
        return "revert"
    return "ignored"
