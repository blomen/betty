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
    fuzzy_threshold: int = 85,
    prefix_filter_length: int = 3,
    odds_batch: "OddsBatchProcessor" = None,
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

        # Pre-filter by team name prefix for better performance
        if prefix_filter_length > 0 and len(candidates) > 10:
            home_prefix = event.home_team[:prefix_filter_length].lower() if event.home_team else ""
            away_prefix = event.away_team[:prefix_filter_length].lower() if event.away_team else ""

            prefix_filtered = [
                (pid, home, away, date)
                for pid, home, away, date in candidates
                if (home[:prefix_filter_length].lower() == home_prefix or
                    away[:prefix_filter_length].lower() == home_prefix or
                    home[:prefix_filter_length].lower() == away_prefix or
                    away[:prefix_filter_length].lower() == away_prefix)
            ]
            # Only use prefix filter if it found matches, otherwise fall back to full list
            if prefix_filtered:
                candidates = prefix_filtered

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

            if avg_score > best_score and avg_score >= fuzzy_threshold:
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

            # Use batch processor if available, otherwise individual upsert
            if odds_batch:
                odds_batch.add(final_id, provider, market_type, outcome_name, odds_value, point_value)
            else:
                odds_new += upsert_odds(session, final_id, provider, market_type, outcome_name, odds_value, point_value)

    # When using batch processor, we don't know the new count until flush
    # Return 0 for now - caller should get stats from batch processor
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
    # Build filter including point (handles NULL correctly)
    filters = [
        Odds.event_id == event_id,
        Odds.provider_id == provider,
        Odds.market == market,
        Odds.outcome == outcome,
    ]
    # Point filter: use is_(None) for NULL comparison
    if point is None:
        filters.append(Odds.point.is_(None))
    else:
        filters.append(Odds.point == point)

    existing = session.query(Odds).filter(*filters).first()

    if existing:
        existing.odds = odds
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


class OddsBatchProcessor:
    """
    Batch processor for odds upserts to reduce DB round-trips.

    Collects odds records and flushes in batches for better performance.
    Uses bulk operations where possible.
    """

    def __init__(self, session, batch_size: int = 100):
        self.session = session
        self.batch_size = batch_size
        # Use dict to deduplicate within batch - last write wins
        self._pending: dict[tuple, dict] = {}
        self._insert_count = 0
        self._update_count = 0

    def add(
        self,
        event_id: str,
        provider: str,
        market: str,
        outcome: str,
        odds: float,
        point: float = None,
    ):
        """Add odds record to batch (will be processed on flush)."""
        # Use tuple key to deduplicate - include point for spread/totals markets
        # This allows "Over 4.5" and "Over 10.5" to coexist
        key = (event_id, provider, market, outcome, point)
        self._pending[key] = {
            "event_id": event_id,
            "provider_id": provider,
            "market": market,
            "outcome": outcome,
            "odds": odds,
            "point": point,
        }

        if len(self._pending) >= self.batch_size:
            self.flush()

    def flush(self):
        """Process pending records with bulk operations."""
        if not self._pending:
            return

        now = datetime.now(timezone.utc)

        # Fetch existing records in one query using 5-column key
        from sqlalchemy import tuple_, and_, or_
        existing_records = {}
        keys = list(self._pending.keys())

        if keys:
            # Query in batches to avoid SQLite limits
            for i in range(0, len(keys), 500):
                batch_keys = keys[i:i + 500]
                # Build filter for 5-column key including point (handles NULL)
                conditions = []
                for event_id, provider_id, market, outcome, point in batch_keys:
                    if point is None:
                        conditions.append(and_(
                            Odds.event_id == event_id,
                            Odds.provider_id == provider_id,
                            Odds.market == market,
                            Odds.outcome == outcome,
                            Odds.point.is_(None)
                        ))
                    else:
                        conditions.append(and_(
                            Odds.event_id == event_id,
                            Odds.provider_id == provider_id,
                            Odds.market == market,
                            Odds.outcome == outcome,
                            Odds.point == point
                        ))

                if conditions:
                    existing = self.session.query(Odds).filter(or_(*conditions)).all()
                    for rec in existing:
                        key = (rec.event_id, rec.provider_id, rec.market, rec.outcome, rec.point)
                        existing_records[key] = rec

        # Separate inserts from updates
        to_insert = []
        for key, record in self._pending.items():
            if key in existing_records:
                # Update existing
                existing = existing_records[key]
                existing.odds = record["odds"]
                existing.updated_at = now
                self._update_count += 1
            else:
                # New record
                to_insert.append(record)

        # Bulk insert new records
        if to_insert:
            self.session.bulk_insert_mappings(Odds, to_insert)
            self._insert_count += len(to_insert)

        self._pending.clear()

    def get_stats(self) -> tuple[int, int]:
        """Return (new_count, update_count)."""
        return self._insert_count, self._update_count

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is None:
            self.flush()
        return False
