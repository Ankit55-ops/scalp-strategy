"""Paper trading simulation: internal account, live-ish signals on current data.

Orders are gated by the RiskEngine (kill switches, session/blackout, spread,
position limits, daily-loss, stop-distance). Approved orders open a
`PaperPosition` backed by a `SimulatedOrder`; closing marks both as closed and
credits/debits the account balance. All decisions are appended to the audit log
and surfaced as alerts.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.backtest.sessions import in_session, is_blackout
from app.models import (
    Alert,
    PaperAccount,
    PaperPosition,
    RiskEvent,
    RiskProfile,
    SimulatedFill,
    SimulatedOrder,
    Strategy,
)
from app.providers.factory import get_broker_provider, get_market_data_provider
from app.risk.engine import ProposedOrder, RiskEngine
from app.risk.killswitch import KillSwitchRegistry
from app.schemas.strategy import StrategySpec
from app.services.audit import AuditService
from app.services.market_math import pip_size


@dataclass
class OrderResult:
    approved: bool
    position: PaperPosition | None
    order: SimulatedOrder | None
    reason: str | None = None
    correlation_id: str | None = None


class PaperTradingService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.md = get_market_data_provider("mock")
        self.broker = get_broker_provider("simulated")

    # -- account -----------------------------------------------------------
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
        acc.started_at = datetime.now(timezone.utc).timestamp()
        self.db.commit()
        self.db.refresh(acc)
        return acc

    def stop(self, workspace_id: str, close_positions: bool = True) -> PaperAccount:
        acc = self.ensure_account(workspace_id)
        acc.is_active = False
        if close_positions:
            positions = self._open_positions(acc.id)
            for pos in positions:
                self._close_position_locked(acc.id, pos.id, reason="account_stop")
        self.db.commit()
        self.db.refresh(acc)
        return acc

    def status(self, workspace_id: str) -> dict:
        acc = self.ensure_account(workspace_id)
        open_count = self._open_positions(acc.id).count()
        closed_count = (
            self.db.query(SimulatedOrder)
            .filter(
                SimulatedOrder.paper_account_id == acc.id,
                SimulatedOrder.status == "closed",
            )
            .count()
        )
        return {
            "is_active": acc.is_active,
            "balance": round(acc.balance, 2),
            "equity": round(acc.equity, 2),
            "open_positions": open_count,
            "closed_trades": closed_count,
        }

    # -- positions ---------------------------------------------------------
    def _open_positions(self, account_id: str):
        return self.db.query(PaperPosition).filter(
            PaperPosition.account_id == account_id, PaperPosition.status == "open"
        )

    def open_positions(self, workspace_id: str) -> list[dict]:
        acc = self.ensure_account(workspace_id)
        out = []
        for pos in self._open_positions(acc.id).all():
            quote = self.md.get_latest_quote(pos.symbol)
            mark = quote["bid"] if pos.side == "long" else quote["ask"]
            if pos.side == "long":
                unrealized = (mark - pos.entry_price) * pos.size_units
            else:
                unrealized = (pos.entry_price - mark) * pos.size_units
            out.append(
                {
                    "id": pos.id,
                    "order_id": pos.order_id,
                    "symbol": pos.symbol,
                    "side": pos.side,
                    "size_units": round(pos.size_units, 2),
                    "entry_price": round(pos.entry_price, 5),
                    "mark_price": round(mark, 5),
                    "stop_loss": round(pos.stop_loss, 5),
                    "take_profit": round(pos.take_profit, 5),
                    "open_ts": pos.open_ts,
                    "unrealized_pnl": round(unrealized, 4),
                }
            )
        return out

    def closed_trades(self, workspace_id: str, limit: int = 100) -> list[dict]:
        acc = self.ensure_account(workspace_id)
        rows = (
            self.db.query(SimulatedOrder)
            .filter(
                SimulatedOrder.paper_account_id == acc.id,
                SimulatedOrder.status == "closed",
            )
            .order_by(SimulatedOrder.exit_ts.desc())
            .limit(limit)
            .all()
        )
        return [
            {
                "id": t.id,
                "strategy_id": t.strategy_id,
                "symbol": t.symbol,
                "side": t.side,
                "entry_ts": t.entry_ts,
                "exit_ts": t.exit_ts,
                "entry_price": t.entry_price,
                "exit_price": t.exit_price,
                "stop_loss": t.stop_loss,
                "take_profit": t.take_profit,
                "size_units": round(t.size_units, 2),
                "pips": round(t.pips, 2),
                "gross_pnl": round(t.gross_pnl, 4),
                "net_pnl": round(t.net_pnl, 4),
                "commission": round(t.commission, 4),
                "exit_reason": t.reasons_exit[0]["rule_id"] if t.reasons_exit else "n/a",
            }
            for t in rows
        ]

    def place_order(
        self,
        workspace_id: str,
        strategy_id: str,
        side: str,
        size_units: float | None = None,
        account_balance: float | None = None,
    ) -> OrderResult:
        acc = self.ensure_account(workspace_id)
        if not acc.is_active:
            return OrderResult(approved=False, position=None, order=None, reason="paper account not active")

        strategy = self.db.get(Strategy, strategy_id)
        if strategy is None or strategy.workspace_id != workspace_id:
            return OrderResult(approved=False, position=None, order=None, reason="strategy not found")
        spec = StrategySpec.model_validate(strategy.spec)
        symbol = spec.supported_pairs[0]
        side = "long" if side in ("long", "buy") else "short"

        quote = self.md.get_latest_quote(symbol)
        if side == "long":
            entry_price = quote["ask"]
        else:
            entry_price = quote["bid"]

        # Compute stop/target from the strategy risk parameters.
        atr_period = spec.risk_management.stop_loss_parameters.get("atr_period", 14)
        atr_val = self._atr(symbol, atr_period)
        stop_dist = self._stop_dist(spec, atr_val)
        target_dist = self._target_dist(spec, stop_dist)
        if side == "long":
            stop, target = entry_price - stop_dist, entry_price + target_dist
        else:
            stop, target = entry_price + stop_dist, entry_price - target_dist

        balance = account_balance if account_balance is not None else acc.balance
        if size_units is None:
            risk_amount = balance * (spec.risk_management.risk_per_trade_pct / 100.0)
            risked_per_unit = abs(entry_price - stop)
            size_units = risk_amount / risked_per_unit if risked_per_unit > 0 else 0.0
        if size_units <= 0:
            return OrderResult(approved=False, position=None, order=None, reason="invalid size")

        now_ts = datetime.now(timezone.utc).timestamp()
        events = self._economic_events()
        profile = self._active_profile(workspace_id)
        engine = RiskEngine(killswitch=KillSwitchRegistry(), profile=profile)
        engine._open_positions = self._open_positions(acc.id).count()

        order = ProposedOrder(
            symbol=symbol,
            side="buy" if side == "long" else "sell",
            size_units=size_units,
            entry_price=entry_price,
            stop_price=stop,
            account_balance=balance,
            account_equity=acc.equity,
            spread_pips=self.md.get_spread(symbol),
            ts=now_ts,
            is_blackout=is_blackout(
                now_ts,
                events,
                spec.execution_filters.news_blackout_minutes_before,
                spec.execution_filters.news_blackout_minutes_after,
            ),
            in_session=in_session(now_ts, spec.sessions_utc),
            strategy_id=strategy.id,
            strategy_version=strategy.current_version or spec.version,
        )
        decision = engine.evaluate(order)
        if decision.approved:
            broker_resp = self.broker.submit_order(
                {
                    "symbol": symbol,
                    "side": side,
                    "size_units": size_units,
                    "entry_price": entry_price,
                    "stop_loss": stop,
                    "take_profit": target,
                }
            )
            trade = SimulatedOrder(
                paper_account_id=acc.id,
                strategy_id=strategy.id,
                idempotency_key=None,
                symbol=symbol,
                timeframe=spec.supported_timeframes[0],
                side="buy" if side == "long" else "sell",
                order_type="market",
                entry_ts=now_ts,
                entry_price=entry_price,
                stop_loss=stop,
                take_profit=target,
                size_units=size_units,
                risk_amount=balance * (spec.risk_management.risk_per_trade_pct / 100.0),
                status="open",
            )
            self.db.add(trade)
            self.db.flush()
            self.db.add(
                SimulatedFill(
                    order_id=trade.id,
                    ts=now_ts,
                    price=entry_price,
                    volume=size_units,
                    side="buy" if side == "long" else "sell",
                    fill_type="entry",
                )
            )
            pos = PaperPosition(
                account_id=acc.id,
                strategy_id=strategy.id,
                order_id=trade.id,
                symbol=symbol,
                side=side,
                size_units=size_units,
                entry_price=entry_price,
                stop_loss=stop,
                take_profit=target,
                open_ts=now_ts,
                status="open",
            )
            self.db.add(pos)
            self._log_decision(workspace_id, decision, order)
            self.db.commit()
            self.db.refresh(trade)
            self.db.refresh(pos)
            return OrderResult(approved=True, position=pos, order=trade)

        self._log_decision(workspace_id, decision, order)
        # No position/order rows were created; commit persists the risk alert.
        self.db.commit()
        return OrderResult(
            approved=False,
            position=None,
            order=None,
            reason=decision.rejection_reason,
            correlation_id=decision.correlation_id,
        )

    def close_position(self, workspace_id: str, position_id: str, reason: str = "manual_close") -> PaperPosition:
        acc = self.ensure_account(workspace_id)
        return self._close_position_locked(acc.id, position_id, reason=reason)

    def _close_position_locked(self, account_id: str, position_id: str, reason: str) -> PaperPosition:
        pos = self.db.get(PaperPosition, position_id)
        if pos is None or pos.account_id != account_id or pos.status != "open":
            raise ValueError("position not found or already closed")

        quote = self.md.get_latest_quote(pos.symbol)
        exit_price = quote["bid"] if pos.side == "long" else quote["ask"]
        if pos.side == "long":
            gross = (exit_price - pos.entry_price) * pos.size_units
        else:
            gross = (pos.entry_price - exit_price) * pos.size_units
        acc = self.db.get(PaperAccount, account_id)
        pip = pip_size("JPY" if pos.symbol.upper().endswith("JPY") else "USD")
        pips = (exit_price - pos.entry_price) / pip
        if pos.side == "short":
            pips = (pos.entry_price - exit_price) / pip

        # Simple cost model on the closing leg (spread + slippage).
        spread_cost = self.md.get_spread(pos.symbol) * pip * pos.size_units
        slippage_cost = 0.3 * pip * pos.size_units
        net = gross - spread_cost - slippage_cost

        now_ts = datetime.now(timezone.utc).timestamp()
        pos.exit_ts = now_ts
        pos.exit_price = exit_price
        pos.gross_pnl = gross
        pos.net_pnl = net
        pos.pips = pips
        pos.exit_reason = reason
        pos.status = "closed"

        if pos.order_id:
            trade = self.db.get(SimulatedOrder, pos.order_id)
            if trade is not None and trade.status == "open":
                trade.status = "closed"
                trade.exit_ts = now_ts
                trade.exit_price = exit_price
                trade.pips = pips
                trade.gross_pnl = gross
                trade.net_pnl = net
                trade.spread_cost = spread_cost
                trade.slippage_cost = slippage_cost
                trade.reasons_exit = [{"rule_id": reason, "description": reason}]
                self.db.add(
                    SimulatedFill(
                        order_id=trade.id,
                        ts=now_ts,
                        price=exit_price,
                        volume=pos.size_units,
                        side="sell" if pos.side == "long" else "buy",
                        fill_type="exit",
                    )
                )

        acc.balance += net
        acc.equity = acc.balance
        self.db.commit()
        self.db.refresh(pos)
        return pos

    # -- risk helpers ------------------------------------------------------
    def _active_profile(self, workspace_id: str) -> RiskProfile | None:
        profile = (
            self.db.query(RiskProfile)
            .filter(RiskProfile.workspace_id == workspace_id, RiskProfile.is_active.is_(True))
            .order_by(RiskProfile.created_at.desc())
            .first()
        )
        return profile

    def _stop_dist(self, spec: StrategySpec, atr_val: float) -> float:
        method = spec.risk_management.stop_loss_method
        params = spec.risk_management.stop_loss_parameters
        if method == "FIXED":
            return float(params.get("fixed_distance_pips", 10.0)) * pip_size(
                "JPY" if spec.supported_pairs[0].upper().endswith("JPY") else "USD"
            )
        mult = float(params.get("atr_multiplier", 1.2))
        return atr_val * mult

    def _target_dist(self, spec: StrategySpec, stop_dist: float) -> float:
        method = spec.risk_management.take_profit_method
        params = spec.risk_management.take_profit_parameters
        if method == "FIXED":
            return float(params.get("fixed_distance_pips", 10.0)) * pip_size(
                "JPY" if spec.supported_pairs[0].upper().endswith("JPY") else "USD"
            )
        rr = float(params.get("risk_reward_ratio", 1.5))
        return stop_dist * rr

    def _atr(self, symbol: str, period: int = 14) -> float:
        from app.backtest.indicators import add_indicators
        from app.services.market_math import pip_size

        try:
            import pandas as pd

            end = datetime.now(timezone.utc)
            start = end - timedelta(days=7)
            candles = self.md.get_historical_candles(symbol, "M5", start, end)
            df = add_indicators(pd.DataFrame(candles), [{"name": "ATR", "parameters": {"period": period}}])
            val = float(df[f"ATR{period}"].dropna().iloc[-1])
            if val > 0:
                return val
        except Exception:  # noqa: BLE001
            pass
        pip = pip_size("JPY" if symbol.upper().endswith("JPY") else "USD")
        return pip * 20.0

    def _economic_events(self) -> list[dict]:
        from datetime import datetime, timezone

        from app.models import EconomicEvent

        now = datetime.now(timezone.utc).timestamp()
        rows = (
            self.db.query(EconomicEvent)
            .filter(EconomicEvent.event_time >= now)
            .order_by(EconomicEvent.event_time.asc())
            .limit(50)
            .all()
        )
        return [
            {
                "time": e.event_time,
                "impact": e.impact,
                "currency": e.currency,
                "name": e.name,
            }
            for e in rows
        ]

    def _log_decision(self, workspace_id: str, decision, order: ProposedOrder) -> None:
        AuditService(self.db).record_risk_decision(workspace_id, decision.as_audit_dict(order))
        if not decision.approved:
            self.db.add(
                Alert(
                    workspace_id=workspace_id or order.strategy_id or "",
                    level="warning",
                    title="Order rejected by risk engine",
                    message=f"{order.symbol} {order.side}: {decision.rejection_reason}",
                )
            )
            self.db.add(
                RiskEvent(
                    workspace_id=workspace_id,
                    strategy_id=order.strategy_id,
                    event_type="order_rejected",
                    severity="warning",
                    symbol=order.symbol,
                    details={
                        "reason": decision.rejection_reason,
                        "correlation_id": decision.correlation_id,
                    },
                )
            )