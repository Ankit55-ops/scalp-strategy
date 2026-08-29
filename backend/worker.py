"""RQ worker entrypoint: processes queued FX Scalper Lab background jobs.

Usage:
    rq worker fxscalper --url redis://localhost:6379/0 --path .
"""

from __future__ import annotations

from app.core.config import get_settings


def get_queue():
    from rq import Queue

    import redis

    return Queue(
        "fxscalper",
        connection=redis.Redis.from_url(get_settings().REDIS_URL),
    )


if __name__ == "__main__":
    import os
    import sys

    os.execvp(
        sys.executable,
        [sys.executable, "-m", "rq", "worker", "fxscalper", "--url", get_settings().REDIS_URL, "--path", "."],
    )