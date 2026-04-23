"""Extract features for M2 Provider Limit Predictor.

Uses existing BehavioralFeatures from risk/features.py plus new features
for predicting how many bets remain before a provider limits the account.
"""

from src.constants import PLATFORM_MAP


def extract_limit_features(
    stake_entropy: float,
    market_diversity: float,
    timing_regularity: float,
    outcome_correlation: float,
    bonus_usage_ratio: float,
    clv_score: float,
    win_rate_deviation: float,
    total_bets: int,
    account_age_days: int,
    total_turnover: float,
    provider_id: str,
    similar_platform_limits: int,
    max_single_bet_edge: float = 0.0,
    bet_frequency_trend: float = 0.0,
    sport_concentration_top3: float = 0.0,
    has_used_freebet: bool = False,
    avg_stake_vs_provider_median: float = 0.0,
    time_between_bets_cv: float = 0.0,
    time_from_odds_change_to_bet: float = 0.0,
    same_side_as_sharp_movement_pct: float = 0.0,
    deposit_withdrawal_ratio: float = 1.0,
) -> dict:
    return {
        "stake_entropy": stake_entropy,
        "market_diversity": market_diversity,
        "timing_regularity": timing_regularity,
        "outcome_correlation": outcome_correlation,
        "bonus_usage_ratio": bonus_usage_ratio,
        "clv_score": clv_score,
        "win_rate_deviation": win_rate_deviation,
        "total_bets": total_bets,
        "account_age_days": account_age_days,
        "total_turnover": total_turnover,
        "provider_platform": PLATFORM_MAP.get(provider_id, provider_id),
        "similar_platform_limits": similar_platform_limits,
        "max_single_bet_edge": max_single_bet_edge,
        "bet_frequency_trend": bet_frequency_trend,
        "sport_concentration_top3": sport_concentration_top3,
        "has_used_freebet": int(has_used_freebet),
        "avg_stake_vs_provider_median": avg_stake_vs_provider_median,
        "time_between_bets_cv": time_between_bets_cv,
        "time_from_odds_change_to_bet": time_from_odds_change_to_bet,
        "same_side_as_sharp_movement_pct": same_side_as_sharp_movement_pct,
        "deposit_withdrawal_ratio": deposit_withdrawal_ratio,
    }
