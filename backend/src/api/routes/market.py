"""Market data API routes — AMT session analysis and scanner signals."""

from fastapi import APIRouter, Depends, Query
from sse_starlette.sse import EventSourceResponse
import asyncio, json

from ..deps import get_db
from ...services.market_service import MarketService

# Module-level singleton for live stream
_live_stream = None

def _get_live_stream():
    """Get or create the singleton DatabentoLiveStream."""
    global _live_stream
    if _live_stream is None:
        import os
        api_key = os.environ.get("DATABENTO_API_KEY")
        if not api_key:
            return None
        from src.market_data.stream import DatabentoLiveStream
        _live_stream = DatabentoLiveStream(api_key=api_key)
    return _live_stream

router = APIRouter(prefix="/api/trading/market", tags=["market"])


def _svc(db=Depends(get_db)) -> MarketService:
    return MarketService(db)


@router.get("/session")
async def get_current_session(svc: MarketService = Depends(_svc)):
    """Get today's computed session data (POC, VAH, VAL, VWAP, IB, delta, etc.)."""
    data = svc.get_current_session()
    if not data:
        return {"status": "no_data", "message": "No session computed yet. POST /compute first."}
    return data


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
    date: str = Query(default=None, description="Date to compute (YYYY-MM-DD, default today)"),
    svc: MarketService = Depends(_svc),
):
    """Fetch market data and compute AMT analysis for a date."""
    data = await svc.compute_session(date)
    return data


@router.get("/history")
async def get_session_history(
    limit: int = Query(default=30, le=100),
    svc: MarketService = Depends(_svc),
):
    """Get historical session data."""
    return {"sessions": svc.get_session_history(limit=limit)}


@router.get("/confirmations")
async def get_confirmations(svc: MarketService = Depends(_svc)):
    """Get auto-evaluated confirmation gates for trading."""
    return svc.get_confirmations()


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
async def market_stream(symbol: str = "NQ"):
    """SSE stream of real-time tick data, candles, and level touches."""
    stream = _get_live_stream()
    if not stream:
        return {"error": "Live stream not available"}

    queue = stream.subscribe()

    async def event_generator():
        try:
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=30)
                    yield {"event": "tick", "data": json.dumps(event)}
                except asyncio.TimeoutError:
                    yield {"event": "heartbeat", "data": "{}"}
        except asyncio.CancelledError:
            stream.unsubscribe(queue)

    return EventSourceResponse(event_generator())


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
        "vp_old_macro_start": ctx.vp_old_macro_start,
        "vp_ongoing_macro_start": ctx.vp_ongoing_macro_start,
        "vp_leg_start": ctx.vp_leg_start,
    }


@router.put("/context")
async def update_context(data: dict, symbol: str = "NQ", svc: MarketService = Depends(_svc)):
    svc.repo.upsert_context(symbol, data)
    return {"status": "ok", "symbol": symbol}
