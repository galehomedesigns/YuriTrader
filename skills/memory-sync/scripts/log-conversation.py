#!/usr/bin/env python3
"""Log a conversation summary to Supabase for cross-session memory.

Usage:
    python3 log-conversation.py --summary "Built newsletter skill" \
        --decisions "Weekly digest on Mondays" "Use Gmail not AgentMail" \
        --next-steps "Send test email to Tony" \
        --topics procurement newsletter \
        --skills-used procurement gmail

    python3 log-conversation.py --summary "Tony asked about BC Bid registration" \
        --channel telegram
"""

import argparse
import json
import os
import sys
import urllib.request
import urllib.error
from datetime import datetime, timezone, date

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")
OPENAI_KEY = os.environ.get("OPENAI_API_KEY", "")

HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
}


def get_embedding(text):
    if not OPENAI_KEY:
        return None
    url = "https://api.openai.com/v1/embeddings"
    data = json.dumps({"input": text[:8000], "model": "text-embedding-3-small"}).encode()
    req = urllib.request.Request(url, data=data, headers={
        "Authorization": f"Bearer {OPENAI_KEY}",
        "Content-Type": "application/json",
    })
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())["data"][0]["embedding"]


def main():
    parser = argparse.ArgumentParser(description="Log conversation summary to Supabase")
    parser.add_argument("--summary", "-s", required=True, help="Conversation summary")
    parser.add_argument("--decisions", "-d", nargs="*", default=[], help="Key decisions made")
    parser.add_argument("--next-steps", "-n", nargs="*", default=[], help="Next steps identified")
    parser.add_argument("--files-changed", "-f", nargs="*", default=[], help="Files modified")
    parser.add_argument("--skills-used", nargs="*", default=[], help="Skills used in session")
    parser.add_argument("--topics", "-t", nargs="*", default=[], help="Topics discussed")
    parser.add_argument("--channel", default="cli", help="Channel (telegram, whatsapp, cli)")
    parser.add_argument("--date", default=None, help="Session date (YYYY-MM-DD), defaults to today")

    args = parser.parse_args()

    if not SUPABASE_URL or not SUPABASE_KEY:
        print("Error: SUPABASE_URL and SUPABASE_SERVICE_KEY must be set", file=sys.stderr)
        sys.exit(1)

    session_date = args.date or date.today().isoformat()

    entry = {
        "session_date": session_date,
        "summary": f"[{args.channel}] {args.summary}",
        "decisions": args.decisions if args.decisions else None,
        "next_steps": args.next_steps if args.next_steps else None,
        "files_changed": args.files_changed if args.files_changed else None,
        "skills_used": args.skills_used if args.skills_used else None,
        "topics": args.topics if args.topics else None,
    }

    # Clean nulls
    entry = {k: v for k, v in entry.items() if v is not None}

    # Embed the summary + topics for RAG
    embed_text = f"{args.summary} {' '.join(args.topics)} {' '.join(args.decisions)}"
    embedding = get_embedding(embed_text)
    if embedding:
        entry["embedding"] = embedding

    url = f"{SUPABASE_URL}/rest/v1/conversation_log"
    body = json.dumps([entry]).encode()
    headers = {**HEADERS, "Prefer": "return=representation"}
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read().decode())
            if result:
                r = result[0]
                print(f"✅ Conversation logged (#{r['id']}, {session_date})")
                print(f"   Summary: {args.summary[:100]}")
                if args.decisions:
                    print(f"   Decisions: {'; '.join(args.decisions[:3])}")
                if args.next_steps:
                    print(f"   Next steps: {'; '.join(args.next_steps[:3])}")
    except urllib.error.HTTPError as e:
        print(f"❌ Failed to log: {e.read().decode()[:200]}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
