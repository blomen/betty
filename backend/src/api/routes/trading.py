"""Trading API routes — thin handlers delegating to TradingService."""

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from ...config.trading_loader import get_routine_config, get_trading_config
from ...services.trading_service import TradingService
from ..deps import get_db
from ..schemas import (
    AddPositionRequest,
    CloseTradeRequest,
    PartialExitRequest,
    RoutineUpdate,
    TradeCreate,
    TradeReviewCreate,
    TradeTransition,
    TradingAccountUpdate,
    TradingBalanceAdjust,
    TrailStopRequest,
)

router = APIRouter(prefix="/api/trading", tags=["trading"])


def _svc(db=Depends(get_db)) -> TradingService:
    return TradingService(db)


# ---- Config ----


@router.get("/config")
def get_config():
    """Full trading config (instruments, setups)."""
    return get_trading_config()


@router.get("/routine/config")
def get_routine_cfg():
    """Checklist config from YAML."""
    return get_routine_config()


# ---- Accounts ----


@router.get("/accounts")
def list_accounts(svc: TradingService = Depends(_svc)):
    accounts = svc.seed_accounts()
    return {"accounts": [svc._acct_dict(a) for a in accounts]}


@router.put("/accounts/{account_id}")
def update_account(account_id: int, data: TradingAccountUpdate, svc: TradingService = Depends(_svc)):
    return svc.update_account(account_id, data.model_dump(exclude_none=True))


@router.post("/accounts/{account_id}/adjust")
def adjust_balance(account_id: int, data: TradingBalanceAdjust, svc: TradingService = Depends(_svc)):
    return svc.adjust_balance(account_id, data.amount)


@router.post("/accounts/{account_id}/reset-daily")
def reset_daily(account_id: int, svc: TradingService = Depends(_svc)):
    return svc.reset_daily(account_id)


@router.post("/accounts/{account_id}/reset-weekly")
def reset_weekly(account_id: int, svc: TradingService = Depends(_svc)):
    return svc.reset_weekly(account_id)


# ---- Routine ----


@router.get("/routine/today")
def get_today_routine(svc: TradingService = Depends(_svc)):
    return svc.get_or_create_routine()


@router.get("/routine/{date}")
def get_routine(date: str, svc: TradingService = Depends(_svc)):
    return svc.get_or_create_routine(date)


@router.put("/routine/{date}")
def update_routine(date: str, data: RoutineUpdate, svc: TradingService = Depends(_svc)):
    return svc.update_routine(date, data.model_dump(exclude_none=True))


# ---- Trades ----


@router.post("/trades")
def create_trade(data: TradeCreate, svc: TradingService = Depends(_svc)):
    return svc.create_trade(data.model_dump())


@router.get("/trades")
def list_trades(
    account_id: int | None = None,
    instrument: str | None = None,
    setup_type: str | None = None,
    state: str | None = None,
    limit: int = 200,
    svc: TradingService = Depends(_svc),
):
    trades = svc.repo.list_trades(
        account_id=account_id,
        instrument=instrument,
        setup_type=setup_type,
        state=state,
        limit=limit,
    )
    return {"trades": [svc.trade_dict(t) for t in trades], "count": len(trades)}


@router.get("/trades/unreviewed")
def get_unreviewed(svc: TradingService = Depends(_svc)):
    trades = svc.repo.get_unreviewed_trades()
    return {"trades": [svc.trade_dict(t) for t in trades], "count": len(trades)}


@router.get("/trades/{trade_id}")
def get_trade(trade_id: int, svc: TradingService = Depends(_svc)):
    trade = svc.repo.get_trade(trade_id)
    if not trade:
        return {"error": "Trade not found"}
    return svc.trade_dict(trade)


@router.post("/trades/{trade_id}/transition")
def transition_trade(trade_id: int, data: TradeTransition, svc: TradingService = Depends(_svc)):
    return svc.transition_trade(trade_id, data.to_state, data.notes)


@router.post("/trades/{trade_id}/partial-exit")
def partial_exit(trade_id: int, data: PartialExitRequest, svc: TradingService = Depends(_svc)):
    return svc.partial_exit(trade_id, data.contracts, data.exit_price, data.notes)


@router.post("/trades/{trade_id}/move-to-be")
def move_to_be(trade_id: int, svc: TradingService = Depends(_svc)):
    return svc.move_to_be(trade_id)


@router.post("/trades/{trade_id}/trail-stop")
def trail_stop(trade_id: int, data: TrailStopRequest, svc: TradingService = Depends(_svc)):
    return svc.trail_stop(trade_id, data.new_stop, data.notes)


@router.post("/trades/{trade_id}/add-position")
def add_position(trade_id: int, data: AddPositionRequest, svc: TradingService = Depends(_svc)):
    return svc.add_position(trade_id, data.contracts, data.entry_price, data.notes)


@router.post("/trades/{trade_id}/close")
def close_trade(trade_id: int, data: CloseTradeRequest, svc: TradingService = Depends(_svc)):
    return svc.close_trade(trade_id, data.exit_price, data.commission, data.notes)


@router.post("/trades/{trade_id}/review")
def submit_review(trade_id: int, data: TradeReviewCreate, svc: TradingService = Depends(_svc)):
    return svc.submit_review(trade_id, data.model_dump(exclude_none=True))


# ---- Quick Position Management (Level Monitor integration) ----


@router.post("/trades/{trade_id}/scale")
def scale_position(trade_id: int, pct: float = Query(default=50), db=Depends(get_db)):
    """Scale out of a position by percentage. Creates TradeEvent(partial_exit)."""
    from ...db.models import Trade, TradeEvent

    trade = db.query(Trade).get(trade_id)
    if not trade or trade.state == "closed":
        raise HTTPException(404, "Trade not found or closed")

    exit_contracts = max(1, int(trade.contracts * pct / 100))
    event = TradeEvent(
        trade_id=trade_id,
        event_type="partial_exit",
        details={"contracts": exit_contracts, "pct": pct},
        notes=f"Scale out {pct}%",
    )
    db.add(event)

    # Move stop to breakeven after first scale
    if trade.be_price is None:
        trade.be_price = trade.entry_price
        trade.stop_price = trade.entry_price
        be_event = TradeEvent(trade_id=trade_id, event_type="move_to_be", details={"new_stop": trade.entry_price})
        db.add(be_event)

    db.commit()
    return {"success": True, "remaining_contracts": trade.contracts - exit_contracts}


@router.post("/trades/{trade_id}/quick-close")
def quick_close_position(trade_id: int, db=Depends(get_db)):
    """Close entire position immediately (no exit price details)."""
    from datetime import datetime, timezone

    from ...db.models import Trade, TradeEvent

    trade = db.query(Trade).get(trade_id)
    if not trade:
        raise HTTPException(404, "Trade not found")
    old_state = trade.state
    trade.state = "closed"
    trade.closed_at = datetime.now(timezone.utc)
    event = TradeEvent(trade_id=trade_id, event_type="transition", details={"from": old_state, "to": "closed"})
    db.add(event)
    db.commit()
    return {"success": True}


@router.post("/trades/{trade_id}/stop")
def update_stop(trade_id: int, new_stop: float = Query(...), db=Depends(get_db)):
    """Update stop price for a trade."""
    from ...db.models import Trade, TradeEvent

    trade = db.query(Trade).get(trade_id)
    if not trade:
        raise HTTPException(404, "Trade not found")
    old_stop = trade.stop_price
    trade.stop_price = new_stop
    event = TradeEvent(trade_id=trade_id, event_type="trail_stop", details={"old": old_stop, "new": new_stop})
    db.add(event)
    db.commit()
    return {"success": True}


# ---- Analytics ----


@router.get("/analytics")
def get_analytics(
    account_id: int | None = None,
    instrument: str | None = None,
    setup_type: str | None = None,
    svc: TradingService = Depends(_svc),
):
    filters = {}
    if account_id:
        filters["account_id"] = account_id
    if instrument:
        filters["instrument"] = instrument
    if setup_type:
        filters["setup_type"] = setup_type
    return svc.get_analytics(filters or None)


# ---- CSV Export ----


@router.get("/export/csv")
def export_csv(
    state: str | None = None,
    account_id: int | None = None,
    instrument: str | None = None,
    svc: TradingService = Depends(_svc),
):
    filters = {}
    if state:
        filters["state"] = state
    if account_id:
        filters["account_id"] = account_id
    if instrument:
        filters["instrument"] = instrument
    csv_data = svc.export_trades_csv(filters or None)
    return StreamingResponse(
        iter([csv_data]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=trades.csv"},
    )


# ---- Auto-reset ----


@router.post("/reset/daily")
def auto_reset_daily(svc: TradingService = Depends(_svc)):
    return svc.auto_reset_daily()


@router.post("/reset/weekly")
def auto_reset_weekly(svc: TradingService = Depends(_svc)):
    return svc.auto_reset_weekly()


@router.get("/signals")
def get_signals(date: str = Query(None, description="Date YYYY-MM-DD, defaults to today")):
    """Get all specialist signals for a given session date."""
    import json
    from datetime import datetime, timezone
    from pathlib import Path

    if date is None:
        date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    signals_dir = Path("data/rl/signals")
    filepath = signals_dir / f"{date}.jsonl"

    if not filepath.exists():
        return {"date": date, "signals": [], "count": 0}

    signals = []
    with open(filepath) as f:
        for line in f:
            line = line.strip()
            if line:
                signals.append(json.loads(line))

    return {"date": date, "signals": signals, "count": len(signals)}


@router.get("/broker/status")
def broker_status(request: Request):
    """Get current broker state — position, P&L, risk status."""
    adapter = getattr(request.app.state, "broker_adapter", None)
    if adapter is None:
        return {"enabled": False, "message": "Broker not enabled"}

    t = adapter.tracker
    return {
        "enabled": True,
        "halted": adapter._halted,
        "halt_reason": adapter._halt_reason,
        "position": {
            "side": t.side,
            "size": t.size,
            "entry_price": t.entry_price,
            "stop_price": t.stop_price,
        },
        "session": {
            "pnl_dollars": round(t.session_pnl, 2),
            "peak_equity": round(t.peak_equity, 2),
            "trailing_dd": round(t.trailing_dd, 2),
            "trade_count": t.trade_count,
            "consecutive_stops": t.consecutive_stops,
            "avg_slippage_ticks": round(t.slippage_ticks(), 2),
        },
    }


async def _send_command(request: Request, cmd: str, **kwargs) -> dict:
    """Send a command to the trading_service via the signals WS and wait for result."""
    ws_client = getattr(request.app.state, "_signals_ws_client", None)
    if ws_client is None:
        raise HTTPException(503, "Trading service not connected")
    cmd_id = str(uuid.uuid4())[:8]
    if not hasattr(request.app.state, "_pending_commands"):
        request.app.state._pending_commands = {}
    fut = asyncio.get_event_loop().create_future()
    request.app.state._pending_commands[cmd_id] = fut
    try:
        await ws_client.send_json({"type": "command", "cmd": cmd, "cmd_id": cmd_id, **kwargs})
        return await asyncio.wait_for(fut, timeout=10.0)
    except asyncio.TimeoutError:
        request.app.state._pending_commands.pop(cmd_id, None)
        raise HTTPException(504, "Trading service did not respond")


@router.post("/broker/flatten")
async def broker_flatten(request: Request):
    """Emergency flatten — liquidate position and cancel all orders."""
    return await _send_command(request, "flatten")


@router.get("/broker/orders")
async def broker_orders(request: Request):
    """Get open orders from TopstepX."""
    return await _send_command(request, "get_orders")


@router.post("/broker/cancel-order/{order_id}")
async def broker_cancel_order(request: Request, order_id: int):
    """Cancel a specific order."""
    return await _send_command(request, "cancel_order", order_id=order_id)
