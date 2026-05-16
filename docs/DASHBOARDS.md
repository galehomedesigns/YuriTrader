# Dashboard standard

Every OpenClaw dashboard follows this contract so layout is locked and
only the data changes between regenerations. If you're about to "just
rewrite the HTML" for a dashboard, read this first and don't.

## Why this exists

Pre-standard, each dashboard was regenerated from scratch by a mix of
hand-written and LLM-assisted generators. Every run produced slightly
different HTML — different palette, different grid, different chart
library, sometimes different section order — so styling work never
survived the next refresh. The rule below makes regeneration a pure
`render(template, data)` call where the template is a human-authored
file that changes only when a human edits it.

## Directory layout

Each dashboard's files live in its owning skill's `dashboards/`
directory, all prefixed by the dashboard name so one skill can own
many dashboards side by side:

```
~/openclaw/skills/<skill>/dashboards/
├── <name>.md                ← what this dashboard shows, who reads it, what data it pulls
├── <name>.template.html     ← locked HTML skeleton, contains exactly one placeholder: {{DATA}}
├── <name>_update.py         ← pulls data → dict → json.dumps → substitutes → writes canvas/<name>.html
└── <name>_cron.sh           ← sources .env then invokes <name>_update.py (for crontab)
```

A skill with only a single dashboard may drop the name prefix on
`update.py` / `cron.sh` for brevity, but prefixed is the default —
the first skill to add a second dashboard (trading-arena) got
renamed retroactively and that's the pattern to copy.

If a dashboard is cross-cutting (spans multiple skills), park it
under `medic/dashboards/` — medic is the system-wide agent and
already owns the other monitoring-flavored dashboards.

Output: every `_update.py` writes to `~/openclaw/canvas/<name>.html`.
That directory is what the `dashboards.service` static server serves.

## The contract

1. **Templates do not change between regenerations.** All runtime
   variation comes from the injected JSON data blob.
2. `update.py` is pure data: read state → build Python dict → serialize
   → substitute. No LLMs in the render path.
3. Data is injected into a single
   `<script id="dashboard-data" type="application/json">{{DATA}}</script>`
   tag. Client-side JavaScript inside the template reads from it and
   populates the DOM + Chart.js.
4. The substitution is literal text replacement:
   `template.replace("{{DATA}}", json.dumps(data, default=str))`.
   One placeholder, one substitution, zero templating engine.
5. If a dashboard needs richer server-side rendering, the template
   still exposes exactly one `{{DATA}}` slot — extra complexity goes
   into `update.py`, never into the template shape.

## Visual standard

### Palette (CSS variables — paste verbatim)

```css
:root {
  --bg: #0d1117;
  --surface: #161b22;
  --surface-2: #1c222b;
  --border: #21262d;
  --text: #e6edf3;
  --text-dim: #8b95a7;
  --text-muted: #556677;
  --accent: #7c8aff;
  --accent-2: #c77dff;
  --ok: #00c853;
  --warn: #ffc107;
  --fail: #ff1744;
  --pnl-pos: #69f0ae;
  --pnl-neg: #ef5350;
  --radius: 12px;
  --radius-sm: 8px;
}
```

### Typography
- Font stack: `-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif`
- Base 14px, H1 28px, H2 16px, small 12px, mono stats 28px bold

### Layout
- Max width 1200px, body centered, 20px padding
- 24px gap between top-level cards, 16px gap within stat rows
- Cards: `background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius); padding: 24px`
- Mobile breakpoint at 768px — stat rows collapse to column

### Required header
Every dashboard starts with:

```html
<header class="dash-header">
  <h1>Title</h1>
  <div class="subtitle">One-line purpose</div>
  <div class="updated">Last updated: <span id="updated-at">…</span></div>
</header>
```

### Charts
- **Library:** Chart.js 4 via CDN
  (`https://cdn.jsdelivr.net/npm/chart.js@4`). Pin major version.
- Dark axes: grid `#21262d`, text `#8b95a7`.
- Line charts: 2px stroke, no points unless series has < 20 data points.
- Default series color order: `--accent`, `--accent-2`, `--pnl-pos`,
  `--warn`, `--fail`, `--ok`, then chromatic fill.
- `maintainAspectRatio: false`; wrap every canvas in
  `<div class="chart-wrap" style="position:relative;height:240px">`.

### Status badges (use everywhere)

```html
<span class="badge badge-ok">OK</span>
<span class="badge badge-warn">WARN</span>
<span class="badge badge-fail">FAIL</span>
```

Corresponding CSS lives in the template header (copy across dashboards).

## update.py contract

```python
# required functions
def build_data() -> dict: ...        # pure read + compute, returns JSON-serializable dict
def main() -> None: ...              # reads template, calls build_data, substitutes, writes output

# data dict conventions
{
  "generated_at": "ISO-8601 UTC",    # always present
  "title": "…",                       # optional override for header
  # ...dashboard-specific keys
}
```

The data dict should be flat-ish and well-named — client-side JS reads
it directly. Avoid deeply nested structures; prefer arrays of typed
objects.

## Archival

When a dashboard is shelved (the underlying job stops running), do
three things:

1. Prepend a banner inside the template's `<body>`:

   ```html
   <div class="banner-archived">
     <strong>Archived — resumable.</strong>
     Data last refreshed on &lt;DATE&gt;. See
     <a href="…">&lt;SKILL&gt;/SKILL.md</a> for how to revive.
   </div>
   ```

   Style (in the palette section of the template):

   ```css
   .banner-archived {
     background: #3a2a1a;
     border: 1px solid #d97706;
     border-radius: var(--radius-sm);
     color: #fbbf24;
     padding: 12px 16px;
     margin-bottom: 24px;
   }
   ```

2. Add `status: archived` to the top of `<name>.md`.
3. Disable or delete the cron entry / systemd unit that regenerates it.

Archived dashboards stay in `~/openclaw/canvas/` — they're still useful
as historical snapshots. Their `update.py` should exit early if run.

## When adding a new dashboard

1. Copy an existing `<name>.template.html` from a sibling skill — don't
   start from a blank file.
2. Author `<name>.md` in one paragraph: purpose, audience, what data.
3. Write `update.py`. Target: under 200 lines. If it's longer,
   push helpers into the skill's `shared/` or `scripts/` modules.
4. Smoke-test by running `update.py` locally, then `curl -u …
   https://gx10-087b.tail3abae6.ts.net:8443/<name>.html`.
5. Add a cron entry if the data needs regular refresh. Naming:
   `<skill>_<name>_cron.sh` next to other wrappers in the skill.

## Who reads the output

Public URL (basic auth `tony` / `decades2026`):
`https://gx10-087b.tail3abae6.ts.net:8443/<name>.html`

Tailnet-internal (no auth, faster): same host on port 8090 direct.

## The anti-pattern this replaces

Don't:
- Ask an LLM to "regenerate the bot arena dashboard" and paste
  whatever HTML comes back into canvas/.
- Style one dashboard differently from the rest "just for now".
- Put data fetching logic inside the template or inline `<script>`
  that reaches out to APIs at page load — templates are static, data
  lives in the injected JSON blob.

Do:
- Edit the template by hand when visual changes are needed.
- Run `update.py` to refresh data; the layout stays put.
- Use the palette variables, never hardcode colors.
