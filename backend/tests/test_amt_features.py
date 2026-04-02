"""Tests for AMT feature extraction (Dalton day types, opening types, VA scalars)."""
import numpy as np
import pytest

from src.rl.features.amt_features import extract_amt_features
from src.market_data.levels import SessionLevels, VolumeProfile, VolumeProfileLevel


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_session_levels(
    ib_high: float = 19100.0,
    ib_low: float = 19000.0,
    pdh: float | None = 19200.0,
    pdl: float | None = 18900.0,
) -> SessionLevels:
    sl = SessionLevels()
    sl.ib_high = ib_high
    sl.ib_low = ib_low
    sl.pdh = pdh
    sl.pdl = pdl
    return sl


def _make_vp(poc: float, vah: float, val: float) -> VolumeProfile:
    return VolumeProfile(poc=poc, vah=vah, val=val)


def _make_ctx(
    daily_high: float | None = None,
    daily_low: float | None = None,
    daily_range_pct: float | None = None,
    open_price: float | None = None,
    prior_vah: float | None = None,
    prior_val: float | None = None,
    prior_poc: float | None = None,
) -> dict:
    ctx: dict = {}
    if daily_high is not None:
        ctx["daily_high"] = daily_high
    if daily_low is not None:
        ctx["daily_low"] = daily_low
    if daily_range_pct is not None:
        ctx["daily_range_pct"] = daily_range_pct
    if open_price is not None:
        ctx["open_price"] = open_price
    if prior_vah is not None:
        ctx["prior_vah"] = prior_vah
    if prior_val is not None:
        ctx["prior_val"] = prior_val
    if prior_poc is not None:
        ctx["prior_poc"] = prior_poc
    return ctx


# ---------------------------------------------------------------------------
# Shape / null safety
# ---------------------------------------------------------------------------

def test_amt_features_shape_all_none():
    feats = extract_amt_features(None, None, None, 19000.0)
    assert feats.shape == (20,)
    assert np.all(feats == 0.0)


def test_amt_features_zeros_on_missing_ib():
    """No IB levels available → return zeros gracefully."""
    sl = SessionLevels()  # ib_high / ib_low both None
    feats = extract_amt_features(sl, None, {}, 19000.0)
    assert feats.shape == (20,)
    assert np.all(feats == 0.0)


def test_amt_features_dtype():
    feats = extract_amt_features(None, None, None, 19000.0)
    assert feats.dtype == np.float32


# ---------------------------------------------------------------------------
# Dalton day types
# ---------------------------------------------------------------------------

def test_non_trend_day():
    """IB range 100, daily range 70 → range_ratio 0.7 → non-trend (idx 0)."""
    sl = _make_session_levels(ib_high=19100.0, ib_low=19000.0)
    ctx = _make_ctx(daily_high=19080.0, daily_low=19010.0)
    feats = extract_amt_features(sl, None, ctx, 19050.0)
    assert feats[0] == 1.0  # non_trend
    assert feats[1:6].sum() == 0.0


def test_normal_day():
    """IB range 100, daily range 110 → range_ratio 1.1 → normal (idx 1)."""
    sl = _make_session_levels(ib_high=19100.0, ib_low=19000.0)
    ctx = _make_ctx(daily_high=19110.0, daily_low=19000.0)
    feats = extract_amt_features(sl, None, ctx, 19050.0)
    assert feats[1] == 1.0  # normal
    assert feats[0] == 0.0
    assert feats[2:6].sum() == 0.0


def test_neutral_day():
    """IB range 100, daily range 150, both sides extend ~25 each → neutral (idx 2)."""
    # ib_high=19100, ib_low=19000
    # daily_high=19125 (ext_up=25), daily_low=18975 (ext_down=25)
    # range_ratio=1.5, imbalance = 0/25 = 0 < 0.2 → neutral
    sl = _make_session_levels(ib_high=19100.0, ib_low=19000.0)
    ctx = _make_ctx(daily_high=19125.0, daily_low=18975.0)
    feats = extract_amt_features(sl, None, ctx, 19050.0)
    assert feats[2] == 1.0  # neutral
    assert feats[0] == 0.0
    assert feats[1] == 0.0


def test_normal_variation_day():
    """IB range 100, daily range 160, ext_up=90, ext_down=10 → normal_variation (idx 3)."""
    # range_ratio=1.6 (1.25-2.0), imbalance=(90-10)/90=0.89 > 0.2 → normal_variation
    sl = _make_session_levels(ib_high=19100.0, ib_low=19000.0)
    ctx = _make_ctx(daily_high=19190.0, daily_low=18990.0)
    feats = extract_amt_features(sl, None, ctx, 19050.0)
    assert feats[3] == 1.0  # normal_variation


def test_trend_day():
    """IB range 100, daily range 250, ext_up=200, ext_down=0 → trend (idx 4)."""
    sl = _make_session_levels(ib_high=19100.0, ib_low=19000.0)
    ctx = _make_ctx(daily_high=19300.0, daily_low=19050.0)
    feats = extract_amt_features(sl, None, ctx, 19200.0)
    assert feats[4] == 1.0  # trend


def test_double_distribution_day():
    """IB range 100, daily range 300, ext_up=150, ext_down=50 → double_dist (idx 5)."""
    # range_ratio=3.0 > 2.0, ext_up/ext_down = 150/50 = 3.0, not > 3x → double dist
    sl = _make_session_levels(ib_high=19100.0, ib_low=19000.0)
    ctx = _make_ctx(daily_high=19250.0, daily_low=18950.0)
    feats = extract_amt_features(sl, None, ctx, 19100.0)
    assert feats[5] == 1.0  # double_distribution


# ---------------------------------------------------------------------------
# Opening types
# ---------------------------------------------------------------------------

def test_opening_od_near_ib_extreme():
    """Open near IB high (open_vs_ib > 0.9) inside prior VA → OD (idx 6)."""
    sl = _make_session_levels(ib_high=19100.0, ib_low=19000.0, pdh=19200.0, pdl=18900.0)
    ctx = _make_ctx(
        daily_high=19120.0, daily_low=18980.0,
        open_price=19095.0,  # open_vs_ib = 95/100 = 0.95 > 0.9
    )
    feats = extract_amt_features(sl, None, ctx, 19095.0)
    assert feats[6] == 1.0  # OD


def test_opening_otd():
    """Open in middle 50% of IB, inside prior VA → OTD (idx 7)."""
    sl = _make_session_levels(ib_high=19100.0, ib_low=19000.0, pdh=19200.0, pdl=18900.0)
    ctx = _make_ctx(
        daily_high=19160.0, daily_low=19000.0,
        open_price=19050.0,  # open_vs_ib = 50/100 = 0.5 → in 0.25-0.75
    )
    feats = extract_amt_features(sl, None, ctx, 19050.0)
    assert feats[7] == 1.0  # OTD


def test_opening_orr():
    """Open outside prior VA, IB mid comes back inside → ORR (idx 8)."""
    # prior VA: 18950-19000. Open at 19050 (above pdh=19000 → outside VA).
    # IB: 19010-19060. ib_mid=19035, which is inside prior VA? No, 19035 > 19000.
    # Let's use: prior VA 19020-19080, open at 19000 (below val → outside VA).
    # IB: 19010-19090. ib_mid=19050, inside prior VA.
    sl = _make_session_levels(ib_high=19090.0, ib_low=19010.0, pdh=None, pdl=None)
    ctx = _make_ctx(
        daily_high=19120.0, daily_low=18980.0,
        open_price=19000.0,  # outside prior VA (below prior_val=19020)
        prior_vah=19080.0,
        prior_val=19020.0,
    )
    feats = extract_amt_features(sl, None, ctx, 19050.0)
    assert feats[8] == 1.0  # ORR


def test_opening_oa():
    """Open in middle of IB, neither extreme → OA (idx 9)."""
    sl = _make_session_levels(ib_high=19100.0, ib_low=19000.0, pdh=19200.0, pdl=18900.0)
    ctx = _make_ctx(
        daily_high=19120.0, daily_low=18980.0,
        open_price=19015.0,  # open_vs_ib = 0.15 → not OD (>0.1), not OTD (< 0.25)
    )
    feats = extract_amt_features(sl, None, ctx, 19015.0)
    assert feats[9] == 1.0  # OA


# ---------------------------------------------------------------------------
# Scalar features
# ---------------------------------------------------------------------------

def test_range_extension_zero_when_daily_inside_ib():
    """Daily range inside IB → range_extension = 0."""
    sl = _make_session_levels(ib_high=19100.0, ib_low=19000.0)
    ctx = _make_ctx(daily_high=19090.0, daily_low=19010.0)
    feats = extract_amt_features(sl, None, ctx, 19050.0)
    assert feats[10] == pytest.approx(0.0)


def test_range_extension_capped_at_one():
    """Daily range much larger than IB → range_extension clipped to 1.0."""
    sl = _make_session_levels(ib_high=19100.0, ib_low=19000.0)
    ctx = _make_ctx(daily_high=19500.0, daily_low=18700.0)
    feats = extract_amt_features(sl, None, ctx, 19100.0)
    assert feats[10] == pytest.approx(1.0)


def test_value_migration_above():
    """POC above prior VAH → value_migration = +1."""
    sl = _make_session_levels(ib_high=19100.0, ib_low=19000.0, pdh=19200.0, pdl=18900.0)
    vp = _make_vp(poc=19250.0, vah=19300.0, val=19200.0)
    ctx = _make_ctx(
        daily_high=19300.0, daily_low=19000.0,
        prior_vah=19200.0,
        prior_val=19100.0,
    )
    feats = extract_amt_features(sl, vp, ctx, 19250.0)
    assert feats[12] == pytest.approx(1.0)


def test_value_migration_below():
    """POC below prior VAL → value_migration = -1."""
    sl = _make_session_levels(ib_high=19100.0, ib_low=19000.0)
    vp = _make_vp(poc=18800.0, vah=18850.0, val=18750.0)
    ctx = _make_ctx(
        daily_high=19100.0, daily_low=18750.0,
        prior_vah=19200.0,
        prior_val=18900.0,
    )
    feats = extract_amt_features(sl, vp, ctx, 18800.0)
    assert feats[12] == pytest.approx(-1.0)


def test_value_migration_inside():
    """POC inside prior VA → value_migration = 0."""
    sl = _make_session_levels(ib_high=19100.0, ib_low=19000.0)
    vp = _make_vp(poc=19050.0, vah=19100.0, val=19000.0)
    ctx = _make_ctx(
        daily_high=19150.0, daily_low=18950.0,
        prior_vah=19200.0,
        prior_val=18900.0,
    )
    feats = extract_amt_features(sl, vp, ctx, 19050.0)
    assert feats[12] == pytest.approx(0.0)


def test_va_overlap_full():
    """Identical VA ranges → overlap = 1."""
    sl = _make_session_levels(ib_high=19100.0, ib_low=19000.0)
    vp = _make_vp(poc=19050.0, vah=19200.0, val=19000.0)
    ctx = _make_ctx(
        daily_high=19200.0, daily_low=18900.0,
        prior_vah=19200.0,
        prior_val=19000.0,
    )
    feats = extract_amt_features(sl, vp, ctx, 19050.0)
    assert feats[11] == pytest.approx(1.0)


def test_va_overlap_none():
    """Non-overlapping VA ranges → overlap = 0."""
    sl = _make_session_levels(ib_high=19100.0, ib_low=19000.0)
    vp = _make_vp(poc=18700.0, vah=18800.0, val=18600.0)
    ctx = _make_ctx(
        daily_high=19100.0, daily_low=18600.0,
        prior_vah=19200.0,
        prior_val=19000.0,
    )
    feats = extract_amt_features(sl, vp, ctx, 18700.0)
    assert feats[11] == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# daily_range_pct fallback
# ---------------------------------------------------------------------------

def test_daily_range_reconstructed_from_pct():
    """When daily_high/low absent, reconstruct from daily_range_pct * price."""
    sl = _make_session_levels(ib_high=19100.0, ib_low=19000.0)
    # daily_range_pct = 0.01, price = 19000 → daily_range = 190
    # range_ratio = 190/100 = 1.9 → > 1.25
    ctx = {"daily_range_pct": 0.01}
    feats = extract_amt_features(sl, None, ctx, 19000.0)
    assert feats.shape == (20,)
    # At least one day type is set
    assert feats[:6].sum() == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# Expanded features (indices 13-19)
# ---------------------------------------------------------------------------

def test_expanded_feature_count():
    """Output vector is 20-dim after expansion."""
    feats = extract_amt_features(None, None, None, 19000.0)
    assert feats.shape == (20,)


def test_ib_percentile_feature():
    """ib_range_percentile from context maps to index 13."""
    sl = _make_session_levels(ib_high=19100.0, ib_low=19000.0)
    ctx = _make_ctx(daily_high=19120.0, daily_low=18980.0)
    ctx["ib_range_percentile"] = 0.75
    feats = extract_amt_features(sl, None, ctx, 19050.0)
    assert feats[13] == pytest.approx(0.75)


def test_overnight_gap_feature():
    """overnight_gap from context maps to index 14."""
    sl = _make_session_levels(ib_high=19100.0, ib_low=19000.0)
    ctx = _make_ctx(daily_high=19120.0, daily_low=18980.0)
    ctx["overnight_gap"] = 0.5
    feats = extract_amt_features(sl, None, ctx, 19050.0)
    assert feats[14] == pytest.approx(0.5)


def test_prior_poor_high_feature():
    """prior_poor_high=True maps to feats[17] == 1.0."""
    sl = _make_session_levels(ib_high=19100.0, ib_low=19000.0)
    ctx = _make_ctx(daily_high=19120.0, daily_low=18980.0)
    ctx["prior_poor_high"] = True
    feats = extract_amt_features(sl, None, ctx, 19050.0)
    assert feats[17] == pytest.approx(1.0)


def test_prior_excess_quality():
    """prior_excess_quality=5 → 5/10 = 0.5 at index 19."""
    sl = _make_session_levels(ib_high=19100.0, ib_low=19000.0)
    ctx = _make_ctx(daily_high=19120.0, daily_low=18980.0)
    ctx["prior_excess_quality"] = 5
    feats = extract_amt_features(sl, None, ctx, 19050.0)
    assert feats[19] == pytest.approx(0.5)
