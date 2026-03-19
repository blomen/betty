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
