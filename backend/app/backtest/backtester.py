"""Transparent event-driven backtester.

Runs a strategy spec bar-by-bar over candles, applying a conservative cost
model and the risk engine, and records every simulated order. Designed to
avoid look-ahead bias:
  - Signals are evaluated on the close of a completed candle.
  - Fills occur on the NEXT bar's open (plus slippage/spread).
  - Exits use the current bar's high/low/close only.

The backtester is deterministic: identical inputs produce identical outputs.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import pandas as pd

from app.backtest.cost import CostParams
from app.backtest.indicators import add_indicators
from app.backtest.sessions import in_session, is_blackout
from app.dsl import ExpressionError, evaluate_expression, validate_expression
from app.risk.engine import ProposedOrder, RiskDecision, RiskEngine
from app.schemas.strategy import StrategySpec
from app.services.market_math import position_size

WINDOW = 400

TIMEFRAME_SECONDS = {
    "M1": 60,
    "M5": 300,
    "M15": 900,
    "M30": 1800,
    "H1": 3600,
    "H4": 14400,
    "D1": 86400,
    "W1": 604800,
}


@dataclass
class Position:
    symbol: str
    side: str  # long | short
    entry_ts: float
    entry_price: float
    size_units: float
    stop: float
    target: float
    risk_amount: float
    entry_cost: float
    reasons: list[dict] = field(default_factory=list)
    timeframe: str = "M5"


@dataclass
class ClosedTrade:
    symbol: str
    side: str
    timeframe: str
    entry_ts: float
    exit_ts: float
    entry_price: float
    exit_price: float
    size_units: float
    stop: float
    target: float
    gross_pnl: float
    net_pnl: float
    spread_cost: float
    slippage_cost: float
    commission: float
    pips: float
    reasons_entry: list[dict]
    reasons_exit: list[dict]
    exit_reason: str


class LookAheadError(RuntimeError):
    pass


class BacktestDataError(RuntimeError):
    pass


class Backtester:
    def __init__(
        self,
        spec: StrategySpec,
        cost: CostParams,
        risk_engine: RiskEngine | None = None,
        events: list[dict] | None = None,
    ) -> None:
        self.spec = spec
        self.cost = cost
        self.risk_engine = risk_engine
        self.events = events or []

        # Validate rules up-front.
        self._validated = self._validate_rule_set()

    def _validate_rule_set(self) -> dict:
        all_rules = [
            ("entry", r) for r in self.spec.entry_rules
        ] + [("exit", r) for r in self.spec.exit_rules]
        for kind, rule in all_rules:
            errs = validate_expression(rule.expression)
            if errs:
                raise ValueError(
                    f"invalid {kind} rule '{rule.id}': {'; '.join(errs)}"
                )
        return {}

    # -- context builder ---------------------------------------------------
    def _context(self, i: int, arrays: dict) -> dict:
        lo = max(0, i - WINDOW + 1)
        ctx: dict = {}
        for k in ("open", "high", "low", "close"):
            ctx[k] = arrays[k][lo : i + 1]
        ts = arrays["ts"][i]
        ctx["time_minute"] = (int(ts) % 86400) // 60
        ctx["spread_pips"] = self.cost.spread_pips
        ctx["in_session"] = in_session(ts, self.spec.sessions_utc)
        ctx["is_blackout"] = is_blackout(
            ts,
            self.events,
            self.spec.execution_filters.news_blackout_minutes_before,
            self.spec.execution_filters.news_blackout_minutes_after,
        )
        ctx["__prev"] = i > 0
        return ctx

    def _rule_matches(self, rule_expr: str, ctx: dict) -> bool:
        try:
            result = evaluate_expression(rule_expr, ctx)
        except ExpressionError:
            return False
        return bool(result)

    # -- data quality --------------------------------------------------------
    @staticmethod
    def validate_data(df, timeframe: str, raise_missing: bool = False) -> None:
        for col in ("open", "high", "low", "close"):
            vals = df[col]
            if df[col].isna().any():
                raise BacktestDataError(f"missing {col} price (NA) in candle data")
            bad = vals[~pd.to_numeric(vals, errors="coerce").notna()]
            if not bad.empty:
                raise BacktestDataError(f"non-finite {col} price in candle data")
            bad_nonzero = df[(vals <= 0) & (df["close"].notna())]
            if not bad_nonzero.empty:
                raise BacktestDataError(f"non-positive {col} price in candle data")
        bad_hl = df[df["high"] < df["low"]]
        if not bad_hl.empty:
            raise BacktestDataError("high < low on one or more candles")
        bad_hc = df[df["high"] < pd.concat([df["open"], df["close"]], axis=1).max(axis=1)]
        if not bad_hc.empty:
            raise BacktestDataError("high below open/close on one or more candles")
        bad_lc = df[df["low"] > pd.concat([df["open"], df["close"]], axis=1).min(axis=1)]
        if not bad_lc.empty:
            raise BacktestDataError("low above open/close on one or more candles")

        if raise_missing and len(df) > 1:
            expected = TIMEFRAME_SECONDS.get(timeframe)
            if expected:
                gaps = df["ts"].diff().dropna()
                bad = gaps[gaps > expected * 1.5]
                if not bad.empty:
                    missing = int(len(bad))
                    raise BacktestDataError(
                        f"missing candles detected ({missing} gaps > {expected}s)"
                    )

    # -- main --------------------------------------------------------------
    def run(
        self,
        candles: list[dict],
        symbol: str,
        timeframe: str,
        starting_balance: float = 100000.0,
        require_contiguous: bool = False,
    ) -> dict:
        if not candles:
            raise ValueError("no candles to backtest")
        candles = sorted(candles, key=lambda c: c["ts"])
        df = pd.DataFrame(candles)
        df = add_indicators(df, self.spec.indicators)
        df = df.sort_values("ts").reset_index(drop=True)
        self.validate_data(df, timeframe, raise_missing=require_contiguous)

        arrays = {
            "open": [float(x) for x in df["open"].tolist()],
            "high": [float(x) for x in df["high"].tolist()],
            "low": [float(x) for x in df["low"].tolist()],
            "close": [float(x) for x in df["close"].tolist()],
            "ts": [float(x) for x in df["ts"].tolist()],
        }
        n = len(df)
        pip = self.cost.pip_size

        balance = starting_balance
        equity = starting_balance
        peak_equity = starting_balance
        equity_curve: list[dict] = []
        position: Position | None = None
        pending: dict | None = None
        trades: list[ClosedTrade] = []
        rejected: list[dict] = []
        rule_ids = {r.id: r.description for r in self.spec.entry_rules}
        exit_desc = {r.id: r.description for r in self.spec.exit_rules}

        # risk bookkeeping
        daily_pnl: dict[str, float] = {}
        session_trades: dict[str, int] = {}
        day_trades: dict[str, int] = {}

        for i in range(n):
            ts = arrays["ts"][i]

            # 1) Fill pending entry from previous bar's signal at this bar's open.
            if pending is not None and position is None:
                open_px = arrays["open"][i]
                entry_px = self._apply_slippage(open_px, pending["side"])
                stop = pending["stop"]
                target = pending["target"]
                risk_amt = balance * (pending["risk_pct"] / 100.0)
                size = self._compute_size(risk_amt, entry_px, stop, pip)
                if size > 0:
                    if self.risk_engine is not None:
                        decision = self._pre_trade_check(
                            pending, entry_px, stop, size, balance, equity, ts
                        )
                        if not decision.approved:
                            rejected.append(
                                {
                                    "ts": ts,
                                    "reason": decision.rejection_reason,
                                    "correlation_id": decision.correlation_id,
                                }
                            )
                            pending = None
                            balance = self._mark_to_market(position, arrays, i, balance, pip)
                            equity = balance
                            continue
                    entry_cost = self._entry_costs(entry_px, size, side=pending["side"])["spread"]
                    position = Position(
                        symbol=symbol,
                        side=pending["side"],
                        entry_ts=ts,
                        entry_price=entry_px,
                        size_units=size,
                        stop=stop,
                        target=target,
                        risk_amount=risk_amt,
                        entry_cost=entry_cost,
                        reasons=pending["reasons"],
                        timeframe=timeframe,
                    )
                    if self.risk_engine is not None:
                        self.risk_engine.open_positions()
                pending = None

            # 2) Manage open position (stop/target/exit rules).
            if position is not None:
                exit_info = self._check_exit(position, arrays, i, pip, ctx=None)
                if exit_info is None:
                    ctx_e = self._context(i, arrays)
                    for rule in self.spec.exit_rules:
                        if self._rule_matches(rule.expression, ctx_e):
                            exit_info = {
                                "reason": rule.id,
                                "price": arrays["close"][i],
                                "fill": "close",
                            }
                            break
                if exit_info is not None:
                    position = self._close_position(
                        position,
                        exit_info["price"],
                        arrays["ts"][i],
                        trades,
                        balance,
                        exit_info["reason"],
                        exit_info["fill"],
                        exit_desc.get(exit_info["reason"], ""),
                    )
                    balance = trades[-1]["balance"]

            # 3) Evaluate new entry signals at close of bar i.
            if position is None and pending is None and i + 1 < n:
                ctx = self._context(i, arrays)
                if ctx["in_session"] and not ctx["is_blackout"]:
                    signal = self._evaluate_entries(ctx)
                    if signal:
                        pending = {
                            "side": signal["side"],
                            "reasons": signal["reasons"],
                            "risk_pct": self.spec.risk_management.risk_per_trade_pct,
                            "stop": None,
                            "target": None,
                        }
                        open_px = arrays["close"][i]
                        pip_s = pip
                        # compute stop/target distances
                        atr_val = self._atr_at(df, i, self.spec.risk_management.stop_loss_parameters.get("atr_period", 14))
                        stop_dist, target_dist = self._stop_target_distances(open_px, atr_val)
                        if signal["side"] == "long":
                            pending["stop"] = open_px - stop_dist
                            pending["target"] = open_px + target_dist
                        else:
                            pending["stop"] = open_px + stop_dist
                            pending["target"] = open_px - target_dist
                        pending["reasons"] = [
                            {"rule_id": rid, "description": rule_ids.get(rid, "")}
                            for rid in signal["rule_ids"]
                        ]

            # 4) equity bookkeeping
            mtm_pnl = 0.0
            if position is not None:
                if position.side == "long":
                    mtm_pnl = (arrays["close"][i] - position.entry_price) * position.size_units
                else:
                    mtm_pnl = (position.entry_price - arrays["close"][i]) * position.size_units
            equity = balance + mtm_pnl
            peak_equity = max(peak_equity, equity)
            equity_curve.append({"ts": ts, "equity": round(equity, 4), "balance": round(balance, 4)})

        # close any still-open position at last close.
        if position is not None:
            last = n - 1
            pos_cpy = self._close_position(
                position,
                arrays["close"][last],
                arrays["ts"][last],
                trades,
                balance,
                "end_of_data",
                "close",
                "end of backtest data",
            )
            balance = trades[-1]["balance"]

        return {
            "equity_curve": equity_curve,
            "trades": trades,
            "rejected": rejected,
            "starting_balance": starting_balance,
            "ending_balance": balance,
            "symbol": symbol,
            "timeframe": timeframe,
        }

    # -- helper methods (see implementation in companion module) ----------
    def _apply_slippage(self, price: float, side: str) -> float:
        slip = self.cost.slippage_pips * self.cost.pip_size
        if side == "long":
            return price + slip
        return price - slip

    def _compute_size(self, risk_amt, entry, stop, pip):
        sd = abs(entry - stop)
        if sd <= 0:
            return 0.0
        risked = self.cost.pip_size * self.cost.contract_size * (sd / pip)
        if risked <= 0:
            return 0.0
        return risk_amt / risked * self.cost.contract_size

    def _entry_costs(self, price, size, side="long"):
        spread_cost = self.cost.spread_pips * self.cost.pip_size * size
        slippage = self.cost.slippage_pips * self.cost.pip_size * size
        return {"spread": spread_cost, "slippage": slippage}

    def _atr_at(self, df, i, period):
        col = f"ATR{period}"
        if col in df.columns:
            val = df[col].iloc[i]
            if val == val and not math.isnan(val) and val > 0:
                return float(val)
        return self.cost.pip_size * 20.0

    def _stop_target_distances(self, price, atr_val):
        method = self.spec.risk_management.stop_loss_method
        sp = self.spec.risk_management.stop_loss_parameters
        mult = float(sp.get("atr_multiplier", 1.2)) if sp.get("atr_multiplier") else 1.2
        stop_dist = atr_val * mult
        tp_method = self.spec.risk_management.take_profit_method
        tpp = self.spec.risk_management.take_profit_parameters
        rr = float(tpp.get("risk_reward_ratio", 1.5))
        target_dist = stop_dist * rr
        return stop_dist, target_dist

    def _evaluate_entries(self, ctx):
        long_hits = []
        short_hits = []
        for rule in self.spec.entry_rules:
            if rule.id.startswith("long") or "long" in rule.id:
                if self._rule_matches(rule.expression, ctx):
                    long_hits.append(rule.id)
            else:
                if self._rule_matches(rule.expression, ctx):
                    short_hits.append(rule.id)
        if long_hits and not short_hits:
            return {"side": "long", "rule_ids": long_hits, "reasons": []}
        if short_hits and not long_hits:
            return {"side": "short", "rule_ids": short_hits, "reasons": []}
        return None

    def _pre_trade_check(self, pending, entry, stop, size, balance, equity, ts):
        order = ProposedOrder(
            symbol=self.spec.supported_pairs[0],
            side="buy" if pending["side"] == "long" else "sell",
            size_units=size,
            entry_price=entry,
            stop_price=stop,
            account_balance=balance,
            account_equity=equity,
            spread_pips=self.cost.spread_pips,
            ts=ts,
            is_blackout=pending.get("is_blackout", False),
            in_session=True,
            strategy_id=self.spec.name,
            strategy_version=self.spec.version,
            base_currency=self.spec.supported_pairs[0][-3:] or "USD",
        )
        return self.risk_engine.evaluate(order)

    def _check_exit(self, pos, arrays, i, pip, ctx):
        o, h, l, c = arrays["open"][i], arrays["high"][i], arrays["low"][i], arrays["close"][i]
        if pos.side == "long":
            if l <= pos.stop:
                return {"reason": "stop_loss", "price": pos.stop, "fill": "stop"}
            if h >= pos.target:
                return {"reason": "take_profit", "price": pos.target, "fill": "target"}
        else:
            if h >= pos.stop:
                return {"reason": "stop_loss", "price": pos.stop, "fill": "stop"}
            if l <= pos.target:
                return {"reason": "take_profit", "price": pos.target, "fill": "target"}
        return None

    def _close_position(self, pos, exit_price, exit_ts, trades, balance, reason, fill, desc):
        if pos.side == "long":
            gross = (exit_price - pos.entry_price) * pos.size_units
        else:
            gross = (pos.entry_price - exit_price) * pos.size_units
        round_trip_lots = pos.size_units / self.cost.contract_size
        commission = self.cost.commission_per_lot * round_trip_lots
        # slippage on exit
        slippage_exit = self.cost.slippage_pips * self.cost.pip_size * pos.size_units
        spread_exit = self.cost.spread_pips * self.cost.pip_size * pos.size_units / 2.0
        # swap: charged per night a position is held
        nights = max(0, (exit_ts - pos.entry_ts) / 86400.0)
        swap = self.cost.swap_pips_per_night * self.cost.pip_size * pos.size_units * nights
        total_cost = pos.entry_cost + slippage_exit + spread_exit + commission + swap
        net = gross - total_cost
        pips = (exit_price - pos.entry_price) / self.cost.pip_size
        if pos.side == "short":
            pips = (pos.entry_price - exit_price) / self.cost.pip_size
        balance += net
        trade = {
            "symbol": pos.symbol,
            "side": pos.side,
            "timeframe": pos.timeframe,
            "entry_ts": pos.entry_ts,
            "exit_ts": exit_ts,
            "entry_price": round(pos.entry_price, 5),
            "exit_price": round(exit_price, 5),
            "size_units": round(pos.size_units, 2),
            "stop": round(pos.stop, 5),
            "target": round(pos.target, 5),
            "gross_pnl": round(gross, 4),
            "net_pnl": round(net, 4),
            "spread_cost": round(pos.entry_cost + spread_exit, 4),
            "slippage_cost": round(slippage_exit, 4),
            "commission": round(commission, 4),
            "swap": round(swap, 4),
            "pips": round(pips, 2),
            "reasons_entry": pos.reasons,
            "reasons_exit": [{"rule_id": reason, "description": desc}],
            "exit_reason": reason,
            "balance": balance,
        }
        trades.append(trade)
        return None
