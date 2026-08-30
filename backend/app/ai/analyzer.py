"""AI Strategy Analyzer.

Converts a plain-English strategy description into a structured, testable
strategy analysis (the ADD-ON spec's JSON contract), marks testability, and —
only for fully testable output — converts it into the safe allow-listed DSL
used by the existing real-data backtest engine.

Safety guarantees:
  * No eval()/exec() anywhere. Rule expressions are generated from
    deterministic, allow-listed templates keyed by strategy family.
  * Each unique prompt text is analyzed once per workspace (cached by SHA-256);
    identical prompts never hit the AI again.
  * Input size is strictly capped (schema enforces <= MAX_PROMPT_LENGTH).
  * Martingale / unlimited grid / no-stop-loss / unlimited averaging-down
    designs are rejected with an INVALID testability status.
  * Ambiguous/incomplete descriptions never auto-convert; they are marked
    NEEDS_USER_INPUT and must be edited in the review step before testing.
  * LLM output is always validated against AIStrategyAnalysis and re-checked
    server-side; invalid LLM output is discarded and reported, never executed.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import datetime, timezone

import pydantic
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.ai.llm import LLMClient, build_system_prompt
from app.models.ai_analyzer import StrategyAnalysisCache
from app.schemas.ai_analyzer import (
    AIEntryRule,
    AIExitRule,
    AIIndicator,
    AIRiskRules,
    AISession,
    AIStopLoss,
    AIStrategyAnalysis,
    AITakeProfit,
    StrategyAnalyzeRequest,
    StrategyAnalyzeResponse,
)
from app.schemas.strategy import StrategySpec

logger = logging.getLogger("fxscalper.ai_analyzer")

SESSION_UTC = {
    "Full Day": {"name": "Full Day", "start": "00:00", "end": "23:59"},
    "Asian": {"name": "Asian", "start": "00:00", "end": "07:00"},
    "London": {"name": "London", "start": "07:00", "end": "12:00"},
    "New York": {"name": "New York", "start": "12:00", "end": "17:00"},
    "London-New York": {"name": "London-NY Overlap", "start": "12:00", "end": "16:00"},
}

DEFAULT_SYMBOLS = ["EURUSD", "GBPUSD", "USDJPY", "USDCAD"]
WRAPPER_SYMBOLS = frozenset(DEFAULT_SYMBOLS + ["USDCHF", "AUDUSD", "NZDUSD", "EURJPY", "GBPJPY", "AUDJPY", "XAUUSD"])

# -- rejection keywords (server-side, never trusted to LLM) -----------------
MARTINGALE_PATTERNS = [
    r"martingale",
    r"double[sd]? (the |our |your )?(position|s|bet|stake|lots)",
    r"2x (the )?position",
    r"increas(e|ing) position (after|on|when).*loss",
    r"add (to|more) (the |your )?lot",
]
GRID_PATTERNS = [
    r"\bgrid\b",
    r"every \d+ (pips|points)",
    r"\bdca\b",
    r"dollar.co?st average",
    r"average down",
    r"scale ?in",
    r"pyramid(ing)?",
]
NO_STOP_PATTERNS = [
    r"no stop loss",
    r"no stop-?loss",
    r"never (use|set) (a )?(stop|sl)",
    r"without (a )?(stop|sl)",
]
TIER_PATTERNS = [
    r"tier",
    r"escalat(e|ing) (the )?size",
]

_HASH_ALGO = "sha256"

# Number of unique analyses allowed per workspace per rolling hour (cache hits
# are free because they do not consume any AI/token budget).
UNIQUE_ANALYSES_PER_HOUR = 30


def _sha256(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def _scan_flagged(text: str, patterns: list[str]) -> bool:
    low = text.lower()
    return any(re.search(p, low) for p in patterns)


def _rejections(text: str, analysis: AIStrategyAnalysis) -> list[str]:
    out: list[str] = []
    low = text.lower()
    if _scan_flagged(low, MARTINGALE_PATTERNS):
        out.append("Rejected: the design appears to use a martingale / position-doubling scheme.")
    if _scan_flagged(low, GRID_PATTERNS):
        out.append("Rejected: the design appears to use an unlimited grid / averaging-down scheme.")
    if _scan_flagged(low, NO_STOP_PATTERNS):
        out.append("Rejected: the design does not declare a stop loss.")
    if _scan_flagged(low, TIER_PATTERNS):
        out.append("Rejected: the design uses tiered / escalating position sizing.")
    if not analysis.entry_rules:
        out.append("Incomplete: no entry rules could be identified.")
    return out


def _extract_timeframe(text: str) -> str | None:
    m = re.search(r"\b(M1|M5|M15|M30|H1|H4|D1)\b", text.upper())
    return m.group(1) if m else None


def _extract_symbols(text: str) -> list[str]:
    upper = text.upper()
    found = [s for s in WRAPPER_SYMBOLS if re.search(rf"\b{s}\b", upper)]
    return found or []


def _extract_sessions(text: str) -> list[AISession]:
    low = text.lower()
    found: list[AISession] = []
    if "asian" in low:
        found.append(AISession(**SESSION_UTC["Asian"]))
    if "london" in low or "ny-london" in low:
        found.append(AISession(**SESSION_UTC["London"]))
    if "new york" in low or "ny" in low:
        found.append(AISession(**SESSION_UTC["New York"]))
    if "overlap" in low:
        found.append(AISession(**SESSION_UTC["London-New York"]))
    return found


def _extract_int(text: str, name: str, default: int) -> int:
    m = re.search(rf"{name}\s*[=:]?\s*(\d{{1,3}})", text, re.IGNORECASE)
    if not m:
        return default
    val = int(m.group(1))
    return val if 1 <= val <= 100 else default


def _extract_direction(text: str) -> str:
    """Detect a directional filter: 'long only' / 'short only' else 'both'."""
    low = text.lower()
    long_only = any(
        k in low
        for k in (
            "long only", "only long", "longs only", "buy only", "only buy",
            "buy signals only", "long entries only", "go long", "look for longs",
        )
    )
    short_only = any(
        k in low
        for k in (
            "short only", "only short", "shorts only", "sell only", "only sell",
            "sell signals only", "short entries only", "go short", "look for shorts",
        )
    )
    if long_only and not short_only:
        return "long"
    if short_only and not long_only:
        return "short"
    return "both"


def _extract_range_window(text: str, default: int = 20) -> int:
    """Detect an explicit range window for breakout-style strategies."""
    m = re.search(r"(\d{1,3})\s*(?:bar|bar range|candle|period)", text.lower())
    if m:
        val = int(m.group(1))
        if 5 <= val <= 200:
            return val
    return default


def _extract_stop(text: str, atr_default: int) -> AIStopLoss:
    """Server-side stop-loss inference (honest: only ATR is fully testable).

    Structure-based stops and fixed-pip stops cannot be reproduced by the
    backtest engine's ATR-based risk engine, so they are surfaced as
    ``STRUCTURE`` / ``FIXED`` and require user calibration (NEEDS_USER_INPUT),
    never silently substituted.
    """
    low = text.lower()
    if ("structure" in low and re.search(r"stop|sl\b|stoploss", low)) or any(
        k in low
        for k in (
            "stop at structure", "structural stop", "stop on support",
            "stop at support", "stop below support", "stop under support",
            "stop at resistance", "stop below resistance",
        )
    ):
        return AIStopLoss(type="STRUCTURE", atr_period=atr_default, multiplier=1.2)
    m = re.search(r"stop\b[^\d]{0,12}?(\d{1,3}(?:\.\d{1,2})?)\s*pips?", low)
    if m:
        return AIStopLoss(type="FIXED", atr_period=atr_default, multiplier=1.2)
    if re.search(r"\batr\b", low):
        m = re.search(r"atr\s*(?:\(?\s*(\d{1,2})\s*\)?)?(?:\s*x\s*(\d(?:\.\d)?))?", low)
        period = int(m.group(1)) if m and m.group(1) else atr_default
        mult = float(m.group(2)) if m and m.group(2) else 1.2
        return AIStopLoss(type="ATR", atr_period=period, multiplier=mult)
    if re.search(r"fixed stop|fixed sl\b", low):
        return AIStopLoss(type="FIXED", atr_period=atr_default, multiplier=1.2)
    return AIStopLoss(type="ATR", atr_period=atr_default, multiplier=1.2)


def _extract_take_profit(text: str) -> AITakeProfit:
    low = text.lower()
    m = re.search(r"\b1\s*[:/]\s*(\d{1,2}(?:\.\d)?)\b", low)
    if not m:
        m = re.search(r"(?:risk[ -]reward|reward)[^\d]{0,14}?(\d{1,2}(?:\.\d)?)\b", low)
    if m:
        val = float(m.group(1))
        return AITakeProfit(type="RISK_REWARD", ratio=min(max(val, 0.5), 50.0))
    return AITakeProfit(type="RISK_REWARD", ratio=1.5)


def _extract_risk_rules(text: str) -> AIRiskRules:
    low = text.lower()
    risk = 0.25
    m = re.search(r"(?:risk|rr)(?:\s*per trade)?\s*(?:of|:|=)?\s*(\d{1,2}(?:\.\d+)?)\s*%", low)
    if m:
        risk = float(m.group(1))
    daily = 1.0
    m = re.search(r"(?:max\s*)?daily\s*(?:loss)?\s*(?:of|:|=)?\s*(\d{1,2})\s*%", low)
    if m:
        daily = float(m.group(1))
    m = re.search(r"max\s*(?:of\s*)?(\d{1,2})\s*trades", low)
    trades = int(m.group(1)) if m else 5
    m = re.search(r"(?:max\s*)?spread\s*(?:of|:|=)?\s*(\d{1,2}(?:\.\d+)?)\s*pips?", low)
    spread = float(m.group(1)) if m else 1.2
    return AIRiskRules(
        risk_per_trade_pct=min(max(risk, 0.1), 5.0),
        max_trades_per_day=max(min(trades, 100), 0),
        max_daily_loss_pct=min(max(daily, 0.1), 100.0),
        max_spread_pips=min(max(spread, 0.0), 50.0),
    )


def _family(text: str) -> str:
    low = text.lower()
    if any(k in low for k in ["mean revert", "fade to mean", "reversion", "buy dip"]):
        return "mean_reversion"
    if any(k in low for k in ["breakout", "break out", "breaks out", "range break"]):
        return "breakout"
    if any(k in low for k in ["liquidity sweep", "sweep"]):
        return "liquidity_sweep"
    if any(k in low for k in ["fade the range", "range fade", "range-bound"]):
        return "range_fade"
    if any(k in low for k in ["momentum", "cross over", "crossunder"]):
        return "momentum"
    if any(k in low for k in ["trend pullback", "pullback", "trend"]):
        return "trend_pullback"
    return "ai_suggested"


def _build_entry_rules(
    family: str,
    text: str,
    ema_fast: int,
    ema_slow: int,
    rsi_period: int,
    direction: str,
) -> list[AIEntryRule]:
    if family == "trend_pullback":
        base = [
            ("long", f"long when close > EMA({ema_fast}) and EMA({ema_slow}) rising"),
            ("short", f"short when close < EMA({ema_fast}) and EMA({ema_slow}) falling"),
        ]
    elif family == "breakout":
        window = _extract_range_window(text)
        base = [
            ("long", f"long when close breaks above the prior {window}-bar high"),
            ("short", f"short when close breaks below the prior {window}-bar low"),
        ]
    elif family == "mean_reversion":
        base = [
            ("long", f"long when RSI({rsi_period}) < 30"),
            ("short", f"short when RSI({rsi_period}) > 70"),
        ]
    elif family == "momentum":
        base = [
            ("long", f"long when EMA({ema_fast}) crosses above SMA({ema_slow})"),
            ("short", f"short when EMA({ema_fast}) crosses below SMA({ema_slow})"),
        ]
    elif family == "liquidity_sweep":
        base = [
            ("long", "long when a sweep of a prior low fails and closes back above it"),
            ("short", "short when a sweep of a prior high fails and closes back below it"),
        ]
    else:  # range_fade / ai_suggested
        base = [
            ("long", "long when price fades the upper extreme of the range"),
            ("short", "short when price fades the lower extreme of the range"),
        ]
    if direction == "long":
        base = [r for r in base if r[0] == "long"]
    elif direction == "short":
        base = [r for r in base if r[0] == "short"]
    return [AIEntryRule(side=side, rule=rule) for side, rule in base]


def _build_exit_rule(family: str, text: str) -> list[AIExitRule]:
    low = text.lower()
    m = re.search(
        r"exit\s*(?:when|on|if)?[^\d]{0,30}?rsi\s*\(?\s*(\d{1,2})\s*\)?\s*([<>=]+)\s*(\d{1,3})",
        low,
    )
    if m:
        return [AIExitRule(rule=f"exit when RSI({m.group(1)}) {m.group(2)} {m.group(3)}")]
    if family == "momentum":
        return [AIExitRule(rule="exit when the moving averages cross back")]
    if family == "mean_reversion":
        return [AIExitRule(rule="exit when RSI(14) returns to the neutral zone (45-55)")]
    return [AIExitRule(rule="exit when the entry signal flips or the stop/target is hit")]


def _mock_analyze(text: str) -> AIStrategyAnalysis:
    """Deterministic offline analyzer: keyword/scoring based, fully testable."""
    low = text.lower()
    tf = _extract_timeframe(text) or "M5"
    symbols = _extract_symbols(text) or ["EURUSD"]
    direction = _extract_direction(text)
    sessions = _extract_sessions(text) or [AISession(**SESSION_UTC["Full Day"])]
    family = _family(text)

    ema_fast = _extract_int(text, "ema", 10)
    ema_slow = _extract_int(text, "sma", 50)
    rsi_period = _extract_int(text, "rsi", 14)
    atr_period = _extract_int(text, "atr", 14)

    indicators = [
        AIIndicator(name="EMA", parameters={"period": ema_fast}),
        AIIndicator(name="EMA", parameters={"period": ema_slow}),
        AIIndicator(name="RSI", parameters={"period": rsi_period}),
        AIIndicator(name="ATR", parameters={"period": atr_period}),
    ]

    entry_rules = _build_entry_rules(
        family, text, ema_fast, ema_slow, rsi_period, direction
    )
    exit_rules = _build_exit_rule(family, text)
    stop_loss = _extract_stop(text, atr_period)
    take_profit = _extract_take_profit(text)

    assumptions = [
        "Rules are evaluated on completed candles only (never the forming candle).",
        "Entry rules gate on the UTC session window via the in_session signal.",
    ]
    if not _extract_sessions(text):
        assumptions.append(
            "No session window was stated; the strategy trades across all UTC sessions."
        )
    if not _extract_timeframe(text):
        assumptions.append(
            "No timeframe was stated in the text; a timeframe is required before testing."
        )
    if not _extract_symbols(text):
        assumptions.append(
            "No symbol was stated in the text; at least one symbol is required before testing."
        )
    if direction != "both":
        assumptions.append(f"Trades {direction} direction only, as stated.")
    if stop_loss.type != "ATR":
        assumptions.append(
            f"Stop is {stop_loss.type}. Only an ATR stop can auto-calibrate; "
            "review/complete the stop before testing."
        )
    elif not re.search(r"\batr\b", low):
        assumptions.append(
            f"No ATR stop was specified; ATR({atr_period}) x{stop_loss.multiplier} assumed."
        )
    if not re.search(r"(?:\b1\s*[:/]|risk[ -]reward|reward)", low):
        assumptions.append(
            f"No take-profit ratio was specified; risk:reward {take_profit.ratio} assumed."
        )

    analysis = AIStrategyAnalysis(
        name=family.replace("_", " ").title() + " " + tf,
        description=(
            f"Analyzed from the description: {low[:280]}"
        ),
        strategy_family=family,
        timeframe=tf,
        recommended_symbols=symbols[:4],
        sessions_utc=sessions[:2],
        indicators=indicators,
        entry_rules=entry_rules,
        exit_rules=exit_rules,
        risk_rules=_extract_risk_rules(text),
        stop_loss=stop_loss,
        take_profit=take_profit,
        assumptions=assumptions,
        warnings=[],
        failure_conditions=[
            "Wide spread / low-liquidity conditions can degrade fills.",
        ],
        testability_status="VALID",
    )
    return analysis


def _apply_status(analysis: AIStrategyAnalysis, text: str) -> AIStrategyAnalysis:
    """Server-side testability checks that override any LLM-supplied status."""
    flags = _rejections(text, analysis)
    if flags:
        analysis.warnings = list(dict.fromkeys(analysis.warnings + flags))
        analysis.testability_status = "INVALID"
        return analysis

    # Inference clarity: we need an unambiguous timeframe + at least one symbol
    # declared in the text + a structured entry rule + an ATR stop loss that the
    # backtest risk engine can actually reproduce, otherwise the strategist must
    # review/edit before anything runs (engine only ATR-calibrates stops).
    missing: list[str] = []
    if not _extract_timeframe(text):
        missing.append("timeframe")
    if not _extract_symbols(text):
        missing.append("symbols")
    if not analysis.entry_rules:
        missing.append("entry rules")
    if analysis.stop_loss.type != "ATR":
        missing.append("a testable (ATR) stop loss method")
    if missing:
        analysis.testability_status = "NEEDS_USER_INPUT"
        analysis.warnings.append(
            "Needs your input before it can be tested: " + ", ".join(missing) + "."
        )
        return analysis

    analysis.testability_status = "VALID"
    return analysis


def _llm_analyze(text: str) -> AIStrategyAnalysis:
    """Single-call OpenAI-compatible analysis, validated against the schema.

    Invalid/unsafe output is rejected by schema validation + server-side checks
    in _apply_status; it is never executed.
    """
    client = LLMClient()
    system = build_system_prompt() + (
        "\n\nConvert the user's strategy description into STRICT JSON with EXACTLY these "
        "keys: name, description, strategy_family, timeframe (M1|M5|M15|M30|H1|H4|D1), "
        "recommended_symbols, sessions_utc, indicators, entry_rules, exit_rules, "
        "risk_rules, stop_loss, take_profit, assumptions, warnings, failure_conditions, "
        "testability_status. entry_rules is a list of {side: long|short, rule:string}. "
        "Rules must be simple and testable; never invent a martingale, grid, or "
        "no-stop-loss design. Return ONLY JSON, no markdown fences."
    )
    resp = client.chat(system=system, user=text, temperature=0.2)
    try:
        data = json.loads(resp)
    except json.JSONDecodeError as exc:
        logger.warning("llm analyzer: invalid JSON output discarded (%s)", exc)
        raise

    try:
        analysis = AIStrategyAnalysis.model_validate(data)
    except Exception as exc:
        logger.warning("llm analyzer: schema-invalid output discarded (%s)", exc)
        raise

    # Testability is re-checked by the caller via _apply_status — we never
    # trust the LLM's self-assessed status.
    return analysis


def _provider_requested(req: StrategyAnalyzeRequest, configured: str) -> str:
    if req.provider != "auto":
        return req.provider
    return "llm" if configured == "llm" else "mock"


def _check_rate(db: Session, workspace_id: str) -> None:
    """Hard cap on unique analyses per workspace per rolling hour.

    Cache hits are free because they never reach the AI. The window is
    ``now - 1 hour`` (not ``>= now``): a freshly-inserted row is always
    fractionally older than the current time, so ``>= now`` would always
    count zero and the limit would never fire.
    """
    from datetime import timedelta

    from fastapi import HTTPException
    from sqlalchemy import func

    cutoff = datetime.now(timezone.utc) - timedelta(hours=1)
    count = (
        db.query(func.count(StrategyAnalysisCache.id))
        .filter(
            StrategyAnalysisCache.workspace_id == workspace_id,
            StrategyAnalysisCache.created_at >= cutoff,
        )
        .scalar()
    )
    if count is not None and count >= UNIQUE_ANALYSES_PER_HOUR:
        raise HTTPException(status_code=429, detail="AI analysis limit reached for this hour")


def analyze_strategy(
    db: Session,
    workspace_id: str,
    req: StrategyAnalyzeRequest,
) -> StrategyAnalyzeResponse:
    """Run the analyzer: cache look-up -> (mock|llm) -> validate -> convert."""

    from app.core.config import get_settings

    _check_rate(db, workspace_id)

    text = req.prompt_text.strip()
    if len(text) > 4000:
        raise ValueError("prompt_text must be at most 4000 characters")
    if len(text) < 10:
        raise ValueError("prompt_text must be at least 10 characters")

    digest = _sha256(text)
    cached = (
        db.query(StrategyAnalysisCache)
        .filter_by(workspace_id=workspace_id, text_sha256=digest)
        .first()
    )
    if cached is not None:
        analysis = AIStrategyAnalysis.model_validate(cached.analysis)
        return StrategyAnalyzeResponse(
            analysis=analysis,
            converted=cached.converted,
            strategy_spec=(
                StrategySpec.model_validate(cached.strategy_spec)
                if cached.strategy_spec is not None
                else None
            ),
            suggested_spec=_suggested_spec(analysis),
            cache_hit=True,
            provider_used=cached.provider_used,
            text_sha256=digest,
        )

    provider = _provider_requested(req, get_settings().LLM_PROVIDER)
    if provider == "llm":
        try:
            analysis = _llm_analyze(text)
        except Exception as exc:  # never fail the request — degrade gracefully
            logger.warning("llm analyzer failed (%s); using offline engine", exc)
            provider = "mock"
            analysis = _mock_analyze(text)
            analysis.warnings.append(
                "The configured LLM provider failed; this analysis came from "
                "the offline engine — review before use."
            )
    else:
        analysis = _mock_analyze(text)

    analysis = _apply_status(analysis, text)

    converted, spec = False, None
    if analysis.testability_status == "VALID":
        try:
            spec = _to_strategy_spec(analysis)
            converted = spec is not None
        except (pydantic.ValidationError, TypeError, ValueError) as exc:
            logger.warning("analyzer: DSL conversion failed: %s", exc)
            analysis.testability_status = "NEEDS_USER_INPUT"
            analysis.warnings.append("Could not convert to an executable spec; please edit the rules.")

    suggested = None if converted else _suggested_spec(analysis)

    row = StrategyAnalysisCache(
        workspace_id=workspace_id,
        text_sha256=digest,
        prompt_text=text,
        provider_used=provider,
        testability_status=analysis.testability_status,
        analysis=analysis.model_dump(mode="json"),
        strategy_spec=spec.model_dump(mode="json") if spec else None,
        converted=converted,
    )
    db.add(row)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        cached = (
            db.query(StrategyAnalysisCache)
            .filter_by(workspace_id=workspace_id, text_sha256=digest)
            .first()
        )
        if cached is not None:
            cached_analysis = AIStrategyAnalysis.model_validate(cached.analysis)
            return StrategyAnalyzeResponse(
                analysis=cached_analysis,
                converted=cached.converted,
                strategy_spec=(
                    StrategySpec.model_validate(cached.strategy_spec)
                    if cached.strategy_spec is not None
                    else None
                ),
                suggested_spec=_suggested_spec(cached_analysis),
                cache_hit=True,
                provider_used=cached.provider_used,
                text_sha256=digest,
            )

    return StrategyAnalyzeResponse(
        analysis=analysis,
        converted=converted,
        strategy_spec=spec,
        suggested_spec=suggested,
        cache_hit=False,
        provider_used=provider,
        text_sha256=digest,
    )


def _suggested_spec(analysis: AIStrategyAnalysis) -> StrategySpec | None:
    """A review draft for NEEDS_USER_INPUT analyses.

    Always carries fully-formed, allow-listed DSL expressions so the user only
    has to complete what the text did not state (e.g. symbol / timeframe / stop
    choice) instead of hand-writing expressions. Returns None when no valid
    draft can be produced (engine never guesses into an unsafe design).
    """
    if analysis.testability_status != "NEEDS_USER_INPUT":
        return None
    try:
        return _to_strategy_spec(analysis)
    except (pydantic.ValidationError, TypeError, ValueError) as exc:
        logger.warning("analyzer: suggested spec failed: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Safe-DSL conversion (allow-listed templates only — no eval/exec).
# ---------------------------------------------------------------------------

def _lookup(analysis: AIStrategyAnalysis, name: str, fallback: int) -> int:
    for ind in analysis.indicators:
        if ind.name.upper() == name.upper():
            period = ind.parameters.get("period")
            if period:
                try:
                    p = int(period)
                    if 1 <= p <= 100:
                        return p
                except (TypeError, ValueError):
                    pass
    return fallback


def _entry_exprs(family: str, analysis: AIStrategyAnalysis) -> list[tuple[str, str]]:
    ema_fast = _lookup(analysis, "EMA", 20)
    ema_slow = _lookup(analysis, "SMA", 50)
    rsi_p = _lookup(analysis, "RSI", 14)

    if family == "trend_pullback":
        return [
            ("long", f"ema(close,{ema_slow}) > ema(close,{ema_fast}) and low <= ema(close,{ema_fast}) and close > ema(close,{ema_fast}) and in_session"),
            ("short", f"ema(close,{ema_slow}) < ema(close,{ema_fast}) and high >= ema(close,{ema_fast}) and close < ema(close,{ema_fast}) and in_session"),
        ]
    if family == "breakout":
        window = _range_window_from_rules(analysis)
        return [
            ("long", f"close > highest(high,{window}) and in_session"),
            ("short", f"close < lowest(low,{window}) and in_session"),
        ]
    if family == "mean_reversion":
        return [
            ("long", f"rsi(close,{rsi_p}) < 30 and in_session"),
            ("short", f"rsi(close,{rsi_p}) > 70 and in_session"),
        ]
    if family == "momentum":
        return [
            ("long", f"crossover(ema(close,{ema_fast}),ema(close,{ema_slow})) and in_session"),
            ("short", f"crossunder(ema(close,{ema_fast}),ema(close,{ema_slow})) and in_session"),
        ]
    if family == "liquidity_sweep":
        return [
            ("long", f"low < lowest(low,{int(max(ema_slow,20))}) and close > low and in_session"),
            ("short", f"high > highest(high,{int(max(ema_slow,20))}) and close < high and in_session"),
        ]
    # range_fade / ai_suggested default: range fade
    return [
        ("long", "close < sma(close,20) and rsi(close,14) < 35 and in_session"),
        ("short", "close > sma(close,20) and rsi(close,14) > 65 and in_session"),
    ]


def _range_window_from_rules(analysis: AIStrategyAnalysis, default: int = 20) -> int:
    for r in analysis.entry_rules:
        m = re.search(r"prior (\d{1,3})-?bar", r.rule)
        if m:
            val = int(m.group(1))
            if 5 <= val <= 200:
                return val
    return default


def _to_strategy_spec(analysis: AIStrategyAnalysis) -> StrategySpec | None:
    family = analysis.strategy_family.value
    slow = _lookup(analysis, "SMA", 50)
    fast = _lookup(analysis, "EMA", 20)
    atr_p = _lookup(analysis, "ATR", 14)
    session = analysis.sessions_utc[0] if analysis.sessions_utc else AISession(**SESSION_UTC["Full Day"])

    # Build entry rules only for the sides the analysis actually declared, in the
    # declared order, so long-only / short-only descriptions stay directional.
    expr_by_side = dict(_entry_exprs(family, analysis))
    entry_rules = []
    for idx, r in enumerate(analysis.entry_rules, start=1):
        expr = expr_by_side.get(r.side)
        if not expr:
            continue
        entry_rules.append({
            "id": f"{r.side}_rule_{idx}",
            "description": r.rule,
            "expression": expr,
        })

    exit_rules = [{
        "id": "exit_rule_1",
        "description": analysis.exit_rules[0].rule if analysis.exit_rules else "Exit when the entry signal flips.",
        "expression": f"close < ema(close,{fast})" if family == "trend_pullback" else "rsi(close,14) > 60",
    }]

    if not entry_rules:
        return None

    # AI schema uses RISK_REWARD/ATR/FIXED (uppercase); the strategy DSL uses
    # risk_reward/ATR/FIXED/STRUCTURE literals.
    tp_method = {
        "RISK_REWARD": "risk_reward",
        "ATR": "ATR",
        "FIXED": "FIXED",
    }.get(analysis.take_profit.type, "risk_reward")
    sl_method = {
        "ATR": "ATR",
        "FIXED": "FIXED",
        "STRUCTURE": "STRUCTURE",
    }.get(analysis.stop_loss.type, "ATR")

    spec_data = {
        "name": analysis.name,
        "version": "1.0.0",
        "strategy_family": family,
        "supported_pairs": analysis.recommended_symbols[:6] or ["EURUSD"],
        "supported_timeframes": [analysis.timeframe],
        "sessions_utc": [{"name": session.name, "start": session.start, "end": session.end}],
        "market_regime": {"preferred": [], "avoid": []},
        "indicators": [i.model_dump() for i in analysis.indicators],
        "entry_rules": entry_rules,
        "exit_rules": exit_rules,
        "risk_management": {
            "risk_per_trade_pct": analysis.risk_rules.risk_per_trade_pct,
            "max_daily_loss_pct": analysis.risk_rules.max_daily_loss_pct,
            "max_consecutive_losses": 3,
            "max_open_positions": 1,
            "max_trades_per_day": analysis.risk_rules.max_trades_per_day,
            "stop_loss_method": sl_method,
            "stop_loss_parameters": {
                "atr_period": analysis.stop_loss.atr_period,
                "atr_multiplier": analysis.stop_loss.multiplier,
            },
            "take_profit_method": tp_method,
            "take_profit_parameters": {"risk_reward_ratio": analysis.take_profit.ratio},
        },
        "execution_filters": {
            "max_spread_pips": analysis.risk_rules.max_spread_pips,
            "max_slippage_pips": 0.5,
            "minimum_atr_pips": 3.0,
            "news_blackout_minutes_before": 15,
            "news_blackout_minutes_after": 15,
        },
        "assumptions": analysis.assumptions or [],
        "failure_modes": analysis.failure_conditions or [],
        "plain_english_explanation": analysis.description,
        "confidence_notes": "Generated by the AI Strategy Analyzer; backtest before use.",
    }

    # ensure indicators include EMA/SMA/RSI used by generated rules
    have = {i.name.upper() for i in analysis.indicators}
    for name, period in (("EMA", fast), ("SMA", slow), ("RSI", 14), ("ATR", atr_p)):
        if name not in have:
            spec_data["indicators"].append({"name": name, "parameters": {"period": period}})

    try:
        return StrategySpec.model_validate(spec_data)
    except (pydantic.ValidationError, TypeError, ValueError) as exc:
        logger.warning("analyzer: spec validation failed for %s: %s", family, exc)
        return None