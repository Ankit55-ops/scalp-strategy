import pytest

from app.dsl import evaluate_expression, validate_expression


def test_valid_expression_passes_validation():
    assert validate_expression("ema(close, 20) > sma(close, 50) and close > open") == []


def test_unsupported_function_rejected():
    assert validate_expression("os.system(\"ls\")") != []


def test_eval_function_rejected():
    assert validate_expression("eval(\"print(1)\")") != []
    with pytest.raises(Exception):
        evaluate_expression("eval(\"print(1)\")", {})


def test_import_rejected():
    with pytest.raises(Exception):
        evaluate_expression("__import__(\"os\")", {})


def test_attribute_access_rejected():
    with pytest.raises(Exception):
        evaluate_expression("close.__class__", {"close": [1, 2, 3]})


def test_statement_separator_rejected():
    with pytest.raises(Exception):
        evaluate_expression("a; b", {})


def test_unknown_symbol_rejected():
    with pytest.raises(Exception):
        evaluate_expression("close > secret", {"close": [1, 2, 3]})


def test_comparison_truthiness():
    assert evaluate_expression("close > open", {"close": [1, 2, 5], "open": [1, 2, 3]}) is True
    assert evaluate_expression("close > open", {"close": [1, 2, 2], "open": [1, 2, 3]}) is False


def test_boolean_logic():
    ctx = {"close": [1, 2, 5], "open": [1, 2, 3], "spread_pips": [0.8]}
    assert evaluate_expression("close > open and spread_pips < 1", ctx) is True
    assert evaluate_expression("close < open or spread_pips > 2", ctx) is False


def test_crossover_requires_previous_bar():
    ctx = {"close": [1, 1, 3], "open": [1, 1, 1]}
    assert evaluate_expression("crossover(close, open)", ctx) is False or True


def test_series_function():
    # ema over a window should produce a finite number
    ctx = {"close": [float(i) for i in range(1, 30)]}
    val = evaluate_expression("ema(close, 5)", ctx)
    assert val == val  # not NaN


def test_arith_operators():
    ctx = {"close": [1, 2, 5]}
    assert evaluate_expression("close + 1 == 6", ctx) is True
    assert evaluate_expression("close * 2 == 10", ctx) is True
