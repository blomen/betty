"""WebSocket relay: local firevstocks client <-> server LevelMonitor."""
from __future__ import annotations

import asyncio
import json
import logging
import time

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

log = logging.getLogger(__name__)
router = APIRouter()


def _persist_candle(app, candle: dict, interval: str) -> None:
    """Persist a closed candle to DB (background, non-blocking)."""
    try:
        db_factory = getattr(app.state, "_market_db_factory", None)
        if not db_factory:
            from ...db.models import get_market_session
            db_factory = get_market_session
            app.state._market_db_factory = db_factory
        db = db_factory()
        try:
            from ...repositories.market_repo import MarketRepo
            MarketRepo(db).upsert_candle(
                "NQ", interval,
                candle["t"], candle["o"], candle["h"],
                candle["l"], candle["c"], candle["v"],
            )
            db.commit()
        finally:
            db.close()
    except Exception:
        log.debug("Failed to persist %s candle", interval)

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

                # Feed candle flows for candle features + DB persistence
                candle_5m = getattr(ws.app.state, "stocks_candle_flow_5m", None)
                candle_1m = getattr(ws.app.state, "stocks_candle_flow_1m", None)
                if candle_5m:
                    _, closed = candle_5m.update(price, size, ts)
                    if closed:
                        _persist_candle(ws.app, closed, "5m")
                if candle_1m:
                    _, closed = candle_1m.update(price, size, ts)
                    if closed:
                        _persist_candle(ws.app, closed, "1m")

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
