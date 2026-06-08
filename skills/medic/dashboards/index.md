# index

The landing page for the OpenClaw dashboard suite on GX10. Lists every dashboard served out of `~/openclaw/canvas/`, grouped by category (live / reference / archived), with its purpose, last-regen timestamp, and a direct link.

**Audience:** Anyone who hits `https://gx10-087b.tail3abae6.ts.net:8443/` — should be able to navigate to any dashboard in one click without memorizing filenames.

**Refresh cadence:** every 15 minutes (cron). Cheap — just `ls` + metadata merge, no external calls.

**Data sources:**
- `~/openclaw/canvas/*.html` mtimes — drives "last updated" per dashboard.
- A hand-curated catalog embedded in `index_update.py` mapping each dashboard filename to its title, purpose, category, and owning skill. Edit the catalog there when adding a new dashboard.

**Output:** `~/openclaw/canvas/index.html`.

**Why it's under `medic/`:** it's cross-cutting (links to every skill's dashboards) — per DASHBOARDS.md the convention is to park cross-cutting dashboards under `medic/dashboards/`.
