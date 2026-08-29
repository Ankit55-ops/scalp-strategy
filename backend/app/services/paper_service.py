"""Paper trading simulation: internal account, live-ish signals on current data."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import PaperAccount, PaperPosition, Strategy
from app.providers.factory import get_broker_provider, get_market_data_provider
from app.schemas.strategy import StrategySpec


class PaperTradingService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.md = get_market_data_provider("mock")
        self.broker = get_broker_provider("simulated")

    def ensure_account(self, workspace_id: str) -> PaperAccount:
        acc = (
            self.db.query(PaperAccount)
            .filter(PaperAccount.workspace_id == workspace_id)
            .first()
        )
        if acc is None:
            acc = PaperAccount(workspace_id=workspace_id, balance=100000.0, equity=100000.0)
            self.db.add(acc)
            self.db.commit()
            self.db.refresh(acc)
        return acc

    def start(self, workspace_id: str, balance: float = 100000.0) -> PaperAccount:
        acc = self.ensure_account(workspace_id)
        acc.balance = balance
        acc.equity = balance
        acc.is_active = True
        acc.started_at = 0.0
        self.db.commit()
        self.db.refresh(acc)
        return acc

    def stop(self, workspace_id: str, close_positions: bool = True) -> PaperAccount:
        acc = self.ensure_account(workspace_id)
        acc.is_active = False
        if close_positions:
            positions = (
                self.db.query(PaperPosition)
                .filter(PaperPosition.account_id == acc.id, PaperPosition.status == "open")
                .all()
            )
            for pos in positions:
                # Mark closed at current quote (mock).
                quote = self.md.get_latest_quote(pos.symbol)
                px = quote["bid"] if pos.side == "long" else quote["ask"]
                if pos.side == "long":
                    pnl = (px - pos.entry_price) * pos.size_units
                else:
                    pnl = (pos.entry_price - px) * pos.size_units
                acc.balance += pnl
                pos.status = "closed"
        acc.equity = acc.balance
        self.db.commit()
        return acc

    def status(self, workspace_id: str) -> dict:
        acc = self.ensure_account(workspace_id)
        open_count = (
            self.db.query(PaperPosition)
            .filter(PaperPosition.account_id == acc.id, PaperPosition.status == "open")
            .count()
        )
        return {
            "is_active": acc.is_active,
            "balance": round(acc.balance, 2),
            "equity": round(acc.equity, 2),
            "open_positions": open_count,
            "closed_trades": 0,
        }

    def evaluate_strategy(self, strategy: Strategy, timeframe: str = "M5") -> dict:
        """Return a signal for current data using the mock provider."""
        from datetime import datetime, timedelta, timezone

        from app.backtest.backtester import Backtester
        from app.backtest.cost import CostParams

        spec = StrategySpec.model_validate(strategy.spec)
        end = datetime.now(timezone.utc)
        start = end - timedelta(days=7)
        symbol = spec.supported_pairs[0]
        candles = self.md.get_historical_candles(symbol, timeframe, start, end)
        if not candles:
            return {"signal": "none", "reason": "no data"}
        jpy = symbol.upper().endswith("JPY")
        cost = CostParams(
            spread_pips=spec.execution_filters.max_spread_pips,
            commission_per_lot=0.0,
            slippage_pips=spec.execution_filters.max_slippage_pips,
            pip_size=0.01 if jpy else 0.0001,
        )
        bt = Backtester(spec, cost, risk_engine=None)
        out = bt.run(candles, symbol, timeframe, starting_balance=100000.0)
        if not out["trades"]:
            return {"signal": "none", "reason": "no recent trades"}
        last = out["trades"][-1]
        return {
            "signal": last["side"],
            "symbol": symbol,
            "price": last["entry_price"],
            "stop": last["stop"],
            "target": last["target"],
        }
