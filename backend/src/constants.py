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
