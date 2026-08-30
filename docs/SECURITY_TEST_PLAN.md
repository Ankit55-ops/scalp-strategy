# Security Test Plan — FX Scalper Lab

Every flaw surfaced by the 2026 audit has a regression test in
`backend/tests/test_security_hardening.py` (36 tests) and a CI gate. Plan:

## 1. Commands

```bash
# regression suite (the security test plan in code)
cd backend
.venv/bin/python -m pytest tests/test_security_hardening.py -q        # 32 passed

# full suite
.venv/bin/python -m pytest -q                                          # 152 passed

# apply schema before running (provider_connections drift fix)
.venv/bin/python -m alembic upgrade head

# supply chain + static gates (also in CI)
.venv/bin/python -m pip_audit -r requirements.txt                      # NONE
cd ../frontend && npm audit --audit-level=high                          # 0 high, 0 critical
cd ../backend && .venv/bin/python -m bandit -r app -q                   # no HIGH
cd ../backend && .venv/bin/python -m ruff check app tests               # clean
cd ../frontend && npm run lint && npm run build                         # clean
```

## 2. Test-to-finding mapping

| Test | Finding | Asserts |
|---|---|---|
| `test_production_environment_requires_data_encryption_key` | C1 | fail-fast: production + empty `DATA_ENCRYPTION_KEY` -> ValueError |
| `test_data_encryption_key_must_decode_to_32_bytes` | C1 | bad-length key -> ValueError |
| `test_data_encryption_key_must_be_valid_base64` | C1 | non-base64 key -> ValueError |
| `test_jwt_algorithm_is_allow_listed` | C1 | `JWT_ALGORITHM=none` -> ValueError |
| `test_cors_origins_parses_as_json_array` | C1 | non-JSON `CORS_ORIGINS` -> ValueError |
| `test_jwt_roundtrip_honors_issuer_and_rejects_tampering` | C1 | iss pinned; tampered signature -> None |
| `test_jwt_rejects_expired_token` | C1 | expired token -> None |
| `test_paper_start_rejects_non_finite_balance` | C1b/H2 | `NaN`/`+Inf`/`-Inf` balance -> clean 422 (no 500) |
| `test_paper_order_rejects_non_finite_size` | C1b/H2 | raw `NaN` size body -> 422 |
| `test_security_headers_present` | C1c/F1 | nosniff/DENY/no-referrer/Permissions/CSP headers on API |
| `test_register_normalizes_email_case` | H1 | mixed-case email stored+logged lowercase |
| `test_register_rejects_long_password` | H1 | >72-char password -> 422 |
| `test_login_rejects_long_password` | H1 | >72-char password on login -> 422 |
| `test_login_lockout_after_consecutive_failures` | H1 | 9x 401, then 429 (+Retry-After); correct password also 429 during window |
| `test_paper_close_is_single_credited_and_idempotent` | H4 | close credits once; 2nd close -> ValueError, balance unchanged |
| `test_csv_candle_name_rejects_path_traversal` | H3 | `../`, `../../etc/passwd`, bad chars, overlong, bad timeframe -> ValueError; valid symbol normalized |
| `test_import_route_rejects_traversal_symbol` | H3 | route 422 on traversal symbol |
| `test_import_route_enforces_upload_size_cap` | H3 | oversize upload -> 413 |
| `test_backtest_concurrency_gate_blocks_active_jobs` | H5 | 2 running jobs + 1 more -> 429 |
| `test_ws_rejects_query_string_token_and_header_only` | M1 | `?token=` and missing subprotocol -> disconnect |
| `test_ws_accepts_valid_subprotocol_token` | M1 | valid subprotocol token -> snapshot received |
| `test_ws_rejects_disallowed_origin_and_enforces_connection_cap` | M1 | bad Origin -> disconnect; 2nd socket over cap -> disconnect |
| `test_strategy_spec_with_oversized_expression_rejected`* | M2 | DSL length/token/depth caps via tokenizer+parser |
| `test_money_math_rejects_non_finite`* | M3 | `money.*` operators reject NaN/Inf |
| `test_paper_broker_pnl_is_decimal`* | M3 | broker P&L matches Decimal arithmetic path |
| `test_provider_error_redacts_secrets_and_truncates`* | M4 | `_safe_error` redacts API key + caps length |
| `test_risk_engine_audit_is_attributed_to_engine`* | M5 | audit entry has `source: risk_engine`, actor None |

`*` utility-level tests cover the M-tier fixes (DSL caps, Decimal money,
provider redaction, audit attribution).

## 3. Manual checks (not scriptable)

1. `docker compose up` fails fast without `SECRET_KEY`/`DATA_ENCRYPTION_KEY`.
2. `/docs`, `/openapi.json` absent when `APP_ENV=production`.
3. Browser network tab: WS handshake requests contain NO token in the URL; the
   token appears only in the `Sec-WebSocket-Protocol` header.
4. Frontend dev tools -> Application -> Storage: an access token exists in
   sessionStorage, NOT localStorage.
5. Response headers on a sample API + frontend page include the security
   headers listed in `SECURITY_HARDENING_GUIDE.md` §5.

## 4. Coverage policy

- New security fix -> new regression test in `test_security_hardening.py`
  mapping to the finding in §2.
- Full suite must stay green before any merge; CI enforces the gates in §1.
- The suite runs against the local Postgres with mock providers; no external
  network dependency.