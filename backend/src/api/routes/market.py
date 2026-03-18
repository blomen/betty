"""Market data API routes — AMT session analysis and scanner signals."""

from fastapi import APIRouter, Depends, Query, Request
from sse_starlette.sse import EventSourceResponse
import asyncio, json

from ..deps import get_db
from ...services.market_service import MarketService


def _get_live_stream(request: Request):
    """Get the DatabentoLiveStream from app state (set during lifespan startup)."""
    return getattr(request.app.state, "databento_stream", None)

router = APIRouter(prefix="/api/trading/market", tags=["market"])


def _svc(db=Depends(get_db)) -> MarketService:
    return MarketService(db)


@router.get("/session")
async def get_current_session(svc: MarketService = Depends(_svc)):
    """Expanded session data with all analytical layers."""
    result = await svc.build_expanded_session()
    if not result:
        return {"status": "no_data", "message": "No session computed yet. POST /compute first."}
    return result


@router.get("/session/{date}")
async def get_session_by_date(date: str, svc: MarketService = Depends(_svc)):
    """Get session data for a specific date."""
    data = svc.get_current_session()  # Will query by date
    # Override: query specific date
    from ...repositories.market_repo import MarketRepo
    repo = MarketRepo(svc.db)
    session = repo.get_session(date, "NQ")
    if session and session.session_json:
        return session.session_json
    return {"status": "no_data", "date": date}


@router.get("/candles")
async def get_candles(
    symbol: str = Query(default="NQ"),
    interval: str = Query(default="5m", pattern="^(1m|5m|15m)$"),
    date: str = Query(default=None),
    days: int = Query(default=5, ge=1, le=365),
    svc: MarketService = Depends(_svc),
):
    """Return OHLCV candles for charting from market_candles DB."""
    return await svc.get_candles(symbol, interval, date, days)


@router.get("/signals")
async def get_active_signals(svc: MarketService = Depends(_svc)):
    """Get currently active trading signals."""
    return {"signals": svc.get_active_signals()}


@router.post("/scan")
async def trigger_scan(
    threshold: float = Query(default=None, description="Score threshold (default from config)"),
    svc: MarketService = Depends(_svc),
):
    """Run scanner on current session → generate signals."""
    signals = await svc.run_scan(threshold)
    return {"signals": signals, "count": len(signals)}


@router.post("/compute")
async def trigger_compute(
    request: Request,
    date: str = Query(default=None, description="Date to compute (YYYY-MM-DD, default today)"),
    svc: MarketService = Depends(_svc),
):
    """Fetch market data and compute AMT analysis for a date."""
    data = await svc.compute_session(date)

    # Refresh level monitor with new session data
    level_monitor = getattr(request.app.state, "level_monitor", None)
    if level_monitor:
        expanded = await svc.build_expanded_session()
        if expanded:
            level_monitor.load_levels(expanded)

    return data


@router.get("/history")
async def get_session_history(
    limit: int = Query(default=30, le=100),
    svc: MarketService = Depends(_svc),
):
    """Get historical session data."""
    return {"sessions": svc.get_session_history(limit=limit)}


@router.get("/indicators")
async def get_indicators(svc: MarketService = Depends(_svc)):
    """Live orderflow indicators + ML predictions."""
    return await svc.get_indicators()


@router.get("/confirmations")
async def get_confirmations(svc: MarketService = Depends(_svc)):
    """Deprecated: use /indicators instead."""
    return await svc.get_indicators()


@router.get("/macro")
async def get_macro_snapshot():
    """Get current macro data (VIX, DXY, yields, regime)."""
    from ...market_data.macro_provider import fetch_macro_snapshot
    macro = await fetch_macro_snapshot()
    return {
        "vix": macro.vix,
        "vix_change_pct": macro.vix_change_pct,
        "dxy": macro.dxy,
        "dxy_change_pct": macro.dxy_change_pct,
        "us10y": macro.us10y,
        "us10y_change_bps": macro.us10y_change_bps,
        "us2y": macro.us2y,
        "yield_curve_spread": macro.yield_curve_spread,
        "regime": macro.regime,
        "regime_score": macro.regime_score,
        "fetched_at": macro.fetched_at,
    }


@router.get("/stream")
async def market_stream(request: Request, symbol: str = "NQ"):
    """SSE stream of real-time tick data, candles, and level touches."""
    stream = _get_live_stream(request)
    if not stream:
        return {"error": "Live stream not available"}

    queue = stream.subscribe()

    async def event_generator():
        try:
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=30)
                    event_type = event.get("type", "tick")
                    yield {"event": event_type, "data": json.dumps(event)}
                except asyncio.TimeoutError:
                    yield {"event": "heartbeat", "data": "{}"}
        except asyncio.CancelledError:
            stream.unsubscribe(queue)

    return EventSourceResponse(event_generator())


@router.get("/levels")
async def get_levels(
    symbol: str = "NQ",
    date: str = None,
    svc: MarketService = Depends(_svc),
):
    """Get all structural levels (PDH/PDL, Tokyo/London, IB, VP, VWAP, etc.) for a session."""
    from datetime import datetime, timezone
    target_date = date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    levels = svc.repo.get_levels(symbol, target_date)
    return [
        {
            "level_type": l.level_type,
            "price_low": l.price_low,
            "price_high": l.price_high,
            "direction": l.direction,
            "session": l.session,
            "is_filled": l.is_filled,
        }
        for l in levels
    ]


@router.get("/levels/live")
async def get_live_levels(request: Request):
    """Get all monitored levels with current distance and status."""
    monitor = getattr(request.app.state, "level_monitor", None)
    stream = _get_live_stream(request)
    if not monitor or not stream:
        return {"levels": [], "price": None}
    last_price = stream.buffer.ticks[-1]["price"] if stream.buffer.ticks else None
    return {
        "levels": monitor.get_levels_snapshot(last_price or 0),
        "price": last_price,
    }


@router.get("/context")
async def get_context(symbol: str = "NQ", svc: MarketService = Depends(_svc)):
    ctx = svc.repo.get_context(symbol)
    if not ctx:
        return {"symbol": symbol, "gates_set": False}
    return {
        "symbol": ctx.symbol,
        "gates_set": True,
        "macro_bias": ctx.macro_bias,
        "risk_mode": ctx.risk_mode,
        "cycle_phase": ctx.cycle_phase,
        "structure": ctx.structure,
        "structure_hl": ctx.structure_hl,
        "structure_lh": ctx.structure_lh,
        "day_type": ctx.day_type,
        "vp_current_start": ctx.vp_old_macro_start,
        "vp_ongoing_macro_start": ctx.vp_ongoing_macro_start,
        "vp_leg_start": ctx.vp_leg_start,
    }


@router.put("/context")
async def update_context(data: dict, symbol: str = "NQ", svc: MarketService = Depends(_svc)):
    """Update market context — accepts ISO date strings for VP anchors."""
    from datetime import datetime as dt_cls
    # Map vp_current_start → vp_old_macro_start (repurposed column)
    if "vp_current_start" in data:
        data["vp_old_macro_start"] = data.pop("vp_current_start")
    for field in ["vp_leg_start", "vp_ongoing_macro_start", "vp_old_macro_start"]:
        if field in data and isinstance(data[field], str):
            parsed = dt_cls.strptime(data[field], "%Y-%m-%d")
            data[field] = int(parsed.timestamp())
    svc.repo.upsert_context(symbol, data)
    return {"status": "ok", "symbol": symbol}


@router.get("/volume-profile")
async def get_volume_profile(
    symbol: str = Query(default="NQ"),
    timeframe: str = Query(default="session", pattern="^(session|weekly|monthly|macro|leg|current)$"),
    svc: MarketService = Depends(_svc),
):
    """Return full VP curve (price→volume pairs) for a given timeframe."""
    return await svc.get_volume_profile_curve(symbol, timeframe)


@router.get("/footprint")
async def get_footprint(
    request: Request,
    period: int = Query(default=300, description="Candle period in seconds (60, 300)"),
    limit: int = Query(default=20, le=50, description="Number of candles to return"),
):
    """Get footprint matrix — per-price-level buy/sell volume for recent candles.

    This is the core orderflow visualization: for each candle, shows
    buy vs sell volume at every price level, diagonal imbalances, and
    stacked imbalances.
    """
    stream = _get_live_stream(request)
    if not stream:
        return {"error": "Live stream not available"}

    from ...market_data.orderflow import build_candle_flow

    ticks = list(stream.buffer.ticks)
    if len(ticks) < 10:
        return {"candles": [], "message": "Insufficient tick data"}

    candles = build_candle_flow(ticks, period_seconds=period)
    candles = candles[-limit:]

    return {
        "candles": [
            {
                "ts": c.ts.isoformat(),
                "o": c.open,
                "h": c.high,
                "l": c.low,
                "c": c.close,
                "volume": c.volume,
                "buy_volume": c.buy_volume,
                "sell_volume": c.sell_volume,
                "delta": c.delta,
                "delta_pct": round(c.delta_pct, 1),
                "price_levels": [
                    {
                        "price": pl.price,
                        "buy_vol": pl.buy_volume,
                        "sell_vol": pl.sell_volume,
                    }
                    for pl in c.price_levels
                ],
                "diagonal_imbalances": [
                    {
                        "price": d.price,
                        "direction": d.direction,
                        "ratio": d.ratio,
                    }
                    for d in c.diagonal_imbalances
                ],
                "stacked_imbalances": [
                    {
                        "direction": s.direction,
                        "price_low": s.price_low,
                        "price_high": s.price_high,
                        "count": s.count,
                    }
                    for s in c.stacked_imbalances
                ],
            }
            for c in candles
        ],
        "period": period,
        "count": len(candles),
    }


@router.get("/book")
async def get_top_of_book(request: Request):
    """Get current top-of-book snapshot from MBP-1 stream."""
    stream = _get_live_stream(request)
    if not stream:
        return {"error": "Live stream not available"}
    book = stream.book
    return {
        "bid_price": book.bid_price,
        "bid_size": book.bid_size,
        "ask_price": book.ask_price,
        "ask_size": book.ask_size,
        "spread": book.spread,
        "ts": book.ts.isoformat() if book.ts else None,
    }


@router.post("/backfill")
async def backfill_trades(
    start: str = Query(description="Start date YYYY-MM-DD"),
    end: str = Query(default=None, description="End date YYYY-MM-DD (default today)"),
    symbol: str = "NQ.FUT",
):
    """Backfill historical trades from Databento into market_trades table."""
    import os
    from datetime import date as dt_date

    api_key = os.environ.get("DATABENTO_API_KEY")
    if not api_key:
        return {"error": "DATABENTO_API_KEY not set"}

    from ...market_data.history import backfill_trades_to_db
    from ...db.models import get_session as get_db_session

    start_date = dt_date.fromisoformat(start)
    end_date = dt_date.fromisoformat(end) if end else None

    count = await backfill_trades_to_db(
        api_key=api_key,
        db_session_factory=get_db_session,
        symbol=symbol,
        start=start_date,
        end=end_date,
    )
    return {"status": "ok", "ticks_inserted": count, "symbol": symbol}


@router.get("/ml/prediction")
async def get_ml_prediction():
    """Get latest ML prediction for level touch."""
    from src.ml.serving.predictor import get_predictor
    predictor = get_predictor()
    if not predictor.is_loaded("level_classifier"):
        return {"status": "model_not_loaded", "prediction": None}

    from src.ml.level_touch import get_last_prediction
    prediction = get_last_prediction()
    if prediction:
        return {"status": "ok", "prediction": prediction}
    return {"status": "no_recent_prediction", "prediction": None}


@router.get("/cot")
async def get_cot_data(limit: int = Query(default=4, le=52)):
    """Get latest COT report data."""
    from ...market_data.cot import fetch_cot
    reports = await fetch_cot(limit=limit)
    return [
        {
            "report_date": r.report_date.isoformat(),
            "net_commercial": r.net_commercial,
            "net_non_commercial": r.net_non_commercial,
            "net_non_reportable": r.net_non_reportable,
            "open_interest": r.open_interest,
        }
        for r in reports
    ]
