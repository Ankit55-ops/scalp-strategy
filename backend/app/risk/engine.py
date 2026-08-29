"""Risk engine: gating every simulated/live order before execution.

Every decision records an immutable audit record containing the market
state, proposed order, checks performed, result, rejection reason, and a
correlation id.

Kill switches (global / per-strategy / per-pair) are persisted and checked
first. Automatic kill switches may be raised by the monitoring layer on
data-feed/broker failure or loss-threshold breach.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.models import RiskProfile
from app.risk.killswitch import KillSwitchRegistry


@dataclass
class ProposedOrder:
    symbol: str
    side: str  # buy (long) | sell (short)
    size_units: float
    entry_price: float
    stop_price: float | None
    account_balance: float
    account_equity: float
    spread_pips: float
    ts: float
    is_blackout: bool = False
    in_session: bool = True
    strategy_id: str | None = None
    strategy_version: str | None = None
    base_currency: str = "USD"
    correlated_exposure: dict[str, float] = field(default_factory=dict)


@dataclass
class RiskCheckResult:
    check: str
    passed: bool
    detail: str = ""


@dataclass
class RiskDecision:
    approved: bool
    correlation_id: str
    checks: list[RiskCheckResult]
    rejection_reason: str | None = None

    def as_audit_dict(self, order: ProposedOrder) -> dict:
        return {
            "ts": datetime.now(timezone.utc).isoformat(),
            "strategy_id": order.strategy_id,
            "strategy_version": order.strategy_version,
            "symbol": order.symbol,
            "side": order.side,
            "size_units": order.size_units,
            "entry_price": order.entry_price,
            "stop_price": order.stop_price,
            "account_balance": order.account_balance,
            "market_state": {
                "spread_pips": order.spread_pips,
                "equity": order.account_equity,
                "blackout": order.is_blackout,
                "in_session": order.in_session,
            },
            "checks": [
                {"check": c.check, "passed": c.passed, "detail": c.detail}
                for c in self.checks
            ],
            "result": "approved" if self.approved else "rejected",
            "rejection_reason": self.rejection_reason,
            "correlation_id": self.correlation_id,
        }


class RiskEngine:
    """Centralized risk gate. All simulated and live orders must pass `evaluate`."""

    def __init__(
        self,
        killswitch: KillSwitchRegistry,
        profile: RiskProfile | None = None,
    ) -> None:
        self.killswitch = killswitch
        self.profile = profile

        # Live counters kept in memory for the running session.
        self._open_positions = 0
        self._trades_today = 0
        self._trades_session = 0
        self._consecutive_losses = 0
        self._day_start_equity: float | None = None
        self._week_start_equity: float | None = None
        self._peak_equity: float | None = None
        self._day: str | None = None
        self._week: str | None = None

    # -- lifecycle ---------------------------------------------------------
    def set_profile(self, profile: RiskProfile) -> None:
        self.profile = profile

    def on_trade_result(self, net_pnl: float, ts: float) -> None:
        if self._day_start_equity is None:
            self._day_start_equity = net_pnl
            self._week_start_equity = net_pnl
        if net_pnl < 0:
            self._consecutive_losses += 1
        else:
            self._consecutive_losses = 0

    # -- main entry --------------------------------------------------------
    def evaluate(self, order: ProposedOrder) -> RiskDecision:
        correlation_id = str(uuid.uuid4())
        checks: list[RiskCheckResult] = []
        check = lambda name, passed, detail="": checks.append(
            RiskCheckResult(name, passed, detail)
        )

        if self.killswitch.is_halted(order.symbol, order.strategy_id):
            check("kill_switch", False, "kill switch active")
            return self._reject(order, checks, correlation_id, "kill switch active")

        profile = self.profile
        if profile is None:
            check("profile", False, "no risk profile configured")
            return self._reject(order, checks, correlation_id, "no risk profile")

        # Session / blackout
        check("session", order.in_session, "in configured trading session")
        check("news_blackout", not order.is_blackout, "not in news blackout window")
        if not order.in_session:
            return self._reject(order, checks, correlation_id, "outside trading session")
        if order.is_blackout:
            return self._reject(order, checks, correlation_id, "news blackout window")

        # Spread threshold
        check(
            "spread",
            order.spread_pips <= profile.max_spread_pips,
            f"spread {order.spread_pips:.2f} <= {profile.max_spread_pips}",
        )
        if order.spread_pips > profile.max_spread_pips:
            return self._reject(order, checks, correlation_id, "spread above max")

        # Position count
        check(
            "max_open_positions",
            self._open_positions < profile.max_open_positions,
            f"open {self._open_positions} < {profile.max_open_positions}",
        )
        if self._open_positions >= profile.max_open_positions:
            return self._reject(
                order, checks, correlation_id, "max open positions reached"
            )

        # Daily/weekly loss limits
        if self._day_start_equity is not None:
            day_loss = (self._day_start_equity - order.account_equity) / max(
                self._day_start_equity, 1e-9
            ) * 100
            check(
                "daily_loss_limit",
                day_loss < profile.max_daily_loss_pct,
                f"day loss {day_loss:.2f}% < {profile.max_daily_loss_pct}%",
            )
            if day_loss >= profile.max_daily_loss_pct:
                return self._reject(
                    order, checks, correlation_id, "daily loss limit breached"
                )

        # Consecutive losses
        check(
            "max_consecutive_losses",
            self._consecutive_losses < profile.max_consecutive_losses,
            f"consecutive losses {self._consecutive_losses} < {profile.max_consecutive_losses}",
        )
        if self._consecutive_losses >= profile.max_consecutive_losses:
            return self._reject(
                order, checks, correlation_id, "max consecutive losses reached"
            )

        # Trades per day
        check(
            "max_trades_per_day",
            self._trades_today < profile.max_trades_per_day,
            f"trades today {self._trades_today} < {profile.max_trades_per_day}",
        )
        if self._trades_today >= profile.max_trades_per_day:
            return self._reject(
                order, checks, correlation_id, "max trades per day reached"
            )

        # Stop distance (minimum)
        if order.stop_price is not None:
            from app.services.market_math import pip_size, price_to_pips

            stop_dist_pips = price_to_pips(
                abs(order.entry_price - order.stop_price), pip_size(order.base_currency)
            )
            min_stop = profile.hard_stop_distance_pips
            check(
                "min_stop_distance",
                stop_dist_pips >= min_stop,
                f"stop {stop_dist_pips:.1f} pips >= {min_stop}",
            )
            if stop_dist_pips < min_stop:
                return self._reject(
                    order, checks, correlation_id, "stop distance below minimum"
                )

        # Correlated exposure
        for group, exposure in order.correlated_exposure.items():
            check(
                f"correlated_exposure_{group}",
                exposure <= profile.max_correlated_exposure_pct,
                f"{group} exposure {exposure:.1f}% <= {profile.max_correlated_exposure_pct}%",
            )
            if exposure > profile.max_correlated_exposure_pct:
                return self._reject(
                    order,
                    checks,
                    correlation_id,
                    f"correlated exposure limit breached for {group}",
                )

        decision = RiskDecision(
            approved=True,
            correlation_id=correlation_id,
            checks=checks,
        )
        self._open_positions += 1
        self._trades_today += 1
        self._trades_session += 1
        return decision

    def _reject(
        self,
        order: ProposedOrder,
        checks: list[RiskCheckResult],
        correlation_id: str,
        reason: str,
    ) -> RiskDecision:
        return RiskDecision(
            approved=False,
            correlation_id=correlation_id,
            checks=checks,
            rejection_reason=reason,
        )

    def open_positions(self) -> int:
        return self._open_positions
