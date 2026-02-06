"""
Normalization Module

Contains all normalization logic:
- Team name normalization for cross-provider matching
- Event title parsing (extracting team names)
- Market and outcome normalization
"""

import logging
import re
import unicodedata
from functools import lru_cache
from pathlib import Path
from typing import Dict, Optional

import yaml

logger = logging.getLogger(__name__)

# Precompiled regex patterns for performance
_RANKING_PATTERN = re.compile(r'^\(\d+\)\s*')
_STATE_SUFFIX_PATTERN = re.compile(r'-[a-z]{2}$')
_PUNCTUATION_PATTERN = re.compile(r'[^\w\s]')

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
    # Reserve/women's/development team suffixes
    ' fc d', ' fc w', ' fc b',
    ' (w)', ' (d)', ' (b)',
    ' women', ' womens', " women's",
    ' reserves', ' ii', ' iii',
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
    # Basketball clubs (various languages)
    'kk ', 'bk ', 'bc ', 'hc ', 'hk ',  # kk = kosarkaski klub (Serbian/Croatian)
    'cb ', 'ck ',  # cb = club baloncesto (Spanish)
    # Arabic article
    'al ', 'al-',
    # Gender/Age
    'mens ', 'womens ', "men's ", "women's ", "men's: ", "women's: ",
    'u21 ', 'u19 ', 'u23 ',
    # League prefixes (Polymarket embeds these)
    'shl: ', 'cehl: ', 'ahl: ', 'del: ', 'nhl: ', 'khl: ',
    'epl: ', 'laliga: ', 'bundesliga: ', 'serie a: ', 'ligue 1: ',
    'club ',
    # Country prefixes sometimes used
    'cd ', 'ud ',  # club deportivo, union deportiva (Spanish)
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
    name = _RANKING_PATTERN.sub('', name)

    # Remove state suffixes: Team-RJ -> Team
    name = _STATE_SUFFIX_PATTERN.sub('', name)

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
    name = _PUNCTUATION_PATTERN.sub('', name)
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
    start_time,
) -> str:
    """
    Generate canonical event ID for cross-provider matching.

    Format: {sport}:{home_normalized}:{away_normalized}:{date}
    Example: "football:manchester_united:liverpool:20250122"

    Args:
        sport: Sport name (e.g., "football", "basketball")
        home_team: Home team name
        away_team: Away team name
        start_time: Event start time (datetime or ISO string)

    Returns:
        Canonical ID string
    """
    from datetime import datetime

    home_norm = normalize_team_name(home_team)
    away_norm = normalize_team_name(away_team)

    if isinstance(start_time, datetime):
        date_str = start_time.strftime('%Y%m%d')
    elif isinstance(start_time, str):
        try:
            dt = datetime.fromisoformat(start_time.replace('Z', '+00:00'))
            date_str = dt.strftime('%Y%m%d')
        except ValueError:
            date_str = 'unknown'
    else:
        date_str = 'unknown'

    return f"{sport}:{home_norm}:{away_norm}:{date_str}"


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

    We support 1x2, moneyline, spread, and total markets.
    Everything else returns 'other' which gets filtered out by ALLOWED_MARKETS in storage.

    Returns:
    - '1x2' for 3-way markets (home/draw/away) - football
    - 'moneyline' for 2-way markets (home/away) - basketball, hockey, tennis
    - 'spread' for handicap/spread markets
    - 'total' for over/under markets
    - 'other' for everything else (filtered out)

    Note: Market type is best determined from outcome structure (has draw or not).
    This function handles text-based detection as fallback.
    """
    market = market.lower().strip()

    # Pass through if already normalized
    if market in ('1x2', 'moneyline', 'spread', 'total'):
        return market

    # Spread/handicap detection
    if any(kw in market for kw in [
        'spread', 'handicap', 'handikapp', 'europeiskt', 'run line', 'puck line',
    ]):
        return 'spread'

    # Total/over-under detection
    if any(kw in market for kw in [
        'total', 'totalt', 'over/under', 'o/u', 'över/under',
    ]):
        return 'total'

    # Early exit for markets that are definitely NOT 1x2/moneyline
    # This avoids false positives from keywords like "förlängning" in handicap markets
    if any(kw in market for kw in [
        # Props and other markets
        'corner', 'card', 'kort', 'shot', 'skott', 'foul', 'booking',
        'player', 'spelare', 'scorer', 'målskytt',
        'both teams', 'btts', 'båda lagen',
        'correct score', 'rätt resultat', 'korrekt resultat',
        'double chance', 'dubbelchans',
        'draw no bet', 'dnb',
        'half time', 'halvtid', 'ht/ft',
        # Margin and race-to markets
        'marginal', 'margin', 'först till', 'race to', 'first to',
        # Period/quarter specific
        'quarter', 'period', '1st half', '2nd half', 'första halvlek', 'andra halvlek',
        # NFL props
        'safety', 'field goal', 'touchdown',
    ]):
        return 'other'

    # 1x2 / Moneyline / Match Winner detection
    # Polymarket "Will X win..." format
    is_polymarket_win = market.startswith('will ') and (' win on ' in market or ' win?' in market)

    # Polymarket "Team A vs. Team B" format (simple match winner)
    is_simple_vs_market = ' vs. ' in market or ' vs ' in market

    # Standard 1x2/moneyline keywords
    is_match_winner = any(kw in market for kw in [
        '1x2', 'full time', 'fulltid', 'heltid',  # Swedish: fulltid/heltid = full time
        'match result', 'helmatchen', 'slutresultat',
        'will win', 'to win', 'vinnare', 'match winner', 'moneyline',
        'money line', 'att vinna', 'vinner matchen', 'vinner match',
        ' vinner', ' wins', 'winner',
        'förlängning', 'forlangning',  # Swedish "including overtime"
        'ordinarie tid', 'match_odds',  # Swedish "regular time"
    ])

    if is_polymarket_win or is_simple_vs_market or is_match_winner:
        # Return '1x2' as default - actual type should be determined
        # by outcome structure (has draw = 1x2, no draw = moneyline)
        # Providers should set this correctly at extraction time
        return '1x2'

    # Everything else is filtered out
    return 'other'


def normalize_outcome(outcome: str, home: str = "", away: str = "") -> str:
    """
    Normalize outcome name to standard format.

    Maps various outcome names to canonical types:
    - Team names -> "home" or "away" (using fuzzy matching)
    - "1", "yes" -> "home"
    - "x", "draw" -> "draw"
    - "2", "no" -> "away"

    Args:
        outcome: Raw outcome string
        home: Home team name (for matching)
        away: Away team name (for matching)

    Returns:
        Normalized outcome string (max 20 chars)
    """
    from thefuzz import fuzz

    outcome_lower = outcome.lower().strip()
    home_norm = normalize_team_name(home)
    away_norm = normalize_team_name(away)
    outcome_norm = normalize_team_name(outcome)

    # Exact match after normalization
    if home_norm and outcome_norm == home_norm:
        return 'home'
    if away_norm and outcome_norm == away_norm:
        return 'away'

    # Fuzzy match for cases like "chattanooga mocs" vs "chattanooga"
    if home_norm and away_norm and outcome_norm:
        # Use multiple fuzzy strategies
        home_scores = [
            fuzz.ratio(outcome_norm, home_norm),
            fuzz.token_set_ratio(outcome_norm, home_norm),
            fuzz.partial_ratio(outcome_norm, home_norm) if len(home_norm) >= 4 else 0,
        ]
        away_scores = [
            fuzz.ratio(outcome_norm, away_norm),
            fuzz.token_set_ratio(outcome_norm, away_norm),
            fuzz.partial_ratio(outcome_norm, away_norm) if len(away_norm) >= 4 else 0,
        ]

        home_score = max(home_scores)
        away_score = max(away_scores)

        # Require clear winner with minimum threshold
        if home_score >= 80 and home_score > away_score + 10:
            return 'home'
        if away_score >= 80 and away_score > home_score + 10:
            return 'away'

    # Standard outcome keywords
    if outcome_lower in ['1', 'home', 'hemma', 'yes', 'ja']:
        return 'home'
    if outcome_lower in ['x', 'draw', 'oavgjort', 'tie']:
        return 'draw'
    if outcome_lower in ['2', 'away', 'borta', 'no', 'nej']:
        return 'away'
    if outcome_lower in ['over', 'över']:
        return 'over'
    if outcome_lower in ['under']:
        return 'under'

    # Log normalization failure for debugging
    logger.debug(
        f"Outcome normalization failed: '{outcome}' did not match "
        f"home='{home}' or away='{away}', returning truncated raw value"
    )
    return outcome[:20]
