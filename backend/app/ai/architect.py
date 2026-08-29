"""Strategy Architect: generates N explainable candidate strategy specs.

In `mock` mode it builds candidates from validated templates (fully offline).
In `llm` mode it prompts an OpenAI-compatible endpoint and validates the JSON
through the Pydantic StrategySpec schema, discarding any invalid output.
"""

from __future__ import annotations

import json

from app.ai.llm import LLMClient, build_system_prompt
from app.core.config import get_settings
from app.schemas.api_strategy import StrategyGenerateRequest
from app.schemas.strategy import StrategySpec

SESSION_UTC = {
    "Asian": {"name": "Asian", "start": "00:00", "end": "07:00"},
    "London": {"name": "London", "start": "07:00", "end": "12:00"},
    "New York": {"name": "New York", "start": "12:00", "end": "17:00"},
    "London-New York": {"name": "London-NY Overlap", "start": "12:00", "end": "16:00"},
}


def _cand(req: StrategyGenerateRequest, idx: int) -> StrategySpec:
    family = req.strategy_family.value
    tf = req.timeframe
    session = SESSION_UTC.get(req.session_name, SESSION_UTC["London"])
    pair = req.pairs[0].upper()
    atr_period = 14
    ema_fast = 10 if tf == "M1" else (20 if tf == "M5" else (34 if tf == "M15" else 50))

    common = {
        "supported_pairs": [p.upper() for p in req.pairs],
        "supported_timeframes": [tf],
        "sessions_utc": [session],
        "version": "1.0.0",
        "confidence_notes": (
            "This is a hypothesis requiring backtesting and paper-trading validation."
        ),
    }

    specs: list[StrategySpec] = []

    # Candidate 1: trend pullback (EMA pullback to fast EMA)
    specs.append(
        StrategySpec(
            name=f"{pair} {tf} Trend Pullback",
            strategy_family="trend_pullback",
            indicators=[
                {"name": "EMA", "parameters": {"period": ema_fast}},
                {"name": "EMA", "parameters": {"period": ema_fast * 2}},
                {"name": "ATR", "parameters": {"period": atr_period}},
            ],
            entry_rules=[
                {
                    "id": "long_rule_1",
                    "description": "Price pulls back to fast EMA while trend EMA is up",
                    "expression": (
                        f"ema(close,{ema_fast*2}) > ema(close,{ema_fast}) and "
                        f"low <= ema(close,{ema_fast}) and close > ema(close,{ema_fast})"
                    ),
                },
                {
                    "id": "short_rule_1",
                    "description": "Price pulls back to fast EMA while trend EMA is down",
                    "expression": (
                        f"ema(close,{ema_fast*2}) < ema(close,{ema_fast}) and "
                        f"high >= ema(close,{ema_fast}) and close < ema(close,{ema_fast})"
                    ),
                },
            ],
            exit_rules=[
                {
                    "id": "exit_rule_1",
                    "description": "Exit long when price closes back below fast EMA",
                    "expression": f"close < ema(close,{ema_fast})",
                },
            ],
            risk_management={
                "risk_per_trade_pct": req.risk_per_trade_pct or 0.25,
                "max_daily_loss_pct": 1.0,
                "max_consecutive_losses": 3,
                "max_open_positions": 1,
                "max_trades_per_day": req.max_trades_per_day or 5,
                "stop_loss_method": "ATR",
                "stop_loss_parameters": {"atr_period": atr_period, "atr_multiplier": 1.2},
                "take_profit_method": "risk_reward",
                "take_profit_parameters": {"risk_reward_ratio": 1.5},
            },
            execution_filters={
                "max_spread_pips": req.max_spread_pips or 1.2,
                "max_slippage_pips": 0.5,
                "minimum_atr_pips": 3.0,
                "news_blackout_minutes_before": 15,
                "news_blackout_minutes_after": 15,
            },
            market_regime={
                "preferred": ["trending", "high_liquidity"],
                "avoid": ["ranging", "high_spread", "major_news_window"],
            },
            assumptions=[
                "EMA values computed on close of completed candles only",
                f"Trading limited to {session['name']} UTC session",
            ],
            failure_modes=[
                "Fails in ranging/choppy markets",
                "Fails during high-impact news and wide spreads",
            ],
            plain_english_explanation=(
                f"This strategy buys pullbacks to the fast {ema_fast}-EMA when the slower "
                f"{(ema_fast*2)}-EMA is rising, and sells pullbacks when it is falling. "
                "Stops are set using ATR and targets use a 1.5 risk-reward ratio. It is "
                "intended for trending sessions only and is expected to be unprofitable in "
                "range-bound conditions."
            ),
            **common,
        )
    )

    # Candidate 2: breakout
    specs.append(
        StrategySpec(
            name=f"{pair} {tf} Range Breakout",
            strategy_family="breakout",
            indicators=[
                {"name": "ATR", "parameters": {"period": atr_period}},
            ],
            entry_rules=[
                {
                    "id": "long_rule_1",
                    "description": "Close breaks above prior 20-candle high",
                    "expression": "close > highest(high,20)",
                },
                {
                    "id": "short_rule_1",
                    "description": "Close breaks below prior 20-candle low",
                    "expression": "close < lowest(low,20)",
                },
            ],
            exit_rules=[
                {
                    "id": "exit_rule_1",
                    "description": "Exit when price retraces below the short 5-bar SMA",
                    "expression": "close < sma(close,5)",
                },
            ],
            risk_management={
                "risk_per_trade_pct": req.risk_per_trade_pct or 0.25,
                "max_daily_loss_pct": 1.0,
                "max_consecutive_losses": 3,
                "max_open_positions": 1,
                "max_trades_per_day": req.max_trades_per_day or 5,
                "stop_loss_method": "ATR",
                "stop_loss_parameters": {"atr_period": atr_period, "atr_multiplier": 1.5},
                "take_profit_method": "risk_reward",
                "take_profit_parameters": {"risk_reward_ratio": 2.0},
            },
            execution_filters={
                "max_spread_pips": req.max_spread_pips or 1.2,
                "max_slippage_pips": 0.5,
                "minimum_atr_pips": 3.0,
                "news_blackout_minutes_before": 15,
                "news_blackout_minutes_after": 15,
            },
            market_regime={
                "preferred": ["volatile", "high_liquidity"],
                "avoid": ["ranging", "low_liquidity"],
            },
            assumptions=[
                "Breakout confirmed on close to avoid false intra-candle breaks",
                f"Trading limited to {session['name']} UTC session",
            ],
            failure_modes=[
                "Fake breakouts in low-liquidity overlap windows",
                "Whipsaw during high-spread news",
            ],
            plain_english_explanation=(
                f"This strategy enters on a confirmed close beyond the prior 20-candle "
                f"range, using ATR-based stops and a 2.0 risk-reward target. It works best "
                "in volatile, liquid sessions and is vulnerable to fake breakouts when "
                "liquidity is thin."
            ),
            **common,
        )
    )

    # Candidate 3: mean reversion
    specs.append(
        StrategySpec(
            name=f"{pair} {tf} Mean Reversion",
            strategy_family="mean_reversion",
            indicators=[
                {"name": "RSI", "parameters": {"period": 14}},
                {"name": "SMA", "parameters": {"period": 50}},
            ],
            entry_rules=[
                {
                    "id": "long_rule_1",
                    "description": "RSI oversold and price above long SMA (buy dip)",
                    "expression": "rsi(close,14) < 30 and close > sma(close,50)",
                },
                {
                    "id": "short_rule_1",
                    "description": "RSI overbought and price below long SMA (sell rally)",
                    "expression": "rsi(close,14) > 70 and close < sma(close,50)",
                },
            ],
            exit_rules=[
                {
                    "id": "exit_rule_1",
                    "description": "Exit when RSI returns to neutral 50",
                    "expression": "rsi(close,14) >= 50 and rsi(close,14) <= 60",
                },
            ],
            risk_management={
                "risk_per_trade_pct": req.risk_per_trade_pct or 0.25,
                "max_daily_loss_pct": 1.0,
                "max_consecutive_losses": 3,
                "max_open_positions": 1,
                "max_trades_per_day": req.max_trades_per_day or 5,
                "stop_loss_method": "ATR",
                "stop_loss_parameters": {"atr_period": atr_period, "atr_multiplier": 1.0},
                "take_profit_method": "risk_reward",
                "take_profit_parameters": {"risk_reward_ratio": 1.5},
            },
            execution_filters={
                "max_spread_pips": req.max_spread_pips or 1.2,
                "max_slippage_pips": 0.5,
                "minimum_atr_pips": 3.0,
                "news_blackout_minutes_before": 15,
                "news_blackout_minutes_after": 15,
            },
            market_regime={
                "preferred": ["ranging", "consolidation"],
                "avoid": ["trending", "major_news_window"],
            },
            assumptions=[
                "Only takes mean-reversion trades when RSI extreme and trend context allows",
                f"Trading limited to {session['name']} UTC session",
            ],
            failure_modes=[
                "Catastrophic losses in strong trends (mean reversion against trend)",
                "Wide-spread news creates false extremes",
            ],
            plain_english_explanation=(
                f"This strategy fades short-term extremes by buying RSI oversold conditions "
                "in an uptrend context and selling RSI overbought in a downtrend context. "
                "It relies on mean reversion and should be avoided in strong directional "
                "markets."
            ),
            **common,
        )
    )

    return specs[min(idx, len(specs) - 1)]


def generate_candidates(req: StrategyGenerateRequest) -> list[tuple[str, StrategySpec]]:
    """Return one candidate per template family, with the requested family first."""
    order = {
        "trend_pullback": [0, 1, 2],
        "breakout": [1, 0, 2],
        "mean_reversion": [2, 0, 1],
    }
    idxs = order.get(req.strategy_family.value, [0, 1, 2])
    candidates = [(f"cand-{i}", _cand(req, i)) for i in idxs]
    return candidates


def _parse_llm_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        # strip markdown fences
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    return json.loads(text)


def generate_candidates_llm(req: StrategyGenerateRequest) -> list[tuple[str, StrategySpec]]:
    client = LLMClient()
    user_prompt = (
        f"Generate 3 forex scalping strategy candidates as a JSON array. "
        f"User request: {req.prompt}. Pairs: {req.pairs}. Timeframe: {req.timeframe}. "
        f"Session: {req.session_name}. Strategy family preference: {req.strategy_family.value}. "
        f"Max spread pips: {req.max_spread_pips}. Risk per trade %: {req.risk_per_trade_pct}. "
        f"Max trades/day: {req.max_trades_per_day}.\n"
        f"Each candidate must be a JSON object conforming exactly to the schema provided "
        f"in the system prompt. Return a JSON array of strategy objects."
    )
    text = client.chat(build_system_prompt(), user_prompt)
    data = _parse_llm_json(text)
    if isinstance(data, dict) and "candidates" in data:
        data = data["candidates"]
    if not isinstance(data, list):
        raise ValueError("LLM did not return a JSON array of strategies")
    candidates: list[tuple[str, StrategySpec]] = []
    for i, item in enumerate(data):
        spec = StrategySpec.model_validate(item)
        candidates.append((f"cand-{i}", spec))
    if not candidates:
        raise ValueError("LLM returned no valid strategy candidates")
    return candidates


def generate(req: StrategyGenerateRequest) -> list[tuple[str, StrategySpec]]:
    settings = get_settings()
    if settings.LLM_PROVIDER.lower() == "llm" and settings.LLM_API_KEY:
        return generate_candidates_llm(req)
    return generate_candidates(req)
