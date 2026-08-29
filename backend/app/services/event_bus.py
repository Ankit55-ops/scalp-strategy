"""Thread-safe in-process event bus for live market-data streaming.

The ingestion service runs on background threads; FastAPI WebSocket handlers
live on the event loop. Rather than bridge threads directly, publishers append
timestamped events to a bounded per-channel history and WebSocket handlers poll
``poll()`` from an ``asyncio`` loop. This keeps the design dependency-free (no
Redis required) while remaining correct under a single uvicorn worker.

If Redis becomes available, a thin broadcaster can be layered on top later.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from typing import Any

_EVENT_CAPACITY = 4000


class EventBus:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._history: dict[str, deque[dict]] = {}

    def _channel(self, workspace_id: str) -> deque[dict]:
        with self._lock:
            ch = self._history.setdefault(workspace_id, deque(maxlen=_EVENT_CAPACITY))
        return ch

    def publish(self, workspace_id: str, event_type: str, data: dict[str, Any], ts: float | None = None) -> dict:
        event = {
            "type": event_type,
            "workspace_id": workspace_id,
            "ts": ts if ts is not None else time.time(),
            "data": data,
        }
        self._channel(workspace_id).append(event)
        return event

    def poll(self, workspace_id: str, after_ts: float) -> list[dict]:
        ch = self._channel(workspace_id)
        out: list[dict] = []
        with self._lock:
            for ev in tuple(ch):
                if ev["ts"] > after_ts:
                    out.append(ev)
        return out

    def clear(self, workspace_id: str) -> None:
        with self._lock:
            self._history.pop(workspace_id, None)


bus = EventBus()