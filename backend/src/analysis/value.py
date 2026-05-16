"""
Value Bet Detection

Finds +EV bets where provider odds exceed fair odds from Pinnacle.

Sharp source: Pinnacle ONLY (de-vigged to remove ~2.5% margin)

Formula:
    edge = (provider_odds / fair_odds) - 1
    edge_pct = edge * 100

A bet has value if edge > 0 (provider odds > fair odds).
"""

import logging
from dataclasses import dataclass

from .devig import get_fair_odds_for_outcome

logger = logging.getLogger(__name__)


@dataclass
class ValueBet:
    """A detected value betting opportunity."""

    event_id: str
    market: str
    outcome: str

    # The bet
    provider: str
    provider_odds: float

    # The truth (from Pinnacle de-vigged)
    fair_odds: float
    fair_probability: float

    # The edge
    edge_pct: float

    # Optional stake recommendation (filled by StakeCalculator integration)
    recommended_stake: float | None = None
    kelly_fraction: float | None = None
    is_high_confidence: bool | None = None
    skip_reason: str | None = None

    # Freshness tracking
    odds_updated_at: str | None = None  # ISO timestamp of when this provider's odds were last updated

    # Point/line value (for spread/total markets)
    point: float | None = None

    # Event context (optional, for display)
    home_team: str | None = None
    away_team: str | None = None
    sport: str | None = None
    start_time: str | None = None

    # ML feature data (populated by scanner, consumed by feature extractor)
    prob_sum: float | None = None
    pinnacle_overround: float | None = None
    odds_snapshot: list | None = None

    @property
    def expected_value(self) -> float:
        """Expected value per $1 bet."""
        return (self.provider_odds * self.fair_probability) - 1


def compute_edge(provider: str, provider_odds: float, fair_odds: float) -> float | None:
    """Compute edge percentage of provider odds vs fair odds.

    Stored odds are already net of any provider-side fee (Polymarket's 2% fee on
    net profit is applied in polymarket._price_to_odds at extraction; Kalshi's
    per-trade fee in kalshi._price_to_odds). No further adjustment here.

    Returns edge as percentage (e.g. 5.0 for 5% edge), or None if inputs invalid.
    """
    if fair_odds <= 1 or provider_odds <= 1:
        return None
    return (provider_odds / fair_odds - 1) * 100


def find_value(
    event_id: str,
    market: str,
    outcome: str,
    provider: str,
    provider_odds: float,
    fair_odds: float,
    min_edge_pct: float = 2.0,
    **kwargs,
) -> ValueBet | None:
    """
    Check if a bet has positive expected value.

    Args:
        event_id: Canonical event ID
        market: Market type
        outcome: Outcome name ("home", "over", etc.)
        provider: Provider offering the odds
        provider_odds: Decimal odds from provider
        fair_odds: Fair decimal odds (from Pinnacle de-vigged)
        min_edge_pct: Minimum edge to consider (default 2%)

    Returns:
        ValueBet if edge >= min_edge_pct, None otherwise
    """
    if fair_odds <= 1 or provider_odds <= 1:
        return None

    # NOTE: Spread cost subtraction removed — Polymarket odds are already based
    # on ask-side VWAP (from _calc_vwap_from_asks), so the taker cost is already
    # reflected in provider_odds. Subtracting spread cost again was double-counting.

    edge_pct = compute_edge(provider, provider_odds, fair_odds)
    if edge_pct is None:
        return None

    if edge_pct < min_edge_pct:
        return None

    fair_probability = 1 / fair_odds

    return ValueBet(
        event_id=event_id,
        market=market,
        outcome=outcome,
        provider=provider,
        provider_odds=provider_odds,
        fair_odds=round(fair_odds, 3),
        fair_probability=round(fair_probability, 3),
        edge_pct=round(edge_pct, 2),
    )


def scan_for_value(
    event_id: str,
    market: str,
    outcome: str,
    fair_odds: float,
    provider_odds_list: list[dict],
    min_edge_pct: float = 2.0,
) -> list[ValueBet]:
    """
    Find all value bets for an outcome across providers.

    Args:
        event_id: Canonical event ID
        market: Market type
        outcome: Outcome name
        fair_odds: Fair odds (from Pinnacle de-vigged)
        provider_odds_list: [{provider, odds}, ...]
        min_edge_pct: Minimum edge threshold

    Returns:
        List of ValueBet opportunities
    """
    value_bets = []

    for po in provider_odds_list:
        vb = find_value(
            event_id=event_id,
            market=market,
            outcome=outcome,
            provider=po["provider"],
            provider_odds=po["odds"],
            fair_odds=fair_odds,
            min_edge_pct=min_edge_pct,
        )
        if vb:
            value_bets.append(vb)
            logger.debug(f"Value bet: {vb.provider} {vb.outcome} @ {vb.provider_odds} (+{vb.edge_pct}%)")

    return value_bets


def find_best_value(
    event_id: str,
    market: str,
    outcome: str,
    fair_odds: float,
    provider_odds_list: list[dict],
    min_edge_pct: float = 2.0,
) -> ValueBet | None:
    """
    Find the single best value bet for an outcome.

    Returns the provider with highest edge, or None if no value exists.
    """
    value_bets = scan_for_value(event_id, market, outcome, fair_odds, provider_odds_list, min_edge_pct)

    if not value_bets:
        return None

    return max(value_bets, key=lambda x: x.edge_pct)


def get_fair_odds(
    outcome: str,
    market_odds: dict[str, list[dict]],
) -> tuple[float, str] | None:
    """
    Get de-vigged fair odds for an outcome from Pinnacle.

    Pinnacle is the sole sharp source. Their ~2.5% margin is removed
    using multiplicative de-vigging.

    Args:
        outcome: The outcome to get fair odds for ("home", "away", etc.)
        market_odds: All odds for this market {outcome: [{provider, odds}, ...]}

    Returns:
        (fair_odds, "pinnacle") or None if Pinnacle not found

    Example:
        >>> market = {
        ...     "home": [{"provider": "pinnacle", "odds": 2.10}],
        ...     "draw": [{"provider": "pinnacle", "odds": 3.40}],
        ...     "away": [{"provider": "pinnacle", "odds": 3.50}],
        ... }
        >>> get_fair_odds("home", market)
        (2.16, "pinnacle")
    """
    # Find Pinnacle odds for this outcome
    outcome_providers = market_odds.get(outcome, [])
    if not outcome_providers:
        return None

    pinnacle_odds = None
    for po in outcome_providers:
        if po.get("provider") == "pinnacle":
            pinnacle_odds = po["odds"]
            break

    if pinnacle_odds is None:
        return None

    # Build Pinnacle market odds for de-vigging
    pinnacle_market = {}
    for out, providers in market_odds.items():
        for p in providers:
            if p.get("provider") == "pinnacle":
                pinnacle_market[out] = p["odds"]
                break

    if len(pinnacle_market) >= 2:
        # Full market available, de-vig properly
        fair_odds = get_fair_odds_for_outcome(outcome, pinnacle_market, method="multiplicative")
        return (fair_odds, "pinnacle")
    else:
        # Single outcome, can't de-vig - use raw (not ideal)
        return (pinnacle_odds, "pinnacle(raw)")
