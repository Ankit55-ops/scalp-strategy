"""Indicator computation using pandas/numpy. No TA-Lib dependency required."""

from __future__ import annotations

import numpy as np
import pandas as pd


def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def sma(series: pd.Series, period: int) -> pd.Series:
    return series.rolling(window=period, min_periods=period).mean()


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    out = 100 - (100 / (1 + rs))
    return out.fillna(50.0)


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high = df["high"]
    low = df["low"]
    close = df["close"]
    prev_close = close.shift(1)
    tr = pd.concat(
        [
            (high - low),
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr.rolling(window=period, min_periods=period).mean()


def add_indicators(df: pd.DataFrame, indicators: list) -> pd.DataFrame:
    """Add indicator columns requested by a strategy spec (for overlays)."""
    df = df.copy()
    for ind in indicators:
        if isinstance(ind, dict):
            name = ind.get("name", "").upper()
            params = ind.get("parameters", {})
        else:
            name = ind.name.upper()
            params = ind.parameters
        if name == "EMA":
            df[f"EMA{params.get('period', 20)}"] = ema(df["close"], int(params.get("period", 20)))
        elif name == "SMA":
            df[f"SMA{params.get('period', 20)}"] = sma(df["close"], int(params.get("period", 20)))
        elif name == "RSI":
            df[f"RSI{params.get('period', 14)}"] = rsi(df["close"], int(params.get("period", 14)))
        elif name == "ATR":
            df[f"ATR{params.get('period', 14)}"] = atr(df, int(params.get("period", 14)))
    return df
