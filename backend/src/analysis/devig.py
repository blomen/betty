"""
De-vigging Functions

Remove bookmaker margin from odds to get fair/true probabilities.

Methods:
- Multiplicative: Scale all odds proportionally (assumes equal margin per outcome)
- Power: More accurate for favorites/underdogs (Shin method approximation)
- Additive: Simple but less accurate

Most common use: Multiplicative for Pinnacle odds.
"""

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
        adjusted_sum = sum(p**k for p in implied_probs)

        if abs(adjusted_sum - 1.0) < 0.0001:
            break
        elif adjusted_sum > 1.0:
            k_low = k
        else:
            k_high = k

    # Apply power adjustment
    fair_probs = [p**k for p in implied_probs]
    total = sum(fair_probs)
    fair_probs = [p / total for p in fair_probs]  # Normalize

    return [1 / p if p > 0 else 100.0 for p in fair_probs]


def get_fair_odds_for_outcome(
    outcome: str, market_odds: dict[str, float], method: str = "multiplicative"
) -> float | None:
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

    # Rules-based method selection: power for 3-way markets (1x2),
    # multiplicative for 2-way (totals, spreads, moneyline)
    if method == "multiplicative" and len(odds_list) >= 3:
        method = "power"

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


def compute_consensus_fair_odds(
    outcome: str,
    odds_by_outcome: dict[str, list[dict]],
    platform_map: dict[str, str],
    sharp_providers: set[str] = frozenset({"pinnacle"}),
    min_platforms: int = 5,
) -> tuple[float, int] | None:
    """
    Compute fair odds from platform-weighted harmonic mean of non-sharp books.

    Each platform contributes ONE devigged odds value (average if multiple
    providers on same platform). Then harmonic mean across platforms.

    Prediction markets (Polymarket, Kalshi) participate — for the reverse-
    value question ("where does the broader market price this vs Pinnacle?")
    sharper non-Pinnacle inputs only strengthen the consensus, and
    excluding them was a stale assumption from when this was only used as
    a "sportsbook consensus" baseline. PREDICTION_MARKETS is no longer
    imported here.

    Args:
        outcome: The outcome to get consensus for ("home", "away", etc.)
        odds_by_outcome: {outcome: [{provider, odds}, ...]}
        platform_map: {provider_id: platform_name}
        sharp_providers: Providers to exclude (Pinnacle is the bet provider
            in reverse_value; never let it contribute to its own "fair").
        min_platforms: Minimum independent platforms required

    Returns:
        (consensus_fair_odds, n_platforms) or None if insufficient data
    """
    all_outcomes = list(odds_by_outcome.keys())
    if len(all_outcomes) < 2:
        return None

    # Build per-provider full markets (need all outcomes to devig)
    provider_markets: dict[str, dict[str, float]] = {}
    for out, providers in odds_by_outcome.items():
        for p in providers:
            pid = p["provider"]
            if pid in sharp_providers:
                continue  # Bet provider can't contribute to its own "fair" baseline
            if pid not in provider_markets:
                provider_markets[pid] = {}
            provider_markets[pid][out] = p["odds"]

    # Devig each provider that has full market coverage, group by platform
    n_outcomes = len(all_outcomes)
    platform_devigged: dict[str, list[float]] = {}
    for pid, p_market in provider_markets.items():
        if len(p_market) != n_outcomes:
            continue  # Incomplete market, can't devig

        p_odds_list = [p_market[o] for o in all_outcomes]
        if any(o <= 1 for o in p_odds_list):
            continue

        # Power for 3-way, multiplicative for 2-way
        if n_outcomes >= 3:
            fair_list = devig_power(p_odds_list)
            outcome_idx = all_outcomes.index(outcome)
            fair = fair_list[outcome_idx]
        else:
            margin = sum(1.0 / o for o in p_odds_list) - 1
            scale = 1 + margin
            fair = p_market[outcome] * scale

        if fair <= 1:
            continue

        platform = platform_map.get(pid, pid)
        if platform not in platform_devigged:
            platform_devigged[platform] = []
        platform_devigged[platform].append(fair)

    if len(platform_devigged) < min_platforms:
        return None

    # One value per platform (average within platform)
    platform_values = []
    for values in platform_devigged.values():
        platform_values.append(sum(values) / len(values))

    # Harmonic mean
    n = len(platform_values)
    hm = n / sum(1.0 / v for v in platform_values)

    return (hm, n)
