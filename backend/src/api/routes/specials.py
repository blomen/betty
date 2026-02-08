"""Oddsboost API routes — scrape and serve odds boost data."""

import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Query

# Add backend root to path for script import
_backend_root = Path(__file__).parent.parent.parent.parent
if str(_backend_root) not in sys.path:
    sys.path.insert(0, str(_backend_root))

router = APIRouter(prefix="/api/specials", tags=["specials"])


@router.get("")
async def get_specials(
    sport: Optional[str] = Query(None, description="Filter by sport (e.g. football)"),
    provider: Optional[str] = Query(None, description="Filter by provider (matches provider + shared_providers)"),
    category: Optional[str] = Query(None, description="Filter by category (boost, superboost)"),
    sort: str = Query("boost_pct", description="Sort field: boost_pct (default), boosted_odds, event_time"),
    order: str = Query("desc", description="Sort order: desc (default), asc"),
):
    """Get current odds boosts from cached JSON with filtering and sorting."""
    from scripts.scrape_specials import load_specials, DATA_DIR
    import json

    specials = _filter_expired(load_specials())

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
    providers = sorted({s.get("provider", "") for s in all_specials if s.get("provider")})
    categories = sorted({s.get("category", "") for s in all_specials if s.get("category")})

    return {
        "specials": specials,
        "count": len(specials),
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
    specials = await loop.run_in_executor(None, lambda: scrape_all(verbose=False))

    save_specials(specials)
    active = _filter_expired([asdict(s) for s in specials])

    # Sort by boost_pct desc by default
    active.sort(key=lambda s: s.get("boost_pct") or 0.0, reverse=True)

    return {
        "specials": active,
        "count": len(active),
        "scraped_at": specials[0].scraped_at if specials else None,
    }


def _filter_expired(specials: list[dict]) -> list[dict]:
    """Remove specials whose expires_at is in the past."""
    now = datetime.now(timezone.utc)
    result = []
    for s in specials:
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
