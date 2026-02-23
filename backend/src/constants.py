"""Shared constants for BankrollBBQ."""

# Market types to extract (all others skipped)
ALLOWED_MARKETS = frozenset({'1x2', 'moneyline', 'spread', 'total'})

# Sharp/reference providers for fair odds
SHARP_PROVIDERS = frozenset({'pinnacle'})

# Providers excluded from opportunity scans (not used for betting)
EXCLUDED_FROM_SCANS = frozenset()

# Platform map: provider_id -> platform name
# Providers on the same platform share the same odds engine (not independent).
# Used for consensus calculations where we need independent pricing sources.
PLATFORM_MAP: dict[str, str] = {
    # Kambi — 100% identical odds across all brands
    'unibet': 'kambi', 'leovegas': 'kambi', 'expekt': 'kambi', 'betmgm': 'kambi',
    'speedybet': 'kambi', 'x3000': 'kambi', 'goldenbull': 'kambi', '1x2': 'kambi',
    # Altenar — 99.7% identical
    'dbet': 'altenar', 'betinia': 'altenar', 'lodur': 'altenar',
    'campobet': 'altenar', 'swiper': 'altenar', 'quickcasino': 'altenar',
    # Gecko V2 — ~40% identical (some variance between brands)
    'betsson': 'gecko', 'nordicbet': 'gecko', 'bethard': 'gecko', 'spelklubben': 'gecko',
    # Spectate — 100% identical
    'mrgreen': 'spectate', '888sport': 'spectate',
    # ComeOn Group — ~68% identical
    'comeon': 'comeon', 'hajper': 'comeon', 'lyllo': 'comeon',
    # Standalone platforms (each is its own independent source)
    'vbet': 'vbet', 'interwetten': 'interwetten', '10bet': '10bet',
    'tipwin': 'tipwin', 'coolbet': 'coolbet', 'snabbare': 'snabbare',
    # Sharp
    'pinnacle': 'pinnacle',
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
        "members": ["betinia", "campobet", "lodur", "quickcasino", "swiper"],
    },
    "gecko_betsson": {
        "canonical": "betsson",
        "members": ["betsson", "nordicbet"],
    },
    "gecko_bethard": {
        "canonical": "bethard",
        "members": ["bethard", "spelklubben"],
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

# Provider domain → provider_id mapping (for CDP session discovery)
# Built from providers.yaml `domain` fields
PROVIDER_DOMAINS: dict[str, str] = {
    # Kambi brands
    "unibet.se": "unibet",
    "leovegas.com": "leovegas",
    "expekt.se": "expekt",
    "betmgm.se": "betmgm",
    "speedybet.com": "speedybet",
    "x3000.se": "x3000",
    "goldenbull.se": "goldenbull",
    "1x2.se": "1x2",
    # Altenar brands
    "betinia.se": "betinia",
    "campobet.se": "campobet",
    "swiper.se": "swiper",
    "lodur.se": "lodur",
    "dbet.com": "dbet",
    "quickcasino.se": "quickcasino",
    # Spectate
    "mrgreen.se": "mrgreen",
    "888sport.se": "888sport",
    # Gecko V2
    "betsson.com": "betsson",
    "betsafe.com": "betsafe",
    "nordicbet.com": "nordicbet",
    "spelklubben.se": "spelklubben",
    "bethard.com": "bethard",
    # ComeOn Group
    "comeon.com": "comeon",
    "hajper.com": "hajper",
    "lyllocasino.com": "lyllo",
    # Standalone
    "vbet.se": "vbet",
    "interwetten.se": "interwetten",
    "10bet.se": "10bet",
    "snabbare.com": "snabbare",
    "coolbet.com": "coolbet",
    "tipwin.se": "tipwin",
    # Sharp — Pinnacle uses several domains
    "pinnacle.com": "pinnacle",
    "pinnacle.se": "pinnacle",
    "pinnacle.bet": "pinnacle",
    "pinnacle.ca": "pinnacle",
    "ps3838.com": "pinnacle",         # Asian mirror
    "arcadia.pinnacle.com": "pinnacle",
}

# Sports to extract - these have pinnacle_id in sports.yaml
# Only extract sports where Pinnacle provides sharp lines AND soft providers
# have head-to-head match coverage for value comparison.
# Excluded: golf, cycling, motorsports (outright/winner markets only — no soft match coverage)
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
    'snooker',
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
