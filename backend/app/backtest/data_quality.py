"""Data Quality Gate — validation of real historical market data before a run.

Every real-historical validation run must pass this gate before the strategy is
scored. It checks ordering, duplication, OHLC sanity, gaps (treating weekend /
expected market closures as non-errors), warm-up sufficiency, and records
spread / bid-ask availability so the cost model can be labelled honestly.

Quality result is one of:
  PASS  — clean enough to evaluate directly.
  PASS_WITH_WARNINGS — usable but with flagged gaps or estimated costs.
  FAIL  — the data cannot support a trustworthy strategy score (final metrics
          are NOT produced for a FAIL).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone

from app.providers.models import _TF_SECONDS, _tf_seconds

VALID_TIMEFRAMES = frozenset(_TF_SECONDS.keys())


@dataclass
class Gap:
    start_ts: float
    end_ts: float
    missing_seconds: int
    is_weekend_closure: bool


@dataclass
class DataQualityReport:
    provider_name: str
    provider_symbol: str
    canonical_symbol: str
    timeout: str
    requested_start: float
    requested_end: float
    expected_candles: int = 0
    received_candles: int = 0
    missing_candles: int = 0
    duplicate_candles_removed: int = 0
    candle_gaps: list = field(default_factory=list)
    gap_count: int = 0
    warmup_candles_used: int = 0
    quality_status: str = "FAIL"  # PASS | PASS_WITH_WARNINGS | FAIL
    issues: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    data_type: str = "historical_candles"  # historical_candles | bid_ask | midpoint | estimated_spread
    source_data_hash: str | None = None
    actual_start: float | None = None
    actual_end: float | None = None
    incomplete_candles_excluded: int = 0
    malformed_ohlc: int = 0
    monotonic_violations: int = 0
    spread_availability: str = "not_checked"
    bid_ask_availability: str = "not_checked"
    cost_model_confidence: str = "high"


def _parse_iso(value: str | float) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def normalize_candles(candles: list[dict]) -> tuple[list[dict], int]:
    """Drop duplicates by ts and filter incomplete candles; return (clean, info)."""
    cleaned: list[dict] = []
    seen: set = set()
    incomplete = 0
    for c in candles:
        ts = float(c["ts"])
        is_complete = c.get("is_complete", True)
        if not is_complete:
            incomplete += 1
            continue
        if ts in seen:
            continue
        seen.add(ts)
        cleaned.append(c)
    cleaned.sort(key=lambda c: float(c["ts"]))
    return cleaned, incomplete


def _is_weekend_like(from_ts: float, to_ts: float) -> bool:
    """Approximate check whether a gap spans the FX weekend closure (Fri->Mon)."""
    span_hours = (to_ts - from_ts) / 3600.0
    start_utc = datetime.fromtimestamp(from_ts, tz=timezone.utc)
    end_utc = datetime.fromtimestamp(to_ts, tz=timezone.utc)
    wraps_weekend = start_utc.weekday() >= 4 and end_utc.weekday() <= 0
    friday_late = start_utc.weekday() == 4 and start_utc.hour >= 20
    return span_hours >= 36 and (wraps_weekend or friday_late)


def run_data_quality_gate(
    candles: list[dict],
    *,
    provider_name: str,
    provider_symbol: str,
    canonical_symbol: str,
    timeout: str,
    requested_start: float | str,
    requested_end: float | str,
    warmup_needed: int = 0,
) -> DataQualityReport:
    tf = timeout.upper()
    step = _tf_seconds(tf)
    req_start = _parse_iso(requested_start)
    req_end = _parse_iso(requested_end)

    report = DataQualityReport(
        provider_name=provider_name,
        provider_symbol=provider_symbol,
        canonical_symbol=canonical_symbol,
        timeout=tf,
        requested_start=req_start,
        requested_end=req_end,
    )

    cleaned, incomplete = normalize_candles(candles)
    report.incomplete_candles_excluded = incomplete

    if not cleaned:
        report.issues.append("no complete candles returned")
        report.quality_status = "FAIL"
        return report

    expected = int((req_end - req_start) / step) + 1
    report.expected_candles = expected
    report.received_candles = len(cleaned)
    report.actual_start = float(cleaned[0]["ts"])
    report.actual_end = float(cleaned[-1]["ts"])

    prev_ts = float(cleaned[0]["ts"])
    gaps: list[dict] = []
    for c in cleaned[1:]:
        ts = float(c["ts"])
        if ts <= prev_ts:
            report.monotonic_violations += 1
        if ts - prev_ts > step:
            g = Gap(prev_ts, ts, int(ts - prev_ts - step), _is_weekend_like(prev_ts, ts))
            gaps.append(g)
        prev_ts = ts

    report.gap_count = len(gaps)
    report.candle_gaps = [
        {"start_ts": g.start_ts, "end_ts": g.end_ts,
         "missing_seconds": g.missing_seconds, "is_weekend_closure": g.is_weekend_closure}
        for g in gaps
    ]

    for c in cleaned:
        o, h, l, cl = (float(c[k]) for k in ("open", "high", "low", "close"))
        if not (l <= o <= h and l <= cl <= h and h >= l):
            report.malformed_ohlc += 1
        if not (o > 0 and h > 0 and l > 0 and cl > 0):
            report.malformed_ohlc += 1

    has_bid = any(c.get("bid") is not None for c in cleaned)
    has_ask = any(c.get("ask") is not None for c in cleaned)
    report.spread_availability = "available" if (has_bid and has_ask) else "estimated"
    report.bid_ask_availability = "available" if (has_bid and has_ask) else "unavailable"
    if has_bid and has_ask:
        report.data_type = "bid_ask"
    else:
        report.data_type = "midpoint"

    report.missing_candles = max(0, expected - len(cleaned))
    report.warmup_candles_used = warmup_needed

    source_hash = hashlib.sha256(
        json.dumps([cleaned[i]["ts"] for i in range(0, len(cleaned), 5)],
                   sort_keys=True).encode()
    ).hexdigest()
    report.source_data_hash = source_hash

    if report.malformed_ohlc > 0:
        report.quality_status = "FAIL"
        report.issues.append(f"{report.malformed_ohlc} malformed OHLC candles")
    elif len(cleaned) < warmup_needed + 30:
        report.quality_status = "FAIL"
        report.issues.append("insufficient candles after warm-up for a stable evaluation")
    elif report.missing_candles > 0 or report.gap_count > 0:
        report.quality_status = "PASS_WITH_WARNINGS"
        report.warnings.append(
            f"{report.missing_candles} expected candle(s) missing / "
            f"{report.gap_count} gap(s); strategy may see incomplete history"
        )
        report.cost_model_confidence = "medium" if not (has_bid and has_ask) else "high"
    else:
        report.quality_status = "PASS"

    if report.data_type == "midpoint":
        report.warnings.append(
            "Only midpoint candles are available; execution prices are ESTIMATED "
            "using the configured spread model — labelled, never presented as "
            "historical bid/ask data."
        )
        report.cost_model_confidence = "low"

    return report