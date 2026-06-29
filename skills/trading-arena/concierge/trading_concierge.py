#!/usr/bin/env python3
"""Trading Concierge — Telegram long-polling service for human-in-the-loop trading.

Listens to Tony on a dedicated Telegram bot (@YuriTrade24Bot) and handles
commands like "best trade", "buy", "sell", "positions", "balance". Runs as
a systemd service.

Uses stdlib only — no python-telegram-bot dependency. Telegram Bot API is
just HTTP + JSON, easy to handle manually.

Commands:
    /start, /help           → Show help
    /best, "best trade"     → Run advisor, show top opportunity with buy buttons
    /positions              → List open manual positions
    /balance                → Kraken USD balance + exposure
    /history                → Today's closed manual trades
    /kill                   → Emergency stop (calls kill_live_trading.sh)

Inline buttons:
    buy_<id>                → Execute manual buy
    sell_<id>               → Execute manual sell
    hold_<id>               → Mute alerts for 30 min
    tighten_<id>            → Mark position for tighter stop (not yet impl)
    skip_<id>               → No-op, dismiss
"""
import html
import json
import os
import sys
import time
import urllib.parse
import urllib.request
import urllib.error
import subprocess
from datetime import datetime, timezone


# CRITICAL: Load .env BEFORE any imports that read env vars at module-load time
# (config.py, shared/, concierge/advisor all import config which reads os.environ
# at load time — if we import them before loading .env, their globals stay blank).
def _load_env():
    env_file = "/home/tonygale/openclaw/.env"
    if not os.path.exists(env_file):
        return
    with open(env_file) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            key, _, value = line.partition("=")
            if key and value:
                os.environ.setdefault(key, value)

_load_env()

# Add parent to path — must happen BEFORE the bot/config imports below
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.kraken_executor import KrakenExecutor, KrakenExecutorError, KRAKEN_PAIR_MAP
from concierge import advisor
from concierge import state

# Telegram chat logging to MongoDB (soft-fail; never blocks the bot)
sys.path.insert(0, "/home/tonygale/openclaw/skills/shared")
try:
    import mongo_telegram
except Exception:
    mongo_telegram = None

TRADER_BOT_TOKEN = os.environ.get("TELEGRAM_TRADER_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "6545739863")
API_BASE = f"https://api.telegram.org/bot{TRADER_BOT_TOKEN}"
POLL_TIMEOUT = 30  # seconds for Telegram long-poll

MANUAL_MAX_EXPOSURE_USD = float(os.environ.get("MANUAL_MAX_EXPOSURE_USD", "50.0"))
MANUAL_DAILY_LOSS_LIMIT = float(os.environ.get("MANUAL_DAILY_LOSS_LIMIT", "-10.0"))

# Buy amount buttons (displayed to user)
BUY_AMOUNTS = [10, 25, 50]


# ========== Telegram API ==========

def tg_request(method, params=None, timeout=60):
    """Call a Telegram Bot API method. Returns the result dict or None on error."""
    url = f"{API_BASE}/{method}"
    data = urllib.parse.urlencode(params or {}).encode() if params else None
    req = urllib.request.Request(url, data=data, method="POST" if data else "GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode())
            if payload.get("ok"):
                return payload.get("result")
            else:
                print(f"  TG API error: {payload.get('description')}", file=sys.stderr)
                return None
    except urllib.error.HTTPError as e:
        print(f"  TG HTTP {e.code}: {e.read().decode()[:200]}", file=sys.stderr)
        return None
    except Exception as e:
        print(f"  TG error: {e}", file=sys.stderr)
        return None


def send_message(text, keyboard=None, chat_id=None):
    """Send a message with optional inline keyboard."""
    params = {
        "chat_id": chat_id or TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
    }
    if keyboard:
        params["reply_markup"] = json.dumps({"inline_keyboard": keyboard})
    res = tg_request("sendMessage", params)
    if mongo_telegram:
        mongo_telegram.log_outbound("trading_concierge", res, text=text,
                                    chat_id=chat_id or TELEGRAM_CHAT_ID)
    return res


def answer_callback(callback_query_id, text=None):
    """Acknowledge a button tap (required by Telegram)."""
    params = {"callback_query_id": callback_query_id}
    if text:
        params["text"] = text[:200]
    return tg_request("answerCallbackQuery", params)


def get_updates(offset=None):
    """Long-poll for new updates."""
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

HELP_TEXT = """<b>🤖 Yuri Trader — Commands</b>

<b>Analysis:</b>
• /best — What's the best trade right now?
• /positions — Open manual positions
• /balance — Kraken balance + exposure
• /history — Today's closed manual trades

<b>Emergency:</b>
• /kill — Stop all live trading, cancel orders

<b>Help:</b>
• /help — This message

You can also just type things like "best trade" or "balance" in plain English.

<b>Current mode:</b> manual trading is <b>{}</b>.
All trades go through Kraken. Double-gate safety means both KRAKEN_ALLOW_TRADING and MANUAL_TRADING_ENABLED must be true in .env for real orders to fire.
"""


def cmd_help(_):
    mode = "ON" if os.environ.get("MANUAL_TRADING_ENABLED", "false").lower() == "true" else "OFF"
    send_message(HELP_TEXT.format(mode))


def cmd_best(_):
    """Run the advisor and present all qualifying opportunities with buy buttons."""
    send_message("⏳ Analyzing crypto opportunities... (this takes ~15s)")

    try:
        results = advisor.get_top_opportunity(top_n=20, asset_class="crypto", min_firing=1)
    except Exception as e:
        send_message(f"❌ Advisor error: <code>{str(e)[:300]}</code>")
        return

    if not results or "error" in results[0]:
        send_message("No opportunities found right now.")
        return

    for i, top in enumerate(results, 1):
        sym = top["symbol"]
        price = top["price"]
        change = top["day_change_pct"]
        firing = top["firing_count"]
        firing_names = ", ".join(top["firing_bots"]) if top["firing_bots"] else "none"
        levels = top["levels"]
        ind = top["indicators"]

        # Build the recommendation message — escape all free-form text
        text = (
            f"🎯 <b>#{i} OPPORTUNITY — {html.escape(sym)}</b>\n"
            f"<b>Price:</b> ${price:,.4f}  ({change:+.2f}% 24h)\n\n"
            f"<b>📊 Indicators:</b>\n"
            f"• RSI(14): {ind['rsi_14']:.0f}\n"
            f"• ADX(14): {ind['adx_14']:.0f}\n"
            f"• BB width: {ind['bb_bandwidth']:.4f}\n"
            f"• Candle: {html.escape(ind['candlestick'] or 'none')}\n"
            f"• ATR(14): ${ind['atr_14']:.4f}\n\n"
            f"<b>🤖 Bot consensus:</b> {firing}/9 firing\n"
            f"{html.escape(firing_names)}\n\n"
            f"<b>📈 Suggested levels:</b>\n"
            f"• Entry:  ${levels['entry']:,.4f}\n"
            f"• Stop:   ${levels['stop']:,.4f} ({levels['stop_pct']:+.1f}%)\n"
            f"• Target: ${levels['target']:,.4f} ({levels['target_pct']:+.1f}%)\n"
            f"• R:R:    {levels['rr']:.1f}:1\n\n"
            f"<b>💬 Analysis:</b>\n<i>{html.escape(top['analysis'])}</i>"
        )

        # If 0 bots firing, don't offer buy buttons — user should skip
        if firing == 0:
            text += "\n\n⚠️ <b>0 bots firing — not a clean setup. Skipping recommended.</b>"
            send_message(text)
            continue

        # Save context for each button
        context = {
            "symbol": sym, "price": price, "firing": firing,
            "levels": levels, "indicators": ind,
        }
        buttons = []
        row = []
        for amount in BUY_AMOUNTS:
            cb = state.save_pending_action("buy", symbol=sym, amount_usd=amount, context=context)
            row.append({"text": f"Buy ${amount}", "callback_data": cb})
        buttons.append(row)
        skip_cb = state.save_pending_action("skip", symbol=sym, context=context)
        buttons.append([{"text": "❌ Skip", "callback_data": skip_cb}])

        send_message(text, keyboard=buttons)


def cmd_positions(_):
    """List open manual positions with live P&L."""
    rows = _sb_get(
        "arena_trades?status=eq.open&manual_trade=eq.true"
        "&select=id,symbol,entry_price,qty,reason,opened_at&order=opened_at.desc"
    )
    if not rows:
        send_message("📭 No open manual positions.")
        return

    # Fetch current prices
    try:
        executor = KrakenExecutor()
        ticker_params = {"pair": ",".join(set(KRAKEN_PAIR_MAP[r["symbol"]] for r in rows if r["symbol"] in KRAKEN_PAIR_MAP))}
        ticker = executor._public("Ticker", ticker_params)
    except Exception as e:
        ticker = {}

    # Build a lookup from Kraken pair → current price
    price_map = {}
    for kraken_pair, info in ticker.items():
        try:
            price_map[kraken_pair] = float(info["c"][0])
        except (KeyError, ValueError, IndexError):
            pass

    lines = [f"<b>📈 Open Manual Positions ({len(rows)})</b>\n"]
    for r in rows:
        sym = r["symbol"]
        entry = float(r["entry_price"] or 0)
        qty = float(r["qty"] or 0)
        kraken_pair = KRAKEN_PAIR_MAP.get(sym)
        # Best-effort price lookup (Kraken may key as X*Z* form)
        current_price = None
        if kraken_pair:
            # Try exact match first, then fuzzy
            for k, v in price_map.items():
                if k == kraken_pair or k.endswith(kraken_pair) or kraken_pair in k:
                    current_price = v
                    break

        if current_price:
            pnl = (current_price - entry) * qty
            pnl_pct = (current_price - entry) / entry * 100 if entry else 0
            emoji = "🟢" if pnl >= 0 else "🔴"
            lines.append(
                f"{emoji} <b>{sym}</b> (id {r['id']})\n"
                f"   Entry: ${entry:,.4f} → Now: ${current_price:,.4f}\n"
                f"   Qty: {qty:.8f}  |  P&L: {pnl:+.2f} ({pnl_pct:+.1f}%)"
            )
        else:
            lines.append(
                f"🟡 <b>{sym}</b> (id {r['id']})\n"
                f"   Entry: ${entry:,.4f}  Qty: {qty:.8f}"
            )

    send_message("\n\n".join(lines))


def cmd_balance(_):
    """Show Kraken balance + live exposure."""
    try:
        executor = KrakenExecutor()
        balance = executor.get_balance()
        usd = executor.get_usd_balance()
    except Exception as e:
        send_message(f"❌ Kraken error: <code>{str(e)[:200]}</code>")
        return

    # Count open manual exposure
    open_positions = _sb_get(
        "arena_trades?status=eq.open&manual_trade=eq.true&select=entry_price,qty"
    )
    exposure = sum(float(p.get("entry_price") or 0) * float(p.get("qty") or 0) for p in open_positions)

    lines = ["<b>💰 Account Status</b>\n"]
    lines.append(f"<b>USD (free):</b> ${usd:.2f}")
    for asset, amount in balance.items():
        if float(amount) > 0 and asset != "ZUSD":
            lines.append(f"<b>{asset}:</b> {amount}")
    lines.append("")
    lines.append(f"<b>Open manual exposure:</b> ${exposure:.2f}")
    lines.append(f"<b>Max allowed:</b> ${MANUAL_MAX_EXPOSURE_USD:.2f}")
    lines.append(f"<b>Mode:</b> {'LIVE' if os.environ.get('MANUAL_TRADING_ENABLED', 'false').lower() == 'true' else 'PAPER'}")

    send_message("\n".join(lines))


def cmd_history(_):
    """Show today's closed manual trades."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    rows = _sb_get(
        f"arena_trades?status=eq.closed&manual_trade=eq.true"
        f"&closed_at=gte.{today}T00:00:00Z&select=symbol,entry_price,exit_price,pnl,pnl_pct,closed_at"
        f"&order=closed_at.desc&limit=20"
    )
    if not rows:
        send_message("📭 No manual trades closed today.")
        return

    total_pnl = sum(float(r.get("pnl") or 0) for r in rows)
    wins = sum(1 for r in rows if float(r.get("pnl") or 0) > 0)

    lines = [f"<b>📊 Today's Manual Trades ({len(rows)})</b>\n"]
    for r in rows:
        emoji = "🟢" if float(r.get("pnl") or 0) >= 0 else "🔴"
        lines.append(
            f"{emoji} <b>{r['symbol']}</b> {float(r['entry_price']):,.4f} → {float(r.get('exit_price') or 0):,.4f}  "
            f"({float(r.get('pnl') or 0):+.2f}, {float(r.get('pnl_pct') or 0):+.1f}%)"
        )
    lines.append("")
    lines.append(f"<b>Net:</b> {total_pnl:+.2f}  |  <b>Wins:</b> {wins}/{len(rows)}")

    send_message("\n".join(lines))


def cmd_kill(_):
    """Trigger the kill switch."""
    send_message("🛑 <b>KILL SWITCH ACTIVATED</b>\nStopping all live trading...")
    script = "/home/tonygale/openclaw/skills/trading-arena/kill_live_trading.sh"
    try:
        result = subprocess.run([script], capture_output=True, text=True, timeout=30)
        send_message(f"✅ Kill complete.\n<pre>{result.stdout[:500]}</pre>")
    except Exception as e:
        send_message(f"❌ Kill switch error: <code>{str(e)[:200]}</code>")


# ========== Button handlers ==========

def handle_buy_callback(action_data):
    """Execute a buy when user taps 'Buy $X'."""
    symbol = action_data["symbol"]
    amount = action_data["amount_usd"]
    context = action_data.get("context") or {}
    current_price = context.get("price", 0)

    send_message(f"⏳ Placing <b>buy ${amount}</b> order for {symbol}...")

    try:
        executor = KrakenExecutor()
        result = executor.execute_manual_trade(
            symbol=symbol, side="buy",
            position_size_usd=amount,
            current_price=current_price,
        )
    except KrakenExecutorError as e:
        send_message(f"❌ Trade rejected: <code>{str(e)[:250]}</code>")
        return
    except Exception as e:
        send_message(f"❌ Unexpected error: <code>{type(e).__name__}: {str(e)[:200]}</code>")
        return

    # Record the trade in Supabase
    is_dry = result.get("dry_run", True)
    mode_tag = "DRY-RUN" if is_dry else "LIVE"
    order_id = result.get("order_id") or "validate-only"
    volume = result.get("volume", 0)

    trade_row = {
        "bot_id": "manual",
        "bot_name": "Manual (Tony)",
        "symbol": symbol,
        "side": "BUY",
        "entry_price": current_price,
        "qty": volume,
        "status": "open",
        "reason": f"Manual via concierge ({mode_tag})",
        "paper": is_dry,
        "manual_trade": True,
        "kraken_order_id": order_id if not is_dry else "",
        "fill_price": current_price,
    }
    _sb_post("arena_trades", trade_row)

    text = (
        f"✅ <b>{mode_tag}: BOUGHT {symbol}</b>\n"
        f"Volume: {volume:.8f}\n"
        f"Paid: ${amount:.2f}\n"
        f"Order: <code>{order_id}</code>\n\n"
        f"Position watcher will monitor for exit signals every 5 min."
    )
    send_message(text)


def handle_sell_callback(action_data):
    """Execute a sell when user taps 'Sell Now' on an alert."""
    trade_id = action_data.get("trade_id")
    context = action_data.get("context") or {}
    symbol = action_data.get("symbol") or context.get("symbol", "")

    if not trade_id:
        send_message("❌ Sell callback missing trade_id")
        return

    # Fetch the trade
    rows = _sb_get(f"arena_trades?id=eq.{trade_id}&select=*")
    if not rows:
        send_message(f"❌ Trade {trade_id} not found")
        return
    trade = rows[0]
    qty = float(trade.get("qty") or 0)
    entry = float(trade.get("entry_price") or 0)

    # Get current price
    try:
        executor = KrakenExecutor()
        kraken_pair = KRAKEN_PAIR_MAP.get(symbol)
        ticker = executor._public("Ticker", {"pair": kraken_pair})
        current_price = float(list(ticker.values())[0]["c"][0])
    except Exception as e:
        send_message(f"❌ Price lookup failed: <code>{str(e)[:200]}</code>")
        return

    send_message(f"⏳ Placing <b>sell</b> order for {symbol}...")

    try:
        env_allow = os.environ.get("KRAKEN_ALLOW_TRADING", "false").lower() == "true"
        manual_enabled = os.environ.get("MANUAL_TRADING_ENABLED", "false").lower() == "true"
        validate = not (env_allow and manual_enabled)
        result = executor.place_market_order(
            kraken_pair=kraken_pair, side="sell", volume=qty, validate=validate
        )
    except Exception as e:
        send_message(f"❌ Sell failed: <code>{str(e)[:200]}</code>")
        return

    # Compute P&L
    pnl = (current_price - entry) * qty
    pnl_pct = (current_price - entry) / entry * 100 if entry else 0
    is_dry = result.get("dry_run", True)
    mode_tag = "DRY-RUN" if is_dry else "LIVE"
    order_id = result.get("order_id") or "validate-only"

    # Update Supabase
    _sb_patch("arena_trades", f"id=eq.{trade_id}", {
        "exit_price": current_price,
        "pnl": round(pnl, 4),
        "pnl_pct": round(pnl_pct, 4),
        "status": "closed",
        "exit_reason": f"Manual sell via concierge ({mode_tag})",
        "closed_at": datetime.now(timezone.utc).isoformat(),
    })

    emoji = "🟢" if pnl >= 0 else "🔴"
    text = (
        f"{emoji} <b>{mode_tag}: SOLD {symbol}</b>\n"
        f"Entry: ${entry:,.4f} → Exit: ${current_price:,.4f}\n"
        f"P&L: {pnl:+.2f} ({pnl_pct:+.1f}%)\n"
        f"Order: <code>{order_id}</code>"
    )
    send_message(text)


def handle_hold_callback(action_data):
    """User tapped 'Hold' — mute alerts for this position for 30 min."""
    trade_id = action_data.get("trade_id")
    if trade_id:
        state.mute_alert(trade_id, minutes=30)
        send_message(f"🔇 Alerts muted for trade #{trade_id} for 30 minutes.")
    else:
        send_message("Noted.")


def handle_skip_callback(_):
    send_message("👍 Skipped.")


# ========== Command router ==========

def route_message(text):
    """Match incoming text to a command handler."""
    t = text.strip().lower()
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


def handle_update(update):
    """Process one Telegram update."""
    # Text message
    if "message" in update:
        msg = update["message"]
        chat_id = str(msg.get("chat", {}).get("id", ""))
        text = msg.get("text", "")
        if not text:
            return
        # Only respond to Tony's chat
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
            send_message(f"Unknown command: <code>{text[:100]}</code>\nTry /help")

    # Button tap (inline keyboard callback)
    elif "callback_query" in update:
        cq = update["callback_query"]
        cb_data = cq.get("data", "")
        chat_id = str(cq.get("message", {}).get("chat", {}).get("id", ""))
        if chat_id != TELEGRAM_CHAT_ID:
            return

        # Always acknowledge
        answer_callback(cq["id"])

        action = state.consume_pending_action(cb_data)
        if not action:
            send_message(f"⚠️ That button has expired or already been used.")
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
    if not TRADER_BOT_TOKEN:
        print("ERROR: TELEGRAM_TRADER_BOT_TOKEN not set", file=sys.stderr)
        sys.exit(1)

    print(f"=== Trading Concierge starting ===", file=sys.stderr)
    print(f"Bot token: ...{TRADER_BOT_TOKEN[-8:]}", file=sys.stderr)
    print(f"Chat ID: {TELEGRAM_CHAT_ID}", file=sys.stderr)

    # Send a boot notification
    send_message("🤖 <b>Yuri Trader online</b>\nType /help for commands.")

    offset = None
    cleanup_counter = 0
    while True:
        try:
            updates = get_updates(offset=offset)
            for u in updates:
                offset = u["update_id"] + 1
                if mongo_telegram:
                    mongo_telegram.log_inbound("trading_concierge", u)
                handle_update(u)

            # Clean up old pending actions every 100 polls (~50 min)
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
