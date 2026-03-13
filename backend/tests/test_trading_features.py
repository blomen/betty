"""Test trading feature extraction for M5 Setup Score model."""


def test_extract_basic_trading_features():
    from src.ml.features.trading_features import extract_trading_features
    features = extract_trading_features(
        setup_type="spring", direction="long", level_touched="val", base_score=65,
        delta=380, delta_pct=0.089, cvd=12500, cvd_slope_5bar=45.2,
        volume=4250, volume_ratio_vs_20bar=1.45, body_ratio_last=0.42,
        spread_ticks=30, passive_active_ratio=1.8, trapped_magnitude=0.35,
        distance_to_level_ticks=3, distance_to_poc_ticks=15, distance_to_vwap_ticks=-8,
        price_position_in_va=0.72, ib_range_ticks=120, ib_range_vs_avg=0.85,
        minutes_since_rth_open=45, market_type="normal", opening_type="OTD",
    )
    assert isinstance(features, dict)
    assert features["setup_type"] == "spring"
    assert features["direction"] == "long"
    assert features["delta"] == 380
    assert features["delta_pct"] == 0.089
    assert features["distance_to_level_ticks"] == 3
    assert features["minutes_since_rth_open"] == 45


def test_extract_with_defaults():
    from src.ml.features.trading_features import extract_trading_features
    features = extract_trading_features(setup_type="ib_break", direction="short")
    assert features["setup_type"] == "ib_break"
    assert features["delta"] is None
    assert features["distance_to_poc_ticks"] is None
