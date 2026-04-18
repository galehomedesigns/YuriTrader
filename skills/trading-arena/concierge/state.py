"""Concierge state store — tracks pending button callbacks.

When the advisor sends a recommendation with inline buttons, we need to
remember what the user was considering so when they tap a button we can
execute the right trade. Uses SQLite for persistence so the concierge
survives restarts.

Schema:
    pending_actions:
        id INTEGER PK
        callback_data TEXT UNIQUE   -- e.g. "buy_BTCUSD_25_1234567890"
        action TEXT                 -- "buy" | "sell" | "hold" | "tighten"
        symbol TEXT                 -- "BTC/USD"
        amount_usd REAL             -- for buy: $ amount; for sell: trade_id
        trade_id INTEGER            -- arena_trades row id (for sell)
        context_json TEXT           -- full context (entry, stop, target, analysis)
        created_at TIMESTAMP
        consumed_at TIMESTAMP       -- NULL if still pending
"""
import json
import os
import sqlite3
import time
import uuid
from datetime import datetime, timezone

DB_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "concierge_state.db"
)


def _conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Create the state table if it doesn't exist."""
    with _conn() as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS pending_actions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                callback_data TEXT UNIQUE NOT NULL,
                action TEXT NOT NULL,
                symbol TEXT,
                amount_usd REAL,
                trade_id INTEGER,
                context_json TEXT,
                created_at TEXT NOT NULL,
                consumed_at TEXT
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS alert_cooldowns (
                trade_id INTEGER PRIMARY KEY,
                muted_until TEXT NOT NULL
            )
        """)
        c.commit()


def save_pending_action(action, symbol=None, amount_usd=None, trade_id=None, context=None):
    """Store a pending action. Returns the callback_data string to put in the button.

    The callback_data is a short unique ID (Telegram limits to 64 bytes).
    """
    # Short unique ID (8 chars)
    cb_id = uuid.uuid4().hex[:8]
    callback_data = f"{action}_{cb_id}"
    now = datetime.now(timezone.utc).isoformat()
    context_json = json.dumps(context) if context else None

    with _conn() as c:
        c.execute("""
            INSERT INTO pending_actions (callback_data, action, symbol, amount_usd, trade_id, context_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (callback_data, action, symbol, amount_usd, trade_id, context_json, now))
        c.commit()

    return callback_data


def consume_pending_action(callback_data):
    """Look up a pending action by callback_data and mark it consumed.

    Returns a dict with the action details, or None if not found/already consumed.
    """
    with _conn() as c:
        row = c.execute("""
            SELECT * FROM pending_actions
            WHERE callback_data = ? AND consumed_at IS NULL
        """, (callback_data,)).fetchone()
        if not row:
            return None

        # Mark consumed
        now = datetime.now(timezone.utc).isoformat()
        c.execute("UPDATE pending_actions SET consumed_at = ? WHERE id = ?", (now, row["id"]))
        c.commit()

        return {
            "id": row["id"],
            "action": row["action"],
            "symbol": row["symbol"],
            "amount_usd": row["amount_usd"],
            "trade_id": row["trade_id"],
            "context": json.loads(row["context_json"]) if row["context_json"] else None,
            "created_at": row["created_at"],
        }


def cleanup_old_actions(max_age_hours=24):
    """Remove consumed actions older than max_age to keep the DB small."""
    cutoff = time.time() - (max_age_hours * 3600)
    cutoff_iso = datetime.fromtimestamp(cutoff, tz=timezone.utc).isoformat()
    with _conn() as c:
        c.execute("""
            DELETE FROM pending_actions
            WHERE consumed_at IS NOT NULL AND consumed_at < ?
        """, (cutoff_iso,))
        c.execute("""
            DELETE FROM pending_actions
            WHERE consumed_at IS NULL AND created_at < ?
        """, (cutoff_iso,))
        c.commit()


def mute_alert(trade_id, minutes=30):
    """Mute alerts for a position for N minutes after a 'hold' tap."""
    cutoff = datetime.fromtimestamp(time.time() + (minutes * 60), tz=timezone.utc).isoformat()
    with _conn() as c:
        c.execute("""
            INSERT OR REPLACE INTO alert_cooldowns (trade_id, muted_until)
            VALUES (?, ?)
        """, (trade_id, cutoff))
        c.commit()


def is_muted(trade_id):
    """Check if a trade's alerts are currently muted."""
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as c:
        row = c.execute("""
            SELECT muted_until FROM alert_cooldowns WHERE trade_id = ?
        """, (trade_id,)).fetchone()
        return row and row["muted_until"] > now


# Auto-init on import
init_db()
