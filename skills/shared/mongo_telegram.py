"""mongo_telegram.py — append-only logger of Telegram chats to MongoDB Atlas.

Shared by every Telegram daemon (yuri, stock-concierge, trading-concierge) to
persist inbound and outbound messages into the Telegram.telegramChat collection.

Design contract — NEVER LOSE A CHAT, NEVER BLOCK A BOT:
  * Config from the process environment (each daemon loads
    /home/tonygale/openclaw/.env at startup): DB_URL, MONGODB_DB,
    MONGODB_TELEGRAM_COLLECTION, optional MONGODB_TELEGRAM_BUFFER_DIR.
  * SOFT FAIL: any DB error (no primary, outage, election) is swallowed so a
    logging hiccup can never block a trade confirmation or a receipt save.
  * WRITE-AHEAD BUFFER: when an insert fails (e.g. Atlas has no primary during a
    flap) the document is spooled to a local JSONL file. The next time a write
    succeeds, the whole backlog is replayed. Replay is IDEMPOTENT — each buffered
    doc keeps a fixed _id, so a partial/retried flush never creates duplicates.
    Result: chats survive cluster flaps and land once Atlas recovers.

Public API:
  log_inbound(bot, update)            # a raw Telegram Update dict from getUpdates
  log_outbound(bot, result, text=..)  # the result dict from sendMessage/editMessageText
"""
import datetime
import json
import os
import sys
import threading
from urllib.parse import urlsplit

_lock = threading.Lock()
_buf_lock = threading.Lock()
_coll = None
_disabled = False  # True only when config is permanently unusable (no/invalid DB_URL)

_BUFFER_DIR = os.environ.get("MONGODB_TELEGRAM_BUFFER_DIR",
                             "/home/tonygale/openclaw/state")
_BUFFER_MAX_BYTES = 50 * 1024 * 1024  # 50 MB per-bot safety cap


def _log(msg):
    print(f"[mongo_telegram] {msg}", file=sys.stderr)


def _utcnow():
    return datetime.datetime.now(datetime.timezone.utc)


def _collection():
    """Return the cached target collection, or None if logging is off."""
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
            _log("DB_URL missing/invalid — telegram chat logging disabled")
            return None
        try:
            from pymongo import MongoClient
            db_name = os.environ.get("MONGODB_DB", "").strip()
            if not db_name:
                db_name = urlsplit(uri).path.lstrip("/").split("?")[0] or "yuri"
            coll_name = (os.environ.get("MONGODB_TELEGRAM_COLLECTION", "").strip()
                         or "telegramChat")
            client = MongoClient(
                uri,
                serverSelectionTimeoutMS=3000,
                connectTimeoutMS=3000,
                socketTimeoutMS=5000,
                appname="gx10-telegram-logger",
            )
            _coll = client[db_name][coll_name]
            _log(f"connected -> {db_name}.{coll_name}")
            return _coll
        except Exception as e:  # construction should rarely fail; never propagate
            _disabled = True
            _log(f"init failed, logging disabled: {str(e)[:160]}")
            return None


# ----------------------------- write-ahead buffer -----------------------------

def _buffer_path(bot):
    safe = "".join(ch if (ch.isalnum() or ch in "-_") else "_" for ch in str(bot or "unknown"))
    return os.path.join(_BUFFER_DIR, f"mongo_telegram_buffer.{safe}.jsonl")


def _buffer_doc(doc):
    """Spool a doc to the per-bot JSONL file after a failed insert."""
    try:
        path = _buffer_path(doc.get("bot"))
        try:
            if os.path.exists(path) and os.path.getsize(path) > _BUFFER_MAX_BYTES:
                _log(f"buffer full ({path}); dropping oldest-protect, doc not buffered")
                return
        except OSError:
            pass
        d = dict(doc)
        oid = d.pop("_id", None)          # pymongo assigns _id on the failed attempt
        if oid is not None:
            d["_buf_oid"] = str(oid)      # preserve it so replay is idempotent
        ts = d.get("ts")
        if hasattr(ts, "isoformat"):
            d["ts"] = ts.isoformat()
        line = json.dumps(d, default=str)
        with _buf_lock:
            os.makedirs(_BUFFER_DIR, exist_ok=True)
            with open(path, "a") as f:
                f.write(line + "\n")
    except Exception as e:
        _log(f"buffer write failed (ignored): {str(e)[:120]}")


def _flush_buffer(bot, coll):
    """Replay the per-bot backlog into Mongo. Idempotent; keeps file on real error."""
    path = _buffer_path(bot)
    if not os.path.exists(path):
        return
    try:
        from bson import ObjectId
        from pymongo.errors import BulkWriteError
        with _buf_lock:
            with open(path) as f:
                lines = [ln for ln in f.read().splitlines() if ln.strip()]
        if not lines:
            _safe_remove(path)
            return
        docs = []
        for ln in lines:
            try:
                d = json.loads(ln)
            except Exception:
                continue
            oid = d.pop("_buf_oid", None)
            if oid:
                try:
                    d["_id"] = ObjectId(oid)
                except Exception:
                    pass
            ts = d.get("ts")
            if isinstance(ts, str):
                try:
                    d["ts"] = datetime.datetime.fromisoformat(ts)
                except Exception:
                    pass
            docs.append(d)
        if not docs:
            _safe_remove(path)
            return
        try:
            coll.insert_many(docs, ordered=False)
        except BulkWriteError as bwe:
            non_dup = [e for e in bwe.details.get("writeErrors", []) if e.get("code") != 11000]
            if non_dup:
                _log(f"flush partial — keeping buffer, retry next write: {str(non_dup[:1])[:100]}")
                return
            # all errors were duplicate-key => those docs already landed; treat as done
        _safe_remove(path)
        _log(f"flushed {len(docs)} buffered chat(s) for {bot}")
    except Exception as e:  # transient (e.g. still no primary) — keep file, retry later
        _log(f"flush deferred: {str(e)[:100]}")


def _safe_remove(path):
    try:
        os.remove(path)
    except OSError:
        pass


def _insert(doc):
    coll = _collection()
    if coll is None:
        return  # logging permanently off (no DB_URL) — buffering would never drain
    doc.setdefault("ts", _utcnow())
    try:
        coll.insert_one(doc)
    except Exception as e:           # Atlas down / no primary -> spool, don't lose it
        _log(f"insert deferred to buffer: {str(e)[:100]}")
        _buffer_doc(doc)
        return
    _flush_buffer(doc.get("bot"), coll)  # write succeeded -> drain any backlog


# ------------------------------- field helpers --------------------------------

def _chat_id(m):
    try:
        cid = m.get("chat", {}).get("id")
        return str(cid) if cid is not None else None
    except Exception:
        return None


def _user(u):
    if not isinstance(u, dict):
        return None
    return {
        "id": u.get("id"),
        "username": u.get("username"),
        "first_name": u.get("first_name"),
        "is_bot": u.get("is_bot"),
    }


def _kind(msg):
    for k in ("photo", "document", "voice", "audio", "video", "sticker", "location", "contact"):
        if k in msg:
            return k
    return "text" if "text" in msg else "other"


# --------------------------------- public API ---------------------------------

def log_inbound(bot, update):
    """Log one inbound Telegram Update (message / edited_message / callback_query)."""
    try:
        if not isinstance(update, dict):
            return
        cq = update.get("callback_query")
        if cq:
            m = cq.get("message", {}) or {}
            _insert({
                "bot": bot, "direction": "in", "type": "callback_query",
                "chat_id": _chat_id(m),
                "message_id": m.get("message_id"),
                "text": cq.get("data"),
                "from_user": _user(cq.get("from")),
                "update_id": update.get("update_id"),
                "raw": update,
            })
            return
        m = update.get("message") or update.get("edited_message")
        if isinstance(m, dict):
            _insert({
                "bot": bot, "direction": "in", "type": _kind(m),
                "chat_id": _chat_id(m),
                "message_id": m.get("message_id"),
                "text": m.get("text") or m.get("caption"),
                "from_user": _user(m.get("from")),
                "update_id": update.get("update_id"),
                "raw": update,
            })
    except Exception as e:
        _log(f"log_inbound error (ignored): {str(e)[:120]}")


def log_outbound(bot, result, text=None, chat_id=None):
    """Log one outbound message from a Telegram sendMessage/editMessageText result."""
    try:
        r = result if isinstance(result, dict) else {}
        _insert({
            "bot": bot, "direction": "out", "type": "text",
            "chat_id": _chat_id(r) if r else (str(chat_id) if chat_id is not None else None),
            "message_id": r.get("message_id"),
            "text": (r.get("text") if r else None) or text,
            "raw": r or None,
        })
    except Exception as e:
        _log(f"log_outbound error (ignored): {str(e)[:120]}")
