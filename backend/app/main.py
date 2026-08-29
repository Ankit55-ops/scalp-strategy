"""FX Scalper Lab FastAPI application entrypoint."""

from __future__ import annotations

from contextlib import asynccontextmanager

import redis as redis_pkg
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.routes import (
    audit,
    auth,
    backtests,
    brokers,
    dashboard,
    deployments,
    health,
    paper,
    risk,
    strategies,
)
from app.core.config import get_settings
from app.core.logging import setup_logging
from app.core.middleware import RateLimitMiddleware, RequestIDMiddleware

settings = get_settings()
setup_logging(settings.LOG_LEVEL)


def _redis_client():
    try:
        client = redis_pkg.Redis.from_url(settings.REDIS_URL, socket_connect_timeout=1)
        client.ping()
        return client
    except Exception:  # noqa: BLE001 - Redis optional
        return None


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Warm DB connection.
    try:
        from app.db.session import engine

        with engine.connect() as conn:
            conn.execute(__import__("sqlalchemy").text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"database unavailable: {exc}") from exc
    yield


app = FastAPI(
    title="FX Scalper Lab API",
    version="0.1.0",
    description="AI-powered forex scalping research, backtesting, explanation, and paper trading.",
    lifespan=lifespan,
)

app.add_middleware(RequestIDMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(RateLimitMiddleware, redis_client=_redis_client())


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    return JSONResponse(
        status_code=500,
        content={"detail": "internal server error"},
    )


# Router mounting: all routes under /api.
api_prefix = "/api"
app.include_router(health.router, prefix=api_prefix)
app.include_router(auth.router, prefix=api_prefix)
app.include_router(strategies.router, prefix=api_prefix)
app.include_router(backtests.router, prefix=api_prefix)
app.include_router(risk.router, prefix=api_prefix)
app.include_router(paper.router, prefix=api_prefix)
app.include_router(brokers.router, prefix=api_prefix)
app.include_router(deployments.router, prefix=api_prefix)
app.include_router(dashboard.router, prefix=api_prefix)
app.include_router(audit.router, prefix=api_prefix)


@app.get("/", include_in_schema=False)
def root() -> dict:
    return {"name": "FX Scalper Lab API", "docs": "/docs", "status": "online"}
