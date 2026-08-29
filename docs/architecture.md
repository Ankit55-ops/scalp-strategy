# Architecture

FX Scalper Lab is a full-stack, educational forex scalping research platform.
It separates research (strategy design, backtesting, explanation) from
paper-trading (simulated execution) and treats **live execution as a gated,
opt-in, sandboxed feature that is OFF by default**.

## High-level topology

```
Browser (Next.js)
   │  HTTPS / JSON
   ▼
FastAPI backend  ──── RateLimit / RequestID / CORS middleware
   │
   ├── Router layer      auth, strategies, backtests, risk, paper,
   │                     brokers, live-deployments, dashboard, audit,
   │                     market-data, health
   ├── Services          strategy_service, backtest_service, market_math,
   │                     audit, deployment_service
   ├── Domain engines    dsl (safe evaluator), risk engine, kill switch,
   │                     backtester, cost, indicators, metrics, sessions,
   │                     validation (walk-forward / Monte Carlo)
   ├── AI layer          architect (mock + OpenAI-compatible LLM)
   └── Providers         mock / CSV market data, simulated broker (factory)
           │
           ▼
PostgreSQL ── SQLAlchemy ORM + Alembic migrations   Redis ── kill switch, rate limiting, jobs
```

## Process flow

1. **Strategy authoring.** The UI (or AI architect) produces a
   `StrategySpec` (pydantic): family, pairs, timeframes, sessions, indicators,
   entry/exit rules expressed in the **safe DSL**, risk-management params and
   execution filters. The DSL is validated at ingest (no `eval`).
2. **Explanation.** Every spec carries `plain_english_explanation`,
   `assumptions`, and `failure_modes` written in plain language so the user can
   judge the hypothesis without trusting a black box.
3. **Backtesting.** `Backtester` is an event-driven, deterministic engine. It
   consumes candles, evaluates entry rules on a bar's close and fills on the
   next bar's open (plus slippage/spread) - **no look-ahead**. The cost model
   applies spread, commission, slippage and swap. A data-quality gate rejects
   NaN prices, invalid OHLC ordering and (optionally) missing candles.
4. **Metrics & validation.** `compute_metrics` produces profit factor, win
   rate, expectancy, Sharpe/Sortino, max drawdown, session/pair/monthly
   breakdowns. `classify_strategy` converts metrics + robustness into an
   eligibility status (`rejected` -> `needs_review` -> `research_only` ->
   `paper_trading_eligible`). Walk-forward tests OOS windows; Monte Carlo
   estimates drawdown bands. **Classification is eligibility, not a forecast.**
5. **Risk gating.** `RiskEngine` re-checks live and paper orders (kill switch,
   session, blackout, spread, open positions, daily loss, stop distance,
   correlated exposure) and writes immutable audit records.
6. **Paper trading.** `SimulatedBroker` executes only risk-approved orders
   against a virtual account; results stay inside the simulation.
7. **Live deployment.** Requested via `live-deployments`, requires a real
   broker adapter (currently only simulated), an approved paper track record,
   a risk acknowledgement, and a deployment request that is foreign to
   automatic execution.

## Key invariants

- **Deterministic backtests** - same candles + same spec = same output.
- **No look-ahead** - signals are evaluated on closed bars only.
- **Audit trail** - risk decisions and mitigations are append-only.
- **No execution without approval** - the risk engine is the only path to an
  order, and live orders require explicit human acknowledgement.

## Security boundaries

- JWT bearer auth; passwords bcrypt-hashed; secrets encrypted at rest
  (Fernet) before reaching PostgreSQL.
- The DSL surface is intentionally tiny; unsafe constructs are rejected.
- Rate limiting and request IDs are applied globally.
- See `docs/threat-model.md` for a structured threat review.

## Persistence model

26 SQLAlchemy models cover users/workspaces, market data metadata, strategies
and versions, backtest jobs/runs/metrics/simulated orders, paper accounts and
positions, broker connections, live deployment requests, risk profiles/events,
audit logs, alerts, and saved chart layouts. Schema is managed by Alembic.