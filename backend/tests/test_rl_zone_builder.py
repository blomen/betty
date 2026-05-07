"""Tests for zone builder — ATR-adaptive level clustering."""

import pytest

from src.rl.config import TICK_SIZE, LevelType
from src.rl.zone_builder import build_zones


class TestBuildZonesEmpty:
    def test_empty_levels_returns_empty(self):
        assert build_zones([], session_atr=40.0) == []


class TestBuildZonesSingleton:
    def test_single_level_creates_one_zone(self):
        levels = [("daily_poc_4500", LevelType.DAILY_POC, 4500.0)]
        zones = build_zones(levels, session_atr=40.0)
        assert len(zones) == 1
        assert zones[0].member_count == 1
        assert zones[0].center_price == 4500.0
        assert zones[0].members[0].name == "daily_poc_4500"


class TestBuildZonesMerging:
    def test_nearby_levels_merged(self):
        """ATR=40 -> radius = 0.05*40 = 2.0 points = 8 ticks (within [4,20]).
        Three levels 0.5 apart should all merge into one zone."""
        levels = [
            ("a", LevelType.DAILY_POC, 4500.0),
            ("b", LevelType.DAILY_VAH, 4500.5),
            ("c", LevelType.VWAP, 4501.0),
        ]
        zones = build_zones(levels, session_atr=40.0)
        assert len(zones) == 1
        assert zones[0].member_count == 3

    def test_far_apart_levels_separate(self):
        """ATR=40 -> radius = 2.0. Levels 10 points apart -> separate zones."""
        levels = [
            ("a", LevelType.DAILY_POC, 4500.0),
            ("b", LevelType.DAILY_VAH, 4510.0),
        ]
        zones = build_zones(levels, session_atr=40.0)
        assert len(zones) == 2


class TestChainMergeCap:
    """Regression for the unbounded chain-merge bug observed live 2026-05-07.

    Before the fix, the merge predicate compared each new level against the
    LAST member's price, so consecutive levels each within `radius` of the
    prior would chain into one zone with span >> radius. A 15-member,
    37-point z0 was produced from order blocks at 28704...28736 with radius
    = 5pt. Anchoring against the FIRST member caps cluster span at radius.
    """

    def test_chain_does_not_merge_beyond_radius(self):
        """ATR=200 -> radius clamped to 5 points. Eight levels spaced 1
        point apart span 7 points total, which exceeds the 5pt radius.
        With first-member anchoring this must produce >=2 zones."""
        levels = [("lvl", LevelType.DAILY_POC, 4500.0 + i) for i in range(8)]
        zones = build_zones(levels, session_atr=200.0)
        assert len(zones) >= 2
        for z in zones:
            prices = [m.price for m in z.members]
            assert max(prices) - min(prices) <= 5.0 + 1e-6

    def test_zone_width_capped_at_two_radius(self):
        """Span of any single zone is bounded by `radius`; total rectangle
        width (with radius/2 padding on each side) is bounded by 2*radius."""
        levels = [("lvl", LevelType.DAILY_POC, 4500.0 + 0.5 * i) for i in range(20)]
        zones = build_zones(levels, session_atr=200.0)  # radius = 5pt
        for z in zones:
            assert z.upper_bound - z.lower_bound <= 2 * 5.0 + 1e-6


class TestDenseFamilyPremerge:
    """SMC detector dedup — FVG/OB clusters within half-radius collapse to
    one representative before normal clustering. Other level types are
    unaffected.
    """

    def test_three_obs_within_half_radius_collapse(self):
        """ATR=200 -> radius=5 -> half-radius=2.5. Three OBs at 4500.0,
        4500.5, 4501.0 (max gap 1.0) collapse to a single representative
        at the median price 4500.5."""
        levels = [
            ("ob_a", LevelType.ORDER_BLOCK_BULL, 4500.0),
            ("ob_b", LevelType.ORDER_BLOCK_BULL, 4500.5),
            ("ob_c", LevelType.ORDER_BLOCK_BULL, 4501.0),
        ]
        zones = build_zones(levels, session_atr=200.0)
        assert len(zones) == 1
        assert zones[0].member_count == 1
        assert zones[0].center_price == pytest.approx(4500.5)

    def test_bull_and_bear_stay_separate(self):
        """Directional info preserved: bull and bear at the same price
        survive as two distinct members."""
        levels = [
            ("bull", LevelType.ORDER_BLOCK_BULL, 4500.0),
            ("bear", LevelType.ORDER_BLOCK_BEAR, 4500.0),
        ]
        zones = build_zones(levels, session_atr=200.0)
        assert len(zones) == 1
        assert zones[0].member_count == 2

    def test_obs_beyond_half_radius_survive(self):
        """ATR=200 -> half-radius=2.5. Two OBs 3pt apart exceed half-radius
        and both survive as members of one zone (still within full radius=5)."""
        levels = [
            ("ob_a", LevelType.ORDER_BLOCK_BULL, 4500.0),
            ("ob_b", LevelType.ORDER_BLOCK_BULL, 4503.0),
        ]
        zones = build_zones(levels, session_atr=200.0)
        assert len(zones) == 1
        assert zones[0].member_count == 2

    def test_non_premerge_types_unaffected(self):
        """VWAP, daily_poc, etc. are not in the pre-merge set — the
        function passes them through verbatim."""
        levels = [
            ("vwap_a", LevelType.VWAP, 4500.0),
            ("vwap_b", LevelType.VWAP, 4500.5),
        ]
        zones = build_zones(levels, session_atr=200.0)
        # Both VWAP entries survive (single zone, two members).
        assert len(zones) == 1
        assert zones[0].member_count == 2


class TestRadiusClamping:
    def test_radius_clamped_to_min(self):
        """ATR=1.0 -> raw radius = 0.05*1 = 0.05 points = 0.2 ticks.
        Clamped to min 4 ticks = 1.0 point.
        Two levels 0.75 apart -> merged due to floor."""
        levels = [
            ("a", LevelType.DAILY_POC, 4500.0),
            ("b", LevelType.DAILY_VAH, 4500.75),
        ]
        zones = build_zones(levels, session_atr=1.0)
        assert len(zones) == 1

    def test_radius_clamped_to_max(self):
        """ATR=200 -> raw radius = 0.05*200 = 10 points = 40 ticks.
        Clamped to max 20 ticks = 5.0 points.
        Two levels 6 apart -> separate zones due to cap."""
        levels = [
            ("a", LevelType.DAILY_POC, 4500.0),
            ("b", LevelType.DAILY_VAH, 4506.0),
        ]
        zones = build_zones(levels, session_atr=200.0)
        assert len(zones) == 2


class TestComposition:
    def test_multihot_composition_correctness(self):
        """Verify specific bits are set in the composition vector."""
        level_types = list(LevelType)
        poc_idx = level_types.index(LevelType.DAILY_POC)
        vah_idx = level_types.index(LevelType.DAILY_VAH)

        levels = [
            ("a", LevelType.DAILY_POC, 4500.0),
            ("b", LevelType.DAILY_VAH, 4500.5),
        ]
        zones = build_zones(levels, session_atr=40.0)
        assert len(zones) == 1
        comp = zones[0].composition
        assert len(comp) == len(LevelType)
        assert comp[poc_idx] == 1.0
        assert comp[vah_idx] == 1.0
        # All other bits should be 0
        for i, val in enumerate(comp):
            if i not in (poc_idx, vah_idx):
                assert val == 0.0


class TestHierarchyScore:
    def test_stronger_levels_score_higher(self):
        """Zones with clearly stronger level types score above clearly weaker.

        Uses large empirical-weight gaps (weekly_swing_low ≈1.14 /
        nyib_high ≈1.08 vs monthly_swing_low ≈0.74 / naked_poc ≈0.83)
        so ordering is robust when the empirical YAML is regenerated.
        """
        strong = [
            ("a", LevelType.WEEKLY_SWING_LOW, 4500.0),
            ("b", LevelType.NYIB_HIGH, 4500.5),
        ]
        weak = [
            ("c", LevelType.MONTHLY_SWING_LOW, 4600.0),
            ("d", LevelType.NAKED_POC, 4600.5),
        ]
        strong_zones = build_zones(strong, session_atr=40.0)
        weak_zones = build_zones(weak, session_atr=40.0)
        assert strong_zones[0].hierarchy_score > weak_zones[0].hierarchy_score


class TestZoneBounds:
    def test_bounds_include_radius_padding(self):
        """Bounds should extend beyond the outermost members by radius/2."""
        levels = [
            ("a", LevelType.DAILY_POC, 4500.0),
            ("b", LevelType.DAILY_VAH, 4501.0),
        ]
        # ATR=40 -> radius = 2.0 points
        zones = build_zones(levels, session_atr=40.0)
        assert len(zones) == 1
        z = zones[0]
        # lower = min(4500) - 2.0/2 = 4499.0
        assert z.lower_bound == pytest.approx(4499.0)
        # upper = max(4501) + 2.0/2 = 4502.0
        assert z.upper_bound == pytest.approx(4502.0)


class TestWidthTicks:
    def test_width_ticks_computed_correctly(self):
        levels = [
            ("a", LevelType.DAILY_POC, 4500.0),
            ("b", LevelType.DAILY_VAH, 4501.0),
        ]
        # ATR=40 -> radius=2.0, bounds: 4499.0 to 4502.0, width=3.0 points = 12 ticks
        zones = build_zones(levels, session_atr=40.0)
        assert zones[0].width_ticks == pytest.approx(3.0 / TICK_SIZE)


class TestSortOrder:
    def test_output_sorted_by_center_price(self):
        """Even if input is unsorted, output zones should be sorted ascending."""
        levels = [
            ("high", LevelType.DAILY_POC, 4600.0),
            ("low", LevelType.DAILY_VAH, 4400.0),
            ("mid", LevelType.VWAP, 4500.0),
        ]
        # ATR=40 -> radius=2.0, all 100+ points apart -> 3 separate zones
        zones = build_zones(levels, session_atr=40.0)
        assert len(zones) == 3
        assert zones[0].center_price < zones[1].center_price < zones[2].center_price
