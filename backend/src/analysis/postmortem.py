"""
Postmortem Classifier — classifies settled bets.

Pure functions that take domain objects and return classification dicts.
No DB access — caller is responsible for storage.
"""

import logging
from datetime import timedelta

logger = logging.getLogger(__name__)

CURRENT_ALGO_VERSION = 1


def classify_bet(bet, profile_bankroll=None) -> dict:
    """
    Classify a settled bet into a postmortem category.

    Args:
        bet: Bet ORM object (must have result in "won"/"lost")
        profile_bankroll: Bankroll for Kelly fraction computation (optional)

    Returns:
        Dict with all BetPostmortem fields (excluding bet_id, computed_at, version).
    """
    # Edge at placement
    edge_at_placement = None
    if bet.odds and bet.fair_odds_at_placement and bet.fair_odds_at_placement > 0:
        edge_at_placement = (bet.odds / bet.fair_odds_at_placement - 1) * 100

    # CLV
    clv_pct = bet.clv_pct

    # CLV confirmed: closing_odds exists AND event started within 12h of placement
    clv_confirmed = False
    if bet.closing_odds is not None and bet.start_time and bet.placed_at:
        try:
            delta = bet.start_time - bet.placed_at
            clv_confirmed = delta <= timedelta(hours=12)
        except Exception:
            pass

    # Expected win probability
    expected_win_pct = None
    if bet.fair_odds_at_placement and bet.fair_odds_at_placement > 0:
        expected_win_pct = 1.0 / bet.fair_odds_at_placement

    # Kelly fraction: actual stake / optimal Kelly stake
    kelly_fraction = None
    is_oversized = False
    is_undersized = False
    if (
        profile_bankroll
        and profile_bankroll > 0
        and bet.odds
        and bet.fair_odds_at_placement
        and bet.fair_odds_at_placement > 0
        and bet.stake
        and bet.stake > 0
    ):
        try:
            from ..bankroll.stake_calculator import calculate_stake

            edge_decimal = bet.odds / bet.fair_odds_at_placement - 1
            if edge_decimal > 0:
                result = calculate_stake(
                    bankroll_total=profile_bankroll,
                    edge_raw=edge_decimal,
                    odds=bet.odds,
                    min_edge=0.0,  # Don't filter — we want optimal stake
                    min_odds=0.0,  # No restriction
                    min_stake=0.0,  # No minimum
                    min_expected_profit=0.0,
                )
                if result.stake > 0:
                    kelly_fraction = bet.stake / result.stake
                    is_oversized = kelly_fraction > 1.5
                    is_undersized = kelly_fraction < 0.5
        except Exception as e:
            logger.debug(f"Kelly computation failed for bet {bet.id}: {e}")

    # Variance score
    variance_score = None
    if expected_win_pct is not None:
        variance_score = 1.0 - expected_win_pct if bet.result == "won" else expected_win_pct

    # Classification
    clv_available = clv_pct is not None
    clv_negative = clv_available and clv_pct < 0
    clv_positive = clv_available and clv_pct >= 0
    edge_value = edge_at_placement if edge_at_placement is not None else 0.0

    if bet.result == "lost":
        if kelly_fraction is not None and kelly_fraction > 1.5:
            classification = "sizing_error"
        elif clv_available and clv_negative and edge_value < 1.0:
            classification = "false_edge"
        elif clv_available and clv_negative and edge_value >= 1.0:
            classification = "edge_erosion"
        elif not clv_available and edge_value < 1.0:
            classification = "false_edge"
        elif clv_positive:
            classification = "expected_loss"
        else:
            classification = "expected_loss"
    else:  # won
        classification = "expected_win" if clv_positive else "bonus_win"

    return {
        "classification": classification,
        "edge_at_placement": edge_at_placement,
        "clv_pct": clv_pct,
        "clv_confirmed": clv_confirmed,
        "expected_win_pct": expected_win_pct,
        "kelly_fraction": kelly_fraction,
        "is_oversized": is_oversized,
        "is_undersized": is_undersized,
        "variance_score": variance_score,
    }
