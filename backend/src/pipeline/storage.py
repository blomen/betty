"""
Pipeline Storage

Functions for storing events and odds in the database.
"""

import json
import logging
import re
from datetime import UTC, datetime, timedelta

from sqlalchemy import literal_column

from ..constants import (
    ALLOWED_MARKETS,
    ENRICHMENT_MARKETS,
    EXTENDED_MARKET_PROVIDERS,
    PROVIDER_CANONICAL,
)
from ..core import StandardEvent
from ..db.models import DeferredEvent, Event, Odds
from ..matching import (
    normalize_market,
    normalize_outcome,
    parse_teams_from_title,
)
from ..matching.normalizer import generate_canonical_id

logger = logging.getLogger(__name__)

# Module-level cache of known event IDs to avoid redundant DB lookups during Polymarket matching
_known_event_ids: set[str] = set()

# Youth/reserve league indicators — used to prevent cross-tier matching
_YOUTH_INDICATORS = re.compile(
    r"\bu[- ]?(?:17|18|19|20|21|23)\b|"
    r"\breserve[s]?\b|"
    r"\byouth\b|"
    r"\bdevelopment\b|"
    r"\bespoir[s]?\b|"
    r"\bjunior[s]?\b|"
    r"\b[bB] team\b|"
    r"\bprimavera\b|"
    r"\bjuvenil\b",
    re.IGNORECASE,
)


def _is_youth_league(league: str) -> bool:
    """Check if a league name indicates a youth/reserve competition."""
    return bool(_YOUTH_INDICATORS.search(league)) if league else False


def _extract_date_str(start_time) -> str:
    """Extract YYYYMMDD date string from start_time (str or datetime)."""
    if isinstance(start_time, str):
        return start_time.split("T")[0].replace("-", "")
    elif hasattr(start_time, "strftime"):
        return start_time.strftime("%Y%m%d")
    return "00000000"


def _parse_display_names(event_name: str) -> tuple[str | None, str | None]:
    """Parse original cased team names from event.name (e.g. 'León vs Necaxa')."""
    if not event_name:
        return None, None
    for sep in [" vs. ", " vs ", " @ "]:
        if sep in event_name:
            parts = event_name.split(sep, 1)
            if len(parts) == 2:
                home = parts[0].strip()
                away = parts[1].strip()
                if home and away:
                    return home, away
    return None, None


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
            home, away, date = entry[0], entry[1], entry[2]
            league = entry[3] if len(entry) > 3 else ""
            candidates.append((pid, home, away, date, league))
    return candidates


def _update_event_cache(
    event_cache: dict,
    date_index: dict,
    sport: str,
    event_id: str,
    home: str,
    away: str,
    date_str: str,
    league: str = "",
):
    """Update both event_cache and date_index atomically."""
    if sport not in event_cache:
        event_cache[sport] = {}
    if event_id not in event_cache[sport]:
        event_cache[sport][event_id] = (home, away, date_str, league)

        # Update date index
        if sport not in date_index:
            date_index[sport] = {}
        if date_str not in date_index[sport]:
            date_index[sport][date_str] = set()
        date_index[sport][date_str].add(event_id)


# Enhanced inversion thresholds (2026-05-26)
INVERSION_RATIO_THRESHOLD = 1.10  # was 1.50 — catches near-coinflip inversions
INVERSION_DEVIG_DISAGREEMENT_PP = 0.25  # 25pp probability disagreement on home outcome
POST_SWAP_DISAGREEMENT_PP = 0.15  # 15pp threshold after attempting swap


def _devig_prob_home(home_odds: float, away_odds: float) -> float:
    """Return de-vigged P(home) for a 2-way market, or 0.5 if odds invalid."""
    if home_odds <= 1 or away_odds <= 1:
        return 0.5
    p_home_raw = 1.0 / home_odds
    p_away_raw = 1.0 / away_odds
    total = p_home_raw + p_away_raw
    if total <= 0:
        return 0.5
    return p_home_raw / total


def _is_inversion_detected(
    sharp_home: float,
    sharp_away: float,
    soft_home: float,
    soft_away: float,
) -> bool:
    """Detect home/away inversion using two signals:

    1. Raw odds ratio: if sharp ratio > 1.10 AND books disagree on favored side.
    2. Devig probability: if devigged P(home) differs by > 25pp between books.
    """
    if any(o <= 1 for o in (sharp_home, sharp_away, soft_home, soft_away)):
        return False

    # Signal 1: ratio + favorite-side disagreement
    sharp_ratio = max(sharp_home, sharp_away) / min(sharp_home, sharp_away)
    sharp_home_favored = sharp_home < sharp_away
    soft_home_favored = soft_home < soft_away
    if sharp_ratio > INVERSION_RATIO_THRESHOLD and sharp_home_favored != soft_home_favored:
        return True

    # Signal 2: devig probability disagreement
    sharp_p_home = _devig_prob_home(sharp_home, sharp_away)
    soft_p_home = _devig_prob_home(soft_home, soft_away)
    if abs(sharp_p_home - soft_p_home) > INVERSION_DEVIG_DISAGREEMENT_PP:
        return True

    return False


def _validate_post_swap(
    sharp_home: float,
    sharp_away: float,
    soft_home: float,
    soft_away: float,
) -> bool:
    """After swap (or with no swap needed), confirm the books agree within
    POST_SWAP_DISAGREEMENT_PP. Returns True if validated."""
    if any(o <= 1 for o in (sharp_home, sharp_away, soft_home, soft_away)):
        return False
    sharp_p = _devig_prob_home(sharp_home, sharp_away)
    soft_p = _devig_prob_home(soft_home, soft_away)
    return abs(sharp_p - soft_p) <= POST_SWAP_DISAGREEMENT_PP


def detect_and_fix_inversion(
    session,
    event_id: str,
    provider: str,
    home_odds: float | None,
    away_odds: float | None,
    sharp_odds_cache: dict = None,
) -> tuple[bool, bool]:
    """Detect if provider odds are inverted vs sharp.

    Returns (swap_needed, validated):
      - swap_needed: True if home/away should be swapped before storing
      - validated: True if the final (post-swap-if-any) odds agree with Pinnacle
        within POST_SWAP_DISAGREEMENT_PP; False means the event should be skipped
        by the scanner (home_away_validated=False).

    Uses two signals to detect inversion:
      1. Sharp odds ratio > 1.10 AND books disagree on favored side.
      2. Devigged P(home) differs by > 25pp between Pinnacle and soft book.

    This catches near-coinflip inversions (e.g. SSG Landers v Samsung Lions,
    Pinnacle ratio 1.06) that the old 1.5-threshold missed.
    """
    if home_odds is None or away_odds is None or home_odds <= 1 or away_odds <= 1:
        return False, True  # no swap, treat as validated (no data to check)

    # Check cache first (Pinnacle data is static during a run)
    if sharp_odds_cache is not None and event_id in sharp_odds_cache:
        sharp = sharp_odds_cache[event_id]
    else:
        # Get sharp odds (Pinnacle only) - exact match uses index
        sharp_rows = (
            session.query(Odds)
            .filter(
                Odds.event_id == event_id,
                Odds.provider_id == "pinnacle",
                Odds.outcome.in_(["home", "away"]),
                Odds.market.in_(["1x2", "moneyline"]),
            )
            .all()
        )

        sharp = {o.outcome: o.odds for o in sharp_rows}
        if sharp_odds_cache is not None:
            sharp_odds_cache[event_id] = sharp

    if "home" not in sharp or "away" not in sharp:
        return False, True  # no sharp data to compare — treat as validated

    sharp_home = sharp["home"]
    sharp_away = sharp["away"]

    swap_needed = _is_inversion_detected(sharp_home, sharp_away, home_odds, away_odds)

    if swap_needed:
        # After swap: soft home↔away are flipped for validation
        validated = _validate_post_swap(sharp_home, sharp_away, away_odds, home_odds)
        logger.debug(
            "[%s] Fixing inverted odds for %s: H=%.2f/A=%.2f vs sharp H=%.2f/A=%.2f (validated=%s)",
            provider,
            event_id,
            home_odds,
            away_odds,
            sharp_home,
            sharp_away,
            validated,
        )
    else:
        validated = _validate_post_swap(sharp_home, sharp_away, home_odds, away_odds)

    return swap_needed, validated


def swap_home_away_outcomes(outcomes: list[dict]) -> list[dict]:
    """Swap home and away outcome labels in a list of outcomes."""
    swapped = []
    for o in outcomes:
        name = o.get("name", "").lower()
        new_outcome = dict(o)

        # Swap home <-> away
        if name in ["home", "hemma", "1"]:
            new_outcome["name"] = "away"
            # Negate spread points when swapping
            if new_outcome.get("point") is not None:
                new_outcome["point"] = -new_outcome["point"]
        elif name in ["away", "borta", "2"]:
            new_outcome["name"] = "home"
            if new_outcome.get("point") is not None:
                new_outcome["point"] = -new_outcome["point"]

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
    date_str = _extract_date_str(event.start_time)

    logger.debug(
        f"[polymarket] Matching '{home_team} vs {away_team}' sport={kambi_sport} "
        f"date={date_str} default_id={default_id} "
        f"cache_sports={list(event_cache.keys())} "
        f"candidates_in_sport={len(event_cache.get(kambi_sport, {}))}"
    )

    # Fuzzy match against existing events in cache (e.g., from Pinnacle)
    matched_id = None
    teams_swapped = False

    # 1. Check if default ID exists (exact match) — memory cache first, module cache, then DB fallback
    sport_events_poly = event_cache.get(kambi_sport, {})
    if default_id in sport_events_poly or default_id in _known_event_ids:
        matched_id = default_id
    elif session.query(Event.id).filter(Event.id == default_id).first():
        _known_event_ids.add(default_id)
        matched_id = default_id
    else:
        # 2. Check swapped team order
        swapped_id = generate_canonical_id(kambi_sport, away_team, home_team, event.start_time)
        if swapped_id in sport_events_poly or swapped_id in _known_event_ids:
            matched_id = swapped_id
            teams_swapped = True
            logger.debug(f"[polymarket] Aligned '{home_team} vs {away_team}' -> canonical event with swapped teams")
        elif session.query(Event.id).filter(Event.id == swapped_id).first():
            _known_event_ids.add(swapped_id)
            matched_id = swapped_id
            teams_swapped = True
            logger.debug(
                f"[polymarket] Aligned '{home_team} vs {away_team}' -> canonical event with swapped teams (DB)"
            )
        else:
            # 3. Fuzzy match against cache (in case of different name normalization)
            # Use date index for O(1) candidate lookup instead of O(N) scan
            # NOTE: keep the date in the tuple — needed to prefer exact-date
            # candidates over ±1-day candidates in the loop below.
            if date_index is not None:
                raw_candidates = _get_date_candidates(event_cache, date_index, kambi_sport, date_str)
                candidates = [(pid, home, away, cdate) for pid, home, away, cdate, *_ in raw_candidates]
            else:
                # Fallback: O(N) scan if no date index provided
                sport_events = event_cache.get(kambi_sport, {})
                candidates = []
                for pid, (cached_home, cached_away, cached_date) in sport_events.items():
                    if cached_date == date_str:
                        candidates.append((pid, cached_home, cached_away, cached_date))

            best_score = 0
            best_match_id = None
            best_is_swapped = False
            # Exact-date match wins absolutely over ±1-day candidates. Without
            # this gate, consecutive-day same-team games (MLB series, multi-day
            # tennis rounds) collapse onto the wrong canonical event (observed
            # on 2026-05-18 for Yankees-Blue Jays).
            best_is_exact_date = False

            for pid, cached_home, cached_away, cached_date in candidates:
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

                # Reject ±1-day candidate when actual start_time gap > 6h.
                # See _resolve_event_id for the rationale; same MLB-series
                # / multi-day-tennis bug applies here.
                candidate_is_exact_date = cached_date == date_str
                if not candidate_is_exact_date and event.start_time is not None:
                    try:
                        with session.no_autoflush:
                            cand_start = session.query(Event.start_time).filter(Event.id == pid).scalar()
                        if cand_start is not None:
                            ev_start = event.start_time
                            if isinstance(ev_start, str):
                                from datetime import datetime as _dt

                                ev_start = _dt.fromisoformat(ev_start.replace("Z", "+00:00"))
                            if ev_start.tzinfo is not None:
                                ev_start = ev_start.replace(tzinfo=None)
                            if cand_start.tzinfo is not None:
                                cand_start = cand_start.replace(tzinfo=None)
                            gap_h = abs((ev_start - cand_start).total_seconds()) / 3600.0
                            if gap_h > 6.0:
                                logger.debug(
                                    f"[polymarket] Rejected adj-day match '{home_team} vs {away_team}' "
                                    f"-> '{cached_home} vs {cached_away}': start_time gap {gap_h:.1f}h > 6h"
                                )
                                continue
                    except Exception as e:
                        logger.debug(f"[polymarket] adj-day start_time check failed for {pid}: {e}")

                if candidate_is_exact_date and not best_is_exact_date:
                    promote = True
                elif candidate_is_exact_date == best_is_exact_date:
                    promote = avg_score > best_score
                else:
                    promote = False
                if promote:
                    best_score = avg_score
                    best_match_id = pid
                    best_is_swapped = is_swapped
                    best_is_exact_date = candidate_is_exact_date

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
        logger.debug(f"[polymarket] Skipped '{home_team} vs {away_team}' ({kambi_sport}) - no sharp match")
        return False, 0, 0

    # Add to sport-indexed cache (use Polymarket's team order for cache key)
    _update_event_cache(
        event_cache,
        date_index or {},
        kambi_sport,
        matched_id,
        home_team,
        away_team,
        date_str,
    )

    # Create/get event
    db_event = session.query(Event).filter(Event.id == matched_id).first()
    is_new_event = False

    if not db_event:
        # Convert start_time to datetime if string
        start_dt = event.start_time
        if isinstance(start_dt, str):
            try:
                start_dt = datetime.fromisoformat(start_dt.replace("Z", "+00:00"))
            except (ValueError, TypeError):
                start_dt = None

        display_home, display_away = _parse_display_names(event.name)

        db_event = Event(
            id=matched_id,
            sport=kambi_sport,
            league=event.league,
            home_team=home_team,
            away_team=away_team,
            display_home=display_home,
            display_away=display_away,
            start_time=start_dt,
        )
        session.add(db_event)
        session.flush()
        is_new_event = True

    # Update live scores from Polymarket (if available)
    if event.live_state:
        ls = event.live_state
        if ls.get("home_score") is not None:
            # Only update if Polymarket has newer data (or no existing data)
            if db_event.home_score is None or ls.get("match_status") == "finished":
                if teams_swapped:
                    db_event.home_score = ls.get("away_score")
                    db_event.away_score = ls.get("home_score")
                else:
                    db_event.home_score = ls["home_score"]
                    db_event.away_score = ls.get("away_score")
        if ls.get("match_status"):
            status = ls["match_status"]
            if status == "started":
                db_event.match_status = "live"
            elif status == "finished":
                db_event.match_status = "finished"
        if ls.get("match_minute") is not None:
            db_event.match_minute = ls["match_minute"]
        if ls.get("match_period") is not None:
            db_event.match_period = ls["match_period"]

        # Store score_raw and BO format from Polymarket in stats_json
        score_raw = ls.get("score_raw")
        if score_raw:
            existing = json.loads(db_event.stats_json) if db_event.stats_json else {}
            existing["score_raw"] = score_raw
            # Parse BO format from esports score_raw: "000-000|2-1|Bo3"
            bo_match = re.search(r"Bo(\d+)", score_raw)
            if bo_match:
                existing["bo"] = int(bo_match.group(1))
            db_event.stats_json = json.dumps(existing)

    # Use canonical event's home/away for outcome normalization
    # This ensures consistent home/away mapping when Polymarket lists teams in different order
    canonical_home = db_event.home_team
    canonical_away = db_event.away_team

    # Store odds
    odds_processed = 0
    odds_new = 0

    # Positional keywords: these map to home/away in normalize_outcome's fast
    # path and are RELATIVE to Polymarket's team order (e.g., spread parser
    # outputs "home"/"away" based on PM's team listing). When teams_swapped,
    # these need flipping to canonical order.
    # Team-name outcomes (e.g., "Celtics") are resolved by normalize_outcome
    # against canonical_home/canonical_away, so they're ALREADY canonical —
    # swapping would double-flip them.
    POSITIONAL_KEYWORDS = frozenset(
        {
            "home",
            "hemma",
            "1",
            "yes",
            "ja",
            "away",
            "borta",
            "2",
            "no",
            "nej",
        }
    )

    # --- Inversion guard ---------------------------------------------------
    # Polymarket's home/away normalization is heuristic and occasionally lands
    # a moneyline market's odds on the wrong team — which makes the arb scanner
    # pair the same physical team on both legs. Soft books are corrected by
    # detect_and_fix_inversion() (see store_provider_event); Polymarket used to
    # only LOG the mismatch. Pre-scan the moneyline market with the SAME
    # normalization the store loop uses, then ask detect_and_fix_inversion()
    # (Pinnacle-backed, with a DB fallback) whether the result is inverted. If
    # so, every outcome is flipped on store.
    def _poly_outcome_norm(raw_name: str) -> str | None:
        """Normalize a Polymarket outcome name to a canonical label, applying
        the teams_swapped positional flip. Shared by the pre-scan and the store
        loop so the two never diverge."""
        norm = normalize_outcome(raw_name, canonical_home, canonical_away)
        if norm not in ("home", "away", "draw", "over", "under"):
            return None
        if teams_swapped and raw_name.lower().strip() in POSITIONAL_KEYWORDS and norm in ("home", "away"):
            norm = "away" if norm == "home" else "home"
        return norm

    pre_ml_home = pre_ml_away = None
    for _market in event.markets:
        if not _market.get("is_active", True):
            continue
        if normalize_market(_market.get("question", "") or _market.get("type", "")) not in ("moneyline", "1x2"):
            continue
        for _outcome in _market.get("outcomes", []):
            _odds = _outcome.get("odds", 0)
            if _odds <= 1 or _odds > 100:
                continue
            _norm = _poly_outcome_norm(_outcome.get("name", ""))
            if _norm == "home":
                pre_ml_home = _odds
            elif _norm == "away":
                pre_ml_away = _odds
    if pre_ml_home and pre_ml_away:
        _poly_swap, _poly_validated = detect_and_fix_inversion(
            session, matched_id, "polymarket", pre_ml_home, pre_ml_away, sharp_odds_cache=sharp_odds_cache
        )
        poly_inverted = _poly_swap
    else:
        poly_inverted = False
        _poly_validated = True
    # Persist polymarket-derived validation on the Event row (best-effort —
    # the same event may be re-validated by store_provider_event for other
    # providers, which will overwrite this).
    if matched_id:
        _poly_event = session.query(Event).filter_by(id=matched_id).one_or_none()
        if _poly_event is not None:
            _poly_event.home_away_validated = _poly_validated
    if poly_inverted:
        logger.warning(f"[polymarket] Inverted odds for {matched_id} — correcting home/away on store")

    for market in event.markets:
        if not market.get("is_active", True):  # Default to active if missing
            continue

        market_type = normalize_market(market.get("question", "") or market.get("type", ""))

        # Only store allowed markets. Pinnacle gets enrichment markets too
        # Pinnacle/Polymarket get esports map markets for map-level scanning.
        allowed = ENRICHMENT_MARKETS if event.provider in EXTENDED_MARKET_PROVIDERS else ALLOWED_MARKETS
        if market_type not in allowed:
            continue

        outcomes = market.get("outcomes", [])

        # Store ALL spread/total lines — scanner groups by market+point
        # (e.g., "spread_-1.5") so value detection only compares matching points.

        # Build provider_meta from market-level metadata (e.g., event_slug for deep links)
        market_meta = market.get("provider_meta", {})
        scope = market.get("scope", "ft")

        for outcome in outcomes:
            outcome_name = outcome.get("name", "")
            odds = outcome.get("odds", 0)
            point_value = outcome.get("point")

            if odds <= 1 or odds > 100:
                continue

            odds_processed += 1
            outcome_norm = _poly_outcome_norm(outcome_name)

            # Skip outcomes that couldn't be normalized to home/away/draw/over/under
            # (e.g., player names from prop markets that slipped through parsing)
            if outcome_norm is None:
                continue

            # Apply the inversion correction decided by the pre-scan above.
            if poly_inverted and outcome_norm in ("home", "away"):
                outcome_norm = "away" if outcome_norm == "home" else "home"

            outcome_meta = outcome.get("provider_meta", {})
            provider_meta = {**market_meta, **outcome_meta} if (market_meta or outcome_meta) else None
            # Swap poly_home/poly_away in metadata to match canonical home/away.
            # teams_swapped flips it once (PM order -> canonical); poly_inverted
            # flips it again (our canonical assignment was wrong) — net = XOR.
            if (
                (teams_swapped != poly_inverted)
                and provider_meta
                and "poly_home" in provider_meta
                and "poly_away" in provider_meta
            ):
                provider_meta["poly_home"], provider_meta["poly_away"] = (
                    provider_meta["poly_away"],
                    provider_meta["poly_home"],
                )
            # CLOB microstructure (bid/ask/depth from order book)
            bid_value = outcome.get("bid")
            ask_value = outcome.get("ask")
            depth_value = outcome.get("depth_usd")

            if odds_batch:
                odds_batch.add(
                    matched_id,
                    "polymarket",
                    market_type,
                    outcome_norm,
                    odds,
                    point_value,
                    provider_meta=provider_meta,
                    bid=bid_value,
                    ask=ask_value,
                    depth_usd=depth_value,
                    scope=scope,
                )
            else:
                odds_new += upsert_odds(
                    session,
                    matched_id,
                    "polymarket",
                    market_type,
                    outcome_norm,
                    odds,
                    point_value,
                    provider_meta=provider_meta,
                    bid=bid_value,
                    ask=ask_value,
                    depth_usd=depth_value,
                    scope=scope,
                )

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
    # no_autoflush: prevent premature flush during read queries — pending Event
    # inserts from prior events can conflict with concurrent providers and
    # poison the session if autoflush triggers here.
    with session.no_autoflush:
        if session.query(Event.id).filter(Event.id == default_id).first():
            return default_id, False

    # 2. Fuzzy match against memory cache
    event_date = _extract_date_str(event.start_time)

    # Use date index for O(1) candidate lookup instead of O(N) scan
    if date_index is not None:
        candidates = _get_date_candidates(event_cache, date_index, event.sport, event_date)
    else:
        # Fallback: O(N) scan if no date index provided
        sport_events = event_cache.get(event.sport, {})
        candidates = []
        for pid, entry in sport_events.items():
            home, away, date = entry[0], entry[1], entry[2]
            league = entry[3] if len(entry) > 3 else ""
            if date == event_date:
                candidates.append((pid, home, away, date, league))
            else:
                try:
                    if date and event_date:
                        d1 = datetime.strptime(event_date, "%Y%m%d")
                        d2 = datetime.strptime(date, "%Y%m%d")
                        if abs((d1 - d2).days) <= 1:
                            candidates.append((pid, home, away, date, league))
                except (ValueError, TypeError):
                    pass

    # Pre-filter by team name prefix for better performance
    # Uses word-level prefixes to handle reversed name order (e.g. "Cina Federico" vs "Federico Cina")
    if prefix_filter_length > 0 and len(candidates) > 10:

        def _word_prefixes(name: str) -> set:
            return {w[:prefix_filter_length].lower() for w in name.split() if len(w) >= prefix_filter_length}

        event_prefixes = _word_prefixes(event.home_team) | _word_prefixes(event.away_team)

        prefix_filtered = [
            (pid, home, away, date, league)
            for pid, home, away, date, league in candidates
            if (_word_prefixes(home) | _word_prefixes(away)) & event_prefixes
        ]
        if prefix_filtered:
            candidates = prefix_filtered

    # Try fuzzy matching with STRICT validation
    best_score = 0
    best_match_id = None
    best_match_details = None
    best_is_swapped = False
    # When an exact-date candidate clears thresholds, prefer it over any
    # ±1-day candidate even if the adjacent one scores marginally higher.
    # Without this, the matcher collapses consecutive-day same-team games
    # (MLB series, Wimbledon multi-day rounds) onto the wrong event — both
    # clear team-score thresholds at 100/100 and the ±1-day timezone-tolerance
    # window admits both.
    best_is_exact_date = False

    near_miss_score = 0
    near_miss_details = None
    near_miss_reason = None

    event_league = (event.league or "").lower()
    event_is_youth = _is_youth_league(event_league)

    for pid, poly_home, poly_away, date, candidate_league in candidates:
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
                near_miss_reason = (
                    f"individual {team1_score:.0f}/{team2_score:.0f}, min required {min_individual_score}"
                )
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

        # Reject league tier mismatch (e.g. senior vs U21/reserve)
        candidate_is_youth = _is_youth_league(candidate_league)
        if candidate_league and event_league and event_is_youth != candidate_is_youth:
            if is_new_best:
                near_miss_reason = f"league tier mismatch: '{event.league}' vs '{candidate_league}'"
            logger.debug(
                f"[{provider}] Rejected league tier mismatch "
                f"'{event.home_team} vs {event.away_team}' ({event.league}) -> "
                f"'{poly_home} vs {poly_away}' ({candidate_league})"
            )
            continue

        # Reject ±1-day candidate when actual start_time gap > 6h. Timezone
        # artifacts (game starting 23:30 UTC = 01:30 local next day) put two
        # different calendar dates on the SAME game, but the start_times are
        # within minutes. MLB series / multi-day tennis put DIFFERENT games
        # on adjacent dates with start_times 20-26h apart. Only the timezone
        # case should fuzzy-match across days.
        candidate_is_exact_date = date == event_date
        if not candidate_is_exact_date and event.start_time is not None:
            try:
                with session.no_autoflush:
                    cand_start = session.query(Event.start_time).filter(Event.id == pid).scalar()
                if cand_start is not None:
                    ev_start = event.start_time
                    if isinstance(ev_start, str):
                        from datetime import datetime as _dt

                        ev_start = _dt.fromisoformat(ev_start.replace("Z", "+00:00"))
                    # Both should be naive or both aware — strip tz for delta calc.
                    if ev_start.tzinfo is not None:
                        ev_start = ev_start.replace(tzinfo=None)
                    if cand_start.tzinfo is not None:
                        cand_start = cand_start.replace(tzinfo=None)
                    gap_h = abs((ev_start - cand_start).total_seconds()) / 3600.0
                    if gap_h > 6.0:
                        if is_new_best:
                            near_miss_reason = (
                                f"adj-day candidate {pid} start_time gap {gap_h:.1f}h > 6h "
                                "(likely different game in series)"
                            )
                        logger.debug(
                            f"[{provider}] Rejected adj-day match '{event.home_team} vs {event.away_team}' "
                            f"-> '{poly_home} vs {poly_away}': start_time gap {gap_h:.1f}h > 6h"
                        )
                        continue
            except Exception as e:
                # Best-effort — if the lookup fails, fall through to the
                # legacy behavior (allow the fuzzy match). Worst case is the
                # pre-2026-05-18 wrong-day-collision bug.
                logger.debug(f"[{provider}] adj-day start_time check failed for {pid}: {e}")

        # Exact-date candidates strictly beat ±1-day candidates regardless of
        # score (both are normally 100/100 for legitimate matches). Within the
        # same date class, higher score wins.
        if candidate_is_exact_date and not best_is_exact_date:
            promote = True
        elif candidate_is_exact_date == best_is_exact_date:
            promote = avg_score > best_score
        else:
            promote = False
        if promote:
            best_score = avg_score
            best_match_id = pid
            best_match_details = (poly_home, poly_away, team1_score, team2_score)
            best_is_swapped = is_swapped
            best_is_exact_date = candidate_is_exact_date

    if best_match_id:
        poly_home, poly_away, t1, t2 = best_match_details
        swap_note = " [SWAPPED]" if best_is_swapped else ""
        date_note = "" if best_is_exact_date else " [adj-date]"
        logger.debug(
            f"[{provider}] Matched '{event.home_team} vs {event.away_team}' -> "
            f"'{poly_home} vs {poly_away}' (scores: {t1:.0f}/{t2:.0f}, avg: {best_score:.0f}){swap_note}{date_note}"
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
        logger.debug(f"[{provider}] Skipped '{event.home_team} vs {event.away_team}' - no sharp match")
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
            f"[{provider}] No match for '{event.home_team} vs {event.away_team}' (0 candidates for {event.sport})"
        )

    return default_id, False


def _store_deferred_event(session, event: StandardEvent, provider: str):
    """Buffer an unmatched soft event for later Pinnacle matching.

    Concurrent providers all UPSERT into deferred_events; PostgreSQL detects
    deadlocks on the unique-index pages and aborts one of the two transactions.
    Retrying with a tiny backoff lets the loser re-acquire the lock cleanly —
    without retry, the entire per-sport storage transaction rolls back, and
    every event extracted in that sport is lost (including the parent rows
    we already flushed). This was the root cause of spelklubben writing zero
    odds rows for 16 days while logging "success".
    """
    import random
    import time as _time

    from sqlalchemy.dialects.postgresql import insert as pg_insert
    from sqlalchemy.exc import OperationalError

    from ..matching.normalizer import normalize_team_name

    try:
        start_time = datetime.fromisoformat(event.start_time) if isinstance(event.start_time, str) else event.start_time
    except (ValueError, TypeError):
        return

    if not start_time:
        return

    normalized_home = normalize_team_name(event.home_team)
    normalized_away = normalize_team_name(event.away_team)
    markets_json = json.dumps(event.markets)

    stmt = (
        pg_insert(DeferredEvent)
        .values(
            provider_id=provider,
            sport=event.sport,
            league=event.league,
            home_team=event.home_team,
            away_team=event.away_team,
            normalized_home=normalized_home,
            normalized_away=normalized_away,
            start_time=start_time,
            markets_json=markets_json,
        )
        .on_conflict_do_update(
            constraint="uq_deferred_provider_event",
            set_={"markets_json": markets_json, "attempt_count": 0},
        )
    )

    max_retries = 3
    for attempt in range(max_retries):
        # Savepoint isolates the deferred insert from the surrounding transaction.
        # If we plain-rollback, every parent Event + Odds flushed earlier in this
        # sport's session is lost too — exactly the bug we're fixing.
        sp = session.begin_nested()
        try:
            session.execute(stmt)
            sp.commit()
            return
        except OperationalError as e:
            sp.rollback()
            err = str(e).lower()
            if "deadlock detected" not in err or attempt == max_retries - 1:
                raise
            backoff = 0.05 * (2**attempt) + random.uniform(0, 0.05)
            _time.sleep(backoff)


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
        session,
        event,
        provider,
        event_cache,
        fuzzy_threshold,
        min_individual_score,
        prefix_filter_length,
        require_match,
        max_asymmetry_diff,
        min_for_asymmetry_check,
        date_index=date_index,
    )

    if matched_id is None:
        if require_match and not getattr(event, "_from_deferred", False):
            _store_deferred_event(session, event, provider)
        return (False, 0, 0)

    final_id = matched_id

    # Create event if doesn't exist (no_autoflush to prevent premature flush)
    with session.no_autoflush:
        db_event = session.query(Event).filter(Event.id == final_id).first()
    is_new_event = False

    if not db_event:
        start_dt = event.start_time
        if isinstance(start_dt, str):
            try:
                start_dt = datetime.fromisoformat(start_dt.replace("Z", "+00:00"))
            except (ValueError, TypeError):
                start_dt = None

        # Parse original cased names from event.name (e.g. "León vs Necaxa")
        display_home, display_away = _parse_display_names(event.name)

        db_event = Event(
            id=final_id,
            sport=event.sport,
            league=event.league,
            home_team=event.home_team,
            away_team=event.away_team,
            display_home=display_home,
            display_away=display_away,
            start_time=start_dt,
        )
        session.add(db_event)
        session.flush()  # Flush event before odds insert (Postgres enforces FKs)
        is_new_event = True

        # Add to cache for subsequent providers to match against
        date_str = _extract_date_str(event.start_time)

        _update_event_cache(
            event_cache,
            date_index or {},
            event.sport,
            final_id,
            db_event.home_team,
            db_event.away_team,
            date_str,
            league=db_event.league or "",
        )

    # ── Update display names from Pinnacle (best quality names) ────
    if provider == "pinnacle" and not db_event.display_home:
        dh, da = _parse_display_names(event.name)
        if dh and da:
            db_event.display_home = dh
            db_event.display_away = da

    # ── Update live scores from Pinnacle ────────────────────────────
    if event.live_state and provider == "pinnacle":
        ls = event.live_state
        if ls.get("home_score") is not None:
            db_event.home_score = ls["home_score"]
        if ls.get("away_score") is not None:
            db_event.away_score = ls["away_score"]
        if ls.get("match_minute") is not None:
            db_event.match_minute = ls["match_minute"]
        if ls.get("match_period") is not None:
            db_event.match_period = ls["match_period"]
        if ls.get("match_status") == "started":
            db_event.match_status = "live"
        stats = ls.get("stats")
        if stats:
            db_event.stats_json = json.dumps(stats)

            # Tennis: derive home_score/away_score from setsWon
            if event.sport == "tennis":
                home_sets = stats.get("home", {}).get("setsWon")
                away_sets = stats.get("away", {}).get("setsWon")
                if home_sets is not None and away_sets is not None:
                    db_event.home_score = home_sets
                    db_event.away_score = away_sets

        # Esports: parse BO format from match_period ("1/3" → bo=3)
        if event.sport == "esports" and ls.get("match_period"):
            period_str = str(ls["match_period"])
            if "/" in period_str:
                try:
                    total = int(period_str.split("/")[1])
                    existing = json.loads(db_event.stats_json) if db_event.stats_json else {}
                    existing["bo"] = total
                    db_event.stats_json = json.dumps(existing)
                except (ValueError, IndexError):
                    pass

    # Extract home/away odds from event markets for inversion detection
    # Only use 1x2/moneyline — spread odds have inverted favorite semantics
    home_odds, away_odds = None, None
    for market in event.markets:
        market_type = normalize_market(market.get("type", ""))
        if market_type in ("1x2", "moneyline"):
            for outcome in market.get("outcomes", []):
                norm = normalize_outcome(outcome.get("name", ""), event.home_team, event.away_team)
                if norm == "home":
                    home_odds = outcome.get("odds")
                elif norm == "away":
                    away_odds = outcome.get("odds")

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
        _swap, _validated = detect_and_fix_inversion(
            session,
            matched_id,
            provider,
            canonical_home_odds,
            canonical_away_odds,
            sharp_odds_cache=sharp_odds_cache,
        )
        if _swap:
            odds_inverted = True
        # Persist validation result on the Event row so the scanner can skip
        # events whose home/away assignment couldn't be reconciled with Pinnacle.
        db_event.home_away_validated = _validated

    # Swap outcomes if:
    # - Team order is different from canonical (fuzzy_swapped), OR
    # - Odds are inverted vs sharp (odds_inverted)
    # Note: if BOTH are true, they cancel out (double swap = no swap)
    should_swap = fuzzy_swapped != odds_inverted  # XOR: swap if exactly one is true

    if should_swap:
        logger.debug(f"[{provider}] Swapping outcomes for {final_id} to align with canonical event")

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
        market_type = normalize_market(market.get("type", ""))
        allowed = ENRICHMENT_MARKETS if provider in EXTENDED_MARKET_PROVIDERS else ALLOWED_MARKETS
        if market_type not in allowed:
            continue

        outcomes = market.get("outcomes", [])

        # Swap home/away if team order differs from canonical event
        if should_swap:
            outcomes = swap_home_away_outcomes(outcomes)

        # Store ALL spread/total lines from soft providers.
        # The scanner groups odds by market+point (e.g., "spread_-1.5") so
        # value detection only compares matching points automatically.
        # Previous filter (_point_matches_pinnacle) dropped ~95% of soft
        # provider spreads — now we keep them all for cross-book comparison.

        # Build provider_meta from market-level + outcome-level metadata
        # Used by placement system to resolve canonical events to provider-specific IDs
        market_meta = market.get("provider_meta", {})
        scope = market.get("scope", "ft")

        # Inject provider's own team names into provider_meta.
        # If should_swap, the provider's home is the canonical away and vice versa.
        # Store in canonical order so frontend always gets (canonical_home_name, canonical_away_name).
        if should_swap:
            _prov_home = event.away_team  # provider's away = canonical home
            _prov_away = event.home_team  # provider's home = canonical away
        else:
            _prov_home = event.home_team
            _prov_away = event.away_team
        if _prov_home or _prov_away:
            market_meta = {**(market_meta or {}), "prov_home": _prov_home, "prov_away": _prov_away}

        for outcome in outcomes:
            outcome_name = normalize_outcome(outcome.get("name", ""), norm_home, norm_away)
            odds_value = outcome.get("odds", 0)
            point_value = outcome.get("point")

            if odds_value <= 1:
                continue

            odds_processed += 1

            # Resolve to canonical provider for storage (platform consolidation)
            # e.g., expekt → unibet, mrgreen → 888sport
            storage_provider = PROVIDER_CANONICAL.get(provider, provider)

            # Merge market-level and outcome-level provider_meta
            outcome_meta = outcome.get("provider_meta", {})
            provider_meta = {**market_meta, **outcome_meta} if (market_meta or outcome_meta) else None

            # CLOB microstructure (Polymarket only, None for others)
            bid_value = outcome.get("bid")
            ask_value = outcome.get("ask")
            depth_value = outcome.get("depth_usd")

            # Use batch processor if available, otherwise individual upsert
            if odds_batch:
                odds_batch.add(
                    final_id,
                    storage_provider,
                    market_type,
                    outcome_name,
                    odds_value,
                    point_value,
                    provider_meta=provider_meta,
                    bid=bid_value,
                    ask=ask_value,
                    depth_usd=depth_value,
                    scope=scope,
                )
            else:
                odds_new += upsert_odds(
                    session,
                    final_id,
                    storage_provider,
                    market_type,
                    outcome_name,
                    odds_value,
                    point_value,
                    provider_meta=provider_meta,
                    bid=bid_value,
                    ask=ask_value,
                    depth_usd=depth_value,
                    scope=scope,
                )

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
    provider_meta: dict = None,
    bid: float = None,
    ask: float = None,
    depth_usd: float = None,
    scope: str = "ft",
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
        provider_meta: Provider-specific IDs (optional)

    Returns:
        1 if new odds inserted, 0 if updated
    """
    # Build filter including point and scope (handles NULL correctly)
    filters = [
        Odds.event_id == event_id,
        Odds.provider_id == provider,
        Odds.market == market,
        Odds.outcome == outcome,
        Odds.scope == scope,
    ]
    # Point filter: use is_(None) for NULL comparison
    if point is None:
        filters.append(Odds.point.is_(None))
    else:
        filters.append(Odds.point == point)

    existing = session.query(Odds).filter(*filters).first()

    if existing:
        existing.odds = odds
        existing.updated_at = datetime.now(UTC)
        if provider_meta:
            existing.provider_meta = provider_meta
        existing.bid = bid
        existing.ask = ask
        existing.depth_usd = depth_usd
        return 0
    else:
        session.add(
            Odds(
                event_id=event_id,
                provider_id=provider,
                market=market,
                outcome=outcome,
                odds=odds,
                point=point,
                provider_meta=provider_meta,
                bid=bid,
                ask=ask,
                depth_usd=depth_usd,
                scope=scope,
            )
        )
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
        self._market_counts: dict[str, int] = {}  # market_type -> count
        self.changed_event_ids: set[str] = set()
        self._changed_records: list[dict] = []
        # Reconciliation tracker — accumulated across flushes (NOT cleared on flush).
        # Shape: provider_id → event_id → (market, scope) → {(outcome, point), ...}.
        # When __exit__ runs, any SHARP_PROVIDERS slot we touched is treated as the
        # source of truth: DB rows in that slot whose (outcome, point) wasn't seen
        # this pass get deleted (Pinnacle drops mainline handicaps when their estimate
        # shifts; upsert_odds doesn't delete the old row, so ghosts survive until
        # the staleness gate catches them ~15 min later).
        self._extracted_keys: dict[str, dict[str, dict[tuple[str, str], set[tuple[str, float | None]]]]] = {}

    def add(
        self,
        event_id: str,
        provider: str,
        market: str,
        outcome: str,
        odds: float,
        point: float = None,
        provider_meta: dict = None,
        bid: float = None,
        ask: float = None,
        depth_usd: float = None,
        scope: str = "ft",
    ):
        """Add odds record to batch (will be processed on flush).

        `scope` identifies the temporal/structural market scope (e.g. 'ft',
        'reg', '1h'). See backend/src/constants.py:VALID_SCOPES. Defaults to
        'ft' so existing callers continue to work; extractors with scope
        ambiguity (Pinnacle period, Altenar typeId) must pass it explicitly.
        """
        # Use tuple key to deduplicate (scope included — two rows at different
        # scopes are physically different markets, must not dedupe each other).
        key = (event_id, provider, market, outcome, point, scope)
        self._pending[key] = {
            "event_id": event_id,
            "provider_id": provider,
            "market": market,
            "outcome": outcome,
            "odds": odds,
            "point": point,
            "provider_meta": provider_meta,
            "bid": bid,
            "ask": ask,
            "depth_usd": depth_usd,
            "scope": scope,
        }
        self._market_counts[market] = self._market_counts.get(market, 0) + 1

        # Reconciliation tracker — accumulated across flushes. Keyed by provider
        # and (market, scope) so __exit__ can purge stale sharp-provider rows
        # whose (outcome, point) wasn't shipped this pass.
        self._extracted_keys.setdefault(provider, {}).setdefault(event_id, {}).setdefault((market, scope), set()).add(
            (outcome, point)
        )

        if len(self._pending) >= self.batch_size:
            self.flush()

    def flush(self):
        """Process pending records with bulk operations.

        Includes retry logic for SQLite "database is locked" errors that occur
        during concurrent extraction (multiple providers flushing simultaneously).

        Note: Uses time.sleep() because flush is called from synchronous contexts
        (__exit__, add()). Reduced to 3 retries / ~140ms max to minimize event
        loop blocking when callers haven't offloaded to a thread.
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
                err_str = str(e).lower()
                is_retryable = "database is locked" in err_str or "deadlock detected" in err_str
                if is_retryable and attempt < max_retries - 1:
                    wait = 0.05 * (2**attempt)  # 50ms, 100ms, 200ms
                    logger.warning(
                        "OddsBatchProcessor: %s on flush (attempt %d/%d), retrying in %.0fms...",
                        "deadlock" if "deadlock" in err_str else "DB locked",
                        attempt + 1,
                        max_retries,
                        wait * 1000,
                    )
                    self.session.rollback()
                    time.sleep(wait)
                else:
                    raise

    def _flush_inner(self):
        """Inner flush logic — uses PostgreSQL ON CONFLICT upsert for atomicity."""
        now = datetime.now(UTC)

        from sqlalchemy.dialects.postgresql import insert as pg_insert

        records = list(self._pending.values())
        if not records:
            self._pending.clear()
            return

        # Steam-detector pre-fetch: when enabled, capture pre-upsert odds
        # so we can log significant deltas to `odds_movements`. Skipped
        # entirely when the env flag is off — keeps the hot path free.
        from ..analysis.steam_detector import is_enabled as steam_enabled

        steam_on = steam_enabled()

        # Process in batches of 500
        for i in range(0, len(records), 500):
            batch = records[i : i + 500]
            prev_odds_map = self._fetch_prev_odds_for_batch(batch) if steam_on else {}
            rows = [
                {
                    "event_id": r["event_id"],
                    "provider_id": r["provider_id"],
                    "market": r["market"],
                    "outcome": r["outcome"],
                    "odds": r["odds"],
                    "point": r.get("point"),
                    "provider_meta": r.get("provider_meta"),
                    "bid": r.get("bid"),
                    "ask": r.get("ask"),
                    "depth_usd": r.get("depth_usd"),
                    "scope": r.get("scope", "ft"),
                    "updated_at": now,
                }
                for r in batch
            ]

            stmt = pg_insert(Odds.__table__).values(rows)
            stmt = stmt.on_conflict_do_update(
                constraint="uq_odds_with_point_scope",
                set_={
                    "odds": stmt.excluded.odds,
                    "updated_at": stmt.excluded.updated_at,
                    "provider_meta": stmt.excluded.provider_meta,
                    "bid": stmt.excluded.bid,
                    "ask": stmt.excluded.ask,
                    "depth_usd": stmt.excluded.depth_usd,
                },
            ).returning(
                Odds.__table__.c.event_id,
                Odds.__table__.c.odds,
                # xmax != 0 means row existed before (update), xmax == 0 means new insert
                literal_column("xmax").label("xmax"),
            )

            result = self.session.execute(stmt)
            for row in result:
                event_id = row.event_id
                is_update = row.xmax != 0
                if is_update:
                    self._update_count += 1
                else:
                    self._insert_count += 1
                    self.changed_event_ids.add(event_id)

            # Track all events in batch as potentially changed for analyzer
            for r in batch:
                self.changed_event_ids.add(r["event_id"])

            if steam_on and prev_odds_map:
                self._log_movements(batch, prev_odds_map, now)

        self._pending.clear()

    @staticmethod
    def _key_for(r: dict) -> tuple:
        """Composite key matching the Odds unique constraint."""
        return (
            r["event_id"],
            r["provider_id"],
            r["market"],
            r["outcome"],
            r.get("point"),
            r.get("scope", "ft"),
        )

    def _fetch_prev_odds_for_batch(self, batch: list[dict]) -> dict[tuple, float]:
        """Return {key: prev_odds} for rows that already exist in the DB.

        Only consulted when STEAM_DETECTOR_ENABLED. A single SELECT
        bounded by the batch's event ids. Within a flush a batch is
        usually one provider × N events, so the IN-clause stays small.
        """
        if not batch:
            return {}
        from sqlalchemy import select

        event_ids = list({r["event_id"] for r in batch})
        provider_ids = list({r["provider_id"] for r in batch})
        if not event_ids:
            return {}

        stmt = select(
            Odds.event_id,
            Odds.provider_id,
            Odds.market,
            Odds.outcome,
            Odds.point,
            Odds.scope,
            Odds.odds,
        ).where(Odds.event_id.in_(event_ids), Odds.provider_id.in_(provider_ids))

        out: dict[tuple, float] = {}
        for row in self.session.execute(stmt):
            key = (row.event_id, row.provider_id, row.market, row.outcome, row.point, row.scope)
            out[key] = float(row.odds) if row.odds is not None else None
        return out

    def _log_movements(
        self,
        batch: list[dict],
        prev_odds_map: dict[tuple, float],
        now: datetime,
    ) -> None:
        """Write movement rows for upserts whose implied probability shifted
        by at least `STEAM_DELTA_PP_MIN` percentage points."""
        from ..analysis.steam_detector import delta_pp_threshold

        threshold_pp = delta_pp_threshold()
        movement_rows: list[dict] = []
        for r in batch:
            key = self._key_for(r)
            prev = prev_odds_map.get(key)
            new = r.get("odds")
            if prev is None or new is None or prev <= 1.0 or new <= 1.0:
                continue
            prev_imp_pp = (1.0 / prev) * 100.0
            new_imp_pp = (1.0 / new) * 100.0
            delta_pp = new_imp_pp - prev_imp_pp
            if abs(delta_pp) < threshold_pp:
                continue
            movement_rows.append(
                {
                    "event_id": r["event_id"],
                    "provider_id": r["provider_id"],
                    "market": r["market"],
                    "outcome": r["outcome"],
                    "point": r.get("point"),
                    "scope": r.get("scope", "ft"),
                    "prev_odds": prev,
                    "new_odds": new,
                    "delta_implied_pp": round(delta_pp, 3),
                    "direction": "up" if delta_pp > 0 else "down",
                    "recorded_at": now,
                }
            )
        if movement_rows:
            from ..db.models import OddsMovement

            self.session.execute(OddsMovement.__table__.insert(), movement_rows)

    def get_stats(self) -> tuple[int, int]:
        """Return (new_count, update_count)."""
        return self._insert_count, self._update_count

    def get_market_counts(self) -> dict[str, int]:
        """Return market type -> odds count mapping."""
        return dict(self._market_counts)

    def get_changed_records(self) -> list[dict]:
        """Return records where odds changed (updates with delta >= 0.01, plus all inserts)."""
        return self._changed_records

    def reconcile_sharp_deletions(self) -> int:
        """Delete sharp-provider Odds rows whose (outcome, point) wasn't seen this pass.

        For every (event_id, sharp_provider, market, scope) slot we wrote to
        during this batch, the just-extracted (outcome, point) set is treated as
        the source of truth. Rows in the same slot whose (outcome, point) wasn't
        shipped are purged.

        Fixes the ghost-line bug: Pinnacle drops mainline handicaps when their
        estimate shifts (e.g. total 2.5 → 2.75); upsert_odds doesn't delete the
        old row, so the stale 1.40/2.78 sits in the DB until the staleness gate
        (15 min for pinnacle) evicts it — long enough for the scanner to surface
        a phantom +EV arb against fresh soft-book odds.

        Scope guards:
            - SHARP_PROVIDERS only. Soft books still rely on the user-in-browser
              live odds check before placing.
            - Only slots actually touched this pass. A market/scope this run
              didn't extract (extractor errored, market gone entirely) keeps its
              old rows and falls back to the staleness gate.
        """
        if not self._extracted_keys:
            return 0
        deleted = 0
        for provider_id, event_map in self._extracted_keys.items():
            if provider_id not in SHARP_PROVIDERS:
                continue
            for event_id, slot_map in event_map.items():
                for (market, scope), seen in slot_map.items():
                    if not seen:
                        continue
                    keep_conds = []
                    for outcome, point in seen:
                        if point is None:
                            keep_conds.append(and_(Odds.outcome == outcome, Odds.point.is_(None)))
                        else:
                            keep_conds.append(and_(Odds.outcome == outcome, Odds.point == point))
                    n = (
                        self.session.query(Odds)
                        .filter(
                            Odds.event_id == event_id,
                            Odds.provider_id == provider_id,
                            Odds.market == market,
                            Odds.scope == scope,
                            ~or_(*keep_conds),
                        )
                        .delete(synchronize_session=False)
                    )
                    if n:
                        logger.info(
                            f"Reconciliation: purged {n} stale {provider_id} rows for {event_id}/{market}@{scope}"
                        )
                        deleted += n
                        self.changed_event_ids.add(event_id)
        self._extracted_keys.clear()
        return deleted

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        try:
            self.flush()
            if exc_type is None:
                self.reconcile_sharp_deletions()
        except Exception:
            if exc_type is None:
                raise
            logger.warning("OddsBatchProcessor: flush failed during exception handling")
        return False
