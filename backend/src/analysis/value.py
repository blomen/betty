"""
Value Bet Detection

Finds +EV bets where provider odds exceed fair odds from Polymarket.

Formula:
    fair_odds = 1 / polymarket_probability
    edge = (provider_odds / fair_odds) - 1
    edge_pct = edge * 100
    
A bet has value if edge > 0 (provider odds > fair odds).
"""

from dataclasses import dataclass
from typing import Optional
import logging

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
