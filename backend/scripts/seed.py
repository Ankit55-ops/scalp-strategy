"""Seed the database with a demo user, workspace, symbols, and sample strategy."""

from __future__ import annotations

from passlib.context import CryptContext

from app.db.session import SessionLocal
from app.models import ForexSymbol, Strategy, StrategyRule, StrategyVersion, User, Workspace
from app.schemas.strategy import StrategySpec

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

SAMPLE_SPEC = {
    "name": "EURUSD M5 Trend Pullback",
    "version": "1.0.0",
    "strategy_family": "trend_pullback",
    "supported_pairs": ["EURUSD"],
    "supported_timeframes": ["M5"],
    "sessions_utc": [{"name": "London", "start": "07:00", "end": "12:00"}],
    "market_regime": {
        "preferred": ["trending", "high_liquidity"],
        "avoid": ["ranging", "high_spread", "major_news_window"],
    },
    "indicators": [
        {"name": "EMA", "parameters": {"period": 20}},
        {"name": "EMA", "parameters": {"period": 40}},
        {"name": "ATR", "parameters": {"period": 14}},
    ],
    "entry_rules": [
        {
            "id": "long_rule_1",
            "description": "Price pulls back to fast EMA in an uptrend and bounces",
            "expression": "ema(close,40) > ema(close,20) and low <= ema(close,20) and close > ema(close,20)",
        },
        {
            "id": "short_rule_1",
            "description": "Price pulls back to fast EMA in a downtrend and rolls over",
            "expression": "ema(close,40) < ema(close,20) and high >= ema(close,20) and close < ema(close,20)",
        },
    ],
    "exit_rules": [
        {
            "id": "exit_rule_1",
            "description": "Exit when price closes back below fast EMA",
            "expression": "close < ema(close,20)",
        }
    ],
    "risk_management": {
        "risk_per_trade_pct": 0.25,
        "max_daily_loss_pct": 1.0,
        "max_consecutive_losses": 3,
        "max_open_positions": 1,
        "max_trades_per_day": 5,
        "stop_loss_method": "ATR",
        "stop_loss_parameters": {"atr_period": 14, "atr_multiplier": 1.2},
        "take_profit_method": "risk_reward",
        "take_profit_parameters": {"risk_reward_ratio": 1.5},
    },
    "execution_filters": {
        "max_spread_pips": 1.2,
        "max_slippage_pips": 0.5,
        "minimum_atr_pips": 3.0,
        "news_blackout_minutes_before": 15,
        "news_blackout_minutes_after": 15,
    },
    "assumptions": ["EMA computed on completed candles", "London session only"],
    "failure_modes": ["Ranging markets", "Wide spreads during news"],
    "plain_english_explanation": (
        "This strategy buys pullbacks to the 20-EMA while the 40-EMA is rising and "
        "sells pullbacks while it falls, using ATR-based stops and a 1.5 risk-reward target. "
        "It is intended for trending, liquid sessions and is not expected to work in "
        "range-bound conditions."
    ),
    "confidence_notes": "This is a hypothesis requiring backtesting and paper-trading validation.",
}


def seed() -> None:
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == "demo@fxscalper.dev").first()
        if not user:
            user = User(
                email="demo@fxscalper.dev",
                hashed_password=pwd_context.hash("demo-password"),
            )
            db.add(user)
            db.commit()
            db.refresh(user)

        ws = db.query(Workspace).filter(Workspace.owner_id == user.id).first()
        if not ws:
            ws = Workspace(name="Default Workspace", owner_id=user.id)
            db.add(ws)
            db.commit()
            db.refresh(ws)

        # Symbols
        symbols = [
            ("EURUSD", "EURUSD", "EUR", "USD", 4),
            ("GBPUSD", "GBPUSD", "GBP", "USD", 4),
            ("USDJPY", "USDJPY", "USD", "JPY", 2),
            ("AUDUSD", "AUDUSD.a", "AUD", "USD", 4),
            ("EURUSD", "EUR/USD", "EUR", "USD", 4),
            ("EURUSD", "EURUSDm", "EUR", "USD", 4),
        ]
        created = 0
        for canonical, provider_sym, base, quote, pip_pos in symbols:
            exists = (
                db.query(ForexSymbol)
                .filter(ForexSymbol.provider_symbol == provider_sym)
                .first()
            )
            if not exists:
                db.add(
                    ForexSymbol(
                        provider="mock",
                        canonical=canonical,
                        provider_symbol=provider_sym,
                        base_currency=base,
                        quote_currency=quote,
                        pip_position=pip_pos,
                        pip_value=0.01 if quote == "JPY" else 0.0001,
                        contract_size=100000.0,
                        is_active=True,
                    )
                )
                created += 1

        # Sample strategy
        spec = StrategySpec.model_validate(SAMPLE_SPEC)
        strategy = db.query(Strategy).filter(Strategy.name == SAMPLE_SPEC["name"]).first()
        if not strategy:
            strategy = Strategy(
                workspace_id=ws.id,
                name=SAMPLE_SPEC["name"],
                strategy_family=spec.strategy_family.value,
                current_version=spec.version,
                status="active",
                spec=spec.model_dump(mode="json"),
            )
            db.add(strategy)
            db.flush()
            db.add(
                StrategyVersion(
                    strategy_id=strategy.id,
                    version=spec.version,
                    spec=spec.model_dump(mode="json"),
                )
            )
            for kind, rules in (("entry", spec.entry_rules), ("exit", spec.exit_rules)):
                for rule in rules:
                    db.add(
                        StrategyRule(
                            strategy_id=strategy.id,
                            rule_id=rule.id,
                            rule_type=kind,
                            description=rule.description,
                            expression=rule.expression,
                            is_valid=True,
                            validation_errors=None,
                        )
                    )

        # Economic calendar events (next 30 days of synthetic high-impact events)
        events_created = seed_economic_events(db)

        db.commit()
        print(
            f"Seeded: user demo@fxscalper.dev, workspace {ws.id}, "
            f"{created} symbols, strategy {strategy.id}, {events_created} events"
        )
    finally:
        db.close()


def seed_economic_events(db) -> int:
    from datetime import datetime, timedelta, timezone

    from app.models import EconomicEvent

    now = datetime.now(timezone.utc)
    created = 0
    # Weekly repeating synthetic events per currency.
    n = 0
    for currency, country in [
        ("USD", "US"),
        ("EUR", "EU"),
        ("GBP", "GB"),
        ("JPY", "JP"),
        ("AUD", "AU"),
        ("NZD", "NZ"),
    ]:
        base = now + timedelta(days=n % 3)
        for week in range(4):
            for day_offset, name, impact in [
                (1, "Interest Rate Decision", "high"),
                (3, "GDP m/m", "high"),
                (4, "CPI y/y", "medium"),
                (2, "PMI Flash", "low"),
            ]:
                event_time = base + timedelta(days=week * 7 + day_offset, hours=11, minutes=30)
                if event_time.timestamp() < now.timestamp():
                    continue
                exists = (
                    db.query(EconomicEvent)
                    .filter(
                        EconomicEvent.currency == currency,
                        EconomicEvent.event_time == event_time.timestamp(),
                        EconomicEvent.name == name,
                    )
                    .first()
                )
                if exists:
                    continue
                db.add(
                    EconomicEvent(
                        country=country,
                        currency=currency,
                        name=name,
                        impact=impact,
                        event_time=event_time.timestamp(),
                        forecast=None,
                        previous=None,
                    )
                )
                created += 1
        n += 1
    return created


if __name__ == "__main__":
    seed()
