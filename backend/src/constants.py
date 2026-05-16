"""Shared constants for Arnold."""

# Market types to extract (all others skipped)
ALLOWED_MARKETS = frozenset({"1x2", "moneyline", "spread", "total"})

# Esports map-level markets — used for map winner value scanning
# (Pinnacle period 1-5 + Polymarket child_moneyline/map_handicap)
MAP_MARKETS = frozenset(
    {
        "moneyline_m1",
        "moneyline_m2",
        "moneyline_m3",
        "moneyline_m4",
        "moneyline_m5",
        "total_m1",
        "total_m2",
        "total_m3",
        "total_m4",
        "total_m5",
    }
)

# Extended markets stored for Pinnacle/Polymarket — includes esports map markets
# for map-level value scanning. team_total/1h markets removed (never used by analysis).
ENRICHMENT_MARKETS = ALLOWED_MARKETS | MAP_MARKETS

# Sharp/reference providers for fair odds
SHARP_PROVIDERS = frozenset({"pinnacle"})

# Signal-only providers — odds used for consensus/fair-odds but NOT for opportunity
# generation (can't place bets on these). Their odds strengthen the model but they
# should never appear as "bet on marathon" in the frontend.
SIGNAL_ONLY_PROVIDERS = frozenset({"marathon", "stake", "smarkets"})

# Polymarket fee is applied once at extraction time inside
# providers.polymarket._price_to_odds (local POLY_FEE_RATE = 0.02). Stored DB
# odds are already net of the fee, so downstream consumers MUST NOT re-apply.

# Kalshi per-trade fee approximation. Actual formula is
# ceil(0.07 × price × (1 − price) × contracts); we model it as a flat
# multiplier on the price (tune from live fills data once enough trades land).
KALSHI_FEE_RATE = 0.07

# Prediction-market exchanges. Their pricing diverges from traditional sportsbook
# books (binary contracts, single-side-of-book quoting, illiquidity-driven floor/
# ceiling prints) so scanner gates that assume sportsbook microstructure must
# exempt them — see scanner.MIN_VALID_PROB_SUM and _has_odds_discrepancy.
PREDICTION_MARKETS = frozenset({"polymarket", "kalshi"})

# Providers that store the extended market set (enrichment + map markets).
# Pinnacle: sharp baseline for all markets.
# Polymarket + Kalshi: prediction-market microstructure for value comparison.
EXTENDED_MARKET_PROVIDERS = SHARP_PROVIDERS | PREDICTION_MARKETS

# Platform map: provider_id -> platform name
# Providers on the same platform share the same odds engine (not independent).
# Used for consensus calculations where we need independent pricing sources.
PLATFORM_MAP: dict[str, str] = {
    # Kambi — 100% identical odds across all brands
    "unibet": "kambi",
    "leovegas": "kambi",
    "expekt": "kambi",
    "betmgm": "kambi",
    "speedybet": "kambi",
    "x3000": "kambi",
    "goldenbull": "kambi",
    "1x2": "kambi",
    # Altenar — main group ~99.7% identical; dbet ~70% identical (separate extraction)
    "dbet": "altenar",
    "betinia": "altenar",
    "lodur": "altenar",
    "campobet": "altenar",
    "swiper": "altenar",
    "quickcasino": "altenar",
    # Gecko V2 / OBG — empirically all 5 brands serve the same backend (live API
    # audit 2026-04-27 hit each brand simultaneously, paired event IDs across all
    # 5, sub-percent odds variance across the matrix). Two margin tiers exist:
    #   Cluster A (CDN-fronted, slightly softer payouts): bethard, spelklubben — 0.00% diff
    #   Cluster B (origin-domain): betsson, betsafe, nordicbet — 0.00% diff in-cluster
    # Cross-cluster diff ≈1-2% — same lines, different markup config. We collapse
    # to a single platform anchored on spelklubben (Cluster A: faster CDN endpoint,
    # slightly better payouts, peak 8.2 ev/s observed). Other 4 fan-out as members.
    "betsson": "gecko_obg",
    "nordicbet": "gecko_obg",
    "spelklubben": "gecko_obg",
    "betsafe": "gecko_obg",
    "bethard": "gecko_obg",
    # Spectate — 100% identical
    "mrgreen": "spectate",
    "888sport": "spectate",
    # ComeOn Group — same odds engine, identical odds confirmed 2026-03-14
    "comeon": "comeon",
    "hajper": "comeon",
    "lyllo": "comeon",
    "snabbare": "comeon",
    # Standalone platforms (each is its own independent source)
    "vbet": "vbet",
    "interwetten": "interwetten",
    "10bet": "10bet",
    "tipwin": "tipwin",
    "coolbet": "coolbet",
    # Sharp
    "pinnacle": "pinnacle",
    # Prediction markets
    "polymarket": "polymarket",
    # International signal providers (independent odds, used for consensus)
    "marathon": "marathon",
    "cloudbet": "cloudbet",
    "stake": "stake",
    # Prediction-market exchange (playable)
    "kalshi": "kalshi",
    # Signal-only exchange (user is IP-banned from placement)
    "smarkets": "smarkets",
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
    "gecko_obg": {
        # Spelklubben canonical: same OBG backend as the others (verified by live API
        # comparison 2026-04-27, 13 shared event IDs, 0.00% diff vs bethard, ~1% vs
        # betsson group). CDN-fronted endpoint d-cf.spelklubbenplayground.net is
        # faster and more stable than origin-domain alternatives.
        "canonical": "spelklubben",
        "members": ["spelklubben", "bethard", "betsson", "betsafe", "nordicbet"],
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
    _group["canonical"]: _group["members"] for _group in PLATFORM_GROUPS.values()
}

# Sports to extract - these have pinnacle_id in sports.yaml
# Only extract sports where Pinnacle provides sharp lines AND soft providers
# have head-to-head match coverage for value comparison.
# Excluded: golf, cycling, motorsports (outright/winner markets only — no soft match coverage)
# Major leagues per sport — used by arb workflow "limited" toggle
# When limited at a provider, only play major leagues where limits are higher
MAJOR_LEAGUES: dict[str, list[str]] = {
    "football": [
        "England - Premier League",
        "Spain - La Liga",
        "Germany - Bundesliga",
        "Italy - Serie A",
        "France - Ligue 1",
        "England - Championship",
        "USA - Major League Soccer",
        "Brazil - Serie A",
        "FIFA - World Cup",
        "FIFA - World Cup Qualifiers Europe",
        "UEFA Champions League",
        "UEFA Europa League",
    ],
    "basketball": ["NBA", "NCAA"],
    "ice_hockey": ["NHL"],
    "baseball": ["MLB", "MLB - Pre Season"],
    "mma": ["UFC"],
    "boxing": ["Boxing Matches"],
}

MAJOR_LEAGUES_FLAT: frozenset[str] = frozenset(league for leagues in MAJOR_LEAGUES.values() for league in leagues)

# Sports where Pinnacle provides sharp lines AND soft providers have head-to-head
# coverage for value comparison. Renamed from ALLOWED_SPORTS for clarity — the set
# represents Pinnacle's coverage, not a generic allowlist.
PINNACLE_SPORTS = frozenset(
    {
        "football",
        "basketball",
        "tennis",
        "ice_hockey",
        "american_football",
        "baseball",
        "mma",
        "esports",
        "boxing",
        "cricket",
        "rugby",
        "volleyball",
        "handball",
        "darts",
        "table_tennis",
        "curling",
    }
)


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
