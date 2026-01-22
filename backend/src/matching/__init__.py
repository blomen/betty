"""
Matching Module

Team name normalization, event matching, and text parsing utilities.
"""

from .normalizer import (
    normalize_team_name,
    generate_canonical_id,
    parse_teams_from_title,
    normalize_market,
    normalize_outcome,
)
from .matcher import (
    fuzzy_match_teams,
    find_best_team_match,
    match_events,
    get_sport_from_league,
    LEAGUE_TO_SPORT,
    MatchResult,
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
