"""Shared constants for OddOpp."""

# Market types to extract (all others skipped)
ALLOWED_MARKETS = frozenset({'1x2', 'moneyline', 'spread', 'total'})

# Sharp/reference providers for fair odds
SHARP_PROVIDERS = frozenset({'pinnacle'})

# Providers excluded from opportunity scans (not used for betting)
EXCLUDED_FROM_SCANS = frozenset({'polymarket'})

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
