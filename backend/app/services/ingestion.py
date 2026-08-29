"""Real-time market-data ingestion.

A per-workspace daemon thread polls the active provider for quotes, feeds the
feed-health tracker, aggregates ticks into live candles (M1..H4), persists
closed candles and a throttled tick stream, and broadcasts events over the
in-process event bus:

- ``quote``          every ingested quote
- ``candle_update``  in-progress candle updates (for the terminal chart)
- ``candle_close``   completed candle (is_complete=True)
- ``feed_health``    feed-state transitions

Threads create their own DB sessions (never reuse request sessions). The whole
service respects ``MARKET_DATA_INGESTION_ENABLED`` and can be started/stopped
per workspace through the API. Workspaces whose configured provider is a real
licensed one (OANDA / Twelve Data) auto-start ingestion on app boot; workspaces
on mock/csv can start it manually for demos.
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.models import Candle, Tick
from app.providers.models import build_candle
from app.services import feed_health
from app.services.event_bus import bus
from app.services.provider_service import get_active_provider

logger = logging.getLogger("fxscalper.ingestion")

_TF_SECONDS = {"M1": 60, "M5": 300, "M15": 900, "M30": 1800, "H1": 3600, "H4": 14400}

_ingestors: dict[str, "MarketDataIngestor"] = {}
_ingestors_lock = threading.Lock()


def _now() -> float:
    return datetime.now(timezone.utc).timestamp()


class MarketDataIngestor:
    """One daemon polling loop per workspace."""

    def __init__(self, workspace_id: str) -> None:
        self.workspace_id = workspace_id
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        # key: (symbol, timeframe) -> dict
        self._agg: dict[tuple[str, str], dict] = {}
        self._pending_ticks: list[Tick] = []
        self._provider_name = "unknown"
        self._last_state: dict[str, str] = {}

    # -- public control --------------------------------------------------
    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run,
            name=f"ingestion-{self.workspace_id[:8]}",
            daemon=True,
        )
        self._thread.start()
        logger.info("ingestion started workspace=%s", self.workspace_id)

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    # -- loop -------------------------------------------------------------
    def _run(self) -> None:
        settings = get_settings()
        interval = settings.DATA_INGESTION_POLL_INTERVAL_SECONDS
        timeframes = [
            tf.strip().upper()
            for tf in settings.DATA_INGESTION_TIMEFRAMES.split(",")
            if tf.strip().upper() in _TF_SECONDS
        ]
        while not self._stop.is_set():
            db = SessionLocal()
            try:
                provider = get_active_provider(db, self.workspace_id)
                self._provider_name = provider.name
                feed_health.set_provider_basis(self.workspace_id, provider.bid_ask_basis)
                symbols = provider.list_symbols()
                for symbol in symbols:
                    try:
                        self._ingest_symbol(db, provider, symbol, timeframes)
                    except Exception as exc:  # noqa: BLE001
                        feed_health.mark_feed_error(self.workspace_id, provider.name, symbol, str(exc))
                        bus.publish(
                            self.workspace_id,
                            "feed_health",
                            {
                                "symbol": symbol.upper(),
                                "provider": provider.name,
                                "feed_status": "DISCONNECTED",
                                "error": str(exc)[:256],
                            },
                        )
                self._flush_ticks(db)
            except Exception as exc:  # noqa: BLE001
                logger.warning("ingestion pass failed workspace=%s: %s", self.workspace_id, exc)
            finally:
                db.close()
            self._stop.wait(interval)

    def _ingest_symbol(self, db, provider, symbol: str, timeframes: list[str]) -> None:
        quote = provider.get_latest_quote(symbol)
        sym = str(quote.get("symbol") or symbol).upper()
        ts = float(quote.get("ts") or _now())
        market_status = quote.get("market_status", "open")

        feed_health.mark_quote_seen(self.workspace_id, provider.name, sym, ts)
        state = feed_health.feed_state(
            self.workspace_id, provider.name, sym, market_status, quote.get("latency_ms")
        )
        bus.publish(
            self.workspace_id,
            "quote",
            {
                "symbol": sym,
                "provider": provider.name,
                "bid": quote.get("bid"),
                "ask": quote.get("ask"),
                "mid": quote.get("mid"),
                "spread_pips": quote.get("spread_pips"),
                "latency_ms": quote.get("latency_ms"),
                "ts": ts,
                "market_status": market_status,
            },
        )
        state_changed = self._last_state.get(sym) != state
        if state_changed:
            self._persist_health(db, provider.name, sym, state, quote.get("latency_ms"))
            bus.publish(
                self.workspace_id,
                "feed_health",
                {
                    "symbol": sym,
                    "provider": provider.name,
                    "feed_status": state,
                    "latency_ms": quote.get("latency_ms"),
                },
            )
        self._last_state[sym] = state

        price = float(quote.get("mid") or (float(quote["bid"]) + float(quote["ask"])) / 2.0)
        for tf in timeframes:
            self._aggregate_tick(sym, tf, ts, price, quota_volume=0.0, provider=provider.name)

        # Throttled tick persistence.
        self._pending_ticks.append(
            Tick(symbol=sym, ts=ts, bid=float(quote.get("bid", price)), ask=float(quote.get("ask", price)), source=provider.name)
        )
        if len(self._pending_ticks) >= get_settings().DATA_INGESTION_TICK_PERSIST_EVERY_N:
            self._flush_ticks(db)

    # -- candle aggregation ----------------------------------------------
    def _aggregate_tick(self, sym: str, tf: str, ts: float, price: float, quota_volume: float, provider: str) -> None:
        tf_sec = _TF_SECONDS[tf]
        bucket = int(ts // tf_sec) * tf_sec
        key = (sym, tf)
        agg = self._agg.get(key)
        if agg is None or agg["bucket"] != bucket:
            if agg is not None:
                agg["is_complete"] = True
                agg["close_ts"] = agg["bucket"] + tf_sec
                self._close_candle(sym, tf, agg, provider)
            agg = {
                "bucket": bucket,
                "symbol": sym,
                "timeframe": tf,
                "open": price,
                "high": price,
                "low": price,
                "close": price,
                "volume": 0.0,
                "is_complete": False,
                "last_ts": ts,
            }
            self._agg[key] = agg
        agg["high"] = max(agg["high"], price)
        agg["low"] = min(agg["low"], price)
        agg["close"] = price
        agg["volume"] += quota_volume
        agg["last_ts"] = ts
        bus.publish(
            self.workspace_id,
            "candle_update",
            {
                "symbol": sym,
                "timeframe": tf,
                "ts": agg["bucket"],
                "open": agg["open"],
                "high": agg["high"],
                "low": agg["low"],
                "close": agg["close"],
                "volume": agg["volume"],
                "is_complete": False,
            },
        )

    def _close_candle(self, sym: str, tf: str, agg: dict, provider: str) -> None:
        candle = build_candle(
            sym,
            tf,
            agg["bucket"],
            agg["open"],
            agg["high"],
            agg["low"],
            agg["close"],
            volume=agg["volume"],
            source=f"{provider}.live",
            is_complete=True,
            bid_ask_basis=feed_health.get_provider_basis(self.workspace_id),
        )
        bus.publish(
            self.workspace_id,
            "candle_close",
            {
                "symbol": sym,
                "timeframe": tf,
                "ts": agg["bucket"],
                "open": candle["open"],
                "high": candle["high"],
                "low": candle["low"],
                "close": candle["close"],
                "volume": candle["volume"],
                "is_complete": True,
            },
        )
        # Persist closed candles (full bar) for history/backfill.
        try:
            db = SessionLocal()
            try:
                candle["open"] = float(candle["open"])
                row = db.get(Candle, (sym, tf, float(agg["bucket"])))
                if row is None:
                    db.add(
                        Candle(
                            symbol=sym,
                            timeframe=tf,
                            ts=float(agg["bucket"]),
                            open=candle["open"],
                            high=candle["high"],
                            low=candle["low"],
                            close=candle["close"],
                            volume=candle["volume"],
                            source=candle["source"],
                            bid_ask_basis=candle["bid_ask_basis"],
                            is_complete=True,
                        )
                    )
                    db.commit()
            finally:
                db.close()
        except Exception as exc:  # noqa: BLE001
            logger.warning("candle persist failed %s %s: %s", sym, tf, exc)
        # Phase 6: evaluate active strategies on the confirmed candle close.
        try:
            from app.services import signal_engine

            signal_engine.trigger_candle_close(self.workspace_id, sym, tf)
        except Exception as exc:  # noqa: BLE001
            logger.debug("signal trigger skipped %s %s: %s", sym, tf, exc)

    def _flush_ticks(self, db) -> None:
        if not self._pending_ticks:
            return
        batch = self._pending_ticks
        self._pending_ticks = []
        try:
            db.add_all(batch)
            db.commit()
        except Exception:  # noqa: BLE001
            db.rollback()
            self._pending_ticks[0:0] = batch

    def _persist_health(self, db, provider: str, symbol: str, state: str, latency_ms: float | None) -> None:
        feed_health._persist_health(db, self.workspace_id, provider, symbol, state, latency_ms, None)  # noqa: SLF001


def start_ingestion(workspace_id: str) -> MarketDataIngestor:
    with _ingestors_lock:
        ing = _ingestors.get(workspace_id)
        if ing is None:
            ing = MarketDataIngestor(workspace_id)
            _ingestors[workspace_id] = ing
        ing.start()
        return ing


def stop_ingestion(workspace_id: str) -> None:
    with _ingestors_lock:
        ing = _ingestors.pop(workspace_id, None)
    if ing is not None:
        ing.stop()


def ingestion_status(workspace_id: str) -> dict:
    with _ingestors_lock:
        ing = _ingestors.get(workspace_id)
        running = ing is not None and ing.running
        provider = ing._provider_name if ing else "unknown"  # noqa: SLF001
    return {"running": running, "provider": provider}


def auto_start() -> None:
    """Start ingestion for workspaces on real licensed providers at boot."""
    if not get_settings().MARKET_DATA_INGESTION_ENABLED:
        return
    db = SessionLocal()
    try:
        from app.models import Workspace

        for ws in db.query(Workspace).all():
            try:
                provider = get_active_provider(db, ws.id)
                if provider.name in ("oanda", "twelvedata"):
                    start_ingestion(ws.id)
            except Exception as exc:  # noqa: BLE001
                logger.info("Skipping ingestion for %s: %s", ws.id, exc)
    finally:
        db.close()