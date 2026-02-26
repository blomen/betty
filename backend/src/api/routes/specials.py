"""Oddsboost API routes — scrape and serve odds boost data with EV analysis.

Specials are stored in the DB (specials table) with pre-computed EV fields.
EV enrichment runs at scrape time (scheduler or manual POST), not at query time.
Falls back to JSON file if DB table is empty (first run before scheduler populates).
"""

import sys
import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from ...paths import get_bundle_dir
from ..deps import get_db
from ...db.models import SpecialOdds
from ...analysis.ev_enrichment import enrich_specials_with_ev, filter_expired, deduplicate_specials, store_specials_to_db

# Ensure scripts/ package is importable (lives in bundle root / backend/)
_backend_root = str(get_bundle_dir())
if _backend_root not in sys.path:
    sys.path.insert(0, _backend_root)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/specials", tags=["specials"])


def _row_to_dict(row: SpecialOdds) -> dict:
    """Convert a SpecialOdds DB row to the dict shape the frontend expects."""
    return {
        "provider": row.provider,
        "title": row.title,
        "description": row.description or "",
        "original_odds": row.original_odds,
        "boosted_odds": row.boosted_odds,
        "boost_pct": row.boost_pct,
        "max_stake": row.max_stake,
        "category": row.category or "boost",
        "sport": row.sport or "unknown",
        "league": row.league or "",
        "event": row.event or "",
        "event_time": row.event_time,
        "expires_at": row.expires_at,
        "url": row.url or "",
        "scraped_at": row.scraped_at or "",
        "source": row.source or "",
        "market_label": row.market_label or "",
        "shared_providers": row.shared_providers,
        # EV fields (pre-computed at scrape time)
        "edge_pct": row.edge_pct,
        "fair_odds": row.fair_odds,
        "ev_per_unit": row.ev_per_unit,
        "is_positive_ev": row.is_positive_ev,
        "matched_outcome": row.matched_outcome,
        "matched_event_id": row.matched_event_id,
        "matched_market": row.matched_market,
        "enrichment_method": row.enrichment_method,
    }


def _load_from_db(db: Session) -> list[dict]:
    """Load specials from DB, filtering expired. Returns list of dicts."""
    rows = db.query(SpecialOdds).all()
    if not rows:
        return []
    specials = [_row_to_dict(r) for r in rows]
    return filter_expired(specials)


def _load_from_json_fallback(db: Session) -> list[dict]:
    """Fallback: load from JSON, enrich with EV, return. Used when DB is empty."""
    from scripts.scrape_specials import load_specials
    specials = filter_expired(load_specials())
    if specials:
        specials = enrich_specials_with_ev(specials, db)
    return specials


@router.get("")
async def get_specials(
    sport: Optional[str] = Query(None, description="Filter by sport (e.g. football)"),
    provider: Optional[str] = Query(None, description="Filter by provider (matches provider + shared_providers)"),
    category: Optional[str] = Query(None, description="Filter by category (boost, superboost)"),
    ev_only: bool = Query(False, description="Only return +EV boosts"),
    sort: str = Query("boost_pct", description="Sort field: boost_pct, edge_pct, boosted_odds, event_time"),
    order: str = Query("desc", description="Sort order: desc (default), asc"),
    db: Session = Depends(get_db),
):
    """Get current odds boosts with pre-computed EV analysis vs Pinnacle fair odds."""

    # Primary: load from DB (EV already computed at scrape time)
    specials = _load_from_db(db)

    # Fallback: if DB empty (first run), load from JSON + compute EV on the fly
    if not specials:
        specials = _load_from_json_fallback(db)

    # Use unfiltered set for filter dropdown values
    all_specials = list(specials)

    # --- Filters ---
    if sport:
        sport_lower = sport.lower()
        specials = [s for s in specials if s.get("sport", "").lower() == sport_lower]

    if provider:
        provider_lower = provider.lower()
        specials = [
            s for s in specials
            if s.get("provider", "").lower() == provider_lower
            or provider_lower in [p.lower() for p in (s.get("shared_providers") or [])]
        ]

    if category:
        cat_lower = category.lower()
        specials = [s for s in specials if s.get("category", "").lower() == cat_lower]

    if ev_only:
        specials = [s for s in specials if s.get("is_positive_ev")]

    # --- Sorting ---
    sort_key = sort.lower()
    reverse = order.lower() != "asc"

    def _sort_val(s: dict):
        if sort_key == "boosted_odds":
            return s.get("boosted_odds") or 0.0
        if sort_key == "event_time":
            et = s.get("event_time")
            if not et:
                return "9999"  # push nulls to end
            return et
        if sort_key == "edge_pct":
            return s.get("edge_pct") or -999.0
        # Default: boost_pct
        return s.get("boost_pct") or 0.0

    specials.sort(key=_sort_val, reverse=reverse)

    # --- Metadata ---
    scraped_at = None
    if all_specials:
        # Get most recent scraped_at from the specials themselves
        scraped_at = max(s.get("scraped_at", "") for s in all_specials) or None

    # Collect available filter values for the frontend
    sports = sorted({s.get("sport", "unknown") for s in all_specials if s.get("sport") and s.get("sport") != "unknown"})
    provider_set: set[str] = set()
    for s in all_specials:
        if s.get("provider"):
            provider_set.add(s["provider"])
        for sp in s.get("shared_providers") or []:
            if sp:
                provider_set.add(sp)
    providers = sorted(provider_set)
    categories = sorted({s.get("category", "") for s in all_specials if s.get("category")})

    # Summary stats
    ev_count = sum(1 for s in specials if s.get("is_positive_ev"))
    matched_count = sum(1 for s in specials if s.get("edge_pct") is not None)

    return {
        "specials": specials,
        "count": len(specials),
        "ev_positive_count": ev_count,
        "matched_count": matched_count,
        "scraped_at": scraped_at,
        "filters": {
            "sports": sports,
            "providers": providers,
            "categories": categories,
        },
    }


@router.post("/scrape")
async def scrape_specials(db: Session = Depends(get_db)):
    """Run the specials scraper, enrich with EV, store to DB, and return fresh results."""
    from scripts.scrape_specials import scrape_all, save_specials
    import asyncio
    from dataclasses import asdict

    loop = asyncio.get_running_loop()
    specials, run_log = await loop.run_in_executor(None, lambda: scrape_all(verbose=False))

    # JSON backup
    save_specials(specials)
    _persist_boost_log(run_log)

    # EV enrichment + DB storage
    active = filter_expired([asdict(s) for s in specials])
    active = deduplicate_specials(active)
    active = enrich_specials_with_ev(active, db)
    try:
        store_specials_to_db(active, db)
    except Exception as e:
        logger.error(f"Failed to store specials to DB: {e}")
        db.rollback()

    # Sort by boost_pct desc by default
    active.sort(key=lambda s: s.get("boost_pct") or 0.0, reverse=True)

    return {
        "specials": active,
        "count": len(active),
        "scraped_at": specials[0].scraped_at if specials else None,
    }


def _persist_boost_log(run_log):
    """Persist boost extraction log to DB (used by manual /scrape endpoint)."""
    from ...db.models import BoostExtractionLog, get_session

    try:
        session = get_session()
        scraped_at = datetime.fromisoformat(run_log.scraped_at) if run_log.scraped_at else datetime.utcnow()

        session.query(BoostExtractionLog).delete()

        for pl in run_log.providers:
            session.add(BoostExtractionLog(
                run_id=run_log.run_id,
                scraped_at=scraped_at,
                provider_id=pl.provider_id,
                scraper_type=pl.scraper_type,
                status=pl.status,
                duration_seconds=pl.duration_seconds,
                boosts_found=pl.boosts_found,
                error_message=pl.error_message,
                run_total_boosts=run_log.total_boosts,
                run_duration_seconds=run_log.duration_seconds,
            ))

        session.commit()
    except Exception as e:
        logger.error(f"Failed to persist boost log: {e}")
        try:
            session.rollback()
        except Exception:
            pass
    finally:
        try:
            session.close()
        except Exception:
            pass


@router.get("/extraction-log")
async def get_boost_extraction_log(db: Session = Depends(get_db)):
    """Get the latest boost extraction log."""
    from ...db.models import BoostExtractionLog

    rows = db.query(BoostExtractionLog).order_by(BoostExtractionLog.id.asc()).all()
    if not rows:
        return {"log": None}

    providers = []
    for r in rows:
        providers.append({
            "provider_id": r.provider_id,
            "scraper_type": r.scraper_type,
            "status": r.status,
            "duration_seconds": r.duration_seconds,
            "boosts_found": r.boosts_found,
            "error_message": r.error_message,
        })

    first = rows[0]
    return {
        "log": {
            "run_id": first.run_id,
            "scraped_at": first.scraped_at.isoformat() if first.scraped_at else None,
            "total_boosts": first.run_total_boosts,
            "duration_seconds": first.run_duration_seconds,
            "providers": providers,
        }
    }
