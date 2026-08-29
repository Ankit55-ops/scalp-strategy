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
| GET | `/paper-trading/status` | Account equity / open positions / closed trades. |
| POST | `/paper-trading/order` | Submit an order; risk engine must approve. |

## Brokers & live deployment

| Method | Path | Description |
|---|---|---|
| POST | `/brokers/connect` | Register a broker connection (simulated only for now). |
| GET | `/brokers` | List connections. |
| POST | `/live-deployments/request` | Request live deployment (gated). |

Live deployment is **disabled by default** and requires: a real broker adapter,
an approved paper-trading track record, `risk_acknowledged: true`, and
superuser review. The platform never executes automatically.

## Dashboard & misc

| Method | Path | Description |
|---|---|---|
| GET | `/dashboard` | KPIs (strategies, backtests, paper P&L). |
| GET | `/audit/events` | Audit log entries. |
| GET | `/market-data/symbols` | Symbols available from CSV provider. |
| POST | `/market-data/import` | Upload a candle CSV (multipart). |
| GET | `/health` | Liveness. |

## Errors

- `401` no/invalid token
- `403` insufficient privileges
- `404` resource not found or belongs to another workspace
- `409` duplicate (register)
- `422` validation error (schema / DSL)
- `500` internal error (never exposes internals)

All responses include a `X-Request-ID` header for correlation with logs.