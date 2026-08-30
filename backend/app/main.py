"""FX Scalper Lab FastAPI application entrypoint."""

from __future__ import annotations

import math
from contextlib import asynccontextmanager

import redis as redis_pkg
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.routes import (
    alerts,
    audit,
    auth,
    backtests,
    brokers,
    chart_layouts,
    dashboard,
    deployments,
    exness,
    health,
    market_data,
    paper,
    real_backtests,
    real_historical,
    risk,
    strategies,
    stream,
    strategy_analyzer,
)
from app.core.config import get_settings
from app.core.logging import setup_logging
from app.core.middleware import (
    RateLimitMiddleware,
    RequestIDMiddleware,
    SecurityHeadersMiddleware,
)

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
    except Exception as exc:
        raise RuntimeError(f"database unavailable: {exc}") from exc
    # Start real-time ingestion for workspaces on real licensed providers.
    try:
        from app.services.ingestion import auto_start

        auto_start()
    except Exception:  # noqa: BLE001 - optional background feature
        pass
    yield


app = FastAPI(
    title="FX Scalper Lab API",
    version="0.1.0",
    description="AI-powered forex scalping research, backtesting, explanation, and paper trading.",
    lifespan=lifespan,
    docs_url="/docs" if not settings.is_production else None,
    redoc_url=None,
    openapi_url="/openapi.json" if not settings.is_production else None,
)

app.add_middleware(RequestIDMiddleware)
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization", "X-Request-ID"],
)
app.add_middleware(RateLimitMiddleware, redis_client=_redis_client())


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    return JSONResponse(
        status_code=500,
        content={"detail": "internal server error"},
    )


def _scrub_value(value):
    """Replace non-finite floats / exception objects with JSON-safe values.

    FastAPI's default validation error handler echoes the offending ``input``
    back into the response body; JSONResponse serializes with
    ``allow_nan=False``, so a NaN/Infinity in the body used to escalate a
    clean 422 into a 500. Scrubbing keeps the error response valid JSON and
    does not leak internal exception objects.
    """
    if isinstance(value, float):
        if math.isnan(value):
            return "NaN"
        if math.isinf(value):
            return "Infinity" if value > 0 else "-Infinity"
        return value
    if isinstance(value, Exception):
        return str(value)
    if isinstance(value, dict):
        return {k: _scrub_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_scrub_value(v) for v in value]
    return value


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    detail = [_scrub_value(e) for e in exc.errors()]
    return JSONResponse(status_code=422, content={"detail": detail})


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
app.include_router(market_data.router, prefix=api_prefix)
app.include_router(stream.router, prefix=api_prefix)
app.include_router(chart_layouts.router, prefix=api_prefix)
app.include_router(audit.router, prefix=api_prefix)
app.include_router(alerts.router, prefix=api_prefix)
app.include_router(exness.router, prefix=api_prefix)
app.include_router(real_historical.router, prefix=api_prefix)
app.include_router(strategy_analyzer.router, prefix=api_prefix)
app.include_router(real_backtests.router, prefix=api_prefix)


@app.get("/", include_in_schema=False)
def root() -> dict:
    return {"name": "FX Scalper Lab API", "docs": "/docs", "status": "online"}
