# agent-overview dashboard

**What it shows:** The full Yuri / OpenClaw agent architecture for Decades Developments — agent project cards, always-on containers, data flow, every cron job's model assignment, and the locally-available models on GX10.

**Who reads it:** Tony, as the single-pane view of "which models are wired where and when."

**Data sources:**
- `LLM_JOBS` list in `agent-overview_update.py` — the 14 cron entries that invoke a local LLM, mapped 1:1 to lines in `~/openclaw/crontab.gx10`. Editing assignments = editing this list, not the HTML.
- `BACKGROUND_DAEMONS` list — the 6 cron entries that are algorithmic/data plumbing with no LLM by design.
- `LOCAL_MODELS` list — the Ollama models actually installed on GX10 and how they're consumed by the stack.
- `datetime.now()` for the "last regenerated" timestamp.

**Format lock:** Per [docs/DASHBOARDS.md](../../docs/DASHBOARDS.md), the layout lives in `agent-overview.template.html` and never changes between regenerations. Only the injected JSON data blob varies.

**How to regenerate:** `agent-overview_update.py` (run on demand). No cron schedule yet — model assignments change rarely.

**To add/move a job:** edit `LLM_JOBS` or `BACKGROUND_DAEMONS` in the update script and re-run. Do not hand-edit `canvas/agent-overview.html`.
