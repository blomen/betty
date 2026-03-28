"""Tests for Polymarket true edge calculation with spread cost."""
from src.analysis.value import find_value, polymarket_effective_odds


def test_spread_cost_reduces_edge():
    """Edge should be lower when bid-ask spread is wide."""
    vb_no_spread = find_value(
        event_id="test", market="moneyline", outcome="away",
        provider="polymarket", provider_odds=16.26, fair_odds=9.27,
    )
    vb_with_spread = find_value(
        event_id="test", market="moneyline", outcome="away",
        provider="polymarket", provider_odds=16.26, fair_odds=9.27,
        bid=0.04, ask=0.07,
    )
    assert vb_no_spread is not None
    assert vb_with_spread is not None
    assert vb_with_spread.edge_pct < vb_no_spread.edge_pct


def test_tight_spread_minimal_impact():
    """Tight spread should barely change the edge."""
    vb_no_spread = find_value(
        event_id="test", market="moneyline", outcome="home",
        provider="polymarket", provider_odds=1.98, fair_odds=1.178,
        min_edge_pct=0,
    )
    vb_tight = find_value(
        event_id="test", market="moneyline", outcome="home",
        provider="polymarket", provider_odds=1.98, fair_odds=1.178,
        bid=0.50, ask=0.52,
        min_edge_pct=0,
    )
    assert vb_no_spread is not None
    assert vb_tight is not None
    assert vb_no_spread.edge_pct - vb_tight.edge_pct < 5


def test_no_bid_falls_back_to_fee_only():
    """When bid is None, should use current fee-only behavior."""
    vb_no_bid = find_value(
        event_id="test", market="moneyline", outcome="away",
        provider="polymarket", provider_odds=3.0, fair_odds=2.5,
        bid=None, ask=0.35,
    )
    vb_baseline = find_value(
        event_id="test", market="moneyline", outcome="away",
        provider="polymarket", provider_odds=3.0, fair_odds=2.5,
    )
    assert vb_no_bid is not None
    assert vb_baseline is not None
    assert vb_no_bid.edge_pct == vb_baseline.edge_pct


def test_non_polymarket_ignores_bid_ask():
    """Non-Polymarket providers should ignore bid/ask even if passed."""
    vb_with = find_value(
        event_id="test", market="moneyline", outcome="home",
        provider="unibet", provider_odds=2.10, fair_odds=1.90,
        bid=0.40, ask=0.50,
        min_edge_pct=0,
    )
    vb_without = find_value(
        event_id="test", market="moneyline", outcome="home",
        provider="unibet", provider_odds=2.10, fair_odds=1.90,
        min_edge_pct=0,
    )
    assert vb_with.edge_pct == vb_without.edge_pct


def test_spread_can_eliminate_edge():
    """Very wide spread should be able to push edge below min threshold."""
    vb = find_value(
        event_id="test", market="moneyline", outcome="away",
        provider="polymarket", provider_odds=2.50, fair_odds=2.30,
        bid=0.20, ask=0.45,
        min_edge_pct=2.0,
    )
    assert vb is None


def test_fee_still_applied_with_spread():
    """Spread cost is additional to the 2% fee, not replacing it."""
    effective = polymarket_effective_odds(4.0)
    fee_only_edge = (effective / 3.0 - 1) * 100

    vb = find_value(
        event_id="test", market="moneyline", outcome="away",
        provider="polymarket", provider_odds=4.0, fair_odds=3.0,
        bid=0.24, ask=0.26,
        min_edge_pct=0,
    )
    assert vb is not None
    assert vb.edge_pct < fee_only_edge
    assert vb.edge_pct > 0
