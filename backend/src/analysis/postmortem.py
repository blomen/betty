"""
Postmortem Classifier — classifies settled bets and closed trades.

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

            edge_decimal = (bet.odds / bet.fair_odds_at_placement - 1)
            if edge_decimal > 0:
                result = calculate_stake(
                    bankroll_total=profile_bankroll,
                    edge_raw=edge_decimal,
                    odds=bet.odds,
                    min_edge=0.0,       # Don't filter — we want optimal stake
                    min_odds=0.0,       # No restriction
                    min_stake=0.0,      # No minimum
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
        if bet.result == "won":
            variance_score = 1.0 - expected_win_pct
        else:
            variance_score = expected_win_pct

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
        if clv_positive:
            classification = "expected_win"
        else:
            classification = "bonus_win"

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


def classify_trade(trade, all_trades_for_setup, streak_position, routine=None, trade_events=None) -> dict:
    """
    Classify a closed trade into a postmortem category.

    Args:
        trade: Trade ORM object (must have state in "closed"/"reviewed")
        all_trades_for_setup: List of Trade objects with same setup_type + account
        streak_position: Consecutive loss streak count (negative = losing)
        routine: DailyRoutine object (optional)
        trade_events: List of TradeEvent objects for this trade (optional)

    Returns:
        Dict with all TradePostmortem fields (excluding trade_id, computed_at, version).
    """
    r_multiple = trade.r_multiple if trade.r_multiple is not None else 0.0

    # Setup stats from peers (excluding current trade)
    closed_peers = [
        t for t in (all_trades_for_setup or [])
        if t.id != trade.id
        and t.state in ("closed", "reviewed")
        and t.r_multiple is not None
    ]

    setup_avg_r = None
    setup_win_rate = None
    if closed_peers:
        setup_avg_r = sum(t.r_multiple for t in closed_peers) / len(closed_peers)
        wins = sum(1 for t in closed_peers if t.r_multiple > 0)
        setup_win_rate = wins / len(closed_peers)

    # Stop quality: check if stop was widened via trade events
    stop_widened = False
    if trade_events:
        for ev in trade_events:
            if ev.event_type == "trail_stop" and ev.details:
                details = ev.details if isinstance(ev.details, dict) else {}
                old_stop = details.get("old_stop")
                new_stop = details.get("new_stop")
                if old_stop is not None and new_stop is not None and trade.entry_price is not None:
                    # Stop is "widened" if new_stop is farther from entry than old_stop
                    old_dist = abs(trade.entry_price - old_stop)
                    new_dist = abs(trade.entry_price - new_stop)
                    if new_dist > old_dist:
                        stop_widened = True
                        break

    stop_quality = "too_wide" if (stop_widened and r_multiple < -1.0) else "optimal"

    # Target quality
    target_quality = None
    if r_multiple > 0:
        target_quality = "hit_target" if r_multiple >= 2.0 else "partial_exit_good"

    # Routine psych average
    routine_psych_avg = None
    if routine and routine.psych_average is not None:
        routine_psych_avg = routine.psych_average

    # Rules followed
    rules_followed = None
    if trade.review and trade.review.followed_rules is not None:
        rules_followed = trade.review.followed_rules

    # Classification
    if r_multiple < 0:
        if r_multiple < -1.0 and stop_widened:
            classification = "stop_too_wide"
        elif setup_avg_r is not None and setup_avg_r < 0 and len(closed_peers) >= 5:
            classification = "thesis_invalid"
        else:
            classification = "expected_loss"
    else:
        if r_multiple >= 2.0:
            classification = "runner"
        else:
            classification = "expected_win"

    return {
        "classification": classification,
        "r_multiple": r_multiple,
        "setup_avg_r": setup_avg_r,
        "setup_win_rate": setup_win_rate,
        "stop_quality": stop_quality,
        "target_quality": target_quality,
        "streak_position": streak_position,
        "routine_psych_avg": routine_psych_avg,
        "rules_followed": rules_followed,
    }
