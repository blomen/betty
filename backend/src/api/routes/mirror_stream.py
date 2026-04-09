"""Mirror Stream API routes — SSE streams and bootstrap endpoints for two-lane fire window."""

import asyncio
import json
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse
from starlette.requests import Request

from ...mirror.channels import sync_channel, price_channel, action_channel
from ...db.models import BalanceLog, Bet, PriceCache, SettlementQueue, get_session
from ..deps import get_db
from ...services import fire_window as fw

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/mirror", tags=["mirror-stream"])


# ---------------------------------------------------------------------------
# SSE stream endpoints
# ---------------------------------------------------------------------------


@router.get("/stream/sync")
async def stream_sync(request: Request):
    """SSE: balance updates, history syncs, settlements, notifications, provider state."""
    client_id, queue = sync_channel.subscribe()

    async def event_generator():
        try:
            while True:
                try:
                    msg = await asyncio.wait_for(queue.get(), timeout=10.0)
                    yield {"event": msg["event"], "data": json.dumps(msg["data"])}
                except asyncio.TimeoutError:
                    yield {"event": "heartbeat", "data": ""}
        except asyncio.CancelledError:
            pass
        finally:
            sync_channel.unsubscribe(client_id)

    return EventSourceResponse(event_generator(), ping=15)


@router.get("/stream/prices")
async def stream_prices(request: Request):
    """SSE: live odds ticks, price verification results, edge updates."""
    client_id, queue = price_channel.subscribe()

    async def event_generator():
        try:
            while True:
                try:
                    msg = await asyncio.wait_for(queue.get(), timeout=10.0)
                    yield {"event": msg["event"], "data": json.dumps(msg["data"])}
                except asyncio.TimeoutError:
                    yield {"event": "heartbeat", "data": ""}
        except asyncio.CancelledError:
            pass
        finally:
            price_channel.unsubscribe(client_id)

    return EventSourceResponse(event_generator(), ping=15)


@router.get("/stream/actions")
async def stream_actions(request: Request):
    """SSE: navigation events, autofill confirmations, bet placement/skip results."""
    client_id, queue = action_channel.subscribe()

    async def event_generator():
        try:
            while True:
                try:
                    msg = await asyncio.wait_for(queue.get(), timeout=10.0)
                    yield {"event": msg["event"], "data": json.dumps(msg["data"])}
                except asyncio.TimeoutError:
                    yield {"event": "heartbeat", "data": ""}
        except asyncio.CancelledError:
            pass
        finally:
            action_channel.unsubscribe(client_id)

    return EventSourceResponse(event_generator(), ping=15)


# ---------------------------------------------------------------------------
# Bootstrap / state endpoints
# ---------------------------------------------------------------------------


@router.get("/state/{provider_id}")
def get_provider_state(provider_id: str, db=Depends(get_db)):
    """Bootstrap: current balance, pending bets, pending settlements, notification status."""
    # Latest balance
    latest_balance = (
        db.query(BalanceLog)
        .filter(BalanceLog.provider_id == provider_id)
        .order_by(BalanceLog.created_at.desc())
        .first()
    )
    balance = latest_balance.amount if latest_balance else None

    # Pending bets
    pending_bet_rows = (
        db.query(Bet)
        .filter(Bet.provider_id == provider_id, Bet.result == "pending")
        .all()
    )
    pending_bets = [
        {"id": b.id, "event_id": b.event_id, "market": b.market,
         "outcome": b.outcome, "odds": b.odds, "stake": b.stake}
        for b in pending_bet_rows
    ]

    # Pending settlements
    settle_rows = (
        db.query(SettlementQueue)
        .filter(
            SettlementQueue.provider_id == provider_id,
            SettlementQueue.status == "pending",
        )
        .all()
    )
    pending_settlements = [
        {"id": s.id, "bet_id": s.bet_id, "result": s.result,
         "payout": s.payout, "detected_at": s.detected_at.isoformat() if s.detected_at else None}
        for s in settle_rows
    ]

    return {
        "provider_id": provider_id,
        "balance": balance,
        "pending_bets": pending_bets,
        "pending_settlements": pending_settlements,
        "notification_status": "ok",
    }


@router.get("/prices/{provider_id}")
def get_cached_prices(provider_id: str, db=Depends(get_db)):
    """Bootstrap: cached live prices from PriceCache table for a provider."""
    rows = (
        db.query(PriceCache)
        .filter(PriceCache.provider_id == provider_id)
        .all()
    )
    return {
        "provider_id": provider_id,
        "prices": [
            {
                "event_id": r.event_id,
                "market": r.market,
                "outcome": r.outcome,
                "odds": r.odds,
                "source": r.source,
                "updated_at": r.updated_at.isoformat() if r.updated_at else None,
            }
            for r in rows
        ],
    }


@router.get("/queue")
def get_provider_queue():
    """Bootstrap: current provider queue from active fire window state."""
    window = fw.get_window()
    if not window:
        return {"queue": [], "current_provider": None, "status": "no_window"}
    return {
        "queue": window.provider_queue,
        "current_provider": window.current_provider,
        "status": window.status,
    }


# ---------------------------------------------------------------------------
# Settlement confirm
# ---------------------------------------------------------------------------


class SettlementConfirmRequest(BaseModel):
    provider_id: str


@router.post("/settlements/confirm-queue")
def confirm_settlement_queue(req: SettlementConfirmRequest, db=Depends(get_db)):
    """Confirm pending SettlementQueue entries — update bets + broadcast."""
    pending = (
        db.query(SettlementQueue)
        .filter(
            SettlementQueue.provider_id == req.provider_id,
            SettlementQueue.status == "pending",
        )
        .all()
    )

    if not pending:
        return {"confirmed": 0, "provider_id": req.provider_id}

    now = datetime.now(timezone.utc)
    confirmed = 0

    for settlement in pending:
        settlement.status = "confirmed"
        settlement.confirmed_at = now

        # Update corresponding Bet row if linked
        if settlement.bet_id is not None:
            bet = db.query(Bet).filter(Bet.id == settlement.bet_id).first()
            if bet:
                bet.result = settlement.result
                bet.payout = settlement.payout
                bet.settled_at = now

        confirmed += 1

    db.flush()

    # Broadcast settlement_confirmed via sync_channel
    sync_channel.publish(
        "settlement_confirmed",
        {
            "provider_id": req.provider_id,
            "confirmed": confirmed,
        },
    )

    return {"confirmed": confirmed, "provider_id": req.provider_id}
