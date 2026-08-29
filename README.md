# FX Scalper Lab

**An AI-powered forex scalping strategy research, visual backtesting, explanation, and paper-trading platform.**

> **Educational / research only.** FX Scalper Lab does **not** make profit guarantees, does **not** provide investment
> recommendations, and does **not** place live orders by default. Live execution is disabled unless a user explicitly
> enables it, connects a supported (sandbox) broker, completes risk configuration, and confirms every deployment step.
> Trading forex on margin is highly speculative and involves substantial risk of loss.

---

## System Requirements

- Python 3.10+
- Node.js 18+
- PostgreSQL (with optional TimescaleDB extension)
- Redis

These are required **only** if you run the full stack outside Docker. A local PostgreSQL and Redis can be used for
development without Docker (the compose file is also provided for containerized setups).

---

## Repository Layout

```
fx-scalper-lab/
├── backend/                 # Python FastAPI application
│   ├── app/
│   │   ├── api/             # REST routes
│   │   ├── core/            # config, security, logging
│   │   ├── models/          # SQLAlchemy ORM models
│   │   ├── schemas/         # Pydantic schemas
│   │   ├── services/        # business logic
│   │   ├── providers/       # MarketDataProvider / BrokerProvider adapters
│   │   ├── backtest/        # event-driven backtester, cost model, metrics
│   │   ├── dsl/             # safe strategy rule DSL interpreter
│   │   ├── risk/            # centralized risk engine
│   │   ├── ai/              # provider-agnostic LLM / mock strategy architect
│   │   └── db/              # engine, session, seeding
│   ├── alembic/             # DB migrations
│   ├── scripts/             # CSV import, seed helpers
│   ├── data/                # sample historical CSV data
│   └── tests/               # pytest suite
├── frontend/                # Next.js + TypeScript + Tailwind
├── infra/                   # Docker Compose, nginx, etc.
├── docs/                    # architecture, threat model, risk controls
└── scripts/                 # convenience scripts
```

---

## Quick Start (local, without Docker)

### 1. Backend

```bash
cd ~/dev/fx-scalper-lab/backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Prepare environment
cp ../.env.example .env          # then edit DATABASE_URL / SECRET_KEY

# Create + migrate the database
createdb fxscalper               # if not present
alembic upgrade head

# Seed data (sample CSV + demo user + mock symbols)
python -m scripts.seed

# Optional: regenerate synthetic sample CSV files into backend/data/
python -m scripts.sample_data

# Run the API server (hot reload)
uvicorn app.main:app --reload --port 8000
```

API docs: http://localhost:8000/docs

### 2. Frontend

```bash
cd ~/dev/fx-scalper-lab/frontend
npm install
export NEXT_PUBLIC_API_URL=http://localhost:8000/api   # or set in .env.local
npm run dev                        # http://localhost:3000
```

The app defaults to `http://localhost:8000/api` when `NEXT_PUBLIC_API_URL` is unset.

### 3. Run the tests

```bash
cd ~/dev/fx-scalper-lab/backend
source .venv/bin/activate
pytest -q
```

---

## Docker Compose

A full containerized stack is provided in `infra/`:

```bash
cd ~/dev/fx-scalper-lab/infra
cp ../.env.example ../.env
docker compose up --build
```

This starts PostgreSQL, Redis, the FastAPI backend, and the Next.js frontend.

---

## Default demo account

After seeding, a demo user is available (for development only — change before any real use):

- email: `demo@fxscalper.dev`
- password: `demo-password`

---

## Documentation

- [Architecture](docs/architecture.md)
- [API Reference](docs/api.md)
- [Threat Model](docs/threat-model.md)
- [Risk Controls](docs/risk-controls.md)
- [Disclaimer](docs/DISCLAIMER.md)

---

## Safety & Compliance Summary

- Validation-first: strategies are Pydantic-validated JSON; no executable code is ever run from an LLM.
- The rule DSL uses a safe, allow-listed expression engine — **no `eval()`**.
- A centralized risk engine gates every simulated and live order.
- Live execution is off by default and requires a multi-step explicit approval workflow.
- Secrets are encrypted at rest and never logged or shipped to the client.
