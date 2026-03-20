"""Tests for session_store: SessionSummary, filter_single_print_zones, composite histogram,
naked POC finder, build_session_summary, and summary I/O."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from src.rl.data.session_store import (
    SessionSummary,
    filter_single_print_zones,
    composite_histogram,
    poc_from_histogram,
    find_naked_pocs,
    build_session_summary,
    save_summaries,
    load_summaries,
    compute_precomputed_levels,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_tick(ts: str, price: float, size: int) -> dict:
    """Build a minimal tick dict."""
    return {"ts": ts, "price": price, "size": size}


def _make_summary(
    date: str,
    poc: float = 100.0,
    vah: float = 101.0,
    val: float = 99.0,
    histogram: dict | None = None,
    rth_high: float | None = None,
    rth_low: float | None = None,
    eth_high: float | None = None,
    eth_low: float | None = None,
    single_print_zones: list | None = None,
) -> SessionSummary:
    return SessionSummary(
        date=date,
        poc=poc,
        vah=vah,
        val=val,
        histogram=histogram or {"100.00": 100},
        rth_high=rth_high,
        rth_low=rth_low,
        eth_high=eth_high,
        eth_low=eth_low,
        single_print_zones=single_print_zones or [],
    )


# ---------------------------------------------------------------------------
# Task 1: filter_single_print_zones
# ---------------------------------------------------------------------------

class TestFilterSinglePrintZones:
    def test_empty_returns_empty(self):
        assert filter_single_print_zones([]) == []

    def test_fewer_than_min_consecutive_filtered_out(self):
        # Two consecutive prints: below min_consecutive=3, should return empty
        result = filter_single_print_zones(
            [(100.0, 100.0), (100.25, 100.25)],
            tick_size=0.25,
            min_consecutive=3,
        )
        assert result == []

    def test_three_consecutive_forms_zone(self):
        # Exactly 3 consecutive ticks at 0.25 spacing -> one zone
        result = filter_single_print_zones(
            [(100.0, 100.0), (100.25, 100.25), (100.50, 100.50)],
            tick_size=0.25,
            min_consecutive=3,
        )
        assert len(result) == 1
        zone_low, zone_high = result[0]
        assert zone_low == pytest.approx(100.0)
        assert zone_high == pytest.approx(100.50)

    def test_gap_splits_into_two_zones(self):
        # Two clusters separated by a gap bigger than 1 tick
        # Cluster A: 100.0, 100.25, 100.50 (3 levels)
        # Cluster B: 101.50, 101.75, 102.00 (3 levels)
        prints = [
            (100.0, 100.0), (100.25, 100.25), (100.50, 100.50),
            (101.50, 101.50), (101.75, 101.75), (102.00, 102.00),
        ]
        result = filter_single_print_zones(prints, tick_size=0.25, min_consecutive=3)
        assert len(result) == 2
        assert result[0] == pytest.approx((100.0, 100.50))
        assert result[1] == pytest.approx((101.50, 102.00))

    def test_unsorted_input_handled(self):
        # Input arrives in random order; must still group correctly
        prints = [
            (100.50, 100.50), (100.0, 100.0), (100.25, 100.25),
        ]
        result = filter_single_print_zones(prints, tick_size=0.25, min_consecutive=3)
        assert len(result) == 1
        assert result[0] == pytest.approx((100.0, 100.50))

    def test_short_group_filtered(self):
        # One group of 4 (passes), one group of 2 (fails)
        prints = [
            (100.0, 100.0), (100.25, 100.25), (100.50, 100.50), (100.75, 100.75),
            (102.0, 102.0), (102.25, 102.25),
        ]
        result = filter_single_print_zones(prints, tick_size=0.25, min_consecutive=3)
        assert len(result) == 1
        assert result[0] == pytest.approx((100.0, 100.75))


# ---------------------------------------------------------------------------
# Task 2: composite_histogram + poc_from_histogram
# ---------------------------------------------------------------------------

class TestCompositeHistogram:
    def test_single_session(self):
        s = _make_summary("2026-01-01", histogram={"100.00": 50, "100.25": 30})
        result = composite_histogram([s])
        assert result[100.00] == 50
        assert result[100.25] == 30

    def test_two_sessions_additive(self):
        s1 = _make_summary("2026-01-01", histogram={"100.00": 50, "100.25": 30})
        s2 = _make_summary("2026-01-02", histogram={"100.00": 20, "100.50": 40})
        result = composite_histogram([s1, s2])
        assert result[100.00] == 70   # 50 + 20
        assert result[100.25] == 30   # only s1
        assert result[100.50] == 40   # only s2

    def test_empty_list(self):
        assert composite_histogram([]) == {}


class TestPocFromHistogram:
    def test_basic(self):
        histo = {100.0: 10, 100.25: 50, 100.50: 30}
        assert poc_from_histogram(histo) == pytest.approx(100.25)

    def test_empty_returns_none(self):
        assert poc_from_histogram({}) is None

    def test_tie_returns_a_value(self):
        # When two bins are equal, must still return one of them (not crash)
        histo = {100.0: 100, 100.25: 100}
        result = poc_from_histogram(histo)
        assert result in (100.0, 100.25)


# ---------------------------------------------------------------------------
# Task 3: find_naked_pocs
# ---------------------------------------------------------------------------

class TestFindNakedPocs:
    def test_all_naked_when_no_overlap(self):
        # Two sessions with POCs outside each other's RTH range
        summaries = {
            "2026-01-01": _make_summary("2026-01-01", poc=100.0, rth_high=101.0, rth_low=99.0),
            "2026-01-02": _make_summary("2026-01-02", poc=100.0, rth_high=102.0, rth_low=103.0),
        }
        # current_date = "2026-01-03", look back at 01 and 02
        result = find_naked_pocs(summaries, current_date="2026-01-03")
        # 2026-01-01 POC at 100.0: is it inside [103, 102]? No -- naked
        # 2026-01-02 POC at 100.0: no later session to test -> naked
        assert any(r["date"] == "2026-01-01" for r in result)

    def test_touched_by_later_session(self):
        # POC of 01-01 lies within the RTH range of 01-02 -> NOT naked
        summaries = {
            "2026-01-01": _make_summary("2026-01-01", poc=100.0, rth_high=101.0, rth_low=99.0),
            "2026-01-02": _make_summary("2026-01-02", poc=105.0, rth_high=101.0, rth_low=99.0),
        }
        result = find_naked_pocs(summaries, current_date="2026-01-03")
        dates = [r["date"] for r in result]
        assert "2026-01-01" not in dates

    def test_empty_summaries(self):
        assert find_naked_pocs({}, current_date="2026-01-01") == []

    def test_max_lookback_limits_sessions(self):
        # Build 25 sessions, only the last 20 should be considered
        summaries = {}
        for i in range(1, 26):
            date = f"2026-01-{i:02d}"
            summaries[date] = _make_summary(date, poc=float(i), rth_high=float(i) + 0.5, rth_low=float(i) - 0.5)
        result = find_naked_pocs(summaries, current_date="2026-01-26", max_lookback_sessions=20)
        result_dates = {r["date"] for r in result}
        # Session 1 (day 1) is outside lookback of 20 -> should NOT appear
        assert "2026-01-01" not in result_dates

    def test_none_rth_range_treated_as_not_touching(self):
        # If a later session has None RTH range, it cannot "touch" a POC
        summaries = {
            "2026-01-01": _make_summary("2026-01-01", poc=100.0, rth_high=101.0, rth_low=99.0),
            "2026-01-02": _make_summary("2026-01-02", poc=105.0, rth_high=None, rth_low=None),
        }
        result = find_naked_pocs(summaries, current_date="2026-01-03")
        dates = [r["date"] for r in result]
        assert "2026-01-01" in dates


# ---------------------------------------------------------------------------
# Task 4: build_session_summary
# ---------------------------------------------------------------------------

class TestBuildSessionSummary:
    def _rth_ts(self, time_str: str) -> str:
        """Build an ISO timestamp during RTH (09:30-16:00 ET on 2026-01-15)."""
        return f"2026-01-15T{time_str}-05:00"

    def _eth_ts(self, time_str: str) -> str:
        """Build an ISO timestamp during ETH (before 09:30 ET on 2026-01-15)."""
        return f"2026-01-15T{time_str}-05:00"

    def test_basic_session(self):
        ticks = [
            _make_tick(self._rth_ts("10:00:00"), 100.0, 50),
            _make_tick(self._rth_ts("11:00:00"), 100.25, 200),
            _make_tick(self._rth_ts("12:00:00"), 100.50, 30),
        ]
        result = build_session_summary("2026-01-15", ticks)
        assert result.date == "2026-01-15"
        assert result.poc == pytest.approx(100.25)  # highest volume
        assert result.vah >= result.poc
        assert result.val <= result.poc

    def test_rth_range_tracked(self):
        ticks = [
            _make_tick(self._rth_ts("09:30:00"), 100.0, 10),
            _make_tick(self._rth_ts("12:00:00"), 105.0, 10),
            _make_tick(self._rth_ts("15:59:00"), 99.0, 10),
        ]
        result = build_session_summary("2026-01-15", ticks)
        assert result.rth_high == pytest.approx(105.0)
        assert result.rth_low == pytest.approx(99.0)

    def test_eth_range_from_pre_rth(self):
        ticks = [
            _make_tick("2026-01-15T06:00:00-05:00", 98.0, 10),
            _make_tick("2026-01-15T08:00:00-05:00", 102.0, 10),
            _make_tick(self._rth_ts("10:00:00"), 100.0, 100),
        ]
        result = build_session_summary("2026-01-15", ticks)
        assert result.eth_high == pytest.approx(102.0)
        assert result.eth_low == pytest.approx(98.0)

    def test_empty_ticks(self):
        result = build_session_summary("2026-01-15", [])
        assert result.date == "2026-01-15"
        assert result.poc == 0.0
        assert result.rth_high is None
        assert result.rth_low is None

    def test_canonical_histogram_keys(self):
        ticks = [
            _make_tick(self._rth_ts("10:00:00"), 100.0, 50),
            _make_tick(self._rth_ts("11:00:00"), 100.25, 30),
        ]
        result = build_session_summary("2026-01-15", ticks)
        # All keys must be "price:.2f" strings
        for key in result.histogram:
            assert isinstance(key, str)
            # Should be parseable as float and re-format to same string
            assert f"{float(key):.2f}" == key


# ---------------------------------------------------------------------------
# Task 5: save_summaries / load_summaries / compute_precomputed_levels
# ---------------------------------------------------------------------------

class TestSummaryIO:
    def test_roundtrip(self):
        summaries = {
            "2026-01-01": _make_summary(
                "2026-01-01",
                poc=100.25,
                vah=101.0,
                val=99.5,
                histogram={"100.00": 50, "100.25": 100},
                rth_high=101.0,
                rth_low=99.5,
                eth_high=101.5,
                eth_low=98.0,
                single_print_zones=[(99.0, 99.25)],
            )
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "sessions.json"
            save_summaries(summaries, path)
            loaded = load_summaries(path)

        s = loaded["2026-01-01"]
        assert s.date == "2026-01-01"
        assert s.poc == pytest.approx(100.25)
        assert s.vah == pytest.approx(101.0)
        assert s.val == pytest.approx(99.5)
        assert s.histogram == {"100.00": 50, "100.25": 100}
        assert s.rth_high == pytest.approx(101.0)
        assert s.rth_low == pytest.approx(99.5)
        assert s.eth_high == pytest.approx(101.5)
        assert s.eth_low == pytest.approx(98.0)
        assert len(s.single_print_zones) == 1
        assert s.single_print_zones[0] == pytest.approx((99.0, 99.25))

    def test_nonexistent_file_returns_empty(self):
        result = load_summaries(Path("/nonexistent/path/sessions.json"))
        assert result == {}


class TestComputePrecomputedLevels:
    def _build_summaries(self, n: int, base_poc: float = 100.0) -> dict[str, SessionSummary]:
        """Build n sessions with incrementing dates starting 2026-01-01."""
        summaries = {}
        for i in range(n):
            date = f"2026-01-{i+1:02d}"
            poc = base_poc + i
            summaries[date] = _make_summary(
                date,
                poc=poc,
                rth_high=poc + 1.0,
                rth_low=poc - 1.0,
                eth_high=poc + 2.0,
                eth_low=poc - 2.0,
                histogram={f"{poc:.2f}": 100},
            )
        return summaries

    def test_all_keys_present(self):
        summaries = self._build_summaries(12)
        current_date = "2026-01-13"
        result = compute_precomputed_levels(summaries, current_date)
        expected_keys = {
            "naked_pocs", "poc_daily", "poc_weekly", "poc_monthly", "poc_macro",
            "globex_high", "globex_low", "overnight_high", "overnight_low",
            "single_print_zones",
        }
        assert set(result.keys()) == expected_keys

    def test_poc_daily_is_previous_session(self):
        summaries = self._build_summaries(5, base_poc=100.0)
        # Sessions: 01-01 poc=100, 01-02 poc=101, ..., 01-05 poc=104
        current_date = "2026-01-06"
        result = compute_precomputed_levels(summaries, current_date)
        # Previous session = 2026-01-05, poc=104.0
        assert result["poc_daily"] == pytest.approx(104.0)

    def test_globex_from_current_session(self):
        summaries = self._build_summaries(5, base_poc=100.0)
        # Add the "current" session (today) with known ETH range
        current_date = "2026-01-06"
        summaries[current_date] = _make_summary(
            current_date,
            poc=105.0,
            eth_high=107.0,
            eth_low=103.0,
            histogram={"105.00": 100},
        )
        result = compute_precomputed_levels(summaries, current_date)
        assert result["globex_high"] == pytest.approx(107.0)
        assert result["globex_low"] == pytest.approx(103.0)
        assert result["overnight_high"] == pytest.approx(107.0)
        assert result["overnight_low"] == pytest.approx(103.0)

    def test_no_prior_sessions_returns_none_pocs(self):
        # Only the current session exists -> no previous data
        current_date = "2026-01-01"
        summaries = {
            current_date: _make_summary(current_date, poc=100.0, eth_high=101.0, eth_low=99.0)
        }
        result = compute_precomputed_levels(summaries, current_date)
        assert result["poc_daily"] is None
        assert result["poc_weekly"] is None
        assert result["poc_monthly"] is None
        assert result["poc_macro"] is None
