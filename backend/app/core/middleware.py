"""Request middleware: request ID, rate limiting (in-process token bucket), log context."""

from __future__ import annotations

import logging
import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

logger = logging.getLogger("fxscalper.http")

try:
    import redis as _redis

    _redis_imported = True
except Exception:  # pragma: no cover
    _redis_imported = False


class RequestIDMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Sliding-window rate limit keyed by client IP (in-memory fallback)."""

    def __init__(self, app, limit: int = 120, window_seconds: int = 60, redis_client=None):
        super().__init__(app)
        self.limit = limit
        self.window = window_seconds
        self._redis = redis_client
        self._buckets: dict[str, list[float]] = {}

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
            except Exception:
                pass
        window = self._buckets.setdefault(key, [])
        window[:] = [t for t in window if t > now - self.window]
        if len(window) >= self.limit:
            return False
        window.append(now)
        return True

    async def dispatch(self, request: Request, call_next):
        if request.url.path.startswith(("/api/auth", "/docs", "/openapi.json", "/health", "/ready")):
            return await call_next(request)
        client = request.client.host if request.client else "unknown"
        if not self._bump(f"rl:{client}"):
            return JSONResponse(
                status_code=429,
                content={"detail": "rate limit exceeded"},
                headers={"Retry-After": str(self.window)},
            )
        return await call_next(request)
