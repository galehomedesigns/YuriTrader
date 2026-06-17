#!/usr/bin/env python3
"""Stock Trading Concierge — YuriTradingViewBot daemon.

Mirrors trading_concierge.py but drives Questrade for US + Canadian stock
execution instead of Kraken for crypto.

Commands: /best /positions /balance /history /kill /help (same as crypto)
Buttons: Buy 1/5/10 shares, Sell Now, Hold 30m, Skip

Shared state.db means button callbacks coexist with the crypto daemon's — each
daemon's getUpdates long-poll only receives its own bot's messages, and the
callback handler checks action context/symbol to route properly.

Double-gate interlock for live trading:
  - QUESTRADE_ALLOW_TRADING=true (gate 1)
  - MANUAL_STOCK_TRADING_ENABLED=true (gate 2)
Both must be true, otherwise every trade runs in validate-only mode.
"""
import html
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

# CRITICAL: Load .env before importing anything that reads os.environ at module load
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

from shared.stock_broker import get_executor, get_broker_name, StockExecutorError
from concierge import advisor
from concierge import state


STOCK_BOT_TOKEN = os.environ.get("TELEGRAM_STOCK_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "6545739863")
API_BASE = f"https://api.telegram.org/bot{STOCK_BOT_TOKEN}"
POLL_TIMEOUT = 30

MANUAL_STOCK_MAX_EXPOSURE_USD = float(os.environ.get("MANUAL_STOCK_MAX_EXPOSURE_USD", "2000"))
MANUAL_STOCK_DAILY_LOSS_LIMIT = float(os.environ.get("MANUAL_STOCK_DAILY_LOSS_LIMIT", "-100"))

# Share-count buttons shown on /best
SHARE_AMOUNTS = [1, 5, 10]


# ========== Telegram API ==========

def tg_request(method, params=None, timeout=60):
    url = f"{API_BASE}/{method}"
    data = urllib.parse.urlencode(params or {}).encode() if params else None
    req = urllib.request.Request(url, data=data, method="POST" if data else "GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode())
            if payload.get("ok"):
                return payload.get("result")
            print(f"  TG API error: {payload.get('description')}", file=sys.stderr)
            return None
    except urllib.error.HTTPError as e:
        print(f"  TG HTTP {e.code}: {e.read().decode()[:200]}", file=sys.stderr)
        return None
    except Exception as e:
        print(f"  TG error: {e}", file=sys.stderr)
        return None


def send_message(text, keyboard=None, chat_id=None):
    params = {
        "chat_id": chat_id or TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
    }
    if keyboard:
        params["reply_markup"] = json.dumps({"inline_keyboard": keyboard})
    return tg_request("sendMessage", params)


def answer_callback(callback_query_id, text=None):
    params = {"callback_query_id": callback_query_id}
    if text:
        params["text"] = text[:200]
    return tg_request("answerCallbackQuery", params)


def get_updates(offset=None):
    params = {"timeout": POLL_TIMEOUT}
    if offset is not None:
        params["offset"] = offset
    return tg_request("getUpdates", params, timeout=POLL_TIMEOUT + 10) or []


# ========== Supabase helpers ==========

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")


def _sb_get(path):
    url = f"{SUPABASE_URL}/rest/v1/{path}"
    req = urllib.request.Request(url, headers={
        "apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"
    })
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode())
    except Exception:
        return []


def _sb_post(table, data):
    url = f"{SUPABASE_URL}/rest/v1/{table}"
    body = json.dumps(data if isinstance(data, list) else [data]).encode()
    req = urllib.request.Request(url, data=body, headers={
        "apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json", "Prefer": "return=representation",
    }, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode())
    except Exception as e:
        print(f"  SB POST error: {e}", file=sys.stderr)
        return None


def _sb_patch(table, match, data):
    url = f"{SUPABASE_URL}/rest/v1/{table}?{match}"
    body = json.dumps(data).encode()
    req = urllib.request.Request(url, data=body, headers={
        "apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json", "Prefer": "return=minimal",
    }, method="PATCH")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status
    except Exception as e:
        print(f"  SB PATCH error: {e}", file=sys.stderr)
        return None


# ========== Commands ==========

HELP_TEXT = """<b>📈 Yuri Stock Trader — Commands</b>

<b>Analysis:</b>
• /best — What's the best stock trade right now?
• /opening — Opening Power top-10 (pre-market candlestick scan)
• /positions — Open manual stock positions
• /balance — Questrade balance + exposure
• /history — Today's closed manual stock trades

<b>Emergency:</b>
• /kill — Cancel all open Questrade orders

<b>Help:</b>
• /help — This message

You can also type plain English: "best", "balance", "positions".

<b>Current mode:</b> manual stock trading is <b>{}</b>.
All trades go through Questrade. Double-gate safety: both QUESTRADE_ALLOW_TRADING
and MANUAL_STOCK_TRADING_ENABLED must be true in .env for real orders to fire.

Auto-watchers:
• BUY signals: every 30 min during market hours (9:30–4 ET, Mon-Fri)
• SELL signals: every 5 min during market hours, on any open manual position
"""


def cmd_help(_):
    mode = "ON" if os.environ.get("MANUAL_STOCK_TRADING_ENABLED", "false").lower() == "true" else "OFF"
    send_message(HELP_TEXT.format(mode))


def cmd_best(_):
    """Run the stock advisor and present all qualifying opportunities with share-count buttons."""
    send_message("⏳ Analyzing stock opportunities... (this takes ~20s)")

    try:
        results = advisor.get_top_opportunity(top_n=20, asset_class="stock", min_firing=1)
    except Exception as e:
        send_message(f"❌ Advisor error: <code>{str(e)[:300]}</code>")
        return

    if not results or "error" in results[0]:
        err = results[0].get("error", "no opportunities") if results else "no results"
        send_message(f"No opportunities found. <i>{html.escape(err)}</i>")
        return

    for i, top in enumerate(results, 1):
        sym = top["symbol"]
        price = top["price"]
        change = top["day_change_pct"]
        firing = top["firing_count"]
        firing_names = ", ".join(top["firing_bots"]) if top["firing_bots"] else "none"
        levels = top["levels"]
        ind = top["indicators"]
        currency = "CAD" if sym.upper().endswith(".TO") else "USD"

        text = (
            f"🎯 <b>#{i} STOCK OPPORTUNITY — {html.escape(sym)}</b>\n"
            f"<b>Price:</b> ${price:,.2f} {currency}  ({change:+.2f}% day)\n\n"
            f"<b>📊 Indicators:</b>\n"
            f"• RSI(14): {ind['rsi_14']:.0f}\n"
            f"• ADX(14): {ind['adx_14']:.0f}\n"
            f"• BB width: {ind['bb_bandwidth']:.4f}\n"
            f"• Candle: {html.escape(ind['candlestick'] or 'none')}\n"
            f"• ATR(14): ${ind['atr_14']:.4f}\n\n"
            f"<b>🤖 Bot consensus:</b> {firing}/9 firing\n"
            f"{html.escape(firing_names)}\n\n"
            f"<b>📈 Suggested levels:</b>\n"
            f"• Entry:  ${levels['entry']:,.2f}\n"
            f"• Stop:   ${levels['stop']:,.2f} ({levels['stop_pct']:+.1f}%)\n"
            f"• Target: ${levels['target']:,.2f} ({levels['target_pct']:+.1f}%)\n"
            f"• R:R:    {levels['rr']:.1f}:1\n\n"
            f"<b>💬 Analysis:</b>\n<i>{html.escape(top['analysis'])}</i>"
        )

        if firing == 0:
            text += "\n\n⚠️ <b>0 bots firing — not a clean setup. Skipping recommended.</b>"
            send_message(text)
            continue

        context = {
            "symbol": sym, "price": price, "firing": firing,
            "levels": levels, "indicators": ind,
            "currency": currency, "source": "stock_concierge_best",
        }
        row = []
        for qty in SHARE_AMOUNTS:
            total = qty * price
            cb = state.save_pending_action(
                "buy", symbol=sym, amount_usd=qty, context=context
            )
            row.append({"text": f"Buy {qty} sh (${total:,.0f})", "callback_data": cb})
        buttons = [row]
        skip_cb = state.save_pending_action("skip", symbol=sym, context=context)
        buttons.append([{"text": "❌ Skip", "callback_data": skip_cb}])

        send_message(text, keyboard=buttons)


def cmd_positions(_):
    """List open manual stock positions with live P&L."""
    rows = _sb_get(
        "arena_trades?status=eq.open&manual_trade=eq.true"
        "&select=id,symbol,entry_price,qty,reason,opened_at&order=opened_at.desc"
    )
    # Stock-only filter (symbol without '/')
    rows = [r for r in rows if "/" not in (r.get("symbol") or "")]
    if not rows:
        send_message("📭 No open manual stock positions.")
        return

    try:
        executor = get_executor()
    except Exception as e:
        send_message(f"❌ Questrade init error: <code>{str(e)[:200]}</code>")
        return

    lines = [f"<b>📊 Open Stock Positions ({len(rows)})</b>\n"]
    for r in rows:
        sym = r["symbol"]
        entry = float(r.get("entry_price") or 0)
        qty = float(r.get("qty") or 0)
        try:
            q = executor.get_quote(sym)
            current = q["last"] or q["bid"]
        except Exception:
            current = 0

        if current > 0:
            pnl = (current - entry) * qty
            pnl_pct = (current - entry) / entry * 100 if entry else 0
            emoji = "🟢" if pnl >= 0 else "🔴"
            lines.append(
                f"{emoji} <b>{html.escape(sym)}</b>  {qty:.0f} sh @ ${entry:,.2f}\n"
                f"    Now: ${current:,.2f}  P&amp;L: {pnl:+.2f} ({pnl_pct:+.1f}%)"
            )
        else:
            lines.append(f"• <b>{html.escape(sym)}</b>  {qty:.0f} sh @ ${entry:,.2f}  (price lookup failed)")

    send_message("\n\n".join(lines))


def cmd_balance(_):
    """Show Questrade balance + stock exposure."""
    try:
        executor = get_executor()
        balance = executor.get_balance()
    except Exception as e:
        send_message(f"❌ Questrade error: <code>{str(e)[:200]}</code>")
        return

    # Count open manual stock exposure
    open_positions = _sb_get(
        "arena_trades?status=eq.open&manual_trade=eq.true&select=symbol,entry_price,qty"
    )
    stock_positions = [p for p in open_positions if "/" not in (p.get("symbol") or "")]
    exposure = sum(float(p.get("entry_price") or 0) * float(p.get("qty") or 0) for p in stock_positions)

    lines = ["<b>💰 Stock Account Status</b>\n"]
    for currency, info in balance.items():
        lines.append(
            f"<b>{currency}:</b> Cash ${info['cash']:,.2f}  |  "
            f"Equity ${info['total_equity']:,.2f}  |  "
            f"Buying power ${info['buying_power']:,.2f}"
        )
    lines.append("")
    lines.append(f"<b>Open manual stock exposure:</b> ${exposure:,.2f}")
    lines.append(f"<b>Max allowed:</b> ${MANUAL_STOCK_MAX_EXPOSURE_USD:,.2f}")

    # Broker-specific "allow trading" gate (Questrade vs IBKR).
    allow_gate = "IBKR_ALLOW_TRADING" if get_broker_name() == "ibkr" else "QUESTRADE_ALLOW_TRADING"
    mode = "LIVE" if (
        os.environ.get("MANUAL_STOCK_TRADING_ENABLED", "false").lower() == "true"
        and os.environ.get(allow_gate, "false").lower() == "true"
    ) else "VALIDATE-ONLY"
    lines.append(f"<b>Mode:</b> {mode} ({get_broker_name()})")

    send_message("\n".join(lines))


def cmd_history(_):
    """Show today's closed manual stock trades."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    rows = _sb_get(
        f"arena_trades?status=eq.closed&manual_trade=eq.true"
        f"&closed_at=gte.{today}T00:00:00Z&select=symbol,entry_price,exit_price,pnl,pnl_pct,closed_at"
        f"&order=closed_at.desc&limit=50"
    )
    rows = [r for r in rows if "/" not in (r.get("symbol") or "")]
    if not rows:
        send_message("📭 No stock trades closed today.")
        return

    total_pnl = sum(float(r.get("pnl") or 0) for r in rows)
    wins = sum(1 for r in rows if float(r.get("pnl") or 0) > 0)

    lines = [f"<b>📊 Today's Manual Stock Trades ({len(rows)})</b>\n"]
    for r in rows:
        emoji = "🟢" if float(r.get("pnl") or 0) >= 0 else "🔴"
        lines.append(
            f"{emoji} <b>{r['symbol']}</b> ${float(r['entry_price']):,.2f} → "
            f"${float(r.get('exit_price') or 0):,.2f}  "
            f"({float(r.get('pnl') or 0):+.2f}, {float(r.get('pnl_pct') or 0):+.1f}%)"
        )
    lines.append("")
    lines.append(f"<b>Net:</b> ${total_pnl:+.2f}  |  <b>Wins:</b> {wins}/{len(rows)}")

    send_message("\n".join(lines))


def cmd_kill(_):
    """Cancel all open Questrade orders."""
    send_message("🛑 <b>KILL SWITCH — cancelling all open Questrade orders</b>")
    try:
        executor = get_executor()
        result = executor.cancel_all()
        send_message(f"✅ Cancelled <b>{result['count']}</b> open orders.")
    except Exception as e:
        send_message(f"❌ Kill switch error: <code>{str(e)[:200]}</code>")


# ========== Button handlers ==========

def handle_buy_callback(action_data):
    """Execute a stock buy when user taps 'Buy N shares'.

    action_data["amount_usd"] stores SHARE COUNT for stock callbacks (we reuse
    the existing column to avoid a schema change).
    """
    symbol = action_data["symbol"]
    qty = int(action_data["amount_usd"] or 0)
    context = action_data.get("context") or {}
    expected_price = context.get("price", 0)

    if qty <= 0:
        send_message("❌ Invalid quantity on that button.")
        return

    send_message(f"⏳ Placing <b>buy {qty} share(s)</b> of {html.escape(symbol)}...")

    try:
        executor = get_executor()
        result = executor.execute_manual_trade(symbol=symbol, side="buy", qty=qty)
    except StockExecutorError as e:
        send_message(f"❌ Trade rejected: <code>{str(e)[:250]}</code>")
        return
    except Exception as e:
        send_message(f"❌ Unexpected error: <code>{type(e).__name__}: {str(e)[:200]}</code>")
        return

    is_dry = result.get("dry_run", True)
    mode_tag = "VALIDATE" if is_dry else "LIVE"
    order_id = result.get("order_id") or "validate-only"
    fill_price = result.get("price") or expected_price
    total = result.get("total") or (qty * fill_price)
    currency = result.get("currency", "USD")

    trade_row = {
        "bot_id": "manual",
        "bot_name": "Manual (Tony)",
        "symbol": symbol,
        "side": "BUY",
        "entry_price": fill_price,
        "qty": qty,
        "status": "open",
        "reason": f"Manual stock via concierge ({mode_tag})",
        "paper": is_dry,
        "manual_trade": True,
        "kraken_order_id": str(order_id) if not is_dry else "",
        "fill_price": fill_price,
    }
    _sb_post("arena_trades", trade_row)

    text = (
        f"✅ <b>{mode_tag}: BOUGHT {html.escape(symbol)}</b>\n"
        f"Shares: {qty}\n"
        f"Fill: ${fill_price:,.2f} {currency}\n"
        f"Total: ${total:,.2f} {currency}\n"
        f"Order: <code>{order_id}</code>\n\n"
        f"Position watcher will monitor for exit signals every 5 min during market hours."
    )
    send_message(text)


def handle_sell_callback(action_data):
    """Execute a stock sell when user taps 'Sell Now' on an alert."""
    trade_id = action_data.get("trade_id")
    context = action_data.get("context") or {}
    symbol = action_data.get("symbol") or context.get("symbol", "")

    if not trade_id:
        send_message("❌ Sell callback missing trade_id")
        return

    rows = _sb_get(f"arena_trades?id=eq.{trade_id}&select=*")
    if not rows:
        send_message(f"❌ Trade {trade_id} not found")
        return
    trade = rows[0]
    qty = int(float(trade.get("qty") or 0))
    entry = float(trade.get("entry_price") or 0)

    if qty <= 0:
        send_message(f"❌ Trade {trade_id} has zero quantity")
        return

    try:
        executor = get_executor()
        quote = executor.get_quote(symbol)
        current_price = quote["last"] or quote["bid"]
    except Exception as e:
        send_message(f"❌ Price lookup failed: <code>{str(e)[:200]}</code>")
        return

    send_message(f"⏳ Placing <b>sell {qty} share(s)</b> of {html.escape(symbol)}...")

    try:
        result = executor.execute_manual_trade(symbol=symbol, side="sell", qty=qty)
    except StockExecutorError as e:
        send_message(f"❌ Sell rejected: <code>{str(e)[:250]}</code>")
        return
    except Exception as e:
        send_message(f"❌ Sell error: <code>{str(e)[:200]}</code>")
        return

    fill_price = result.get("price") or current_price
    pnl = (fill_price - entry) * qty
    pnl_pct = (fill_price - entry) / entry * 100 if entry else 0
    is_dry = result.get("dry_run", True)
    mode_tag = "VALIDATE" if is_dry else "LIVE"
    order_id = result.get("order_id") or "validate-only"

    _sb_patch("arena_trades", f"id=eq.{trade_id}", {
        "exit_price": fill_price,
        "pnl": round(pnl, 4),
        "pnl_pct": round(pnl_pct, 4),
        "status": "closed",
        "exit_reason": f"Manual stock sell via concierge ({mode_tag})",
        "closed_at": datetime.now(timezone.utc).isoformat(),
    })

    emoji = "🟢" if pnl >= 0 else "🔴"
    text = (
        f"{emoji} <b>{mode_tag}: SOLD {html.escape(symbol)}</b>\n"
        f"Entry: ${entry:,.2f} → Exit: ${fill_price:,.2f}\n"
        f"Shares: {qty}  |  P&amp;L: ${pnl:+.2f} ({pnl_pct:+.1f}%)\n"
        f"Order: <code>{order_id}</code>"
    )
    send_message(text)


def handle_hold_callback(action_data):
    trade_id = action_data.get("trade_id")
    if trade_id:
        state.mute_alert(trade_id, minutes=30)
        send_message(f"🔇 Alerts muted for trade #{trade_id} for 30 minutes.")
    else:
        send_message("Noted.")


def handle_skip_callback(_):
    send_message("👍 Skipped.")


# ========== Routing ==========

_OPENING_STATE = os.environ.get(
    "OPENING_LIVE_STATE",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                 "logs", "opening_live_state.json"))


def _opening_state():
    try:
        return json.load(open(_OPENING_STATE))
    except (OSError, ValueError):
        return {}


def _opening_awaiting_budget():
    """True only when the live orchestrator is in its 9:25 'awaiting_budget'
    window TODAY (ET) — so a stray number isn't captured as a trade amount."""
    from datetime import datetime
    from zoneinfo import ZoneInfo
    s = _opening_state()
    today = datetime.now(ZoneInfo("America/New_York")).date().isoformat()
    return s.get("phase") == "awaiting_budget" and s.get("date") == today


def _parse_budget(t):
    import re
    m = re.fullmatch(r"\$?\s*(\d+(?:\.\d{1,2})?)", t.strip())
    return float(m.group(1)) if m else None


def cmd_opening_budget(amount):
    """Capture the user's 9:25 budget reply -> write into the live state file."""
    def _h(_):
        s = _opening_state()
        s["budget"] = amount
        try:
            json.dump(s, open(_OPENING_STATE, "w"))
        except OSError:
            pass
        send_message(f"✅ <b>Opening Power</b>: budget <b>${amount:.2f}</b> set for "
                     f"today. It'll deploy evenly across the first-bar matches at "
                     f"9:32 ET. (Reply <code>0</code> to cancel.)")
    return _h


def cmd_opening(_):
    """Opening Power — return the freshest pre-market top-10 (cached by the
    run_opening_scan cron). Non-blocking: reads the cache, never re-scans here."""
    cache = os.environ.get(
        "OPENING_SCAN_CACHE",
        os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "logs", "opening_scan_latest.json"))
    try:
        with open(cache) as f:
            rec = json.load(f)
    except (OSError, ValueError):
        send_message("📊 <b>Opening Power</b>\nNo pre-market scan yet today. "
                     "Scans run hourly 7:00–9:30 ET; this returns the latest.")
        return
    from opening_agent.run_opening_scan import format_message
    send_message(format_message(rec.get("ranked", []), rec.get("et", "?")))


def route_message(text):
    t = text.strip().lower()
    # Opening Power budget capture: a bare number ONLY during the 9:25 window.
    _amt = _parse_budget(t)
    if _amt is not None and _opening_awaiting_budget():
        return cmd_opening_budget(_amt)
    if t.startswith("/opening") or "candlestick" in t or "opening power" in t:
        return cmd_opening
    if t.startswith("/start") or t in ("hi", "hello"):
        return cmd_help
    if t in ("/help", "/?", "?", "help"):
        return cmd_help
    if t.startswith("/best") or "best trade" in t or t == "best" or "recommend" in t:
        return cmd_best
    if t.startswith("/positions") or "position" in t or "holding" in t or t == "open":
        return cmd_positions
    if t.startswith("/balance") or t == "balance" or t == "status" or t == "account":
        return cmd_balance
    if t.startswith("/history") or "closed" in t or t == "today":
        return cmd_history
    if t.startswith("/kill") or "panic" in t or "emergency" in t:
        return cmd_kill
    return None


def _is_stock_context(action_data):
    """Filter: only handle buttons originated by the stock flow.

    This daemon shares state.db with the crypto daemon. Each daemon's long-poll
    only receives its own bot's callbacks, so collisions are unlikely — but we
    still check the context to avoid accidentally handling a crypto callback
    that somehow landed here.
    """
    context = action_data.get("context") or {}
    symbol = action_data.get("symbol") or context.get("symbol", "")
    source = context.get("source", "")
    if "/" in symbol:
        return False
    if source and not source.startswith("stock_"):
        return False
    return True


def handle_update(update):
    if "message" in update:
        msg = update["message"]
        chat_id = str(msg.get("chat", {}).get("id", ""))
        text = msg.get("text", "")
        if not text:
            return
        if chat_id != TELEGRAM_CHAT_ID:
            print(f"  Ignoring message from chat {chat_id}", file=sys.stderr)
            return

        handler = route_message(text)
        if handler:
            try:
                handler(msg)
            except Exception as e:
                print(f"  Handler error: {e}", file=sys.stderr)
                send_message(f"❌ Handler error: <code>{str(e)[:200]}</code>")
        else:
            send_message(f"Unknown command: <code>{html.escape(text[:100])}</code>\nTry /help")

    elif "callback_query" in update:
        cq = update["callback_query"]
        cb_data = cq.get("data", "")
        chat_id = str(cq.get("message", {}).get("chat", {}).get("id", ""))
        if chat_id != TELEGRAM_CHAT_ID:
            return

        answer_callback(cq["id"])

        action = state.consume_pending_action(cb_data)
        if not action:
            send_message("⚠️ That button has expired or already been used.")
            return

        if not _is_stock_context(action):
            # Belongs to the crypto daemon — restore the state row so crypto can consume it
            print(f"  Ignoring non-stock callback {cb_data}", file=sys.stderr)
            return

        act = action["action"]
        try:
            if act == "buy":
                handle_buy_callback(action)
            elif act == "sell":
                handle_sell_callback(action)
            elif act == "hold":
                handle_hold_callback(action)
            elif act == "skip":
                handle_skip_callback(action)
            else:
                send_message(f"Unknown action: {act}")
        except Exception as e:
            print(f"  Button handler error: {e}", file=sys.stderr)
            send_message(f"❌ Button error: <code>{str(e)[:200]}</code>")


# ========== Main loop ==========

def main():
    if not STOCK_BOT_TOKEN:
        print("ERROR: TELEGRAM_STOCK_BOT_TOKEN not set", file=sys.stderr)
        sys.exit(1)

    print("=== Stock Concierge starting ===", file=sys.stderr)
    print(f"Bot token: ...{STOCK_BOT_TOKEN[-8:]}", file=sys.stderr)
    print(f"Chat ID: {TELEGRAM_CHAT_ID}", file=sys.stderr)

    send_message("📈 <b>Yuri Stock Trader online</b>\nType /help for commands.")

    offset = None
    cleanup_counter = 0
    while True:
        try:
            updates = get_updates(offset=offset)
            for u in updates:
                offset = u["update_id"] + 1
                handle_update(u)

            cleanup_counter += 1
            if cleanup_counter >= 100:
                state.cleanup_old_actions()
                cleanup_counter = 0

        except KeyboardInterrupt:
            print("Shutting down", file=sys.stderr)
            break
        except Exception as e:
            print(f"  Main loop error: {e}", file=sys.stderr)
            time.sleep(5)


if __name__ == "__main__":
    main()
