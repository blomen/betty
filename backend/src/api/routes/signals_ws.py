"""WebSocket relay: local firevstocks client <-> server LevelMonitor."""

from __future__ import annotations

import json
import logging
import threading
import time

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

log = logging.getLogger(__name__)
router = APIRouter()


def _get_market_db(app):
    """Get or cache the market DB session factory."""
    factory = getattr(app.state, "_market_db_factory", None)
    if not factory:
        from ...db.models import get_market_session

        factory = get_market_session
        app.state._market_db_factory = factory
    return factory


def _persist_candle(app, candle: dict, interval: str) -> None:
    """Persist a closed candle to DB (background, non-blocking)."""
    from datetime import datetime, timezone

    try:
        db = _get_market_db(app)()
        try:
            from ...repositories.market_repo import MarketRepo

            ts = datetime.fromtimestamp(candle["t"], tz=timezone.utc)
            MarketRepo(db).upsert_candle(
                "NQ",
                interval,
                ts,
                candle["o"],
                candle["h"],
                candle["l"],
                candle["c"],
                candle["v"],
            )
            db.commit()
        finally:
            db.close()
    except Exception:
        log.warning("Failed to persist %s candle: %s", interval, candle, exc_info=True)


# Batch tick writer for market_trades (same table Databento path uses)
_tick_batch: list[dict] = []
_tick_batch_lock = threading.Lock()
_TICK_FLUSH_SIZE = 500
_TICK_FLUSH_INTERVAL = 5.0
_tick_flush_thread: threading.Thread | None = None


def _start_tick_flusher(app) -> None:
    """Start background thread that flushes ticks to market_trades every 5s."""
    global _tick_flush_thread
    if _tick_flush_thread is not None:
        return

    def _loop():
        while True:
            time.sleep(_TICK_FLUSH_INTERVAL)
            _flush_tick_batch(app)

    _tick_flush_thread = threading.Thread(target=_loop, daemon=True, name="ws-tick-flusher")
    _tick_flush_thread.start()


def _buffer_tick(app, price: float, size: int, ts_dt, side: str) -> None:
    """Add a tick to the batch buffer, flush if full."""
    batch = None
    with _tick_batch_lock:
        _tick_batch.append(
            {
                "symbol": "NQ",
                "price": price,
                "size": size,
                "ts": ts_dt,
                "side": side,
            }
        )
        if len(_tick_batch) >= _TICK_FLUSH_SIZE:
            batch = list(_tick_batch)
            _tick_batch.clear()
    if batch:
        _do_flush(app, batch)


def _flush_tick_batch(app) -> None:
    """Flush current batch to DB."""
    with _tick_batch_lock:
        if not _tick_batch:
            return
        batch = list(_tick_batch)
        _tick_batch.clear()
    _do_flush(app, batch)


def _do_flush(app, batch: list[dict]) -> None:
    """Write batch to market_trades."""
    try:
        db = _get_market_db(app)()
        try:
            from ...repositories.market_repo import MarketRepo

            MarketRepo(db).bulk_insert_trades(batch)
        finally:
            db.close()
    except Exception:
        log.debug("Failed to flush %d ticks to market_trades", len(batch))


@router.websocket("/ws/signals")
async def signal_relay(ws: WebSocket):
    """Accept ticks from local client, feed to LevelMonitor, send signals back."""
    await ws.accept()
    log.info("Signal relay connected from %s", ws.client)

    level_monitor = getattr(ws.app.state, "level_monitor", None)
    if level_monitor is None:
        await ws.send_json({"type": "error", "message": "LevelMonitor not initialized"})
        await ws.close()
        return

    # Register signal callback — sends specialist signals to local client
    async def _on_signal(signal: dict):
        try:
            await ws.send_json({"type": "signal", **signal})
        except Exception:
            log.debug("Failed to send signal to relay client")

    level_monitor.set_signal_callback(_on_signal)

    try:
        while True:
            raw = await ws.receive_text()
            msg = json.loads(raw)
            msg_type = msg.get("type")

            if msg_type == "tick":
                price = msg["price"]
                size = msg["size"]
                ts = msg["ts"]

                # Feed tick buffer for micro/orderflow features
                tick_buffer = getattr(ws.app.state, "stocks_tick_buffer", None)
                if tick_buffer:
                    from datetime import datetime, timezone

                    tick_ts = datetime.fromtimestamp(ts, tz=timezone.utc)
                    side = msg.get("side", "B")  # A=sell aggressor, B=buy aggressor
                    tick_buffer.add(tick_ts, price, size, side)

                    # Persist to market_trades (used by LiveEpisodeCollector + charts)
                    _buffer_tick(ws.app, price, size, tick_ts, side)
                    _start_tick_flusher(ws.app)

                # Feed candle flows for candle features + DB persistence
                candle_5m = getattr(ws.app.state, "stocks_candle_flow_5m", None)
                candle_1m = getattr(ws.app.state, "stocks_candle_flow_1m", None)
                if candle_5m:
                    _, closed = candle_5m.update(price, size, ts)
                    if closed:
                        threading.Thread(target=_persist_candle, args=(ws.app, closed, "5m"), daemon=True).start()
                if candle_1m:
                    _, closed = candle_1m.update(price, size, ts)
                    if closed:
                        threading.Thread(target=_persist_candle, args=(ws.app, closed, "1m"), daemon=True).start()

                # Feed level monitor (triggers zone detection + inference)
                level_monitor.on_tick(price, size, ts)
            elif msg_type == "fill":
                adapter = getattr(ws.app.state, "broker_adapter", None)
                if adapter:
                    adapter.tracker.on_fill(
                        side=msg.get("side", "long"),
                        price=msg["price"],
                        size=msg.get("size", 1),
                        stop_price=msg.get("stop_price", 0.0),
                        signal_price=msg.get("signal_price", 0.0),
                    )
            elif msg_type == "exit":
                adapter = getattr(ws.app.state, "broker_adapter", None)
                if adapter:
                    adapter.tracker.on_exit(
                        exit_price=msg["price"],
                        was_stop=msg.get("was_stop", False),
                    )
            elif msg_type == "ping":
                await ws.send_json({"type": "pong", "ts": time.time()})
    except WebSocketDisconnect:
        log.info("Signal relay disconnected")
    except Exception:
        log.exception("Signal relay error")
    finally:
        level_monitor.set_signal_callback(None)
