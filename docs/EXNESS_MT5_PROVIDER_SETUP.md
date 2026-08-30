# Exness / MetaTrader 5 Provider + Real Historical Data — Setup Guide

This add-on lets the app connect to **Exness via MetaTrader 5** as a secure,
**read-only** data provider and run **Real Historical Data validations** of a
saved strategy against real historical candles that pass a data-quality gate.

> **Live trading stays disabled.** `LIVE_TRADING_ENABLED` is `false`; this
> feature never routes orders and never touches real funds. The connection is
> used only for research (historical candles) and read-only market data for
> display/signals/paper checks.

---

## 1. How it works

```
Browser (wizard) ──► API (auth + workspace-scoped) ──► ExnessProviderService
                                                        │  encrypt at rest (Fernet)
                                                        ▼
                                            ExnessMT5ProviderAdapter
                                                ├─ Server-side MT5 connector
                                                └─ MT5 Gateway Agent (read-only)
                                                        ▼
                                            RealHistoricalValidator
                              fetch ▸ quality gate ▸ bid/ask-aware execution ▸ report
```

- Credentials are **encrypted at rest server-side** (`app/core/secrets.py`,
  Fernet with a key from `SECRET_KEY`). The browser and logs/WS/exports never
  see them.
- Credential-changing actions (connect, pair, disconnect) require a
  **recent re-authentication** (`require_recent_auth`, 15-minute window) and are
  audit-logged with redacted detail.
- A **connection-attempt budget** (default 20 attempts / 5 minutes) rate-limits
  the wizard to throttle brute force.

## 2. Configuration (backend)

`.env` / environment:

| Setting | Default | Purpose |
| --- | --- | --- |
| `EXNESS_MOCK_ADAPTER` | `true` | Dev/test: uses the clearly-labelled mock adapter. **Must be `false` in production.** |
| `PROVIDER_REAUTH_WINDOW_SECONDS` | `900` | Re-auth window for credential mutations |
| `PROVIDER_CONNECT_MAX_ATTEMPTS_PER_WINDOW` | `20` | Attempt budget |
| `PROVIDER_CONNECT_WINDOW_SECONDS` | `300` | Budget window |
| `VALIDATION_ASYNC` | `false` | `true` queues validation to the RQ worker |
| `SECRET_KEY` | — | Fernet encryption key (existing setting) |

Database migrations are applied with `alembic upgrade head`.

## 3. Connecting a provider (two supported modes)

Use **Settings → Data providers** (or the connect card on Market Data /
Real Historical Data).

### A. Server-side MT5 (recommended for research)

Enter your MT5 **login** (numeric), **server** (e.g. `Exness-MT5Trial` for demo),
and **password**, then **Test connection**. If the capability report shows
`CONNECTED`, click **Connect (read-only)**.

- Credentials are stored encrypted on the backend only.
- The server-side connector is **not implemented in this mock build**; with
  `EXNESS_MOCK_ADAPTER=false` and no approved connector/gateway configured the
  app returns `UNSUPPORTED_ACCOUNT` rather than claiming access it cannot verify.
- The gateway/agent path below is the currently supported production path.

### B. Read-only MT5 Gateway Agent (advanced / production)

A small gateway agent runs on a machine with MT5 and connects **outbound** to
the backend:

1. In the wizard select **Read-only gateway agent (advanced)**.
2. Enter the gateway URL (e.g. `wss://gateway.example.local`), a device name,
   and the pairing code shown by the gateway terminal.
3. **Issue pairing token** → a short-lived token (encrypted at rest) is
   displayed once. Paste it into the gateway terminal.
4. **Verify gateway** → the backend marks the agent `ONLINE` (token invalid/expired
   → `401`). Then **Test connection** → **Connect (read-only)**.

The gateway is limited to a **read-only allow-list** (`read_only_capabilities`);
it cannot place orders.

## 4. Real Historical Data validation

**Backtest Lab → Real Historical Data**:

1. Pick one of your saved strategies (exact version is recorded).
2. Pick the provider symbol (auto-discovered/mapped) and timeframe.
3. Pick date range and realistic cost assumptions
   (spread model, commission/lot, adverse slippage, swap).
4. **Preview coverage** shows provider status, symbol-mapping status, expected
   candles and required warm-up before you spend time on a run.
5. **Run validation** → data is fetched, normalized (UTC, incomplete candles
   excluded, duplicates removed, gaps detected), and passed through the
   **data-quality gate** before any trade is computed.
6. Results show metrics, the quality report, equity/drawdown, a candlestick
   chart with entry/exit/stop/target markers, per-trade cost breakdowns, signals,
   and a **redacted JSON export**.

Guarantees: execution is look-ahead-safe (closed candles only), long entries use
the ask and exits the bid when bid/ask data is available (falls back to
estimated-spread labels otherwise), P&L and balances are computed in `Decimal`,
the quality gate can pause on provider outage, and complete runs are
reproducible from the stored source-data hash.

## 5. Testing

```
cd backend
.venv/bin/python -m pytest tests/test_exness_provider.py tests/test_real_historical_validation.py -q
```

See `tests/test_exness_provider.py` (connection surface + gateway pairing) and
`tests/test_real_historical_validation.py` (validator pipeline + quality gate).

## 6. Security notes

- No secret ever leaves the backend; exports are redacted (verified by tests).
- Cross-workspace access to connections, gateways, and validation runs is
  denied (404/401).
- Capabilities are never claimed to be available unless verified server-side.
- `live_trading_status` is always `disabled`.