"""
Value Bet Detection

Finds +EV bets where provider odds exceed fair odds from sharp sources.

Primary sharp: Pinnacle (de-vigged) > Polymarket
If both exist, blend 60% Pinnacle + 40% Polymarket.

Formula:
    edge = (provider_odds / fair_odds) - 1
    edge_pct = edge * 100

A bet has value if edge > 0 (provider odds > fair odds).
"""

from dataclasses import dataclass
from typing import Optional
import logging

from .devig import get_fair_odds_for_outcome, blend_fair_odds

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
    
    # The truth
    fair_odds: float          # From Polymarket
    fair_probability: float   # Implied probability
    
    # The edge
    edge_pct: float
    
    @property
    def expected_value(self) -> float:
        """Expected value per $1 bet."""
        return (self.provider_odds * self.fair_probability) - 1


def find_value(
    event_id: str,
    market: str,
    outcome: str,
    provider: str,
    provider_odds: float,
    fair_odds: float,
    min_edge_pct: float = 2.0
) -> Optional[ValueBet]:
    """
    Check if a bet has positive expected value.
    
    Args:
        event_id: Canonical event ID
        market: Market type
        outcome: Outcome name ("home", "over", etc.)
        provider: Provider offering the odds
        provider_odds: Decimal odds from provider
        fair_odds: Fair decimal odds from Polymarket
        min_edge_pct: Minimum edge to consider (default 2%)
    
    Returns:
        ValueBet if edge >= min_edge_pct, None otherwise
    """
    if fair_odds <= 1 or provider_odds <= 1:
        return None
    
    # Calculate edge
    edge = (provider_odds / fair_odds) - 1
    edge_pct = edge * 100
    
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
    min_edge_pct: float = 2.0
) -> list[ValueBet]:
    """
    Find all value bets for an outcome across providers.
    
    Args:
        event_id: Canonical event ID
        market: Market type
        outcome: Outcome name
        fair_odds: Fair odds from Polymarket
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
            logger.info(f"Value bet: {vb.provider} {vb.outcome} @ {vb.provider_odds} (+{vb.edge_pct}%)")
    
    return value_bets


def find_best_value(
    event_id: str,
    market: str,
    outcome: str,
    fair_odds: float,
    provider_odds_list: list[dict],
    min_edge_pct: float = 2.0
) -> Optional[ValueBet]:
    """
    Find the single best value bet for an outcome.
    
    Returns the provider with highest edge, or None if no value exists.
    """
    value_bets = scan_for_value(
        event_id, market, outcome, fair_odds, provider_odds_list, min_edge_pct
    )
    
    if not value_bets:
        return None
    
    return max(value_bets, key=lambda x: x.edge_pct)


def get_fair_odds(
    outcome: str,
    market_odds: dict[str, list[dict]],
    sharp_priority: list[str] = None,
    blend_weight: float = 0.6,
) -> Optional[tuple[float, str]]:
    """
    Get de-vigged fair odds for an outcome.

    - De-vigs Pinnacle if available
    - Blends Pinnacle (60%) + Polymarket (40%) if both exist
    - Falls back to Polymarket only

    Args:
        outcome: The outcome to get fair odds for ("home", "away", etc.)
        market_odds: All odds for this market {outcome: [{provider, odds}, ...]}
        sharp_priority: Sharp providers in priority order (default ["pinnacle", "polymarket"])
        blend_weight: Pinnacle weight when blending (default 0.6 = 60%)

    Returns:
        (fair_odds, source_description) or None if no sharp found

    Example:
        >>> market = {
        ...     "home": [{"provider": "pinnacle", "odds": 2.10}, {"provider": "polymarket", "odds": 2.05}],
        ...     "draw": [{"provider": "pinnacle", "odds": 3.40}],
        ...     "away": [{"provider": "pinnacle", "odds": 3.50}],
        ... }
        >>> get_fair_odds("home", market)
        (2.16, "pinnacle(60%)+polymarket(40%)")
    """
    if sharp_priority is None:
        sharp_priority = ["pinnacle", "polymarket"]

    # Find sharp provider odds for this outcome
    outcome_providers = market_odds.get(outcome, [])
    if not outcome_providers:
        return None

    pinnacle_odds = None
    polymarket_odds = None

    for po in outcome_providers:
        provider = po.get("provider", "")
        if provider == "pinnacle":
            pinnacle_odds = po["odds"]
        elif provider == "polymarket":
            polymarket_odds = po["odds"]

    # De-vig Pinnacle if available
    pinnacle_fair = None
    if pinnacle_odds is not None:
        # Build Pinnacle market odds for de-vigging
        pinnacle_market = {}
        for out, providers in market_odds.items():
            for p in providers:
                if p.get("provider") == "pinnacle":
                    pinnacle_market[out] = p["odds"]
                    break

        if len(pinnacle_market) >= 2:
            # Full market available, de-vig properly
            pinnacle_fair = get_fair_odds_for_outcome(
                outcome, pinnacle_market, method="multiplicative"
            )
        else:
            # Single outcome, can't de-vig - use raw
            pinnacle_fair = pinnacle_odds

    # Blend or fall back
    return blend_fair_odds(pinnacle_fair, polymarket_odds, blend_weight)


# Quick test
if __name__ == "__main__":
    # Example: Polymarket says 45% chance of home win (fair odds = 2.22)
    # Unibet offers 2.40 - is this value?
    
    fair_odds = 1 / 0.45  # 2.22
    
    providers = [
        {"provider": "unibet", "odds": 2.40},    # +8% edge
        {"provider": "bet365", "odds": 2.25},    # +1% edge (below threshold)
        {"provider": "betsson", "odds": 2.15},   # -3% edge (no value)
    ]
    
    print(f"Fair odds: {fair_odds:.2f} (45% probability)")
    print()
    
    for p in providers:
        edge = (p["odds"] / fair_odds - 1) * 100
        print(f"{p['provider']}: {p['odds']} → {edge:+.1f}% edge")
    
    print()
    
    best = find_best_value("test", "1x2", "home", fair_odds, providers, min_edge_pct=2.0)
    if best:
        print(f"Best value: {best.provider} @ {best.provider_odds} (+{best.edge_pct}%)")
    else:
        print("No value found above threshold")
