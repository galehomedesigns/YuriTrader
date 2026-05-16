#!/usr/bin/env python3
"""Recall tasks, conversations, and context from Supabase.

Usage:
    python3 recall.py tasks                              # All tasks
    python3 recall.py tasks --status pending             # Filter by status
    python3 recall.py tasks --priority high              # Filter by priority
    python3 recall.py tasks --assigned-to tony           # Filter by assignee
    python3 recall.py search-tasks "newsletter"          # Semantic search tasks
    python3 recall.py search-conversations "scraping"    # Semantic search convos
    python3 recall.py search-context "Project Wheel"     # Semantic search context
    python3 recall.py all                                # Full memory dump
"""

import argparse
import json
import os
import sys
import urllib.request
import urllib.error

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")
OLLAMA_URL = os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434")
EMBED_MODEL = "mxbai-embed-large"

HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
}


def supabase_get(path):
    url = f"{SUPABASE_URL}/rest/v1/{path}"
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        print(f"Error: {e.read().decode()[:200]}", file=sys.stderr)
        return []


def supabase_rpc(fn_name, params):
    url = f"{SUPABASE_URL}/rest/v1/rpc/{fn_name}"
    body = json.dumps(params).encode()
    req = urllib.request.Request(url, data=body, headers=HEADERS, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        print(f"RPC Error: {e.read().decode()[:200]}", file=sys.stderr)
        return []


def get_embedding(text):
    """Returns a 1024-dim vector from local Ollama mxbai-embed-large."""
    url = f"{OLLAMA_URL}/api/embed"
    data = json.dumps({"model": EMBED_MODEL, "input": text[:1500]}).encode()
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())["embeddings"][0]
    except (urllib.error.URLError, urllib.error.HTTPError, KeyError) as e:
        print(f"Embedding error: {e}", file=sys.stderr)
        return None


def format_task(t):
    status_icons = {"pending": "⬜", "in_progress": "🔄", "completed": "✅", "blocked": "🚫", "cancelled": "❌"}
    priority_icons = {"critical": "🔴", "high": "🟠", "medium": "🟡", "low": "🟢"}
    icon = status_icons.get(t.get("status", ""), "⬜")
    pri = priority_icons.get(t.get("priority", ""), "")
    assigned = f" (@{t['assigned_to']})" if t.get("assigned_to") else ""
    cat = f" [{t['category']}]" if t.get("category") else ""
    desc = f"\n   {t['description'][:120]}" if t.get("description") else ""
    notes = f"\n   Notes: {t['notes'][:100]}" if t.get("notes") else ""
    return f"{icon} {pri} #{t['id']} {t['title']}{assigned}{cat}{desc}{notes}"


def format_conversation(c):
    decisions = ""
    if c.get("decisions"):
        decisions = "\n   Decisions: " + "; ".join(c["decisions"][:3])
    next_steps = ""
    if c.get("next_steps"):
        next_steps = "\n   Next: " + "; ".join(c["next_steps"][:3])
    return f"📅 {c['session_date']} — {c['summary'][:200]}{decisions}{next_steps}"


def format_context(c):
    return f"🔑 {c['key']} = {c['value'][:150]}"


def cmd_tasks(args):
    filters = ["select=id,title,description,status,priority,category,assigned_to,notes,due_date,created_at"]
    if args.status:
        statuses = args.status.split(",")
        if len(statuses) == 1:
            filters.append(f"status=eq.{statuses[0]}")
        else:
            filters.append(f"status=in.({','.join(statuses)})")
    if args.priority:
        filters.append(f"priority=eq.{args.priority}")
    if args.assigned_to:
        filters.append(f"assigned_to=eq.{args.assigned_to}")
    filters.append("order=priority.asc,created_at.asc")

    tasks = supabase_get("project_tasks?" + "&".join(filters))
    if not tasks:
        print("No tasks found.")
        return

    # Group by status
    by_status = {}
    for t in tasks:
        s = t.get("status", "unknown")
        by_status.setdefault(s, []).append(t)

    for status in ["in_progress", "pending", "blocked", "completed", "cancelled"]:
        if status in by_status:
            print(f"\n{'─'*50}")
            print(f" {status.upper().replace('_', ' ')} ({len(by_status[status])})")
            print(f"{'─'*50}")
            for t in by_status[status]:
                print(format_task(t))


def cmd_search_tasks(args):
    embedding = get_embedding(args.query)
    if not embedding:
        return
    results = supabase_rpc("match_tasks", {
        "query_embedding": embedding,
        "match_threshold": 0.3,
        "match_count": args.limit,
    })
    if not results:
        print(f"No tasks matching '{args.query}'")
        return
    print(f"\nTasks matching '{args.query}':\n")
    for r in results:
        sim = f"({r['similarity']:.0%})" if "similarity" in r else ""
        print(f"  {format_task(r)} {sim}")


def cmd_search_conversations(args):
    embedding = get_embedding(args.query)
    if not embedding:
        return
    results = supabase_rpc("match_conversations", {
        "query_embedding": embedding,
        "match_threshold": 0.3,
        "match_count": args.limit,
    })
    if not results:
        print(f"No conversations matching '{args.query}'")
        return
    print(f"\nConversations matching '{args.query}':\n")
    for r in results:
        sim = f"({r['similarity']:.0%})" if "similarity" in r else ""
        print(f"  {format_conversation(r)} {sim}")


def cmd_search_context(args):
    embedding = get_embedding(args.query)
    if not embedding:
        return
    results = supabase_rpc("match_context", {
        "query_embedding": embedding,
        "match_threshold": 0.3,
        "match_count": args.limit,
    })
    if not results:
        print(f"No context matching '{args.query}'")
        return
    print(f"\nContext matching '{args.query}':\n")
    for r in results:
        sim = f"({r['similarity']:.0%})" if "similarity" in r else ""
        print(f"  {format_context(r)} {sim}")


def cmd_all(args):
    print("=" * 60)
    print(" YURI'S MEMORY — FULL DUMP")
    print("=" * 60)

    # Tasks
    tasks = supabase_get("project_tasks?select=id,title,description,status,priority,category,assigned_to,notes&order=priority.asc,created_at.asc")
    print(f"\n📋 TASKS ({len(tasks)} total)")
    for t in tasks:
        print(f"  {format_task(t)}")

    # Recent conversations
    convos = supabase_get("conversation_log?select=id,session_date,summary,decisions,next_steps&order=session_date.desc&limit=5")
    print(f"\n💬 RECENT CONVERSATIONS ({len(convos)})")
    for c in convos:
        print(f"  {format_conversation(c)}")

    # Project context
    contexts = supabase_get("project_context?select=id,key,value,category&order=category,key")
    print(f"\n🔑 PROJECT CONTEXT ({len(contexts)})")
    for c in contexts:
        print(f"  {format_context(c)}")


def main():
    parser = argparse.ArgumentParser(description="Recall from Yuri's persistent memory")
    subparsers = parser.add_subparsers(dest="command")

    # tasks
    p_tasks = subparsers.add_parser("tasks", help="List tasks")
    p_tasks.add_argument("--status", help="Filter by status (comma-separated)")
    p_tasks.add_argument("--priority", help="Filter by priority")
    p_tasks.add_argument("--assigned-to", help="Filter by assignee")

    # search-tasks
    p_st = subparsers.add_parser("search-tasks", help="Semantic search tasks")
    p_st.add_argument("query", help="Search query")
    p_st.add_argument("--limit", type=int, default=5)

    # search-conversations
    p_sc = subparsers.add_parser("search-conversations", help="Semantic search conversations")
    p_sc.add_argument("query", help="Search query")
    p_sc.add_argument("--limit", type=int, default=5)

    # search-context
    p_sx = subparsers.add_parser("search-context", help="Semantic search project context")
    p_sx.add_argument("query", help="Search query")
    p_sx.add_argument("--limit", type=int, default=5)

    # all
    subparsers.add_parser("all", help="Full memory dump")

    args = parser.parse_args()

    if not SUPABASE_URL or not SUPABASE_KEY:
        print("Error: SUPABASE_URL and SUPABASE_SERVICE_KEY must be set", file=sys.stderr)
        sys.exit(1)

    if args.command == "tasks":
        cmd_tasks(args)
    elif args.command == "search-tasks":
        cmd_search_tasks(args)
    elif args.command == "search-conversations":
        cmd_search_conversations(args)
    elif args.command == "search-context":
        cmd_search_context(args)
    elif args.command == "all":
        cmd_all(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
