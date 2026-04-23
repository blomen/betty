"""
Matching Module

Team name normalization, event matching, and text parsing utilities.
"""

from .matcher import (
    LEAGUE_TO_SPORT,
    MatchResult,
    find_best_team_match,
    fuzzy_match_teams,
    get_sport_from_league,
    match_events,
)
from .normalizer import (
    generate_canonical_id,
    normalize_market,
    normalize_outcome,
    normalize_team_name,
    parse_teams_from_title,
)

__all__ = [
    # Normalization
    "normalize_team_name",
    "generate_canonical_id",
    "parse_teams_from_title",
    "normalize_market",
    "normalize_outcome",
    # Matching
    "fuzzy_match_teams",
    "find_best_team_match",
    "match_events",
    "get_sport_from_league",
    "LEAGUE_TO_SPORT",
    "MatchResult",
]
