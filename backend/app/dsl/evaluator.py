"""Safe evaluation of DSL AST nodes against an allow-listed environment."""

from __future__ import annotations

import math
from typing import Any, Callable

from app.dsl.parser import ALLOWED_FUNCTIONS, Node, ParseError, parse_expression


class ExpressionError(Exception):
    pass


# Symbol names that are available in rules. Unknown symbols are rejected.
def is_reserved_symbol(name: str) -> bool:
    return name in ALLOWED_SYMBOLS


# ---------------------------------------------------------------------------
# Built-in series helpers used by allow-listed functions. These mimic the
# names a strategy author expects (EMA, RSI, etc.). Each function receives
# a window of the relevant value and returns a scalar.
# ---------------------------------------------------------------------------


def _ema(values: list[float], period: int) -> float:
    if not values or period <= 0:
        return float("nan")
    k = 2 / (period + 1)
    ema = values[0]
    for v in values[1:]:
        ema = v * k + ema * (1 - k)
    return ema


def _sma(values: list[float], period: int) -> float:
    if not values or period <= 0:
        return float("nan")
    return sum(values[-period:]) / min(period, len(values))


def _stdev(values: list[float], period: int) -> float:
    if not values or period <= 0:
        return float("nan")
    window = values[-period:]
    mean = sum(window) / len(window)
    variance = sum((x - mean) ** 2 for x in window) / len(window)
    return math.sqrt(variance)


def _rsi(values: list[float], period: int) -> float:
    if values is None or period <= 0:
        return float("nan")
    if len(values) <= period:
        return 50.0
    gains = []
    losses = []
    for i in range(1, len(values)):
        change = values[i] - values[i - 1]
        gains.append(max(change, 0))
        losses.append(max(-change, 0))
    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def _atr(values: list[float], period: int) -> float:
    if len(values) < 2 or period <= 0:
        return float("nan")
    trs = []
    for i in range(1, len(values)):
        trs.append(abs(values[i] - values[i - 1]))
    return sum(trs[-period:]) / min(period, len(trs))


def _highest(values: list[float], period: int) -> float:
    if not values or period <= 0:
        return float("nan")
    return max(values[-period:])


def _lowest(values: list[float], period: int) -> float:
    if not values or period <= 0:
        return float("nan")
    return min(values[-period:])


# ---------------------------------------------------------------------------
# Function registry
# ---------------------------------------------------------------------------

def _resolve_scalar(node: Node, ctx: dict) -> Any:
    """For a non-series argument, evaluate the node to a scalar."""
    return _evaluate(node, ctx)


def _resolve_series_name(node: Node, ctx: dict, allow_prev: bool = False) -> str:
    if node.kind == "symbol":
        return node.value
    raise ExpressionError("series argument must be a bare symbol like close/ema('...')")


def _call_series(ctx: dict, name: str, arg_nodes: list[Node]) -> Any:
    """Series functions: first arg is a symbol name, rest are scalar params."""
    series_name = _resolve_series_name(arg_nodes[0], ctx)
    series = ctx.get(series_name)
    if series is None:
        raise ExpressionError(f"unknown series '{series_name}'")
    if not isinstance(series, list):
        raise ExpressionError(f"'{series_name}' is not a series")
    params = [_resolve_scalar(n, ctx) for n in arg_nodes[1:]]
    if name == "ema":
        return _ema(series, int(params[0]))
    if name == "sma":
        return _sma(series, int(params[0]))
    if name == "rsi":
        return _rsi(series, int(params[0]))
    if name == "atr":
        return _atr(series, int(params[0]))
    if name == "stdev":
        return _stdev(series, int(params[0]))
    if name == "highest":
        return _highest(series, int(params[0]))
    if name == "lowest":
        return _lowest(series, int(params[0]))
    raise ExpressionError(f"unknown series function '{name}'")


def _call_cross(ctx: dict, name: str, arg_nodes: list[Node]) -> bool:
    if not ctx.get("__prev"):
        return False
    cur_a, prev_a = _cross_endpoints(ctx, arg_nodes[0])
    cur_b, prev_b = _cross_endpoints(ctx, arg_nodes[1])
    if name == "crossover":
        return cur_a >= cur_b and prev_a < prev_b
    return cur_a <= cur_b and prev_a > prev_b


_SERIES_FNS = {
    "ema": _ema,
    "sma": _sma,
    "rsi": _rsi,
    "atr": _atr,
    "stdev": _stdev,
    "highest": _highest,
    "lowest": _lowest,
}


def _cross_endpoints(ctx: dict, node: Node) -> tuple[float, float]:
    """Current + previous bar values for a crossover/crossunder argument.

    Supports bare series symbols (``crossover(close, sma(close,20))``) as well
    as nested series calls such as ``crossover(ema(close,10), ema(close,50))``
    so real strategies generated by the analyzer are actually evaluable.
    """
    if node.kind == "symbol":
        return _series(ctx, node.value), _prev(ctx, node.value)
    if node.kind == "call":
        fn = _SERIES_FNS.get(node.name)
        if fn is None:
            raise ExpressionError(
                f"crossover/crossunder expects series symbols or ema/sma/rsi/atr/"
                f"stdev/highest/lowest calls, got '{node.name}'"
            )
        series_name = _resolve_series_name(node.args[0], ctx)
        series = ctx.get(series_name)
        if not isinstance(series, list) or not series:
            raise ExpressionError(f"'{series_name}' is not a series")
        params = [int(_resolve_scalar(n, ctx)) for n in node.args[1:]]
        cur = fn(series, *params)
        prev = fn(series[:-1], *params) if len(series) > 1 else float("nan")
        return cur, prev
    raise ExpressionError("crossover/crossunder arguments must be series")


# name -> (kind, [series arg indices]) ; kind in {"series","cross","scalar"}
_FUNC_INFO: dict[str, str] = {
    "ema": "series",
    "sma": "series",
    "rsi": "series",
    "atr": "series",
    "stdev": "series",
    "highest": "series",
    "lowest": "series",
    "crossover": "cross",
    "crossunder": "cross",
    "abs": "scalar",
    "min": "scalar",
    "max": "scalar",
}


def _series(ctx: dict, name: str) -> Any:
    arr = ctx.get(name)
    if arr is None:
        raise ExpressionError(f"unknown symbol '{name}' in rule")
    return arr[-1]


def _prev(ctx: dict, name: str) -> Any:
    arr = ctx.get(name)
    if not isinstance(arr, list) or len(arr) < 2:
        raise ExpressionError(f"'{name}' has no previous value available")
    return arr[-2]


# Symbols a context may provide. This is the allow-list for symbol access.
ALLOWED_SYMBOLS = frozenset(
    {
        "open",
        "high",
        "low",
        "close",
        "volume",
        "spread_pips",
        "atr",
        "time_minute",
        "in_session",
        "is_blackout",
    }
)


def _evaluate(node: Node, ctx: dict) -> Any:
    kind = node.kind
    if kind == "literal":
        return node.value
    if kind == "symbol":
        name = node.value
        if name not in ALLOWED_SYMBOLS:
            raise ExpressionError(f"symbol '{name}' is not allow-listed")
        if name not in ctx:
            raise ExpressionError(f"symbol '{name}' not present in context")
        val = ctx[name]
        if isinstance(val, list):
            if not val:
                raise ExpressionError(f"symbol '{name}' has no data")
            return val[-1]
        return val
    if kind == "call":
        kind_ = _FUNC_INFO[node.name]
        if kind_ == "series":
            return _call_series(ctx, node.name, node.args)
        if kind_ == "cross":
            return _call_cross(ctx, node.name, node.args)
        values = [_evaluate(a, ctx) for a in node.args]
        if node.name == "abs":
            return abs(values[0])
        if node.name == "min":
            return min(values)
        if node.name == "max":
            return max(values)
        raise ExpressionError(f"unsupported function '{node.name}'")
    if kind == "unary":
        val = _evaluate(node.left, ctx)
        if node.value == "-":
            return -val
        raise ExpressionError(f"unsupported unary '{node.value}'")
    if kind == "logical":
        op = node.value
        if op == "and":
            return bool(_evaluate(node.left, ctx)) and bool(_evaluate(node.right, ctx))
        if op == "or":
            return bool(_evaluate(node.left, ctx)) or bool(_evaluate(node.right, ctx))
        if op == "not":
            return not bool(_evaluate(node.left, ctx))
        raise ExpressionError(f"unsupported logical '{op}'")
    if kind == "binary":
        op = node.value
        left = _evaluate(node.left, ctx)
        right = _evaluate(node.right, ctx)
        if op == "+":
            return left + right
        if op == "-":
            return left - right
        if op == "*":
            return left * right
        if op == "/":
            return left / right if right else float("inf")
        if op == "%":
            return left % right if right else float("nan")
        if op == "==":
            return left == right
        if op == "!=":
            return left != right
        if op == ">":
            return left > right
        if op == ">=":
            return left >= right
        if op == "<":
            return left < right
        if op == "<=":
            return left <= right
        raise ExpressionError(f"unsupported binary '{op}'")
    raise ExpressionError(f"unsupported node kind '{kind}'")


def validate_expression(expr: str) -> list[str]:
    """Validate an expression string. Return a list of validation errors (empty if valid)."""
    errors: list[str] = []
    try:
        parse_expression(expr)
    except ParseError as exc:
        errors.append(str(exc))
    return errors


def evaluate_expression(expr: str, context: dict) -> Any:
    """Parse and evaluate an expression. Raises ExpressionError on any problem."""
    node = parse_expression(expr)
    return _evaluate(node, context)
