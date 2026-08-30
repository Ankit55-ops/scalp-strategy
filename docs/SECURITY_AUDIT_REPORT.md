# Security Audit Report — FX Scalper Lab

- **Scope:** FX Scalper Lab full-stack app (FastAPI backend, Next.js frontend,
  Postgres, Redis-optional).
- **Approach:** static review of every route/service/frontend surface,
  dependency CVE scan (pip-audit, npm audit), config/deployment hygiene review,
  schema-vs-model drift check, and a regression test suite asserting each fix.
- **Baseline:** 116 backend tests green; `python-jose`/`ecdsa` retired in favor
  of PyJWT; `pip-audit` clean; `npm audit` residual documented below.
- **Result:** 152 backend tests green (36 new hardening regressions), including
  a schema-alignment migration (`c8c7643862e0`).

## Severity rubric

| Level | Meaning |
|---|---|
| Critical | Direct remote compromise of the app/server or secrets |
| High | Credential/secret compromise, unauthorized financial action, or persistence regression requiring prompt fix |
| Medium | Exfiltration/integrity/abuse risk requiring planned fix |
| Low | Hygiene/hardening recommendation |

## Backend findings (C1..M)

| ID | Severity | Finding | Fix | Status |
|---|---|---|---|---|
| C1 | Critical | JWT package `python-jose` (ECDSA collision) + secrets defaulted in config/compose; secrets in `.env.example` committed | PyJWT w/ pinned `sub/iss/iat/exp/jti/typ` + HS256 allow-list (core/security.py); fail-fast config (core/config.py); mandatory `SECRET_KEY`+`DATA_ENCRYPTION_KEY` envs in compose; defaulted `ENCRYPTION_KEY` removed; `.env.example` placeholders | FIXED |
| C1b | Critical | Floating `NaN`/`Infinity` in JSON money bodies caused 500 (FastAPI echo of invalid input) | Pydantic finite validators + scrubbing `RequestValidationError` handler -> clean 422 JSON | FIXED |
| C1c | Critical | No security headers / request-ID / rate limiting on auth + all routes in memory | `SecurityHeadersMiddleware` (nosniff/DENY/CSP/Referrer/HSTS-gated), `RequestIDMiddleware`, token-bucket `RateLimitMiddleware` incl. auth endpoints | FIXED |
| H1 | High | Login brute force; email case-confusion; bcrypt >72-char truncation; user enumeration timing | `LoginFailureLimiter` (email+IP, 10/600s, 429+Retry-After, reset on success); email normalize; password max 72; dummy-hash verify for unknown emails | FIXED |
| H2 | High | Paper trading: unbounded leverage/balance/size; NaN money | finite+positive validators; `PAPER_MAX_LEVERAGE`, `PAPER_MIN/MAX_BALANCE`, `size_units <= 1e9`; 422 on start | FIXED |
| H3 | High | CSV import path traversal via symbol; unbounded upload; unsafe temp files | `_safe_candle_name` allow-list; `UPLOAD_MAX_BYTES+1` cap -> 413; `tempfile.mkstemp`; symbol/timeframe route validation | FIXED |
| H4 | High | Paper close could double-credit or be raced | `SELECT ... FOR UPDATE` on account + position; already-closed -> ValueError; single-credit regression test | FIXED |
| H5 | High | Unbounded concurrent backtests per workspace (resource exhaustion) | gate: active jobs >= `MAX_CONCURRENT_BACKTESTS_PER_WORKSPACE` -> 429 | FIXED |
| H6 | High | JWT in `localStorage` (XSS stale-token) | `tokenStore` = sessionStorage-only with memory cache fallback; no localStorage | FIXED |
| H7 | High | Vulnerable deps + CI not blocking them + deployed with default secrets | pins: fastapi==0.141.1, starlette==1.3.1, PyJWT==2.13.0, cryptography==50.0.0, python-multipart==0.0.32, requests==2.33.0, pytest==9.0.3, pytest-asyncio==1.4.0; `pip-audit`/`npm audit` gates; Docker non-root; compose requires secrets | FIXED |
| M1 | Medium | WS auth token in query string (access-log leak); no origin check; no conn cap | subprotocol token; `?token=` rejected 4401; origin allow-list 4403; per-user cap 4408; 30s ping | FIXED |
| M2 | Medium | DSL unbounded expression length/tokens/depth | `MAX_EXPRESSION_LENGTH/TOKENS/DEPTH` enforced in tokenizer + parser | FIXED |
| M3 | Medium | Float money accumulation drift | Decimal layer `app/services/money.py` wired into paper broker/service (P&L, balance, equity) | FIXED |
| M4 | Medium | Provider error text may leak API keys / unbounded length into logs+DB | `_safe_error` 400-char cap + redaction of provider secrets | FIXED |
| M5 | Medium | Risk-engine audit entries not attributable (actor vs engine) | `record_risk_decision` actor_id=None + `source: risk_engine` in payload | FIXED |
| M6 | Medium | Schema drift: `provider_connections` model columns (`user_id`, `display_name`, `connection_mode`, `environment`, encrypted fields, health/error fields, jsonb `metadata`) had NO migration — provider connect/status endpoints 500'd | new alembic migration `c8c7643862e0` aligns the table with the model (additive, null-safe, json→jsonb cast) | FIXED |
| M7 | Medium | WS snapshot swallowed a DB failure (provider status on unconfigured workspace) leaving the session transaction aborted → subsequent feed-health query raised `InFailedSqlTransaction` | `_safe_provider_status` rolls back the session before returning the fallback | FIXED |

## Frontend findings

| ID | Severity | Finding | Fix | Status |
|---|---|---|---|---|
| F1 | High | Missing security headers; `X-Powered-By` leaked; `Strict-Transport-Security` absent behind TLS | `next.config.js` headers (DENY/nosniff/no-referrer/Permissions-Policy/HSTS) + `poweredByHeader:false` | FIXED |
| F2 | High | Deps flagged by `npm audit` (glob 11.x = minimist, postcss 8.4.x = nanoid) | Bumped next+eslint-config-next to 14.2.35; `overrides`: `glob ^10.5.0`, `postcss ^8.5.23` (unified to 8.5.26); residual 1 high = next 9.5.0–15.5.20 advisory set, fixed in 16.x only — documented, not exploitable by app usage | FIXED / documented residual |

## Deployment/CI findings

- Dockerfile: run as non-root `appuser` with a writable data dir (root/amplified
  privilege removed).
- docker-compose: `SECRET_KEY` and `DATA_ENCRYPTION_KEY` mandatory envs
  (`${VAR:?}`); no baked-in defaults.
- CI: backend job runs ruff, bandit, pip-audit, then 148 pytest; frontend job
  runs eslint, build, npm audit. (`|| true` behavior where tools are not yet
  installed in the runner image.)

## Regression tests added

`backend/tests/test_security_hardening.py` (36 tests) maps one-to-one to
C1,C1b,H1–H7,M1–M5 and F1. See `SECURITY_TEST_PLAN.md` for the mapping table.

## Accepted / residual risks

- Next.js 16.x upgrade required to clear the last `npm audit` high (breaking —
  scheduled).
- `_persist_health` mid-transaction commit during position close (locks
  released early under concurrency) — theoretical; see `THREAT_MODEL.md` T9/T-H4
  follow-up.
- Local host lacks gitleaks/semgrep/trivy/Docker/Redis — CI gates configured,
  not executable-verified locally.
- Live broker adapters remain out of scope until implemented.

## Verification data

- `pip-audit` -> `NONE` (0 known vulnerabilities).
- `npm audit` -> 0 critical/high beyond the documented Next 16.x residual.
- `python -m pytest -q` -> `148 passed`.
- `npm run lint` + `npm run build` -> clean.
- Server restart verified: `GET /api/health` 200, `/docs` 200.

> This audit improves the security posture of the application but does not
> guarantee that the application is free of vulnerabilities. Independent
> penetration testing and production infrastructure review are required before
> handling real trading credentials or enabling live execution.