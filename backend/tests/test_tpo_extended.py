"""Tests for extended TPO engine functions."""
import pytest
from src.market_data.tpo import _period_letter, TPOProfile, compute_tpo_profile


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
