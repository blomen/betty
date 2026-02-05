"""Shared constants for OddOpp."""

# Market types to extract (all others skipped)
ALLOWED_MARKETS = frozenset({'1x2', 'moneyline'})

# Sharp/reference providers for fair odds
SHARP_PROVIDERS = frozenset({'pinnacle'})

# Providers excluded from main value scans
# Empty set - scan ALL providers including Polymarket for value
EXCLUDED_FROM_SCANS = frozenset()
