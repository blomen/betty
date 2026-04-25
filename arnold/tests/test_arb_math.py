"""Pure arb math — guaranteed-profit and equal-payout stake calculations."""

from __future__ import annotations

import pytest

from arnold.mirror.arb_math import (
    is_valid_arb_shape,
    recalc_counter_stakes,
    recalc_profit_pct,
    should_update_stake,
)

UNLIMITED = {"pinnacle", "polymarket", "cloudbet", "kalshi"}


def test_is_valid_arb_shape_two_way_soft_plus_unlimited():
    legs = [{"provider": "unibet"}, {"provider": "pinnacle"}]
    assert is_valid_arb_shape(legs, unlimited=UNLIMITED) is True


def test_is_valid_arb_shape_two_softs_rejected():
    legs = [{"provider": "unibet"}, {"provider": "betsson"}]
    assert is_valid_arb_shape(legs, unlimited=UNLIMITED) is False


def test_is_valid_arb_shape_three_way_one_soft_two_unlimited():
    legs = [{"provider": "unibet"}, {"provider": "pinnacle"}, {"provider": "polymarket"}]
    assert is_valid_arb_shape(legs, unlimited=UNLIMITED) is True


def test_is_valid_arb_shape_three_way_two_softs_rejected():
    legs = [{"provider": "unibet"}, {"provider": "betsson"}, {"provider": "pinnacle"}]
    assert is_valid_arb_shape(legs, unlimited=UNLIMITED) is False


def test_is_valid_arb_shape_all_unlimited_rejected():
    # Pure unlimited isn't an arb opportunity for this UI — not the soft+unlimited shape we want
    legs = [{"provider": "pinnacle"}, {"provider": "polymarket"}]
    assert is_valid_arb_shape(legs, unlimited=UNLIMITED) is False


def test_recalc_profit_pct_two_way_positive():
    # Anchor 2.10 + counter 2.10 → 1/2.10 + 1/2.10 = 0.952 → profit ≈ 5%
    profit = recalc_profit_pct(anchor_odds=2.10, counter_odds=[2.10])
    assert pytest.approx(profit, rel=1e-3) == 5.0


def test_recalc_profit_pct_two_way_negative():
    # Anchor 1.90 + counter 1.90 → sum > 1 → negative
    profit = recalc_profit_pct(anchor_odds=1.90, counter_odds=[1.90])
    assert profit < 0


def test_recalc_profit_pct_three_way():
    # Three odds at 3.10 each — 3/3.10 = 0.9677 → profit ~3.33%
    profit = recalc_profit_pct(anchor_odds=3.10, counter_odds=[3.10, 3.10])
    assert pytest.approx(profit, rel=1e-3) == 3.333


def test_recalc_profit_pct_zero_odds_returns_none():
    assert recalc_profit_pct(anchor_odds=0.0, counter_odds=[2.0]) is None
    assert recalc_profit_pct(anchor_odds=2.0, counter_odds=[0.0, 2.0]) is None


def test_recalc_counter_stakes_two_way():
    # Anchor 100 SEK @ 2.0 → total payout 200 → counter @ 2.0 → 100 SEK
    stakes = recalc_counter_stakes(anchor_stake=100.0, anchor_odds=2.0, counter_odds=[2.0])
    assert stakes == [100.0]


def test_recalc_counter_stakes_uneven_odds():
    # Anchor 100 @ 2.0 → payout 200 → counter @ 4.0 → 50 SEK
    stakes = recalc_counter_stakes(anchor_stake=100.0, anchor_odds=2.0, counter_odds=[4.0])
    assert stakes == [50.0]


def test_recalc_counter_stakes_three_way():
    # Anchor 100 @ 3.0 → payout 300 → counters @ 3.0 each → 100 each
    stakes = recalc_counter_stakes(anchor_stake=100.0, anchor_odds=3.0, counter_odds=[3.0, 3.0])
    assert stakes == [100.0, 100.0]


def test_recalc_counter_stakes_rounded_to_cents():
    # 100 @ 1.91 → payout 191 → counter @ 2.13 → 89.67
    stakes = recalc_counter_stakes(anchor_stake=100.0, anchor_odds=1.91, counter_odds=[2.13])
    assert stakes == [89.67]


def test_should_update_stake_below_threshold():
    # Drift of 0.5 SEK on a 100 SEK stake — below 1 SEK and 1% — no update
    assert should_update_stake(old=100.0, new=100.5) is False


def test_should_update_stake_above_abs_threshold():
    # Drift of 1.5 SEK — above 1 SEK abs threshold — update
    assert should_update_stake(old=100.0, new=101.5) is True


def test_should_update_stake_above_pct_threshold_small_stake():
    # Stake 50 SEK, drift 0.7 SEK = 1.4% — above 1% threshold — update
    assert should_update_stake(old=50.0, new=50.7) is True


def test_should_update_stake_zero_old_always_updates():
    assert should_update_stake(old=0.0, new=10.0) is True
