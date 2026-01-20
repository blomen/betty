"""
Normalization Rules

Contains logic for normalizing team names, markets, and outcomes.
"""

import re
from src.utils.matching import normalize_team_name

# ============ Team Name Parsing ============

# Tournament/league prefixes to strip from team names
TOURNAMENT_PREFIXES = [
    # Tennis Grand Slams
    "australian open mens ", "australian open womens ", "australian open ",
    "us open mens ", "us open womens ", "us open ",
    "french open mens ", "french open womens ", "french open ",
    "wimbledon mens ", "wimbledon womens ", "wimbledon ",
    # Tennis tours
    "atp tour ", "atp ", "wta tour ", "wta ",
    "itf mens ", "itf womens ", "itf ",
    # Football competitions
    "uefa champions league ", "champions league ",
    "uefa europa league ", "europa league ",
    "uefa europa conference league ", "conference league ",
    "fifa world cup ", "world cup ",
    "copa america ", "euro 2024 ", "euro 2028 ",
    "african cup of nations ", "afcon ",
    # Leagues with common prefixes
    "english premier league ", "premier league ",
    "spanish la liga ", "la liga ",
    "german bundesliga ", "bundesliga ",
    "italian serie a ", "serie a ",
    "french ligue 1 ", "ligue 1 ",
    # US Sports
    "nba ", "nfl ", "nhl ", "mlb ",
    "ncaa ", "college ",
    # eSports
    "valorant ", "league of legends ", "lol ", "dota 2 ", "dota ", 
    "counter-strike ", "cs:go ", "cs2 ", "cs ",
    "call of duty ", "cod ", "rocket league ", "rl ",
    "starcraft 2 ", "starcraft ",
    # Generic
    "mens ", "womens ", "women's ", "men's ",
]

def strip_tournament_prefix(title: str) -> str:
    """Strip tournament/league prefixes from event title recursively."""
    title_lower = title.lower()
    for prefix in TOURNAMENT_PREFIXES:
        if title_lower.startswith(prefix):
            # Recurse to handle multiple prefixes (e.g. "Australian Open" then "Mens")
            return strip_tournament_prefix(title[len(prefix):].strip())
    return title


def parse_teams_from_title(title: str) -> tuple[str, str] | None:
    """Parse home and away teams from event title."""
    # Remove "More Markets" suffix
    title = re.sub(r'\s*-\s*More Markets$', '', title)
    
    # Strip tournament prefixes (e.g., "Australian Open Mens" from tennis)
    title = strip_tournament_prefix(title)
    
    for sep in [' vs. ', ' vs ', ' @ ']:
        if sep in title:
            parts = title.split(sep, 1)
            if len(parts) == 2:
                return (parts[0].strip(), parts[1].strip())
    return None

def normalize_market(market: str) -> str:
    """Normalize market type."""
    market = market.lower().strip()
    
    if '1x2' in market or 'full time' in market or ('will' in market and 'win' in market):
        return '1x2'
    if 'over' in market and 'under' in market or 'o/u' in market:
        return 'over_under'
    if 'spread' in market or 'handicap' in market:
        return 'spread'
    if 'draw' in market:
        return '1x2'
    
    return market.replace(' ', '_')[:30]

def normalize_outcome(outcome: str, home: str = "", away: str = "") -> str:
    """Normalize outcome name."""
    outcome = outcome.lower().strip()
    home_norm = normalize_team_name(home)
    away_norm = normalize_team_name(away)
    outcome_norm = normalize_team_name(outcome)
    
    if home_norm and outcome_norm == home_norm:
        return 'home'
    if away_norm and outcome_norm == away_norm:
        return 'away'
    
    if outcome in ['1', 'home', 'hemma', 'yes']:
        return 'home'
    if outcome in ['x', 'draw', 'oavgjort']:
        return 'draw'
    if outcome in ['2', 'away', 'borta', 'no']:
        return 'away'
    if 'over' in outcome:
        return 'over'
    if 'under' in outcome:
        return 'under'
    
    return outcome[:20]
