"""Shared constants for OddOpp."""

# Market types to extract (all others skipped)
ALLOWED_MARKETS = frozenset({'1x2', 'moneyline'})

# Sharp/reference providers for fair odds
SHARP_PROVIDERS = frozenset({'pinnacle'})

# Providers excluded from main arb/value scans (have their own dedicated views)
EXCLUDED_FROM_SCANS = frozenset({'polymarket'})
