#!/usr/bin/env python3
"""
TradingView Webhook Receiver — listens for alerts from TradingView
and logs them to Supabase + sends Telegram notifications.

TradingView's broker integration with Questrade handles order execution
directly. This webhook receiver logs, monitors, and notifies.

Runs as a simple HTTP server on port 8089 inside the container.
Caddy reverse proxies /webhook to this port.

Usage: python3 webhook_server.py
"""
import json
import os
import sys
from datetime import datetime, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler

import httpx

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "6545739863")
WEBHOOK_SECRET = os.environ.get("TRADINGVIEW_WEBHOOK_SECRET", "yuri-tv-2026")
PORT = 8089


def send_telegram(message):
    if not TELEGRAM_BOT_TOKEN:
        return
    try:
        httpx.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"},
            timeout=10,
        )
    except Exception:
        pass


def log_to_supabase(data):
    if not SUPABASE_URL:
        return
    try:
        httpx.post(
            f"{SUPABASE_URL}/rest/v1/trade_audit",
            headers={
                "apikey": SUPABASE_KEY,
                "Authorization": f"Bearer {SUPABASE_KEY}",
                "Content-Type": "application/json",
                "Prefer": "return=minimal",
            },
            json={
                "action": f"TV_{data.get('action', 'UNKNOWN')}",
                "symbol": data.get("ticker", ""),
                "details": data,
            },
            timeout=10,
        )
    except Exception:
        pass


class WebhookHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length).decode("utf-8")

        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            # Plain text alert
            data = {"message": body}

        # Validate secret if present
        if data.get("secret") and data["secret"] != WEBHOOK_SECRET:
            self.send_response(403)
            self.end_headers()
            self.wfile.write(b"Invalid secret")
            return

        # Log
        timestamp = datetime.now(timezone.utc).isoformat()
        print(f"[{timestamp}] Webhook received: {json.dumps(data)[:200]}")

        # Process the alert
        action = data.get("action", "").upper()
        ticker = data.get("ticker", data.get("symbol", ""))
        price = data.get("price", data.get("close", ""))
        qty = data.get("quantity", data.get("qty", ""))
        flags = data.get("flags", "")

        # Log to Supabase
        log_to_supabase(data)

        # Telegram notification
        if action in ("BUY", "SELL"):
            msg = (
                f"<b>TRADINGVIEW SIGNAL: {action}</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"Symbol: {ticker}\n"
                f"Price: ${price}\n"
                f"Quantity: {qty}\n"
                f"Flags: {flags}\n"
                f"Source: TradingView Strategy"
            )
            send_telegram(msg)

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"status": "ok"}).encode())

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"TradingView Webhook Receiver - Yuri Trading Bot")

    def log_message(self, format, *args):
        pass  # Suppress default access logs


if __name__ == "__main__":
    server = HTTPServer(("0.0.0.0", PORT), WebhookHandler)
    print(f"Webhook server listening on port {PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("Webhook server stopped")
