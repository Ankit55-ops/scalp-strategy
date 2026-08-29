"""Synthetic candle generation for the mock data provider (development only)."""

from __future__ import annotations

import math
import random
from datetime import datetime, timezone

from app.services.market_math import pip_size

TIMEFRAME_SECONDS = {
    "M1": 60,
    "M5": 300,
    "M15": 900,
    "H1": 3600,
}


def _next_ts(ts: float, tf: str) -> float:
    return ts + TIMEFRAME_SECONDS[tf]


def gen_candles(
    symbol: str,
    timeframe: str,
    start: datetime,
    n_candles: int,
    seed: int | None = None,
    start_price: float | None = None,
) -> list[dict]:
    """Generate n OHLC candles using Brownian motion around a mean level.

    This is purely synthetic and NOT market data. Used for local development
    and testing the full pipeline without external data.
    """
    rng = random.Random(seed)
    tf_sec = TIMEFRAME_SECONDS[timeframe]
    base = 1.0
    jpy = symbol.upper().endswith("JPY")
    if jpy:
        base = 150.0
        drift = 0.0
        vol = 0.06
    elif symbol.upper() in ("EURUSD", "GBPUSD", "AUDUSD", "NZDUSD"):
        base = 1.10
        drift = 0.0
        vol = 0.0045
    else:
        base = 1.20
        drift = 0.0
        vol = 0.005

    price = start_price or base
    ts = int(start.replace(tzinfo=timezone.utc).timestamp())
    ts = ts - (ts % tf_sec)
    candles: list[dict] = []
    for _ in range(n_candles):
        ret = rng.gauss(drift, vol) * math.sqrt(tf_sec / 300.0)
        open_p = price
        close_p = open_p * (1 + ret)
        hi = max(open_p, close_p) * (1 + abs(rng.gauss(0, vol * 0.3)))
        lo = min(open_p, close_p) * (1 - abs(rng.gauss(0, vol * 0.3)))
        ps = pip_size("JPY" if jpy else "USD")
        candles.append(
            {
                "ts": ts,
                "open": round(open_p, 5),
                "high": round(hi, 5),
                "low": round(lo, 5),
                "close": round(close_p, 5),
                "volume": round(abs(rng.gauss(100, 20)), 1),
            }
        )
        price = close_p
        ts += tf_sec
    return candles
