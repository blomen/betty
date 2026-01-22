"""
Normalization Module

Contains all normalization logic:
- Team name normalization for cross-provider matching
- Event title parsing (extracting team names)
- Market and outcome normalization
"""

import re
import unicodedata
from functools import lru_cache
from pathlib import Path
from typing import Dict, Optional

import yaml

# Common suffixes to remove (order matters - longer first)
TEAM_SUFFIXES = [
    # Years and founding dates
    ' 1846', ' 1848', ' 1860', ' 1899', ' 1900', ' 1903', ' 1904', ' 1905',
    ' 1906', ' 1907', ' 1908', ' 1909', ' 1910', ' 1911', ' 1912', ' 1913',
    ' 1919', ' 1920', ' 1921', ' 1945', ' 1946',
    ' 05', ' 07', ' 08', ' 09', ' 04', ' 96', ' 99',
    # Standard suffixes
    ' fc', ' cf', ' if', ' aik', ' fk', ' bk', ' sk', ' ff', ' ik',
    ' afc', ' sc', ' ud', ' cd', ' ac', ' as', ' ss', ' us', ' bc',
    ' ssc', ' kc', ' rsc', ' vfl', ' tsv', ' sv', ' fsv', ' spvgg',
    ' calcio', ' gf', ' bf', ' scc', ' jsc', ' sdc',
    ' de futbol', ' football', ' futbol', ' club', ' united',
    ' kv', ' alsace',
]

# Common prefixes to remove
TEAM_PREFIXES = [
    # German
    '1. fc ', '1. fsv ', '1. sv ', 'bv ', 'vfl ', 'vfb ', 'sv ', 'tsv ',
    'fsv ', 'spvgg ', 'sc ', 'ssv ', 'tsg ', 'sg ',
    # General
    'fc ', 'cf ', 'ac ', 'as ', 'us ', 'ss ', 'sk ', 'fk ',
    'real ', 'sporting ', 'atletico ', 'athletic ', 'olympique ',
    'afc ', 'rsc ', 'krc ', 'kv ', 'kaa ', 'rsca ', 'rc ', 'ca ',
    # Gender/Age
    'mens ', 'womens ', "men's ", "women's ", "men's: ", "women's: ",
    'u21 ', 'u19 ', 'u23 ',
    # League prefixes (Polymarket embeds these)
    'shl: ', 'cehl: ', 'ahl: ', 'del: ', 'nhl: ', 'khl: ',
    'epl: ', 'laliga: ', 'bundesliga: ', 'serie a: ', 'ligue 1: ',
    'club ',
]


@lru_cache(maxsize=1)
def _load_aliases() -> Dict[str, str]:
    """Load and build reverse alias lookup from YAML."""
    aliases_path = Path(__file__).parent / "aliases.yaml"

    lookup = {}

    if aliases_path.exists():
        with open(aliases_path, "r", encoding="utf-8") as f:
            aliases_data = yaml.safe_load(f)

        for canonical, alias_list in aliases_data.items():
            lookup[canonical.lower()] = canonical.lower()
            if alias_list:
                for alias in alias_list:
                    lookup[alias.lower()] = canonical.lower()

    return lookup


@lru_cache(maxsize=2048)
def normalize_team_name(name: str) -> str:
    """
    Normalize team name for matching.

    1. Remove accents/diacritics
    2. Lowercase
    3. Remove suffixes (FC, IF, etc.)
    4. Remove prefixes (Real, Sporting, etc.)
    5. Remove punctuation
    6. Map to canonical name if known
    """
    if not name:
        return ""

    name = name.lower().strip()

    # Manual character replacements
    name = name.replace('æ', 'ae').replace('ø', 'o').replace('å', 'a')

    # Unicode normalization (remove accents)
    name = unicodedata.normalize('NFKD', name)
    name = "".join([c for c in name if not unicodedata.combining(c)])

    # Post-unicode fixups
    name = name.replace('ß', 'ss')

    # Remove rankings: (12) Team -> Team
    name = re.sub(r'^\(\d+\)\s*', '', name)

    # Remove state suffixes: Team-RJ -> Team
    name = re.sub(r'-[a-z]{2}$', '', name)

    # Remove suffixes
    for suffix in TEAM_SUFFIXES:
        if name.endswith(suffix):
            name = name[:-len(suffix)].strip()

    # Remove prefixes
    original = name
    for prefix in TEAM_PREFIXES:
        if name.startswith(prefix):
            name = name[len(prefix):].strip()
            break

    # Remove punctuation
    name = re.sub(r'[^\w\s]', '', name)
    name = ' '.join(name.split())

    # Try alias lookup
    alias_lookup = _load_aliases()

    if name in alias_lookup:
        return alias_lookup[name]
    if original in alias_lookup:
        return alias_lookup[original]

    # Fallback: simplify Scandinavian transliterations
    if 'ae' in name or 'oe' in name or 'aa' in name:
        simplified = name.replace('ae', 'a').replace('oe', 'o').replace('aa', 'a')
        if simplified in alias_lookup:
            return alias_lookup[simplified]

    return name


def generate_canonical_id(
    sport: str,
    home_team: str,
    away_team: str,
    start_date: str,
) -> str:
    """
    Generate a canonical event ID.

    Format: {sport}:{home_normalized}:{away_normalized}:{date}
    """
    home_norm = normalize_team_name(home_team)
    away_norm = normalize_team_name(away_team)

    return f"{sport}:{home_norm}:{away_norm}:{start_date}"


# ============ Event Title Parsing ============

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


def parse_teams_from_title(title: str) -> Optional[tuple[str, str]]:
    """
    Parse home and away teams from event title.

    Handles common separators: " vs. ", " vs ", " @ "
    Strips tournament prefixes and "More Markets" suffixes.

    Returns:
        (home_team, away_team) or None if parsing fails
    """
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


# ============ Market and Outcome Normalization ============

def normalize_market(market: str) -> str:
    """
    Normalize market type to standard format.

    Maps various market names to canonical types:
    - "1x2", "full time result", etc. -> "1x2"
    - "over/under", "totals" -> "over_under"
    - "spread", "handicap" -> "spread"
    """
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
    """
    Normalize outcome name to standard format.

    Maps various outcome names to canonical types:
    - Team names -> "home" or "away"
    - "1", "yes" -> "home"
    - "x", "draw" -> "draw"
    - "2", "no" -> "away"
    - "over" -> "over"
    - "under" -> "under"

    Args:
        outcome: Raw outcome string
        home: Home team name (for matching)
        away: Away team name (for matching)

    Returns:
        Normalized outcome string (max 20 chars)
    """
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
