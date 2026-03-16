"""Trading API routes — thin handlers delegating to TradingService."""

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse

from ..deps import get_db
from ..schemas import (
    TradingAccountUpdate,
    TradingBalanceAdjust,
    RoutineUpdate,
    TradeCreate,
    TradeTransition,
    PartialExitRequest,
    CloseTradeRequest,
    TrailStopRequest,
    AddPositionRequest,
    TradeReviewCreate,
)
from ...services.trading_service import TradingService
from ...config.trading_loader import get_trading_config, get_routine_config

router = APIRouter(prefix="/api/trading", tags=["trading"])


def _svc(db=Depends(get_db)) -> TradingService:
    return TradingService(db)


# ---- Config ----

@router.get("/config")
async def get_config():
    """Full trading config (instruments, setups)."""
    return get_trading_config()


@router.get("/routine/config")
async def get_routine_cfg():
    """Checklist config from YAML."""
    return get_routine_config()


# ---- Accounts ----

@router.get("/accounts")
async def list_accounts(svc: TradingService = Depends(_svc)):
    accounts = svc.seed_accounts()
    return {"accounts": [svc._acct_dict(a) for a in accounts]}


@router.put("/accounts/{account_id}")
async def update_account(account_id: int, data: TradingAccountUpdate, svc: TradingService = Depends(_svc)):
    return svc.update_account(account_id, data.model_dump(exclude_none=True))


@router.post("/accounts/{account_id}/adjust")
async def adjust_balance(account_id: int, data: TradingBalanceAdjust, svc: TradingService = Depends(_svc)):
    return svc.adjust_balance(account_id, data.amount)


@router.post("/accounts/{account_id}/reset-daily")
async def reset_daily(account_id: int, svc: TradingService = Depends(_svc)):
    return svc.reset_daily(account_id)


@router.post("/accounts/{account_id}/reset-weekly")
async def reset_weekly(account_id: int, svc: TradingService = Depends(_svc)):
    return svc.reset_weekly(account_id)


# ---- Routine ----

@router.get("/routine/today")
async def get_today_routine(svc: TradingService = Depends(_svc)):
    return svc.get_or_create_routine()


@router.get("/routine/{date}")
async def get_routine(date: str, svc: TradingService = Depends(_svc)):
    return svc.get_or_create_routine(date)


@router.put("/routine/{date}")
async def update_routine(date: str, data: RoutineUpdate, svc: TradingService = Depends(_svc)):
    return svc.update_routine(date, data.model_dump(exclude_none=True))


# ---- Trades ----

@router.post("/trades")
async def create_trade(data: TradeCreate, svc: TradingService = Depends(_svc)):
    return svc.create_trade(data.model_dump())


@router.get("/trades")
async def list_trades(
    account_id: int | None = None,
    instrument: str | None = None,
    setup_type: str | None = None,
    state: str | None = None,
    limit: int = 200,
    svc: TradingService = Depends(_svc),
):
    trades = svc.repo.list_trades(
        account_id=account_id, instrument=instrument,
        setup_type=setup_type, state=state, limit=limit,
    )
    return {"trades": [svc.trade_dict(t) for t in trades], "count": len(trades)}


@router.get("/trades/unreviewed")
async def get_unreviewed(svc: TradingService = Depends(_svc)):
    trades = svc.repo.get_unreviewed_trades()
    return {"trades": [svc.trade_dict(t) for t in trades], "count": len(trades)}


@router.get("/trades/{trade_id}")
async def get_trade(trade_id: int, svc: TradingService = Depends(_svc)):
    trade = svc.repo.get_trade(trade_id)
    if not trade:
        return {"error": "Trade not found"}
    return svc.trade_dict(trade)


@router.post("/trades/{trade_id}/transition")
async def transition_trade(trade_id: int, data: TradeTransition, svc: TradingService = Depends(_svc)):
    return svc.transition_trade(trade_id, data.to_state, data.notes)


@router.post("/trades/{trade_id}/partial-exit")
async def partial_exit(trade_id: int, data: PartialExitRequest, svc: TradingService = Depends(_svc)):
    return svc.partial_exit(trade_id, data.contracts, data.exit_price, data.notes)


@router.post("/trades/{trade_id}/move-to-be")
async def move_to_be(trade_id: int, svc: TradingService = Depends(_svc)):
    return svc.move_to_be(trade_id)


@router.post("/trades/{trade_id}/trail-stop")
async def trail_stop(trade_id: int, data: TrailStopRequest, svc: TradingService = Depends(_svc)):
    return svc.trail_stop(trade_id, data.new_stop, data.notes)


@router.post("/trades/{trade_id}/add-position")
async def add_position(trade_id: int, data: AddPositionRequest, svc: TradingService = Depends(_svc)):
    return svc.add_position(trade_id, data.contracts, data.entry_price, data.notes)


@router.post("/trades/{trade_id}/close")
async def close_trade(trade_id: int, data: CloseTradeRequest, svc: TradingService = Depends(_svc)):
    return svc.close_trade(trade_id, data.exit_price, data.commission, data.notes)


@router.post("/trades/{trade_id}/review")
async def submit_review(trade_id: int, data: TradeReviewCreate, svc: TradingService = Depends(_svc)):
    return svc.submit_review(trade_id, data.model_dump(exclude_none=True))


# ---- Quick Position Management (Level Monitor integration) ----

@router.post("/trades/{trade_id}/scale")
async def scale_position(trade_id: int, pct: float = Query(default=50), db=Depends(get_db)):
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
async def quick_close_position(trade_id: int, db=Depends(get_db)):
    """Close entire position immediately (no exit price details)."""
    from ...db.models import Trade, TradeEvent
    from datetime import datetime, timezone
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
async def update_stop(trade_id: int, new_stop: float = Query(...), db=Depends(get_db)):
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
async def get_analytics(
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
async def export_csv(
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
async def auto_reset_daily(svc: TradingService = Depends(_svc)):
    return svc.auto_reset_daily()


@router.post("/reset/weekly")
async def auto_reset_weekly(svc: TradingService = Depends(_svc)):
    return svc.auto_reset_weekly()
