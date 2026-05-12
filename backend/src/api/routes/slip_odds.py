"""Slip-odds tick logging endpoint — writes scraped odds + drift for analysis.

Off by default; ArbRunner / ProviderRunner only POST here when env var
SLIP_ODDS_LOGGING=true is set. Useful for tuning the alignment threshold
from live data.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from ..deps import get_db

router = APIRouter(prefix="/api", tags=["slip-odds"])


class SlipOddsTick(BaseModel):
    provider_id: str
    event_id: str
    market: str
    outcome: str
    scraped_odds: float
    scanner_odds: float | None = None


@router.post("/slip-odds-tick")
def log_slip_odds_tick(tick: SlipOddsTick, db: Session = Depends(get_db)) -> dict:
    drift_pct = None
    if tick.scanner_odds and tick.scanner_odds > 0:
        drift_pct = (tick.scraped_odds - tick.scanner_odds) / tick.scanner_odds * 100.0
    db.execute(
        text(
            "INSERT INTO slip_odds_ticks "
            "(provider_id, event_id, market, outcome, scraped_odds, scanner_odds, drift_pct) "
            "VALUES (:pid, :eid, :market, :outcome, :scraped, :scanner, :drift)"
        ),
        {
            "pid": tick.provider_id,
            "eid": tick.event_id,
            "market": tick.market,
            "outcome": tick.outcome,
            "scraped": tick.scraped_odds,
            "scanner": tick.scanner_odds,
            "drift": drift_pct,
        },
    )
    return {"ok": True}


class OddsLiveUpdate(BaseModel):
    provider_id: str
    event_id: str
    market: str
    outcome: str
    point: float | None = None
    odds: float
    source: str = "mirror"  # "mirror" | "extraction" — for audit only


@router.post("/odds/live-update")
def odds_live_update(payload: OddsLiveUpdate, db: Session = Depends(get_db)) -> dict:
    """Update a single odds row from the mirror's live observation.

    Frontend's arb table reads from this odds table on every scan, so the
    next /arb-workflow request returns the live-updated value. Pre-2026-05-12
    the mirror only broadcast SSE arb_leg_odds and kept the update in a
    frontend in-memory overlay — on refresh the overlay was rehydrated from
    localStorage but other clients (different tab, different device) had no
    way to see the live value. Persisting to DB makes mirror updates a real
    source of truth.

    UPDATE-only (no INSERT). If the row doesn't exist, the bet wasn't
    scanner-stamped and we shouldn't fabricate one — extraction owns the
    insert path.
    """
    res = db.execute(
        text(
            """
            UPDATE odds
               SET odds = :odds,
                   updated_at = now()
             WHERE event_id = :eid
               AND provider_id = :pid
               AND market = :market
               AND outcome = :outcome
               AND ((:point IS NULL AND point IS NULL) OR point = :point)
            """
        ),
        {
            "odds": payload.odds,
            "eid": payload.event_id,
            "pid": payload.provider_id,
            "market": payload.market,
            "outcome": payload.outcome,
            "point": payload.point,
        },
    )
    db.commit()
    return {"ok": True, "updated": res.rowcount, "source": payload.source}
