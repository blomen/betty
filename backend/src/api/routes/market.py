"""Market data API routes — AMT session analysis and scanner signals."""

from fastapi import APIRouter, Depends, Query, Request, Response
from sse_starlette.sse import EventSourceResponse
import asyncio, json, time

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


# Pre-serialized candle cache: {cache_key: (json_bytes, expiry)}
_candle_json_cache: dict[tuple, tuple] = {}

@router.get("/candles")
async def get_candles(
    response: Response,
    symbol: str = Query(default="NQ"),
    interval: str = Query(default="5m", pattern="^(1m|5m|15m)$"),
    date: str = Query(default=None),
    days: int = Query(default=5, ge=1, le=365),
    svc: MarketService = Depends(_svc),
):
    """Return OHLCV candles for charting from market_candles DB."""
    import time as _time
    cache_key = (symbol, interval, date, days)
    cached = _candle_json_cache.get(cache_key)
    now = _time.time()
    if cached and now < cached[1]:
        return Response(content=cached[0], media_type="application/json",
                        headers={"Cache-Control": "max-age=15"})

    data = await svc.get_candles(symbol, interval, date, days)
    serialized = json.dumps(data, separators=(",", ":"))
    _candle_json_cache[cache_key] = (serialized, now + 15)
    response.headers["Cache-Control"] = "max-age=15"
    return data


@router.get("/vwap")
async def get_developing_vwap(
    symbol: str = Query(default="NQ"),
    interval: str = Query(default="1m", pattern="^(1m|5m)$"),
    svc: MarketService = Depends(_svc),
):
    """Return developing VWAP time series from tick data (RTH only)."""
    return await svc.get_developing_vwap(symbol, interval)


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

        # Pass session context for DQN live inference
        # The compute_session return dict contains the VWAP, VP, TPO, and level data
        session = data.get("session", {})
        rl_context = {
            "vwap_bands": {
                "vwap": session.get("vwap"),
                "upper_1": session.get("vwap_1sd_upper"),
                "lower_1": session.get("vwap_1sd_lower"),
                "upper_2": session.get("vwap_2sd_upper"),
                "lower_2": session.get("vwap_2sd_lower"),
                "upper_3": session.get("vwap_3sd_upper"),
                "lower_3": session.get("vwap_3sd_lower"),
            } if session.get("vwap") else None,
            "volume_profile": session.get("volume_profile"),
            "session_levels": session.get("session_levels"),
            "session_tpos": session.get("session_tpos_obj"),
            "tpo_profile": session.get("tpo"),
            "session_context": session.get("session_context"),
            "macro": session.get("macro"),
            "day_type": session.get("day_type"),
            "fvgs": [],
            "single_print_zones": [],
        }
        level_monitor.set_session_context(rl_context)

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


@router.get("/status")
async def market_status(request: Request):
    """Check if Globex is open and trading features are active.

    Frontend can use this to skip SSE/data calls when market is closed.
    """
    from ...services.market_service import MarketService
    from ...market_data.stream import DatabentoLiveStream

    closed = MarketService._is_globex_closed()
    stream = _get_live_stream(request)
    stream_running = stream._running if stream else False

    result = {
        "globex_open": not closed,
        "stream_active": stream_running,
    }
    if closed:
        secs = DatabentoLiveStream._seconds_until_globex_open()
        result["opens_in_seconds"] = int(secs)
        result["opens_in_hours"] = round(secs / 3600, 1)
    return result


@router.get("/stream")
async def market_stream(request: Request, symbol: str = "NQ"):
    """SSE stream of real-time tick data, candles, and level touches.

    Uses a polling model: the stream thread writes to shared state,
    and this generator polls it every 500ms. This avoids call_soon_threadsafe
    which was overwhelming the Windows ProactorEventLoop.
    """
    stream = _get_live_stream(request)
    if not stream:
        return {"error": "Live stream not available"}

    state = stream.get_shared_state()

    async def event_generator():
        versions: dict[str, int] = {}
        event_seq = 0
        last_yield = time.monotonic()
        try:
            while True:
                events, versions, event_seq = state.poll(versions, event_seq)
                for event in events:
                    event_type = event.get("type", "tick")
                    yield {"event": event_type, "data": json.dumps(event)}
                    last_yield = time.monotonic()
                if not events and time.monotonic() - last_yield > 30:
                    yield {"event": "heartbeat", "data": "{}"}
                    last_yield = time.monotonic()
                await asyncio.sleep(0.5)
        except asyncio.CancelledError:
            pass

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


@router.get("/levels/replay")
async def get_replay_levels(
    date: str = Query(description="Session date YYYY-MM-DD"),
):
    """Replay a historical session and return all computed levels for visual verification.

    This endpoint runs the RL ReplayEngine on historical tick data and returns
    the exact session levels, VWAP, VP, FVGs, OBs, and swing points that the
    RL agent would see during training — enabling visual verification before training.
    """
    import json
    from pathlib import Path
    from datetime import datetime as dt_cls

    # Check for pre-computed JSON first (from CLI verify-levels)
    data_dir = Path(__file__).resolve().parents[3] / "data" / "rl"
    cached = data_dir / f"levels_{date}.json"
    if cached.exists():
        with open(cached) as f:
            return json.load(f)

    # Otherwise replay on-the-fly from parquet
    try:
        import pandas as pd
        from zoneinfo import ZoneInfo
        from ...rl.data.fetcher import TICKS_DIR
        from ...rl.data.replay_engine import ReplayEngine

        target = pd.Timestamp(date)
        month_str = target.strftime("%Y-%m")
        pfile = TICKS_DIR / f"NQ_{month_str}.parquet"

        if not pfile.exists():
            return {"error": f"No tick data for {month_str}. Run 'rl fetch' first."}

        df = pd.read_parquet(pfile)
        df["_date"] = pd.to_datetime(df["timestamp"]).dt.date
        target_date = target.date()
        day_df = df[df["_date"] == target_date].drop(columns=["_date"])

        if day_df.empty:
            return {"error": f"No ticks for {date}"}

        ticks = day_df.rename(columns={"timestamp": "ts"}).to_dict(orient="records")

        _ET = ZoneInfo("US/Eastern")
        session_dt = dt_cls(target_date.year, target_date.month, target_date.day, 12, 0, 0, tzinfo=_ET)

        engine = ReplayEngine()
        episodes = engine.replay_session(ticks, session_dt)
        snapshot = engine.get_level_snapshot()

        snapshot["episodes_count"] = len(episodes)
        snapshot["ticks_count"] = len(ticks)
        snapshot["date"] = date

        return snapshot
    except ImportError:
        return {"error": "pandas not available — use CLI 'rl verify-levels' instead"}
    except Exception as e:
        return {"error": str(e)}


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
    response: Response,
    symbol: str = Query(default="NQ"),
    timeframe: str = Query(default="session", pattern="^(session|weekly|monthly)$"),
    svc: MarketService = Depends(_svc),
):
    """Return VP curve (price→volume pairs) for session/weekly/monthly."""
    response.headers["Cache-Control"] = f"max-age={30 if timeframe == 'session' else 120}"
    return await svc.get_volume_profile_curve(symbol, timeframe=timeframe)


@router.get("/session-levels")
async def get_session_levels(
    response: Response,
    symbol: str = Query(default="NQ"),
    days: int = Query(default=5, ge=1, le=30),
    svc: MarketService = Depends(_svc),
):
    """Return per-day session levels (PDH/PDL, IB, Tokyo, London) with time boundaries."""
    response.headers["Cache-Control"] = "max-age=30"
    return await svc.get_session_levels(symbol, days)


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


@router.post("/backfill-candles")
async def backfill_candles(
    symbol: str = Query(default="NQ"),
    days: int = Query(default=1, ge=1, le=30, description="Days to scan for gaps"),
    db=Depends(get_db),
):
    """Detect and backfill candle gaps from Databento historical.

    Scans the last N days of 1m and 5m candles for gaps and fills them.
    Returns synchronously (not fire-and-forget) so you can see results/errors.
    """
    import os
    from datetime import datetime, timedelta, timezone

    api_key = os.environ.get("DATABENTO_API_KEY")
    if not api_key:
        return {"error": "DATABENTO_API_KEY not set"}

    from ...market_data.databento_provider import DabentoProvider
    from ...config.trading_loader import get_market_data_config
    from ...repositories.market_repo import MarketRepo
    from ...db.models import get_session as get_db_session

    config = get_market_data_config()
    db_symbol = config.get("symbol", "NQ.v.0")
    provider = DabentoProvider(config)

    now = datetime.now(timezone.utc)
    lookback_start = now - timedelta(days=days)
    # Databento historical has ~15 min delay
    fetch_end = now - timedelta(minutes=15)

    repo = MarketRepo(db)
    results = {}

    for interval in ("1m", "5m"):
        bucket_s = 60 if interval == "1m" else 300
        max_gap = bucket_s * 3

        rows = repo.get_candles(symbol, interval, lookback_start, now)
        if len(rows) < 2:
            results[interval] = {"status": "insufficient_data", "rows": len(rows)}
            continue

        # Find gaps
        gaps = []
        for i in range(1, len(rows)):
            ts_prev = rows[i - 1].ts if rows[i - 1].ts.tzinfo else rows[i - 1].ts.replace(tzinfo=timezone.utc)
            ts_curr = rows[i].ts if rows[i].ts.tzinfo else rows[i].ts.replace(tzinfo=timezone.utc)
            diff = (ts_curr - ts_prev).total_seconds()
            if diff > max_gap and ts_curr < fetch_end:
                gaps.append({"start": ts_prev.isoformat(), "end": ts_curr.isoformat(), "minutes": round(diff / 60)})

        if not gaps:
            results[interval] = {"status": "no_gaps", "candles_scanned": len(rows)}
            continue

        # Backfill each gap
        total_inserted = 0
        gap_details = []
        for gap in gaps:
            start_dt = datetime.fromisoformat(gap["start"])
            end_dt = datetime.fromisoformat(gap["end"])
            try:
                bars = await asyncio.wait_for(
                    provider.get_bars(db_symbol, interval, start_dt, end_dt),
                    timeout=120.0,
                )
                if bars:
                    write_db = get_db_session()
                    try:
                        count = MarketRepo(write_db).bulk_insert_candles(symbol, interval, bars)
                        total_inserted += count
                        gap_details.append({**gap, "fetched": len(bars), "inserted": count})
                    finally:
                        write_db.close()
                else:
                    gap_details.append({**gap, "fetched": 0, "inserted": 0, "note": "no data from Databento"})
            except Exception as e:
                gap_details.append({**gap, "error": str(e)})

        results[interval] = {
            "status": "backfilled",
            "gaps_found": len(gaps),
            "total_inserted": total_inserted,
            "gaps": gap_details,
        }

    # Clear candle cache so next request serves fresh data
    MarketService._candle_cache.clear()

    return {"symbol": symbol, "days_scanned": days, "results": results}


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


@router.get("/ml/health")
async def get_ml_health():
    """Get ML level classifier model health and training stats."""
    import json
    from src.ml.serving.predictor import get_predictor
    from src.db.models import get_session, LevelTouchOutcome
    from sqlalchemy import func

    result = {
        "model_loaded": False,
        "version": None,
        "training_data_count": 0,
        "validation_score": None,
        "baseline_metric": None,
        "class_distribution": {},
        "recent_accuracy": {},
        "top_features": [],
        "use_fallback": False,
        "trained_at": None,
    }

    predictor = get_predictor()
    if predictor.is_loaded("level_classifier"):
        result["model_loaded"] = True
        model_data = predictor.models.get("level_classifier", {})
        model_obj = model_data.get("model")
        classes = model_data.get("classes", [])
        feature_names = model_data.get("feature_names", [])
        result["use_fallback"] = model_data.get("use_fallback", False)

        # Feature importances from LightGBM
        if model_obj and hasattr(model_obj, "feature_importances_"):
            importances = model_obj.feature_importances_
            pairs = sorted(
                zip(feature_names, importances),
                key=lambda x: x[1], reverse=True,
            )
            total = sum(importances) or 1
            result["top_features"] = [
                {"name": n, "importance": round(float(v / total), 4)}
                for n, v in pairs[:10]
            ]

    # Class distribution + recent accuracy from DB
    try:
        db = get_session()
        try:
            # Class distribution
            dist = (
                db.query(LevelTouchOutcome.outcome, func.count())
                .filter(LevelTouchOutcome.outcome.isnot(None))
                .group_by(LevelTouchOutcome.outcome)
                .all()
            )
            result["class_distribution"] = {cls: cnt for cls, cnt in dist}
            result["training_data_count"] = sum(cnt for _, cnt in dist)

            # Recent accuracy (prediction vs actual on last 50)
            recent = (
                db.query(LevelTouchOutcome.prediction, LevelTouchOutcome.outcome)
                .filter(
                    LevelTouchOutcome.outcome.isnot(None),
                    LevelTouchOutcome.prediction.isnot(None),
                )
                .order_by(LevelTouchOutcome.touch_ts.desc())
                .limit(50)
                .all()
            )
            if recent:
                correct = sum(1 for pred, actual in recent if pred == actual)
                result["recent_accuracy"] = {
                    "last_50": round(correct / len(recent), 3),
                    "sample_count": len(recent),
                }
        finally:
            db.close()
    except Exception:
        pass

    return result


@router.get("/tpo")
async def get_tpo_history(
    symbol: str = Query("NQ"),
    days: int = Query(30, ge=1, le=365),
    svc: MarketService = Depends(_svc),
):
    """Historical TPO sessions for RL batch access."""
    sessions = svc.get_tpo_history(symbol=symbol, days=days)
    return {"sessions": sessions, "symbol": symbol, "count": len(sessions)}


@router.get("/tpo/live")
async def get_tpo_live(
    symbol: str = Query("NQ"),
    svc: MarketService = Depends(_svc),
):
    """Today's developing TPO profile."""
    return svc.get_tpo_live(symbol=symbol)


@router.get("/tpo/sessions")
async def get_tpo_sessions(
    symbol: str = Query("NQ"),
    svc: MarketService = Depends(_svc),
):
    """Per-session TPO profiles (Tokyo/London/NY) with letter grids for chart visualization."""
    return svc.get_session_tpos(symbol=symbol)


@router.post("/tpo/backfill")
async def backfill_tpo(
    symbol: str = Query("NQ"),
    days: int = Query(30, ge=1, le=365),
    svc: MarketService = Depends(_svc),
):
    """Backfill historical TPO sessions from existing 1m bar data."""
    stored = svc.backfill_tpo_sessions(symbol=symbol, days=days)
    return {"stored": stored, "symbol": symbol, "days_checked": days}


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
