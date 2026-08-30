# Security Hardening Guide — FX Scalper Lab

Operational guide for deploying and operating the hardened app. Assume you are
NOT running in `development` whenever this guide says "production-ish"; the app
fails closed on missing/weak secrets in any non-dev `APP_ENV`.

## 1. Secrets

Generate fresh, strong secrets:

```bash
# SECRET_KEY — any dev environment, CI, or deployment
openssl rand -hex 32

# DATA_ENCRYPTION_KEY — 32 random bytes, urlsafe base64
python -c "import base64,os;print(base64.urlsafe_b64encode(os.urandom(32)).decode())"
```

Required in any non-dev env (`APP_ENV` in
`{"production"}` or anything not in the dev allow-list):

- `SECRET_KEY` >= 32 chars and not a known placeholder (see
  `app/core/config.py` `WEAK_SECRETS`).
- `DATA_ENCRYPTION_KEY` non-empty and decoding to exactly 32 bytes.
- `LLM_API_KEY` when `LLM_PROVIDER=llm`.

```bash
export APP_ENV=production
export SECRET_KEY=$(openssl rand -hex 32)
export DATA_ENCRYPTION_KEY=$(python -c "import base64,os;print(base64.urlsafe_b64encode(os.urandom(32)).decode())")
```

## 2. Non-negotiable security settings

| Variable | Default | Purpose |
|---|---|---|
| `JWT_ALGORITHM` | `HS256` | allow-listed; anything else fails startup |
| `JWT_ISSUER` | `fxscalper-lab` | tokens pinned to this issuer |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | `60` | short-lived access tokens |
| `CORS_ORIGINS` | `["http://localhost:3000","http://127.0.0.1:3000"]` | JSON array; used by middleware + WS Origin check |
| `LIVE_TRADING_ENABLED` | `false` | master kill for real execution |
| `BROKER_PRACTICE_DRY_RUN` | `true` | keep practice trades paper-only |
| `TRUST_PROXY_HEADERS` | `false` | set `true` ONLY behind a proxy that overwrites X-Forwarded-For |
| `ENABLE_HSTS` | `false` | set `true` once the API is 100% HTTPS |

## 3. Controls you can tune (with care)

- `AUTH_LOGIN_MAX_ATTEMPTS_PER_WINDOW` (10) / `AUTH_LOGIN_WINDOW_SECONDS` (600):
  login lockout. Lower for stricter anti-brute-force, raise for headless demos.
- `RATE_LIMIT_DEFAULT` (120/min) / `RATE_LIMIT_WINDOW_SECONDS` (60): API token
  bucket. `/docs`, `/openapi.json`, `/health`, `/ready` are exempt by design.
- `UPLOAD_MAX_BYTES` (8 MiB): CSV import cap.
- `MAX_CONCURRENT_BACKTESTS_PER_WORKSPACE` (1): backtest gate.
- `MAX_CONCURRENT_WS_PER_USER` (4): WS connection cap.
- `MAX_EXPRESSION_LENGTH` (2048) / `MAX_EXPRESSION_TOKENS` (512) /
  `MAX_EXPRESSION_DEPTH` (40): DSL resource caps.
- `PAPER_MAX_LEVERAGE` (20), `PAPER_MIN_BALANCE` (1000),
  `PAPER_MAX_BALANCE` (1e7): paper financial bounds.

## 4. Authentication / session checklist

- Token storage is sessionStorage-only in the browser (never localStorage),
  with an in-memory cache fallback when storage is unavailable. Do NOT revert
  to localStorage.
- WS auth uses the `Sec-WebSocket-Protocol` subprotocol, never `?token=`
  query strings (rejected 4401).
- Frontend retains an `Authorization: Bearer <token>` header on REST calls;
  tokens are short-lived (60 min).

## 5. TLS, headers, proxies

- Backend headers (API): `X-Content-Type-Options: nosniff`, `X-Frame-Options:
  DENY`, `Referrer-Policy: no-referrer`, `Permissions-Policy: camera=(),
  microphone=(), geolocation=()`, `Content-Security-Policy: default-src
  'none'; frame-ancestors 'none'; base-uri 'none'`.
- Frontend headers (Next): same plus `Strict-Transport-Security` and
  `poweredByHeader: false`.
- HSTS on the backend is opt-in via `ENABLE_HSTS` — enable once fully HTTPS,
  else clients behind HTTP break.
- Set `TRUST_PROXY_HEADERS=true` only when the edge proxy guarantees
  `X-Forwarded-For` correctness; otherwise rate-limit/lockout keys use the
  direct socket IP.

## 6. Dependency / supply-chain hygiene

- Backend pins in `backend/requirements.txt` (fastapi/starlette/PyJWT/
  cryptography/requests/passlib + bcrypt==4.0.1 — do NOT move to bcrypt>=4.1,
  passlib 1.7.4 is incompatible).
- CI enforces `pip-audit` (must report `NONE`) and `npm audit`
  (0 critical/high) plus ruff + bandit.
- Frontend `overrides` keep `glob` and `postcss` on patched versions
  (`glob ^10.5.0`, `postcss ^8.5.23`).
- Known residual: `next@14.2.35` is the newest 14.x that still lists a high
  advisory set fixed only in Next 16.x. Plan the 16.x upgrade; the app uses
  none of the affected features (no middleware/rewrites/Server Actions/Image
  API).

## 7. Deployment checklist (before ANY real credentials or live execution)

- [ ] `APP_ENV` is a production-ish value and `SECRET_KEY` +
  `DATA_ENCRYPTION_KEY` are set (startup fails otherwise).
- [ ] `LIVE_TRADING_ENABLED=false`, `BROKER_PRACTICE_DRY_RUN=true`.
- [ ] Run as non-root (the provided Dockerfile already runs `appuser`).
- [ ] Run `pip-audit -r backend/requirements.txt`, `npm audit`, `bandit -r
      backend/app`, `ruff check backend/app frontend`.
- [ ] Execute the whole backend suite: `python -m pytest -q` (148 passing).
- [ ] `TRUST_PROXY_HEADERS` + `ENABLE_HSTS` set correctly for the proxy model.
- [ ] Confirm `/docs` and `/openapi.json` are disabled when
      `APP_ENV=production`.
- [ ] Never log `SECRET_KEY`, `DATA_ENCRYPTION_KEY`, user passwords, JWTs, or
      provider API keys.

## 8. Recommended follow-ups

1. **Migrate financial floats to NUMERIC(20,6)-backed columns.** The Decimal
   layer already covers in-process math; a migration removes float drift at
   rest (`alembic revision` after model change).
2. **Make `feed_health._persist_health` join the caller's transaction** so the
   `FOR UPDATE` close-serialization locks are not released mid-operation.
3. **Upgrade Next.js to 16.x** once the routes are migrated (clears the last
   `npm audit` high).
4. Enable the missing local tooling (Docker, Redis, gitleaks, semgrep, trivy)
   so the CI gates run end-to-end on a dev host too.
5. Prefer refresh-token rotation over bare access tokens when adding longer
   sessions.
6. **Keep `provider_connections` in sync:** the hardened baseline applies
   migration `c8c7643862e0` (model columns for gateway/broker connections,
   json→jsonb `metadata`). Any fresh clone must run `alembic upgrade head`
   before `pytest`; the suite asserts provider connect/status endpoint behavior.