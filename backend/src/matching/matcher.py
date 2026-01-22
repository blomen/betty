"""
Event Matching Logic

Fuzzy matching for team names and event matching across providers.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Optional, List

from thefuzz import fuzz

from .normalizer import normalize_team_name, generate_canonical_id


@dataclass
class MatchResult:
    """Result of an event matching attempt."""
    matched: bool
    canonical_id: str
    confidence: float  # 0-100
    home_team_normalized: str
    away_team_normalized: str


def fuzzy_match_teams(team1: str, team2: str, threshold: int = 85) -> bool:
    """
    Check if two team names match using fuzzy matching.

    Args:
        team1: First team name
        team2: Second team name
        threshold: Minimum similarity score (0-100)

    Returns:
        True if teams match
    """
    norm1 = normalize_team_name(team1)
    norm2 = normalize_team_name(team2)

    # Exact match after normalization
    if norm1 == norm2:
        return True

    # Fuzzy match
    ratio = fuzz.ratio(norm1, norm2)
    if ratio >= threshold:
        return True

    # Token sort ratio (handles word order differences)
    token_ratio = fuzz.token_sort_ratio(norm1, norm2)
    if token_ratio >= threshold:
        return True

    return False


def find_best_team_match(team: str, candidates: List[str], threshold: int = 80) -> Optional[str]:
    """
    Find the best matching team from a list of candidates.

    Returns the best match or None if no match meets threshold.
    """
    norm_team = normalize_team_name(team)

    best_match = None
    best_score = 0

    for candidate in candidates:
        norm_candidate = normalize_team_name(candidate)

        # Exact match
        if norm_team == norm_candidate:
            return candidate

        # Fuzzy score
        score = max(
            fuzz.ratio(norm_team, norm_candidate),
            fuzz.token_sort_ratio(norm_team, norm_candidate),
        )

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
    threshold: int = 80,
) -> MatchResult:
    """
    Try to match two events.

    Returns a MatchResult with match status and confidence.
    """
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
            home_team_normalized=normalize_team_name(event1_home),
            away_team_normalized=normalize_team_name(event1_away),
        )

    # Normalize team names
    home1 = normalize_team_name(event1_home)
    away1 = normalize_team_name(event1_away)
    home2 = normalize_team_name(event2_home)
    away2 = normalize_team_name(event2_away)

    # Check home/away match
    home_score = max(fuzz.ratio(home1, home2), fuzz.token_sort_ratio(home1, home2))
    away_score = max(fuzz.ratio(away1, away2), fuzz.token_sort_ratio(away1, away2))

    # Also check swapped (in case home/away is reversed)
    home_swap_score = max(fuzz.ratio(home1, away2), fuzz.token_sort_ratio(home1, away2))
    away_swap_score = max(fuzz.ratio(away1, home2), fuzz.token_sort_ratio(away1, home2))

    # Best match
    direct_score = (home_score + away_score) / 2
    swapped_score = (home_swap_score + away_swap_score) / 2

    if direct_score >= swapped_score and direct_score >= threshold:
        canonical_id = generate_canonical_id(sport, home1, away1, event1_date)
        return MatchResult(
            matched=True,
            canonical_id=canonical_id,
            confidence=direct_score,
            home_team_normalized=home1,
            away_team_normalized=away1,
        )
    elif swapped_score >= threshold:
        # Use event1's order as canonical
        canonical_id = generate_canonical_id(sport, home1, away1, event1_date)
        return MatchResult(
            matched=True,
            canonical_id=canonical_id,
            confidence=swapped_score,
            home_team_normalized=home1,
            away_team_normalized=away1,
        )

    return MatchResult(
        matched=False,
        canonical_id=generate_canonical_id(sport, home1, away1, event1_date),
        confidence=max(direct_score, swapped_score),
        home_team_normalized=home1,
        away_team_normalized=away1,
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
