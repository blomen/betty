"""Opportunities API routes - value betting opportunities."""

import logging
import time as _time
from datetime import datetime, timezone
from typing import Optional
from fastapi import APIRouter, HTTPException, Depends, Response
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel
from sqlalchemy.orm import Session
import json

from ...services import OpportunityService
from ...services.play_service import PlaySessionService
from ...services.batch_builder import BatchBuilder
from ...repositories import ProfileRepo
from ..deps import get_db
from ..schemas import BonusMatchRequest

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Batch lock cache (in-memory, 30-minute TTL)
# ---------------------------------------------------------------------------
_locked_batches: dict[int, dict] = {}  # profile_id -> {"batch": [...], "locked_at": datetime}
LOCK_TTL_SECONDS = 1800  # 30 minutes


router = APIRouter(prefix="/api/opportunities", tags=["opportunities"])

# Response-level TTL cache for opportunities listing
# Key: (type, provider1, provider2, providers, market, sport, min_value, limit)
# Value: (pre-serialized JSON bytes, expiry_time)
_opp_cache: dict[tuple, tuple] = {}
_OPP_CACHE_TTL = 120  # seconds — data only changes on extraction (every 5 min)


def _get_service(db: Session = Depends(get_db)) -> OpportunityService:
    return OpportunityService(db)


@router.get("")
def list_opportunities(
    response: Response,
    type: Optional[str] = None,
    active_only: bool = True,
    provider1: Optional[str] = None,
    provider2: Optional[str] = None,
    providers: Optional[str] = None,
    market: Optional[str] = None,
    sport: Optional[str] = None,
    min_value: Optional[float] = None,
    limit: int = 2000,
    service: OpportunityService = Depends(_get_service),
):
    """Get current value/bonus opportunities with enhanced filtering and stake recommendations."""
    cache_key = (type, provider1, provider2, providers, market, sport, min_value, limit)
    cached = _opp_cache.get(cache_key)
    now = _time.time()
    if cached and now < cached[1]:
        return Response(
            content=cached[0],
            media_type="application/json",
            headers={"Cache-Control": f"max-age={_OPP_CACHE_TTL}, stale-while-revalidate=60"},
        )

    result = service.list_opportunities(
        type=type,
        provider1=provider1,
        provider2=provider2,
        providers=providers,
        market=market,
        sport=sport,
        min_value=min_value,
        limit=min(limit, 2000),
    )
    # Pre-serialize to avoid repeated jsonable_encoder + json.dumps on cache hits
    serialized = json.dumps(jsonable_encoder(result), ensure_ascii=False, separators=(",", ":"))
    _opp_cache[cache_key] = (serialized, now + _OPP_CACHE_TTL)
    response.headers["Cache-Control"] = f"max-age={_OPP_CACHE_TTL}, stale-while-revalidate=60"
    return result


@router.get("/clusters")
def list_clusters(
    service: OpportunityService = Depends(_get_service),
):
    """Get all available provider clusters with balance info."""
    return {"clusters": service.get_clusters()}


@router.get("/cluster-summary")
def cluster_summary(
    cluster: str,
    service: OpportunityService = Depends(_get_service),
):
    """Get provider status summary for a cluster (balance, wagering, limits)."""
    return service.get_cluster_summary(cluster)


@router.post("/bonus/match")
def match_bonus_bet(
    data: BonusMatchRequest,
    service: OpportunityService = Depends(_get_service),
):
    """Find the best hedge for a bonus bet."""
    result = service.find_hedge(
        event_id=data.event_id,
        market=data.market,
        anchor_provider=data.anchor_provider,
        anchor_outcome=data.anchor_outcome,
        anchor_odds=data.anchor_odds,
        anchor_stake=data.anchor_stake,
        counterpart_providers=data.counterpart_providers,
        is_free_bet=data.is_free_bet,
    )

    if not result:
        raise HTTPException(
            404,
            "No suitable hedge found (all hedges are same-provider or no valid options)"
        )

    return result


@router.get("/dutch-workflow")
def dutch_workflow(
    providers: str,
    major_only: bool = False,
    counterpart_providers: Optional[str] = None,
    limit: int = 50,
    service: OpportunityService = Depends(_get_service),
):
    """Live-scan dutch opportunities for specific anchor providers."""
    provider_list = [p.strip() for p in providers.split(",") if p.strip()]
    if not provider_list:
        raise HTTPException(400, "At least one provider required")
    counterpart_list = (
        [p.strip() for p in counterpart_providers.split(",") if p.strip()]
        if counterpart_providers else None
    )
    return service.scan_dutch_workflow(
        anchor_providers=provider_list,
        major_only=major_only,
        counterpart_providers=counterpart_list,
        limit=min(limit, 100),
    )


@router.get("/bonus/scan")
def scan_bonus_opportunities(
    anchor_provider: str,
    limit: int = 10,
    include_negative: bool = True,
    service: OpportunityService = Depends(_get_service),
):
    """Scan for bonus opportunities at anchor provider vs Pinnacle."""
    return service.scan_bonus(
        anchor_provider=anchor_provider,
        limit=limit,
        include_negative=include_negative,
    )


@router.get("/play/session")
def get_play_session(db: Session = Depends(get_db)):
    """Get session data for Play panel: clusters, siblings, lifecycle states."""
    profile_repo = ProfileRepo(db)
    profile = profile_repo.get_active()

    service = PlaySessionService(db)
    return service.get_session(profile.id)


@router.get("/play/pending-bets")
def get_pending_bets(db: Session = Depends(get_db)):
    """Get all pending (unsettled) bets grouped by provider."""
    from ...db.models import Bet, Event
    from ...repositories import ProfileRepo

    profile_repo = ProfileRepo(db)
    profile = profile_repo.get_active()

    pending = (
        db.query(Bet)
        .filter(Bet.profile_id == profile.id, Bet.result == "pending")
        .order_by(Bet.provider_id, Bet.placed_at)
        .all()
    )

    # Build event name lookup
    event_ids = {b.event_id for b in pending if b.event_id}
    events = {}
    if event_ids:
        for ev in db.query(Event).filter(Event.id.in_(event_ids)).all():
            events[ev.id] = f"{ev.home_team} vs {ev.away_team}" if ev.home_team and ev.away_team else ev.id

    # Group by provider
    from collections import defaultdict
    groups: dict[str, list] = defaultdict(list)
    for bet in pending:
        groups[bet.provider_id].append({
            "id": bet.id,
            "event_name": events.get(bet.event_id, bet.event_id or "Unknown"),
            "market": bet.market,
            "outcome": bet.outcome,
            "odds": bet.odds,
            "stake": bet.stake,
            "currency": bet.currency or "SEK",
            "placed_at": bet.placed_at.isoformat() if bet.placed_at else None,
        })

    providers = [
        {
            "provider_id": pid,
            "pending_count": len(bets),
            "total_stake": sum(b["stake"] for b in bets),
            "bets": bets,
        }
        for pid, bets in sorted(groups.items())
    ]

    return {
        "providers": providers,
        "total_pending": len(pending),
        "total_stake": sum(b.stake for b in pending),
    }


class SettleBetRequest(BaseModel):
    bet_id: int
    result: str  # "won", "lost", "void"


@router.post("/play/settle-bet")
def settle_bet(body: SettleBetRequest, db: Session = Depends(get_db)):
    """Manually settle a single pending bet."""
    from ...db.models import Bet
    from ...services.bet_service import BetService

    if body.result not in ("won", "lost", "void"):
        raise HTTPException(400, f"Invalid result: {body.result}. Must be won, lost, or void.")

    bet = db.get(Bet, body.bet_id)
    if not bet:
        raise HTTPException(404, f"Bet {body.bet_id} not found")

    # Calculate payout
    if body.result == "won":
        payout = bet.stake * bet.odds
    elif body.result == "void":
        payout = bet.stake
    else:
        payout = 0.0

    # Route through BetService so wagering progress gets recorded
    bet_service = BetService(db)
    bet_service.settle_bet(body.bet_id, body.result, payout)

    return {
        "bet_id": bet.id,
        "result": bet.result,
        "payout": bet.payout,
        "settled_at": bet.settled_at.isoformat() if bet.settled_at else None,
    }


class BuildBatchRequest(BaseModel):
    exclude: list[str] | None = None
    skip_siblings: list[str] | None = None


@router.post("/play/batch")
def build_batch(
    body: BuildBatchRequest | None = None,
    db: Session = Depends(get_db),
):
    """Build cluster-level batch of all +EV opportunities (no provider assignment)."""
    profile_repo = ProfileRepo(db)
    profile = profile_repo.get_active()
    builder = BatchBuilder(db)
    exclude = body.exclude if body else None
    return builder.build(profile.id, exclude=exclude)


class LockBatchRequest(BaseModel):
    batch: list[dict]


@router.post("/play/lock-batch")
def lock_batch(
    body: LockBatchRequest,
    db: Session = Depends(get_db),
):
    """Lock the current batch for capital allocation."""
    profile_repo = ProfileRepo(db)
    profile = profile_repo.get_active()
    locked_at = datetime.now(timezone.utc)
    _locked_batches[profile.id] = {
        "batch": body.batch,
        "locked_at": locked_at,
    }
    return {
        "locked": True,
        "count": len(body.batch),
        "locked_at": locked_at.isoformat(),
        "ttl_seconds": LOCK_TTL_SECONDS,
    }


@router.post("/play/unlock-batch")
def unlock_batch(db: Session = Depends(get_db)):
    """Clear the locked batch so a fresh one can be built."""
    profile_repo = ProfileRepo(db)
    profile = profile_repo.get_active()
    _locked_batches.pop(profile.id, None)
    return {"unlocked": True}


class AllocateRequest(BaseModel):
    skip_siblings: list[str] | None = None
    budget_sek: float | None = None
    budget_usdc: float | None = None


@router.post("/play/allocate")
def allocate_capital(
    body: AllocateRequest | None = None,
    db: Session = Depends(get_db),
):
    """Allocate locked batch to provider siblings. Uses fresh balances."""
    profile_repo = ProfileRepo(db)
    profile = profile_repo.get_active()

    lock = _locked_batches.get(profile.id)
    if not lock:
        raise HTTPException(400, "No locked batch found. Build and lock a batch first.")

    age = (datetime.now(timezone.utc) - lock["locked_at"]).total_seconds()
    if age > LOCK_TTL_SECONDS:
        del _locked_batches[profile.id]
        raise HTTPException(410, "Locked batch expired (>30 min). Rebuild the batch.")

    skip = body.skip_siblings if body else None
    b_sek = body.budget_sek if body else None
    b_usdc = body.budget_usdc if body else None
    builder = BatchBuilder(db)
    return builder.allocate_capital(
        lock["batch"], profile.id,
        skip_siblings=skip,
        budget_sek=b_sek,
        budget_usdc=b_usdc,
    )


@router.post("/play/confirm-capital")
def confirm_capital(
    db: Session = Depends(get_db),
):
    """Rebuild batch after capital actions.

    Balances are auto-synced by the mirror browser — this endpoint
    just rebuilds the batch with current (already-synced) balances.
    """
    profile_repo = ProfileRepo(db)
    profile = profile_repo.get_active()
    builder = BatchBuilder(db)
    return builder.build(profile.id)
