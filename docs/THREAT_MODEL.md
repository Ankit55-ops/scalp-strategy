# Threat Model

Reviewed against the codebase as hardened during the 2026 audit pass. It uses a
STRIDE-style checklist. The platform's stated risk posture is: **research +
paper trading by default; live execution gated, opt-in and OFF unless
explicitly enabled by an operator** (`LIVE_TRADING_ENABLED=false`,
`BROKER_PRACTICE_DRY_RUN=true`).

## Assets

- User accounts and password hashes (bcrypt).
- Strategy specs / intellectual property.
- Broker connection credentials (API keys) — encrypted at rest (Fernet via
  `DATA_ENCRYPTION_KEY`).
- Paper account state and paper positions (financial integrity).
- Backtest jobs and results.
- Audit logs (integrity matters, append-only at the app layer).
- Deployment request state (high integrity).

## Trust boundaries

| # | Boundary | Trust |
|---|---|---|
| 1 | Browser/client -> FastAPI | untrusted (any HTTP client) |
| 2 | Rule DSL expression (in spec) -> evaluator | untrusted input -> sandboxed evaluator |
| 3 | Strategy spec -> backtester | semi-trusted (user authored) |
| 4 | Market data provider / CSV import -> backtester | untrusted data |
| 5 | Backtester -> risk engine -> simulated broker | trusted research layer, gated |
| 6 | WebSocket client -> `/api/ws/market-data` | untrusted; auth required |
| 7 | Broker adapter -> live execution | OUT OF SCOPE until real adapters exist |

## Threats and mitigations

### T1 - AuthN credential stuffing / brute force (H1)
- bcrypt hashing; HTTP 401 with no user-enumeration hints on login.
- Per-(email+IP) consecutive-failure lockout: `LoginFailureLimiter`, sliding
  window (`AUTH_LOGIN_MAX_ATTEMPTS_PER_WINDOW=10`,
  `AUTH_LOGIN_WINDOW_SECONDS=600`), returns 429 + `Retry-After` on reaching the
  cap. Successful login resets the counter.
- Email normalization (strip + lowercase) on register/login prevents
  case-confusion bypass of the limiter.
- Global token-bucket rate limiting on all non-docs/health endpoints (`/api/auth/*`
  included) — Redis-backed with bounded in-memory fallback.
- Password length guard (max 72 chars — bcrypt's input limit) on register and
  login.

### T2 - JWT abuse (C1)
- HS256 only, allow-listed at config (`JWT_ALGORITHM` rejects anything else).
- `SECRET_KEY` minimum strength enforced by fail-fast config in non-dev envs;
  weak/placeholder values rejected at startup.
- Tokens pinned with `sub`, `iss` (`fxscalper-lab`), `iat`, `exp`, `jti`, `typ`;
  `decode_access_token` enforces algorithm + issuer.
- Default TTL 60 min (`ACCESS_TOKEN_EXPIRE_MINUTES`).
- Subject is the user UUID; user is re-fetched per request; inactive accounts
  rejected (DB and token path).

### T3 - Cross-workspace data access
- Every strategy/backtest/paper read verifies `workspace_id` matches the
  caller; otherwise 404 (no existence leak).
- Paper positions are credited only under the account owned by the caller's
  workspace; `close_position` verifies position belongs to the account.
- `test_workspace_isolation` covers row scoping.

### T4 - DSL injection / code execution (M)
- Hand-written recursive-descent parser; allow-list of functions/symbols.
- `eval`, `exec`, `__import__`, attribute access (`.`), statement separators
  (`;`) rejected at tokenizer/parser stage.
- Resource caps: `MAX_EXPRESSION_LENGTH=2048`, `MAX_EXPRESSION_TOKENS=512`
  (checked in `tokenize`), `MAX_EXPRESSION_DEPTH=40` (parser enter/leave
  counter).
- Expressions validated before persistence and before each backtest run.

### T5 - Secret exposure / broker credentials (M/H7)
- API keys encrypted at rest with Fernet (`DATA_ENCRYPTION_KEY`).
- `DATA_ENCRYPTION_KEY` must be provided and decode to exactly 32 bytes in
  non-dev envs (fail-fast); `.env` git-ignored; `.env.example` placeholders
  only.
- Error/audit surfaces are scrubbed: `provider_service._safe_error` caps to 400
  chars and redacts provider API keys before persistence/raise.
- Logs carry no sensitive fields; `poweredByHeader: false`; default secrets
  removed from docker-compose (now `SECRET_KEY`/`DATA_ENCRYPTION_KEY` envs are
  mandatory).

### T6 - Malicious / malformed market data (H3)
- CSV import: symbol passed through `_safe_candle_name` — regex allow-list
  `^[A-Z0-9]{1,16}$` + timeframe whitelist (`M1..D1`); path traversal and
  unsanitized names rejected with 422.
- Upload body capped at `UPLOAD_MAX_BYTES` (+1 read sentinel) -> 413; files
  written via `tempfile.mkstemp` (no client-controlled paths).
- NaN / non-finite OHLC rejected by the backtester data-quality gate.

### T7 - Cost/overfitting blind spots (research integrity, not security)
- Walk-forward OOS windows and Monte Carlo drawdown bands surfaced; strategy
  classification labeled as eligibility, never a profit prediction.

### T8 - Unauthorized power-upgrade (paper -> live)
- Live deployment gated: real broker adapter exists, risk acknowledged, paper
  track record non-negative, superuser review, `LIVE_TRADING_ENABLED` master
  kill. Broker connections with `sandbox=false` rejected until a real adapter
  is registered.

### T9 - Availability / resource exhaustion (H5, M, C1)
- Backtest concurrency gate: per workspace, active (queued/running) jobs >=
  `MAX_CONCURRENT_BACKTESTS_PER_WORKSPACE` -> 429.
- WebSocket: per-user connection cap (`MAX_CONCURRENT_WS_PER_USER=4`),
  server ping every 30s, snapshot on connect, origin + token checks before
  accept.
- Rate limiting protects shared endpoints; upload size caps; bounded in-memory
  rate-limiter map.

### T10 - Audit tampering (M)
- Audit records append-only from the app layer; risk decisions carry a
  correlation ID linking proposals to outcomes.
- `record_risk_decision` now attributes `actor_id=None` + `source:
  risk_engine` in the payload so automated risk gating is distinguishable from
  human action.

### T11 - WebSocket hijacking / token leakage (M)
- JWT travels in the `Sec-WebSocket-Protocol` subprotocol (browser cannot set
  arbitrary headers); `?token=` query strings are rejected (4401) so tokens
  never leak into access logs/history.
- Origin header checked against the CORS allow-list (4403) before accept —
  cross-site WebSocket hijacking blocked.

### T12 - Non-finite input / deserialization abuse (C1/H2)
- JSON bodies containing `NaN`/`Infinity` for money fields are rejected (422)
  by Pydantic validators; the validation-error handler scrubs non-finite
  echoed input so the error response serializes as valid JSON instead of 500.
- Money math is Decimal-backed (`app.services.money`), rejecting non-finite
  values; paper balances/size are bounded (`PAPER_MIN/MAX_BALANCE`,
  `size_units <= 1e9`, `PAPER_MAX_LEVERAGE`).

## Residual risks (accepted)

- `next@14.2.35` has remaining high advisories (9.5.0–15.5.20 set) whose fixed
  release is Next 16.x (breaking upgrade). The app does not use the affected
  features (middleware/rewrites/Server Actions/Image API); upgrade to Next 16
  is scheduled as a follow-up.
- `_persist_health` inside `_close_position_locked` commits mid-transaction;
  row locks used for close serialization are therefore released at that point.
  Sequential double-close is guarded; a concurrent double-close window remains
  theoretical and is mitigated by per-workspace single-user usage and WS/API
  rate limits. Recommended follow-up: make `_persist_health` participate in the
  caller's transaction.
- Real brokerage adapters (OANDA/Twelve Data) are not integration-tested
  against live accounts; fill/slippage models remain simulated.
- Mock market data is a random-walk generator; it under-tests regime changes
  and fat tails.
- gitleaks/semgrep/trivy/Docker/Redis are not installed on the dev host; those
  CI gates are configured but must be exercised in the CI image.

## Out of scope

- Real broker integrations until adapters are implemented and reviewed.
  Until then, live deployment is a sandbox that cannot reach a market.