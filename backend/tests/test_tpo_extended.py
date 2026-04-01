"""Tests for extended TPO engine functions."""
import pytest
from datetime import datetime, timezone

from src.market_data.tpo import (
    _period_letter, TPOProfile, compute_tpo_profile,
    classify_tpo_shape, detect_excess, classify_opening_type,
    build_full_tpo_profile, aggregate_bars_30m,
    SessionTPO, compute_session_tpos,
)


class TestPeriodLetter:
    def test_first_26_letters(self):
        assert _period_letter(0) == "A"
        assert _period_letter(25) == "Z"

    def test_beyond_26(self):
        assert _period_letter(26) == "AA"
        assert _period_letter(27) == "AB"
        assert _period_letter(51) == "AZ"
        assert _period_letter(52) == "BA"

    def test_full_globex_session(self):
        assert _period_letter(45) == "AT"


class TestTPOProfileNewFields:
    def test_new_fields_have_defaults(self):
        profile = TPOProfile(
            letters={}, poc=0, vah=0, val=0,
            single_prints=[], ledges=[],
            poor_high=False, poor_low=False, ib_tpo_count=0,
        )
        assert profile.tpo_counts == {}
        assert profile.rotation_factor == 0
        assert profile.profile_shape == "balanced"
        assert profile.opening_type == "OA"
        assert profile.opening_direction == "neutral"
        assert profile.upper_excess == 0
        assert profile.lower_excess == 0
        assert profile.ib_high == 0.0
        assert profile.ib_low == 0.0
        assert profile.session_high == 0.0
        assert profile.session_low == 0.0


def _make_bar(o, h, l, c, v=100):
    return {"open": o, "high": h, "low": l, "close": c, "volume": v}


class TestComputeTPOProfileExtended:
    def test_tpo_counts_populated(self):
        bars = [_make_bar(100, 101, 99, 100.5)]
        profile = compute_tpo_profile(bars, tick_size=0.5)
        assert profile.tpo_counts[100.0] == 1

    def test_ib_high_low_from_first_two_periods(self):
        bars = [
            _make_bar(100, 105, 98, 103),
            _make_bar(103, 107, 101, 106),
            _make_bar(106, 110, 104, 109),
        ]
        profile = compute_tpo_profile(bars, tick_size=0.25)
        assert profile.ib_high == 107.0
        assert profile.ib_low == 98.0

    def test_ib_with_single_bar(self):
        bars = [_make_bar(100, 105, 98, 103)]
        profile = compute_tpo_profile(bars, tick_size=0.25)
        assert profile.ib_high == 105.0
        assert profile.ib_low == 98.0

    def test_session_high_low(self):
        bars = [
            _make_bar(100, 105, 95, 103),
            _make_bar(103, 110, 100, 108),
        ]
        profile = compute_tpo_profile(bars, tick_size=0.25)
        assert profile.session_high == 110.0
        assert profile.session_low == 95.0

    def test_upper_excess_counts_consecutive_single_prints(self):
        bars = [
            _make_bar(100, 106, 100, 105),
            _make_bar(100, 104, 100, 103),
            _make_bar(100, 102, 100, 101),
        ]
        profile = compute_tpo_profile(bars, tick_size=0.25)
        assert profile.upper_excess > 0

    def test_lower_excess_counts_consecutive_single_prints(self):
        bars = [
            _make_bar(100, 106, 94, 105),
            _make_bar(100, 106, 96, 103),
            _make_bar(100, 106, 98, 101),
        ]
        profile = compute_tpo_profile(bars, tick_size=0.25)
        assert profile.lower_excess > 0

    def test_empty_bars(self):
        profile = compute_tpo_profile([], tick_size=0.25)
        assert profile.tpo_counts == {}
        assert profile.ib_high == 0.0
        assert profile.session_high == 0.0

    def test_letters_beyond_26(self):
        bars = [_make_bar(100, 100.25, 100, 100.25) for _ in range(27)]
        profile = compute_tpo_profile(bars, tick_size=0.25)
        assert "AA" in profile.letters[100.0]


class TestClassifyTPOShapeBShape:
    def test_double_distribution_is_B_shape(self):
        """Two peaks with a valley between them -> B-shape."""
        letters = {}
        # Lower cluster: 98-103, heavy TPOs
        for p_int in range(392, 413):  # 98.0 to 103.0 in 0.25 steps
            p = p_int * 0.25
            letters[p] = ["A", "B", "C", "D", "E", "F", "G"]
        # Valley: 103.25-106.75, minimal TPOs
        for p_int in range(413, 428):
            p = p_int * 0.25
            letters[p] = ["D"]
        # Upper cluster: 107-112, heavy TPOs
        for p_int in range(428, 449):
            p = p_int * 0.25
            letters[p] = ["E", "F", "G", "H", "I", "J", "K"]
        profile = TPOProfile(
            letters=letters, poc=100.5, vah=112.0, val=98.0,
            single_prints=[], ledges=[], poor_high=False, poor_low=False,
            ib_tpo_count=0,
        )
        assert classify_tpo_shape(profile) == "B-shape"

    def test_single_cluster_not_B_shape(self):
        letters = {}
        for p_int in range(400, 421):
            p = p_int * 0.25
            count = max(1, 7 - abs(p_int - 410))
            letters[p] = [chr(65 + j) for j in range(count)]
        profile = TPOProfile(
            letters=letters, poc=102.5, vah=104.0, val=101.0,
            single_prints=[], ledges=[], poor_high=False, poor_low=False,
            ib_tpo_count=0,
        )
        assert classify_tpo_shape(profile) != "B-shape"


class TestDetectExcessTickCounts:
    def test_returns_int_counts(self):
        letters = {
            100.0: ["A"],
            100.25: ["A"],
            100.5: ["A", "B", "C"],
            100.75: ["A", "B"],
            101.0: ["A"],
        }
        profile = TPOProfile(
            letters=letters, poc=100.5, vah=100.75, val=100.25,
            single_prints=[100.0, 100.25, 101.0], ledges=[],
            poor_high=False, poor_low=False, ib_tpo_count=0,
        )
        upper, lower = detect_excess(profile)
        assert isinstance(upper, int)
        assert isinstance(lower, int)
        assert upper == 1
        assert lower == 2

    def test_truthy_compat(self):
        letters = {100.0: ["A"], 100.25: ["A", "B"]}
        profile = TPOProfile(
            letters=letters, poc=100.25, vah=100.25, val=100.0,
            single_prints=[100.0], ledges=[],
            poor_high=False, poor_low=False, ib_tpo_count=0,
        )
        upper, lower = detect_excess(profile)
        assert not upper
        assert lower


class TestClassifyOpeningType:
    def test_open_drive_up(self):
        bars = [
            _make_bar(100, 105, 99, 104),
            _make_bar(104, 110, 103, 109),
            _make_bar(109, 112, 107, 111),
            _make_bar(111, 113, 110, 112),
        ]
        otype, direction = classify_opening_type(bars)
        assert otype == "OD"
        assert direction == "up"

    def test_open_test_drive(self):
        bars = [
            _make_bar(100, 105, 99, 104),
            _make_bar(104, 104, 100, 101),
            _make_bar(101, 108, 101, 107),
            _make_bar(107, 109, 106, 108),
        ]
        otype, direction = classify_opening_type(bars)
        assert otype == "OTD"
        assert direction == "up"

    def test_open_rejection_reverse(self):
        bars = [
            _make_bar(100, 105, 99, 104),
            _make_bar(104, 108, 103, 107),
            _make_bar(107, 107, 97, 98),
            _make_bar(98, 99, 95, 96),
        ]
        otype, direction = classify_opening_type(bars)
        assert otype == "ORR"

    def test_open_auction(self):
        bars = [
            _make_bar(100, 103, 99, 101),
            _make_bar(101, 104, 100, 102),
            _make_bar(102, 103, 99, 100),
            _make_bar(100, 102, 98, 101),
        ]
        otype, direction = classify_opening_type(bars)
        assert otype == "OA"

    def test_fewer_than_4_bars(self):
        bars = [_make_bar(100, 105, 99, 104)]
        otype, direction = classify_opening_type(bars)
        assert otype == "OA"
        assert direction == "neutral"

    def test_empty_bars(self):
        otype, direction = classify_opening_type([])
        assert otype == "OA"
        assert direction == "neutral"


class TestBuildFullTPOProfile:
    def test_all_fields_populated(self):
        bars = [
            _make_bar(100, 108, 98, 106),
            _make_bar(106, 112, 104, 110),
            _make_bar(110, 114, 108, 113),
            _make_bar(113, 115, 111, 114),
            _make_bar(114, 116, 112, 115),
        ]
        profile = build_full_tpo_profile(bars, tick_size=0.25)
        assert profile.poc > 0
        assert profile.vah >= profile.val
        assert isinstance(profile.rotation_factor, int)
        assert profile.profile_shape in ("p-shape", "b-shape", "d-shape", "balanced", "B-shape")
        assert profile.opening_type in ("OD", "OTD", "ORR", "OA")
        assert profile.opening_direction in ("up", "down", "neutral")
        assert profile.ib_high == 112.0
        assert profile.ib_low == 98.0
        assert profile.session_high == 116.0
        assert profile.session_low == 98.0
        assert profile.upper_excess >= 0
        assert profile.lower_excess >= 0

    def test_empty_bars(self):
        profile = build_full_tpo_profile([], tick_size=0.25)
        assert profile.poc == 0
        assert profile.rotation_factor == 0
        assert profile.opening_type == "OA"


class TestAggregateBars30m:
    def test_groups_into_30_bar_chunks(self):
        class FakeBar:
            def __init__(self, i):
                self.open = 100 + i * 0.1
                self.high = 100 + i * 0.1 + 0.5
                self.low = 100 + i * 0.1 - 0.5
                self.close = 100 + i * 0.1 + 0.2
                self.volume = 10
        bars = [FakeBar(i) for i in range(60)]
        result = aggregate_bars_30m(bars)
        assert len(result) == 2
        assert "high" in result[0]
        assert "low" in result[0]
        assert result[0]["volume"] == 300

    def test_partial_chunk_dropped(self):
        class FakeBar:
            def __init__(self):
                self.open = self.high = self.low = self.close = 100
                self.volume = 10
        bars = [FakeBar() for _ in range(45)]
        result = aggregate_bars_30m(bars)
        assert len(result) == 1


def _make_bar_ts(o, h, l, c, ts_str, v=100):
    """Create a 30m bar dict with timestamp for session splitting."""
    ts = datetime.fromisoformat(ts_str).replace(tzinfo=timezone.utc)
    return {"open": o, "high": h, "low": l, "close": c, "volume": v, "ts": ts}


class TestSessionTPOLetters:
    def test_session_tpo_has_letters(self):
        """SessionTPO should include letters dict after enrichment."""
        # Tokyo session: 2 bars at 01:00 and 01:30 UTC (= 02:00/02:30 CET → Tokyo)
        bars = [
            _make_bar_ts(100, 102, 99, 101, "2026-03-27T01:00:00"),
            _make_bar_ts(101, 103, 100, 102, "2026-03-27T01:30:00"),
        ]
        result = compute_session_tpos(bars, tick_size=0.25)
        tky = result.tokyo
        assert tky is not None
        assert isinstance(tky.letters, dict)
        assert len(tky.letters) > 0
        # POC price should have the most letters
        poc_letters = tky.letters[tky.poc]
        assert len(poc_letters) >= 1

    def test_session_tpo_has_opening_type(self):
        """SessionTPO should include opening_type and opening_direction."""
        bars = [
            _make_bar_ts(100, 102, 99, 101, "2026-03-27T01:00:00"),
            _make_bar_ts(101, 103, 100, 102, "2026-03-27T01:30:00"),
        ]
        result = compute_session_tpos(bars, tick_size=0.25)
        tky = result.tokyo
        assert tky is not None
        assert hasattr(tky, "opening_type")
        assert hasattr(tky, "opening_direction")
        assert tky.opening_type in ("OD", "OTD", "ORR", "OA")

    def test_session_tpo_has_excess_and_session_range(self):
        """SessionTPO should include excess counts and session high/low."""
        bars = [
            _make_bar_ts(100, 102, 99, 101, "2026-03-27T01:00:00"),
            _make_bar_ts(101, 103, 100, 102, "2026-03-27T01:30:00"),
        ]
        result = compute_session_tpos(bars, tick_size=0.25)
        tky = result.tokyo
        assert tky is not None
        assert isinstance(tky.upper_excess, int)
        assert isinstance(tky.lower_excess, int)
        assert tky.session_high == 103
        assert tky.session_low == 99
        assert isinstance(tky.tpo_counts, dict)
