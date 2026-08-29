"""Request middleware: request ID, rate limiting (token bucket), security headers."""

from __future__ import annotations

import logging
import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from app.core.config import get_settings

logger = logging.getLogger("fxscalper.http")

try:
    import redis as _redis

    _redis_imported = True
except Exception:  # pragma: no cover
    _redis_imported = False


def client_ip(request: Request) -> str:
    """Best-effort client IP. X-Forwarded-For is only honored when enabled."""
    settings = get_settings()
    if settings.TRUST_PROXY_HEADERS:
        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded:
            return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


class RequestIDMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response


class RateLimitMiddleware(BaseHTTPMiddleware):
    """IP-keyed sliding-window rate limit (Redis-backed, in-memory fallback).

    Ops like /docs and /health stay exempt; auth endpoints are intentionally
    included so registration and login cannot be hammered from one source IP.
    """

    def __init__(self, app, redis_client=None):
        super().__init__(app)
        settings = get_settings()
        self.limit = settings.RATE_LIMIT_DEFAULT
        self.window = settings.RATE_LIMIT_WINDOW_SECONDS
        self._redis = redis_client
        self._buckets: dict[str, list[float]] = {}
        self._compact_at = 0.0
        self._max_keys = 10_000

    def _bump(self, key: str) -> bool:
        now = time.time()
        if self._redis is not None:
            try:
                pipe = self._redis.pipeline()
                pipe.zremrangebyscore(key, 0, now - self.window)
                pipe.zadd(key, {str(uuid.uuid4()): now})
                pipe.zcard(key)
                pipe.expire(key, self.window)
                count = pipe.execute()[-1]
                return count <= self.limit
            except Exception:  # noqa: BLE001
                pass
        window = self._buckets.setdefault(key, [])
        window[:] = [t for t in window if t > now - self.window]
        if len(window) >= self.limit:
            return False
        window.append(now)
        # Bounded in-memory map: drop idle buckets periodically.
        if len(self._buckets) > self._max_keys and now > self._compact_at:
            self._buckets = {
                k: [t for t in v if t > now - self.window]
                for k, v in self._buckets.items()
            }
            self._compact_at = now + self.window
        return True

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if path.startswith(("/docs", "/openapi.json", "/health", "/ready")):
            return await call_next(request)
        client = client_ip(request)
        if not self._bump(f"rl:{client}"):
            return JSONResponse(
                status_code=429,
                content={"detail": "rate limit exceeded"},
                headers={"Retry-After": str(self.window)},
            )
        return await call_next(request)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Defense-in-depth HTTP response headers (hardened for the JSON API)."""

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault(
            "Permissions-Policy", "camera=(), microphone=(), geolocation=()"
        )
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'none'; frame-ancestors 'none'; base-uri 'none'",
        )
        if get_settings().ENABLE_HSTS and request.url.scheme == "https":
            response.headers.setdefault(
                "Strict-Transport-Security",
                "max-age=31536000; includeSubDomains",
            )
        return response