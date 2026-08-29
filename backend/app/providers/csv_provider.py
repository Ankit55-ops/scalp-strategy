"""CSV market data provider for importing historical forex data.

Expected CSV columns (case-insensitive):
  timestamp, open, high, low, close, volume
Timestamp may be ISO-8601 or epoch seconds.
"""

from __future__ import annotations

import csv
from datetime import datetime, timezone
from pathlib import Path

from app.providers.base import MarketDataProvider


class CSVMarketDataProvider(MarketDataProvider):
    name = "csv"

    def __init__(self, data_dir: str | Path | None = None) -> None:
        if data_dir is None:
            data_dir = Path(__file__).resolve().parent.parent.parent / "data"
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)

    def _candle_file(self, symbol: str, timeframe: str) -> Path:
        return self.data_dir / f"{symbol.lower()}_{timeframe.lower()}.csv"

    def import_file(
        self, symbol: str, timeframe: str, path: str | Path | None = None
    ) -> int:
        path = Path(path) if path else self._candle_file(symbol, timeframe)
        candles = self._read(path)
        self._persist(symbol, timeframe, candles)
        return len(candles)

    def _read(self, path: Path) -> list[dict]:
        candles: list[dict] = []
        with open(path, newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                candles.append(
                    {
                        "ts": _parse_ts(row.get("timestamp") or row.get("ts")),
                        "open": float(row["open"]),
                        "high": float(row["high"]),
                        "low": float(row["low"]),
                        "close": float(row["close"]),
                        "volume": float(row.get("volume", 0.0) or 0.0),
                    }
                )
        candles.sort(key=lambda c: c["ts"])
        return candles

    def _persist(self, symbol: str, timeframe: str, candles: list[dict]) -> None:
        out = self._candle_file(symbol, timeframe)
        with open(out, "w", newline="") as f:
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

    def get_historical_candles(
        self, symbol: str, timeframe: str, start: datetime, end: datetime
    ) -> list[dict]:
        path = self._candle_file(symbol, timeframe)
        if not path.exists():
            return []
        candles = self._read(path)
        s = start.replace(tzinfo=timezone.utc).timestamp() if start.tzinfo else start.timestamp()
        e = end.replace(tzinfo=timezone.utc).timestamp() if end.tzinfo else end.timestamp()
        return [c for c in candles if s <= c["ts"] <= e]

    def list_symbols(self) -> list[str]:
        files = sorted(self.data_dir.glob("*_*.csv"))
        seen: set[str] = set()
        out: list[str] = []
        for f in files:
            sym = f.stem.split("_")[0].upper()
            if sym not in seen:
                seen.add(sym)
                out.append(sym)
        return out

    def get_latest_quote(self, symbol: str) -> dict:
        return {"symbol": symbol, "bid": 0.0, "ask": 0.0, "ts": 0.0}

    def get_spread(self, symbol: str, ts: float | None = None) -> float:
        return 1.0


def _parse_ts(value: str) -> float:
    value = value.strip()
    try:
        return float(value)
    except ValueError:
        pass
    # Try ISO formats.
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S"):
        try:
            dt = datetime.strptime(value, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.timestamp()
        except ValueError:
            continue
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except ValueError:
        pass
    raise ValueError(f"cannot parse timestamp: {value}")
