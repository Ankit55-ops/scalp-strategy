import pytest

from app.services.market_math import (
    pip_size,
    position_size,
    price_to_pips,
    spread_in_pips,
    normalized_symbol,
    symbol_variant,
)


def test_pip_size_jpy():
    assert pip_size("JPY") == 0.01


def test_pip_size_non_jpy():
    assert pip_size("USD") == 0.0001


def test_pip_size_explicit_position():
    assert pip_size("USD", pip_position=2) == 0.01


def test_price_to_pips_conversion():
    assert price_to_pips(0.005, 0.0001) == 50.0
    assert price_to_pips(0.50, 0.01) == 50.0


def test_spread_in_pips():
    assert spread_in_pips(1.1000, 1.1001, 0.0001) == pytest.approx(1.0, abs=1e-9)
    assert spread_in_pips(150.00, 150.02, 0.01) == pytest.approx(2.0, abs=1e-9)


def test_position_sizing_risk_matches():
    # EURUSD: risk 0.25% of 100k = 250; stop 20 pips -> 0.002 price distance
    result = position_size(
        account_balance=100000.0,
        risk_per_trade_pct=0.25,
        entry_price=1.1000,
        stop_price=1.0980,
        pip_size=0.0001,
        pnl_per_pip_per_unit=0.0001,  # risk-equivalent approx; see below
        contract_size=100000.0,
    )
    # pnl per pip = pip_size * contract_size = 0.0001 * 100000 = 10 per pip
    # risked = 10 * 20 = 200 ; size = 250/200 * 100000 = 125000
    assert result.size_units == pytest.approx(125000.0)
    assert result.risk_amount == pytest.approx(250.0)
    assert result.stop_distance_pips == pytest.approx(20.0)


def test_position_size_rejects_zero_stop_distance():
    with pytest.raises(ValueError):
        position_size(
            100000, 0.25, 1.1000, 1.1000, 0.0001, 0.0001, 100000
        )


def test_symbol_normalization():
    assert normalized_symbol("EURUSD", "EURUSD.a") == "EURUSD"
    assert normalized_symbol("EURUSD", "EUR/USD") == "EURUSD"
    assert normalized_symbol("EURUSD", "EURUSDm") == "EURUSD"
    assert normalized_symbol("EURUSD", "EURUSD") == "EURUSD"


def test_symbol_variant():
    assert symbol_variant("EURUSD", suffix=".a") == "EURUSD.a"
    assert symbol_variant("EURUSD") == "EURUSD"
