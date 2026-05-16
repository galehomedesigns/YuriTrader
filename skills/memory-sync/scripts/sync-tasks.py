#!/usr/bin/env python3
"""Manage tasks in Supabase persistent storage.

Usage:
    python3 sync-tasks.py add "Build newsletter" --priority high --category newsletter --assigned-to yuri
    python3 sync-tasks.py update 1 --status completed
    python3 sync-tasks.py update 1 --status in_progress --notes "Started working on template"
    python3 sync-tasks.py delete 1
"""

import argparse
import json
import os
import sys
import urllib.request
import urllib.error
from datetime import datetime, timezone

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")
OLLAMA_URL = os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434")
EMBED_MODEL = "mxbai-embed-large"

HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
}


def get_embedding(text):
    """Returns a 1024-dim vector from local Ollama mxbai-embed-large."""
    url = f"{OLLAMA_URL}/api/embed"
    data = json.dumps({"model": EMBED_MODEL, "input": text[:1500]}).encode()
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())["embeddings"][0]
    except (urllib.error.URLError, urllib.error.HTTPError, KeyError):
        return None


def supabase_post(data):
    url = f"{SUPABASE_URL}/rest/v1/project_tasks"
    body = json.dumps([data]).encode()
    headers = {**HEADERS, "Prefer": "return=representation"}
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())


def supabase_patch(task_id, data):
    url = f"{SUPABASE_URL}/rest/v1/project_tasks?id=eq.{task_id}"
    body = json.dumps(data).encode()
    headers = {**HEADERS, "Prefer": "return=representation"}
    req = urllib.request.Request(url, data=body, headers=headers, method="PATCH")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())


def supabase_delete(task_id):
    url = f"{SUPABASE_URL}/rest/v1/project_tasks?id=eq.{task_id}"
    req = urllib.request.Request(url, headers=HEADERS, method="DELETE")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.status


def cmd_add(args):
    task = {
        "title": args.title,
        "priority": args.priority,
        "category": args.category,
        "assigned_to": args.assigned_to,
        "status": "pending",
    }
    if args.description:
        task["description"] = args.description
    if args.notes:
        task["notes"] = args.notes
    if args.due_date:
        task["due_date"] = args.due_date

    embed_text = f"{task['title']} {task.get('description', '')} {task.get('category', '')}"
    embedding = get_embedding(embed_text)
    if embedding:
        task["embedding"] = embedding

    result = supabase_post(task)
    if result:
        t = result[0]
        print(f"✅ Task #{t['id']} created: {t['title']}")
        print(f"   Priority: {t['priority']} | Category: {t.get('category', 'none')} | Assigned: {t['assigned_to']}")
    else:
        print("❌ Failed to create task", file=sys.stderr)


def cmd_update(args):
    data = {"updated_at": datetime.now(timezone.utc).isoformat()}

    if args.status:
        data["status"] = args.status
        if args.status == "completed":
            data["completed_at"] = datetime.now(timezone.utc).isoformat()
    if args.priority:
        data["priority"] = args.priority
    if args.title:
        data["title"] = args.title
    if args.notes:
        data["notes"] = args.notes
    if args.assigned_to:
        data["assigned_to"] = args.assigned_to
    if args.category:
        data["category"] = args.category

    # Re-embed if title changed
    if args.title:
        embedding = get_embedding(args.title)
        if embedding:
            data["embedding"] = embedding

    result = supabase_patch(args.task_id, data)
    if result:
        t = result[0]
        print(f"✅ Task #{t['id']} updated: {t['title']}")
        print(f"   Status: {t['status']} | Priority: {t['priority']}")
    else:
        print(f"❌ Failed to update task #{args.task_id}", file=sys.stderr)


def cmd_delete(args):
    status = supabase_delete(args.task_id)
    if status and status < 300:
        print(f"🗑️ Task #{args.task_id} deleted")
    else:
        print(f"❌ Failed to delete task #{args.task_id}", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(description="Manage persistent tasks in Supabase")
    subparsers = parser.add_subparsers(dest="command")

    # add
    p_add = subparsers.add_parser("add", help="Add a new task")
    p_add.add_argument("title", help="Task title")
    p_add.add_argument("--description", "-d", help="Task description")
    p_add.add_argument("--priority", "-p", default="medium", choices=["critical", "high", "medium", "low"])
    p_add.add_argument("--category", "-c", default=None)
    p_add.add_argument("--assigned-to", "-a", default="yuri")
    p_add.add_argument("--notes", "-n", default=None)
    p_add.add_argument("--due-date", default=None)

    # update
    p_update = subparsers.add_parser("update", help="Update a task")
    p_update.add_argument("task_id", type=int, help="Task ID")
    p_update.add_argument("--status", "-s", choices=["pending", "in_progress", "completed", "blocked", "cancelled"])
    p_update.add_argument("--priority", "-p", choices=["critical", "high", "medium", "low"])
    p_update.add_argument("--title", "-t")
    p_update.add_argument("--notes", "-n")
    p_update.add_argument("--assigned-to", "-a")
    p_update.add_argument("--category", "-c")

    # delete
    p_delete = subparsers.add_parser("delete", help="Delete a task")
    p_delete.add_argument("task_id", type=int, help="Task ID")

    args = parser.parse_args()

    if not SUPABASE_URL or not SUPABASE_KEY:
        print("Error: SUPABASE_URL and SUPABASE_SERVICE_KEY must be set", file=sys.stderr)
        sys.exit(1)

    if args.command == "add":
        cmd_add(args)
    elif args.command == "update":
        cmd_update(args)
    elif args.command == "delete":
        cmd_delete(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
