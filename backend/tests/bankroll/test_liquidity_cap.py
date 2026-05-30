"""Tests for the prediction-market liquidity stake cap."""

from src.bankroll.stake_calculator import liquidity_capped_stake


def test_gated_provider_over_cap_is_capped():
    # polymarket fraction 0.5, depth $400, rate 10.5 -> cap = 0.5*400*10.5 = 2100 SEK.
    capped, was_capped, reason = liquidity_capped_stake(
        stake_sek=5000.0, provider_id="polymarket", depth_usd=400.0, exchange_rate_sek=10.5
    )
    assert capped == 2100.0
    assert was_capped is True
    assert reason is not None and "liquidity" in reason


def test_gated_provider_under_cap_unchanged():
    capped, was_capped, reason = liquidity_capped_stake(
        stake_sek=1000.0, provider_id="polymarket", depth_usd=400.0, exchange_rate_sek=10.5
    )
    assert capped == 1000.0
    assert was_capped is False
    assert reason is None


def test_kalshi_is_gated():
    capped, was_capped, _ = liquidity_capped_stake(
        stake_sek=5000.0, provider_id="kalshi", depth_usd=100.0, exchange_rate_sek=10.5
    )
    assert capped == 525.0  # 0.5*100*10.5
    assert was_capped is True


def test_ungated_provider_never_capped():
    capped, was_capped, reason = liquidity_capped_stake(
        stake_sek=5000.0, provider_id="pinnacle", depth_usd=10.0, exchange_rate_sek=1.0
    )
    assert capped == 5000.0
    assert was_capped is False
    assert reason is None


def test_cloudbet_ungated():
    capped, was_capped, _ = liquidity_capped_stake(
        stake_sek=5000.0, provider_id="cloudbet", depth_usd=10.0, exchange_rate_sek=10.5
    )
    assert capped == 5000.0
    assert was_capped is False


def test_null_depth_no_cap():
    capped, was_capped, _ = liquidity_capped_stake(
        stake_sek=5000.0, provider_id="polymarket", depth_usd=None, exchange_rate_sek=10.5
    )
    assert capped == 5000.0
    assert was_capped is False


def test_zero_or_negative_depth_no_cap():
    for bad in (0.0, -5.0):
        capped, was_capped, _ = liquidity_capped_stake(
            stake_sek=5000.0, provider_id="polymarket", depth_usd=bad, exchange_rate_sek=10.5
        )
        assert capped == 5000.0
        assert was_capped is False


def test_unknown_provider_no_cap():
    capped, was_capped, _ = liquidity_capped_stake(
        stake_sek=5000.0, provider_id="betsson", depth_usd=10.0, exchange_rate_sek=1.0
    )
    assert capped == 5000.0
    assert was_capped is False
