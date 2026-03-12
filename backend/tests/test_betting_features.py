"""Test betting feature extraction for M1 Edge Quality model."""
from datetime import datetime, timezone, timedelta


def test_extract_basic_features():
    from src.ml.features.betting_features import extract_betting_features
    features = extract_betting_features(
        edge_pct=7.5, provider_odds=2.10, fair_odds=1.95, fair_probability=0.513,
        provider="betsson", sport="football", market="1x2", event_id="evt-1",
        prob_sum=1.02,
        odds_by_outcome={"home": [
            {"provider": "pinnacle", "odds": 1.95, "updated_at": datetime.now(timezone.utc)},
            {"provider": "betsson", "odds": 2.10, "updated_at": datetime.now(timezone.utc)},
            {"provider": "unibet", "odds": 2.05, "updated_at": datetime.now(timezone.utc)},
        ]},
        pinnacle_overround=0.025,
        event_start_time=datetime.now(timezone.utc) + timedelta(hours=2),
    )
    assert isinstance(features, dict)
    assert features["edge_pct"] == 7.5
    assert features["prob_sum"] == 1.02
    assert abs(features["odds_ratio"] - 2.10 / 1.95) < 0.01
    assert features["sport"] == "football"
    assert features["market_type"] == "1x2"
    assert features["provider_platform"] == "gecko"  # betsson → gecko via PLATFORM_MAP
    assert features["num_providers_with_odds"] >= 2
    assert "time_to_start_minutes" in features
    assert "hour_of_day" in features


def test_extract_provider_odds_rank():
    from src.ml.features.betting_features import extract_betting_features
    features = extract_betting_features(
        edge_pct=5.0, provider_odds=2.20, fair_odds=2.00, fair_probability=0.50,
        provider="betsson", sport="football", market="1x2", event_id="evt-1",
        prob_sum=1.01,
        odds_by_outcome={"home": [
            {"provider": "pinnacle", "odds": 2.00, "updated_at": datetime.now(timezone.utc)},
            {"provider": "betsson", "odds": 2.20, "updated_at": datetime.now(timezone.utc)},
            {"provider": "unibet", "odds": 2.10, "updated_at": datetime.now(timezone.utc)},
            {"provider": "10bet", "odds": 2.05, "updated_at": datetime.now(timezone.utc)},
        ]},
        pinnacle_overround=0.03,
        event_start_time=datetime.now(timezone.utc) + timedelta(hours=3),
    )
    assert features["provider_odds_rank"] == 1
    assert features["num_providers_with_odds"] == 3


def test_extract_handles_missing_start_time():
    from src.ml.features.betting_features import extract_betting_features
    features = extract_betting_features(
        edge_pct=6.0, provider_odds=2.10, fair_odds=1.98, fair_probability=0.505,
        provider="unibet", sport="tennis", market="moneyline", event_id="evt-2",
        prob_sum=1.01,
        odds_by_outcome={"home": [
            {"provider": "pinnacle", "odds": 1.98, "updated_at": datetime.now(timezone.utc)},
            {"provider": "unibet", "odds": 2.10, "updated_at": datetime.now(timezone.utc)},
        ]},
        pinnacle_overround=0.02, event_start_time=None,
    )
    assert features["time_to_start_minutes"] is None
