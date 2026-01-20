"""
Team and Event Matching Utilities

Provides fuzzy matching for team names and league mapping
to improve cross-provider event matching.
"""

import re
from dataclasses import dataclass
from thefuzz import fuzz
from typing import Optional
import unicodedata


# ============ Team Name Normalization ============

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

# Team name aliases (canonical -> alternatives)
TEAM_ALIASES = {
    # English
    "manchester united": ["man utd", "man united", "mufc"],
    "manchester city": ["man city", "mcfc"],
    "tottenham": ["tottenham hotspur", "spurs", "thfc"],
    "wolverhampton": ["wolverhampton wanderers", "wolves"],
    "brighton": ["brighton & hove albion", "brighton hove"],
    "newcastle": ["newcastle united", "nufc"],
    "west ham": ["west ham united", "whu", "hammers"],
    "nottingham forest": ["notts forest", "nffc"],
    
    # Spanish
    "atletico madrid": ["atl madrid", "atletico de madrid"],
    "real madrid": ["r madrid", "rmcf"],
    "barcelona": ["fc barcelona", "barca", "fcb"],
    "real sociedad": ["r sociedad", "la real"],
    
    # German (important: handle BV, VfL, etc variations)
    "bayern munich": ["bayern munchen", "fc bayern", "bayern", "bayern münchen"],
    "borussia dortmund": ["bvb", "dortmund", "bv borussia 09 dortmund", "bv borussia dortmund"],
    "rb leipzig": ["rasenballsport leipzig", "leipzig"],
    "bayer leverkusen": ["leverkusen", "b04", "bayer 04 leverkusen"],
    "heidenheim": ["1 fc heidenheim", "1 fc heidenheim 1846", "fc heidenheim"],
    "koln": ["1 fc koln", "fc koln", "1 fc köln", "fc köln", "cologne"],
    "union berlin": ["1 fc union berlin", "fc union berlin"],
    "mainz": ["1 fsv mainz 05", "mainz 05", "fsv mainz"],
    "wolfsburg": ["vfl wolfsburg"],
    "frankfurt": ["eintracht frankfurt", "sge"],
    "gladbach": ["borussia monchengladbach", "borussia mönchengladbach", "bmg", "borussia m'gladbach"],
    "freiburg": ["sc freiburg"],
    "hoffenheim": ["tsg hoffenheim", "tsg 1899 hoffenheim"],
    "augsburg": ["fc augsburg"],
    "bremen": ["sv werder bremen", "werder bremen", "werder"],
    "bochum": ["vfl bochum", "vfl bochum 1848"],
    "st pauli": ["fc st pauli", "fc st pauli 1910", "st pauli 1910"],
    
    # Italian (with Calcio variations)
    "inter milan": ["internazionale", "inter", "fc inter", "fc internazionale milano"],
    "ac milan": ["milan", "acm"],
    "juventus": ["juve", "juventus fc"],
    "napoli": ["ssc napoli"],
    "roma": ["as roma"],
    "lazio": ["ss lazio", "lazio roma"],
    "fiorentina": ["acf fiorentina"],
    "atalanta": ["atalanta bc", "atalanta bergamo"],
    "bologna": ["bologna fc", "bologna fc 1909"],
    "torino": ["torino fc"],
    "udinese": ["udinese calcio"],
    "cagliari": ["cagliari calcio"],
    "genoa": ["genoa cfc"],
    "lecce": ["us lecce"],
    "verona": ["hellas verona"],
    "empoli": ["empoli fc"],
    "sassuolo": ["us sassuolo", "us sassuolo calcio"],
    "monza": ["ac monza"],
    
    # French
    "paris saint-germain": ["psg", "paris sg"],
    "olympique marseille": ["marseille", "om"],
    "olympique lyon": ["lyon", "ol"],
    
    # NBA
    "lakers": ["los angeles lakers", "la lakers"],
    "clippers": ["los angeles clippers", "la clippers"],
    "celtics": ["boston celtics"],
    "warriors": ["golden state warriors", "gsw"],
    "heat": ["miami heat"],
    "bulls": ["chicago bulls"],
    "knicks": ["new york knicks", "ny knicks"],
    "nets": ["brooklyn nets"],
    "sixers": ["philadelphia 76ers", "76ers"],
    "bucks": ["milwaukee bucks"],
    "mavericks": ["dallas mavericks", "mavs"],
    "suns": ["phoenix suns"],
    "nuggets": ["denver nuggets"],
    "jazz": ["utah jazz"],
    "grizzlies": ["memphis grizzlies"],
    "pelicans": ["new orleans pelicans"],
    "hawks": ["atlanta hawks"],
    "magic": ["orlando magic"],
    "thunder": ["oklahoma city thunder", "okc thunder"],
    "rockets": ["houston rockets"],
    "spurs": ["san antonio spurs"],
    "raptors": ["toronto raptors"],
    "pacers": ["indiana pacers"],
    "cavaliers": ["cleveland cavaliers", "cavs"],
    "pistons": ["detroit pistons"],
    "hornets": ["charlotte hornets"],
    "wizards": ["washington wizards"],
    "timberwolves": ["minnesota timberwolves", "wolves"],
    "blazers": ["portland trail blazers", "trail blazers"],
    "kings": ["sacramento kings"],
    
    # NFL
    "chiefs": ["kansas city chiefs", "kc chiefs"],
    "bills": ["buffalo bills"],
    "ravens": ["baltimore ravens"],
    "eagles": ["philadelphia eagles"],
    "49ers": ["san francisco 49ers", "niners"],
    "cowboys": ["dallas cowboys"],
    "packers": ["green bay packers"],
    "lions": ["detroit lions"],
    "dolphins": ["miami dolphins"],
    "broncos": ["denver broncos"],
    "bengals": ["cincinnati bengals"],
    "vikings": ["minnesota vikings"],
    "saints": ["new orleans saints"],
    "chargers": ["los angeles chargers", "la chargers"],
    "seahawks": ["seattle seahawks"],
    "steelers": ["pittsburgh steelers"],
    "patriots": ["new england patriots", "pats"],
    "bears": ["chicago bears"],
    "commanders": ["washington commanders"],
    "giants": ["new york giants", "ny giants"],
    "jets": ["new york jets", "ny jets"],
    "raiders": ["las vegas raiders", "oakland raiders"],
    "browns": ["cleveland browns"],
    "texans": ["houston texans"],
    "colts": ["indianapolis colts"],
    "jaguars": ["jacksonville jaguars"],
    "titans": ["tennessee titans"],
    "falcons": ["atlanta falcons"],
    "panthers": ["carolina panthers"],
    "buccaneers": ["tampa bay buccaneers", "bucs"],
    "cardinals": ["arizona cardinals"],
    "rams": ["los angeles rams", "la rams"],
    
    # NHL
    "maple leafs": ["toronto maple leafs"],
    "canadiens": ["montreal canadiens", "habs"],
    "bruins": ["boston bruins"],
    "rangers": ["new york rangers", "ny rangers"],
    "islanders": ["new york islanders", "ny islanders"],
    "flyers": ["philadelphia flyers"],
    "penguins": ["pittsburgh penguins", "pens"],
    "capitals": ["washington capitals", "caps"],
    "hurricanes": ["carolina hurricanes", "canes"],
    "lightning": ["tampa bay lightning", "bolts"],
    "panthers": ["florida panthers"],
    "red wings": ["detroit red wings"],
    "blackhawks": ["chicago blackhawks", "hawks"],
    "wild": ["minnesota wild"],
    "blues": ["st louis blues"],
    "predators": ["nashville predators", "preds"],
    "stars": ["dallas stars"],
    "avalanche": ["colorado avalanche", "avs"],
    "flames": ["calgary flames"],
    "oilers": ["edmonton oilers"],
    "canucks": ["vancouver canucks"],
    "kraken": ["seattle kraken"],
    "golden knights": ["vegas golden knights", "vgk"],
    "sharks": ["san jose sharks"],
    "ducks": ["anaheim ducks"],
    "coyotes": ["arizona coyotes"],
    # French (more)
    "nice": ["ogc nice"],
    "nantes": ["fc nantes"],
    "monaco": ["as monaco"],
    "lille": ["losc", "lille osc"],
    
    # Dutch
    "feyenoord": ["feyenoord rotterdam"],
    "psv": ["psv eindhoven"],
    "ajax": ["afc ajax"],
    "az alkmaar": ["az"],
    
    # Portugese
    "benfica": ["sl benfica", "sport lisboa e benfica"],
    "sporting cp": ["sporting lisbon", "sporting clube de portugal"],
    "porto": ["fc porto"],
    "braga": ["sc braga"],
    
    # Swedish/Nordic
    "farjestad": ["faerjestad", "färjestad", "farjestads bk"],
    "lulea": ["luleaa", "luleå", "lulea hf"],
    "vaxjo": ["vaexjoe", "växjö", "vaxjo lakers"],
    "orebro": ["oerebro", "örebro", "orebro hk"],
    "skelleftea": ["skellefteaa", "skellefteå", "skelleftea aik"],
    "timra": ["timraa", "timrå", "timra ik"],
    "frolunda": ["froelunda", "frölunda", "frolunda hc"],
    "rogle": ["roegle", "rögle", "rogle bk"],
    "hv71": ["hv 71"],
    "sonderjyske": ["sönderjyske", "soenderjyske", "sonderjyske fodbold"],
    "nordsjaelland": ["fc nordsjaelland", "nordsjalland", "nordsjælland", "fc nordsjælland"],
}

# Build reverse lookup
_ALIAS_TO_CANONICAL = {}
for canonical, aliases in TEAM_ALIASES.items():
    _ALIAS_TO_CANONICAL[canonical] = canonical
    for alias in aliases:
        _ALIAS_TO_CANONICAL[alias.lower()] = canonical


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
    
    # 0. Pre-normalization replacements for specific transliterations
    replacements = {
        'ä': 'a', 'ae': 'a',
        'ö': 'o', 'oe': 'o', 
        'å': 'a', 'aa': 'a',
        'ü': 'u', 'ue': 'u',
        'é': 'e', 'è': 'e',
        'ø': 'o', 'æ': 'ae', # Keep ae joined for now or map to a? Mapping to 'a' is safer if inconsistent.
        'ß': 'ss',
    }
    # Note: 'ae' -> 'a' is aggressive but helps with Faerjestad vs Farjestad if target is Farjestad.
    # But usually ae is kept. Let's do selective.
    
    # 1. Unicode normalization (remove accents)
    # Manual first
    name = name.replace('æ', 'ae').replace('ø', 'o').replace('å', 'a')
    
    name = unicodedata.normalize('NFKD', name)
    name = "".join([c for c in name if not unicodedata.combining(c)])
    
    # Post-unicode normalization fixups
    name = name.replace('ß', 'ss')
    
    # Normalize Scandinavian transliterations
    if 'shl:' in name or 'hockey' in name: # Context heuristic if passed? No context here.
        pass
    
    # Regex Cleanup
    # 1. Remove rankings: (12) Team -> Team
    name = re.sub(r'^\(\d+\)\s*', '', name)
    
    # 2. Remove state suffixes: Team-RJ -> Team
    name = re.sub(r'-[a-z]{2}$', '', name)
    
    # Remove suffixes
    for suffix in TEAM_SUFFIXES:
        if name.endswith(suffix):
            name = name[:-len(suffix)].strip()
    
    # Remove prefixes (but keep for lookup first)
    original = name
    for prefix in TEAM_PREFIXES:
        if name.startswith(prefix):
            name = name[len(prefix):].strip()
            break
    
    # Remove punctuation
    name = re.sub(r'[^\w\s]', '', name)
    name = ' '.join(name.split())
    
    # Try alias lookup
    if name in _ALIAS_TO_CANONICAL:
        return _ALIAS_TO_CANONICAL[name]
    if original in _ALIAS_TO_CANONICAL:
        return _ALIAS_TO_CANONICAL[original]
    
    # Final fallback: replace 'ae' -> 'a', 'oe' -> 'o' if common mismatch pattern
    # (Only doing this if alias lookup failed)
    if 'ae' in name or 'oe' in name or 'aa' in name:
        simplified = name.replace('ae', 'a').replace('oe', 'o').replace('aa', 'a')
        if simplified in _ALIAS_TO_CANONICAL:
            return _ALIAS_TO_CANONICAL[simplified]
    
    return name


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


def find_best_team_match(team: str, candidates: list[str], threshold: int = 80) -> Optional[str]:
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


# ============ League Mapping ============

# Map Polymarket league names to Kambi sport categories
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

# Kambi league name patterns
KAMBI_LEAGUE_PATTERNS = {
    "football": [
        "premier league", "la liga", "bundesliga", "serie a", "ligue 1",
        "champions league", "europa league", "eredivisie", "allsvenskan",
        "eliteserien", "superligaen", "primeira liga", "süper lig",
    ],
    "basketball": [
        "nba", "euroleague", "acb", "lega basket", "pro a",
    ],
    "ice_hockey": [
        "nhl", "khl", "shl", "liiga", "del", "extraliga",
    ],
    "tennis": [
        "atp", "wta", "grand slam", "masters", "open",
    ],
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


# ============ Canonical ID Generation ============

def generate_canonical_id(
    sport: str, 
    home_team: str, 
    away_team: str, 
    start_date: str,  # YYYYMMDD format
) -> str:
    """
    Generate a canonical event ID.
    
    Format: {sport}:{home_normalized}:{away_normalized}:{date}
    """
    home_norm = normalize_team_name(home_team)
    away_norm = normalize_team_name(away_team)
    
    return f"{sport}:{home_norm}:{away_norm}:{start_date}"


@dataclass
class MatchResult:
    """Result of an event matching attempt."""
    matched: bool
    canonical_id: str
    confidence: float  # 0-100
    home_team_normalized: str
    away_team_normalized: str


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
            from datetime import datetime
            d1 = datetime.strptime(event1_date, "%Y%m%d")
            d2 = datetime.strptime(event2_date, "%Y%m%d")
            diff = abs((d1 - d2).days)
            if diff <= 1:
                date_match = True
        except (ValueError, ImportError):
            pass  # Fallback to exact match required if date parsing fails

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


# ============ Test ============

if __name__ == "__main__":
    # Test team normalization
    print("Team Normalization Tests:")
    tests = [
        ("Liverpool FC", "liverpool"),
        ("Manchester United", "manchester united"),
        ("Man Utd", "manchester united"),
        ("Tottenham Hotspur FC", "tottenham"),
        ("FC Barcelona", "barcelona"),
        ("Real Madrid CF", "real madrid"),
        ("Los Angeles Lakers", "lakers"),
        ("LA Lakers", "lakers"),
        ("Golden State Warriors", "warriors"),
    ]
    
    for input_name, expected in tests:
        result = normalize_team_name(input_name)
        status = "✓" if result == expected else "✗"
        print(f"  {status} '{input_name}' -> '{result}' (expected: '{expected}')")
    
    # Test fuzzy matching
    print("\nFuzzy Matching Tests:")
    matches = [
        ("Liverpool FC", "Liverpool", True),
        ("Man United", "Manchester United", True),
        ("Real Madrid CF", "R Madrid", True),
        ("Lakers", "Los Angeles Lakers", True),
        ("Arsenal", "Liverpool", False),
    ]
    
    for team1, team2, expected in matches:
        result = fuzzy_match_teams(team1, team2)
        status = "✓" if result == expected else "✗"
        print(f"  {status} '{team1}' vs '{team2}' -> {result}")
