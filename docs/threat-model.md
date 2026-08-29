# Threat Model

This document reviews the highest-risk trust boundaries in FX Scalper Lab.
It follows a STRIDE-style checklist. The platform's stated risk posture is:
**research + paper trading by default; live execution gated, opt-in and OFF
unless explicitly enabled by an operator.**

## Assets

- User accounts and passwords (bcrypt hashes).
- Strategy spec / intellectual property.
- Broker connection credentials (API keys) - encrypted at rest (Fernet).
- Paper account state.
- Backtest jobs and results.
- Audit logs (integrity matters).
- Deployment request state (high integrity requirement).

## Trust boundaries

| # | Boundary | Trust |
|---|---|---|
| 1 | Browser -> FastAPI | untrusted (any HTTP client) |
| 2 | Rule DSL expression (stored in spec) -> evaluator | untrusted input -> sandboxed evaluator |
| 3 | Strategy spec -> backtester | semi-trusted (user authored) |
| 4 | Market data provider -> backtester | untrusted data (CSV import) |
| 5 | Backtester -> risk engine -> simulated broker | trusted research layer, gated |
| 6 | Broker adapter -> live execution | OUT OF SCOPE until real adapters exist |

## Threats and mitigations

### T1 - AuthN credential stuffing / brute force
- bcrypt cost defaults; HTTP 401 with no user enumeration hints on login.
- Global rate limiting (Redis-backed, in-memory fallback) on all routes.
- Mitigation extension: per-account lockout + exponential backoff, MFA.

### T2 - JWT abuse
- HS256 with a server-side secret (`JWT_SECRET_KEY`); short-lived default TTL.
- JWT subject is the user UUID; user is re-fetched per request and inactive
  accounts are rejected.
- Mitigation extension: rotating keys, allow-list for refresh tokens.

### T3 - Cross-workspace data access
- Every strategy/backtest read verifies `workspace_id` matches the caller's
  workspace; otherwise 404 (no existence leak).
- Future: enforce per-row ownership at the repository layer + row scoping in
  tests (already covered by `test_workspace_isolation`).

### T4 - DSL injection / code execution
- The evaluator is a hand-written recursive-descent parser with an explicit
  allow-list of functions and symbols. `eval`, `exec`, `__import__`,
  attribute access (`.`), and statement separators (`;`) are rejected at the
  tokenizer/parser stage and verified by tests.
- Rule expressions are validated before persistence AND before each backtest
  run; malformed expressions never reach the engine.

### T5 - Secret exposure / broker credentials
- API keys stored with Fernet field-level encryption (`ENCRYPTION_KEY`).
- `.env` is git-ignored; `.env.example` holds placeholders only.
- No secrets are logged (structured logging strips sensitive fields).
- Mitigation risk: `ENCRYPTION_KEY` and `JWT_SECRET_KEY` are defaulted in
  docker-compose for local dev; production must override - see
  `docs/risk-controls.md`.

### T6 - Malicious / malformed market data (CSV import)
- CSV import parses into float fields; NaN / non-finite and invalid OHLC
  ordering are rejected by the backtester's data-quality gate before any
  trading logic runs.
- Missing-candle gaps are flagged (optionally hard-fail in strict mode).

### T7 - Cost/overfitting blind spots (research integrity, not security)
- Walk-forward OOS windows and Monte Carlo drawdown bands are surfaced in
  results; `classify_strategy` explicitly labels output as an eligibility
  classification, never a profit prediction.
- `plain_english_explanation`, `assumptions` and `failure_modes` are required
  fields on every spec.

### T8 - Unauthorized power-upgrade (paper -> live)
- Live deployment requires: real broker adapter exists, risk acknowledged,
  paper track record non-negative and sufficient, and superuser review.
- Broker connections with `sandbox=false` are rejected until a real adapter is
  registered. This policy is re-tested in CI.

### T9 - Availability / resource exhaustion
- Rate limiting protects shared endpoints; backtests are synchronous and
  bounded by configured data windows.
- Mitigation extension: background job queue (RQ present in dependencies),
  request size limits on `/market-data/import`.

### T10 - Audit tampering
- Audit records are append-only from the application layer; risk decisions
  carry a correlation ID that links proposals to outcomes.
- Mitigation extension: hash-chain / append-only store, periodic export.

## Residual risks (accepted)

- Dependency CVEs are NOT auto-scanned locally; pin versions and run `pip
  audit`/`npm audit` in CI.
- Without a real broker adapter, slippage and fill models remain simulated;
  live results will differ from backtests.
- The mock market data generator is random-walk only; it under-tests regime
  changes and fat tails.

## Out of scope

- Real broker integrations (OANDA/IB/etc.) until adapters are implemented and
  reviewed. Until then, live deployment is a sandbox that cannot reach a
  market.