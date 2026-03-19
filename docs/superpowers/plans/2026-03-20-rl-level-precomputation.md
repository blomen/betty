# RL Level Precomputation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire up all 26 LevelType values in the RL replay engine by adding a two-pass precomputation system for cross-session levels (naked POCs, multi-TF composite POCs, Globex/overnight HL, single print zones).

**Architecture:** Pass 1 (`rl precompute`) sweeps tick Parquet files and builds per-session summaries (POC, histogram, RTH/ETH ranges, single prints). Pass 2 (`rl replay`) loads summaries and injects cross-session levels into the replay engine before each session. Composite VPs are computed by merging per-session volume histograms.

**Tech Stack:** Python 3.10+, pytest, Parquet (pyarrow), JSON, existing RL pipeline (`backend/src/rl/`)

**Spec:** `docs/superpowers/specs/2026-03-20-rl-level-precomputation-design.md`

---

## File Map

| File | Role | Change |
|---|---|---|
| `backend/src/rl/data/session_store.py` | Session summary dataclass, precompute logic, composite VP, naked POC finder | **NEW** |
| `backend/tests/test_rl_session_store.py` | Tests for all session_store functions | **NEW** |
| `backend/src/rl/data/replay_engine.py` | Accept + use precomputed_levels; naked POC invalidation; Globex HL RTH gate | **MODIFY** |
| `backend/tests/test_rl_replay_engine.py` | Tests for precomputed level injection | **NEW** |
| `backend/src/rl/cli.py` | Add `precompute` command; enhance `replay` to load/pass summaries | **MODIFY** |
| `backend/src/ml/level_touch/backfill.py` | Fix undefined `trades` bug on line 287 | **MODIFY** |

---

### Task 1: Single Print Zone Filtering

**Files:**
- Create: `backend/src/rl/data/session_store.py`
- Test: `backend/tests/test_rl_session_store.py`

- [ ] **Step 1: Write failing tests for filter_single_print_zones**

```python
# backend/tests/test_rl_session_store.py
"""Tests for RL session store — precomputation pipeline."""
import pytest

from src.rl.data.session_store import filter_single_print_zones


class TestFilterSinglePrintZones:
    def test_empty_input_returns_empty(self):
        assert filter_single_print_zones([]) == []

    def test_fewer_than_min_consecutive_returns_empty(self):
        # Two consecutive single prints — below threshold of 3
        singles = [(100.0, 100.0), (100.25, 100.25)]
        assert filter_single_print_zones(singles, tick_size=0.25, min_consecutive=3) == []

    def test_three_consecutive_returns_one_zone(self):
        singles = [(100.0, 100.0), (100.25, 100.25), (100.50, 100.50)]
        result = filter_single_print_zones(singles, tick_size=0.25, min_consecutive=3)
        assert len(result) == 1
        assert result[0] == (100.0, 100.50)

    def test_gap_splits_into_separate_zones(self):
        # Two groups separated by a gap (100.75 missing)
        singles = [
            (100.0, 100.0), (100.25, 100.25), (100.50, 100.50),
            # gap at 100.75
            (101.0, 101.0), (101.25, 101.25), (101.50, 101.50),
        ]
        result = filter_single_print_zones(singles, tick_size=0.25, min_consecutive=3)
        assert len(result) == 2
        assert result[0] == (100.0, 100.50)
        assert result[1] == (101.0, 101.50)

    def test_unsorted_input_still_works(self):
        singles = [(100.50, 100.50), (100.0, 100.0), (100.25, 100.25)]
        result = filter_single_print_zones(singles, tick_size=0.25, min_consecutive=3)
        assert len(result) == 1
        assert result[0] == (100.0, 100.50)

    def test_short_group_filtered_out(self):
        # 4 consecutive + 2 consecutive — only the 4-group passes
        singles = [
            (100.0, 100.0), (100.25, 100.25), (100.50, 100.50), (100.75, 100.75),
            # gap
            (200.0, 200.0), (200.25, 200.25),
        ]
        result = filter_single_print_zones(singles, tick_size=0.25, min_consecutive=3)
        assert len(result) == 1
        assert result[0] == (100.0, 100.75)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_rl_session_store.py -v`
Expected: FAIL — `ImportError: cannot import name 'filter_single_print_zones'`

- [ ] **Step 3: Implement filter_single_print_zones**

```python
# backend/src/rl/data/session_store.py
"""Session summary store for RL precomputation pipeline.

Builds per-session summaries from tick data, enabling cross-session level
computation (naked POCs, composite VPs, Globex HL) without re-reading raw ticks.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field, asdict
from pathlib import Path

log = logging.getLogger(__name__)

TICK_SIZE = 0.25


@dataclass
class SessionSummary:
    """Lightweight summary of one trading session."""
    date: str
    poc: float
    vah: float
    val: float
    histogram: dict[str, int]
    rth_high: float | None = None
    rth_low: float | None = None
    eth_high: float | None = None
    eth_low: float | None = None
    single_print_zones: list[tuple[float, float]] = field(default_factory=list)


def filter_single_print_zones(
    single_prints: list[tuple[float, float]],
    tick_size: float = TICK_SIZE,
    min_consecutive: int = 3,
) -> list[tuple[float, float]]:
    """Group consecutive single-print prices into zones.

    Args:
        single_prints: List of (price, price) tuples from VP single print detection.
        tick_size: Minimum price increment (0.25 for NQ).
        min_consecutive: Minimum consecutive tick levels to form a zone.

    Returns:
        List of (zone_low, zone_high) tuples for significant zones.
    """
    if not single_prints:
        return []

    # Extract unique prices sorted ascending
    prices = sorted({sp[0] for sp in single_prints})
    if not prices:
        return []

    # Walk prices and group consecutive ones (within 1 tick)
    zones: list[tuple[float, float]] = []
    group_start = prices[0]
    group_end = prices[0]

    for i in range(1, len(prices)):
        gap = prices[i] - group_end
        if gap <= tick_size * 1.01:  # epsilon for float comparison
            group_end = prices[i]
        else:
            # End of group — check if long enough
            n_levels = round((group_end - group_start) / tick_size) + 1
            if n_levels >= min_consecutive:
                zones.append((group_start, group_end))
            group_start = prices[i]
            group_end = prices[i]

    # Final group
    n_levels = round((group_end - group_start) / tick_size) + 1
    if n_levels >= min_consecutive:
        zones.append((group_start, group_end))

    return zones
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_rl_session_store.py -v`
Expected: All 6 tests PASS

- [ ] **Step 5: Commit**

```bash
git add backend/src/rl/data/session_store.py backend/tests/test_rl_session_store.py
git commit -m "feat(rl): add filter_single_print_zones for SP zone detection"
```

---

### Task 2: Composite Histogram + POC from Merged VP

**Files:**
- Modify: `backend/src/rl/data/session_store.py`
- Test: `backend/tests/test_rl_session_store.py`

- [ ] **Step 1: Write failing tests for composite_histogram and poc_from_histogram**

Append to `backend/tests/test_rl_session_store.py`:

```python
from src.rl.data.session_store import (
    composite_histogram,
    poc_from_histogram,
    SessionSummary,
)


def _make_summary(date: str, histogram: dict[str, int], poc: float = 0.0) -> SessionSummary:
    """Helper to build a minimal SessionSummary for testing."""
    return SessionSummary(
        date=date, poc=poc, vah=0.0, val=0.0, histogram=histogram,
    )


class TestCompositeHistogram:
    def test_single_session(self):
        s = _make_summary("2025-01-01", {"100.00": 500, "100.25": 300})
        merged = composite_histogram([s])
        assert merged[100.0] == 500
        assert merged[100.25] == 300

    def test_two_sessions_additive(self):
        s1 = _make_summary("2025-01-01", {"100.00": 500, "100.25": 300})
        s2 = _make_summary("2025-01-02", {"100.00": 200, "100.50": 400})
        merged = composite_histogram([s1, s2])
        assert merged[100.0] == 700  # 500 + 200
        assert merged[100.25] == 300
        assert merged[100.50] == 400

    def test_empty_list(self):
        assert composite_histogram([]) == {}


class TestPocFromHistogram:
    def test_basic_poc(self):
        hist = {100.0: 500, 100.25: 1000, 100.50: 300}
        assert poc_from_histogram(hist) == 100.25

    def test_empty_returns_none(self):
        assert poc_from_histogram({}) is None

    def test_tie_returns_highest_volume_price(self):
        # Two equal — max() picks the first seen which is deterministic
        hist = {100.0: 500, 100.25: 500}
        result = poc_from_histogram(hist)
        assert result in (100.0, 100.25)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_rl_session_store.py::TestCompositeHistogram -v`
Expected: FAIL — `ImportError`

- [ ] **Step 3: Implement composite_histogram and poc_from_histogram**

Add to `backend/src/rl/data/session_store.py`:

```python
def composite_histogram(summaries: list[SessionSummary]) -> dict[float, int]:
    """Merge volume histograms from multiple sessions.

    Adds bucket volumes together. The merged histogram can be passed to
    poc_from_histogram() to get the composite POC.
    """
    merged: dict[float, int] = {}
    for s in summaries:
        for price_str, vol in s.histogram.items():
            price = round(float(price_str) / TICK_SIZE) * TICK_SIZE
            merged[price] = merged.get(price, 0) + vol
    return merged


def poc_from_histogram(histogram: dict[float, int]) -> float | None:
    """Find the Point of Control (highest volume price) from a histogram."""
    if not histogram:
        return None
    return max(histogram, key=histogram.__getitem__)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_rl_session_store.py -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add backend/src/rl/data/session_store.py backend/tests/test_rl_session_store.py
git commit -m "feat(rl): add composite histogram merging and POC extraction"
```

---

### Task 3: Naked POC Finder

**Files:**
- Modify: `backend/src/rl/data/session_store.py`
- Test: `backend/tests/test_rl_session_store.py`

- [ ] **Step 1: Write failing tests for find_naked_pocs**

Append to `backend/tests/test_rl_session_store.py`:

```python
from src.rl.data.session_store import find_naked_pocs


class TestFindNakedPocs:
    def _summaries(self) -> dict[str, SessionSummary]:
        """3 sessions: day1 POC=100, day2 POC=200, day3 POC=300."""
        return {
            "2025-01-01": SessionSummary(
                date="2025-01-01", poc=100.0, vah=110.0, val=90.0,
                histogram={}, rth_high=115.0, rth_low=85.0,
            ),
            "2025-01-02": SessionSummary(
                date="2025-01-02", poc=200.0, vah=210.0, val=190.0,
                histogram={}, rth_high=215.0, rth_low=185.0,
            ),
            "2025-01-03": SessionSummary(
                date="2025-01-03", poc=300.0, vah=310.0, val=290.0,
                histogram={}, rth_high=315.0, rth_low=285.0,
            ),
        }

    def test_all_pocs_naked_when_ranges_dont_overlap(self):
        summaries = self._summaries()
        # Querying for day 4 — no session has swept 100, 200, or 300
        result = find_naked_pocs(summaries, "2025-01-04")
        prices = [r["price"] for r in result]
        assert 100.0 in prices
        assert 200.0 in prices
        assert 300.0 in prices

    def test_poc_touched_by_later_session_is_not_naked(self):
        summaries = self._summaries()
        # Day2 RTH range is 185-215, which covers day1 POC=100? No, 100 < 185.
        # Let's make day2 range cover day1's POC
        summaries["2025-01-02"].rth_low = 95.0  # Now 95-215 covers POC=100
        result = find_naked_pocs(summaries, "2025-01-04")
        prices = [r["price"] for r in result]
        assert 100.0 not in prices  # Touched by day2
        assert 200.0 in prices
        assert 300.0 in prices

    def test_no_prior_sessions_returns_empty(self):
        result = find_naked_pocs({}, "2025-01-01")
        assert result == []

    def test_max_lookback_limits_scope(self):
        summaries = self._summaries()
        result = find_naked_pocs(summaries, "2025-01-04", max_lookback_sessions=1)
        # Only looks back 1 session (day3)
        assert len(result) == 1
        assert result[0]["price"] == 300.0

    def test_session_with_none_rth_range_does_not_touch(self):
        summaries = self._summaries()
        summaries["2025-01-02"].rth_high = None
        summaries["2025-01-02"].rth_low = None
        # Day2 can't touch anything
        result = find_naked_pocs(summaries, "2025-01-04")
        assert len(result) == 3
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_rl_session_store.py::TestFindNakedPocs -v`
Expected: FAIL — `ImportError`

- [ ] **Step 3: Implement find_naked_pocs**

Add to `backend/src/rl/data/session_store.py`:

```python
def find_naked_pocs(
    summaries: dict[str, SessionSummary],
    current_date: str,
    max_lookback_sessions: int = 20,
) -> list[dict]:
    """Find prior session POCs not yet revisited by subsequent RTH ranges.

    A POC is 'naked' if no later session's RTH range (low-high) includes it.

    Args:
        summaries: All session summaries keyed by date string.
        current_date: The date we're computing for (exclusive upper bound).
        max_lookback_sessions: How many prior sessions to check.

    Returns:
        List of {"date": str, "price": float} for naked POCs.
    """
    sorted_dates = sorted(d for d in summaries if d < current_date)
    recent = sorted_dates[-max_lookback_sessions:]

    naked: list[dict] = []
    for session_date in recent:
        poc = summaries[session_date].poc
        touched = False
        for later_date in sorted_dates:
            if later_date <= session_date:
                continue
            s = summaries[later_date]
            if s.rth_low is not None and s.rth_high is not None:
                if s.rth_low <= poc <= s.rth_high:
                    touched = True
                    break
        if not touched:
            naked.append({"date": session_date, "price": poc})

    return naked
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_rl_session_store.py -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add backend/src/rl/data/session_store.py backend/tests/test_rl_session_store.py
git commit -m "feat(rl): add naked POC finder using session RTH ranges"
```

---

### Task 4: Build Session Summary from Ticks

**Files:**
- Modify: `backend/src/rl/data/session_store.py`
- Test: `backend/tests/test_rl_session_store.py`

This is the core function that processes a session's ticks into a `SessionSummary`.

- [ ] **Step 1: Write failing tests for build_session_summary**

Append to `backend/tests/test_rl_session_store.py`:

```python
from datetime import datetime, timezone, time
from zoneinfo import ZoneInfo

from src.rl.data.session_store import build_session_summary

ET = ZoneInfo("US/Eastern")


def _make_tick(hour: int, minute: int, price: float, size: int, side: str = "A",
               date_str: str = "2025-01-15") -> dict:
    """Build a tick dict at a specific ET time."""
    y, m, d = (int(x) for x in date_str.split("-"))
    ts = datetime(y, m, d, hour, minute, 0, tzinfo=ET)
    return {"ts": ts, "price": price, "size": size, "side": side}


class TestBuildSessionSummary:
    def test_basic_session_produces_summary(self):
        ticks = [
            _make_tick(9, 30, 100.0, 500),
            _make_tick(9, 31, 100.25, 300),
            _make_tick(9, 32, 100.50, 200),
            _make_tick(10, 0, 100.25, 400),
        ]
        result = build_session_summary("2025-01-15", ticks)
        assert result.date == "2025-01-15"
        assert result.poc > 0
        assert result.vah >= result.val
        assert len(result.histogram) > 0

    def test_rth_range_computed(self):
        ticks = [
            _make_tick(9, 30, 100.0, 100),   # RTH
            _make_tick(12, 0, 105.0, 100),    # RTH high
            _make_tick(15, 0, 95.0, 100),     # RTH low
        ]
        result = build_session_summary("2025-01-15", ticks)
        assert result.rth_high == 105.0
        assert result.rth_low == 95.0

    def test_eth_range_from_pre_rth_ticks(self):
        ticks = [
            _make_tick(3, 0, 110.0, 100),   # ETH (pre-RTH)
            _make_tick(5, 0, 90.0, 100),     # ETH low
            _make_tick(9, 30, 100.0, 100),   # RTH start
        ]
        result = build_session_summary("2025-01-15", ticks)
        assert result.eth_high == 110.0
        assert result.eth_low == 90.0

    def test_empty_ticks_returns_defaults(self):
        result = build_session_summary("2025-01-15", [])
        assert result.poc == 0.0
        assert result.rth_high is None
        assert result.histogram == {}

    def test_histogram_uses_canonical_keys(self):
        ticks = [_make_tick(9, 30, 100.25, 500)]
        result = build_session_summary("2025-01-15", ticks)
        assert "100.25" in result.histogram
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_rl_session_store.py::TestBuildSessionSummary -v`
Expected: FAIL — `ImportError`

- [ ] **Step 3: Implement build_session_summary**

Add to `backend/src/rl/data/session_store.py`:

```python
from datetime import datetime, time
from zoneinfo import ZoneInfo

ET = ZoneInfo("US/Eastern")

# RTH boundaries in ET
_RTH_START = time(9, 30)
_RTH_END = time(16, 0)


def build_session_summary(date_str: str, ticks: list[dict]) -> SessionSummary:
    """Build a SessionSummary from a session's tick data.

    Args:
        date_str: ISO date string, e.g. "2025-01-15".
        ticks: List of tick dicts with keys: ts (datetime), price (float), size (int).

    Returns:
        SessionSummary with VP, RTH/ETH ranges, and single print zones.
    """
    if not ticks:
        return SessionSummary(
            date=date_str, poc=0.0, vah=0.0, val=0.0, histogram={},
        )

    # Use IncrementalVolumeProfile to avoid duplicating VA expansion logic
    from .accumulators import IncrementalVolumeProfile

    vp_acc = IncrementalVolumeProfile(tick_size=TICK_SIZE)
    rth_high: float | None = None
    rth_low: float | None = None
    eth_high: float | None = None
    eth_low: float | None = None

    for tick in ticks:
        price = tick["price"]
        size = tick["size"]
        vp_acc.update(price, size)

        # Classify tick as RTH or ETH
        ts = tick["ts"]
        if hasattr(ts, "astimezone"):
            ts_et = ts.astimezone(ET)
        else:
            continue

        t = ts_et.time()
        if _RTH_START <= t < _RTH_END:
            rth_high = max(rth_high or price, price)
            rth_low = min(rth_low or price, price)
        else:
            eth_high = max(eth_high or price, price)
            eth_low = min(eth_low or price, price)

    vp = vp_acc.get()
    if vp is None:
        return SessionSummary(
            date=date_str, poc=0.0, vah=0.0, val=0.0, histogram={},
        )

    # Extract histogram from accumulator for composite VP merging
    histogram = vp_acc._histogram

    # Single prints from VP, filtered to significant zones
    sp_zones = filter_single_print_zones(vp.single_prints, tick_size=TICK_SIZE)

    # Canonical histogram keys
    canonical_hist = {f"{p:.2f}": v for p, v in histogram.items()}

    return SessionSummary(
        date=date_str,
        poc=vp.poc,
        vah=vp.vah,
        val=vp.val,
        histogram=canonical_hist,
        rth_high=rth_high,
        rth_low=rth_low,
        eth_high=eth_high,
        eth_low=eth_low,
        single_print_zones=sp_zones,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_rl_session_store.py -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add backend/src/rl/data/session_store.py backend/tests/test_rl_session_store.py
git commit -m "feat(rl): add build_session_summary for tick-to-summary conversion"
```

---

### Task 5: Session Summary JSON I/O + compute_precomputed_levels

**Files:**
- Modify: `backend/src/rl/data/session_store.py`
- Test: `backend/tests/test_rl_session_store.py`

- [ ] **Step 1: Write failing tests for save/load and compute_precomputed_levels**

Append to `backend/tests/test_rl_session_store.py`:

```python
from src.rl.data.session_store import (
    save_summaries,
    load_summaries,
    compute_precomputed_levels,
)


class TestSummaryIO:
    def test_roundtrip(self, tmp_path):
        s = SessionSummary(
            date="2025-01-15", poc=100.0, vah=110.0, val=90.0,
            histogram={"100.00": 500, "100.25": 300},
            rth_high=115.0, rth_low=85.0,
            eth_high=120.0, eth_low=80.0,
            single_print_zones=[(95.0, 95.50)],
        )
        path = tmp_path / "summaries.json"
        save_summaries({"2025-01-15": s}, path)
        loaded = load_summaries(path)
        assert "2025-01-15" in loaded
        r = loaded["2025-01-15"]
        assert r.poc == 100.0
        assert r.histogram == {"100.00": 500, "100.25": 300}
        assert r.single_print_zones == [(95.0, 95.50)]

    def test_load_nonexistent_returns_empty(self, tmp_path):
        path = tmp_path / "nope.json"
        result = load_summaries(path)
        assert result == {}


class TestComputePrecomputedLevels:
    def _build_summaries(self) -> dict[str, SessionSummary]:
        """5 sessions with distinct POCs and histograms."""
        summaries = {}
        for i in range(1, 6):
            date = f"2025-01-{i:02d}"
            poc = 100.0 + i * 10  # 110, 120, 130, 140, 150
            summaries[date] = SessionSummary(
                date=date, poc=poc, vah=poc + 5, val=poc - 5,
                histogram={f"{poc:.2f}": 1000, f"{poc - 5:.2f}": 500, f"{poc + 5:.2f}": 500},
                rth_high=poc + 10, rth_low=poc - 10,
                eth_high=poc + 15, eth_low=poc - 15,
                single_print_zones=[(poc + 7, poc + 8)],
            )
        return summaries

    def test_has_all_expected_keys(self):
        summaries = self._build_summaries()
        result = compute_precomputed_levels(summaries, "2025-01-06")
        expected_keys = {
            "naked_pocs", "poc_daily", "poc_weekly", "poc_monthly", "poc_macro",
            "globex_high", "globex_low", "overnight_high", "overnight_low",
            "single_print_zones",
        }
        assert expected_keys == set(result.keys())

    def test_poc_daily_is_previous_session(self):
        summaries = self._build_summaries()
        result = compute_precomputed_levels(summaries, "2025-01-06")
        # Previous session is 2025-01-05 with POC=150
        assert result["poc_daily"] == 150.0

    def test_globex_from_current_session(self):
        summaries = self._build_summaries()
        # Add a "current" session entry
        summaries["2025-01-06"] = SessionSummary(
            date="2025-01-06", poc=160.0, vah=165.0, val=155.0,
            histogram={"160.00": 1000},
            rth_high=170.0, rth_low=150.0,
            eth_high=175.0, eth_low=145.0,
        )
        result = compute_precomputed_levels(summaries, "2025-01-06")
        assert result["globex_high"] == 175.0
        assert result["globex_low"] == 145.0

    def test_no_prior_sessions(self):
        result = compute_precomputed_levels({}, "2025-01-01")
        assert result["naked_pocs"] == []
        assert result["poc_daily"] is None
        assert result["poc_weekly"] is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_rl_session_store.py::TestSummaryIO -v`
Expected: FAIL — `ImportError`

- [ ] **Step 3: Implement save_summaries, load_summaries, compute_precomputed_levels**

Add to `backend/src/rl/data/session_store.py`:

```python
def save_summaries(summaries: dict[str, SessionSummary], path: Path) -> None:
    """Write session summaries to JSON."""
    data = {}
    for date_str, s in summaries.items():
        data[date_str] = {
            "poc": s.poc,
            "vah": s.vah,
            "val": s.val,
            "histogram": s.histogram,
            "rth_high": s.rth_high,
            "rth_low": s.rth_low,
            "eth_high": s.eth_high,
            "eth_low": s.eth_low,
            "single_print_zones": [list(z) for z in s.single_print_zones],
        }
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2)
    log.info("Wrote %d session summaries to %s", len(data), path)


def load_summaries(path: Path) -> dict[str, SessionSummary]:
    """Load session summaries from JSON. Returns empty dict if file not found."""
    if not path.exists():
        return {}
    with open(path) as f:
        data = json.load(f)

    summaries: dict[str, SessionSummary] = {}
    for date_str, d in data.items():
        summaries[date_str] = SessionSummary(
            date=date_str,
            poc=d["poc"],
            vah=d["vah"],
            val=d["val"],
            histogram=d["histogram"],
            rth_high=d.get("rth_high"),
            rth_low=d.get("rth_low"),
            eth_high=d.get("eth_high"),
            eth_low=d.get("eth_low"),
            single_print_zones=[tuple(z) for z in d.get("single_print_zones", [])],
        )
    return summaries


def compute_precomputed_levels(
    summaries: dict[str, SessionSummary],
    current_date: str,
) -> dict:
    """Compute all cross-session levels for injection into the replay engine.

    Args:
        summaries: All session summaries keyed by date.
        current_date: The session date being replayed.

    Returns:
        Dict matching the precomputed_levels schema expected by ReplayEngine.
    """
    sorted_prior = sorted(d for d in summaries if d < current_date)

    # POC Daily: previous session's POC
    poc_daily = summaries[sorted_prior[-1]].poc if sorted_prior else None

    # Composite POCs: merge histograms for different lookback windows
    # Require meaningful session counts: 3 for weekly, 10 for monthly, 10 for macro
    poc_weekly = _composite_poc(summaries, sorted_prior[-5:]) if len(sorted_prior) >= 3 else None
    poc_monthly = _composite_poc(summaries, sorted_prior[-20:]) if len(sorted_prior) >= 10 else None
    poc_macro = _composite_poc(summaries, sorted_prior) if len(sorted_prior) >= 10 else None

    # Naked POCs
    naked_pocs = find_naked_pocs(summaries, current_date)

    # Globex/overnight HL from current session (if summary exists)
    current = summaries.get(current_date)
    globex_high = current.eth_high if current else None
    globex_low = current.eth_low if current else None

    # Single print zones from current session
    sp_zones = current.single_print_zones if current else []

    return {
        "naked_pocs": naked_pocs,
        "poc_daily": poc_daily,
        "poc_weekly": poc_weekly,
        "poc_monthly": poc_monthly,
        "poc_macro": poc_macro,
        "globex_high": globex_high,
        "globex_low": globex_low,
        "overnight_high": globex_high,
        "overnight_low": globex_low,
        "single_print_zones": sp_zones,
    }


def _composite_poc(
    summaries: dict[str, SessionSummary],
    dates: list[str],
) -> float | None:
    """Compute composite POC from a list of session dates."""
    selected = [summaries[d] for d in dates if d in summaries]
    if not selected:
        return None
    merged = composite_histogram(selected)
    return poc_from_histogram(merged)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_rl_session_store.py -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add backend/src/rl/data/session_store.py backend/tests/test_rl_session_store.py
git commit -m "feat(rl): add summary I/O and compute_precomputed_levels"
```

---

### Task 6: Replay Engine — Accept and Use Precomputed Levels

**Files:**
- Modify: `backend/src/rl/data/replay_engine.py`
- Test: `backend/tests/test_rl_replay_engine.py`

- [ ] **Step 1: Write failing tests for precomputed level injection**

```python
# backend/tests/test_rl_replay_engine.py
"""Tests for ReplayEngine precomputed level injection."""
import pytest
from datetime import datetime
from zoneinfo import ZoneInfo

from src.rl.data.replay_engine import ReplayEngine
from src.rl.config import LevelType

ET = ZoneInfo("US/Eastern")


def _make_tick(hour: int, minute: int, price: float, size: int = 10, side: str = "A") -> dict:
    """Build a tick dict at a specific ET time on 2025-01-15."""
    ts = datetime(2025, 1, 15, hour, minute, 0, tzinfo=ET)
    return {"ts": ts, "price": price, "size": size, "side": side}


def _rth_ticks(price: float = 20000.0, count: int = 100) -> list[dict]:
    """Generate simple RTH ticks for testing (09:30-10:00 ET)."""
    ticks = []
    for i in range(count):
        minute = 30 + (i // 4)  # 4 ticks per minute starting 09:30
        if minute >= 60:
            break
        ticks.append(_make_tick(9, minute, price + (i % 5) * 0.25, 10))
    return ticks


class TestReplayEnginePrecomputedLevels:
    def test_precomputed_levels_appear_in_active_levels(self):
        engine = ReplayEngine()
        ticks = _rth_ticks(20000.0, 100)
        session_dt = datetime(2025, 1, 15, 12, 0, 0, tzinfo=ET)

        precomputed = {
            "naked_pocs": [{"date": "2025-01-14", "price": 20001.0}],
            "poc_daily": 19999.0,
            "poc_weekly": 19998.0,
            "poc_monthly": 19997.0,
            "poc_macro": 19996.0,
            "globex_high": 20010.0,
            "globex_low": 19990.0,
            "overnight_high": 20010.0,
            "overnight_low": 19990.0,
            "single_print_zones": [(19995.0, 19995.75)],
        }

        engine.replay_session(ticks, session_dt, precomputed_levels=precomputed)
        snapshot = engine.get_level_snapshot()

        level_types = {lv["type"] for lv in snapshot["active_levels"]}
        assert "naked_poc" in level_types
        assert "poc_daily" in level_types
        assert "poc_weekly" in level_types
        assert "poc_monthly" in level_types
        assert "poc_macro" in level_types
        assert "globex_hl" in level_types
        assert "overnight_hl" in level_types
        assert "single_print" in level_types

    def test_backward_compatible_without_precomputed(self):
        engine = ReplayEngine()
        ticks = _rth_ticks(20000.0, 100)
        session_dt = datetime(2025, 1, 15, 12, 0, 0, tzinfo=ET)

        # Should not raise without precomputed_levels
        episodes = engine.replay_session(ticks, session_dt)
        snapshot = engine.get_level_snapshot()
        # Still has basic levels
        assert len(snapshot["active_levels"]) > 0

    def test_globex_hl_only_active_after_rth_start(self):
        engine = ReplayEngine()
        # Pre-RTH ticks only (08:00-09:00 ET)
        pre_rth_ticks = [_make_tick(8, m, 20000.0 + m * 0.25, 10) for m in range(60)]
        session_dt = datetime(2025, 1, 15, 12, 0, 0, tzinfo=ET)

        precomputed = {
            "naked_pocs": [],
            "poc_daily": None,
            "poc_weekly": None,
            "poc_monthly": None,
            "poc_macro": None,
            "globex_high": 20020.0,
            "globex_low": 19980.0,
            "overnight_high": 20020.0,
            "overnight_low": 19980.0,
            "single_print_zones": [],
        }

        engine.replay_session(pre_rth_ticks, session_dt, precomputed_levels=precomputed)
        snapshot = engine.get_level_snapshot()

        # Globex HL should NOT be in active levels since RTH hasn't started
        level_types = {lv["type"] for lv in snapshot["active_levels"]}
        assert "globex_hl" not in level_types

    def test_naked_poc_invalidated_when_price_sweeps_through(self):
        engine = ReplayEngine()
        # Naked POC at 20001.0. Ticks trade through it during RTH.
        ticks = []
        for i in range(200):
            minute = 30 + (i // 8)
            if minute >= 60:
                break
            # Price oscillates 19999-20003, sweeping through naked POC at 20001
            price = 19999.0 + (i % 5) * 1.0
            ticks.append(_make_tick(9, minute, price, 10))

        session_dt = datetime(2025, 1, 15, 12, 0, 0, tzinfo=ET)
        precomputed = {
            "naked_pocs": [{"date": "2025-01-10", "price": 20001.0}],
            "poc_daily": None, "poc_weekly": None, "poc_monthly": None,
            "poc_macro": None, "globex_high": None, "globex_low": None,
            "overnight_high": None, "overnight_low": None,
            "single_print_zones": [],
        }

        engine.replay_session(ticks, session_dt, precomputed_levels=precomputed)
        snapshot = engine.get_level_snapshot()

        # Naked POC should be removed since price swept through 20001.0
        naked_levels = [lv for lv in snapshot["active_levels"] if lv["type"] == "naked_poc"]
        assert len(naked_levels) == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_rl_replay_engine.py -v`
Expected: FAIL — tests fail because `precomputed_levels` param doesn't exist yet

- [ ] **Step 3: Modify replay_engine.py**

In `backend/src/rl/data/replay_engine.py`, make these changes:

**3a.** Add `precomputed_levels` to `_reset()`:

In the `_reset()` method, after `self._prior_monthly_low: float | None = None`, add:

```python
# Precomputed cross-session levels (injected before replay)
self._precomputed: dict | None = None
```

**3b.** Add `precomputed_levels` param to `replay_session()`:

Change the signature to accept `precomputed_levels: dict | None = None` and store it in `self._precomputed` after `self._reset()`:

```python
if precomputed_levels:
    self._precomputed = precomputed_levels
```

**3c.** Add precomputed levels to `_rebuild_active_levels()`:

After the swing points section (after `_add_optional(levels, "swing_ll", ...)`), add:

```python
# --- Precomputed cross-session levels ---
if self._precomputed:
    # Only inject Globex/overnight HL after RTH has started (avoid look-ahead bias)
    if self._rth_vwap_started:
        _add_optional(levels, "globex_high", LevelType.GLOBEX_HL, self._precomputed.get("globex_high"))
        _add_optional(levels, "globex_low", LevelType.GLOBEX_HL, self._precomputed.get("globex_low"))
        _add_optional(levels, "overnight_high", LevelType.OVERNIGHT_HL, self._precomputed.get("overnight_high"))
        _add_optional(levels, "overnight_low", LevelType.OVERNIGHT_HL, self._precomputed.get("overnight_low"))

    for naked in self._precomputed.get("naked_pocs", []):
        levels.append(("naked_poc", LevelType.NAKED_POC, naked["price"]))

    _add_optional(levels, "poc_daily", LevelType.POC_DAILY, self._precomputed.get("poc_daily"))
    _add_optional(levels, "poc_weekly", LevelType.POC_WEEKLY, self._precomputed.get("poc_weekly"))
    _add_optional(levels, "poc_monthly", LevelType.POC_MONTHLY, self._precomputed.get("poc_monthly"))
    _add_optional(levels, "poc_macro", LevelType.POC_MACRO, self._precomputed.get("poc_macro"))

    for sp_low, sp_high in self._precomputed.get("single_print_zones", []):
        mid = (sp_low + sp_high) / 2.0
        levels.append(("single_print", LevelType.SINGLE_PRINT, mid))
```

**3d.** Add running session high/low tracking and naked POC invalidation:

In `_reset()`, add:
```python
# Running session high/low for naked POC invalidation
self._session_high: float | None = None
self._session_low: float | None = None
```

In the main tick loop of `replay_session()` (after `price: float = tick["price"]`), add:
```python
self._session_high = max(self._session_high or price, price)
self._session_low = min(self._session_low or price, price)
```

At the end of `_on_bar_close()`, before `self._rebuild_active_levels()`, add:

```python
# Invalidate naked POCs that the current session has swept through
if (self._precomputed and self._precomputed.get("naked_pocs")
        and self._session_high is not None):
    self._precomputed["naked_pocs"] = [
        n for n in self._precomputed["naked_pocs"]
        if not (self._session_low <= n["price"] <= self._session_high)
    ]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_rl_replay_engine.py -v`
Expected: All 3 tests PASS

- [ ] **Step 5: Run existing replay engine tests for regression**

Run: `cd backend && python -m pytest tests/test_rl_accumulators.py tests/test_rl_features.py tests/test_rl_episode_builder.py -v`
Expected: All existing tests still PASS

- [ ] **Step 6: Commit**

```bash
git add backend/src/rl/data/replay_engine.py backend/tests/test_rl_replay_engine.py
git commit -m "feat(rl): inject precomputed cross-session levels into replay engine"
```

---

### Task 7: CLI — `rl precompute` Command

**Files:**
- Modify: `backend/src/rl/cli.py`

- [ ] **Step 1: Add the `precompute` command**

Add to `backend/src/rl/cli.py`, after the `verify-levels` command:

```python
# ---------------------------------------------------------------------------
# precompute
# ---------------------------------------------------------------------------

@rl_app.command()
def precompute(
    all_months: bool = typer.Option(False, "--all", help="Process all Parquet files"),
    month: Optional[str] = typer.Option(None, help="Process a specific month YYYY-MM"),
) -> None:
    """Build session summaries from tick data for cross-session level computation.

    Must run before 'rl replay' to enable naked POCs, composite POCs,
    Globex HL, and single print zone levels.
    """
    import pandas as pd

    from src.rl.data.fetcher import TICKS_DIR
    from src.rl.data.session_store import (
        build_session_summary,
        save_summaries,
        load_summaries,
        SessionSummary,
    )

    ticks_dir = TICKS_DIR
    summaries_path = _DATA_DIR / "session_summaries.json"

    # Load existing summaries for incremental mode
    existing = load_summaries(summaries_path)
    typer.echo(f"Loaded {len(existing)} existing session summaries.")

    # Collect Parquet files
    if all_months:
        parquet_files = sorted(ticks_dir.glob("NQ_*.parquet"))
    elif month:
        p = ticks_dir / f"NQ_{month}.parquet"
        if not p.exists():
            typer.echo(f"File not found: {p}", err=True)
            raise typer.Exit(1)
        parquet_files = [p]
    else:
        parquet_files = sorted(ticks_dir.glob("NQ_*.parquet"))

    if not parquet_files:
        typer.echo(f"No Parquet files found in {ticks_dir}", err=True)
        raise typer.Exit(1)

    typer.echo(f"Processing {len(parquet_files)} tick file(s) ...")

    from datetime import datetime as _dt, timezone as _tz
    from zoneinfo import ZoneInfo
    _ET = ZoneInfo("US/Eastern")

    new_count = 0
    for pfile in parquet_files:
        try:
            df = pd.read_parquet(pfile)
        except Exception as exc:
            typer.echo(f"  Skipping {pfile.name}: {exc}")
            continue

        if "timestamp" not in df.columns:
            typer.echo(f"  Skipping {pfile.name}: no 'timestamp' column")
            continue

        # Convert timestamps to timezone-aware ET datetimes
        df["_ts_et"] = pd.to_datetime(df["timestamp"], utc=True).dt.tz_convert(_ET)

        # Assign session dates: subtract 18 hours so that ticks at 18:00+ ET
        # map to the NEXT calendar day's session. The date of (ts - 18h) IS
        # the session date. Example: Mon 18:05 ET → (Mon 00:05) → Mon date,
        # but this is Tue's session... so we need to ADD 1 day.
        # Simpler: just check time >= 18:00 → next business day.
        def _assign_session_date(ts_et):
            """Assign a tick to its trading session date."""
            t = ts_et.time()
            d = ts_et.date()
            if t.hour >= 18:
                # After 18:00 ET → belongs to next business day
                d = d + pd.Timedelta(days=1)
                # Skip Saturday → Monday
                while d.weekday() >= 5:
                    d = d + pd.Timedelta(days=1)
            elif t.hour < 9 or (t.hour == 9 and t.minute < 30):
                # Before 09:30 ET → same calendar date (it's already the session date)
                pass
            # 09:30-17:00 → same calendar date
            # Skip weekends
            if hasattr(d, "weekday") and d.weekday() >= 5:
                return None
            return d

        df["_session_date"] = df["_ts_et"].apply(_assign_session_date)
        df = df.dropna(subset=["_session_date"])

        dates = sorted(df["_session_date"].unique())

        for session_date in dates:
            date_str = str(session_date)[:10]  # "YYYY-MM-DD"

            # Incremental: skip if already computed
            if date_str in existing:
                continue

            day_df = df[df["_session_date"] == session_date]

            # Ensure ts field is timezone-aware datetime (not raw pandas Timestamp)
            day_df = day_df.copy()
            day_df["ts"] = day_df["_ts_et"]  # Use the ET-converted column
            ticks = day_df[["ts", "price", "size", "side"]].to_dict(orient="records")

            if not ticks:
                continue

            summary = build_session_summary(date_str, ticks)
            existing[date_str] = summary
            new_count += 1

        typer.echo(f"  {pfile.name}: processed")

    save_summaries(existing, summaries_path)
    typer.echo(f"\nDone. {new_count} new sessions added. Total: {len(existing)} sessions.")
    typer.echo(f"Saved to: {summaries_path}")
```

- [ ] **Step 2: Verify CLI registration**

Run: `cd backend && python -m src.app rl precompute --help`
Expected: Shows help text with `--all` and `--month` options

- [ ] **Step 3: Commit**

```bash
git add backend/src/rl/cli.py
git commit -m "feat(rl): add 'rl precompute' CLI command"
```

---

### Task 8: CLI — Enhance `rl replay` to Load Summaries

**Files:**
- Modify: `backend/src/rl/cli.py`

- [ ] **Step 1: Add summary loading and precomputed level injection to replay command**

In the `replay()` function in `backend/src/rl/cli.py`, make these changes:

**1a.** After the macro data loading block (around line 212), add:

```python
# Load session summaries for precomputed levels
from src.rl.data.session_store import load_summaries, compute_precomputed_levels

summaries_path = _DATA_DIR / "session_summaries.json"
summaries = load_summaries(summaries_path)
if summaries:
    typer.echo(f"Loaded session summaries: {len(summaries)} sessions.")
else:
    typer.echo("No session_summaries.json found — precomputed levels disabled.")
    typer.echo("Run 'rl precompute' first for full level coverage.")
```

**1b.** Inside the per-session loop, before `engine.replay_session(...)`, compute and pass precomputed levels:

```python
# Compute precomputed levels from summaries
precomputed = None
if summaries:
    precomputed = compute_precomputed_levels(summaries, date_str)

try:
    episodes = engine.replay_session(
        ticks, session_dt,
        prior_session_levels=prior_levels,
        precomputed_levels=precomputed,
    )
```

Where `date_str = str(session_date)` (the ISO date string for the current session).

- [ ] **Step 2: Verify the replay command still runs**

Run: `cd backend && python -m src.app rl replay --help`
Expected: Shows help text (unchanged interface)

- [ ] **Step 3: Commit**

```bash
git add backend/src/rl/cli.py
git commit -m "feat(rl): wire precomputed levels into replay command"
```

---

### Task 9: Fix backfill.py Bug (Line 287)

**Files:**
- Modify: `backend/src/ml/level_touch/backfill.py:273-287`

- [ ] **Step 1: Fix the undefined `trades` variable**

In `backend/src/ml/level_touch/backfill.py`, change the VP + VWAP computation block (lines 273-287):

Before:
```python
    try:
        vp = compute_volume_profile(bars_to_trades(bars))
```

After:
```python
    try:
        trades = bars_to_trades(bars)
        vp = compute_volume_profile(trades)
```

And line 287 (`compute_vwap_bands(trades)`) now works because `trades` is defined.

- [ ] **Step 2: Run existing backfill tests**

Run: `cd backend && python -m pytest tests/test_level_touch_backfill.py -v`
Expected: PASS (or existing failures unrelated to this fix)

- [ ] **Step 3: Commit**

```bash
git add backend/src/ml/level_touch/backfill.py
git commit -m "fix(ml): define trades variable in backfill level computation"
```

---

### Task 10: Update `verify-levels` to Use Precomputed Levels

**Files:**
- Modify: `backend/src/rl/cli.py`

The existing `verify-levels` command does not load session summaries. Without this fix, it will never show the new level types.

- [ ] **Step 1: Add summary loading to verify-levels**

In the `verify_levels()` function in `backend/src/rl/cli.py`, after creating the `ReplayEngine` instance and before calling `engine.replay_session(...)`, add:

```python
# Load precomputed levels if available
from src.rl.data.session_store import load_summaries, compute_precomputed_levels

summaries_path = _DATA_DIR / "session_summaries.json"
summaries = load_summaries(summaries_path)
precomputed = None
if summaries:
    precomputed = compute_precomputed_levels(summaries, date)
    typer.echo(f"Loaded precomputed levels from {len(summaries)} sessions.")
```

Then pass `precomputed_levels=precomputed` to `engine.replay_session(...)`.

- [ ] **Step 2: Commit**

```bash
git add backend/src/rl/cli.py
git commit -m "feat(rl): wire precomputed levels into verify-levels command"
```

---

### Task 11: Integration Smoke Test

**Files:**
- No new files — validates the full pipeline

- [ ] **Step 1: Run the full RL test suite**

Run: `cd backend && python -m pytest tests/test_rl_*.py -v`
Expected: All tests PASS

- [ ] **Step 2: Run precompute on a single month of real data (if available)**

Run: `cd backend && python -m src.app rl precompute --month 2025-09`
Expected: Creates/updates `data/rl/session_summaries.json` with ~20 session entries

- [ ] **Step 3: Run verify-levels on a single date to confirm new level types appear**

Run: `cd backend && python -m src.app rl verify-levels 2025-09-15`
Expected: Active levels output now includes entries with types like `naked_poc`, `poc_daily`, `poc_weekly`, `globex_hl`, etc.

- [ ] **Step 4: Commit all together if any fixups were needed**

```bash
git add -A
git commit -m "test(rl): integration smoke test for precomputed levels"
```
