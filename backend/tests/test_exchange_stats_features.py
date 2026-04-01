"""Tests for exchange statistics feature extraction."""
import numpy as np
from src.rl.features.exchange_stats_features import extract_exchange_stats_features, _N_FEATURES


def test_none_returns_zeros():
    result = extract_exchange_stats_features(None, price=19000.0)
    assert result.shape == (_N_FEATURES,)
    assert result.dtype == np.float32
    assert (result == 0.0).all()


def test_empty_dict_returns_zeros():
    result = extract_exchange_stats_features({}, price=19000.0)
    assert result.shape == (_N_FEATURES,)
    assert (result == 0.0).all()


def test_full_stats():
    macro = {
        "oi": 250_000,
        "oi_change": 10_000,
        "settlement_price": 19050.0,
        "cleared_volume": 400_000,
        "block_volume": 20_000,
    }
    result = extract_exchange_stats_features(macro, price=19000.0)
    assert result.shape == (_N_FEATURES,)
    # oi_norm = 250000 / 1_000_000 = 0.25
    assert abs(result[0] - 0.25) < 1e-5
    # oi_change_norm = 10000 / 50000 = 0.2
    assert abs(result[1] - 0.2) < 1e-5
    # settlement_dist = (19000 - 19050) / (0.25 * 200) = -50 / 50 = -1.0
    assert abs(result[2] - (-1.0)) < 1e-5
    # cleared_vol_norm = 400000 / 500000 = 0.8
    assert abs(result[3] - 0.8) < 1e-5
    # block_vol_ratio = 20000 / 400000 = 0.05
    assert abs(result[4] - 0.05) < 1e-5


def test_clipping():
    macro = {
        "oi": 2_000_000,  # > 1M → clipped to 1.0
        "oi_change": 100_000,  # > 50k → clipped to 1.0
        "settlement_price": 18000.0,  # 1000pts away → clipped
        "cleared_volume": 1_000_000,  # > 500k → clipped to 1.0
        "block_volume": 1_100_000,  # ratio > 1 → clipped to 1.0
    }
    result = extract_exchange_stats_features(macro, price=19000.0)
    assert result[0] == 1.0  # oi clipped
    assert result[1] == 1.0  # oi_change clipped
    assert abs(result[2]) == 1.0  # settlement clipped
    assert result[3] == 1.0  # cleared_vol clipped
    assert result[4] == 1.0  # block_vol clipped
