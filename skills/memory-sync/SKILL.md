---
name: memory-sync
description: Persistent memory system backed by Supabase. Manages tasks, conversation logs, and project context across all channels (Telegram, WhatsApp, CLI). Provides RAG search for recalling past work. Triggers on requests about tasks, to-do lists, memory, recall, what we worked on, or conversation history.
---

# Memory Sync

Persistent brain for Yuri — stores tasks, conversation history, and project context in Supabase with vector embeddings for RAG retrieval. Works across Telegram, WhatsApp, and CLI sessions.

## Quick Commands

```bash
# View all pending tasks
python3 {baseDir}/scripts/recall.py tasks --status pending

# View tasks by priority
python3 {baseDir}/scripts/recall.py tasks --priority high

# Search tasks semantically
python3 {baseDir}/scripts/recall.py search-tasks "newsletter for procurement"

# Add a new task
python3 {baseDir}/scripts/sync-tasks.py add "Build newsletter skill" --priority high --category newsletter

# Update task status
python3 {baseDir}/scripts/sync-tasks.py update 1 --status completed

# Recall what we discussed about a topic
python3 {baseDir}/scripts/recall.py search-conversations "ConstructConnect scraping"

# Recall project context
python3 {baseDir}/scripts/recall.py search-context "Project Wheel funnel"

# Log a conversation summary
python3 {baseDir}/scripts/log-conversation.py --summary "Built newsletter skill" --decisions "Weekly digest on Mondays" --next-steps "Send test email"

# Full memory dump (tasks + recent convos + context)
python3 {baseDir}/scripts/recall.py all
```

## When to Use

**At conversation start (BOOT.md calls this):**
```bash
python3 {baseDir}/scripts/recall.py tasks --status pending,in_progress
```

**When Tony asks "what's on my to-do list":**
```bash
python3 {baseDir}/scripts/recall.py tasks
```

**When Tony asks "what were we working on":**
```bash
python3 {baseDir}/scripts/recall.py search-conversations "recent work"
```

**When Tony says "add X to my tasks":**
```bash
python3 {baseDir}/scripts/sync-tasks.py add "X" --priority medium --assigned-to tony
```

**At conversation end (log what happened):**
```bash
python3 {baseDir}/scripts/log-conversation.py --summary "..." --decisions "..." --next-steps "..."
```

## Database Tables

- **project_tasks** — To-do items with priority, status, category, assignment
- **conversation_log** — Session summaries with decisions, next steps, topics
- **project_context** — Key facts, URLs, strategies, decisions

All tables have pgvector embeddings for semantic RAG search.

## Environment Variables Required

- `SUPABASE_URL`
- `SUPABASE_SERVICE_KEY`
- `OPENAI_API_KEY` — For embeddings (text-embedding-3-small)
