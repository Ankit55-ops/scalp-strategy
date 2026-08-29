import csv
from datetime import datetime, timezone

from app.providers.csv_provider import CSVMarketDataProvider

SOURCE_ROWS = [
    {"timestamp": "2023-11-14 22:13:00", "open": "1.1", "high": "1.11", "low": "1.09", "close": "1.105", "volume": "10"},
    {"timestamp": "2023-11-14 22:18:00", "open": "1.105", "high": "1.12", "low": "1.10", "close": "1.115", "volume": "12"},
]


def _write_source(dir_path, name="source.csv"):
    p = dir_path / name
    with open(p, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["timestamp", "open", "high", "low", "close", "volume"])
        writer.writeheader()
        writer.writerows(SOURCE_ROWS)
    return p


def test_csv_provider_roundtrip(tmp_path):
    provider = CSVMarketDataProvider(data_dir=tmp_path)
    src = _write_source(tmp_path)
    count = provider.import_file("EURUSD", "M5", src)
    assert count == 2
    got = provider._read(tmp_path / "eurusd_m5.csv")
    assert len(got) == 2
    assert got[0]["close"] == 1.105


def test_csv_provider_get_historical_filters_by_window(tmp_path):
    provider = CSVMarketDataProvider(data_dir=tmp_path)
    provider.import_file("EURUSD", "M5", _write_source(tmp_path))
    start = datetime(2023, 11, 14, 22, 12, tzinfo=timezone.utc)
    end = datetime(2023, 11, 14, 22, 20, tzinfo=timezone.utc)
    got = provider.get_historical_candles("EURUSD", "M5", start, end)
    assert len(got) == 2


def test_csv_provider_list_symbols(tmp_path):
    provider = CSVMarketDataProvider(data_dir=tmp_path)
    provider.import_file("GBPUSD", "M5", _write_source(tmp_path, "gbp.csv"))
    provider.import_file("USDJPY", "M5", _write_source(tmp_path, "jpy.csv"))
    assert sorted(provider.list_symbols()) == ["GBPUSD", "USDJPY"]