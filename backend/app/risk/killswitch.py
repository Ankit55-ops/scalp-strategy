"""Kill-switch registry: global, per-strategy, per-pair.

Uses Redis for cross-process coordination with a JSON in-memory fallback
so it works without Redis in tests. Persisted flip-state is also recorded
to the DB audit log by the API layer.
"""

from __future__ import annotations

import json
from typing import Any

import redis

from app.core.config import get_settings


class KillSwitchRegistry:
    def __init__(self, redis_client: redis.Redis | None = None) -> None:
        settings = get_settings()
        if redis_client is None:
            try:
                redis_client = redis.Redis.from_url(
                    settings.REDIS_URL, socket_connect_timeout=1
                )
                redis_client.ping()
            except Exception:  # Redis unavailable -> in-memory fallback
                redis_client = None
        self._redis = redis_client
        self._mem: dict[str, bool] = {}

    def _set(self, key: str, value: bool) -> None:
        if self._redis is not None:
            try:
                self._redis.set(key, "1" if value else "0")
                return
            except Exception:
                pass
        self._mem[key] = value

    def _get(self, key: str) -> bool:
        if self._redis is not None:
            try:
                v = self._redis.get(key)
                return v == b"1" or v == "1"
            except Exception:
                pass
        return self._mem.get(key, False)

    def reset(self) -> None:
        """Clear all kill-switch state (Redis-prefixed keys or memory)."""
        if self._redis is not None:
            try:
                for key in self._redis.scan_iter("ks:*"):
                    self._redis.delete(key)
                return
            except Exception:
                pass
        self._mem.clear()

    # -- scopes ------------------------------------------------------------
    def set_global(self, enabled: bool) -> None:
        self._set("ks:global", enabled)

    def set_strategy(self, strategy_id: str, enabled: bool) -> None:
        self._set(f"ks:strategy:{strategy_id}", enabled)

    def set_pair(self, symbol: str, enabled: bool) -> None:
        self._set(f"ks:pair:{symbol}", enabled)

    # -- checks ------------------------------------------------------------
    def is_halted(self, symbol: str, strategy_id: str | None = None) -> bool:
        if self._get("ks:global"):
            return True
        if strategy_id and self._get(f"ks:strategy:{strategy_id}"):
            return True
        if symbol and self._get(f"ks:pair:{symbol}"):
            return True
        return False

    def status(self) -> dict[str, bool]:
        return {
            "global": self._get("ks:global"),
            "strategy": {},
            "pair": {},
        }

    def is_global_halted(self) -> bool:
        return self._get("ks:global")
