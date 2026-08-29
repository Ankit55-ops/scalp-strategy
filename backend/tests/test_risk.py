from app.models import RiskProfile
from app.risk.engine import ProposedOrder, RiskEngine
from app.risk.killswitch import KillSwitchRegistry

import pytest


@pytest.fixture(autouse=True)
def _reset_killswitches():
    registry = KillSwitchRegistry()
    registry.reset()
    yield
    registry.reset()


def make_profile(**overrides):
    defaults = dict(
        risk_per_trade_pct=0.25,
        max_daily_loss_pct=1.0,
        max_weekly_loss_pct=3.0,
        max_drawdown_pct=10.0,
        max_consecutive_losses=3,
        max_open_positions=1,
        max_trades_per_day=5,
        max_correlated_exposure_pct=2.0,
        max_spread_pips=1.2,
        max_slippage_pips=0.5,
        news_blackout_minutes_before=15,
        news_blackout_minutes_after=15,
        hard_stop_distance_pips=0.0,
    )
    defaults.update(overrides)
    return RiskProfile(**defaults)


def order(**overrides):
    defaults = dict(
        symbol="EURUSD",
        side="buy",
        size_units=10000.0,
        entry_price=1.1000,
        stop_price=1.0980,
        account_balance=100000.0,
        account_equity=100000.0,
        spread_pips=0.8,
        ts=1700000000.0,
        is_blackout=False,
        in_session=True,
        base_currency="USD",
        correlated_exposure={"eurusd": 1.0},
    )
    defaults.update(overrides)
    return ProposedOrder(**defaults)


def test_approves_normal_order():
    engine = RiskEngine(KillSwitchRegistry(), make_profile())
    decision = engine.evaluate(order())
    assert decision.approved is True


def test_global_kill_switch_blocks():
    ks = KillSwitchRegistry()
    ks.set_global(True)
    engine = RiskEngine(ks, make_profile())
    decision = engine.evaluate(order())
    assert decision.approved is False
    assert "kill switch" in decision.rejection_reason


def test_per_strategy_kill_switch_blocks():
    ks = KillSwitchRegistry()
    ks.set_strategy("strat-1", True)
    engine = RiskEngine(ks, make_profile())
    decision = engine.evaluate(order(strategy_id="strat-1"))
    assert decision.approved is False


def test_per_pair_kill_switch_blocks():
    ks = KillSwitchRegistry()
    ks.set_pair("EURUSD", True)
    engine = RiskEngine(ks, make_profile())
    decision = engine.evaluate(order())
    assert decision.approved is False


def test_spread_threshold_rejection():
    engine = RiskEngine(KillSwitchRegistry(), make_profile(max_spread_pips=1.2))
    decision = engine.evaluate(order(spread_pips=2.5))
    assert decision.approved is False
    assert "spread" in decision.rejection_reason


def test_news_blackout_rejection():
    engine = RiskEngine(KillSwitchRegistry(), make_profile())
    decision = engine.evaluate(order(is_blackout=True))
    assert decision.approved is False
    assert "blackout" in decision.rejection_reason


def test_outside_session_rejection():
    engine = RiskEngine(KillSwitchRegistry(), make_profile())
    decision = engine.evaluate(order(in_session=False))
    assert decision.approved is False
    assert "session" in decision.rejection_reason


def test_max_open_positions_rejection():
    engine = RiskEngine(KillSwitchRegistry(), make_profile(max_open_positions=1))
    assert engine.evaluate(order()).approved is True
    decision = engine.evaluate(order())
    assert decision.approved is False
    assert "open positions" in decision.rejection_reason


def test_correlated_exposure_rejection():
    engine = RiskEngine(KillSwitchRegistry(), make_profile(max_correlated_exposure_pct=2.0))
    decision = engine.evaluate(order(correlated_exposure={"eurusd": 5.0}))
    assert decision.approved is False
    assert "correlated" in decision.rejection_reason


def test_min_stop_distance_rejection():
    # stop distance = 20 pips (>= 5) OK
    engine = RiskEngine(KillSwitchRegistry(), make_profile(hard_stop_distance_pips=5.0, max_open_positions=10))
    assert engine.evaluate(order(entry_price=1.1000, stop_price=1.0995)).approved is True
    # fresh engine, stop distance = 2 pips (< 5) rejects
    engine2 = RiskEngine(KillSwitchRegistry(), make_profile(hard_stop_distance_pips=5.0, max_open_positions=10))
    decision = engine2.evaluate(order(entry_price=1.1000, stop_price=1.0998))
    assert decision.approved is False
    assert "stop distance" in decision.rejection_reason


def test_audit_dict_shape():
    engine = RiskEngine(KillSwitchRegistry(), make_profile())
    decision = engine.evaluate(order())
    audit = decision.as_audit_dict(order())
    assert audit["correlation_id"]
    assert audit["result"] == "approved"
    assert isinstance(audit["checks"], list)
    assert all("check" in c and "passed" in c for c in audit["checks"])


def test_rejection_produces_audit_record():
    engine = RiskEngine(KillSwitchRegistry(), make_profile(max_spread_pips=1.2))
    o = order(spread_pips=3.0)
    decision = engine.evaluate(o)
    audit = decision.as_audit_dict(o)
    assert audit["result"] == "rejected"
    assert audit["rejection_reason"] is not None
