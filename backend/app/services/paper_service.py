"""Paper trading simulation: internal account, live-ish signals on current data.

Orders are gated by the RiskEngine (kill switches, session/blackout, spread,
position limits, daily-loss, stop-distance). Approved orders open a
`PaperPosition` backed by a `SimulatedOrder`; closing marks both as closed and
credits/debits the account balance. All decisions are appended to the audit log
and surfaced as alerts.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.backtest.sessions import in_session, is_blackout
from app.core.config import get_settings
from app.models import (
    Alert,
    PaperAccount,
    PaperFill,
    PaperMarginEvent,
    PaperOrder,
    PaperPosition,
    RiskEvent,
    RiskProfile,
    SimulatedFill,
    SimulatedOrder,
    Strategy,
)
from app.providers.factory import get_broker_provider
from app.risk.engine import ProposedOrder, RiskEngine
from app.risk.killswitch import KillSwitchRegistry
from app.schemas.strategy import StrategySpec
from app.services import feed_health
from app.services.audit import AuditService
from app.services.market_math import pip_size
from app.services.money import add as money_add
from app.services.money import sub as money_sub
from app.services.paper_broker import PaperBroker
from app.services.provider_service import get_active_provider


@dataclass
class OrderResult:
    approved: bool
    position: PaperPosition | None
    order: SimulatedOrder | None
    reason: str | None = None
    correlation_id: str | None = None


# (account_id, idempotency_key) -> closed position_id. Best-effort memo used so a
# duplicated position-close request returns the already-closed result instead of
# failing with "already closed". Single event loop / single process today.
_CLOSE_MEMO: dict[tuple[str, str], str] = {}


class PaperTradingService:
    def __init__(self, db: Session, slippage_pips: float = 0.1) -> None:
        self.db = db
        self.broker = get_broker_provider("simulated")
        self.paper_broker = PaperBroker(slippage_pips=slippage_pips)

    def _md(self, workspace_id: str):
        return get_active_provider(self.db, workspace_id)

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

    def _account_locked(self, workspace_id: str) -> PaperAccount:
        """Fetch the account with SELECT ... FOR UPDATE so concurrent order
        placement and position closing serialize on the same row."""
        acc = self.db.execute(
            select(PaperAccount)
            .where(PaperAccount.workspace_id == workspace_id)
            .with_for_update()
        ).scalar_one_or_none()
        if acc is None:
            acc = PaperAccount(workspace_id=workspace_id, balance=100000.0, equity=100000.0)
            self.db.add(acc)
            self.db.flush()
            acc = self.db.execute(
                select(PaperAccount)
                .where(PaperAccount.workspace_id == workspace_id)
                .with_for_update()
            ).scalar_one()
        return acc

    def start(self, workspace_id: str, balance: float = 100000.0) -> PaperAccount:
        settings = get_settings()
        if not math.isfinite(balance) or not (
            settings.PAPER_MIN_BALANCE <= balance <= settings.PAPER_MAX_BALANCE
        ):
            raise ValueError(
                f"balance must be a finite number between {settings.PAPER_MIN_BALANCE:,.0f} "
                f"and {settings.PAPER_MAX_BALANCE:,.0f}"
            )
        acc = self._account_locked(workspace_id)
        acc.balance = balance
        acc.equity = balance
        acc.is_active = True
        acc.trading_state = "ACTIVE"
        acc.state_reason = None
        acc.started_at = datetime.now(timezone.utc).timestamp()
        self.db.commit()
        self.db.refresh(acc)
        return acc

    def stop(self, workspace_id: str, close_positions: bool = True) -> PaperAccount:
        acc = self.ensure_account(workspace_id)
        acc.is_active = False
        acc.trading_state = "INACTIVE"
        acc.state_reason = "paper trading stopped"
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
        state, reason = self.evaluate_trading_state(acc)
        acc.trading_state = state
        acc.state_reason = reason
        self.db.commit()
        return {
            "is_active": acc.is_active,
            "balance": round(acc.balance, 2),
            "equity": round(acc.equity, 2),
            "open_positions": open_count,
            "closed_trades": closed_count,
            "trading_state": state,
            "state_reason": reason,
            "pending_orders": round(self._pending_order_count(acc.id), 0),
        }

    # -- account state machine -------------------------------------------
    def evaluate_trading_state(self, acc: PaperAccount) -> tuple[str, str | None]:
        """Resolve the paper-account state: ACTIVE | INACTIVE | RISK_PAUSED | DATA_PAUSED | KILL_SWITCHED."""
        if not acc.is_active:
            return "INACTIVE", "paper trading stopped"
        try:
            ks = KillSwitchRegistry(db=self.db, workspace_id=acc.workspace_id)
            if ks.is_global_halted():
                return "KILL_SWITCHED", "global kill switch is on"
        except Exception:  # noqa: BLE001
            pass
        try:
            stale = feed_health.stale_supported_symbols(self.db, acc.workspace_id)
            involved = {p.symbol for p in self._open_positions(acc.id).all()}
            involved |= self._active_strategy_symbols(acc.workspace_id)
            if stale & involved:
                return "DATA_PAUSED", f"stale or disconnected feed for {', '.join(sorted(stale & involved))}"
        except Exception:  # noqa: BLE001
            pass
        profile = self._active_profile(acc.workspace_id)
        if profile is not None:
            try:
                now_ts = datetime.now(timezone.utc).timestamp()
                self._rollover_account(acc, now_ts)
                equity = self._equity(acc)
                peak = acc.equity_peak or equity
                drawdown_pct = ((peak - equity) / peak * 100.0) if peak > 0 else 0.0
                day_loss_pct = self._loss_pct(acc.day_start_equity, equity)
                week_loss_pct = self._loss_pct(acc.week_start_equity, equity)
                cons = self._consecutive_losses(acc.id)
                if day_loss_pct >= profile.max_daily_loss_pct:
                    return "RISK_PAUSED", f"daily loss limit reached ({day_loss_pct:.2f}% >= {profile.max_daily_loss_pct}%)"
                if week_loss_pct >= profile.max_weekly_loss_pct:
                    return "RISK_PAUSED", f"weekly loss limit reached ({week_loss_pct:.2f}% >= {profile.max_weekly_loss_pct}%)"
                if drawdown_pct >= profile.max_drawdown_pct:
                    return "RISK_PAUSED", f"max drawdown reached ({drawdown_pct:.2f}% >= {profile.max_drawdown_pct}%)"
                if cons >= int(profile.max_consecutive_losses or 0):
                    return "RISK_PAUSED", f"max consecutive losses reached ({cons} >= {int(profile.max_consecutive_losses or 0)})"
            except Exception:  # noqa: BLE001
                pass
        return "ACTIVE", None

    def _loss_pct(self, start_equity: float | None, equity: float) -> float:
        if not start_equity:
            return 0.0
        return max(0.0, (start_equity - equity) / start_equity * 100.0)

    def _active_strategy_symbols(self, workspace_id: str) -> set[str]:
        syms: set[str] = set()
        rows = self.db.query(Strategy).filter(Strategy.workspace_id == workspace_id, Strategy.status == "active").all()
        for s in rows:
            try:
                syms.add(StrategySpec.model_validate(s.spec).supported_pairs[0].upper())
            except Exception:  # noqa: BLE001
                continue
        return syms

    def _pending_order_count(self, account_id: str) -> int:
        return (
            self.db.query(PaperOrder)
            .filter(PaperOrder.account_id == account_id, PaperOrder.status == "PENDING")
            .count()
        )

    def _record_margin_event(self, acc: PaperAccount, event_type: str, detail: str | None, meta: dict | None = None) -> None:
        now_ts = datetime.now(timezone.utc).timestamp()
        peak = acc.equity_peak or acc.equity
        drawdown_pct = ((peak - acc.equity) / peak * 100.0) if peak > 0 else 0.0
        self.db.add(
            PaperMarginEvent(
                account_id=acc.id,
                ts=now_ts,
                event_type=event_type,
                detail=detail,
                balance=round(acc.balance, 4),
                equity=round(acc.equity, 4),
                drawdown_pct=round(drawdown_pct, 4),
                trading_state=acc.trading_state,
                meta=meta,
            )
        )

    # -- positions ---------------------------------------------------------
    def _open_positions(self, account_id: str):
        return self.db.query(PaperPosition).filter(
            PaperPosition.account_id == account_id, PaperPosition.status == "open"
        )

    def open_positions(self, workspace_id: str) -> list[dict]:
        acc = self.ensure_account(workspace_id)
        md = self._md(workspace_id)
        out = []
        for pos in self._open_positions(acc.id).all():
            quote = md.get_latest_quote(pos.symbol)
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
                    "mode": "PAPER",
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
        idempotency_key: str | None = None,
    ) -> OrderResult:
        acc = self._account_locked(workspace_id)
        if not acc.is_active:
            return OrderResult(approved=False, position=None, order=None, reason="paper account not active")

        # Idempotent replay: if this client already submitted this order (same
        # workspace + idempotency key), return the existing result instead of
        # opening a second position. The account row lock above serializes
        # concurrent identical submissions.
        if idempotency_key:
            existing = (
                self.db.query(SimulatedOrder)
                .filter(
                    SimulatedOrder.paper_account_id == acc.id,
                    SimulatedOrder.idempotency_key == idempotency_key,
                )
                .first()
            )
            if existing is not None:
                pos = (
                    self.db.query(PaperPosition)
                    .filter(PaperPosition.order_id == existing.id)
                    .first()
                )
                return OrderResult(
                    approved=True,
                    position=pos,
                    order=existing,
                    correlation_id="idempotent-replay",
                )

        state, state_reason = self.evaluate_trading_state(acc)
        if state != "ACTIVE":
            return OrderResult(
                approved=False,
                position=None,
                order=None,
                reason=f"paper account is {state}: {state_reason}" if state_reason else f"paper account is {state}",
            )

        strategy = self.db.get(Strategy, strategy_id)
        if strategy is None or strategy.workspace_id != workspace_id:
            return OrderResult(approved=False, position=None, order=None, reason="strategy not found")
        spec = StrategySpec.model_validate(strategy.spec)
        symbol = spec.supported_pairs[0]
        side = "long" if side in ("long", "buy") else "short"

        quote = feed_health.get_quote(self.db, workspace_id, symbol)
        if quote.get("is_stale") or quote.get("feed_state") in ("STALE", "DISCONNECTED", "CONNECTING"):
            return OrderResult(
                approved=False,
                position=None,
                order=None,
                reason=f"{symbol} market data is not fresh (feed={quote.get('feed_state')}); order blocked",
            )
        if quote.get("market_status") not in ("open", "unknown"):
            return OrderResult(
                approved=False,
                position=None,
                order=None,
                reason=f"market closed ({quote.get('market_status')}); paper orders paused",
            )

        entry_price = self.paper_broker.entry_price(quote, side)

        # Compute stop/target from the strategy risk parameters.
        atr_period = spec.risk_management.stop_loss_parameters.get("atr_period", 14)
        atr_val = self._atr(workspace_id, symbol, atr_period)
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
        if not math.isfinite(size_units):
            return OrderResult(approved=False, position=None, order=None, reason="invalid size")
        settings = get_settings()
        max_notional = acc.balance * settings.PAPER_MAX_LEVERAGE
        if size_units * entry_price > max_notional:
            return OrderResult(
                approved=False,
                position=None,
                order=None,
                reason=(
                    f"position notional {size_units * entry_price:.2f} exceeds "
                    f"{settings.PAPER_MAX_LEVERAGE:g}x leverage cap "
                    f"({max_notional:.2f})"
                ),
            )

        now_ts = datetime.now(timezone.utc).timestamp()
        events = self._economic_events()
        profile = self._active_profile(workspace_id)
        self._rollover_account(acc, now_ts)
        self.db.flush()
        engine = RiskEngine(
            killswitch=KillSwitchRegistry(db=self.db, workspace_id=workspace_id),
            profile=profile,
        )
        engine._open_positions = self._open_positions(acc.id).count()
        engine._trades_today = self._trades_today(acc.id, now_ts)
        engine._trades_session = 0
        engine._consecutive_losses = self._consecutive_losses(acc.id)
        engine._day_start_equity = acc.day_start_equity
        engine._week_start_equity = acc.week_start_equity
        engine._peak_equity = acc.equity_peak
        engine._day = acc.day_key
        engine._week = acc.week_key

        order = ProposedOrder(
            symbol=symbol,
            side="buy" if side == "long" else "sell",
            size_units=size_units,
            entry_price=entry_price,
            stop_price=stop,
            account_balance=balance,
            account_equity=self._equity(acc),
            spread_pips=quote.get("spread_pips", self._md(workspace_id).get_spread(symbol)),
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
                idempotency_key=idempotency_key,
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
            po = PaperOrder(
                account_id=acc.id,
                position_id=pos.id,
                trade_id=trade.id,
                strategy_id=strategy.id,
                symbol=symbol,
                side="buy" if side == "long" else "sell",
                order_type="market",
                status="FILLED",
                size_units=size_units,
                stop_loss=stop,
                take_profit=target,
                request_ts=now_ts,
                approval_ts=now_ts,
                fill_ts=now_ts,
                fill_price=entry_price,
                fill_side="entry",
                meta={"basis": quote.get("bid_ask_basis"), "provider": quote.get("provider") or self._md(workspace_id).name},
            )
            self.db.add(po)
            self.db.flush()
            pf = PaperFill(
                account_id=acc.id,
                order_id=po.id,
                position_id=pos.id,
                trade_id=trade.id,
                ts=now_ts,
                price=entry_price,
                volume=size_units,
                side="buy" if side == "long" else "sell",
                fill_type="entry",
                bid_ask_basis=quote.get("bid_ask_basis", "mid"),
                provider=quote.get("provider") or self._md(workspace_id).name,
            )
            self.db.add(pf)
            self._record_margin_event(acc, "position_opened", f"opened {side} {symbol} {round(size_units, 2)}u @ {entry_price}")
            self._log_decision(workspace_id, decision, order)
            self.db.commit()
            self.db.refresh(trade)
            self.db.refresh(pos)
            return OrderResult(approved=True, position=pos, order=trade)

        self._log_decision(workspace_id, decision, order)
        # No position/order rows were created; commit persists the risk alert.
        self.db.add(
            PaperOrder(
                account_id=acc.id,
                strategy_id=strategy.id,
                symbol=symbol,
                side="buy" if side == "long" else "sell",
                order_type="market",
                status="REJECTED",
                size_units=size_units,
                stop_loss=stop,
                take_profit=target,
                request_ts=now_ts,
                rejection_reason=decision.rejection_reason,
                meta={"correlation_id": decision.correlation_id, "basis": quote.get("bid_ask_basis")},
            )
        )
        self.db.commit()
        return OrderResult(
            approved=False,
            position=None,
            order=None,
            reason=decision.rejection_reason,
            correlation_id=decision.correlation_id,
        )

    def close_position(
        self,
        workspace_id: str,
        position_id: str,
        reason: str = "manual_close",
        idempotency_key: str | None = None,
    ) -> PaperPosition:
        acc = self.ensure_account(workspace_id)
        # Idempotent replay: return the already-closed position for a duplicate
        # close request keyed by (account, idempotency_key) instead of failing
        # with "already closed". Belt-and-suspenders on top of the row lock.
        if idempotency_key and (acc.id, idempotency_key) in _CLOSE_MEMO:
            memo_pos_id = _CLOSE_MEMO[(acc.id, idempotency_key)]
            existing = self.db.get(PaperPosition, memo_pos_id)
            if existing is not None:
                return existing
        pos = self._close_position_locked(acc.id, position_id, reason=reason)
        if idempotency_key:
            _CLOSE_MEMO[(acc.id, idempotency_key)] = pos.id
        return pos

    def _close_position_locked(self, account_id: str, position_id: str, reason: str) -> PaperPosition:
        # Serialize concurrent closes by locking the account row first, then the
        # position row, so a double-close can never credit the balance twice.
        acc = self.db.execute(
            select(PaperAccount).where(PaperAccount.id == account_id).with_for_update()
        ).scalar_one_or_none()
        if acc is None:
            raise ValueError("account not found")
        pos = self.db.execute(
            select(PaperPosition).where(PaperPosition.id == position_id).with_for_update()
        ).scalar_one_or_none()
        if pos is None or pos.account_id != account_id or pos.status != "open":
            raise ValueError("position not found or already closed")

        quote = feed_health.get_quote(self.db, acc.workspace_id, pos.symbol)
        exit_price = self.paper_broker.exit_price(quote, pos.side)
        gross = self.paper_broker.gross_pnl(pos.side, pos.entry_price, exit_price, pos.size_units)
        pip = pip_size("JPY" if pos.symbol.upper().endswith("JPY") else "USD")
        pips = self.paper_broker.pips(pos.side, pos.entry_price, exit_price, pip)
        costs = self.paper_broker.costs(quote, pos.side, pos.size_units)
        net = money_sub(gross, costs.total)

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
                trade.spread_cost = costs.spread_cost
                trade.slippage_cost = costs.slippage_cost
                trade.commission = costs.commission
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

        acc.balance = money_add(acc.balance, net)
        acc.equity = acc.balance
        # Execution ledger: exit order + fill + margin event.
        if pos.order_id:
            po = PaperOrder(
                account_id=acc.id,
                position_id=pos.id,
                trade_id=pos.order_id,
                strategy_id=pos.strategy_id,
                symbol=pos.symbol,
                side="sell" if pos.side == "long" else "buy",
                order_type="market",
                status="FILLED",
                size_units=pos.size_units,
                stop_loss=pos.stop_loss,
                take_profit=pos.take_profit,
                request_ts=now_ts,
                approval_ts=now_ts,
                fill_ts=now_ts,
                fill_price=exit_price,
                fill_side="exit",
                meta={"basis": quote.get("bid_ask_basis"), "provider": quote.get("provider"), "reason": reason},
            )
            self.db.add(po)
            self.db.flush()
            self.db.add(
                PaperFill(
                    account_id=acc.id,
                    order_id=po.id,
                    position_id=pos.id,
                    trade_id=pos.order_id,
                    ts=now_ts,
                    price=exit_price,
                    volume=pos.size_units,
                    side="sell" if pos.side == "long" else "buy",
                    fill_type="exit",
                    spread_cost=round(costs.spread_cost, 4),
                    slippage_cost=round(costs.slippage_cost, 4),
                    commission=round(costs.commission, 4),
                    bid_ask_basis=quote.get("bid_ask_basis", "mid"),
                    provider=quote.get("provider") or "mock",
                )
            )
        state, reason = self.evaluate_trading_state(acc)
        acc.trading_state = state
        acc.state_reason = reason
        self._record_margin_event(
            acc,
            "position_closed",
            f"closed {pos.side} {pos.symbol} net {round(net, 4)} ({reason})",
        )
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

    def _equity(self, acc: PaperAccount) -> float:
        """Mark-to-market equity: cash balance plus unrealized P&L on open positions."""
        md = self._md(acc.workspace_id)
        unrealized = 0.0
        for pos in self._open_positions(acc.id).all():
            quote = md.get_latest_quote(pos.symbol)
            mark = quote["bid"] if pos.side == "long" else quote["ask"]
            raw = (mark - pos.entry_price) * pos.size_units
            if pos.side != "long":
                raw = -raw
            unrealized = money_add(unrealized, raw)
        return money_add(acc.balance, unrealized)

    def _rollover_account(self, acc: PaperAccount, ts: float) -> None:
        """Roll day/week start equity and track the equity peak for drawdown gating."""
        dt = datetime.fromtimestamp(ts, timezone.utc)
        day = dt.strftime("%Y-%m-%d")
        week = dt.strftime("%G-W%V")
        equity = self._equity(acc)
        if acc.day_key != day:
            acc.day_key = day
            acc.day_start_equity = equity
        if acc.week_key != week:
            acc.week_key = week
            acc.week_start_equity = equity
        if acc.equity_peak is None or equity > acc.equity_peak:
            acc.equity_peak = equity

    def _trades_today(self, account_id: str, ts: float) -> int:
        start = (
            datetime.fromtimestamp(ts, timezone.utc)
            .replace(hour=0, minute=0, second=0, microsecond=0)
            .timestamp()
        )
        return (
            self.db.query(SimulatedOrder)
            .filter(SimulatedOrder.paper_account_id == account_id, SimulatedOrder.entry_ts >= start)
            .count()
        )

    def _consecutive_losses(self, account_id: str) -> int:
        rows = (
            self.db.query(SimulatedOrder)
            .filter(
                SimulatedOrder.paper_account_id == account_id,
                SimulatedOrder.status == "closed",
            )
            .order_by(SimulatedOrder.exit_ts.desc())
            .limit(50)
            .all()
        )
        count = 0
        for row in rows:
            if (row.net_pnl or 0.0) < 0:
                count += 1
            else:
                break
        return count

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

    def _atr(self, workspace_id: str, symbol: str, period: int = 14) -> float:
        from app.backtest.indicators import add_indicators
        from app.services.market_math import pip_size

        md = self._md(workspace_id)
        try:
            import pandas as pd

            end = datetime.now(timezone.utc)
            start = end - timedelta(days=7)
            candles = md.get_historical_candles(symbol, "M5", start, end)
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
                    workspace_id=workspace_id,
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

    # -- paper execution ledger (orders / fills / margin events) ----------
    def paper_orders(self, workspace_id: str, limit: int = 100, status: str | None = None) -> list[dict]:
        acc = self.ensure_account(workspace_id)
        q = self.db.query(PaperOrder).filter(PaperOrder.account_id == acc.id)
        if status:
            q = q.filter(PaperOrder.status == status.upper())
        rows = q.order_by(PaperOrder.created_at.desc()).limit(limit).all()
        return [
            {
                "id": o.id,
                "position_id": o.position_id,
                "trade_id": o.trade_id,
                "strategy_id": o.strategy_id,
                "symbol": o.symbol,
                "side": o.side,
                "order_type": o.order_type,
                "status": o.status,
                "size_units": round(o.size_units, 2),
                "stop_loss": round(o.stop_loss, 5) if o.stop_loss else None,
                "take_profit": round(o.take_profit, 5) if o.take_profit else None,
                "request_ts": o.request_ts,
                "approval_ts": o.approval_ts,
                "fill_ts": o.fill_ts,
                "fill_price": round(o.fill_price, 5) if o.fill_price else None,
                "fill_side": o.fill_side,
                "rejection_reason": o.rejection_reason,
                "meta": o.meta,
            }
            for o in rows
        ]

    def paper_fills(self, workspace_id: str, limit: int = 100) -> list[dict]:
        acc = self.ensure_account(workspace_id)
        rows = (
            self.db.query(PaperFill)
            .filter(PaperFill.account_id == acc.id)
            .order_by(PaperFill.created_at.desc())
            .limit(limit)
            .all()
        )
        return [
            {
                "id": f.id,
                "order_id": f.order_id,
                "position_id": f.position_id,
                "trade_id": f.trade_id,
                "ts": f.ts,
                "price": round(f.price, 5),
                "volume": round(f.volume, 2),
                "side": f.side,
                "fill_type": f.fill_type,
                "spread_cost": round(f.spread_cost, 4),
                "slippage_cost": round(f.slippage_cost, 4),
                "commission": round(f.commission, 4),
                "bid_ask_basis": f.bid_ask_basis,
                "provider": f.provider,
            }
            for f in rows
        ]

    def margin_events(self, workspace_id: str, limit: int = 100) -> list[dict]:
        acc = self.ensure_account(workspace_id)
        rows = (
            self.db.query(PaperMarginEvent)
            .filter(PaperMarginEvent.account_id == acc.id)
            .order_by(PaperMarginEvent.created_at.desc())
            .limit(limit)
            .all()
        )
        return [
            {
                "id": m.id,
                "ts": m.ts,
                "event_type": m.event_type,
                "detail": m.detail,
                "balance": round(m.balance, 2),
                "equity": round(m.equity, 2),
                "drawdown_pct": round(m.drawdown_pct, 4),
                "trading_state": m.trading_state,
                "meta": m.meta,
            }
            for m in rows
        ]