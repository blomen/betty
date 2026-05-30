"""Tests for the shading-risk classifier (analysis/shading.py)."""

from src.analysis.shading import ShadingSignal, compute_shading


def _lean(lean: str, divergence_pp: float) -> dict:
    return {"lean": lean, "divergence_pp": divergence_pp}


def test_none_when_no_consensus_lean():
    assert compute_shading(0.5, "moneyline", None) is None


def test_stale_outlier_high_divergence_is_high():
    sig = compute_shading(0.55, "moneyline", _lean("stale_outlier", 6.0))
    assert sig is not None
    assert sig.risk == "high"
    assert sig.divergence_pp == 6.0


def test_stale_outlier_moderate_divergence_is_elevated():
    sig = compute_shading(0.55, "moneyline", _lean("stale_outlier", 2.5))
    assert sig.risk == "elevated"


def test_sharp_value_lean_is_low():
    sig = compute_shading(0.55, "moneyline", _lean("sharp_value", -5.0))
    assert sig.risk == "low"


def test_market_lag_lean_is_low():
    sig = compute_shading(0.55, "moneyline", _lean("market_lag", 0.5))
    assert sig.risk == "low"


def test_flb_flag_fires_on_two_way_heavy_favorite():
    sig = compute_shading(0.90, "moneyline", _lean("market_lag", 0.0))
    assert sig.flb_contrib is True
    assert sig.risk == "elevated"  # FLB lifts low -> elevated, never to high alone


def test_flb_flag_fires_on_two_way_longshot():
    sig = compute_shading(0.05, "total", _lean("market_lag", 0.0))
    assert sig.flb_contrib is True


def test_flb_longshot_boundary_exactly_at_threshold():
    # p == 1 - SHADING_FAV_EXTREME_PROB (0.20) must fire — symmetric with the
    # favorite side at 0.80. Guards against IEEE-754 drift in (1.0 - 0.80).
    sig = compute_shading(0.20, "moneyline", _lean("market_lag", 0.0))
    assert sig.flb_contrib is True
    sig_fav = compute_shading(0.80, "moneyline", _lean("market_lag", 0.0))
    assert sig_fav.flb_contrib is True


def test_flb_flag_never_fires_on_1x2():
    sig = compute_shading(0.90, "1x2", _lean("market_lag", 0.0))
    assert sig.flb_contrib is False
    assert sig.risk == "low"


def test_favorite_side_flag():
    assert compute_shading(0.70, "moneyline", _lean("market_lag", 0.0)).favorite_side is True
    assert compute_shading(0.30, "moneyline", _lean("market_lag", 0.0)).favorite_side is False


def test_to_dict_shape():
    d = compute_shading(0.90, "moneyline", _lean("stale_outlier", 6.0)).to_dict()
    assert set(d) == {"risk", "favorite_side", "fav_prob", "divergence_pp", "flb_contrib", "reason"}
    assert d["risk"] == "high"
