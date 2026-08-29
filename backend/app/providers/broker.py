"""Broker provider abstractions and the simulated broker for paper trading."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class BrokerProvider(ABC):
    name = "base"

    @abstractmethod
    def authenticate(self, credentials: dict | None = None) -> bool:
        ...

    @abstractmethod
    def list_symbols(self) -> list[str]:
        ...

    @abstractmethod
    def get_account_summary(self) -> dict:
        ...

    @abstractmethod
    def get_open_positions(self) -> list[dict]:
        ...

    @abstractmethod
    def submit_order(self, order: dict, idempotency_key: str | None = None) -> dict:
        ...

    def cancel_order(self, order_id: str) -> dict:
        raise NotImplementedError

    def close_position(self, position_id: str) -> dict:
        raise NotImplementedError

    def get_order_status(self, order_id: str) -> dict:
        raise NotImplementedError


class SimulatedBroker(BrokerProvider):
    """In-memory simulated broker used for paper trading and integration tests."""

    name = "simulated"

    def __init__(self, starting_balance: float = 100000.0) -> None:
        self.balance = starting_balance
        self.equity = starting_balance
        self.positions: dict[str, dict] = {}
        self.closed: list[dict] = []
        self._authenticated = False

    def authenticate(self, credentials: dict | None = None) -> bool:
        self._authenticated = True
        return True

    def list_symbols(self) -> list[str]:
        return ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "NZDUSD", "USDCAD"]

    def get_account_summary(self) -> dict:
        return {
            "balance": self.balance,
            "equity": self.equity,
            "currency": "USD",
            "open_positions": len(self.positions),
        }

    def get_open_positions(self) -> list[dict]:
        return list(self.positions.values())

    def submit_order(self, order: dict, idempotency_key: str | None = None) -> dict:
        pid = f"pos-{len(self.positions) + len(self.closed) + 1}"
        pos = {
            "id": pid,
            "symbol": order["symbol"],
            "side": order["side"],
            "size": order.get("size_units", 0.0),
            "entry_price": order.get("entry_price"),
            "stop_loss": order.get("stop_loss"),
            "take_profit": order.get("take_profit"),
            "status": "open",
        }
        self.positions[pid] = pos
        return {"id": pid, "status": "submitted", "position": pos}
