"""
Event Matching Logic

Fuzzy matching for team names and event matching across providers.

MATCHING PHILOSOPHY:
- Better to miss a match than to create a false positive
- Both teams must match individually (not just average)
- Short team names need higher thresholds
- Cross-validate with multiple metrics
"""

from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from typing import Optional, List
import logging

from rapidfuzz import fuzz

from .normalizer import normalize_team_name, generate_canonical_id

logger = logging.getLogger(__name__)


@dataclass
class MatchResult:
    """Result of an event matching attempt."""
    matched: bool
    canonical_id: str
    confidence: float  # 0-100
    home_team_normalized: str
    away_team_normalized: str
    home_score: float = 0  # Individual team scores for debugging
    away_score: float = 0


def get_team_match_score(team1: str, team2: str) -> float:
    """
    Get the matching score between two team names.

    Delegates to a cached inner function keyed on normalized names so that
    repeated comparisons (common during multi-provider extraction) are free.
    """
    norm1 = normalize_team_name(team1)
    norm2 = normalize_team_name(team2)
    return _match_score_cached(norm1, norm2)


@lru_cache(maxsize=16384)
def _match_score_cached(norm1: str, norm2: str) -> float:
    """LRU-cached fuzzy score on already-normalized names."""
    if norm1 == norm2:
        return 100.0
    if not norm1 or not norm2:
        return 0.0

    score = fuzz.token_set_ratio(norm1, norm2)

    if score < 85 and len(norm1) > 3 and len(norm2) > 3:
        len_ratio = min(len(norm1), len(norm2)) / max(len(norm1), len(norm2))
        if len_ratio < 0.6:
            partial = fuzz.partial_ratio(norm1, norm2)
            if partial > score:
                score = partial

    return float(score)


def fuzzy_match_teams(team1: str, team2: str, threshold: int = 85) -> int:
    """
    Check if two team names match using fuzzy matching.

    Args:
        team1: First team name
        team2: Second team name
        threshold: Minimum similarity score (0-100)

    Returns:
        Match score (0-100), or 0 if below threshold
    """
    score = get_team_match_score(team1, team2)

    # Short team names need stricter matching
    # e.g., "PSG" vs "PSV" would score high but are different teams
    norm1 = normalize_team_name(team1)
    norm2 = normalize_team_name(team2)
    min_len = min(len(norm1), len(norm2))

    if min_len <= 3:
        # Very short names: require exact or near-exact match
        effective_threshold = max(threshold, 95)
    elif min_len <= 5:
        # Short names: require higher threshold
        effective_threshold = max(threshold, 90)
    else:
        effective_threshold = threshold

    return int(score) if score >= effective_threshold else 0


def find_best_team_match(team: str, candidates: List[str], threshold: int = 80) -> Optional[str]:
    """
    Find the best matching team from a list of candidates.

    Returns the best match or None if no match meets threshold.
    """
    best_match = None
    best_score = 0

    for candidate in candidates:
        score = get_team_match_score(team, candidate)

        if score == 100:  # Exact match
            return candidate

        if score > best_score and score >= threshold:
            best_score = score
            best_match = candidate

    return best_match


def match_events(
    event1_home: str,
    event1_away: str,
    event1_date: str,
    event2_home: str,
    event2_away: str,
    event2_date: str,
    sport: str,
    threshold: int = 85,
    min_individual_score: int = 75,
) -> MatchResult:
    """
    Try to match two events with strict validation.

    BULLETPROOF MATCHING RULES:
    1. Date must match (or be within 1 day)
    2. BOTH teams must match individually above min_individual_score
    3. Average score must be above threshold
    4. Cross-validate: reject if one team matches perfectly but other doesn't

    Args:
        threshold: Minimum average score for match (default 85)
        min_individual_score: Minimum score for EACH team (default 75)

    Returns:
        MatchResult with match status and confidence.
    """
    # Normalize team names
    home1 = normalize_team_name(event1_home)
    away1 = normalize_team_name(event1_away)
    home2 = normalize_team_name(event2_home)
    away2 = normalize_team_name(event2_away)

    # Check date match (allow +/- 1 day for timezones)
    date_match = False
    if event1_date == event2_date:
        date_match = True
    else:
        try:
            d1 = datetime.strptime(event1_date, "%Y%m%d")
            d2 = datetime.strptime(event2_date, "%Y%m%d")
            diff = abs((d1 - d2).days)
            if diff <= 1:
                date_match = True
        except ValueError:
            pass

    if not date_match:
        return MatchResult(
            matched=False,
            canonical_id="",
            confidence=0,
            home_team_normalized=home1,
            away_team_normalized=away1,
        )

    # Get individual team scores for DIRECT match (home1↔home2, away1↔away2)
    home_direct = get_team_match_score(home1, home2)
    away_direct = get_team_match_score(away1, away2)

    # Get individual team scores for SWAPPED match (home1↔away2, away1↔home2)
    home_swapped = get_team_match_score(home1, away2)
    away_swapped = get_team_match_score(away1, home2)

    # Calculate average scores
    direct_avg = (home_direct + away_direct) / 2
    swapped_avg = (home_swapped + away_swapped) / 2

    # Determine best match type
    is_swapped = swapped_avg > direct_avg
    if is_swapped:
        team1_score, team2_score = home_swapped, away_swapped
        avg_score = swapped_avg
    else:
        team1_score, team2_score = home_direct, away_direct
        avg_score = direct_avg

    # BULLETPROOF VALIDATION
    # Rule 1: Average must meet threshold
    if avg_score < threshold:
        return MatchResult(
            matched=False,
            canonical_id=generate_canonical_id(sport, home1, away1, event1_date),
            confidence=avg_score,
            home_team_normalized=home1,
            away_team_normalized=away1,
            home_score=team1_score,
            away_score=team2_score,
        )

    # Rule 2: BOTH teams must meet minimum individual score
    if team1_score < min_individual_score or team2_score < min_individual_score:
        logger.debug(
            f"Match rejected: individual scores too low. "
            f"'{home1}' vs '{home2}' ({team1_score}), '{away1}' vs '{away2}' ({team2_score})"
        )
        return MatchResult(
            matched=False,
            canonical_id=generate_canonical_id(sport, home1, away1, event1_date),
            confidence=avg_score,
            home_team_normalized=home1,
            away_team_normalized=away1,
            home_score=team1_score,
            away_score=team2_score,
        )

    # Rule 3: Reject suspicious asymmetric matches
    # If one team is 100% but other is below 85%, likely wrong event
    score_diff = abs(team1_score - team2_score)
    if score_diff > 25 and min(team1_score, team2_score) < 85:
        logger.debug(
            f"Match rejected: asymmetric scores ({team1_score} vs {team2_score}). "
            f"Likely partial team name collision."
        )
        return MatchResult(
            matched=False,
            canonical_id=generate_canonical_id(sport, home1, away1, event1_date),
            confidence=avg_score,
            home_team_normalized=home1,
            away_team_normalized=away1,
            home_score=team1_score,
            away_score=team2_score,
        )

    # Match successful
    canonical_id = generate_canonical_id(sport, home1, away1, event1_date)
    return MatchResult(
        matched=True,
        canonical_id=canonical_id,
        confidence=avg_score,
        home_team_normalized=home1,
        away_team_normalized=away1,
        home_score=team1_score,
        away_score=team2_score,
    )


# League to sport mapping
LEAGUE_TO_SPORT = {
    # Football
    "premier league": "football",
    "la liga": "football",
    "bundesliga": "football",
    "serie a": "football",
    "ligue 1": "football",
    "champions league": "football",
    "europa league": "football",
    "eredivisie": "football",
    "mls": "football",
    # Basketball
    "nba": "basketball",
    "wnba": "basketball",
    "ncaa basketball": "basketball",
    "euroleague": "basketball",
    # Hockey
    "nhl": "ice_hockey",
    "khl": "ice_hockey",
    "shl": "ice_hockey",
    # American Football
    "nfl": "american_football",
    "ncaa football": "american_football",
    # Tennis
    "atp": "tennis",
    "wta": "tennis",
    # Other
    "mlb": "baseball",
    "mma/ufc": "mma",
}


def get_sport_from_league(league_name: str) -> str:
    """Get the sport category from a league name."""
    league_lower = league_name.lower()

    for league, sport in LEAGUE_TO_SPORT.items():
        if league in league_lower:
            return sport

    # Default to football for unrecognized soccer leagues
    if any(word in league_lower for word in ["liga", "league", "cup", "division"]):
        return "football"

    return "unknown"
