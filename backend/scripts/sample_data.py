"""Generate synthetic sample CSV candle files into backend/data/.

Usage:
    PYTHONPATH=. .venv/bin/python -m scripts.sample_data

Generates deterministic random-walk candles (same seed each run) for the
core symbols so the CSVMarketDataProvider has something realistic to read.
This is synthetic data for development/tests only - NOT market data.
"""

from __future__ import annotations

import csv
from datetime import datetime, timezone
from pathlib import Path

from app.providers.mock_data import gen_candles

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
SYMBOLS = ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD"]
TIMEFRAME = "M5"
CANDLES_PER_SYMBOL = 20000
START = datetime(2025, 1, 1, tzinfo=timezone.utc)


def generate() -> dict[str, int]:
    counted: dict[str, int] = {}
    for symbol in SYMBOLS:
        candles = gen_candles(
            symbol,
            TIMEFRAME,
            START,
            CANDLES_PER_SYMBOL,
            seed=hash(f"{symbol}-{TIMEFRAME}-sample") % (2**31),
        )
        path = DATA_DIR / f"{symbol.lower()}_{TIMEFRAME.lower()}.csv"
        with open(path, "w", newline="") as f:
            writer = csv.DictWriter(
                f, fieldnames=["timestamp", "open", "high", "low", "close", "volume"]
            )
            writer.writeheader()
            for c in candles:
                writer.writerow(
                    {
                        "timestamp": datetime.fromtimestamp(c["ts"], tz=timezone.utc).isoformat(),
                        "open": c["open"],
                        "high": c["high"],
                        "low": c["low"],
                        "close": c["close"],
                        "volume": c["volume"],
                    }
                )
        counted[symbol] = len(candles)
    return counted


if __name__ == "__main__":
    result = generate()
    for symbol, n in result.items():
        print(f"{symbol:8s} {n} candles -> {DATA_DIR}/{symbol.lower()}_{TIMEFRAME.lower()}.csv")
    print(f"done: {sum(result.values())} total candles")