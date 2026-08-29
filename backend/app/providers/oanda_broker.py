"""OANDA v20 practice-account broker adapter.

Maps platform paper orders onto an OANDA account using the official v20 REST
API. This is a **practice-only** adapter by default:

- ``env`` must be ``practice``; ``live`` is only honored when both
  ``LIVE_TRADING_ENABLED`` and ``!BROKER_PRACTICE_DRY_RUN`` are set.
- With ``BROKER_PRACTICE_DRY_RUN = True`` (the default) no HTTP request is ever
  sent; the order intent is validated, recorded in memory, and returned with
  ``dry_run: true``. This is the safe default until a superuser disables it per
  deployment.

Secrets are read from encrypted settings — never from the browser.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

import requests

from app.core.config import get_settings
from app.providers.broker import BrokerProvider

logger = logging.getLogger("fxscalper.oanda_broker")

_BASE_URLS = {"practice": "https://api-fxpractice.oanda.com", "live": "https://api-fxtrade.oanda.com"}

_RATE_MSG = "live execution is disabled: set LIVE_TRADING_ENABLED and BROKER_PRACTICE_DRY_RUN=false to enable"


class OandaPracticeBroker(BrokerProvider):
    name = "oanda_practice"

    def __init__(
        self,
        api_key: str | None = None,
        account_id: str | None = None,
        env: str = "practice",
    ) -> None:
        settings = get_settings()
        self.api_key = api_key or settings.OANDA_API_KEY
        self.account_id = account_id or settings.OANDA_ACCOUNT_ID
        self.env = (env or "practice").lower()
        if self.env not in _BASE_URLS:
            raise ValueError(f"unsupported OANDA env '{self.env}'; try practice or live")
        self.base = _BASE_URLS[self.env]
        self.dry_run = bool(settings.BROKER_PRACTICE_DRY_RUN)
        self._inbox: dict[str, dict] = {}

    def _headers(self) -> dict:
        if not (self.api_key and self.account_id):
            raise RuntimeError("OANDA broker credentials are not configured (OANDA_API_KEY / OANDA_ACCOUNT_ID)")
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def _allowed(self) -> None:
        if self.env == "live" and not get_settings().LIVE_TRADING_ENABLED:
            raise PermissionError(_RATE_MSG)
        if self.dry_run:
            raise PermissionError("practice broker is in dry-run mode — no execution is sent")

    def authenticate(self, credentials: dict | None = None) -> bool:
        if self.dry_run:
            return True  # no credential check that could leak secrets
        try:
            r = requests.get(f"{self.base}/v3/accounts/{self.account_id}/summary", headers=self._headers(), timeout=10)
            return r.status_code == 200
        except requests.RequestException:
            return False

    def list_symbols(self) -> list[str]:
        return ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "NZDUSD", "USDCAD", "USDCHF", "EURJPY"]

    def get_account_summary(self) -> dict:
        if self.dry_run:
            return {
                "account_id": self.account_id,
                "currency": "USD",
                "balance": None,
                "open_positions": 0,
                "dry_run": True,
                "env": self.env,
            }
        self._allowed()
        r = requests.get(f"{self.base}/v3/accounts/{self.account_id}/summary", headers=self._headers(), timeout=15)
        r.raise_for_status()
        acc = r.json().get("account", {})
        return {
            "account_id": acc.get("id"),
            "currency": acc.get("currency"),
            "balance": acc.get("balance"),
            "open_positions": acc.get("openTradeCount", 0),
            "dry_run": False,
            "env": self.env,
        }

    def get_open_positions(self) -> list[dict]:
        if self.dry_run:
            return list(self._inbox.values())
        self._allowed()
        r = requests.get(f"{self.base}/v3/accounts/{self.account_id}/openPositions", headers=self._headers(), timeout=15)
        r.raise_for_status()
        return r.json().get("positions", [])

    def submit_order(self, order: dict, idempotency_key: str | None = None) -> dict:
        symbol = order["symbol"].replace("/", "").replace("_", "")
        instrument = f"{symbol[:3]}_{symbol[3:]}"
        side = order.get("side")
        units = abs(float(order.get("size_units", 0.0)))
        if side in ("long", "buy"):
            units = units
        elif side in ("short", "sell"):
            units = -units
        else:
            raise ValueError(f"invalid side '{side}'")
        if units == 0:
            raise ValueError("order size must be non-zero")
        payload = {
            "type": "MARKET",
            "instrument": instrument,
            "units": str(int(units)),
            "timeInForce": "FOK",
        }
        if order.get("stop_loss"):
            payload["stopLossOnFill"] = {"price": str(round(float(order["stop_loss"]), 6))}
        if order.get("take_profit"):
            payload["takeProfitOnFill"] = {"price": str(round(float(order["take_profit"]), 6))}
        oid = idempotency_key or str(uuid.uuid4())

        if self.dry_run:
            record = {
                "id": oid,
                "dry_run": True,
                "status": "accepted_for_review",
                "request": payload,
                "env": self.env,
                "submitted_at": datetime.now(timezone.utc).isoformat(),
            }
            self._inbox[oid] = record
            logger.info("dry-run practice order: %s", record)
            return record

        self._allowed()
        r = requests.post(
            f"{self.base}/v3/accounts/{self.account_id}/orders",
            headers=self._headers(),
            json={"order": payload},
            timeout=15,
        )
        r.raise_for_status()
        return r.json()

    def close_position(self, position_id: str) -> dict:
        if self.dry_run:
            rec = self._inbox.pop(position_id, None)
            return {"id": position_id, "status": "closed" if rec else "not_found", "dry_run": True}
        self._allowed()
        r = requests.put(
            f"{self.base}/v3/accounts/{self.account_id}/positions/{position_id}",
            headers=self._headers(),
            json={"longUnits": "ALL"},
            timeout=15,
        )
        r.raise_for_status()
        return r.json()

    def cancel_order(self, order_id: str) -> dict:
        if self.dry_run:
            rec = self._inbox.pop(order_id, None)
            return {"id": order_id, "status": "cancelled" if rec else "not_found", "dry_run": True}
        self._allowed()
        r = requests.delete(
            f"{self.base}/v3/accounts/{self.account_id}/orders/{order_id}",
            headers=self._headers(),
            timeout=15,
        )
        r.raise_for_status()
        return r.json()

    def get_order_status(self, order_id: str) -> dict:
        if self.dry_run:
            rec = self._inbox.get(order_id)
            return {"id": order_id, "status": rec["status"] if rec else "not_found", "dry_run": True}
        self._allowed()
        r = requests.get(
            f"{self.base}/v3/accounts/{self.account_id}/orders/{order_id}",
            headers=self._headers(),
            timeout=15,
        )
        r.raise_for_status()
        return r.json()

    def enable_live(self) -> None:
        """Flip off dry-run for a specific deployment (superuser-only)."""
        self.dry_run = False