"""
Pipeline Storage

Functions for storing events and odds in the database.
"""

import logging
from datetime import datetime, timezone

from ..core import StandardEvent
from ..db.models import Event, Odds
from ..matching import (
    parse_teams_from_title,
    normalize_market,
    normalize_outcome,
    fuzzy_match_teams,
)
from .utils import generate_canonical_id

logger = logging.getLogger(__name__)


def store_polymarket_event(
    session,
    event: StandardEvent,
    kambi_sport: str,
    polymarket_cache: dict,
) -> tuple[bool, int, int]:
    """
    Store Polymarket event in database.

    Args:
        session: SQLAlchemy session
        event: StandardEvent from Polymarket
        kambi_sport: Normalized sport name
        polymarket_cache: Dict {sport: [(id, home, away, date), ...]} for fuzzy matching

    Returns:
        (is_new_event, odds_processed, odds_new)
    """
    teams = parse_teams_from_title(event.name)
    if not teams:
        logger.warning(f"Failed to parse teams from: {event.name}")
        return False, 0, 0

    home_team, away_team = teams
    canonical_id = generate_canonical_id(kambi_sport, home_team, away_team, event.start_time)

    # Cache for fuzzy matching - indexed by sport for O(1) lookup
    if isinstance(event.start_time, datetime):
        date_str = event.start_time.strftime("%Y%m%d")
    else:
        date_str = str(event.start_time)[:10].replace('-', '') if event.start_time else "00000000"

    # Add to sport-indexed cache
    if kambi_sport not in polymarket_cache:
        polymarket_cache[kambi_sport] = []
    polymarket_cache[kambi_sport].append((canonical_id, home_team, away_team, date_str))

    # Create/get event
    db_event = session.query(Event).filter(Event.id == canonical_id).first()
    is_new_event = False

    if not db_event:
        # Convert start_time to datetime if string
        start_dt = event.start_time
        if isinstance(start_dt, str):
            try:
                start_dt = datetime.fromisoformat(start_dt.replace('Z', '+00:00'))
            except:
                start_dt = None

        db_event = Event(
            id=canonical_id,
            sport=kambi_sport,
            league=event.sport,
            home_team=home_team,
            away_team=away_team,
            start_time=start_dt,
        )
        session.add(db_event)
        is_new_event = True

    # Store odds
    odds_processed = 0
    odds_new = 0

    for market in event.markets:
        if not market.get("is_active", True):  # Default to active if missing
            continue

        market_type = normalize_market(market.get("question", "") or market.get("type", ""))
        outcomes = market.get("outcomes", [])

        for outcome in outcomes:
            outcome_name = outcome.get("name", "")
            odds = outcome.get("odds", 0)

            if odds <= 1 or odds > 100:
                continue

            odds_processed += 1
            outcome_norm = normalize_outcome(outcome_name, home_team, away_team)
            odds_new += upsert_odds(session, canonical_id, "polymarket", market_type, outcome_norm, odds)

    return is_new_event, odds_processed, odds_new


def store_provider_event(
    session,
    event: StandardEvent,
    provider: str,
    polymarket_cache: dict,
) -> tuple[bool, int, int]:
    """
    Store provider event with fuzzy matching against Polymarket.

    Args:
        session: SQLAlchemy session
        event: StandardEvent from provider
        provider: Provider ID
        polymarket_cache: Dict {sport: [(id, home, away, date), ...]} for fuzzy matching

    Returns:
        (is_new_event, odds_processed, odds_new)
    """
    # Generate default ID
    default_id = generate_canonical_id(event.sport, event.home_team, event.away_team, event.start_time)
    matched_id = None

    # 1. Check if default ID exists (exact match)
    if session.query(Event.id).filter(Event.id == default_id).first():
        matched_id = default_id
    else:
        # 2. Fuzzy match against memory cache (O(1) sport lookup)
        from ..matching import match_events

        # Safe strftime
        if isinstance(event.start_time, str):
            event_date = event.start_time.split('T')[0].replace('-', '')
        else:
            event_date = "00000000"

        # Get candidates for this sport only (O(1) lookup)
        sport_events = polymarket_cache.get(event.sport, [])

        # Filter by date
        candidates = [
            (pid, home, away, date)
            for pid, home, away, date in sport_events
            if date == event_date
        ]

        # Try fuzzy matching
        best_score = 0
        best_match_id = None

        for pid, poly_home, poly_away, date in candidates:
            home_score = max(
                fuzzy_match_teams(event.home_team, poly_home),
                fuzzy_match_teams(event.home_team, poly_away)
            )
            away_score = max(
                fuzzy_match_teams(event.away_team, poly_away),
                fuzzy_match_teams(event.away_team, poly_home)
            )

            avg_score = (home_score + away_score) / 2

            if avg_score > best_score and avg_score >= 85:
                best_score = avg_score
                best_match_id = pid

        if best_match_id:
            matched_id = best_match_id
            logger.info(f"Fuzzy matched {provider} '{event.home_team} vs {event.away_team}' to {matched_id} (score: {best_score:.1f})")
        else:
            # 3. No match - use default ID
            matched_id = default_id

    final_id = matched_id

    # Create event if doesn't exist
    db_event = session.query(Event).filter(Event.id == final_id).first()
    is_new_event = False

    if not db_event:
        start_dt = event.start_time
        if isinstance(start_dt, str):
            try:
                start_dt = datetime.fromisoformat(start_dt.replace('Z', '+00:00'))
            except:
                start_dt = None

        db_event = Event(
            id=final_id,
            sport=event.sport,
            league=event.league,
            home_team=event.home_team,
            away_team=event.away_team,
            start_time=start_dt,
        )
        session.add(db_event)
        is_new_event = True

    # Store odds
    odds_processed = 0
    odds_new = 0

    for market in event.markets:
        market_type = normalize_market(market.get('type', ''))
        outcomes = market.get('outcomes', [])

        for outcome in outcomes:
            outcome_name = normalize_outcome(outcome.get('name', ''), event.home_team, event.away_team)
            odds_value = outcome.get('odds', 0)
            point_value = outcome.get('point')

            if odds_value <= 1:
                continue

            odds_processed += 1
            odds_new += upsert_odds(session, final_id, provider, market_type, outcome_name, odds_value, point_value)

    return is_new_event, odds_processed, odds_new


def upsert_odds(
    session,
    event_id: str,
    provider: str,
    market: str,
    outcome: str,
    odds: float,
    point: float = None,
) -> int:
    """
    Insert or update odds record.

    Args:
        session: SQLAlchemy session
        event_id: Event canonical ID
        provider: Provider ID
        market: Market type
        outcome: Outcome name
        odds: Decimal odds
        point: Point/line value (optional)

    Returns:
        1 if new odds inserted, 0 if updated
    """
    existing = session.query(Odds).filter(
        Odds.event_id == event_id,
        Odds.provider_id == provider,
        Odds.market == market,
        Odds.outcome == outcome,
    ).first()

    if existing:
        existing.odds = odds
        existing.point = point
        existing.updated_at = datetime.now(timezone.utc)
        return 0
    else:
        session.add(Odds(
            event_id=event_id,
            provider_id=provider,
            market=market,
            outcome=outcome,
            odds=odds,
            point=point
        ))
        return 1
