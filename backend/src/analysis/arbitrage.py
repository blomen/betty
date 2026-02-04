"""
Arbitrage Detection

Finds guaranteed profit opportunities when the sum of implied 
probabilities across providers is less than 100%.

Formula:
    implied_prob = 1 / decimal_odds
    arb_exists = sum(best_implied_prob_per_outcome) < 1
    profit_pct = (1 - sum) * 100
"""

from dataclasses import dataclass
from typing import Optional
import logging

logger = logging.getLogger(__name__)


@dataclass
class ArbitrageOpportunity:
    """A detected arbitrage opportunity."""
    event_id: str
    market: str
    profit_pct: float

    # Best odds per outcome
    outcomes: list[dict]  # [{outcome, provider, odds}]

    # Recommended stakes for $100 total
    stakes: list[dict]    # [{outcome, provider, stake, return}]

    # Quality classification: "verified" (normal), "suspect" (needs validation)
    # Suspect arbs have unusually high profit (>7%) indicating possible data issues
    quality: str = "verified"

    @property
    def total_stake(self) -> float:
        return sum(s["stake"] for s in self.stakes)

    @property
    def is_suspect(self) -> bool:
        return self.quality == "suspect"


def find_arbitrage(
    event_id: str,
    market: str,
    odds_by_outcome: dict[str, list[dict]],
    min_profit_pct: float = 0.5
) -> Optional[ArbitrageOpportunity]:
    """
    Check if arbitrage exists for a market.

    Args:
        event_id: Canonical event ID
        market: Market type ("1x2", "moneyline")
        odds_by_outcome: {outcome: [{provider, odds}, ...]}
        min_profit_pct: Minimum profit to consider (default 0.5%)

    Returns:
        ArbitrageOpportunity if found, None otherwise

    Example:
        odds_by_outcome = {
            "home": [{"provider": "unibet", "odds": 2.10}, {"provider": "bet365", "odds": 2.05}],
            "draw": [{"provider": "unibet", "odds": 3.50}, {"provider": "bet365", "odds": 3.40}],
            "away": [{"provider": "unibet", "odds": 3.20}, {"provider": "bet365", "odds": 3.30}],
        }
    """
    if not odds_by_outcome:
        return None

    # Validate market completeness - must have all expected outcomes
    if not _is_valid_market(market, odds_by_outcome):
        return None

    # Find best odds for each outcome
    best_per_outcome = []
    for outcome, odds_list in odds_by_outcome.items():
        if not odds_list:
            continue
        best = max(odds_list, key=lambda x: x["odds"])
        best_per_outcome.append({
            "outcome": outcome,
            "provider": best["provider"],
            "odds": best["odds"],
        })

    if len(best_per_outcome) < 2:
        return None  # Need at least 2 outcomes

    # CRITICAL: Arb requires DIFFERENT providers for different outcomes
    # If all best odds come from the same provider, it's not an arb
    providers_used = set(o["provider"] for o in best_per_outcome)
    if len(providers_used) < 2:
        return None  # Same provider for all outcomes - no arb possible

    # Calculate sum of implied probabilities
    implied_sum = sum(1 / o["odds"] for o in best_per_outcome)

    # Arb exists if sum < 1
    if implied_sum >= 1:
        return None

    profit_pct = (1 - implied_sum) * 100

    if profit_pct < min_profit_pct:
        return None

    # Calculate stakes for $100 total stake
    stakes = calculate_arb_stakes(best_per_outcome, total_stake=100)

    return ArbitrageOpportunity(
        event_id=event_id,
        market=market,
        profit_pct=round(profit_pct, 2),
        outcomes=best_per_outcome,
        stakes=stakes,
    )


def _is_valid_market(market: str, odds_by_outcome: dict) -> bool:
    """
    Check if market has all required outcomes.

    Returns False for incomplete markets that would give false arb signals.
    """
    outcomes = set(k.lower() for k in odds_by_outcome.keys())
    market_lower = market.lower()

    # 1x2 markets require all 3 outcomes
    if market_lower in ("1x2", "match_result", "fulltime", "slutresultat"):
        required = {"home", "draw", "away"}
        # Also accept Swedish: hemma, oavgjort, borta
        alt_required = {"hemma", "oavgjort", "borta"}
        # Also accept: 1, x, 2
        num_required = {"1", "x", "2"}
        return (
            required.issubset(outcomes) or
            alt_required.issubset(outcomes) or
            num_required.issubset(outcomes)
        )

    # Moneyline (2-way) requires exactly 2 outcomes
    if market_lower in ("moneyline", "winner", "h2h", "head_to_head"):
        return len(outcomes) == 2

    # We only support 1x2/moneyline - reject all other markets
    return False


def calculate_arb_stakes(
    outcomes: list[dict], 
    total_stake: float = 100
) -> list[dict]:
    """
    Calculate optimal stake per outcome for guaranteed equal return.
    
    For arbitrage, stake each outcome proportionally to 1/odds.
    This ensures equal return regardless of outcome.
    """
    implied_sum = sum(1 / o["odds"] for o in outcomes)
    
    stakes = []
    for o in outcomes:
        stake = (total_stake / o["odds"]) / implied_sum * total_stake / total_stake
        stake = (1 / o["odds"]) / implied_sum * total_stake
        potential_return = stake * o["odds"]
        
        stakes.append({
            "outcome": o["outcome"],
            "provider": o["provider"],
            "stake": round(stake, 2),
            "return": round(potential_return, 2),
        })
    
    return stakes


def scan_for_arbitrage(
    events_odds: list[dict],
    min_profit_pct: float = 0.5
) -> list[ArbitrageOpportunity]:
    """
    Scan multiple events for arbitrage opportunities.
    
    Args:
        events_odds: [{event_id, market, odds_by_outcome}, ...]
        min_profit_pct: Minimum profit threshold
    
    Returns:
        List of ArbitrageOpportunity
    """
    opportunities = []
    
    for event in events_odds:
        arb = find_arbitrage(
            event_id=event["event_id"],
            market=event["market"],
            odds_by_outcome=event["odds_by_outcome"],
            min_profit_pct=min_profit_pct,
        )
        if arb:
            opportunities.append(arb)
            logger.info(f"Arb found: {arb.event_id} {arb.market} +{arb.profit_pct}%")
    
    return opportunities


# Quick test
if __name__ == "__main__":
    # Example: 1x2 market with arb opportunity
    test_odds = {
        "home": [
            {"provider": "unibet", "odds": 2.50},
            {"provider": "bet365", "odds": 2.40},
        ],
        "draw": [
            {"provider": "unibet", "odds": 3.60},
            {"provider": "bet365", "odds": 3.80},  # Best
        ],
        "away": [
            {"provider": "unibet", "odds": 3.00},
            {"provider": "bet365", "odds": 2.90},
        ],
    }
    
    result = find_arbitrage("test_event", "1x2", test_odds, min_profit_pct=0)
    if result:
        print(f"Arb found! Profit: {result.profit_pct}%")
        for s in result.stakes:
            print(f"  {s['outcome']} @ {s['provider']}: ${s['stake']:.2f} → ${s['return']:.2f}")
    else:
        print("No arb found")
