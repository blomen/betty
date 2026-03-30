"""Tests for zone builder — ATR-adaptive level clustering."""

import pytest
from src.rl.config import LevelType, TICK_SIZE
from src.rl.zone_builder import Zone, ZoneMember, build_zones


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
    def test_poc_cluster_beats_sd_cluster(self):
        """POC levels (weight 1.0) should score higher than SD levels (0.4, 0.3)."""
        poc_levels = [
            ("a", LevelType.DAILY_POC, 4500.0),
            ("b", LevelType.WEEKLY_POC, 4500.5),
        ]
        sd_levels = [
            ("c", LevelType.VWAP_SD2, 4600.0),
            ("d", LevelType.VWAP_SD3, 4600.5),
        ]
        poc_zones = build_zones(poc_levels, session_atr=40.0)
        sd_zones = build_zones(sd_levels, session_atr=40.0)
        assert poc_zones[0].hierarchy_score > sd_zones[0].hierarchy_score


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
