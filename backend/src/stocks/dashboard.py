"""Arnold stocks dashboard router — mounted under /stocks in the unified Arnold app."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from collections import deque

import httpx
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

log = logging.getLogger(__name__)

SERVER_URL = "http://127.0.0.1:18000"
_SERVER_API_KEY = os.environ.get("ARNOLD_API_KEY", "aqxorczyd8rLzomW94nBjHWaa6tUh6NZ8aMktDbKMgI")

# Persistent HTTP client — reuses TCP connections through SSH tunnel
_http_client: httpx.AsyncClient | None = None
_proxy_cache: dict[str, tuple[float, dict]] = {}  # key → (expiry_ts, data)

import time as _time


def _get_client() -> httpx.AsyncClient:
    global _http_client
    if _http_client is None or _http_client.is_closed:
        _http_client = httpx.AsyncClient(
            base_url=SERVER_URL,
            headers={"X-API-Key": _SERVER_API_KEY},
            timeout=120.0,
            limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
        )
    return _http_client


async def _proxy(path: str, params: dict | None = None, cache_ttl: float = 0):
    """Proxy GET to server via SSH tunnel with optional local caching."""
    cache_key = f"{path}?{json.dumps(params, sort_keys=True)}" if params else path

    if cache_ttl > 0:
        cached = _proxy_cache.get(cache_key)
        if cached and _time.time() < cached[0]:
            return cached[1]

    try:
        client = _get_client()
        r = await client.get(path, params=params)
        data = r.json()
        if cache_ttl > 0 and data:
            _proxy_cache[cache_key] = (_time.time() + cache_ttl, data)
        return data
    except Exception as exc:
        log.warning("Proxy %s failed: %s: %s", path, type(exc).__name__, exc)
        # Return stale cache if available
        cached = _proxy_cache.get(cache_key)
        if cached:
            return cached[1]
        return {}


# Shared state — populated by the pipeline
_state = {
    "ticks": deque(maxlen=2000),  # last 2000 ticks for chart
    "signals": deque(maxlen=100),  # last 100 signals
    "quotes": deque(maxlen=1),  # latest quote
    "zones": [],  # current zones from server
    "depth": {"bids": {}, "asks": {}, "ts": 0.0},  # price → size, refreshed by record_depth
    "account": {},  # TopstepX account info
    "positions": [],  # open positions
    "stats": {  # session stats
        "tick_count": 0,
        "signal_count": 0,
        "trade_count": 0,
        "session_start": None,
        "relay_connected": False,
        "stream_running": False,
    },
}

_dashboard_clients: list[WebSocket] = []
_boot_id = str(int(_time.time()))  # unique per process start — frontend reloads on change
_dash_loop: asyncio.AbstractEventLoop | None = None


def bind_loop(loop: asyncio.AbstractEventLoop) -> None:
    """Set the event loop used by sync callbacks to schedule WS broadcasts.

    Called once at Arnold server startup. Required because record_tick / record_fill
    run on the Playwright/TopstepX thread, not the FastAPI loop.
    """
    global _dash_loop
    _dash_loop = loop


def create_dashboard_router() -> APIRouter:
    router = APIRouter()

    @router.get("/api/state")
    async def get_state():
        depth = _state["depth"]
        bids = sorted(depth["bids"].items(), key=lambda kv: -kv[0])[:20]
        asks = sorted(depth["asks"].items(), key=lambda kv: kv[0])[:20]
        return {
            "ticks": list(_state["ticks"])[-200:],  # last 200 for initial chart
            "signals": list(_state["signals"]),
            "quote": list(_state["quotes"])[-1] if _state["quotes"] else None,
            "zones": _state["zones"],
            "depth": {
                "bids": [{"price": p, "size": s} for p, s in bids],
                "asks": [{"price": p, "size": s} for p, s in asks],
                "ts": depth["ts"],
            },
            "account": _state["account"],
            "positions": _state["positions"],
            "stats": _state["stats"],
        }

    @router.get("/api/candles")
    async def get_candles(interval: str = "5m", days: int = 3, date: str | None = None):
        """Fetch 1m candles from server and aggregate locally (same as old DB path)."""
        params = {"symbol": "NQ", "interval": "1m", "days": str(days)}
        if date:
            params["date"] = date
        raw = await _proxy("/api/trading/market/candles", params, cache_ttl=15)
        candles = raw.get("candles", [])

        if interval != "1m" and candles:
            secs = {"5m": 300, "15m": 900, "30m": 1800, "1h": 3600}.get(interval, 300)
            agg: dict[int, dict] = {}
            for c in candles:
                bucket = (c["t"] // secs) * secs
                if bucket not in agg:
                    agg[bucket] = {"t": bucket, "o": c["o"], "h": c["h"], "l": c["l"], "c": c["c"], "v": c["v"]}
                else:
                    b = agg[bucket]
                    b["h"] = max(b["h"], c["h"])
                    b["l"] = min(b["l"], c["l"])
                    b["c"] = c["c"]
                    b["v"] += c["v"]
            candles = sorted(agg.values(), key=lambda x: x["t"])

        return {"candles": candles}

    @router.get("/api/session")
    async def proxy_session():
        return await _proxy("/api/trading/market/session", cache_ttl=30)

    @router.get("/api/session-levels")
    async def proxy_session_levels(days: int = 5):
        return await _proxy("/api/trading/market/session-levels", {"symbol": "NQ", "days": str(days)}, cache_ttl=60)

    @router.get("/api/vp/{tf}")
    async def proxy_vp(tf: str, date: str | None = None):
        params = {"symbol": "NQ", "timeframe": tf}
        if date:
            params["date"] = date
        # Session VP: 30s cache, historical/weekly/monthly: 5 min local cache
        ttl = 30 if tf == "session" and not date else 300
        return await _proxy("/api/trading/market/volume-profile", params, cache_ttl=ttl)

    @router.get("/api/vwap")
    async def local_vwap(days: int = 3, interval: str = "5m"):
        """Compute developing VWAP from server 1m candles, daily reset at 00:00 CET."""
        raw = await _proxy(
            "/api/trading/market/candles", {"symbol": "NQ", "interval": "1m", "days": str(days)}, cache_ttl=15
        )
        candles = raw.get("candles", [])
        if not candles:
            return {"vwap_days": [], "symbol": "NQ", "count": 0}

        import math
        from datetime import datetime, timezone
        from zoneinfo import ZoneInfo

        _CET = ZoneInfo("Europe/Stockholm")

        series: list[dict] = []
        cum_pv = cum_vol = cum_pv2 = 0.0
        current_cet_date = None

        for c in candles:
            ts_utc = datetime.fromtimestamp(c["t"], tz=timezone.utc)
            cet_date = ts_utc.astimezone(_CET).date()

            if cet_date != current_cet_date:
                cum_pv = cum_vol = cum_pv2 = 0.0
                current_cet_date = cet_date

            tp = (c["h"] + c["l"] + c["c"]) / 3
            vol = c["v"] or 1
            cum_pv += tp * vol
            cum_vol += vol
            cum_pv2 += tp * tp * vol

            if cum_vol == 0:
                continue

            vwap = cum_pv / cum_vol
            variance = max(0, (cum_pv2 / cum_vol) - vwap * vwap)
            sd = math.sqrt(variance)

            series.append(
                {
                    "t": c["t"],
                    "vwap": round(vwap, 2),
                    "sd1_u": round(vwap + sd, 2),
                    "sd1_l": round(vwap - sd, 2),
                    "sd2_u": round(vwap + 2 * sd, 2),
                    "sd2_l": round(vwap - 2 * sd, 2),
                    "sd3_u": round(vwap + 3 * sd, 2),
                    "sd3_l": round(vwap - 3 * sd, 2),
                }
            )

        # Downsample to chart interval
        secs = {"1m": 60, "5m": 300, "15m": 900}.get(interval, 300)
        if secs > 60 and series:
            sampled: dict[int, dict] = {}
            for p in series:
                bucket = (p["t"] // secs) * secs
                sampled[bucket] = p
            series = [sampled[k] for k in sorted(sampled)]

        # Split into per-day segments
        segments: list[list[dict]] = []
        current_seg: list[dict] = []
        prev_cet = None
        for p in series:
            cet = datetime.fromtimestamp(p["t"], tz=timezone.utc).astimezone(_CET).date()
            if prev_cet and cet != prev_cet and current_seg:
                segments.append(current_seg)
                current_seg = []
            current_seg.append(p)
            prev_cet = cet
        if current_seg:
            segments.append(current_seg)

        return {"vwap_days": segments, "symbol": "NQ", "count": sum(len(s) for s in segments)}

    @router.get("/api/session-tpo")
    async def proxy_session_tpo():
        return await _proxy("/api/trading/market/tpo/sessions", {"symbol": "NQ"}, cache_ttl=120)

    @router.get("/api/levels")
    async def proxy_levels(date: str | None = None):
        params = {"symbol": "NQ"}
        if date:
            params["date"] = date
        return await _proxy("/api/trading/market/levels", params)

    @router.get("/api/levels/replay")
    async def local_levels_replay(date: str | None = None):
        """Run RL replay engine locally on tick parquet and return levels/zones."""
        import asyncio
        from functools import partial

        def _do_replay(target_date: str) -> dict:
            import json as _json
            from datetime import datetime as dt_cls
            from pathlib import Path
            from zoneinfo import ZoneInfo

            import pandas as pd

            data_dir = Path(__file__).resolve().parents[2] / "data" / "rl"
            ticks_dir = data_dir / "ticks"

            # Check for cached result first
            cached = data_dir / f"levels_{target_date}.json"
            if cached.exists():
                with open(cached) as f:
                    return _json.load(f)

            target = pd.Timestamp(target_date)
            month_str = target.strftime("%Y-%m")
            pfile = ticks_dir / f"NQ_{month_str}.parquet"
            if not pfile.exists():
                return {"error": f"No tick data for {month_str}"}

            df = pd.read_parquet(pfile)
            df["_date"] = pd.to_datetime(df["timestamp"]).dt.date
            day_df = df[df["_date"] == target.date()].drop(columns=["_date"])
            if day_df.empty:
                return {"error": f"No ticks for {target_date}"}

            ticks = day_df.rename(columns={"timestamp": "ts"}).to_dict(orient="records")

            from src.rl.data.replay_engine import ReplayEngine

            _ET = ZoneInfo("US/Eastern")
            session_dt = dt_cls(target.year, target.month, target.day, 12, 0, 0, tzinfo=_ET)
            engine = ReplayEngine()
            episodes = engine.replay_session(ticks, session_dt)
            snapshot = engine.get_level_snapshot()
            snapshot["episodes_count"] = len(episodes)
            snapshot["ticks_count"] = len(ticks)
            snapshot["date"] = target_date

            # Cache for next time
            with open(cached, "w") as f:
                _json.dump(snapshot, f)

            return snapshot

        if not date:
            # Find the last trading day with tick data
            from datetime import datetime, timezone
            from pathlib import Path as _P

            import pandas as pd

            ticks_dir = _P(__file__).resolve().parents[2] / "data" / "rl" / "ticks"
            parquets = sorted(ticks_dir.glob("NQ_*.parquet"))
            if parquets:
                last_pf = parquets[-1]
                df_dates = pd.read_parquet(last_pf, columns=["timestamp"])
                last_ts = pd.to_datetime(df_dates["timestamp"]).max()
                date = last_ts.strftime("%Y-%m-%d")
            else:
                date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, partial(_do_replay, date))

    @router.get("/api/trades")
    async def get_trades():
        client = _state.get("topstepx_client")
        if not client:
            return {"trades": []}
        try:
            return await client._post(
                "/api/Trade/search",
                {
                    "accountId": client._account_id,
                },
            )
        except Exception:
            return {"trades": []}

    @router.get("/api/broker-trades")
    async def get_broker_trades(days: int = 30):
        """Return completed trades — not yet available via server API."""
        return {"trades": []}

    @router.get("/api/model-status")
    async def get_model_status():
        """Return live model/adapter status."""
        adapter = _state.get("adapter")
        stats = _state["stats"]
        result = {
            "relay_connected": stats.get("relay_connected", False),
            "stream_running": stats.get("stream_running", False),
            "trade_count": stats.get("trade_count", 0),
            "signal_count": stats.get("signal_count", 0),
            "session_start": stats.get("session_start"),
        }
        if adapter:
            tracker = adapter.tracker
            result["halted"] = adapter._halted
            result["halt_reason"] = adapter._halt_reason
            result["session_pnl"] = tracker.session_pnl
            result["peak_equity"] = tracker.peak_equity
            result["trailing_dd"] = tracker.trailing_dd
            result["consecutive_stops"] = tracker.consecutive_stops
            result["is_flat"] = tracker.is_flat
            result["position_side"] = tracker.side
            result["position_size"] = tracker.size
            result["entry_price"] = tracker.entry_price
            result["stop_price"] = tracker.stop_price
        return result

    @router.get("/api/account-info")
    async def get_account_info():
        client = _state.get("topstepx_client")
        if not client:
            return {}
        try:
            data = await client._post(
                "/api/Account/search",
                {
                    "onlyActiveAccounts": True,
                },
            )
            accounts = data.get("accounts", []) if isinstance(data, dict) else data
            # Return the account the client is actually using
            acct = next(
                (a for a in accounts if a.get("id") == client._account_id),
                accounts[0] if accounts else {},
            )
            return acct
        except Exception:
            return {}

    @router.get("/api/orders")
    async def get_orders():
        """Return open orders from TopstepX."""
        client = _state.get("topstepx_client")
        if not client:
            return {"orders": []}
        try:
            orders = await client.get_orders()
            return {"orders": orders if isinstance(orders, list) else []}
        except Exception:
            log.exception("Failed to get orders")
            return {"orders": []}

    @router.post("/api/flatten")
    async def flatten_position():
        """Emergency flatten — cancel all orders and liquidate position."""
        adapter = _state.get("adapter")
        if adapter:
            result = await adapter.flatten("manual_ui")
            return result
        client = _state.get("topstepx_client")
        if client:
            try:
                await client.liquidate_position()
                return {"action": "flatten", "reason": "manual_ui"}
            except Exception:
                log.exception("Failed to flatten")
                return {"error": "flatten failed"}
        return {"error": "no client"}

    @router.post("/api/resume")
    async def resume_trading():
        """Clear halt so the adapter can accept signals again."""
        adapter = _state.get("adapter")
        if not adapter:
            return {"error": "no adapter"}
        adapter.reset_session()
        log.info("Trading resumed via /api/resume")
        return {"resumed": True}

    @router.post("/api/cancel-order/{order_id}")
    async def cancel_order(order_id: int):
        """Cancel a specific order."""
        client = _state.get("topstepx_client")
        if not client:
            return {"error": "no client"}
        try:
            result = await client.cancel_order(order_id)
            return result
        except Exception:
            log.exception("Failed to cancel order %s", order_id)
            return {"error": "cancel failed"}

    @router.get("/api/quote")
    async def get_quote():
        """Return latest quote from state."""
        quotes = _state["quotes"]
        if quotes:
            return dict(quotes[-1]) if hasattr(quotes[-1], "__getitem__") else quotes[-1]
        return {}

    @router.websocket("/ws/dashboard")
    async def dashboard_ws(ws: WebSocket):
        await ws.accept()
        await ws.send_json({"type": "boot", "boot_id": _boot_id})
        # Initial snapshot — zones only push when the relay broadcasts a
        # fresh cluster, so a new client that connects mid-session would
        # otherwise see nothing until the next rebuild. Backfill from the
        # cached state so the chart has levels to draw immediately.
        try:
            zones = _state.get("zones") or []
            if zones:
                await ws.send_json({"type": "zones", "zones": zones})
        except Exception:
            pass
        _dashboard_clients.append(ws)
        try:
            while True:
                await ws.receive_text()  # keep alive / ping-pong
        except WebSocketDisconnect:
            pass
        except Exception:
            pass
        finally:
            if ws in _dashboard_clients:
                _dashboard_clients.remove(ws)

    return router


async def broadcast(event: dict) -> None:
    """Push event to all connected dashboard clients."""
    if not _dashboard_clients:
        return
    msg = json.dumps(event, default=str)
    dead = []
    for ws in _dashboard_clients:
        try:
            await ws.send_text(msg)
        except Exception:
            dead.append(ws)
    for ws in dead:
        if ws in _dashboard_clients:
            _dashboard_clients.remove(ws)


def _emit(event: dict) -> None:
    """Schedule broadcast on the dashboard's event loop (thread-safe)."""
    if _dash_loop is None or _dash_loop.is_closed():
        return
    asyncio.run_coroutine_threadsafe(broadcast(event), _dash_loop)


def record_tick(price: float, size: int, ts: float, side: str = "B") -> None:
    """Called from pipeline on each tick. Throttles dashboard broadcasts to every 10th tick."""
    _state["ticks"].append({"p": price, "s": size, "t": ts, "d": side})
    _state["stats"]["tick_count"] += 1
    if _state["stats"]["tick_count"] % 10 == 0:
        _emit(
            {
                "type": "tick",
                "price": price,
                "ts": ts,
                "tick_count": _state["stats"]["tick_count"],
            }
        )


def record_quote(quote: dict) -> None:
    """Called from pipeline on each quote update.

    Normalizes TopstepX GatewayQuote field names (bestBid/bestAsk) to the
    canonical names the frontend Quote interface expects (bid/ask).
    """
    normalized = {
        "bid": quote.get("bestBid") or quote.get("bid") or 0.0,
        "ask": quote.get("bestAsk") or quote.get("ask") or 0.0,
        "bid_size": quote.get("bestBidSize") or quote.get("bid_size") or 0,
        "ask_size": quote.get("bestAskSize") or quote.get("ask_size") or 0,
    }
    _state["quotes"].append(normalized)
    _emit({"type": "quote", **normalized})


def record_signal(signal: dict) -> None:
    """Called from pipeline when the server sends a trading signal."""
    _state["signals"].append(signal)
    _state["stats"]["signal_count"] += 1
    _emit({"type": "signal", **signal})


def record_dqn_inference(inference: dict) -> None:
    """Called from pipeline when a DQN inference event arrives (for live viz)."""
    _emit(inference)  # already has type: dqn_inference


def record_fill(fill: dict) -> None:
    """Called from pipeline when a trade fill occurs."""
    _state["stats"]["trade_count"] += 1
    _emit({"type": "fill", **fill})


def record_exit(exit_info: dict) -> None:
    """Called from pipeline when a trade exit occurs."""
    _emit({"type": "exit", **exit_info})


def record_trade(trade: dict) -> None:
    """Called from broker_adapter._log_broker_trade when a round-trip closes.
    Carries full P&L/context fields (entry_price, exit_price, pnl_dollars, pnl_r,
    was_stop, trail_count, signal_*). Front-end consumers render this as the
    trade-closed event; fills/exits already tracked separately.
    """
    _emit({"type": "trade", **trade})


def update_zones(zones: list) -> None:
    """Called from pipeline when zone data is received from server."""
    _state["zones"] = zones
    _emit({"type": "zones", "zones": zones})


def update_account(account: dict) -> None:
    """Called from pipeline with TopstepX account info."""
    _state["account"] = account
    _emit({"type": "account", **account})


def update_positions(positions: list) -> None:
    """Called from pipeline with open position data."""
    _state["positions"] = positions
    _emit({"type": "positions", "positions": positions})


def update_status(relay_connected: bool, stream_running: bool) -> None:
    """Called from health-check loop to update connection status."""
    _state["stats"]["relay_connected"] = relay_connected
    _state["stats"]["stream_running"] = stream_running
    _emit(
        {
            "type": "status",
            "relay_connected": relay_connected,
            "stream_running": stream_running,
        }
    )


# Throttle depth broadcasts: TopstepX fires GatewayDepth at >50Hz; the UI
# can repaint the ladder ~5Hz without missing meaningful structure.
_DEPTH_THROTTLE_S = 0.2
_last_depth_emit = 0.0


def record_depth(level: dict) -> None:
    """Called from TopstepXStream.on_depth.

    `level` shape (from GatewayDepth, see backend/src/stocks/topstepx_stream.py:276-289):
      {"price": float, "currentVolume": int, "type": 1|2}  (1 = bid, 2 = ask)
    Maintains a price→size dict per side; size 0 removes the level.
    """
    global _last_depth_emit
    price = float(level.get("price", 0))
    if price == 0:
        return
    size = int(level.get("currentVolume", 0))
    side = level.get("type", 0)
    book = _state["depth"]["bids"] if side == 1 else _state["depth"]["asks"]
    if size <= 0:
        book.pop(price, None)
    else:
        book[price] = size

    now = _time.time()
    if now - _last_depth_emit < _DEPTH_THROTTLE_S:
        return
    _last_depth_emit = now
    _state["depth"]["ts"] = now

    # Snapshot the current book — only top 20 levels each side, sorted.
    bids_sorted = sorted(_state["depth"]["bids"].items(), key=lambda kv: -kv[0])[:20]
    asks_sorted = sorted(_state["depth"]["asks"].items(), key=lambda kv: kv[0])[:20]
    _emit(
        {
            "type": "depth",
            "bids": [{"price": p, "size": s} for p, s in bids_sorted],
            "asks": [{"price": p, "size": s} for p, s in asks_sorted],
            "ts": now,
        }
    )
