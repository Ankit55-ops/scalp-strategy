# API Reference

Base URL: `http://localhost:8000/api` (Docker Compose maps port 8000).
Interactive docs: `http://localhost:8000/docs` (OpenAPI).

Authenticate: `POST /auth/login` -> `{"access_token": "..."}`.
Send `Authorization: Bearer <token>` on protected routes.

## Auth

| Method | Path | Description |
|---|---|---|
| POST | `/auth/register` | Create account (+ default workspace). 409 on duplicate email. |
| POST | `/auth/login` | Get access token. |
| GET | `/auth/me` | Current user. |
| POST | `/auth/workspaces` | Create a workspace. |

## Strategies

| Method | Path | Description |
|---|---|---|
| POST | `/strategies/generate` | Ask the architect (mock/LLM) for strategy candidates. |
| GET | `/strategies` | List strategies in the current workspace. |
| POST | `/strategies` | Save a `StrategySpec`. |
| GET | `/strategies/{id}` | Fetch strategy + current spec. |
| POST | `/strategies/{id}/versions` | Add a version to a strategy. |

A `StrategySpec` includes `entry_rules`/`exit_rules` written in the safe DSL,
e.g. `crossover(ema(close,10), ema(close,30))`. All rules are validated before
persistence.

## Backtests

| Method | Path | Description |
|---|---|---|
| POST | `/backtests` | Create + run a backtest (synchronous). |
| GET | `/backtests` | List jobs. |
| GET | `/backtests/{id}` | Job status + metrics + validation + robustness. |
| GET | `/backtests/{id}/trades` | Simulated trade list. |
| GET | `/backtests/{id}/equity-curve` | Equity curve points. |
| GET | `/backtests/{id}/chart-data` | Trades + equity for charting. |

Request body highlights: `strategy_id`, `pairs`, `timeframe` (M1/M5/M15/H1),
`date_from`/`date_to`, `balance`, `spread_pips`, `commission_per_lot`,
`slippage_pips`, `run_walk_forward`, `run_monte_carlo`, `wf_window_bars`,
`wf_step_bars`, `mc_iterations`.

## Risk

| Method | Path | Description |
|---|---|---|
| GET | `/risk/profile` | Current risk profile. |
| PUT | `/risk/profile` | Update risk profile. |
| PUT | `/risk/evaluate` | Evaluate a proposed order against the risk engine. |
| GET | `/risk/kill-switch/status` | Kill switch state. |
| POST | `/risk/kill-switch/{scope}/{id}/enable` | Enable a kill switch. |
| POST | `/risk/kill-switch/{scope}/{id}/disable` | Disable a kill switch. |

Kill switch scopes: `global`, `strategy/{id}`, `pair/{symbol}`.

## Risk

| Method | Path | Description |
|---|---|---|
| GET | `/risk/kill-switch` | Global + per-strategy + per-pair armed state (workspace-scoped). |
| GET | `/risk/kill-switch/engagements` | Engaged switches incl. reason and timestamp. |
| POST | `/risk/kill-switch` | Engage/disarm a switch (`{scope, resource_id, enabled, reason}`). |
| GET | `/risk/events` | Recent risk events (`?limit=`). |
| GET | `/risk/profiles` | All risk profiles (full parameter set). |
| POST | `/risk/profiles` | Create a profile (activates it if none active). |
| PATCH | `/risk/profiles/{id}` | Update parameters; `is_active: true` deactivates others. |
| POST | `/risk/profiles/{id}/activate` | Make a profile the single active profile. |
| DELETE | `/risk/profiles/{id}` | Remove a profile. |

Kill-switch state is **persisted per workspace** (survives restarts). Orders
are gated by the active profile: daily/weekly loss, peak drawdown, consecutive
losses, trades-per-day, open positions, spread, stop distance and session/blackout.

## Strategy check

| Method | Path | Description |
|---|---|---|
| POST | `/strategies/{id}/check` | Offline verification report (DSL, tautologies, exit-vs-entry, risk sanity, data availability, latest-backtest review) **plus an intrabar signal preview** against the live feed (`intrabar.*`: provisional side, rules hit, price, spread; `state=blocked` when the feed is stale). |
| GET | `/strategies/{id}/signals` | Recent confirmed / intrabar / blocked signal events for a strategy (`?limit=`). |

## Paper trading

| Method | Path | Description |
|---|---|---|
| POST | `/paper-trading/start` | Start simulated account (seed balance); account enters `ACTIVE` state machine. |
| POST | `/paper-trading/stop` | Stop account; optionally close open positions. |
| GET | `/paper-trading/status` | Account equity / open positions / closed trades / **`trading_state`, `state_reason`, `pending_orders`**. |
| POST | `/paper-trading/order` | Submit an order; must pass the account **state machine** + risk engine. |
| GET | `/paper-trading/positions` | Open positions with mark-to-market unrealized P&L. |
| POST | `/paper-trading/positions/{id}/close` | Close a position at the current quote. |
| GET | `/paper-trading/trades` | Closed-trade history. |
| GET | `/paper-trading/signals` | Latest mock signals per strategy. |
| GET | `/paper-trading/account-state` | Current `trading_state` / `state_reason` / `pending_orders` for the account. |
| GET | `/paper-trading/orders` | Paper order ledger (`?status=` filter), incl. REJECTED orders with reasons. |
| GET | `/paper-trading/fills` | Paper fill ledger (entry/exit, spread/slippage/commission costs). |
| GET | `/paper-trading/margin-events` | Margin/burn-down events (balance, equity, drawdown %, state). |

Every order is gated by the **account state machine and RiskEngine**:
- Account state: `INACTIVE` → `ACTIVE`; auto-pauses to `KILL_SWITCHED` (global
  kill switch), `DATA_PAUSED` (stale/disconnected/degraded feed for a symbol the
  strategy or an open position needs), or `RISK_PAUSED` (daily/weekly loss cap,
  max drawdown, max consecutive losses exceeded). `CONNECTING` is *not* treated
  as a dead feed, so a fresh workspace can trade before its first live tick.
- RiskEngine: kill switches, session, news blackout, spread ceiling,
  open-position cap, stop-distance floor, daily/weekly loss limits, drawdown,
  consecutive-loss cap, per-day trade cap.
- Every order lifecycle is recorded in `paper_orders`/`paper_fills`/`paper_margin_events`
  (entry + exit), rejections carry the reason; alerts + RiskEvent written on rejection.

## Market data

| Method | Path | Description |
|---|---|---|
| POST | `/market-data/providers/connect` | Store encrypted provider creds for the workspace and validate them. Providers: `oanda` (needs `api_key` + `account_id` + `env` practice/live), `twelvedata` (`api_key`). |
| GET | `/market-data/providers/status` | Active provider, health probe, per-provider connection states, stale threshold. Never returns secrets. |
| GET | `/market-data/instruments` | Instruments served by the active provider (canonical/provider symbol, pips, delay status). |
| GET | `/market-data/quotes/{symbol}` | Live quote: bid/ask/mid, spread in price and pips, latency, feed state, `is_stale`. Tracks feed health. |
| GET | `/market-data/candles/{symbol}` | Candles `?provider=&timeframe=&start=&end=`; persisted to DB with `source`/`bid_ask_basis`/`is_complete`, gap detection. |
| GET | `/market-data/feed-health` | Per-symbol feed state, last-quote timestamps, latency, errors. |
| POST | `/market-data/ingest/start` | Start the per-workspace ingestion daemon (poll → ticks/candles, feed health, signal engine on candle close, WS broadcast). Real providers only persist/emit; safe to call repeatedly. |
| POST | `/market-data/ingest/stop` | Stop the ingestion daemon for the workspace. |
| GET | `/market-data/ingest/status` | Whether ingestion is running and which provider feeds it. |
| WS | `/ws/market-data?token=<jwt>` | Live stream. On connect a `snapshot` (provider status, feed health, ingestion state), then `quote`, `candle_update`, `candle_close`, `feed_health`, `signal` events asynchronously. Auth via query token or `Authorization: Bearer`. |

Provider resolution order: env-selected real provider (fails fast if its key is
missing) → workspace credentials → CSV import → simulated mock. With no provider
configured the platform runs on `mock`, which is **simulated data only**;
production should never silently fall back to it. Ingestion auto-starts on boot
only for workspaces on real providers (guarded by `MARKET_DATA_INGESTION_ENABLED`).

## Live-deployment config

| Method | Path | Description |
|---|---|---|
| GET | `/live-deployments/config` | Master execution gates: `live_trading_enabled`, `practice_broker_dry_run`, `broker_provider`, `market_data_provider`. |
| GET | `/risk/overview` | Live risk dashboard: paper account + `trading_state`, global kill-switch state, active-profile limits, per-open-trade risk budget, risk-event count. |

## Brokers & live deployment

| Method | Path | Description |
|---|---|---|
| POST | `/brokers/connect` | Register a broker connection. `simulated` or `oanda_practice`. Non-sandbox OANDA requires `LIVE_TRADING_ENABLED` **and** superuser. Secrets encrypted at rest. |
| GET | `/brokers` | List connections. |
| GET | `/brokers/{id}` | Connection detail incl. provider symbols. |
| PATCH | `/brokers/{id}` | Update label/status. |
| DELETE | `/brokers/{id}` | Remove connection. |
| POST | `/brokers/{id}/test` | Test the connection against the provider. |
| POST | `/live-deployments/request` | Request live deployment (gated). |
| GET | `/live-deployments` | List requests, optional `?status=` filter. |
| GET | `/live-deployments/{id}` | Detail incl. live pre-flight checks. |
| POST | `/live-deployments/{id}/approve` | Superuser-only; re-runs pre-flight gates. |
| POST | `/live-deployments/{id}/reject` | Superuser-only. |
| POST | `/live-deployments/{id}/disable` | Superuser-only. |

Live deployment is **disabled by default** and requires: `risk_acknowledged:
true`, an **active risk profile**, at least 30 closed paper trades, and
superuser approval (which itself requires `LIVE_TRADING_ENABLED=true`, else the
approval is blocked and audited). Sandbox connections approve to
`approved_sandbox_only`.

### OANDA practice broker

- Default `env=practice`, wired to `api-fxpractice.oanda.com`.
- **Dry-run by default** (`BROKER_PRACTICE_DRY_RUN=true`): orders are validated
  and recorded in memory with `dry_run: true`, never sent to the API.
- Compute from encrypted settings only; held order intents plus
  Docker/Redis-independent operation. Live execution is only possible with
  `LIVE_TRADING_ENABLED=true` **and** `BROKER_PRACTICE_DRY_RUN=false` **and** a
  superuser-approved non-sandbox connection.

## Alerts & audit

| Method | Path | Description |
|---|---|---|
| GET | `/alerts` | List alerts, `?unread_only=true`. |
| GET | `/alerts/unread-count` | Count of unread alerts. |
| POST | `/alerts/{id}/read` | Mark an alert read. |
| POST | `/alerts/mark-all-read` | Mark all alerts read. |
| GET | `/audit/logs` | Paginated audit log (`limit`/`offset`, `action`, `resource_type` filters). |

## Dashboard & misc

| Method | Path | Description |
|---|---|---|
| GET | `/dashboard/overview` | KPIs (strategies, paper account, alerts, sessions, feed). |
| GET | `/market-data/symbols` | Symbols available from CSV provider. |
| POST | `/market-data/import` | Upload a candle CSV (multipart). |
| GET | `/market-data/economic-calendar` | Upcoming economic events (`?days=`, `?currency=`, `?impact=`). |
| GET | `/chart-layouts` | Saved chart layouts. |
| POST | `/chart-layouts` | Save a layout. |
| DELETE | `/chart-layouts/{id}` | Delete a layout. |
| GET | `/health` | Liveness. |

## Errors

- `401` no/invalid token
- `403` insufficient privileges
- `404` resource not found or belongs to another workspace
- `409` duplicate (register)
- `422` validation error (schema / DSL)
- `500` internal error (never exposes internals)

All responses include a `X-Request-ID` header for correlation with logs.