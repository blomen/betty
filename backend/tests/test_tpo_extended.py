"""Tests for extended TPO engine functions."""
import pytest
from src.market_data.tpo import (
    _period_letter, TPOProfile, compute_tpo_profile,
    classify_tpo_shape, detect_excess,
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
