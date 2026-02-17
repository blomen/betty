"""Shared constants for DegenTraderXD."""

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

# Sports to extract - these have pinnacle_id in sports.yaml
# Only extract sports where Pinnacle provides sharp lines
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
    'golf',
    'volleyball',
    'handball',
    'darts',
    'table_tennis',
    'snooker',
    'motorsports',
    'cycling',
    'curling',
})
