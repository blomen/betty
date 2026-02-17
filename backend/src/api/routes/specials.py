"""Oddsboost API routes — scrape and serve odds boost data with EV analysis."""

import sys
import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from ...paths import get_bundle_dir
from ..deps import get_db
from ...db.models import Event, Odds
from ...analysis.devig import get_fair_odds_for_outcome
from ...matching.normalizer import normalize_team_name

# Ensure scripts/ package is importable (lives in bundle root / backend/)
_backend_root = str(get_bundle_dir())
if _backend_root not in sys.path:
    sys.path.insert(0, _backend_root)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/specials", tags=["specials"])


def _enrich_with_ev(specials: list[dict], db: Session) -> list[dict]:
    """
    Enrich specials with edge_pct vs Pinnacle fair odds.

    For each boost, try to find the matching Pinnacle event and calculate:
      edge_pct = (boosted_odds / fair_odds - 1) * 100

    This tells the user whether a boost is actually +EV vs the sharp line,
    not just "boosted" relative to the provider's own original odds.
    """
    if not specials:
        return specials

    # Collect unique sports from specials for batch DB query
    sports = {s.get("sport") for s in specials if s.get("sport") and s.get("sport") != "unknown"}
    if not sports:
        return specials

    # Load all Pinnacle odds for relevant sports in one query
    pinnacle_odds_query = (
        db.query(Odds, Event)
        .join(Event, Odds.event_id == Event.id)
        .filter(
            Odds.provider_id == "pinnacle",
            Odds.market.in_(["1x2", "moneyline"]),
            Event.sport.in_(list(sports)),
        )
    )
    pinnacle_rows = pinnacle_odds_query.all()

    # Build lookup: {sport: {normalized_event_key: {outcome: odds}}}
    # event_key = normalized "home_vs_away"
    pinnacle_markets: dict[str, dict[str, dict[str, float]]] = {}
    event_info: dict[str, dict] = {}  # event_key -> {event_id, market}

    for odds_row, event_row in pinnacle_rows:
        sport = event_row.sport
        home_norm = normalize_team_name(event_row.home_team).lower() if event_row.home_team else ""
        away_norm = normalize_team_name(event_row.away_team).lower() if event_row.away_team else ""
        event_key = f"{home_norm}_vs_{away_norm}"

        if sport not in pinnacle_markets:
            pinnacle_markets[sport] = {}
        if event_key not in pinnacle_markets[sport]:
            pinnacle_markets[sport][event_key] = {}
            event_info[event_key] = {"event_id": event_row.id, "market": odds_row.market}

        pinnacle_markets[sport][event_key][odds_row.outcome] = odds_row.odds

    # Keywords indicating combo/prop markets that can't be compared to 1x2
    PROP_KEYWORDS = {
        "målgörare", "goalscorer", "first goal", "första mål",
        "antal mål", "over", "under", "över", "btts",
        "båda lagen", "both teams", "resultat +", "result +",
        "tidpunkt", "time of", "kort", "card", "hörna", "corner",
        "poäng", "points", "assist", "rebound",
    }

    # Enrich each special
    for special in specials:
        boosted_odds = special.get("boosted_odds")
        event_name = special.get("event", "")
        sport = special.get("sport", "unknown")

        if not boosted_odds or not event_name or sport == "unknown":
            continue

        # Skip combo/prop boosts — these can't be compared to 1x2/moneyline
        title_lower = (special.get("title", "") + " " + special.get("market_label", "")).lower()
        if any(kw in title_lower for kw in PROP_KEYWORDS):
            continue

        # Parse event name to get teams
        parts = None
        for sep in [" vs ", " - ", " v "]:
            if sep in event_name:
                parts = event_name.split(sep, 1)
                break

        if not parts or len(parts) != 2:
            continue

        home_norm = normalize_team_name(parts[0].strip()).lower()
        away_norm = normalize_team_name(parts[1].strip()).lower()
        event_key = f"{home_norm}_vs_{away_norm}"

        # Look up Pinnacle market for this event
        sport_markets = pinnacle_markets.get(sport, {})
        pin_market = sport_markets.get(event_key)

        # Try swapped order if not found
        if not pin_market:
            swapped_key = f"{away_norm}_vs_{home_norm}"
            pin_market = sport_markets.get(swapped_key)
            if pin_market:
                event_key = swapped_key

        if not pin_market or len(pin_market) < 2:
            continue

        # The boost is on a specific selection — we need to figure out which outcome
        # For single-selection boosts, the title/selection maps to home/away/draw
        # We compute fair odds for ALL outcomes and use the one closest to original_odds
        original_odds = special.get("original_odds")
        if not original_odds:
            continue

        # Find the outcome whose Pinnacle odds are closest to original_odds
        best_outcome = None
        best_diff = float("inf")
        for outcome, pin_odds in pin_market.items():
            diff = abs(pin_odds - original_odds)
            if diff < best_diff:
                best_diff = diff
                best_outcome = outcome

        if not best_outcome or best_diff > 1.5:
            # Too far off — likely wrong event or prop market
            continue

        # De-vig to get fair odds
        fair_odds = get_fair_odds_for_outcome(best_outcome, pin_market, method="multiplicative")
        if not fair_odds or fair_odds <= 1.0:
            continue

        # Calculate edge vs fair line
        edge_pct = round((boosted_odds / fair_odds - 1) * 100, 2)
        ev_per_unit = round(boosted_odds * (1.0 / fair_odds) - 1, 4)

        special["edge_pct"] = edge_pct
        special["fair_odds"] = round(fair_odds, 3)
        special["ev_per_unit"] = ev_per_unit
        special["is_positive_ev"] = edge_pct > 0
        special["matched_outcome"] = best_outcome
        info = event_info.get(event_key, {})
        special["matched_event_id"] = info.get("event_id")
        special["matched_market"] = info.get("market")

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
    """Get current odds boosts with EV analysis vs Pinnacle fair odds."""
    from scripts.scrape_specials import load_specials, DATA_DIR
    import json

    specials = _filter_expired(load_specials())

    # Enrich with EV analysis from Pinnacle
    specials = _enrich_with_ev(specials, db)

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
    specials_path = DATA_DIR / "specials.json"
    scraped_at = None
    if specials_path.exists():
        try:
            with open(specials_path, encoding="utf-8") as f:
                data = json.load(f)
            scraped_at = data.get("scraped_at")
        except Exception:
            pass

    # Collect available filter values for the frontend
    all_specials = _filter_expired(load_specials())
    sports = sorted({s.get("sport", "unknown") for s in all_specials if s.get("sport") and s.get("sport") != "unknown"})
    # Include both primary provider and shared_providers in filter options
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
async def scrape_specials():
    """Run the specials scraper and return fresh results."""
    from scripts.scrape_specials import scrape_all, save_specials
    import asyncio
    from dataclasses import asdict

    loop = asyncio.get_running_loop()
    specials, run_log = await loop.run_in_executor(None, lambda: scrape_all(verbose=False))

    save_specials(specials)
    _persist_boost_log(run_log)
    active = _filter_expired([asdict(s) for s in specials])

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


def _filter_expired(specials: list[dict]) -> list[dict]:
    """Remove specials whose expires_at is in the past or event has already started."""
    now = datetime.now(timezone.utc)
    result = []
    for s in specials:
        # Filter out events that have already started (live/in-play)
        event_time = s.get("event_time")
        if event_time:
            try:
                et = datetime.fromisoformat(event_time.replace("Z", "+00:00"))
                if et.tzinfo is None:
                    et = et.replace(tzinfo=timezone.utc)
                if et <= now:
                    continue  # Event already kicked off
            except (ValueError, TypeError):
                pass

        exp = s.get("expires_at")
        if not exp:
            result.append(s)
            continue
        try:
            dt = datetime.fromisoformat(exp.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            if dt > now:
                result.append(s)
        except (ValueError, TypeError):
            result.append(s)
    return result
