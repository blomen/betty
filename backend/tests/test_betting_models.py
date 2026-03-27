"""Tests for betting ML models (M1-M4, M8)."""
import pytest
import numpy as np
from unittest.mock import MagicMock


# ===== Helpers =====

def _mock_ml_feature(features: dict, outcome_binary: int = 1, outcome: float = 0.05):
    mock = MagicMock()
    mock.features = features
    mock.outcome = outcome
    mock.outcome_binary = outcome_binary
    return mock


def _generate_edge_features():
    return {
        "edge_pct": np.random.uniform(1, 30),
        "prob_sum": np.random.uniform(0.85, 1.1),
        "odds_ratio": np.random.uniform(0.8, 1.4),
        "odds_age_minutes": np.random.uniform(0, 120),
        "sharp_age_minutes": np.random.uniform(0, 60),
        "time_to_start_minutes": np.random.uniform(30, 2880),
        "pinnacle_overround": np.random.uniform(0.02, 0.08),
        "num_providers_with_odds": np.random.randint(1, 10),
        "provider_odds_rank": np.random.randint(1, 5),
        "market_consensus_spread": np.random.uniform(0, 0.5),
        "hour_of_day": np.random.randint(0, 24),
        "day_of_week": np.random.randint(0, 7),
        "sport": np.random.randint(0, 10),
        "market_type": np.random.randint(0, 4),
        "point": np.random.uniform(-5, 5),
    }


# ===== M1: Edge Quality =====

def test_edge_quality_feature_names():
    from src.ml.models.edge_quality import EdgeQualityModel
    model = EdgeQualityModel()
    assert "edge_pct" in model.feature_names
    assert "prob_sum" in model.feature_names


def test_edge_quality_train_insufficient_data():
    from src.ml.models.edge_quality import EdgeQualityModel
    model = EdgeQualityModel()
    data = [_mock_ml_feature(_generate_edge_features(), outcome_binary=1) for _ in range(10)]
    result = model.train(data)
    assert result is None


def test_edge_quality_train_sufficient_data():
    from src.ml.models.edge_quality import EdgeQualityModel
    model = EdgeQualityModel()
    data = []
    for i in range(250):
        outcome = 1 if np.random.random() > 0.4 else 0
        data.append(_mock_ml_feature(_generate_edge_features(), outcome_binary=outcome))
    result = model.train(data)
    assert result is not None
    assert "model" in result
    assert "file_path" in result
    assert result["training_data_count"] == 250


def test_edge_quality_predict():
    from src.ml.models.edge_quality import EdgeQualityModel
    model = EdgeQualityModel()
    data = []
    for i in range(250):
        outcome = 1 if np.random.random() > 0.4 else 0
        data.append(_mock_ml_feature(_generate_edge_features(), outcome_binary=outcome))
    model.train(data)
    prob = model.predict(_generate_edge_features())
    assert prob is not None
    assert 0 <= prob <= 1


# ===== M2: Limit Predictor =====

def test_limit_features_extraction():
    from src.ml.features.limit_features import extract_limit_features
    features = extract_limit_features(
        stake_entropy=0.3, market_diversity=0.4,
        timing_regularity=0.5, outcome_correlation=0.2,
        bonus_usage_ratio=0.1, clv_score=0.6, win_rate_deviation=0.3,
        total_bets=50, account_age_days=90,
        total_turnover=5000, provider_id="betsson",
        similar_platform_limits=0,
    )
    assert "clv_score" in features
    assert "total_bets" in features
    assert "provider_platform" in features
    assert features["total_bets"] == 50


def test_limit_predictor_low_data_logistic():
    from src.ml.models.limit_predictor import LimitPredictorModel
    model = LimitPredictorModel()
    data = []
    for i in range(25):
        features = {
            "clv_score": np.random.uniform(0, 1),
            "total_bets": np.random.randint(10, 200),
            "max_single_bet_edge": np.random.uniform(0, 30),
            "stake_entropy": np.random.uniform(0, 1),
            "similar_platform_limits": np.random.randint(0, 3),
        }
        data.append(_mock_ml_feature(features, outcome_binary=1 if np.random.random() > 0.7 else 0))
    result = model.train(data)
    assert result is not None
    assert result.get("algorithm") == "logistic_regression"


def test_limit_predictor_high_data_lgbm():
    from src.ml.models.limit_predictor import LimitPredictorModel
    model = LimitPredictorModel()
    data = []
    for i in range(60):
        features = {
            "clv_score": np.random.uniform(0, 1),
            "total_bets": np.random.randint(10, 200),
            "max_single_bet_edge": np.random.uniform(0, 30),
            "stake_entropy": np.random.uniform(0, 1),
            "market_diversity": np.random.uniform(0, 1),
            "timing_regularity": np.random.uniform(0, 1),
            "outcome_correlation": np.random.uniform(0, 1),
            "bonus_usage_ratio": np.random.uniform(0, 1),
            "win_rate_deviation": np.random.uniform(0, 1),
            "account_age_days": np.random.randint(1, 365),
            "total_turnover": np.random.uniform(100, 50000),
            "similar_platform_limits": np.random.randint(0, 5),
            "bet_frequency_trend": np.random.uniform(-1, 1),
            "sport_concentration_top3": np.random.uniform(0.3, 1.0),
            "has_used_freebet": np.random.randint(0, 2),
            "avg_stake_vs_provider_median": np.random.uniform(0.5, 3.0),
            "time_between_bets_cv": np.random.uniform(0, 2),
            "time_from_odds_change_to_bet": np.random.uniform(0, 60),
            "same_side_as_sharp_movement_pct": np.random.uniform(0, 1),
            "deposit_withdrawal_ratio": np.random.uniform(0.5, 5.0),
        }
        data.append(_mock_ml_feature(features, outcome_binary=1 if np.random.random() > 0.7 else 0))
    result = model.train(data)
    assert result is not None
    assert result.get("algorithm") == "lightgbm"



# ===== M4: Boost Calibrator =====

def test_boost_features_extraction():
    from src.ml.features.boost_features import extract_boost_features
    features = extract_boost_features(
        llm_raw_probability=0.45, llm_confidence=3,
        boost_type="single", sport="football", league="premier_league",
        num_legs=1, has_pinnacle_match=True,
        pinnacle_implied_prob=0.42, original_odds=2.20,
        boosted_odds=2.80, provider="betsson",
        hours_to_event=5.0, llm_reasoning_length=500,
    )
    assert features["llm_raw_probability"] == 0.45
    assert abs(features["boost_margin"] - (2.80 - 2.20) / 2.20) < 0.01
    assert features["has_pinnacle_match"] == 1
    assert features["keyword_anytime_scorer"] == 0


def test_boost_calibrator_train():
    from src.ml.models.boost_calibrator import BoostCalibratorModel
    model = BoostCalibratorModel()
    data = []
    for i in range(120):
        llm_prob = np.random.uniform(0.1, 0.9)
        features = {
            "llm_raw_probability": llm_prob,
            "llm_confidence": np.random.randint(1, 6),
            "boost_type_single": np.random.randint(0, 2),
            "boost_type_combo": 0, "sport": np.random.randint(0, 5),
            "num_legs": 1, "has_pinnacle_match": np.random.randint(0, 2),
            "pinnacle_implied_prob": np.random.uniform(0.2, 0.8),
            "legs_matched_ratio": np.random.uniform(0, 1),
            "original_odds": np.random.uniform(1.5, 5.0),
            "boosted_odds": np.random.uniform(2.0, 7.0),
            "boost_margin": np.random.uniform(0.1, 0.5),
            "hours_to_event": np.random.uniform(1, 48),
            "llm_reasoning_length": np.random.randint(100, 2000),
            "brave_results_count": np.random.randint(0, 20),
            "keyword_anytime_scorer": 0, "keyword_both_teams": 0,
            "keyword_over": 0, "day_of_week": np.random.randint(0, 7),
        }
        outcome = 1 if np.random.random() < llm_prob * 0.8 else 0
        data.append(_mock_ml_feature(features, outcome_binary=outcome))
    result = model.train(data)
    assert result is not None


def test_boost_calibrator_predict():
    from src.ml.models.boost_calibrator import BoostCalibratorModel
    model = BoostCalibratorModel()
    data = []
    for i in range(120):
        llm_prob = np.random.uniform(0.1, 0.9)
        features = {
            "llm_raw_probability": llm_prob, "llm_confidence": 3,
            "boost_type_single": 1, "boost_type_combo": 0, "sport": 0,
            "num_legs": 1, "has_pinnacle_match": 1,
            "pinnacle_implied_prob": 0.4, "legs_matched_ratio": 1.0,
            "original_odds": 2.5, "boosted_odds": 3.0, "boost_margin": 0.2,
            "hours_to_event": 5, "llm_reasoning_length": 500,
            "brave_results_count": 10,
            "keyword_anytime_scorer": 0, "keyword_both_teams": 0,
            "keyword_over": 0, "day_of_week": 3,
        }
        outcome = 1 if np.random.random() < llm_prob else 0
        data.append(_mock_ml_feature(features, outcome_binary=outcome))
    model.train(data)
    prob = model.predict({
        "llm_raw_probability": 0.5, "llm_confidence": 3,
        "boost_type_single": 1, "boost_type_combo": 0, "sport": 0,
        "num_legs": 1, "has_pinnacle_match": 1,
        "pinnacle_implied_prob": 0.4, "legs_matched_ratio": 1.0,
        "original_odds": 2.5, "boosted_odds": 3.0, "boost_margin": 0.2,
        "hours_to_event": 5, "llm_reasoning_length": 500,
        "brave_results_count": 10,
        "keyword_anytime_scorer": 0, "keyword_both_teams": 0,
        "keyword_over": 0, "day_of_week": 3,
    })
    assert prob is not None
    assert 0 <= prob <= 1


# ===== M8: Adaptive Kelly =====

def test_kelly_features_extraction():
    from src.ml.features.kelly_features import extract_kelly_features
    features = extract_kelly_features(
        domain="betting", model_confidence=0.75, predicted_edge=8.0,
        historical_win_rate=0.55, historical_avg_return=0.03,
        recent_drawdown_pct=2.5, consecutive_wins=3, consecutive_losses=0,
        daily_pnl=150.0, weekly_pnl=500.0, account_utilization=0.4,
        volatility_regime=0.5,
    )
    assert features["model_confidence"] == 0.75
    assert features["predicted_edge"] == 8.0
    assert features["domain_betting"] == 1
    assert features["domain_trading"] == 0
    assert features["time_of_day"] == 12  # default


def test_adaptive_kelly_train():
    from src.ml.models.adaptive_kelly import AdaptiveKellyModel
    model = AdaptiveKellyModel()
    data = []
    for i in range(350):
        features = {
            "domain_betting": 1, "domain_trading": 0,
            "model_confidence": np.random.uniform(0.5, 1.0),
            "predicted_edge": np.random.uniform(1, 20),
            "historical_win_rate": np.random.uniform(0.4, 0.65),
            "historical_avg_return": np.random.uniform(-0.05, 0.1),
            "recent_drawdown_pct": np.random.uniform(0, 15),
            "consecutive_wins": np.random.randint(0, 10),
            "consecutive_losses": np.random.randint(0, 5),
            "daily_pnl": np.random.uniform(-500, 500),
            "weekly_pnl": np.random.uniform(-2000, 2000),
            "account_utilization": np.random.uniform(0, 1),
            "volatility_regime": np.random.uniform(0, 1),
            "time_of_day": np.random.randint(0, 24),
            "provider_remaining_lifetime": np.random.uniform(0, 200),
            "is_freebet": np.random.randint(0, 2),
            "bonus_wagering_remaining": np.random.uniform(0, 5000),
            "gex": 0.0, "correlation_with_open": 0.0,
            "session_volume_regime": 1.0,
        }
        outcome = np.clip(np.random.uniform(0.05, 0.5), 0, 1)
        data.append(_mock_ml_feature(features, outcome_binary=1, outcome=outcome))
    result = model.train(data)
    assert result is not None


def test_adaptive_kelly_predict():
    from src.ml.models.adaptive_kelly import AdaptiveKellyModel
    model = AdaptiveKellyModel()
    data = []
    for i in range(350):
        features = {
            "domain_betting": 1, "domain_trading": 0,
            "model_confidence": np.random.uniform(0.5, 1.0),
            "predicted_edge": np.random.uniform(1, 20),
            "historical_win_rate": np.random.uniform(0.4, 0.65),
            "historical_avg_return": np.random.uniform(-0.05, 0.1),
            "recent_drawdown_pct": np.random.uniform(0, 15),
            "consecutive_wins": np.random.randint(0, 10),
            "consecutive_losses": np.random.randint(0, 5),
            "daily_pnl": np.random.uniform(-500, 500),
            "weekly_pnl": np.random.uniform(-2000, 2000),
            "account_utilization": np.random.uniform(0, 1),
            "volatility_regime": np.random.uniform(0, 1),
            "time_of_day": np.random.randint(0, 24),
            "provider_remaining_lifetime": 0.0,
            "is_freebet": 0, "bonus_wagering_remaining": 0.0,
            "gex": 0.0, "correlation_with_open": 0.0,
            "session_volume_regime": 1.0,
        }
        outcome = np.clip(np.random.uniform(0.05, 0.5), 0, 1)
        data.append(_mock_ml_feature(features, outcome_binary=1, outcome=outcome))
    model.train(data)
    kelly = model.predict({
        "domain_betting": 1, "domain_trading": 0,
        "model_confidence": 0.8, "predicted_edge": 10.0,
        "historical_win_rate": 0.55, "historical_avg_return": 0.04,
        "recent_drawdown_pct": 3.0, "consecutive_wins": 2,
        "consecutive_losses": 0, "daily_pnl": 100.0,
        "weekly_pnl": 400.0, "account_utilization": 0.3,
        "volatility_regime": 0.5, "time_of_day": 14,
        "provider_remaining_lifetime": 100.0,
        "is_freebet": 0, "bonus_wagering_remaining": 0.0,
        "gex": 0.0, "correlation_with_open": 0.0,
        "session_volume_regime": 1.0,
    })
    assert kelly is not None
    assert 0 <= kelly <= 1
