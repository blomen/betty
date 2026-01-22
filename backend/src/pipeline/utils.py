"""
Pipeline Utilities

Helper functions for event processing and ID generation.
"""

from datetime import datetime

from ..matching import normalize_team_name


def generate_canonical_id(sport: str, home: str, away: str, start_time: datetime | str) -> str:
    """
    Generate canonical event ID for cross-provider matching.

    Format: {sport}:{home_normalized}:{away_normalized}:{date}
    Example: "football:manchester_united:liverpool:20250122"

    Args:
        sport: Sport name (e.g., "football", "basketball")
        home: Home team name
        away: Away team name
        start_time: Event start time (datetime or ISO string)

    Returns:
        Canonical ID string
    """
    home_norm = normalize_team_name(home)
    away_norm = normalize_team_name(away)

    if isinstance(start_time, datetime):
        date_str = start_time.strftime('%Y%m%d')
    elif isinstance(start_time, str):
        try:
            dt = datetime.fromisoformat(start_time.replace('Z', '+00:00'))
            date_str = dt.strftime('%Y%m%d')
        except:
            date_str = 'unknown'
    else:
        date_str = 'unknown'

    return f"{sport}:{home_norm}:{away_norm}:{date_str}"
