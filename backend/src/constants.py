"""Shared constants for BankrollBBQ."""

# Market types to extract (all others skipped)
ALLOWED_MARKETS = frozenset({'1x2', 'moneyline', 'spread', 'total'})

# Esports map-level markets — used for map winner value scanning
# (Pinnacle period 1-5 + Polymarket child_moneyline/map_handicap)
MAP_MARKETS = frozenset({
    'moneyline_m1', 'moneyline_m2', 'moneyline_m3', 'moneyline_m4', 'moneyline_m5',
    'total_m1', 'total_m2', 'total_m3', 'total_m4', 'total_m5',
})

# Extended markets stored for Pinnacle only — used by boost EV enrichment,
# NOT by the value scanner.
ENRICHMENT_MARKETS = ALLOWED_MARKETS | MAP_MARKETS | frozenset({
    'team_total',       # Team over/under (Pinnacle API type)
    '1x2_1h',           # First-half 1x2 (period=1)
    'moneyline_1h',     # First-half moneyline (period=1)
    'total_1h',         # First-half total (period=1)
})

# Sharp/reference providers for fair odds
SHARP_PROVIDERS = frozenset({'pinnacle'})

# Providers that store the extended market set (enrichment + map markets).
# Pinnacle: sharp baseline for all markets.
# Polymarket: needs map markets for value comparison vs Pinnacle.
EXTENDED_MARKET_PROVIDERS = SHARP_PROVIDERS | frozenset({'polymarket'})

# Platform map: provider_id -> platform name
# Providers on the same platform share the same odds engine (not independent).
# Used for consensus calculations where we need independent pricing sources.
PLATFORM_MAP: dict[str, str] = {
    # Kambi — 100% identical odds across all brands
    'unibet': 'kambi', 'leovegas': 'kambi', 'expekt': 'kambi', 'betmgm': 'kambi',
    'speedybet': 'kambi', 'x3000': 'kambi', 'goldenbull': 'kambi', '1x2': 'kambi',
    # Altenar — main group ~99.7% identical; dbet ~70% identical (separate extraction)
    'dbet': 'altenar', 'betinia': 'altenar', 'lodur': 'altenar',
    'campobet': 'altenar', 'swiper': 'altenar', 'quickcasino': 'altenar',
    # Gecko V2 — ~40% identical (some variance between brands)
    'betsson': 'gecko', 'nordicbet': 'gecko', 'bethard': 'gecko', 'spelklubben': 'gecko',
    # Spectate — 100% identical
    'mrgreen': 'spectate', '888sport': 'spectate',
    # ComeOn Group — same odds engine, identical odds confirmed 2026-03-14
    'comeon': 'comeon', 'hajper': 'comeon', 'lyllo': 'comeon', 'snabbare': 'comeon',
    # Standalone platforms (each is its own independent source)
    'vbet': 'vbet', 'interwetten': 'interwetten', '10bet': '10bet',
    'tipwin': 'tipwin', 'coolbet': 'coolbet',
    # Sharp
    'pinnacle': 'pinnacle',
    # Prediction markets
    'polymarket': 'polymarket',
}

# Platform groups for consolidation: extract once per platform, store under canonical
# Providers on the same platform produce identical (or near-identical) odds.
# We extract only the canonical provider and fan out opportunities to all members.
PLATFORM_GROUPS: dict[str, dict] = {
    "kambi": {
        "canonical": "unibet",
        "members": ["unibet", "leovegas", "expekt", "betmgm", "speedybet", "x3000", "goldenbull", "1x2"],
    },
    "spectate": {
        "canonical": "888sport",
        "members": ["888sport", "mrgreen"],
    },
    "altenar_main": {
        "canonical": "betinia",
        "members": ["betinia", "campobet", "lodur", "quickcasino", "swiper", "dbet"],
    },
    "gecko_v2": {
        "canonical": "betsson",
        "members": ["betsson", "nordicbet", "bethard", "spelklubben", "betsafe"],
    },
    "comeon_group": {
        "canonical": "comeon",
        "members": ["comeon", "lyllo", "hajper", "snabbare"],
    },
}

# Reverse lookup: non-canonical provider → canonical provider
# e.g. {"expekt": "unibet", "mrgreen": "888sport", ...}
# Only contains non-canonical providers (canonical maps to itself implicitly)
PROVIDER_CANONICAL: dict[str, str] = {}
for _group in PLATFORM_GROUPS.values():
    _canonical = _group["canonical"]
    for _member in _group["members"]:
        if _member != _canonical:
            PROVIDER_CANONICAL[_member] = _canonical

# Reverse lookup: canonical provider → all member providers
# e.g. {"unibet": ["unibet", "leovegas", ...], "888sport": ["888sport", "mrgreen"]}
CANONICAL_MEMBERS: dict[str, list[str]] = {
    _group["canonical"]: _group["members"]
    for _group in PLATFORM_GROUPS.values()
}

# Sports to extract - these have pinnacle_id in sports.yaml
# Only extract sports where Pinnacle provides sharp lines AND soft providers
# have head-to-head match coverage for value comparison.
# Excluded: golf, cycling, motorsports (outright/winner markets only — no soft match coverage)
# Major leagues per sport — used by dutch workflow "limited" toggle
# When limited at a provider, only play major leagues where limits are higher
MAJOR_LEAGUES: dict[str, list[str]] = {
    "football": [
        "England - Premier League", "Spain - La Liga", "Germany - Bundesliga",
        "Italy - Serie A", "France - Ligue 1", "England - Championship",
        "USA - Major League Soccer", "Brazil - Serie A",
        "FIFA - World Cup", "FIFA - World Cup Qualifiers Europe",
        "UEFA Champions League", "UEFA Europa League",
    ],
    "basketball": ["NBA", "NCAA"],
    "ice_hockey": ["NHL"],
    "baseball": ["MLB", "MLB - Pre Season"],
    "mma": ["UFC"],
    "boxing": ["Boxing Matches"],
}

MAJOR_LEAGUES_FLAT: frozenset[str] = frozenset(
    league for leagues in MAJOR_LEAGUES.values() for league in leagues
)

ALLOWED_SPORTS = frozenset({
    'football',
    'basketball',
    'tennis',
    'ice_hockey',
    'american_football',
    'baseball',
    'mma',
    'esports',
    'boxing',
    'cricket',
    'rugby',
    'volleyball',
    'handball',
    'darts',
    'table_tennis',
    'curling',
})


# ============ Trading Constants ============

TRADING_ACCOUNT_TYPES = frozenset({"intraday", "swing", "hodl"})

TRADE_STATES = ("created", "armed", "triggered", "open", "managed", "closed", "reviewed")

TRADE_DIRECTIONS = frozenset({"long", "short"})

BIAS_DIRECTIONS = frozenset({"bullish", "bearish", "neutral"})

TRADE_STATE_TRANSITIONS = {
    "created": {"armed", "closed"},
    "armed": {"triggered", "closed"},
    "triggered": {"open", "closed"},
    "open": {"managed", "closed"},
    "managed": {"closed"},
    "closed": {"reviewed"},
}

PSYCH_GATE_THRESHOLD = 5.0
