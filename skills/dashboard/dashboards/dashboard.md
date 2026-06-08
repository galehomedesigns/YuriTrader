# dashboard

Tony's personal dashboard for Decades Developments — spending by category, recent transactions, active to-dos.

**Audience:** Tony — morning check of expenses + what's on his plate.

**Refresh cadence:** twice daily (noon ET, midnight ET) via cron. Lightweight — reads a local JSON state file; optionally attempts a Google Drive sync for the latest Excel expense ledger but handles a missing rclone config gracefully.

**Data sources:**
- `~/openclaw/state/dashboard/data.json` — persisted todos + aggregated expense summary (totalExpenses, receiptCount, recentExpenses, todos, activeTodos, lastUpdate). This is the source of truth; manage it by editing the JSON or via the `skills/dashboard/` CLI.
- `gdrive:Accountant/Expenses_<year>.xlsx` (optional) — if `rclone` + `gdrive:` remote are configured, the updater downloads the latest Excel and rebuilds the `by_category` + `recentExpenses` projections. If rclone fails, it falls back to whatever's in `data.json`.

**Output:** `~/openclaw/canvas/dashboard.html`.

**Supersedes:** `skills/dashboard/scripts/generate.py` (the legacy generator, which produced HTML by string concat against `/data/.openclaw/...` paths that don't exist on GX10).

**Managing todos:**
- Edit `~/openclaw/state/dashboard/data.json` — add/modify entries in the `todos` array. Each todo: `{"id": N, "text": "…", "priority": "high|medium|low", "status": "pending|in_progress|done", "due": "YYYY-MM-DD"}`.
- Run `dashboard_cron.sh` or wait for the next scheduled run; the dashboard reflects the JSON on the next regen.
