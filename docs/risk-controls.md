# Risk Controls

This is the operating manual for the risk surface of FX Scalper Lab. It
documents every control, how to verify it, and the operational limits we
enforce so that overfitting or misconfiguration does not translate into
uncontrolled exposure.

All controls are layered: **data-quality -> strategy eligibility ->
execution gating -> account limits -> kill switches**.

## 1. Research-time controls

| Control | Implementation | Enforcement |
|---|---|---|
| No look-ahead | signals evaluated on closed bar; fill on next bar open | backtester + tests |
| Cost realism | spread + commission + slippage (+swap) model | `CostParams` |
| Data quality | reject NaN/non-finite OHLC, high<low, malformed order | `Backtester.validate_data` |
| Missing candles | gap detection, strict mode available | flag or raise |
| Overfit detection | temporal walk-forward IS/OOS split | `walk_forward_test` |
| Drawdown bands | Monte Carlo trade-order resampling | `run_monte_carlo_trade_order` |
| Anti-curve-fit | eligibility classification transparent & labeled | `classify_strategy` |

## 2. Strategy eligibility ladder

A strategy lives in exactly one of these states:

1. `rejected` - non-positive expectancy after costs, or failed thresholds.
2. `needs_review` - insufficient sample or marginal metrics.
3. `research_only` - passes minimums but not robust enough for paper trading.
4. `paper_trading_eligible` - passes minimums AND robustness gates.

Promotion is **not automatic**. Each rung requires explicit human action and
updated metrics.

## 3. Execution gating (RiskEngine)

Every proposed order (paper or live) passes through the same checks:

- Global / strategy / pair kill switch - hard stop, must be explicitly re-armed.
- Trading session (UTC windows) - default off-hours blocked.
- News blackout - no entries within configured minutes of a high-impact event.
- Spread ceiling - reject orders when instantaneous spread > `max_spread_pips`.
- Stop distance floor - reject stop-loss closer than `hard_stop_distance_pips`.
- Open position cap - `max_open_positions`.
- Trade frequency cap - `max_trades_per_day`, `max_consecutive_losses`.
- Daily loss limit - `max_daily_loss_pct` halts the day.
- Correlated exposure ceiling - `max_correlated_exposure_pct` across
  correlated pairs.

## 4. Kill switches

Three scopes, all with one-way enable + explicit disable:

- **Global** - halts every strategy instantly.
- **Per-strategy** - halts one strategy.
- **Per-pair** - halts one symbol.

State lives in Redis (cross-process) with an in-memory fallback and is
audited via `AuditService`. A global kill switch engaged during a session
stops new entries; existing stops/targets still manage open positions (no free
floating).

## 5. Operational limits (defaults)

| Limit | Default |
|---|---|
| Risk per trade | 0.25% |
| Max daily loss | 1.0% |
| Max weekly loss | 3.0% (schema supports; enforced when exceeded) |
| Max open positions | 1 |
| Max trades/day | 5 |
| Stop distance floor | configured per profile |
| Paper trading balance | seeded, simulated only |

## 6. Live deployment controls (default OFF)

A live-deployment request requires ALL of:

1. A registered **real** broker adapter (only `simulated` ships today).
2. Paper track record that is non-negative with sufficient trades.
3. `risk_acknowledged: true`.
4. Superuser review of the deployment request.

Even after approval, execution remains fully on the RiskEngine gating stack,
and any kill switch overrides it.

## 7. Verification procedure

1. Unit/integration tests are run in CI (`pytest`) and must pass before any
   deployment tag. The suite covers: pip math, position sizing, spread /
   slippage, SL/TP fills, stale/missing candle rejection, DST-safe sessions,
   look-ahead prevention, blackout no-trade, daily loss, correlated exposure,
   stop-distance floor, kill switches, DSL rejection, API authz, workspace
   isolation, walk-forward, and backtest reproducibility.
2. Run `pytest` locally: `cd backend && PYTHONPATH=. .venv/bin/pytest`.
3. Re-verify the eligibility ladder periodically on fresh data windows (N-month
   rolling reclassification).

## 8. Hard rules that never change

- No strategy runs without a completed backtest.
- No order is sent without RiskEngine approval.
- No kill switch is auto-rearmed.
- No live execution without an explicit human-approved deployment request.
- Backtests are deterministic and auditable (reproducible from stored spec).