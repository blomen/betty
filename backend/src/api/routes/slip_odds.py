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
