# IBKR Live Trading — What's Built & How Ready We Are

**As of 2026-06-08.** This is a *status / readiness* document — what exists, how it's
wired, what state it's in right now, and exactly what's left to flip to trade for
real. For the step-by-step bring-up playbook, see [`IBKR_SETUP.md`](IBKR_SETUP.md).

Everything below is traced to actual code/config in the repo, not aspirational.

---

## 1. One-paragraph summary

Interactive Brokers is fully wired as a **stock execution backend** for the trading
arena, selected via `STOCK_BROKER=ibkr` in `.env`. Orders route through a local
**IB Gateway** Docker container (no API key — auth is your IBKR login + 2FA held by
the container). The executor mirrors the Questrade interface so the concierge is
broker-agnostic. **Right now it is in PAPER mode with BOTH safety gates OFF**, which
means the system runs **validate-only (dry-run)** — it prices and checks orders but
transmits nothing to IBKR. It is *built and ready*, not *armed*.

---

## 2. What we already have (components)

| Piece | File | What it does |
|---|---|---|
| **IB Gateway** | [`infra/ib-gateway/docker-compose.yml`](../../infra/ib-gateway/docker-compose.yml) | Runs `gnzsnz/ib-gateway` in Docker; holds the IBKR session; exposes the API on `127.0.0.1:4002` (paper) / `:4001` (live) |
| **Executor** | [`shared/ibkr_executor.py`](shared/ibkr_executor.py) | `ib_async` client: balances, positions, quotes (with free delayed-data fallback), market orders, `cancel_all` for the kill-switch |
| **Broker selector** | [`shared/stock_broker.py`](shared/stock_broker.py) | `get_executor()` returns IBKR or Questrade based on `STOCK_BROKER`; `StockExecutorError` catches either |
| **Concierge** | [`concierge/stock_concierge.py`](concierge/stock_concierge.py) | Uses `get_executor()`; broker-aware gate display |
| **Smoke test** | [`smoke_ibkr.py`](smoke_ibkr.py) | Connection + account + quote check before trusting the link |
| **Setup playbook** | [`IBKR_SETUP.md`](IBKR_SETUP.md) | Full bring-up / go-live procedure |

Both executors (IBKR + Questrade) remain in the repo. Ordering is on IBKR;
Questrade was abandoned for orders (its API token is read-only-scoped — orders
return 403; see [`QUESTRADE_NEXT_STEPS.md`](QUESTRADE_NEXT_STEPS.md)).

---

## 3. How orders flow

```
stock_concierge  ->  get_executor()  ->  IBKRExecutor
                                            |  ib_async over localhost
                                            v
                              IB Gateway (Docker, 127.0.0.1:4002)
                                            |  IBKR login + 2FA push
                                            v
                                   IBKR paper account DUP633613
```

There is **no API key**. The only credential is your IBKR login, held by the
Gateway container; logins (and the daily ~03:00 ET auto-restart) trigger a 2FA
push to IBKR Mobile that must be approved.

---

## 4. The double-gate safety model

`execute_manual_trade()` transmits a real order **only when BOTH** of these are true:

| Gate | Env var | Current value |
|---|---|---|
| Gate 1 | `IBKR_ALLOW_TRADING` | **`false`** |
| Gate 2 | `MANUAL_STOCK_TRADING_ENABLED` | **`false`** |

Either one `false` → the executor returns a `dry_run: True` result (priced,
buying-power-checked, but **nothing sent to IBKR**). This applies in paper mode too,
so an accidental config flip can't fire unexpected trades. (Source:
`ibkr_executor.py` lines ~288–321.)

---

## 5. Current configured state (`.env`)

| Setting | Value | Meaning |
|---|---|---|
| `STOCK_BROKER` | `ibkr` | IBKR is the active stock backend |
| `IBKR_TRADING_MODE` | `paper` | Paper system — fake money |
| `IBKR_PORT` | `4002` | Paper API port |
| `IBKR_HOST` | `127.0.0.1` | Loopback only |
| `IBKR_CLIENT_ID` | `17` | API client id |
| `IBKR_ACCOUNT_ID` | `DUP633613` | Paper account |
| `IBKR_READ_ONLY_API` | `no` | Gateway will accept orders (not portfolio-only) |
| `IBKR_ALLOW_TRADING` | `false` | **Gate 1 closed** |
| `MANUAL_STOCK_TRADING_ENABLED` | `false` | **Gate 2 closed** |
| `IBKR_USERID` / `IBKR_PASSWORD` (live) | *blank* | Still in the paper phase; live creds not set |
| Paper username / password | *set in `.env`* | Not reproduced here |

So: **paper mode, gates closed, live creds intentionally blank.** This is the
"built and safe" resting state.

---

## 6. What actually ran today (2026-06-08, Monday)

Honest status, from the logs — *no order (paper or live) was transmitted today*:

- **Automated stock buy-watcher ran during US market hours** and scanned the
  watchlist (TSLA, COIN, QQQ, AMD, NIO, RIVN, XLK…). Result: **`0–1/9 bots firing`
  vs a threshold of 3 → no alerts, no trade signals.**
  (`logs/stock_buy_watcher.log`)
- **Outside market hours** both the buy-watcher and position-watcher logged
  `Outside US market hours — skipping`.
- **IB Gateway container is not currently running** (`docker ps` shows no
  `ib-gateway`), and **both gates are off** — so even a firing signal would have
  produced only a dry-run, and there was no live API session up.

**Bottom line for today:** the pipeline executed end-to-end *up to the signal stage*
and correctly produced no trades (no qualifying signal + gates closed). It did **not**
place a paper or live order, and was not in an armed state to do so.

---

## 7. To actually place a PAPER order (verify the link)

1. Bring up Gateway:
   ```bash
   cd /home/tonygale/openclaw/infra/ib-gateway && docker compose up -d
   docker logs -f ib-gateway        # wait for login complete; approve the 2FA push
   ```
2. Smoke test:
   ```bash
   cd /home/tonygale/openclaw && source .env
   .venv/bin/python skills/trading-arena/smoke_ibkr.py --symbol AAPL
   ```
   Expect: managed account `DUP633613`, a `NetLiquidation` figure, an AAPL quote
   (a `None` quote just means no market-data subscription — orders still work).
3. Open **both** gates and fire one test order (per `IBKR_SETUP.md` Step 6). A
   position showing `qty=1.0 AAPL` confirms paper trading end-to-end.

---

## 8. To go LIVE (real money)

Per `IBKR_SETUP.md` Step 8 — do **not** do this until paper has run cleanly:

1. **Fund** the live account at IBKR (CAD via bank bill-payment; 1–2 business days).
2. In `.env`: set `IBKR_TRADING_MODE=live`, `IBKR_PORT=4001`, and fill
   `IBKR_USERID` / `IBKR_PASSWORD` with the **live** creds.
3. `docker compose -f infra/ib-gateway/docker-compose.yml restart` and approve the
   new 2FA push.
4. Re-run the smoke test against port 4001.
5. Flip `IBKR_ALLOW_TRADING=true` (and `MANUAL_STOCK_TRADING_ENABLED=true`) **only**
   when genuinely ready for real orders.

---

## 9. Known gotchas (already handled / to remember)

- **Port mapping**: host `4001/4002` map to the container's **socat** ports
  `4003/4004`, not the raw API ports — wiring them directly causes "connection
  reset by peer". Already correct in the compose file.
- **Single-mode creds**: the image only swaps in `*_PAPER` vars in *dual* mode; in
  single paper/live mode it uses `TWS_USERID/TWS_PASSWORD` verbatim. The compose
  file falls back to paper creds when live creds are blank, so both phases work
  unchanged.
- **Daily restart**: IBKR forces a ~24h session restart; compose auto-restarts at
  03:00 ET, which re-fires the 2FA push — keep IBKR Mobile notifications on.
- **No market-data subscription**: quotes fall back to free *delayed* data; market
  orders still fill at the real exchange price.

---

## 10. Readiness verdict

| Capability | State |
|---|---|
| Code / executor / selector / concierge | ✅ Built |
| IB Gateway container defined | ✅ Built |
| Paper account configured (`DUP633613`) | ✅ Configured |
| Gateway currently running | ❌ Down right now |
| Paper order actually verified end-to-end | ⏳ Not confirmed in this record — run §7 |
| Live creds / funding | ❌ Not set (intentional) |
| Safety gates | 🔒 Both OFF (safe resting state) |

**We are one `docker compose up` + 2FA approval + two gate flips away from paper
trading, and a funding + creds swap beyond that from live.** The system is built
and parked safely; it is not currently armed.
