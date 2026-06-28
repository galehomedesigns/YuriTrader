"""mongo_bids.py — per-user bid/fee tracking into MongoDB Atlas.

Records the dollar amount each family follower bids per order so the operator can
total per-user spend and derive each user's fee. Writes to the Telegram.userBids
collection (separate from the raw chat log telegramChat).

Tracks all THREE bid variables per order:
  * slot_usd      — the budget/slot the user set (day_budget / max_trades)
  * notional_usd  — the confirmed order notional = qty * entry (committed at Send)
  * filled_usd    — actual filled = filled_qty * filled_avg (known after reconcile)

Two-stage capture: log_bid() at stage/confirm, update_bid_fill() after reconcile.

Design contract — NEVER LOSE A BID, NEVER BLOCK A CONFIRMATION (mirrors
mongo_telegram.py):
  * Config from the process environment: DB_URL, MONGODB_DB,
    MONGODB_BIDS_COLLECTION, optional MONGODB_BIDS_BUFFER_DIR.
  * SOFT FAIL: any DB error (no primary, outage, election) is swallowed.
  * WRITE-AHEAD BUFFER: a failed write is spooled to a local JSONL file and
    replayed on the next successful write. Replay is IDEMPOTENT — each doc uses
    `order_id` as its `_id`, so an insert dup is a no-op and a fill-update is
    naturally idempotent. Bids survive the Cluster0 no-primary flaps.

Public API:
  log_bid(user, order)            # order: dict with order_id/symbol/side/qty/entry/...
  update_bid_fill(order_id, fill) # fill: {filled_qty, filled_avg, status}
"""
import datetime
import json
import os
import sys
import threading

_lock = threading.Lock()
_buf_lock = threading.Lock()
_coll = None
_disabled = False

_BUFFER_DIR = os.environ.get("MONGODB_BIDS_BUFFER_DIR", "/home/tonygale/openclaw/state")
_BUFFER_PATH = os.path.join(_BUFFER_DIR, "mongo_bids_buffer.jsonl")
_BUFFER_MAX_BYTES = 50 * 1024 * 1024


def _log(msg):
    print(f"[mongo_bids] {msg}", file=sys.stderr)


def _utcnow():
    return datetime.datetime.now(datetime.timezone.utc)


def _collection():
    """Cached target collection (Telegram.userBids) or None if logging is off."""
    global _coll, _disabled
    if _coll is not None:
        return _coll
    if _disabled:
        return None
    with _lock:
        if _coll is not None:
            return _coll
        if _disabled:
            return None
        uri = os.environ.get("DB_URL", "")
        if not uri.startswith("mongodb"):
            _disabled = True
            _log("DB_URL missing/invalid — bid logging disabled")
            return None
        try:
            from pymongo import MongoClient
            from urllib.parse import urlsplit
            db_name = os.environ.get("MONGODB_DB", "").strip() \
                or urlsplit(uri).path.lstrip("/").split("?")[0] or "yuri"
            coll_name = os.environ.get("MONGODB_BIDS_COLLECTION", "").strip() or "userBids"
            client = MongoClient(
                uri,
                serverSelectionTimeoutMS=3000,
                connectTimeoutMS=3000,
                socketTimeoutMS=5000,
                appname="gx10-bid-logger",
            )
            _coll = client[db_name][coll_name]
            _log(f"connected -> {db_name}.{coll_name}")
            return _coll
        except Exception as e:
            _disabled = True
            _log(f"init failed, logging disabled: {str(e)[:160]}")
            return None


# ----------------------------- write-ahead buffer -----------------------------

def _spool(op):
    """Append one pending op ({'op':'insert'|'update', ...}) to the JSONL buffer."""
    try:
        try:
            if os.path.exists(_BUFFER_PATH) and os.path.getsize(_BUFFER_PATH) > _BUFFER_MAX_BYTES:
                _log("buffer full; op not buffered")
                return
        except OSError:
            pass
        with _buf_lock:
            os.makedirs(_BUFFER_DIR, exist_ok=True)
            with open(_BUFFER_PATH, "a") as f:
                f.write(json.dumps(op, default=str) + "\n")
    except Exception as e:
        _log(f"buffer write failed (ignored): {str(e)[:120]}")


def _flush_buffer(coll):
    """Replay the backlog of insert/update ops. Idempotent; keeps file on real error."""
    if not os.path.exists(_BUFFER_PATH):
        return
    try:
        from pymongo.errors import BulkWriteError
        with _buf_lock:
            with open(_BUFFER_PATH) as f:
                lines = [ln for ln in f.read().splitlines() if ln.strip()]
        if not lines:
            _safe_remove(_BUFFER_PATH)
            return
        upserts, updates = [], []
        for ln in lines:
            try:
                op = json.loads(ln)
            except Exception:
                continue
            if op.get("op") == "upsert" and op.get("_id"):
                upserts.append((op["_id"], _revive_ts(op.get("set", {})), _revive_ts(op.get("on_insert", {}))))
            elif op.get("op") == "update" and op.get("_id"):
                updates.append((op["_id"], _revive_ts(op.get("set", {}))))
        for oid, st, oi in upserts:
            coll.update_one({"_id": oid}, {"$set": st, "$setOnInsert": oi}, upsert=True)
        for oid, st in updates:
            coll.update_one({"_id": oid}, {"$set": st}, upsert=False)
        _safe_remove(_BUFFER_PATH)
        _log(f"flushed {len(upserts)} upsert(s) + {len(updates)} update(s)")
    except Exception as e:
        _log(f"flush deferred: {str(e)[:100]}")


def _safe_remove(path):
    try:
        os.remove(path)
    except OSError:
        pass


def _revive_ts(d):
    """Turn ISO strings back into datetimes for ts_* fields after buffer round-trip."""
    for k in ("ts_sent", "ts_filled"):
        v = d.get(k)
        if isinstance(v, str):
            try:
                d[k] = datetime.datetime.fromisoformat(v)
            except Exception:
                pass
    return d


# --------------------------------- public API ---------------------------------

def log_bid(user, order, action="approve"):
    """Insert one userBids doc at stage/confirm time. `order` keys used:
    order_id (required), broadcast_id, symbol, side, qty, entry, slot_usd,
    day_budget_usd, notional_usd, chat_id, bot. Soft-fail + buffered."""
    try:
        oid = order.get("order_id")
        if not oid:
            _log("log_bid: order has no order_id — skipped")
            return
        qty = order.get("qty"); entry = order.get("entry")
        notional = order.get("notional_usd")
        if notional is None and qty is not None and entry is not None:
            notional = round(float(qty) * float(entry), 2)
        # Decision-authoritative upsert: action/status reflect the LATEST decision
        # (so a Skip after a prior Approve corrects the record); the order facts and
        # ts_sent are written once on first insert. Idempotent if the decision repeats.
        set_fields = {"action": action, "status": order.get("status", "decided")}
        on_insert = {
            "order_id": oid, "user": user, "chat_id": order.get("chat_id"),
            "bot": order.get("bot", "family_bot"), "broadcast_id": order.get("broadcast_id"),
            "symbol": order.get("symbol"), "side": order.get("side", "buy"),
            "qty": qty, "entry": entry,
            "slot_usd": order.get("slot_usd"), "day_budget_usd": order.get("day_budget_usd"),
            "notional_usd": notional,
            "filled_qty": None, "filled_avg": None, "filled_usd": None,
            "ts_sent": _utcnow(), "ts_filled": None,
        }
        coll = _collection()
        if coll is None:
            return
        try:
            coll.update_one({"_id": oid}, {"$set": set_fields, "$setOnInsert": on_insert}, upsert=True)
        except Exception as e:
            _log(f"upsert deferred to buffer: {str(e)[:100]}")
            _spool({"op": "upsert", "_id": oid, "set": set_fields, "on_insert": on_insert})
            return
        _flush_buffer(coll)
    except Exception as e:
        _log(f"log_bid error (ignored): {str(e)[:120]}")


def update_bid_fill(order_id, fill):
    """Update the fill stage. `fill` keys: filled_qty, filled_avg, status
    ('filled'|'partial'|'unfilled'). Computes filled_usd. Soft-fail + buffered."""
    try:
        if not order_id:
            return
        fq = fill.get("filled_qty"); fa = fill.get("filled_avg")
        fusd = round(float(fq) * float(fa), 2) if (fq is not None and fa is not None) else None
        st = {
            "filled_qty": fq, "filled_avg": fa, "filled_usd": fusd,
            "status": fill.get("status", "filled"), "ts_filled": _utcnow(),
        }
        coll = _collection()
        if coll is None:
            return
        try:
            coll.update_one({"_id": order_id}, {"$set": st}, upsert=False)
        except Exception as e:
            _log(f"update deferred to buffer: {str(e)[:100]}")
            _spool({"op": "update", "_id": order_id, "set": st})
            return
        _flush_buffer(coll)
    except Exception as e:
        _log(f"update_bid_fill error (ignored): {str(e)[:120]}")
