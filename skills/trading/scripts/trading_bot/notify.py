"""
Telegram notifications and Supabase event logging for the trading agent.
"""
import httpx
import config


def send_telegram(message):
    """Send a message to Tony via Telegram Bot API."""
    if not config.TELEGRAM_BOT_TOKEN:
        print(f"[Telegram] No bot token — skipping: {message[:80]}")
        return False

    try:
        resp = httpx.post(
            f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage",
            json={
                "chat_id": config.TELEGRAM_CHAT_ID,
                "text": message,
                "parse_mode": "HTML",
            },
            timeout=10,
        )
        return resp.status_code == 200
    except Exception as e:
        print(f"[Telegram] Send failed: {e}")
        return False


def log_trade_to_supabase(trade_data):
    """Write a trade record to the auto_trades table."""
    if not config.SUPABASE_URL:
        return None

    headers = {
        "apikey": config.SUPABASE_SERVICE_KEY,
        "Authorization": f"Bearer {config.SUPABASE_SERVICE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }

    try:
        resp = httpx.post(
            f"{config.SUPABASE_URL}/rest/v1/auto_trades",
            headers=headers,
            json=trade_data,
            timeout=10,
        )
        if resp.status_code in (200, 201):
            result = resp.json()
            return result[0]["id"] if result else None
    except Exception as e:
        print(f"[Supabase] auto_trades write failed: {e}")
    return None


def update_trade_in_supabase(trade_id, data):
    """Update an existing auto_trades record."""
    if not config.SUPABASE_URL or not trade_id:
        return

    headers = {
        "apikey": config.SUPABASE_SERVICE_KEY,
        "Authorization": f"Bearer {config.SUPABASE_SERVICE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=minimal",
    }

    try:
        httpx.patch(
            f"{config.SUPABASE_URL}/rest/v1/auto_trades",
            headers=headers,
            params={"id": f"eq.{trade_id}"},
            json=data,
            timeout=10,
        )
    except Exception as e:
        print(f"[Supabase] auto_trades update failed: {e}")


def audit_log(action, symbol, trade_id=None, details=None):
    """Write to the trade_audit table."""
    if not config.SUPABASE_URL:
        return

    headers = {
        "apikey": config.SUPABASE_SERVICE_KEY,
        "Authorization": f"Bearer {config.SUPABASE_SERVICE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=minimal",
    }

    try:
        httpx.post(
            f"{config.SUPABASE_URL}/rest/v1/trade_audit",
            headers=headers,
            json={
                "action": action,
                "symbol": symbol,
                "trade_id": trade_id,
                "details": details or {},
            },
            timeout=10,
        )
    except Exception as e:
        print(f"[Supabase] trade_audit write failed: {e}")


def store_snapshot(symbol, price, volume, day_change_pct=None, bid=None, ask=None):
    """Write a price snapshot to market_snapshots for dashboard use."""
    if not config.SUPABASE_URL:
        return

    headers = {
        "apikey": config.SUPABASE_SERVICE_KEY,
        "Authorization": f"Bearer {config.SUPABASE_SERVICE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=minimal",
    }

    try:
        httpx.post(
            f"{config.SUPABASE_URL}/rest/v1/market_snapshots",
            headers=headers,
            json={
                "symbol": symbol,
                "price": price,
                "volume": int(volume) if volume else 0,
                "day_change_pct": day_change_pct,
                "bid": bid,
                "ask": ask,
            },
            timeout=10,
        )
    except Exception:
        pass
