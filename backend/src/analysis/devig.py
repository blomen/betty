"""
De-vigging Functions

Remove bookmaker margin from odds to get fair/true probabilities.

Methods:
- Multiplicative: Scale all odds proportionally (assumes equal margin per outcome)
- Power: More accurate for favorites/underdogs (Shin method approximation)
- Additive: Simple but less accurate

Most common use: Multiplicative for Pinnacle odds.
"""

from typing import Optional
import logging

logger = logging.getLogger(__name__)


def calculate_margin(odds_list: list[float]) -> float:
    """
    Calculate total margin (overround) from a list of decimal odds.

    Formula: sum(1/odds) - 1

    Examples:
        - [2.0, 2.0] = 0% margin (fair odds)
        - [1.91, 1.91] = 4.7% margin (typical soft book)
        - [1.95, 1.95] = 2.6% margin (typical Pinnacle)

    Args:
        odds_list: List of decimal odds for all outcomes in a market

    Returns:
        Margin as decimal (0.05 = 5% margin)
    """
    if not odds_list or any(o <= 1 for o in odds_list):
        return 0.0

    implied_sum = sum(1 / o for o in odds_list)
    return implied_sum - 1


def devig_multiplicative(odds_list: list[float]) -> list[float]:
    """
    Remove margin using multiplicative method.

    Assumes margin is distributed equally across all outcomes.
    Best for markets where outcomes have similar probabilities.

    Formula: fair_odds = original_odds * (1 + margin)

    Args:
        odds_list: List of decimal odds with margin

    Returns:
        List of fair decimal odds (sum of implied probs = 1.0)

    Example:
        >>> devig_multiplicative([1.91, 1.91])  # 4.7% margin
        [2.0, 2.0]  # Fair 50/50
    """
    if not odds_list or any(o <= 1 for o in odds_list):
        return odds_list

    margin = calculate_margin(odds_list)
    scale = 1 + margin

    return [o * scale for o in odds_list]


def devig_additive(odds_list: list[float]) -> list[float]:
    """
    Remove margin using additive method.

    Distributes margin equally in probability space.
    Simple but less accurate than multiplicative.

    Formula: fair_prob = implied_prob - (margin / n_outcomes)

    Args:
        odds_list: List of decimal odds with margin

    Returns:
        List of fair decimal odds
    """
    if not odds_list or any(o <= 1 for o in odds_list):
        return odds_list

    n = len(odds_list)
    margin = calculate_margin(odds_list)
    margin_per_outcome = margin / n

    fair_odds = []
    for o in odds_list:
        implied_prob = 1 / o
        fair_prob = implied_prob - margin_per_outcome
        if fair_prob <= 0:
            # Edge case: very unlikely outcome, use multiplicative fallback
            return devig_multiplicative(odds_list)
        fair_odds.append(1 / fair_prob)

    return fair_odds


def devig_power(odds_list: list[float]) -> list[float]:
    """
    Remove margin using power method (Shin approximation).

    More accurate for markets with strong favorites/underdogs.
    Accounts for the fact that bookmakers apply more margin to favorites.

    Uses iterative approach to find the power k such that:
    sum((1/odds)^k) = 1

    Args:
        odds_list: List of decimal odds with margin

    Returns:
        List of fair decimal odds
    """
    if not odds_list or any(o <= 1 for o in odds_list):
        return odds_list

    # Binary search for k
    implied_probs = [1 / o for o in odds_list]

    k_low, k_high = 0.5, 2.0
    for _ in range(50):  # Max iterations
        k = (k_low + k_high) / 2
        adjusted_sum = sum(p ** k for p in implied_probs)

        if abs(adjusted_sum - 1.0) < 0.0001:
            break
        elif adjusted_sum > 1.0:
            k_low = k
        else:
            k_high = k

    # Apply power adjustment
    fair_probs = [p ** k for p in implied_probs]
    total = sum(fair_probs)
    fair_probs = [p / total for p in fair_probs]  # Normalize

    return [1 / p if p > 0 else 100.0 for p in fair_probs]


def get_fair_odds_for_outcome(
    outcome: str,
    market_odds: dict[str, float],
    method: str = "multiplicative"
) -> Optional[float]:
    """
    Get fair odds for a specific outcome from a market.

    Args:
        outcome: The outcome to get fair odds for ("home", "away", etc.)
        market_odds: Dict of {outcome: odds} for the full market
        method: De-vig method ("multiplicative", "additive", "power")

    Returns:
        Fair decimal odds for the outcome, or None if not found

    Example:
        >>> market = {"home": 2.10, "draw": 3.40, "away": 3.50}
        >>> get_fair_odds_for_outcome("home", market)
        2.23  # De-vigged fair odds for home win
    """
    if outcome not in market_odds:
        return None

    # Get all odds in consistent order
    outcomes = list(market_odds.keys())
    odds_list = [market_odds[o] for o in outcomes]

    # De-vig
    if method == "additive":
        fair_list = devig_additive(odds_list)
    elif method == "power":
        fair_list = devig_power(odds_list)
    else:
        fair_list = devig_multiplicative(odds_list)

    # Find the outcome's fair odds
    outcome_idx = outcomes.index(outcome)
    return fair_list[outcome_idx]


# Quick test
if __name__ == "__main__":
    print("=== De-vig Examples ===\n")

    # Pinnacle 1x2 odds with ~2.5% margin
    pinnacle_odds = [2.10, 3.40, 3.50]
    margin = calculate_margin(pinnacle_odds)
    print(f"Pinnacle odds: {pinnacle_odds}")
    print(f"Margin: {margin * 100:.1f}%")

    fair_mult = devig_multiplicative(pinnacle_odds)
    print(f"De-vigged (multiplicative): {[round(o, 2) for o in fair_mult]}")

    fair_power = devig_power(pinnacle_odds)
    print(f"De-vigged (power): {[round(o, 2) for o in fair_power]}")

    # Verify fair odds sum to 100%
    fair_sum = sum(1/o for o in fair_mult)
    print(f"Fair implied sum: {fair_sum:.4f} (should be ~1.0)")
