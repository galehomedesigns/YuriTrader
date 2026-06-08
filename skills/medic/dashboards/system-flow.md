# system-flow

A one-page map of how the GX10 agent stack actually runs: who triggers what, where the data flows, and which role each of orchestrator / overseer / medic plays.

**Audience:** Tony (quick refresher on the stack), + future collaborators who land on GX10 cold. This is the "read this first" page.

**Refresh cadence:** every 30 minutes (cron). The mermaid diagram is static and only the component status (running / last ran) is data-driven.

**Data sources:**
- `systemctl --user is-active` for services (`stock-concierge`, `trading-concierge`, `tv-webhook`, `dashboards`, `dashboard-proxy`).
- Log file mtimes for cron-driven components (`medic/logs/cron.log`, `trading-arena/logs/overseer.log`, `trading-arena/logs/arena_scan.log`).
- Supabase `arena_balances.updated_at` for "data freshness" on the bot fleet.

**Output:** `~/openclaw/canvas/system-flow.html`.

**Why it lives under `medic/`:** this dashboard is cross-cutting (spans trading-arena, tradingview, medic itself, and the infra skills). Medic is the system-wide monitoring agent, so by the DASHBOARDS.md rule "cross-cutting dashboards go under `medic/dashboards/`", that's its home.

**Editing the mermaid diagram:** open `system-flow.template.html` and edit the `<pre class="mermaid">…</pre>` block directly. The diagram is static — it doesn't get regenerated. Only component status / timestamps come from `system-flow_update.py`.
