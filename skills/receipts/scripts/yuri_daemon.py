#!/usr/bin/env python3
"""Yuri — @MyProjectWorldbot Telegram daemon.

Long-polls TELEGRAM_BOT_TOKEN and routes:
  - message.photo -> process_single.py (dry) -> inline Save/Skip confirmation
  - /summary      -> summary.py current-month breakdown
  - /receipts     -> list pending receipts in gdrive:Receipts/
  - /help         -> command list
  - /kill         -> graceful daemon stop

Auth: only TELEGRAM_CHAT_ID may interact. Other chats are silently dropped.
State: in-memory pending dict for Save/Skip callbacks (lost on restart).
"""
import html
import json
import os
import secrets
import subprocess
import sys
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


def _load_env():
    env_file = "/home/tonygale/openclaw/.env"
    if not os.path.exists(env_file):
        return
    with open(env_file) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            if k and v:
                os.environ.setdefault(k, v)


_load_env()

# Telegram chat logging to MongoDB (soft-fail; never blocks the bot)
sys.path.insert(0, "/home/tonygale/openclaw/skills/shared")
try:
    import mongo_telegram
except Exception:
    mongo_telegram = None

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
ALLOWED_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
API_BASE = f"https://api.telegram.org/bot{BOT_TOKEN}"
POLL_TIMEOUT = 30

INBOX_DIR = Path.home() / "openclaw" / "state" / "receipts_inbox"
INBOX_DIR.mkdir(parents=True, exist_ok=True)

PYTHON = "/home/tonygale/openclaw/.venv/bin/python"
PROCESS_SINGLE = "/home/tonygale/openclaw/skills/receipts/scripts/process_single.py"
SUMMARY = "/home/tonygale/openclaw/skills/receipts/scripts/summary.py"
RCLONE_CONFIG = os.path.expanduser("~/.config/rclone/rclone.conf")

pending = {}  # token -> {"path": str, "summary": str}


# ---------- Telegram API ----------

def tg(method, params=None, timeout=60):
    url = f"{API_BASE}/{method}"
    data = urllib.parse.urlencode(params or {}).encode() if params else None
    req = urllib.request.Request(url, data=data, method="POST" if data else "GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            payload = json.loads(r.read().decode())
            if payload.get("ok"):
                return payload.get("result")
            print(f"TG API error ({method}): {payload.get('description')}", file=sys.stderr)
            return None
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as e:
        print(f"TG transport error ({method}): {e}", file=sys.stderr)
        return None


def send(chat_id, text, keyboard=None):
    p = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
    if keyboard:
        p["reply_markup"] = json.dumps({"inline_keyboard": keyboard})
    res = tg("sendMessage", p)
    if mongo_telegram:
        mongo_telegram.log_outbound("yuri", res, text=text, chat_id=chat_id)
    return res


def edit(chat_id, message_id, text, keyboard=None):
    p = {"chat_id": chat_id, "message_id": message_id, "text": text, "parse_mode": "HTML"}
    if keyboard is not None:
        p["reply_markup"] = json.dumps({"inline_keyboard": keyboard})
    return tg("editMessageText", p)


def answer_callback(callback_id, text=None):
    p = {"callback_query_id": callback_id}
    if text:
        p["text"] = text
    return tg("answerCallbackQuery", p)


def get_file_path(file_id):
    r = tg("getFile", {"file_id": file_id})
    return r.get("file_path") if r else None


def download_file(file_path, local_path):
    url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_path}"
    try:
        urllib.request.urlretrieve(url, local_path)
        return True
    except (urllib.error.URLError, OSError) as e:
        print(f"download failed: {e}", file=sys.stderr)
        return False


# ---------- Handlers ----------

def stash(path, summary_text):
    token = secrets.token_urlsafe(6)
    pending[token] = {"path": path, "summary": summary_text}
    return token


def handle_photo(message):
    chat_id = str(message["chat"]["id"])
    photos = message.get("photo", [])
    if not photos:
        return
    largest = photos[-1]
    file_path = get_file_path(largest["file_id"])
    if not file_path:
        send(chat_id, "Couldn't fetch the photo from Telegram. Try again.")
        return

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    local = INBOX_DIR / f"{chat_id}_{message['message_id']}_{ts}.jpg"
    if not download_file(file_path, str(local)):
        send(chat_id, "Photo download failed.")
        return

    print(f"photo received: {local.name}", file=sys.stderr)
    send(chat_id, "Analyzing receipt…")

    try:
        result = subprocess.run(
            [PYTHON, PROCESS_SINGLE, str(local)],
            capture_output=True, text=True, timeout=240,
        )
    except subprocess.TimeoutExpired:
        send(chat_id, "Analysis timed out (240s). Try again.")
        local.unlink(missing_ok=True)
        return

    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        send(chat_id, "Parse error from analyzer.\n<pre>" +
             html.escape(result.stdout[:1500] or result.stderr[:1500]) + "</pre>")
        local.unlink(missing_ok=True)
        return

    if "error" in data:
        send(chat_id, f"Analyzer error: {html.escape(str(data['error']))}")
        local.unlink(missing_ok=True)
        return

    status = data.get("status")

    if status == "not_a_receipt":
        send(chat_id, html.escape(data.get("message", "Doesn't look like a receipt.")))
        local.unlink(missing_ok=True)
        return

    if status == "unreadable":
        msg = html.escape(data.get("message", "Too blurry to read."))
        suggestion = html.escape(data.get("suggestion", ""))
        send(chat_id, f"{msg}\n\n{suggestion}")
        local.unlink(missing_ok=True)
        return

    if status == "poor_quality":
        partial = data.get("partial_data", {}) or {}
        vendor = partial.get("vendor") or "?"
        date = partial.get("date") or "?"
        total = partial.get("total") or "?"
        category = partial.get("category") or "?"
        issues = ", ".join(data.get("issues", [])) or "some details"
        summary = (f"<b>{html.escape(str(vendor))}</b> — ${total} on {date}\n"
                   f"Category: {html.escape(str(category))}\n"
                   f"<i>Partial read. Unclear: {html.escape(issues)}</i>")
        token = stash(str(local), summary)
        send(chat_id, summary + "\n\nSave anyway?", keyboard=[[
            {"text": "Save ✓", "callback_data": f"s:{token}"},
            {"text": "Discard ✗", "callback_data": f"x:{token}"},
        ]])
        return

    if status == "success":
        d = data.get("data", {}) or {}
        vendor = d.get("vendor") or "?"
        date = d.get("date") or "?"
        total = d.get("total") or "?"
        category = d.get("category") or "Other"
        gst = d.get("tax_gst_hst") or 0
        pst = d.get("tax_pst") or 0
        currency = d.get("currency") or "CAD"
        summary = (f"<b>{html.escape(str(vendor))}</b> — ${total} {currency} on {date}\n"
                   f"Category: {html.escape(str(category))}\n"
                   f"GST/HST: ${gst} · PST: ${pst}")
        if data.get("duplicate_warning"):
            summary += f"\n⚠️ {html.escape(data['duplicate_warning'])}"
        token = stash(str(local), summary)
        send(chat_id, summary + "\n\nSave?", keyboard=[[
            {"text": "Save ✓", "callback_data": f"s:{token}"},
            {"text": "Skip ✗", "callback_data": f"x:{token}"},
        ]])
        return

    send(chat_id, f"Unexpected status: {status}")
    local.unlink(missing_ok=True)


def handle_callback(cq):
    chat_id = str(cq["message"]["chat"]["id"])
    message_id = cq["message"]["message_id"]
    data = cq.get("data", "")
    callback_id = cq["id"]

    if ":" not in data:
        answer_callback(callback_id, "Unknown action")
        return
    action, token = data.split(":", 1)
    entry = pending.pop(token, None)
    if not entry:
        # Already processed (likely a double-tap) — toast the user, leave message alone
        answer_callback(callback_id, "Already handled")
        print(f"callback {action}:{token} ignored (no pending entry)", file=sys.stderr)
        return

    if action == "s":
        answer_callback(callback_id, "Saving…")
        # Strip the keyboard immediately so a second tap is impossible
        edit(chat_id, message_id, entry["summary"] + "\n\n⏳ Saving…", keyboard=[])
        try:
            r = subprocess.run(
                [PYTHON, PROCESS_SINGLE, entry["path"], "--save"],
                capture_output=True, text=True, timeout=240,
            )
        except subprocess.TimeoutExpired:
            edit(chat_id, message_id, entry["summary"] + "\n\n❌ Save timed out.")
            print(f"save timed out: {entry['path']}", file=sys.stderr)
            return
        if r.returncode == 0:
            edit(chat_id, message_id, entry["summary"] + "\n\n✅ Saved.")
            print(f"saved: {entry['path']}", file=sys.stderr)
        else:
            err = (r.stderr or r.stdout or "unknown")[:800]
            edit(chat_id, message_id,
                 entry["summary"] + f"\n\n❌ Save failed:\n<pre>{html.escape(err)}</pre>")
            print(f"save failed rc={r.returncode}: {entry['path']}", file=sys.stderr)
        return

    if action == "x":
        answer_callback(callback_id, "Skipped")
        Path(entry["path"]).unlink(missing_ok=True)
        edit(chat_id, message_id, entry["summary"] + "\n\n⏭ Skipped.", keyboard=[])
        print(f"skipped: {entry['path']}", file=sys.stderr)
        return

    answer_callback(callback_id, "Unknown action")


def handle_command(message):
    chat_id = str(message["chat"]["id"])
    text = message.get("text", "").strip()
    cmd = text.split()[0].lower().split("@")[0]

    if cmd in ("/help", "/start"):
        send(chat_id,
             "<b>Yuri</b>\n\n"
             "Send a photo → I extract vendor/date/total/category and ask before saving.\n\n"
             "Commands:\n"
             "  /summary — expense breakdown\n"
             "  /receipts — pending receipts on Drive\n"
             "  /help — this message\n"
             "  /kill — stop the daemon")
        return

    if cmd == "/summary":
        try:
            r = subprocess.run([PYTHON, SUMMARY], capture_output=True, text=True, timeout=60)
        except subprocess.TimeoutExpired:
            send(chat_id, "Summary timed out.")
            return
        out = r.stdout or r.stderr or "(empty)"
        send(chat_id, f"<pre>{html.escape(out[:3500])}</pre>")
        return

    if cmd == "/receipts":
        try:
            r = subprocess.run(
                ["rclone", "--config", RCLONE_CONFIG, "lsf", "--files-only", "gdrive:Receipts/"],
                capture_output=True, text=True, timeout=30,
            )
        except subprocess.TimeoutExpired:
            send(chat_id, "Drive listing timed out.")
            return
        files = [l for l in r.stdout.splitlines() if l.strip()]
        if not files:
            send(chat_id, "No pending receipts on Drive.")
        else:
            send(chat_id, f"{len(files)} pending on Drive:\n<pre>" +
                 html.escape("\n".join(files[:30])) + "</pre>")
        return

    if cmd == "/kill":
        send(chat_id, "Yuri shutting down.")
        sys.exit(0)

    send(chat_id, "Unknown command. /help")


# ---------- Main loop ----------

def main():
    if not BOT_TOKEN:
        print("TELEGRAM_BOT_TOKEN not set", file=sys.stderr)
        sys.exit(1)
    if not ALLOWED_CHAT_ID:
        print("TELEGRAM_CHAT_ID not set", file=sys.stderr)
        sys.exit(1)

    me = tg("getMe")
    if me:
        print(f"Yuri online as @{me.get('username')} ({me.get('first_name')})", file=sys.stderr)
        print(f"Allowed chat: {ALLOWED_CHAT_ID}", file=sys.stderr)

    # Skip any backlog from before the daemon started
    initial = tg("getUpdates", {"offset": -1, "limit": 1})
    offset = (initial[0]["update_id"] + 1) if initial else 0

    while True:
        updates = tg("getUpdates",
                     {"offset": offset, "timeout": POLL_TIMEOUT},
                     timeout=POLL_TIMEOUT + 10) or []
        for u in updates:
            offset = u["update_id"] + 1
            if mongo_telegram:
                mongo_telegram.log_inbound("yuri", u)
            try:
                if "message" in u:
                    m = u["message"]
                    chat_id = str(m["chat"]["id"])
                    if chat_id != ALLOWED_CHAT_ID:
                        continue
                    if "photo" in m:
                        handle_photo(m)
                    elif "text" in m and m["text"].startswith("/"):
                        handle_command(m)
                elif "callback_query" in u:
                    cq = u["callback_query"]
                    chat_id = str(cq["message"]["chat"]["id"])
                    if chat_id != ALLOWED_CHAT_ID:
                        answer_callback(cq["id"], "Not authorized")
                        continue
                    handle_callback(cq)
            except SystemExit:
                raise
            except Exception as e:
                print(f"handler error: {e}", file=sys.stderr)
                traceback.print_exc(file=sys.stderr)


if __name__ == "__main__":
    main()
