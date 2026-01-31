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
)
from .utils import generate_canonical_id
from ..constants import ALLOWED_MARKETS

logger = logging.getLogger(__name__)


def detect_and_fix_inversion(
    session,
    event_id: str,
    provider: str,
    home_odds: float | None,
    away_odds: float | None,
) -> bool:
    """
    Detect if provider odds are inverted vs sharp and return True if swap needed.

    Silent operation - no warnings, just fixes the data.
    Only triggers for clear inversions (odds ratio > 2.0).

    This catches cases where providers list teams in opposite home/away order
    for neutral venue games (e.g., Super Bowl), resulting in odds being stored
    under the wrong team.
    """
    if home_odds is None or away_odds is None or home_odds <= 1 or away_odds <= 1:
        return False

    # Get sharp odds (Pinnacle only)
    sharp_odds = session.query(Odds).filter(
        Odds.event_id == event_id,
        Odds.provider_id == 'pinnacle',
        Odds.outcome.in_(['home', 'away']),
    ).all()

    if len(sharp_odds) < 2:
        return False

    sharp = {o.outcome: o.odds for o in sharp_odds}
    if 'home' not in sharp or 'away' not in sharp:
        return False

    # Determine favorites
    new_fav = 'home' if home_odds < away_odds else 'away'
    sharp_fav = 'home' if sharp['home'] < sharp['away'] else 'away'

    if new_fav == sharp_fav:
        return False  # Same favorite, no inversion

    # Only trigger for significant odds skew (ratio > 2.0)
    new_ratio = max(home_odds, away_odds) / min(home_odds, away_odds)
    if new_ratio < 2.0:
        return False  # Close odds, could be legitimate difference

    # Log at DEBUG level (silent in normal operation)
    logger.debug(
        f"[{provider}] Fixing inverted odds for {event_id}: "
        f"H={home_odds:.2f}/A={away_odds:.2f} vs sharp H={sharp['home']:.2f}/A={sharp['away']:.2f}"
    )
    return True


def swap_home_away_outcomes(outcomes: list[dict]) -> list[dict]:
    """Swap home and away outcome labels in a list of outcomes."""
    swapped = []
    for o in outcomes:
        name = o.get('name', '').lower()
        new_outcome = dict(o)

        # Swap home <-> away
        if name in ['home', 'hemma', '1']:
            new_outcome['name'] = 'away'
        elif name in ['away', 'borta', '2']:
            new_outcome['name'] = 'home'

        swapped.append(new_outcome)
    return swapped


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
    # Use pre-parsed team names from extractor (already cleaned of prefixes/suffixes)
    # instead of re-parsing from raw title
    home_team = event.home_team
    away_team = event.away_team

    if not home_team or not away_team:
        # Fallback to parsing from title if extractor didn't set teams
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

        # Only store 1x2/moneyline markets (consistent with provider extraction)
        if market_type not in ALLOWED_MARKETS:
            continue

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
    fuzzy_threshold: int = 90,
    min_individual_score: int = 80,
    prefix_filter_length: int = 3,
    odds_batch: "OddsBatchProcessor" = None,
) -> tuple[bool, int, int]:
    """
    Store provider event with STRICT fuzzy matching against Polymarket.

    BULLETPROOF MATCHING:
    - Requires BOTH teams to match individually (min_individual_score)
    - Higher default threshold (90 vs 85)
    - Rejects asymmetric matches (one team perfect, other poor)

    Args:
        session: SQLAlchemy session
        event: StandardEvent from provider
        provider: Provider ID
        polymarket_cache: Dict {sport: [(id, home, away, date), ...]} for fuzzy matching
        fuzzy_threshold: Minimum average match score (default 90)
        min_individual_score: Minimum score for EACH team (default 80)

    Returns:
        (is_new_event, odds_processed, odds_new)
    """
    from ..matching.matcher import get_team_match_score

    # Generate default ID
    default_id = generate_canonical_id(event.sport, event.home_team, event.away_team, event.start_time)
    matched_id = None
    fuzzy_swapped = False  # Track if fuzzy match detected swapped team order

    # 1. Check if default ID exists (exact match)
    if session.query(Event.id).filter(Event.id == default_id).first():
        matched_id = default_id
    else:
        # 2. Fuzzy match against memory cache (O(1) sport lookup)

        # Safe strftime
        if isinstance(event.start_time, str):
            event_date = event.start_time.split('T')[0].replace('-', '')
        else:
            event_date = "00000000"

        # Get candidates for this sport only (O(1) lookup)
        sport_events = polymarket_cache.get(event.sport, [])

        # Filter by date (allow +/- 1 day for timezone issues)
        candidates = []
        for pid, home, away, date in sport_events:
            if date == event_date:
                candidates.append((pid, home, away, date))
            else:
                # Check +/- 1 day
                try:
                    from datetime import datetime
                    if date and event_date:
                        d1 = datetime.strptime(event_date, "%Y%m%d")
                        d2 = datetime.strptime(date, "%Y%m%d")
                        if abs((d1 - d2).days) <= 1:
                            candidates.append((pid, home, away, date))
                except (ValueError, TypeError):
                    pass

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

        # Try fuzzy matching with STRICT validation
        best_score = 0
        best_match_id = None
        best_match_details = None
        best_is_swapped = False  # Track if match was in swapped order

        for pid, poly_home, poly_away, date in candidates:
            # Get individual scores for DIRECT match
            home_direct = get_team_match_score(event.home_team, poly_home)
            away_direct = get_team_match_score(event.away_team, poly_away)

            # Get individual scores for SWAPPED match
            home_swapped = get_team_match_score(event.home_team, poly_away)
            away_swapped = get_team_match_score(event.away_team, poly_home)

            # Choose best orientation
            direct_avg = (home_direct + away_direct) / 2
            swapped_avg = (home_swapped + away_swapped) / 2

            is_swapped = swapped_avg > direct_avg
            if is_swapped:
                team1_score, team2_score = home_swapped, away_swapped
                avg_score = swapped_avg
            else:
                team1_score, team2_score = home_direct, away_direct
                avg_score = direct_avg

            # BULLETPROOF VALIDATION
            # Skip if average below threshold
            if avg_score < fuzzy_threshold:
                continue

            # Skip if EITHER team below minimum individual score
            if team1_score < min_individual_score or team2_score < min_individual_score:
                logger.debug(
                    f"[{provider}] Rejected match '{event.home_team} vs {event.away_team}' -> "
                    f"'{poly_home} vs {poly_away}': individual scores {team1_score:.0f}/{team2_score:.0f}"
                )
                continue

            # Skip asymmetric matches (one team perfect, other poor)
            score_diff = abs(team1_score - team2_score)
            if score_diff > 20 and min(team1_score, team2_score) < 85:
                logger.debug(
                    f"[{provider}] Rejected asymmetric match '{event.home_team} vs {event.away_team}': "
                    f"scores {team1_score:.0f}/{team2_score:.0f}"
                )
                continue

            if avg_score > best_score:
                best_score = avg_score
                best_match_id = pid
                best_match_details = (poly_home, poly_away, team1_score, team2_score)
                best_is_swapped = is_swapped

        if best_match_id:
            matched_id = best_match_id
            fuzzy_swapped = best_is_swapped  # Record if teams were swapped
            poly_home, poly_away, t1, t2 = best_match_details
            swap_note = " [SWAPPED]" if best_is_swapped else ""
            logger.info(
                f"[{provider}] Matched '{event.home_team} vs {event.away_team}' -> "
                f"'{poly_home} vs {poly_away}' (scores: {t1:.0f}/{t2:.0f}, avg: {best_score:.0f}){swap_note}"
            )
        else:
            # 3. No fuzzy match - check if canonical event exists with swapped teams
            # This catches cases where the provider has home/away reversed vs sharp source
            swapped_id = generate_canonical_id(event.sport, event.away_team, event.home_team, event.start_time)
            if session.query(Event.id).filter(Event.id == swapped_id).first():
                # Canonical event exists with swapped team order - use it
                matched_id = swapped_id
                fuzzy_swapped = True  # Mark as swapped so outcomes get flipped
                logger.info(
                    f"[{provider}] Aligned '{event.home_team} vs {event.away_team}' -> "
                    f"canonical event with swapped teams (using {swapped_id})"
                )
            else:
                # 4. No match at all - use default ID
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

        # Add to cache for subsequent providers to match against
        # This enables cross-provider matching (e.g., LeoVegas ↔ Pinnacle)
        if isinstance(event.start_time, str):
            date_str = event.start_time.split('T')[0].replace('-', '')
        else:
            date_str = "00000000"

        if event.sport not in polymarket_cache:
            polymarket_cache[event.sport] = []
        cache_entry = (final_id, db_event.home_team, db_event.away_team, date_str)
        if cache_entry not in polymarket_cache[event.sport]:
            polymarket_cache[event.sport].append(cache_entry)

    # If we matched to an existing event and fuzzy matching didn't detect a swap,
    # check for odds-based inversion against sharp source
    if matched_id and not fuzzy_swapped:
        # Extract home/away odds from event markets
        home_odds, away_odds = None, None
        for market in event.markets:
            if normalize_market(market.get('type', '')) in ALLOWED_MARKETS:
                for outcome in market.get('outcomes', []):
                    norm = normalize_outcome(
                        outcome.get('name', ''),
                        event.home_team,
                        event.away_team
                    )
                    if norm == 'home':
                        home_odds = outcome.get('odds')
                    elif norm == 'away':
                        away_odds = outcome.get('odds')

        # Check for inversion against sharp odds
        if detect_and_fix_inversion(session, matched_id, provider, home_odds, away_odds):
            fuzzy_swapped = True  # Trigger swap

    # Swap outcomes if team order was detected as different from canonical event
    # (either via fuzzy matching, swapped-ID check, or odds-based inversion)
    should_swap = fuzzy_swapped

    if should_swap:
        logger.debug(
            f"[{provider}] Swapping outcomes for {final_id} to align with canonical event"
        )

    # Store odds
    odds_processed = 0
    odds_new = 0

    for market in event.markets:
        market_type = normalize_market(market.get('type', ''))
        if market_type not in ALLOWED_MARKETS:
            continue
        outcomes = market.get('outcomes', [])

        # Swap home/away if team order differs from canonical event
        if should_swap:
            outcomes = swap_home_away_outcomes(outcomes)

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
        # Use tuple key to deduplicate (point included for schema compatibility)
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
