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

## Paper trading

| Method | Path | Description |
|---|---|---|
| POST | `/paper-trading/start` | Start simulated account (seed balance). |
| POST | `/paper-trading/stop` | Stop account; optionally close open positions. |
| GET | `/paper-trading/status` | Account equity / open positions / closed trades. |
| POST | `/paper-trading/order` | Submit an order; risk engine must approve. |
| GET | `/paper-trading/positions` | Open positions with mark-to-market unrealized P&L. |
| POST | `/paper-trading/positions/{id}/close` | Close a position at the current quote. |
| GET | `/paper-trading/trades` | Closed-trade history. |
| GET | `/paper-trading/signals` | Latest mock signals per strategy. |

Every order is gated by the **RiskEngine**: kill switches, session, news
blackout, spread ceiling, open-position cap, stop-distance floor, daily-loss
limit and correlated-exposure ceiling. Rejections are written to the audit log,
a `RiskEvent`, and an `Alert`.

## Brokers & live deployment

| Method | Path | Description |
|---|---|---|
| POST | `/brokers/connect` | Register a broker connection (simulated only for now); secrets encrypted at rest. |
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
superuser approval. Sandbox connections approve to `approved_sandbox_only` —
the platform never executes automatically and has no real broker adapter.

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