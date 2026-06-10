# LLM Signal Advisor — Operations Runbook

How to **run, monitor, tune, and graduate** the LLM advisor. For the risk model
and the invariants, see [`LLM_ADVISOR_DESIGN.md`](LLM_ADVISOR_DESIGN.md). For
where it stands, this is the live operational guide.

> **Current phase: SHADOW (not armed).** Both trading gates stay OFF. The advisor
> places no trades and sends no alerts — it only logs what it *would* do.

---

## What it is, in one line

A local Ollama model (`huihui_ai/gemma-4-abliterated:31b`) that takes the signals
your bots **already fired** and either **vetoes** or **approves (at the same or
smaller size)** each one — never originates, upsizes, or changes symbol/side.

## The pieces

| File | Role |
|---|---|
| `shared/llm_advisor.py` | `advise()` — schema-constrained model call + fail-closed validator (the safety boundary) |
| `shared/llm_advisor_caps.py` | deterministic per-trade / position / daily caps + daily-loss circuit breaker |
| `shared/news_feed.py` | Finnhub untrusted-news feed (defensive; any failure → `[]`) |
| `shared/advisor_path_state.py` | daily-resetting ledger of the advisor path's own positions/P&L/trades (feeds the caps) |
| `concierge/advisor_shadow.py` | the SHADOW runner (cron'd) |

---

## Run it manually

```bash
cd /home/tonygale/openclaw
ADVISOR_TIMEOUT_S=240 .venv/bin/python \
  skills/trading-arena/concierge/advisor_shadow.py --asset crypto --top 5
```

- First call after the model is cold loads ~19 GB → can exceed the default 30s
  timeout. Either pre-warm or raise `ADVISOR_TIMEOUT_S` (the cron sets 240).
- Pre-warm: `curl -s localhost:11434/api/generate -d '{"model":"huihui_ai/gemma-4-abliterated:31b","prompt":"ready","stream":false,"keep_alive":"30m","options":{"num_predict":1}}'`

## The cron (installed)

```
*/30 * * * * ADVISOR_TIMEOUT_S=240 .../advisor_shadow.py --asset crypto --top 5 \
             >> .../logs/advisor_shadow_cron.log 2>&1
```
The model is only invoked when bots actually fire (`firing >= BUY_WATCHER_MIN_FIRING`).
Quiet markets cost nothing (no model call).

---

## Monitor

- **Per-run stdout / cron log**: `logs/advisor_shadow_cron.log`
- **Structured verdicts (the data that matters)**: `logs/advisor_shadow.jsonl`
  — one JSON line per run.

Quick reads:

```bash
# Runs where bots actually fired (the only interesting ones)
grep -E '"n_actionable": [1-9]' skills/trading-arena/logs/advisor_shadow.jsonl

# The headline disagreement: bots say trade, LLM vetoes
python3 -c "import json;[print(c['symbol'],c['firing_count'],c['llm_decision']) \
 for l in open('skills/trading-arena/logs/advisor_shadow.jsonl') \
 for c in json.loads(l).get('candidates',[]) if c.get('llm_overrides_bot')]"
```

Each candidate record: `symbol, firing_count, bot_would_alert, llm_decision,
llm_rank, llm_qty, caps_result, llm_overrides_bot`.

---

## Tune (env vars, set in `.env`)

| Var | Default | Effect |
|---|---|---|
| `ADVISOR_MODEL` | `huihui_ai/gemma-4-abliterated:31b` | which Ollama model judges |
| `ADVISOR_MIN_CONFIDENCE` | `0.55` | below this → veto |
| `ADVISOR_TIMEOUT_S` | `30` (cron 240) | model call timeout; on timeout → veto-all |
| `ADVISOR_PER_TRADE_MAX_USD` | `50` | per-trade notional ceiling (clamps, never raises) |
| `ADVISOR_MAX_OPEN_POSITIONS` | `3` | path position cap |
| `ADVISOR_MAX_DAILY_TRADES` | `5` | path daily trade cap |
| `ADVISOR_DAILY_LOSS_LIMIT_USD` | `25` | breaker trips at ≤ −this |
| `ADVISOR_SYMBOL_ALLOWLIST` | `""` (any) | e.g. `BTC,ETH,SOL` to lock symbols |
| `ADVISOR_NEWS_MAX` | `3` | headlines per symbol |
| `BUY_WATCHER_MIN_FIRING` | `3` | firing threshold (shared with buy_watcher) |

---

## Graduation ladder

You are at **step 1**. Do not advance a step until the prior one looks sane.

1. **SHADOW** *(now)* — cron logs verdicts; nothing trades. Collect a window that
   **includes real firing events** (quiet markets log `n_actionable: 0`). Review
   `llm_overrides_bot` cases — does the model veto/approve sensibly?
2. **DRY-RUN** — wire the advisor into the live caller before the gated executor,
   gates still OFF (validate-only). Confirm caps + ledger behave on real flow.
3. **PAPER** — open gates on the paper account (`DUP633613` / Kraken paper).
4. **LIVE, tiny caps** — only after paper is clean. Flip gates last.

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Every run vetoes, even strong signals | model not emitting the schema | ensure `format` is the JSON **schema** (`VERDICT_SCHEMA`), not `"json"` — already wired |
| All vetoes right after a restart | cold model load > timeout → fail-closed | raise `ADVISOR_TIMEOUT_S` / pre-warm |
| `n_actionable: 0` every run | no bots firing (quiet market) | expected — wait for firing events |
| News always empty | no/invalid `FINNHUB_KEY` or rate-limit | check key; feed is defensive so this is non-fatal |
| LLM "approves" a 0-firing symbol | should be impossible | set-level filter only passes `firing >= threshold`; if seen, that filter regressed |

---

## Honest framing

A filter/ranker can only **remove** trades. Its best realistic outcome here is
cutting fee drag and bad entries — it does **not** create edge (the arena has
none at this size). The abliterated model trades calibration for compliance, so
`ADVISOR_MIN_CONFIDENCE` and the shadow window exist to catch over/under-confidence
*before* any capital is involved. Treat this as risk-reduction tooling.
