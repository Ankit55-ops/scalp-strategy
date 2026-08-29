"""Live strategy-checker signal engine.

Evaluates strategy entry/exit rules as real candles close and produces
provisional intrabar previews. Reuses the same safe DSL evaluator and the
backtester's indicator stack so live signals are consistent with backtest
results (no look-ahead: confirmed signals evaluate only on completed candles).

Outputs are persisted as ``StrategySignalEvent`` audit rows (state / signal
machines below) and emitted over the in-process event bus so the terminal and
paper service can react.

Signal labels (per spec):
  - CONFIRMED_CANDLE_CLOSE  rules fired on a completed candle
  - INTRABAR_PREVIEW        provisional match on the forming candle
  - BLOCKED_DATA            stale/absent feed blocked evaluation
  - NO_SIGNAL               evaluated cleanly, nothing fired
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import pandas as pd

from app.backtest.indicators import add_indicators
from app.dsl import ExpressionError, evaluate_expression, validate_expression
from app.db.session import SessionLocal
from app.models import Strategy, StrategySignalEvent
from app.schemas.strategy import StrategySpec
from app.services.event_bus import bus
from app.services import feed_health
from app.services.provider_service import get_active_provider, get_candles_from_db

logger = logging.getLogger("fxscalper.signals")

WINDOW = 400  # indicator warm-up window, mirrors the backtester


# -- context --------------------------------------------------------------
def _context_at(candles: list[dict], spread_pips: float, spec: StrategySpec, i: int) -> dict:
    lo = max(0, i - WINDOW + 1)
    ctx: dict = {}
    for k in ("open", "high", "low", "close"):
        ctx[k] = [float(c[k]) for c in candles[lo : i + 1]]
    ts = float(candles[i]["ts"])
    ctx["time_minute"] = (int(ts) % 86400) // 60
    ctx["spread_pips"] = spread_pips
    from app.backtest.sessions import in_session, is_blackout

    ctx["in_session"] = in_session(ts, spec.sessions_utc)
    ctx["is_blackout"] = False
    ctx["__prev"] = i > 0
    return ctx


def _rule_matches(expr: str, ctx: dict) -> bool:
    try:
        return bool(evaluate_expression(expr, ctx))
    except ExpressionError:
        return False


def _entry_side(rule_id: str) -> str:
    rid = rule_id.lower()
    if rid.startswith("long") or rid.startswith("buy"):
        return "long"
    return "short"


# -- evaluation -----------------------------------------------------------
def evaluate_candle_close(
    db,
    workspace_id: str,
    strategy: Strategy,
    spec: StrategySpec,
    symbol: str,
    timeframe: str,
    candles: list[dict],
    quote: dict | None,
) -> dict:
    """Evaluate rules on the last *completed* candle. Returns an event dict."""
    if len(candles) < 3:
        return _result("NO_SIGNAL", "monitoring", "insufficient candle history", None, 0.0, 0.0)
    df = pd.DataFrame(candles)
    df = add_indicators(df, spec.indicators)
    candles = df.to_dict("records")
    i = len(candles) - 1
    spread = quote.get("spread_pips") if quote else 0.0
    ctx = _context_at(candles, spread, spec, i)
    long_hits, short_hits = [], []
    for rule in spec.entry_rules:
        if _rule_matches(rule.expression, ctx):
            long_hits.append(rule.id) if _entry_side(rule.id) == "long" else short_hits.append(rule.id)
    side = None
    if long_hits and not short_hits:
        side = "long"
    elif short_hits and not long_hits:
        side = "short"
    if side:
        return _result(
            "CONFIRMED_CANDLE_CLOSE",
            "signal_found",
            None,
            side,
            long_hits if side == "long" else short_hits,
            float(candles[i]["close"]),
            spread,
            ts=float(candles[i]["ts"]),
            rule_ids=long_hits if side == "long" else short_hits,
        )
    return _result("NO_SIGNAL", "ready", None, None, [], float(candles[i]["close"]), spread, ts=float(candles[i]["ts"]))


def intrabar_preview(
    db,
    workspace_id: str,
    strategy: Strategy,
    spec: StrategySpec,
    symbol: str,
    timeframe: str,
    candles: list[dict],
    quote: dict | None,
) -> dict:
    """Provisional evaluation on the forming candle (filled with the live mid)."""
    if not candles:
        return _result("NO_SIGNAL", "monitoring", "no candle data", None, 0.0, 0.0)
    series = list(candles)
    forming = dict(series[-1])
    if quote:
        mid = quote.get("mid")
        if mid:
            forming["close"] = float(mid)
            forming["high"] = max(float(forming.get("high") or mid), float(mid))
            forming["low"] = min(float(forming.get("low") or mid), float(mid))
    series[-1] = forming
    df = pd.DataFrame(series)
    df = add_indicators(df, spec.indicators)
    candles = df.to_dict("records")
    i = len(candles) - 1
    spread = quote.get("spread_pips") if quote else 0.0
    ctx = _context_at(candles, spread, spec, i)
    long_hits, short_hits = [], []
    for rule in spec.entry_rules:
        if _rule_matches(rule.expression, ctx):
            long_hits.append(rule.id) if _entry_side(rule.id) == "long" else short_hits.append(rule.id)
    side = None
    if long_hits and not short_hits:
        side = "long"
    elif short_hits and not long_hits:
        side = "short"
    if side:
        return _result(
            "INTRABAR_PREVIEW",
            "signal_found",
            "provisional — confirm on candle close",
            side,
            long_hits if side == "long" else short_hits,
            float(candles[i]["close"]),
            spread,
            ts=float(candles[i]["ts"]),
            is_intrabar=True,
        )
    return _result("NO_SIGNAL", "ready", None, None, [], float(candles[i]["close"]), spread, is_intrabar=True)


def _result(label, state, detail, side, rule_ids, price, spread, ts=None, is_intrabar=False) -> dict:
    return {
        "signal-label": label if label != "NO_SIGNAL" else "NO_SIGNAL",
        "state": "blocked" if label == "BLOCKED_DATA" else state,
        "side": side,
        "rule_ids": rule_ids or [],
        "price": price,
        "spread_pips": spread,
        "ts": ts,
        "is_intrabar": is_intrabar,
        "blocked_reason": detail,
        "detail": detail,
    }


# -- orchestration (called from the ingestion thread on candle close) -----
def trigger_candle_close(workspace_id: str, symbol: str, timeframe: str) -> None:
    db = SessionLocal()
    try:
        provider = get_active_provider(db, workspace_id)
        strategies = (
            db.query(Strategy)
            .filter(Strategy.workspace_id == workspace_id, Strategy.status == "active")
            .all()
        )
        for strategy in strategies:
            try:
                spec = StrategySpec.model_validate(strategy.spec)
            except Exception:  # noqa: BLE001
                continue
            pair = spec.supported_pairs[0]
            if pair.upper() != symbol.upper() or timeframe not in spec.supported_timeframes:
                continue
            try:
                quote = feed_health.get_quote(db, workspace_id, pair)
            except Exception:  # noqa: BLE001
                quote = None
            start = datetime.now(timezone.utc).timestamp() - _lookback_seconds(timeframe)
            candles = get_candles_from_db(db, symbol.upper(), timeframe, start, datetime.now(timezone.utc).timestamp())
            if not candles:
                continue
            if quote and (quote.get("is_stale") or quote.get("feed_state") in ("STALE", "DISCONNECTED", "CONNECTING")):
                _persist(db, workspace_id, strategy, spec, symbol, timeframe, _result("BLOCKED_DATA", "blocked", f"feed {quote.get('feed_state')}", None, 0.0, 0.0))
                bus.publish(workspace_id, "signal", _result("BLOCKED_DATA", "blocked", f"feed {quote.get('feed_state')}", None, 0.0, 0.0, ), )
                continue
            event = evaluate_candle_close(db, workspace_id, strategy, spec, symbol, timeframe, candles, quote)
            _persist(db, workspace_id, strategy, spec, symbol, timeframe, event)
            if event["side"]:
                bus.publish(
                    workspace_id,
                    "signal",
                    {
                        "strategy_id": strategy.id,
                        "strategy_name": strategy.name,
                        "symbol": symbol.upper(),
                        "timeframe": timeframe,
                        "side": event["side"],
                        "signal_label": event["signal-label"],
                        "rule_ids": event["rule_ids"],
                        "price": event["price"],
                        "ts": event["ts"],
                        "is_intrabar": False,
                    },
                )
    finally:
        db.close()


def _lookback_seconds(timeframe: str) -> float:
    return {"M1": 43200, "M5": 108000, "M15": 172800, "M30": 259200, "H1": 604800, "H4": 1209600}.get(timeframe.upper(), 172800)


def _persist(db, workspace_id: str, strategy: Strategy, spec: StrategySpec, symbol: str, timeframe: str, event: dict) -> None:
    label = event.get("signal-label") or "NO_SIGNAL"
    state = event.get("state") or "monitoring"
    prev = (
        db.query(StrategySignalEvent)
        .filter(
            StrategySignalEvent.strategy_id == strategy.id,
            StrategySignalEvent.symbol == symbol.upper(),
            StrategySignalEvent.timeframe == timeframe,
        )
        .order_by(StrategySignalEvent.created_at.desc())
        .first()
    )
    if prev is not None and prev.signal_label == label and prev.state == state:
        return  # audit is transition-based to avoid unbounded growth
    db.add(
        StrategySignalEvent(
            workspace_id=workspace_id,
            strategy_id=strategy.id,
            strategy_version=strategy.current_version or spec.version,
            symbol=symbol.upper(),
            timeframe=timeframe,
            signal=event["side"] or "none",
            signal_label=label,
            state=state,
            blocked_reason=event.get("blocked_reason"),
            detail={} if event.get("is_intrabar") else {"rule_ids": event.get("rule_ids", [])},
            price=event.get("price", 0.0),
            spread_pips=event.get("spread_pips", 0.0),
        )
    )
    db.commit()