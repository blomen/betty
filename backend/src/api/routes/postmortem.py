"""Postmortem API routes — thin handlers delegating to PostmortemService."""

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ...analysis.patterns import detect_bet_patterns, detect_trade_patterns
from ...repositories import ProfileRepo
from ...repositories.postmortem_repo import PostmortemRepo
from ...services.postmortem_service import PostmortemService
from ..deps import get_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/postmortem", tags=["postmortem"])


def _active_profile_id(db: Session) -> int | None:
    profile = ProfileRepo(db).get_active()
    return profile.id if profile else None


@router.get("/bets")
def get_bet_postmortems(
    classification: str | None = None,
    market: str | None = None,
    provider: str | None = None,
    sport: str | None = None,
    db: Session = Depends(get_db),
):
    """Classified bets with optional filters. Scoped to active profile."""
    profile_id = _active_profile_id(db)
    if not profile_id:
        return {"postmortems": [], "count": 0}

    repo = PostmortemRepo(db)
    rows = repo.get_bet_pms_for_profile(profile_id)

    if classification:
        rows = [(b, pm) for b, pm in rows if pm.classification == classification]
    if market:
        rows = [(b, pm) for b, pm in rows if b.market == market]
    if provider:
        rows = [(b, pm) for b, pm in rows if b.provider_id == provider]
    if sport:
        rows = [(b, pm) for b, pm in rows if b.event and b.event.sport == sport]

    return {
        "postmortems": [
            {
                "bet_id": b.id,
                "provider": b.provider_id,
                "market": b.market,
                "outcome": b.outcome,
                "odds": b.odds,
                "stake": b.stake,
                "result": b.result,
                "profit": b.profit,
                "classification": pm.classification,
                "edge_at_placement": pm.edge_at_placement,
                "clv_pct": pm.clv_pct,
                "clv_confirmed": pm.clv_confirmed,
                "expected_win_pct": pm.expected_win_pct,
                "kelly_fraction": pm.kelly_fraction,
                "is_oversized": pm.is_oversized,
                "variance_score": pm.variance_score,
                "placed_at": b.placed_at.isoformat() if b.placed_at else None,
            }
            for b, pm in rows
        ],
        "count": len(rows),
    }


@router.get("/bets/summary")
def get_bet_summary(db: Session = Depends(get_db)):
    """Aggregate stats by classification."""
    profile_id = _active_profile_id(db)
    if not profile_id:
        return {"summary": [], "total": 0}

    repo = PostmortemRepo(db)
    rows = repo.get_bet_pms_for_profile(profile_id)

    from collections import defaultdict

    buckets = defaultdict(
        lambda: {
            "count": 0,
            "total_stake": 0.0,
            "total_profit": 0.0,
            "edge_sum": 0.0,
            "clv_sum": 0.0,
            "edge_count": 0,
            "clv_count": 0,
        }
    )

    for bet, pm in rows:
        b = buckets[pm.classification]
        b["count"] += 1
        b["total_stake"] += bet.stake
        b["total_profit"] += bet.profit
        if pm.edge_at_placement is not None:
            b["edge_sum"] += pm.edge_at_placement
            b["edge_count"] += 1
        if pm.clv_pct is not None:
            b["clv_sum"] += pm.clv_pct
            b["clv_count"] += 1

    summary = []
    for cls, b in buckets.items():
        summary.append(
            {
                "classification": cls,
                "count": b["count"],
                "avg_edge": round(b["edge_sum"] / b["edge_count"], 2) if b["edge_count"] else None,
                "avg_clv": round(b["clv_sum"] / b["clv_count"], 2) if b["clv_count"] else None,
                "total_profit": round(b["total_profit"], 2),
                "roi": round(b["total_profit"] / b["total_stake"] * 100, 2) if b["total_stake"] > 0 else 0,
            }
        )

    summary.sort(key=lambda s: s["count"], reverse=True)
    return {"summary": summary, "total": len(rows)}


@router.get("/bets/patterns")
def get_bet_patterns(db: Session = Depends(get_db)):
    profile_id = _active_profile_id(db)
    if not profile_id:
        return {"patterns": []}
    repo = PostmortemRepo(db)
    rows = repo.get_bet_pms_for_profile(profile_id)
    return {"patterns": detect_bet_patterns(rows)}


@router.get("/trades")
def get_trade_postmortems(
    classification: str | None = None,
    account_id: int | None = None,
    db: Session = Depends(get_db),
):
    if not account_id:
        return {"postmortems": [], "count": 0}

    repo = PostmortemRepo(db)
    rows = repo.get_trade_pms_for_account(account_id)

    if classification:
        rows = [(t, pm) for t, pm in rows if pm.classification == classification]

    return {
        "postmortems": [
            {
                "trade_id": t.id,
                "instrument": t.instrument,
                "direction": t.direction,
                "setup_type": t.setup_type,
                "r_multiple": pm.r_multiple,
                "classification": pm.classification,
                "setup_avg_r": pm.setup_avg_r,
                "setup_win_rate": pm.setup_win_rate,
                "stop_quality": pm.stop_quality,
                "target_quality": pm.target_quality,
                "streak_position": pm.streak_position,
                "routine_psych_avg": pm.routine_psych_avg,
                "rules_followed": pm.rules_followed,
                "closed_at": t.closed_at.isoformat() if t.closed_at else None,
            }
            for t, pm in rows
        ],
        "count": len(rows),
    }


@router.get("/trades/summary")
def get_trade_summary(account_id: int, db: Session = Depends(get_db)):
    repo = PostmortemRepo(db)
    rows = repo.get_trade_pms_for_account(account_id)

    from collections import defaultdict

    buckets = defaultdict(lambda: {"count": 0, "r_sum": 0.0, "pnl_sum": 0.0})

    for trade, pm in rows:
        b = buckets[pm.classification]
        b["count"] += 1
        b["r_sum"] += pm.r_multiple or 0
        b["pnl_sum"] += trade.realized_pnl or 0

    summary = []
    for cls, b in buckets.items():
        summary.append(
            {
                "classification": cls,
                "count": b["count"],
                "avg_r": round(b["r_sum"] / b["count"], 2) if b["count"] else 0,
                "total_pnl": round(b["pnl_sum"], 2),
            }
        )

    summary.sort(key=lambda s: s["count"], reverse=True)
    return {"summary": summary, "total": len(rows)}


@router.get("/trades/patterns")
def get_trade_patterns(account_id: int, db: Session = Depends(get_db)):
    repo = PostmortemRepo(db)
    rows = repo.get_trade_pms_for_account(account_id)
    return {"patterns": detect_trade_patterns(rows)}


@router.post("/recompute")
def recompute_postmortems(
    profile_id: int | None = None,
    account_id: int | None = None,
    db: Session = Depends(get_db),
):
    """Force recompute all postmortems. Returns 409 if already running."""
    if not PostmortemService.try_acquire_recompute_lock():
        raise HTTPException(status_code=409, detail="Recompute already in progress")

    try:
        svc = PostmortemService(db)
        bet_count = 0
        trade_count = 0

        if profile_id:
            bet_count = svc.recompute_all_bets(profile_id)
        elif not account_id:
            pid = _active_profile_id(db)
            if pid:
                bet_count = svc.recompute_all_bets(pid)

        if account_id:
            trade_count = svc.recompute_all_trades(account_id)

        return {"bets_recomputed": bet_count, "trades_recomputed": trade_count}
    finally:
        PostmortemService.release_recompute_lock()
