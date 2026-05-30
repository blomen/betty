"""Integration: liquidity cap applied in the value-bet stake path."""

from src.analysis.value import ValueBet
from src.bankroll.stake_calculator import liquidity_capped_stake, provider_min_stake_sek


def test_value_bet_carries_depth_and_cap_fields():
    vb = ValueBet(
        event_id="e",
        market="moneyline",
        outcome="home",
        provider="polymarket",
        provider_odds=2.0,
        fair_odds=1.9,
        fair_probability=0.53,
        edge_pct=5.0,
    )
    assert vb.depth_usd is None
    assert vb.was_liquidity_capped is False
    assert vb.liquidity_cap_reason is None
    vb.depth_usd = 400.0
    assert vb.depth_usd == 400.0


def test_cap_then_min_floor_logic():
    # Mirrors the scanner logic: cap a stake, then re-check the provider floor.
    capped, was_capped, _ = liquidity_capped_stake(
        stake_sek=500.0, provider_id="polymarket", depth_usd=1.0, exchange_rate_sek=10.5
    )
    assert was_capped is True
    assert capped == 5.25  # 0.5*1*10.5
    floor = provider_min_stake_sek("polymarket", 10.5, 25.0)  # 1.0 native * 10.5 = 10.5
    assert capped < floor  # below the $1 native floor -> scanner skips the bet
