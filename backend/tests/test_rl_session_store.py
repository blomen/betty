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
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_tick(ts: str, price: float, size: int) -> dict:
    """Build a minimal tick dict."""
    return {"ts_event": ts, "price": price, "size": size}


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
