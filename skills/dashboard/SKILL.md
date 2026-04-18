---
name: dashboard
description: Generate and manage Tony's personal dashboard showing spending stats, to-do list, and priorities. Use when Tony asks about his dashboard, to-dos, priorities, or wants to see spending summaries.
---

# Dashboard

Tony's personal dashboard for Decades Developments. Shows spending statistics, to-do list, and priorities.

## Generate/Update Dashboard

```bash
python3 /data/skills/dashboard/scripts/generate.py
```

This pulls latest expense data from Google Drive, combines it with todos/priorities, and generates an HTML dashboard at `/data/.openclaw/canvas/dashboard.html`.

## Access Dashboard

The dashboard is served via OpenClaw's canvas host:
```
/__openclaw__/canvas/dashboard.html
```
Access through the Control UI (port 43298 via VS Code port forwarding).

A standalone copy is also synced to Google Drive at `gdrive:Dashboard/dashboard.html`.

## Manage To-Do Items

Edit the data file at `/data/.openclaw/workspace/dashboard/data.json`.

### Adding a todo:
Read the file, add an entry to the `todos` array:
```json
{"id": 6, "text": "New task description", "priority": "high", "status": "pending", "due": "2026-03-15"}
```

### Updating a todo:
Change `status` to `"in_progress"` or `"done"`.

### Priority levels:
- `high` (red)
- `medium` (yellow)
- `low` (blue)

### Status values:
- `pending` (empty circle)
- `in_progress` (half circle)
- `done` (filled circle, strikethrough)

## Manage Priorities

Edit the `priorities` array in the same data file. Each priority has a `category` and a list of `items`.

## After Any Change

Always regenerate the dashboard after modifying data:
```bash
python3 /data/skills/dashboard/scripts/generate.py
```

## Cron Schedule

Dashboard auto-updates twice daily:
- **12:00 PM ET** (noon)
- **12:00 AM ET** (midnight)

Tony can also ask Yuri to update it anytime: "Update my dashboard"
