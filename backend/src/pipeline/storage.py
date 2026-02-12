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
from ..matching.normalizer import generate_canonical_id
from ..constants import ALLOWED_MARKETS, SHARP_PROVIDERS

logger = logging.getLogger(__name__)

# Tolerance for comparing spread/total point values (floats)
_POINT_TOLERANCE = 0.01


def _get_date_candidates(event_cache: dict, date_index: dict, sport: str, event_date: str) -> list:
    """
    Get fuzzy-match candidates for a sport+date using the date index.

    Returns list of (event_id, home, away, date) tuples for the target date ±1 day.
    Uses O(1) set lookups instead of scanning all events in the sport.
    """
    sport_events = event_cache.get(sport, {})
    sport_dates = date_index.get(sport, {})

    # Collect candidate IDs from exact date + adjacent dates
    candidate_ids = set()
    if event_date in sport_dates:
        candidate_ids.update(sport_dates[event_date])

    # ±1 day for timezone issues
    try:
        d = datetime.strptime(event_date, "%Y%m%d")
        from datetime import timedelta
        for delta in (-1, 1):
            adj_date = (d + timedelta(days=delta)).strftime("%Y%m%d")
            if adj_date in sport_dates:
                candidate_ids.update(sport_dates[adj_date])
    except (ValueError, TypeError):
        pass

    # Resolve IDs to full candidate tuples
    candidates = []
    for pid in candidate_ids:
        entry = sport_events.get(pid)
        if entry:
            home, away, date = entry
            candidates.append((pid, home, away, date))
    return candidates


def _update_event_cache(event_cache: dict, date_index: dict,
                        sport: str, event_id: str,
                        home: str, away: str, date_str: str):
    """Update both event_cache and date_index atomically."""
    if sport not in event_cache:
        event_cache[sport] = {}
    if event_id not in event_cache[sport]:
        event_cache[sport][event_id] = (home, away, date_str)

        # Update date index
        if sport not in date_index:
            date_index[sport] = {}
        if date_str not in date_index[sport]:
            date_index[sport][date_str] = set()
        date_index[sport][date_str].add(event_id)


def _get_pinnacle_points(session, event_id: str, cache: dict = None) -> dict[str, set[float]]:
    """
    Get Pinnacle's spread and total point values for a matched event.

    For spread: returns set of HOME outcome points (e.g., {0.25, 1.25}).
    For total: returns set of point values (e.g., {222.5}).
    Pinnacle may publish multiple non-alternate lines for the same market.

    Args:
        cache: Optional dict keyed by event_id for caching results across calls.

    Returns {"spread": set, "total": set}.
    """
    if cache is not None and event_id in cache:
        return cache[event_id]

    from sqlalchemy import func
    pinnacle_odds = session.query(Odds.market, Odds.outcome, Odds.point).filter(
        Odds.event_id == event_id,
        func.lower(Odds.provider_id).like('pinnacle%'),
        Odds.market.in_(['spread', 'total']),
        Odds.point.isnot(None),
    ).all()

    result: dict[str, set[float]] = {"spread": set(), "total": set()}
    for market, outcome, point in pinnacle_odds:
        if market == "spread" and outcome == "home":
            result["spread"].add(point)
        elif market == "total" and outcome in ("over", "under"):
            result["total"].add(point)

    if cache is not None:
        cache[event_id] = result
    return result


def _point_matches_pinnacle(market_type: str, home_point: float | None, pinnacle_points: dict) -> bool:
    """
    Check if a market's home-perspective point matches any of Pinnacle's lines.

    For spread: compare home outcome's point directly (sign matters — +0.25 != -0.25).
    For total: compare point value directly (always positive).
    """
    pin_points = pinnacle_points.get(market_type, set())
    if not pin_points:
        return False  # Pinnacle doesn't have this market — skip
    if home_point is None:
        return False
    return any(abs(home_point - pp) < _POINT_TOLERANCE for pp in pin_points)


def detect_and_fix_inversion(
    session,
    event_id: str,
    provider: str,
    home_odds: float | None,
    away_odds: float | None,
    sharp_odds_cache: dict = None,
) -> bool:
    """
    Detect if provider odds are inverted vs sharp and return True if swap needed.

    Silent operation - no warnings, just fixes the data.
    Only triggers when sharp (Pinnacle) shows a clear favorite (odds ratio > 1.5).

    This catches cases where providers list teams in opposite home/away order
    for neutral venue games (e.g., Super Bowl), resulting in odds being stored
    under the wrong team. Even if the incoming provider's odds are close to 50/50,
    if Pinnacle has a clear favorite, disagreement on which team is favored
    indicates an inversion.
    """
    if home_odds is None or away_odds is None or home_odds <= 1 or away_odds <= 1:
        return False

    # Check cache first (Pinnacle data is static during a run)
    if sharp_odds_cache is not None and event_id in sharp_odds_cache:
        sharp = sharp_odds_cache[event_id]
    else:
        # Get sharp odds (Pinnacle only) - use case-insensitive match for provider ID
        from sqlalchemy import func
        sharp_rows = session.query(Odds).filter(
            Odds.event_id == event_id,
            func.lower(Odds.provider_id).like('pinnacle%'),
            Odds.outcome.in_(['home', 'away']),
            Odds.market.in_(['1x2', 'moneyline']),
        ).all()

        sharp = {o.outcome: o.odds for o in sharp_rows}
        if sharp_odds_cache is not None:
            sharp_odds_cache[event_id] = sharp

    if 'home' not in sharp or 'away' not in sharp:
        return False

    # Determine favorites
    new_fav = 'home' if home_odds < away_odds else 'away'
    sharp_fav = 'home' if sharp['home'] < sharp['away'] else 'away'

    if new_fav == sharp_fav:
        return False  # Same favorite, no inversion

    # Only trigger if SHARP shows a clear favorite (ratio > 1.3)
    # This catches cases where Pinnacle shows clear favorite but provider shows opposite
    # e.g., Pinnacle home=7.38/away=1.11 (away fav) vs Polymarket home=1.94/away=2.06 (home fav)
    # Lower threshold (1.3) catches more edge cases like Pinnacle 1.63/2.29 (ratio 1.4)
    sharp_ratio = max(sharp['home'], sharp['away']) / min(sharp['home'], sharp['away'])
    if sharp_ratio < 1.3:
        return False  # Sharp odds are close, could be legitimate difference

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
            # Negate spread points when swapping
            if new_outcome.get('point') is not None:
                new_outcome['point'] = -new_outcome['point']
        elif name in ['away', 'borta', '2']:
            new_outcome['name'] = 'home'
            if new_outcome.get('point') is not None:
                new_outcome['point'] = -new_outcome['point']

        swapped.append(new_outcome)
    return swapped


def store_polymarket_event(
    session,
    event: StandardEvent,
    kambi_sport: str,
    event_cache: dict,
    fuzzy_threshold: int = 90,
    min_individual_score: int = 80,
    odds_batch: "OddsBatchProcessor" = None,
    pinnacle_points_cache: dict = None,
    sharp_odds_cache: dict = None,
    date_index: dict = None,
) -> tuple[bool, int, int]:
    """
    Store Polymarket event in database with fuzzy matching.

    Args:
        session: SQLAlchemy session
        event: StandardEvent from Polymarket
        kambi_sport: Normalized sport name
        event_cache: Dict {sport: {event_id: (home, away, date)}} for O(1) lookup
        fuzzy_threshold: Minimum average match score (default 90)
        min_individual_score: Minimum score for EACH team (default 80)

    Returns:
        (is_new_event, odds_processed, odds_new)
    """
    from ..matching.matcher import get_team_match_score

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

    default_id = generate_canonical_id(kambi_sport, home_team, away_team, event.start_time)

    # Diagnostic logging for matching
    if isinstance(event.start_time, datetime):
        date_str = event.start_time.strftime("%Y%m%d")
    else:
        date_str = str(event.start_time)[:10].replace('-', '') if event.start_time else "00000000"

    logger.debug(
        f"[polymarket] Matching '{home_team} vs {away_team}' sport={kambi_sport} "
        f"date={date_str} default_id={default_id} "
        f"cache_sports={list(event_cache.keys())} "
        f"candidates_in_sport={len(event_cache.get(kambi_sport, {}))}"
    )

    # Fuzzy match against existing events in cache (e.g., from Pinnacle)
    matched_id = None
    teams_swapped = False

    # 1. Check if default ID exists (exact match) — memory cache first, DB fallback
    sport_events_poly = event_cache.get(kambi_sport, {})
    if default_id in sport_events_poly or session.query(Event.id).filter(Event.id == default_id).first():
        matched_id = default_id
    else:
        # 2. Check swapped team order
        swapped_id = generate_canonical_id(kambi_sport, away_team, home_team, event.start_time)
        if swapped_id in sport_events_poly or session.query(Event.id).filter(Event.id == swapped_id).first():
            matched_id = swapped_id
            teams_swapped = True
            logger.debug(
                f"[polymarket] Aligned '{home_team} vs {away_team}' -> "
                f"canonical event with swapped teams"
            )
        else:
            # 3. Fuzzy match against cache (in case of different name normalization)
            # Use date index for O(1) candidate lookup instead of O(N) scan
            if date_index is not None:
                raw_candidates = _get_date_candidates(event_cache, date_index, kambi_sport, date_str)
                candidates = [(pid, home, away) for pid, home, away, _date in raw_candidates]
            else:
                # Fallback: O(N) scan if no date index provided
                sport_events = event_cache.get(kambi_sport, {})
                candidates = []
                for pid, (cached_home, cached_away, cached_date) in sport_events.items():
                    if cached_date == date_str:
                        candidates.append((pid, cached_home, cached_away))

            best_score = 0
            best_match_id = None
            best_is_swapped = False

            for pid, cached_home, cached_away in candidates:
                # Skip if this is the same ID we'd generate (already checked)
                if pid == default_id:
                    continue

                # Get individual scores for DIRECT match
                home_direct = get_team_match_score(home_team, cached_home)
                away_direct = get_team_match_score(away_team, cached_away)

                # Get individual scores for SWAPPED match
                home_swapped = get_team_match_score(home_team, cached_away)
                away_swapped = get_team_match_score(away_team, cached_home)

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

                # Skip if below thresholds
                if avg_score < fuzzy_threshold:
                    continue
                if team1_score < min_individual_score or team2_score < min_individual_score:
                    continue

                if avg_score > best_score:
                    best_score = avg_score
                    best_match_id = pid
                    best_is_swapped = is_swapped

            if best_match_id and session.query(Event.id).filter(Event.id == best_match_id).first():
                matched_id = best_match_id
                teams_swapped = best_is_swapped
                logger.debug(
                    f"[polymarket] Fuzzy matched '{home_team} vs {away_team}' -> "
                    f"existing event (score: {best_score:.0f}, swapped: {best_is_swapped})"
                )

    # If no match found, skip — Polymarket is not a sharp source,
    # so unmatched events are useless (no sharp baseline to compare against)
    if not matched_id:
        logger.debug(
            f"[polymarket] Skipped '{home_team} vs {away_team}' ({kambi_sport}) - no sharp match"
        )
        return False, 0, 0

    # Add to sport-indexed cache (use Polymarket's team order for cache key)
    _update_event_cache(
        event_cache, date_index or {},
        kambi_sport, matched_id, home_team, away_team, date_str,
    )

    # Create/get event
    db_event = session.query(Event).filter(Event.id == matched_id).first()
    is_new_event = False

    if not db_event:
        # Convert start_time to datetime if string
        start_dt = event.start_time
        if isinstance(start_dt, str):
            try:
                start_dt = datetime.fromisoformat(start_dt.replace('Z', '+00:00'))
            except (ValueError, TypeError):
                start_dt = None

        db_event = Event(
            id=matched_id,
            sport=kambi_sport,
            league=event.league,
            home_team=home_team,
            away_team=away_team,
            start_time=start_dt,
        )
        session.add(db_event)
        is_new_event = True

    # Use canonical event's home/away for outcome normalization
    # This ensures consistent home/away mapping when Polymarket lists teams in different order
    canonical_home = db_event.home_team
    canonical_away = db_event.away_team

    # Extract home/away odds from Polymarket outcomes for inversion detection
    # CRITICAL: Only use winner-market (1x2/moneyline) odds for inversion check.
    # Spread/total odds have different semantics (home spread=-0.5 doesn't indicate favorite)
    # and would overwrite moneyline odds due to last-write-wins in this loop.
    poly_home_odds, poly_away_odds = None, None
    for market in event.markets:
        market_type = normalize_market(market.get("question", "") or market.get("type", ""))
        if market_type in ('1x2', 'moneyline'):
            for outcome in market.get("outcomes", []):
                norm = normalize_outcome(
                    outcome.get("name", ""),
                    home_team,  # Use Polymarket's team order for normalization
                    away_team
                )
                if norm == "home":
                    poly_home_odds = outcome.get("odds")
                elif norm == "away":
                    poly_away_odds = outcome.get("odds")

    # Track odds inversion (separate from team order swap)
    odds_inverted = False

    # Check for odds-based inversion against sharp source (Pinnacle)
    if matched_id and poly_home_odds and poly_away_odds:
        # Convert to canonical order if teams were swapped
        if teams_swapped:
            canonical_home_odds = poly_away_odds
            canonical_away_odds = poly_home_odds
        else:
            canonical_home_odds = poly_home_odds
            canonical_away_odds = poly_away_odds

        inversion_result = detect_and_fix_inversion(
            session, matched_id, "polymarket", canonical_home_odds, canonical_away_odds,
            sharp_odds_cache=sharp_odds_cache,
        )
        if inversion_result:
            odds_inverted = True
            logger.debug(
                f"[polymarket] Detected inverted odds for {matched_id}: "
                f"canonical H={canonical_home_odds:.2f}/A={canonical_away_odds:.2f}"
            )

    # Determine if we need to swap during storage
    # For Polymarket, team order is already handled by using canonical teams in normalization
    # So we ONLY need to swap if odds are inverted vs sharp (Pinnacle)
    should_swap_outcomes = odds_inverted

    # Look up Pinnacle's spread/total points for this event (if matched)
    pinnacle_points = _get_pinnacle_points(session, matched_id, cache=pinnacle_points_cache) if matched_id else {"spread": None, "total": None}

    # Store odds
    odds_processed = 0
    odds_new = 0

    for market in event.markets:
        if not market.get("is_active", True):  # Default to active if missing
            continue

        market_type = normalize_market(market.get("question", "") or market.get("type", ""))

        # Only store allowed markets (1x2, moneyline, spread, total)
        if market_type not in ALLOWED_MARKETS:
            continue

        outcomes = market.get("outcomes", [])

        # For spread/total: only keep lines matching Pinnacle's point
        # Determine canonical home/over point (accounting for swap)
        if market_type in ("spread", "total"):
            # Find the home/over outcome's point
            target_names = ("home",) if market_type == "spread" else ("over",)
            home_point = None
            for o in outcomes:
                norm = normalize_outcome(o.get("name", ""), canonical_home, canonical_away)
                if norm in target_names and o.get("point") is not None:
                    pt = o["point"]
                    # If swapping, negate spread point to get canonical perspective
                    if should_swap_outcomes and market_type == "spread":
                        pt = -pt
                    home_point = pt
                    break
            if not _point_matches_pinnacle(market_type, home_point, pinnacle_points):
                continue

        for outcome in outcomes:
            outcome_name = outcome.get("name", "")
            odds = outcome.get("odds", 0)
            point_value = outcome.get("point")

            if odds <= 1 or odds > 100:
                continue

            odds_processed += 1
            outcome_norm = normalize_outcome(outcome_name, canonical_home, canonical_away)

            # Skip outcomes that couldn't be normalized to home/away/draw/over/under
            # (e.g., player names from prop markets that slipped through parsing)
            if outcome_norm not in ('home', 'away', 'draw', 'over', 'under'):
                continue

            # Swap home/away based on XOR of team swap and odds inversion
            # - teams_swapped only: swap (team order different from canonical)
            # - odds_inverted only: swap (odds favor wrong team)
            # - both: don't swap (they cancel out)
            if should_swap_outcomes:
                if outcome_norm == "home":
                    outcome_norm = "away"
                elif outcome_norm == "away":
                    outcome_norm = "home"
                # Negate spread points when swapping home/away
                if market_type == "spread" and point_value is not None:
                    point_value = -point_value

            if odds_batch:
                odds_batch.add(matched_id, "polymarket", market_type, outcome_norm, odds, point_value)
            else:
                odds_new += upsert_odds(session, matched_id, "polymarket", market_type, outcome_norm, odds, point_value)

    return is_new_event, odds_processed, odds_new


def _resolve_event_id(
    session,
    event: StandardEvent,
    provider: str,
    event_cache: dict,
    fuzzy_threshold: int,
    min_individual_score: int,
    prefix_filter_length: int,
    require_match: bool,
    max_asymmetry_diff: int = 25,
    min_for_asymmetry_check: int = 80,
    date_index: dict = None,
) -> tuple[str | None, bool]:
    """
    Resolve event to a canonical ID via exact match, fuzzy match, or swapped-team fallback.

    Returns:
        (event_id, is_swapped) or (None, False) if require_match=True and no match found.
    """
    from ..matching.matcher import get_team_match_score

    default_id = generate_canonical_id(event.sport, event.home_team, event.away_team, event.start_time)

    # 1. Exact match on canonical ID — check memory cache first, DB fallback
    sport_events = event_cache.get(event.sport, {})
    if default_id in sport_events:
        return default_id, False
    if session.query(Event.id).filter(Event.id == default_id).first():
        return default_id, False

    # 2. Fuzzy match against memory cache
    if isinstance(event.start_time, str):
        event_date = event.start_time.split('T')[0].replace('-', '')
    elif hasattr(event.start_time, 'strftime'):
        event_date = event.start_time.strftime('%Y%m%d')
    else:
        event_date = "00000000"

    # Use date index for O(1) candidate lookup instead of O(N) scan
    if date_index is not None:
        candidates = _get_date_candidates(event_cache, date_index, event.sport, event_date)
    else:
        # Fallback: O(N) scan if no date index provided
        sport_events = event_cache.get(event.sport, {})
        candidates = []
        for pid, (home, away, date) in sport_events.items():
            if date == event_date:
                candidates.append((pid, home, away, date))
            else:
                try:
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
        if prefix_filtered:
            candidates = prefix_filtered

    # Try fuzzy matching with STRICT validation
    best_score = 0
    best_match_id = None
    best_match_details = None
    best_is_swapped = False

    near_miss_score = 0
    near_miss_details = None
    near_miss_reason = None

    for pid, poly_home, poly_away, date in candidates:
        home_direct = get_team_match_score(event.home_team, poly_home)
        away_direct = get_team_match_score(event.away_team, poly_away)
        home_swapped = get_team_match_score(event.home_team, poly_away)
        away_swapped = get_team_match_score(event.away_team, poly_home)

        direct_avg = (home_direct + away_direct) / 2
        swapped_avg = (home_swapped + away_swapped) / 2

        is_swapped = swapped_avg > direct_avg
        if is_swapped:
            team1_score, team2_score = home_swapped, away_swapped
            avg_score = swapped_avg
        else:
            team1_score, team2_score = home_direct, away_direct
            avg_score = direct_avg

        is_new_best = avg_score > near_miss_score
        if is_new_best:
            near_miss_score = avg_score
            near_miss_details = (poly_home, poly_away, team1_score, team2_score)

        if avg_score < fuzzy_threshold:
            if is_new_best:
                near_miss_reason = f"avg {avg_score:.0f} < threshold {fuzzy_threshold}"
            continue

        if team1_score < min_individual_score or team2_score < min_individual_score:
            if is_new_best:
                near_miss_reason = f"individual {team1_score:.0f}/{team2_score:.0f}, min required {min_individual_score}"
            logger.debug(
                f"[{provider}] Rejected match '{event.home_team} vs {event.away_team}' -> "
                f"'{poly_home} vs {poly_away}': individual scores {team1_score:.0f}/{team2_score:.0f}"
            )
            continue

        score_diff = abs(team1_score - team2_score)
        if score_diff > max_asymmetry_diff and min(team1_score, team2_score) < min_for_asymmetry_check:
            if is_new_best:
                near_miss_reason = f"asymmetric {team1_score:.0f}/{team2_score:.0f}"
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
        poly_home, poly_away, t1, t2 = best_match_details
        swap_note = " [SWAPPED]" if best_is_swapped else ""
        logger.debug(
            f"[{provider}] Matched '{event.home_team} vs {event.away_team}' -> "
            f"'{poly_home} vs {poly_away}' (scores: {t1:.0f}/{t2:.0f}, avg: {best_score:.0f}){swap_note}"
        )
        return best_match_id, best_is_swapped

    # 3. No fuzzy match - check if canonical event exists with swapped teams
    swapped_id = generate_canonical_id(event.sport, event.away_team, event.home_team, event.start_time)
    if swapped_id in sport_events or session.query(Event.id).filter(Event.id == swapped_id).first():
        logger.debug(
            f"[{provider}] Aligned '{event.home_team} vs {event.away_team}' -> "
            f"canonical event with swapped teams (using {swapped_id})"
        )
        return swapped_id, True

    # 4. No match at all
    if require_match:
        logger.debug(
            f"[{provider}] Skipped '{event.home_team} vs {event.away_team}' - no sharp match"
        )
        return None, False

    # Use default ID — sharp providers creating new events (expected, log at DEBUG)
    if near_miss_details:
        nm_home, nm_away, nm_t1, nm_t2 = near_miss_details
        logger.debug(
            f"[{provider}] No match for '{event.home_team} vs {event.away_team}' "
            f"({len(candidates)} candidates, best: '{nm_home} vs {nm_away}' "
            f"score {near_miss_score:.0f}, reason: {near_miss_reason})"
        )
    elif candidates:
        logger.debug(
            f"[{provider}] No match for '{event.home_team} vs {event.away_team}' "
            f"({len(candidates)} candidates, all below scoring threshold)"
        )
    else:
        logger.debug(
            f"[{provider}] No match for '{event.home_team} vs {event.away_team}' "
            f"(0 candidates for {event.sport})"
        )

    return default_id, False


def store_provider_event(
    session,
    event: StandardEvent,
    provider: str,
    event_cache: dict,
    fuzzy_threshold: int = 90,
    min_individual_score: int = 75,
    prefix_filter_length: int = 3,
    odds_batch: "OddsBatchProcessor" = None,
    require_match: bool = False,
    pinnacle_points_cache: dict = None,
    sharp_odds_cache: dict = None,
    max_asymmetry_diff: int = 25,
    min_for_asymmetry_check: int = 80,
    date_index: dict = None,
) -> tuple[bool, int, int]:
    """
    Store provider event with STRICT fuzzy matching against existing events.

    Returns:
        (is_new_event, odds_processed, odds_new)
    """
    # Resolve event ID via exact/fuzzy/swapped matching
    matched_id, fuzzy_swapped = _resolve_event_id(
        session, event, provider, event_cache,
        fuzzy_threshold, min_individual_score, prefix_filter_length, require_match,
        max_asymmetry_diff, min_for_asymmetry_check,
        date_index=date_index,
    )

    if matched_id is None:
        return (False, 0, 0)

    final_id = matched_id

    # Create event if doesn't exist
    db_event = session.query(Event).filter(Event.id == final_id).first()
    is_new_event = False

    if not db_event:
        start_dt = event.start_time
        if isinstance(start_dt, str):
            try:
                start_dt = datetime.fromisoformat(start_dt.replace('Z', '+00:00'))
            except (ValueError, TypeError):
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
        if isinstance(event.start_time, str):
            date_str = event.start_time.split('T')[0].replace('-', '')
        elif hasattr(event.start_time, 'strftime'):
            date_str = event.start_time.strftime('%Y%m%d')
        else:
            date_str = "00000000"

        _update_event_cache(
            event_cache, date_index or {},
            event.sport, final_id, db_event.home_team, db_event.away_team, date_str,
        )

    # Extract home/away odds from event markets for inversion detection
    # Only use 1x2/moneyline — spread odds have inverted favorite semantics
    home_odds, away_odds = None, None
    for market in event.markets:
        market_type = normalize_market(market.get('type', ''))
        if market_type in ('1x2', 'moneyline'):
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

    # Track if we need an odds inversion swap (separate from team order swap)
    odds_inverted = False

    if matched_id and home_odds and away_odds:
        # If teams were already swapped, the extracted odds are in the provider's order.
        # To check inversion against canonical (Pinnacle), we need to map to canonical order.
        if fuzzy_swapped:
            # Provider's home→canonical away, provider's away→canonical home
            canonical_home_odds = away_odds
            canonical_away_odds = home_odds
        else:
            canonical_home_odds = home_odds
            canonical_away_odds = away_odds

        # Check for odds inversion against sharp source
        if detect_and_fix_inversion(session, matched_id, provider, canonical_home_odds, canonical_away_odds,
                                    sharp_odds_cache=sharp_odds_cache):
            odds_inverted = True

    # Swap outcomes if:
    # - Team order is different from canonical (fuzzy_swapped), OR
    # - Odds are inverted vs sharp (odds_inverted)
    # Note: if BOTH are true, they cancel out (double swap = no swap)
    should_swap = fuzzy_swapped != odds_inverted  # XOR: swap if exactly one is true

    if should_swap:
        logger.debug(
            f"[{provider}] Swapping outcomes for {final_id} to align with canonical event"
        )

    # For soft books, look up Pinnacle's spread/total points to filter lines
    is_sharp = provider.lower() in SHARP_PROVIDERS
    pinnacle_points = (
        _get_pinnacle_points(session, final_id, cache=pinnacle_points_cache)
        if not is_sharp and final_id
        else {"spread": None, "total": None}
    )

    # Store odds
    odds_processed = 0
    odds_new = 0

    # Determine teams for normalization
    # When team order differs from canonical, use swapped teams so team name outcomes
    # (e.g., "Manhattan") normalize correctly to the canonical home/away
    if should_swap:
        norm_home = event.away_team  # Provider's away = canonical home
        norm_away = event.home_team  # Provider's home = canonical away
    else:
        norm_home = event.home_team
        norm_away = event.away_team

    for market in event.markets:
        market_type = normalize_market(market.get('type', ''))
        if market_type not in ALLOWED_MARKETS:
            continue

        outcomes = market.get('outcomes', [])

        # Swap home/away if team order differs from canonical event
        if should_swap:
            outcomes = swap_home_away_outcomes(outcomes)

        # For soft book spread/total: only keep lines matching Pinnacle's point
        # Check AFTER swap so "home" point is in canonical perspective
        if not is_sharp and market_type in ("spread", "total"):
            home_point = next(
                (o.get("point") for o in outcomes
                 if o.get("name", "").lower() in ("home", "over") and o.get("point") is not None),
                None,
            )
            if not _point_matches_pinnacle(market_type, home_point, pinnacle_points):
                continue

        for outcome in outcomes:
            outcome_name = normalize_outcome(outcome.get('name', ''), norm_home, norm_away)
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
        """Process pending records with bulk operations.

        Includes retry logic for SQLite "database is locked" errors that occur
        during concurrent extraction (multiple providers flushing simultaneously).
        """
        if not self._pending:
            return

        import time
        from sqlalchemy.exc import OperationalError as SAOperationalError

        max_retries = 3
        for attempt in range(max_retries):
            try:
                self._flush_inner()
                return
            except SAOperationalError as e:
                if "database is locked" in str(e) and attempt < max_retries - 1:
                    wait = 0.5 * (2 ** attempt)  # 0.5s, 1s, 2s
                    logger.warning(
                        f"OddsBatchProcessor: DB locked on flush (attempt {attempt + 1}/{max_retries}), "
                        f"retrying in {wait:.1f}s..."
                    )
                    self.session.rollback()
                    time.sleep(wait)
                else:
                    raise

    def _flush_inner(self):
        """Inner flush logic — separated for retry wrapper."""
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
        try:
            self.flush()
        except Exception:
            if exc_type is None:
                raise
            logger.warning("OddsBatchProcessor: flush failed during exception handling")
        return False
