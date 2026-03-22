"""Test boost feature extraction after dead feature removal."""


def test_extract_boost_features_returns_17_features():
    from src.ml.features.boost_features import extract_boost_features
    features = extract_boost_features(
        llm_raw_probability=0.45,
        llm_confidence=1,
        boost_type="single",
        sport="football",
        league="Premier League",
        num_legs=1,
        has_pinnacle_match=True,
        pinnacle_implied_prob=0.40,
        original_odds=2.50,
        boosted_odds=3.00,
        provider="unibet",
    )
    assert len(features) == 17
    assert "brave_results_count" not in features
    assert "legs_matched_ratio" not in features
    assert "llm_raw_probability" in features
    assert features["boost_margin"] == (3.00 - 2.50) / 2.50


def test_feature_names_match_extraction():
    """FEATURE_NAMES in calibrator must match keys from extract_boost_features."""
    from src.ml.features.boost_features import extract_boost_features
    from src.ml.models.boost_calibrator import FEATURE_NAMES
    features = extract_boost_features(
        llm_raw_probability=0.5, llm_confidence=1,
        boost_type="single", sport="football", league="",
        num_legs=1, has_pinnacle_match=False, pinnacle_implied_prob=None,
        original_odds=2.0, boosted_odds=2.5, provider="test",
    )
    assert set(FEATURE_NAMES) == set(features.keys())
    assert len(FEATURE_NAMES) == 17
