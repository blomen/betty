"""Tests for the multi-book sharp blend math (analysis/devig.py)."""

import pytest

from src.analysis.devig import (
    compute_blended_sharp_fair,
    get_fair_odds_for_outcome,
)

MEMBERS = ["pinnacle", "cloudbet", "kalshi", "polymarket"]
LIQ_GATED = {"kalshi", "polymarket"}


def _market(rows):
    """rows: {outcome: [(provider, odds, depth_usd), ...]} -> odds_by_outcome dict."""
    return {out: [{"provider": p, "odds": o, "depth_usd": d} for (p, o, d) in lst] for out, lst in rows.items()}


def test_only_pinnacle_returns_pinnacle_fair_parity():
    # Blend with only Pinnacle present must equal the single-source devig.
    obo = _market({"home": [("pinnacle", 1.91, None)], "away": [("pinnacle", 1.91, None)]})
    result = compute_blended_sharp_fair(
        outcome="home",
        odds_by_outcome=obo,
        members=MEMBERS,
        weights={"pinnacle": 1.0, "cloudbet": 0.6, "max_dev_pct": 8},
        liquidity_gated=LIQ_GATED,
        liquidity_min_usd=500,
    )
    assert result is not None
    assert result.n_sources == 1
    assert result.sources == ["pinnacle"]
    expected = get_fair_odds_for_outcome("home", {"home": 1.91, "away": 1.91})
    assert result.fair_odds == pytest.approx(expected, rel=1e-9)


def test_weighted_harmonic_mean_two_sources():
    # Pinnacle fair home ~2.0 (1.91/1.91), Cloudbet fair home ~2.5 (1.80/3.00 devig).
    # Weighted harmonic mean of fair odds = inverse of weighted-mean probability.
    obo = _market(
        {
            "home": [("pinnacle", 1.91, None), ("cloudbet", 1.80, None)],
            "away": [("pinnacle", 1.91, None), ("cloudbet", 2.20, None)],
        }
    )
    weights = {"pinnacle": 1.0, "cloudbet": 1.0, "max_dev_pct": 50}  # loose leash
    result = compute_blended_sharp_fair(
        outcome="home",
        odds_by_outcome=obo,
        members=MEMBERS,
        weights=weights,
        liquidity_gated=LIQ_GATED,
        liquidity_min_usd=500,
    )
    assert result is not None
    assert result.n_sources == 2
    pin = get_fair_odds_for_outcome("home", {"home": 1.91, "away": 1.91})
    cb = get_fair_odds_for_outcome("home", {"home": 1.80, "away": 2.20})
    expected = 2.0 / (1.0 / pin + 1.0 / cb)  # equal-weight harmonic mean of odds
    assert result.fair_odds == pytest.approx(expected, rel=1e-9)
    assert result.clamped is False


def test_liquidity_gate_drops_thin_prediction_market():
    # Kalshi has depth below the gate -> excluded; only pinnacle qualifies.
    obo = _market(
        {
            "home": [("pinnacle", 1.91, None), ("kalshi", 1.50, 100.0)],
            "away": [("pinnacle", 1.91, None), ("kalshi", 2.50, 100.0)],
        }
    )
    result = compute_blended_sharp_fair(
        outcome="home",
        odds_by_outcome=obo,
        members=MEMBERS,
        weights={"pinnacle": 1.0, "kalshi": 1.0, "max_dev_pct": 50},
        liquidity_gated=LIQ_GATED,
        liquidity_min_usd=500,
    )
    assert result.n_sources == 1
    assert result.sources == ["pinnacle"]


def test_liquidity_gate_admits_deep_prediction_market():
    obo = _market(
        {
            "home": [("pinnacle", 1.91, None), ("kalshi", 1.80, 5000.0)],
            "away": [("pinnacle", 1.91, None), ("kalshi", 2.20, 5000.0)],
        }
    )
    result = compute_blended_sharp_fair(
        outcome="home",
        odds_by_outcome=obo,
        members=MEMBERS,
        weights={"pinnacle": 1.0, "kalshi": 1.0, "max_dev_pct": 50},
        liquidity_gated=LIQ_GATED,
        liquidity_min_usd=500,
    )
    assert result.n_sources == 2
    assert "kalshi" in result.sources


def test_guardrail_clamps_outlier_blend_toward_pinnacle():
    # Cloudbet wildly off -> blend would deviate far from Pinnacle; clamp to +/-4%.
    obo = _market(
        {
            "home": [("pinnacle", 2.00, None), ("cloudbet", 5.00, None)],
            "away": [("pinnacle", 2.00, None), ("cloudbet", 1.25, None)],
        }
    )
    weights = {"pinnacle": 1.0, "cloudbet": 1.0, "max_dev_pct": 4}
    result = compute_blended_sharp_fair(
        outcome="home",
        odds_by_outcome=obo,
        members=MEMBERS,
        weights=weights,
        liquidity_gated=LIQ_GATED,
        liquidity_min_usd=500,
    )
    pin = get_fair_odds_for_outcome("home", {"home": 2.00, "away": 2.00})  # ~2.0
    assert result.clamped is True
    assert result.fair_odds <= pin * 1.04 + 1e-9
    assert result.fair_odds >= pin * 0.96 - 1e-9


def test_no_members_returns_none():
    obo = _market({"home": [("betsson", 1.95, None)], "away": [("betsson", 1.95, None)]})
    result = compute_blended_sharp_fair(
        outcome="home",
        odds_by_outcome=obo,
        members=MEMBERS,
        weights={"pinnacle": 1.0, "max_dev_pct": 8},
        liquidity_gated=LIQ_GATED,
        liquidity_min_usd=500,
    )
    assert result is None


def test_incomplete_member_market_skipped():
    # Cloudbet only has 'home', not 'away' -> can't devig -> skipped.
    obo = _market(
        {
            "home": [("pinnacle", 1.91, None), ("cloudbet", 1.80, None)],
            "away": [("pinnacle", 1.91, None)],
        }
    )
    result = compute_blended_sharp_fair(
        outcome="home",
        odds_by_outcome=obo,
        members=MEMBERS,
        weights={"pinnacle": 1.0, "cloudbet": 1.0, "max_dev_pct": 50},
        liquidity_gated=LIQ_GATED,
        liquidity_min_usd=500,
    )
    assert result.n_sources == 1
    assert result.sources == ["pinnacle"]


def test_loader_exposes_sharp_blend_block():
    from src.config.loader import load_config

    cfg = load_config()
    blend = cfg.get_sharp_blend()
    assert "pinnacle" in blend["members"]
    assert set(blend["liquidity_gated"]) == {"kalshi", "polymarket"}
    assert blend["liquidity_min_usd"] == 500
    assert "default" in blend["per_sport"]


def test_resolve_weights_falls_back_to_default():
    from src.analysis.sharp_blend import resolve_weights

    w = resolve_weights("some_unknown_sport")
    assert w["pinnacle"] == 1.0
    assert "max_dev_pct" in w


def test_blended_fair_from_rows():
    from src.analysis.sharp_blend import blended_fair_from_rows

    # rows mimic Odds: objects with provider_id, outcome, odds, depth_usd.
    class Row:
        def __init__(self, provider_id, outcome, odds, depth_usd=None):
            self.provider_id = provider_id
            self.outcome = outcome
            self.odds = odds
            self.depth_usd = depth_usd

    rows = [
        Row("pinnacle", "home", 1.91),
        Row("pinnacle", "away", 1.91),
        Row("cloudbet", "home", 1.80),
        Row("cloudbet", "away", 2.20),
        Row("betsson", "home", 1.95),
        Row("betsson", "away", 1.95),  # non-member ignored
    ]
    result = blended_fair_from_rows(outcome="home", rows=rows, sport="soccer_epl")
    assert result is not None
    assert "pinnacle" in result.sources
    assert "cloudbet" in result.sources
    assert "betsson" not in result.sources
