# Runbooks — FX Scalper Lab

Local runbooks for the backend + frontend under `@ankitpaudel` on macOS
(darwin). Paths assume working dir `/Users/ankitpaudel/dev/fx-scalper-lab`.

## 1. Backend (FastAPI, uvicorn)

```bash
cd backend
# dev server on :8000 with auto-reload (background, log to file)
nohup .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload \
  > /tmp/fxsl-backend.log 2>&1 & echo $! > /tmp/fxsl-backend.pid

# restart (pick up config/code changes without --reload)
kill $(cat /tmp/fxsl-backend.pid) 2>/dev/null
nohup .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000 \
  > /tmp/fxsl-backend.log 2>&1 & echo $! > /tmp/fxsl-backend.pid

# verify
curl -s http://localhost:8000/api/health   # {"status":"ok"}
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8000/docs
```

Startup invariants after the hardening pass:
- In `test`/`development` weak secrets only warn; in any other `APP_ENV` a weak
  `SECRET_KEY`, missing `DATA_ENCRYPTION_KEY`, or missing `LLM_API_KEY`
  (when provider=llm) aborts startup.
- `/docs` and `/openapi.json` are absent when `APP_ENV=production`.

## 2. Frontend (Next.js)

```bash
cd frontend
npm run dev            # :3000, dev
npm run build          # standalone + lint-clean build (CI parity)
npm run lint

# restart dev server (pid saved elsewhere)
# e.g. kill $(cat /tmp/fxsl-frontend.pid); nohup npm run dev > /tmp/fxsl-frontend.log 2>&1 & echo $! > /tmp/fxsl-frontend.pid
```

Known good: `next@14.2.35`, `eslint-config-next@14.2.35`, `glob ^10.5.0`,
`postcss ^8.5.23` pinned via `overrides` in `package.json`. If `npm install`
prints `allow-scripts` warnings (fsevents/unrs-resolver) it is non-blocking.

## 3. Tests

```bash
# backend — full suite (needs local Postgres + alembic head applied)
cd backend
.venv/bin/python -m pytest -q                      # 148 passing

# hardening regressions only
.venv/bin/python -m pytest tests/test_security_hardening.py -q   # 36 passing
```

Network notes for the suite:
- Uses `postgresql+psycopg://ankitpaudel@localhost:5432/fxscalper`.
- Runtime is `mock` market data + `simulated` broker — no external APIs.
- Do not leave direct service sessions open after a raised exception: tests
  that intentionally raise (e.g. double paper close) must `svc.db.close()` or
  the FOR UPDATE locks block the cascade cleanup DELETE.

## 4. Security scanners

```bash
# supply chain — must both be clean
cd backend && .venv/bin/python -m pip_audit -r requirements.txt
cd frontend && npm audit --audit-level=high

# static
cd backend && .venv/bin/python -m bandit -r app -q     # no HIGH
cd backend && .venv/bin/python -m ruff check app tests

# prompt check on the live server
curl -s http://localhost:8000/api/health
```

Expected output: `pip-audit` -> `NONE`; bandit -> no HIGH findings; ruff ->
no errors; npm audit -> 0 high/critical (one documented info-level residual for
Next <=14.2.35).

## 5. Operational day-to-day

| Scenario | Action |
|---|---|
| Backend won't start | Read `/tmp/fxsl-backend.log`; check config fail-fast messages (SECRET_KEY/DATA_ENCRYPTION_KEY/LLM_API_KEY); ensure Postgres up |
| Health endpoint 5xx | `psql -U <user> -d fxscalper` reachable? `redis` optional; if unavailable RateLimit falls back in-memory |
| 429s everywhere | Rate limit window — check `RATE_LIMIT_DEFAULT`, or a stuck background client; logs carry `X-Request-ID` for correlation |
| Account locked (429 on login) | Failed-login lockout 10/600s per (email, IP); wait for the window or adjust `AUTH_LOGIN_MAX_ATTEMPTS_PER_WINDOW` |
| WS won't connect | Token must go in `Sec-WebSocket-Protocol`; Origin must match `CORS_ORIGINS`; cap is `MAX_CONCURRENT_WS_PER_USER` |
| Google/HSTS problem after enabling TLS | `ENABLE_HSTS` only after 100% HTTPS (else browsers keep a 1-year cache) |

## 6. Key rotation

- `SECRET_KEY` rotation invalidates all issued JWTs — schedule at a quiet
  window, restart uvicorn, keep old JWT_ISSUER consistent.
- `DATA_ENCRYPTION_KEY` rotation requires re-encrypting broker secrets stored
  with Fernet; perform a data-migration step before flipping the variable.
  Keep the old key available until all records are re-encrypted.

## 7. Incident cheat-sheet

1. Capture request: every response echoes `X-Request-ID`; correlate with
   backend logs.
2. Audit trail: risk decisions are `source: risk_engine`
   (`record_risk_decision`), human actions carry `actor` — filter by both.
3. Kill real-trade switch instantly: set `LIVE_TRADING_ENABLED=false` and
   restart backend (master kill; practice dry-run stays `true`).
4. Rotate provider API keys and `DATA_ENCRYPTION_KEY` if either is suspected
   leaked.