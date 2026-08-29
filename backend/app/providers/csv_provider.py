"""CSV market data provider for importing historical forex data.

Expected CSV columns (case-insensitive):
  timestamp, open, high, low, close, volume
Timestamp may be ISO-8601 or epoch seconds.
"""

from __future__ import annotations

import csv
import re
from datetime import datetime, timezone
from pathlib import Path

from app.providers.base import MarketDataProvider
from app.providers.models import (
    InstrumentMetadata,
    MarketStatus,
    ProviderHealth,
    build_candle,
    build_quote,
)

_SAFE_SYMBOL_RE = re.compile(r"^[A-Z0-9]{1,16}$")
_SAFE_TIMEFRAMES = frozenset({"M1", "M5", "M15", "M30", "H1", "H4", "D1"})


def _safe_candle_name(symbol: str, timeframe: str) -> str:
    """Basename fragment used to build candle-file paths. Rejects anything that
    could escape the data directory (slashes, dots, reserved chars)."""
    sym = symbol.upper().strip()
    tf = timeframe.upper().strip()
    if not _SAFE_SYMBOL_RE.match(sym):
        raise ValueError(f"unsafe symbol: {symbol!r}")
    if tf not in _SAFE_TIMEFRAMES:
        raise ValueError(f"unsafe timeframe: {timeframe!r}")
    return f"{sym.lower()}_{tf.lower()}.csv"


class CSVMarketDataProvider(MarketDataProvider):
    name = "csv"
    bid_ask_basis = "mid"

    def __init__(self, data_dir: str | Path | None = None) -> None:
        if data_dir is None:
            data_dir = Path(__file__).resolve().parent.parent.parent / "data"
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)

    def _candle_file(self, symbol: str, timeframe: str) -> Path:
        return self.data_dir / _safe_candle_name(symbol, timeframe)

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
        out = []
        for c in candles:
            if not (s <= c["ts"] <= e):
                continue
            out.append(
                build_candle(
                    symbol.upper(),
                    timeframe,
                    c["ts"],
                    c["open"],
                    c["high"],
                    c["low"],
                    c["close"],
                    c["volume"],
                    source=self.name,
                    is_complete=True,
                    bid_ask_basis=self.bid_ask_basis,
                )
            )
        out.sort(key=lambda c: c["ts"])
        return out

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
        symbol = symbol.upper().replace("/", "")
        meta = self.get_instrument_metadata(symbol) or InstrumentMetadata(
            canonical_symbol=symbol,
            display_symbol=f"{symbol[:3]}/{symbol[3:]}",
            provider_symbol=symbol,
            base_currency=symbol[:3],
            quote_currency=symbol[3:] or "USD",
            pip_size=0.01 if symbol.endswith("JPY") else 0.0001,
            data_provider=self.name,
        )
        last = self._last_close(symbol)
        if last is None:
            return build_quote(symbol, 0.0, 0.0, source=self.name, instrument=meta)
        half = meta.pip_size / 2
        return build_quote(
            symbol,
            round(last - half, 8),
            round(last + half, 8),
            source=self.name,
            market_status="closed" if _is_weekend() else "open",
            instrument=meta,
        )

    def _last_close(self, symbol: str) -> float | None:
        candles = self._read(self._candle_file(symbol, "M1"))
        if not candles:
            for tf in ("M5", "M15", "H1"):
                candles = self._read(self._candle_file(symbol, tf))
                if candles:
                    break
        if not candles:
            # try any timeframe present for the symbol
            sym = _safe_candle_name(symbol, "M1").split("_m1.csv")[0]
            for f in sorted(self.data_dir.glob(f"{sym}_*.csv")):
                candles = self._read(self._candle_file(symbol, f.stem.split("_")[1]))
                if candles:
                    break
        return candles[-1]["close"] if candles else None

    def get_spread(self, symbol: str, ts: float | None = None) -> float:
        return 1.0

    def get_market_status(self, symbol: str) -> MarketStatus:
        return MarketStatus(
            symbol=symbol.upper(),
            market_status="closed" if _is_weekend() else "open",
            reason="csv data has no streamed market state",
            provider_symbol=symbol.upper(),
        )

    def health_check(self) -> ProviderHealth:
        has_files = any(self.data_dir.glob("*_*.csv"))
        return ProviderHealth(
            provider=self.name,
            status="ok" if has_files else "unavailable",
            detail="data directory" if has_files else "no CSV files imported",
        )


def _is_weekend() -> bool:
    now = datetime.now(timezone.utc)
    return now.weekday() >= 5


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
