"""Opportunities API routes - value betting opportunities."""

import json
import logging
import time as _time

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ...repositories import ProfileRepo
from ...services import OpportunityService
from ...services.batch_builder import BatchBuilder
from ...services.play_service import PlaySessionService
from ..deps import get_db
from ..schemas import BonusMatchRequest

logger = logging.getLogger(__name__)

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
    type: str | None = None,
    active_only: bool = True,
    provider1: str | None = None,
    provider2: str | None = None,
    providers: str | None = None,
    market: str | None = None,
    sport: str | None = None,
    min_value: float | None = None,
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
        raise HTTPException(404, "No suitable hedge found (all hedges are same-provider or no valid options)")

    return result


@router.get("/arb-workflow")
def arb_workflow(
    providers: str,
    major_only: bool = False,
    counterpart_providers: str | None = None,
    limit: int = 50,
    service: OpportunityService = Depends(_get_service),
):
    """Live-scan arb opportunities for specific anchor providers."""
    provider_list = [p.strip() for p in providers.split(",") if p.strip()]
    if not provider_list:
        raise HTTPException(400, "At least one provider required")
    counterpart_list = (
        [p.strip() for p in counterpart_providers.split(",") if p.strip()] if counterpart_providers else None
    )
    return service.scan_arb_workflow(
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
    priority_provider: str | None = None


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
    priority = body.priority_provider if body else None
    return builder.build(profile.id, exclude=exclude, priority_provider=priority)


# ---------------------------------------------------------------------------
# Bet blacklist
# ---------------------------------------------------------------------------


class BlacklistRequest(BaseModel):
    event_id: str
    provider_id: str
    market: str | None = None
    outcome: str | None = None


@router.post("/play/blacklist")
def blacklist_bet(body: BlacklistRequest, db: Session = Depends(get_db)):
    """Permanently exclude an event/provider from the play batch."""
    from ...db.models import BetBlacklist

    profile = ProfileRepo(db).get_active()
    existing = (
        db.query(BetBlacklist)
        .filter(
            BetBlacklist.profile_id == profile.id,
            BetBlacklist.event_id == body.event_id,
            BetBlacklist.provider_id == body.provider_id,
        )
        .first()
    )
    if existing:
        return {"status": "already_blacklisted"}

    db.add(
        BetBlacklist(
            profile_id=profile.id,
            event_id=body.event_id,
            provider_id=body.provider_id,
            market=body.market,
            outcome=body.outcome,
        )
    )
    db.commit()
    return {"status": "blacklisted", "event_id": body.event_id, "provider_id": body.provider_id}


# ---------------------------------------------------------------------------
# Settlement: pending bets, scan, confirm
# ---------------------------------------------------------------------------


@router.get("/play/pending-bets")
def get_pending_bets(db: Session = Depends(get_db)):
    """Return all pending bets grouped by provider."""
    from ...db.models import Bet, Event

    profile = ProfileRepo(db).get_active()

    pending = (
        db.query(Bet, Event)
        .join(Event, Bet.event_id == Event.id, isouter=True)
        .filter(
            Bet.profile_id == profile.id,
            Bet.result == "pending",
        )
        .order_by(Bet.start_time.asc())
        .all()
    )

    by_provider: dict[str, list] = {}
    for bet, event in pending:
        pid = bet.provider_id
        by_provider.setdefault(pid, [])
        by_provider[pid].append(
            {
                "id": bet.id,
                "bet_id": bet.id,
                "provider_bet_id": bet.provider_bet_id,
                "event_id": bet.event_id,
                "provider_id": pid,
                "market": bet.market,
                "outcome": bet.outcome,
                "point": bet.point,
                "odds": bet.odds,
                "stake": bet.stake,
                "currency": bet.currency or "SEK",
                "placed_at": bet.placed_at.isoformat() if bet.placed_at else None,
                "start_time": bet.start_time.isoformat() if bet.start_time else None,
                "home_team": (event.display_home or event.home_team) if event else None,
                "away_team": (event.display_away or event.away_team) if event else None,
                "sport": event.sport if event else None,
            }
        )

    providers = []
    for pid, bets in by_provider.items():
        total_stake = sum(b["stake"] for b in bets)
        providers.append(
            {
                "provider_id": pid,
                "bet_count": len(bets),
                "total_stake": total_stake,
                "currency": bets[0]["currency"],
                "bets": bets,
            }
        )
    providers.sort(key=lambda p: p["bet_count"], reverse=True)

    return {"providers": providers, "total_bets": sum(p["bet_count"] for p in providers)}


@router.get("/play/settle-scan")
async def settle_scan():
    """Scan pending bets for resolved events. Returns proposals for confirmation."""
    from ...services.auto_settle import scan_settlements

    proposals = await scan_settlements()
    return {"proposals": proposals, "count": len(proposals)}


class ConfirmSettleRequest(BaseModel):
    bet_id: int
    result: str  # "won", "lost", "void"


@router.post("/play/settle-confirm")
def settle_confirm(body: ConfirmSettleRequest):
    """Confirm settlement of a single bet."""
    from ...services.auto_settle import confirm_settlement

    if body.result not in ("won", "lost", "void"):
        from fastapi import HTTPException

        raise HTTPException(400, f"Invalid result: {body.result}")
    return confirm_settlement(body.bet_id, body.result)


@router.post("/play/settle-batch")
async def settle_batch(body: list[ConfirmSettleRequest]):
    """Confirm settlement of multiple bets at once."""
    from ...services.auto_settle import confirm_settlement

    results = []
    for item in body:
        if item.result not in ("won", "lost", "void"):
            results.append({"bet_id": item.bet_id, "error": f"Invalid result: {item.result}"})
            continue
        resp = confirm_settlement(item.bet_id, item.result)
        results.append(resp)
    settled = sum(1 for r in results if r.get("status") == "settled")
    return {"results": results, "settled": settled, "total": len(body)}
