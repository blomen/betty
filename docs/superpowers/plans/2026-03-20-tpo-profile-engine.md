# TPO Profile Engine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend the TPO engine with rotation factor, opening type, profile shape (B-shape), excess tick counts, storage for RL backtesting, API endpoints, backfill CLI, and frontend overlay on the L1 CandleChart.

**Architecture:** Extend existing `backend/src/market_data/tpo.py` with new fields on `TPOProfile`, add `build_full_tpo_profile()` as the single entry point, store completed sessions in a new `market_tpo_sessions` table for RL batch access, and render TPO as an orange overlay on the CandleChart with stats in BookSnapshot.

**Tech Stack:** Python 3.10+ / FastAPI / SQLAlchemy / SQLite / Typer CLI | React 19 / TypeScript / lightweight-charts canvas overlay

**Spec:** `docs/superpowers/specs/2026-03-20-tpo-profile-engine-design.md`

---

## File Map

### New Files
| File | Responsibility |
|------|---------------|
| `backend/tests/test_tpo_extended.py` | Tests for all new TPO functions |

### Modified Files
| File | Changes |
|------|---------|
| `backend/src/market_data/tpo.py` | Extend `TPOProfile`, add `_period_letter`, `classify_opening_type`, `build_full_tpo_profile`, update `classify_tpo_shape` (B-shape), update `detect_excess` (tick counts), remove old `compute_rotation_factor`, extract `aggregate_bars_30m` |
| `backend/src/db/models.py` | Add `MarketTPOSession` model |
| `backend/src/services/market_service.py` | Add `get_tpo_live()`, `get_tpo_history()`, `store_tpo_session()`, update `compute_session()` to use `build_full_tpo_profile`, update TPO stub |
| `backend/src/api/routes/market.py` | Add `/tpo` and `/tpo/live` endpoints |
| `backend/src/app.py` | Add `backfill-tpo` CLI command |
| `backend/src/rl/data/replay_engine.py` | Update imports and TPO block to use `build_full_tpo_profile` |
| `frontend/src/types/market.ts` | Add `TPOLiveProfile` interface |
| `frontend/src/services/api.ts` | Add `getTpoLive()`, `getTpoHistory()` |
| `frontend/src/components/Terminal/pages/L1Page.tsx` | Fetch TPO data, pass to children |
| `frontend/src/components/Terminal/pages/BookSnapshot.tsx` | Add TPO stats section with eye-toggles |
| `frontend/src/components/Terminal/pages/CandleChart.tsx` | Add TPO histogram overlay + level lines |

---

## Task 1: Extend TPOProfile dataclass and fix letter overflow

**Files:**
- Modify: `backend/src/market_data/tpo.py:1-21`
- Test: `backend/tests/test_tpo_extended.py` (create)

- [ ] **Step 1: Write failing tests for `_period_letter` and extended TPOProfile**

Create `backend/tests/test_tpo_extended.py`:

```python
"""Tests for extended TPO engine functions."""
import pytest
from src.market_data.tpo import _period_letter, TPOProfile


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
        """46 periods in a full Globex session → letter index 45 = 'AT'."""
        assert _period_letter(45) == "AT"


class TestTPOProfileNewFields:
    def test_new_fields_have_defaults(self):
        """Existing callers that construct TPOProfile positionally still work."""
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_tpo_extended.py -v`
Expected: FAIL — `_period_letter` not importable, `TPOProfile` missing new fields

- [ ] **Step 3: Implement `_period_letter` and extend `TPOProfile`**

In `backend/src/market_data/tpo.py`, add `_period_letter` helper and extend the dataclass:

```python
# Replace the existing TPO_LETTERS line and add:
def _period_letter(index: int) -> str:
    """Convert period index to letter(s): 0→A, 25→Z, 26→AA, 27→AB, ..."""
    if index < 26:
        return chr(65 + index)
    return chr(65 + (index // 26) - 1) + chr(65 + (index % 26))
```

Add new fields to `TPOProfile` dataclass (after existing fields, all with defaults):

```python
from dataclasses import dataclass, field

@dataclass
class TPOProfile:
    # --- Existing fields (unchanged) ---
    letters: dict[float, list[str]]
    poc: float
    vah: float
    val: float
    single_prints: list[float]
    ledges: list[float]
    poor_high: bool
    poor_low: bool
    ib_tpo_count: int

    # --- New fields (all with defaults for backward compat) ---
    tpo_counts: dict[float, int] = field(default_factory=dict)
    ib_high: float = 0.0
    ib_low: float = 0.0
    rotation_factor: int = 0
    profile_shape: str = "balanced"
    opening_type: str = "OA"
    opening_direction: str = "neutral"
    upper_excess: int = 0
    lower_excess: int = 0
    session_high: float = 0.0
    session_low: float = 0.0
```

Also update `compute_tpo_profile` to use `_period_letter(i)` instead of `TPO_LETTERS[i] if i < len(TPO_LETTERS) else TPO_LETTERS[-1]`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_tpo_extended.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/src/market_data/tpo.py backend/tests/test_tpo_extended.py
git commit -m "feat(tpo): extend TPOProfile dataclass with new fields and fix letter overflow"
```

---

## Task 2: Update `compute_tpo_profile` to populate new fields

**Files:**
- Modify: `backend/src/market_data/tpo.py:24-113`
- Test: `backend/tests/test_tpo_extended.py`

- [ ] **Step 1: Write failing tests for new fields in compute_tpo_profile**

Add to `backend/tests/test_tpo_extended.py`:

```python
from src.market_data.tpo import compute_tpo_profile


def _make_bar(o, h, l, c, v=100):
    return {"open": o, "high": h, "low": l, "close": c, "volume": v}


class TestComputeTPOProfileExtended:
    def test_tpo_counts_populated(self):
        bars = [_make_bar(100, 101, 99, 100.5)]
        profile = compute_tpo_profile(bars, tick_size=0.5)
        # prices: 99.0, 99.5, 100.0, 100.5, 101.0 → each has 1 TPO
        assert profile.tpo_counts[100.0] == 1

    def test_ib_high_low_from_first_two_periods(self):
        bars = [
            _make_bar(100, 105, 98, 103),   # A
            _make_bar(103, 107, 101, 106),   # B
            _make_bar(106, 110, 104, 109),   # C
        ]
        profile = compute_tpo_profile(bars, tick_size=0.25)
        assert profile.ib_high == 107.0  # max high of A,B
        assert profile.ib_low == 98.0    # min low of A,B

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
        # 3 bars: A covers 100-106, B covers 100-104, C covers 100-102
        # Top prices (106, 105.75, etc.) only have letter A → single prints at top
        bars = [
            _make_bar(100, 106, 100, 105),
            _make_bar(100, 104, 100, 103),
            _make_bar(100, 102, 100, 101),
        ]
        profile = compute_tpo_profile(bars, tick_size=0.25)
        assert profile.upper_excess > 0  # at least some single prints at top

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
        """27 bars should use letters A-Z then AA."""
        bars = [_make_bar(100, 100.25, 100, 100.25) for _ in range(27)]
        profile = compute_tpo_profile(bars, tick_size=0.25)
        assert "AA" in profile.letters[100.0]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_tpo_extended.py::TestComputeTPOProfileExtended -v`
Expected: FAIL — new fields not populated yet

- [ ] **Step 3: Update `compute_tpo_profile` to populate new fields**

In `backend/src/market_data/tpo.py`, modify `compute_tpo_profile`:

After the existing code that builds `letters`, `poc`, `vah`, `val`, `single_prints`, `ledges`, `poor_high`, `poor_low`, `ib_tpo_count`, add:

```python
    # TPO counts (derived from letters)
    tpo_counts = {p: len(v) for p, v in letters.items()}

    # IB from first 2 bars
    ib_bars = bars_30m[:2] if len(bars_30m) >= 2 else bars_30m
    ib_high = max(b["high"] for b in ib_bars) if ib_bars else 0.0
    ib_low = min(b["low"] for b in ib_bars) if ib_bars else 0.0

    # Session range
    session_high = max(b["high"] for b in bars_30m)
    session_low = min(b["low"] for b in bars_30m)

    # Excess: consecutive single-print levels at extremes
    upper_excess = 0
    for p in reversed(sorted_prices):
        if len(letters[p]) == 1:
            upper_excess += 1
        else:
            break

    lower_excess = 0
    for p in sorted_prices:
        if len(letters[p]) == 1:
            lower_excess += 1
        else:
            break
```

Pass these into the `TPOProfile` constructor. Also update the letter assignment line:
```python
letter = _period_letter(i)  # was: TPO_LETTERS[i] if i < len(TPO_LETTERS) else TPO_LETTERS[-1]
```

Update the empty-bars early return to include new fields with defaults.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_tpo_extended.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add backend/src/market_data/tpo.py backend/tests/test_tpo_extended.py
git commit -m "feat(tpo): populate tpo_counts, IB, session range, excess in compute_tpo_profile"
```

---

## Task 3: B-shape detection and `detect_excess` tick counts

**Files:**
- Modify: `backend/src/market_data/tpo.py:116-199`
- Test: `backend/tests/test_tpo_extended.py`

- [ ] **Step 1: Write failing tests for B-shape and updated detect_excess**

Add to `backend/tests/test_tpo_extended.py`:

```python
from src.market_data.tpo import classify_tpo_shape, detect_excess


class TestClassifyTPOShapeBShape:
    def test_double_distribution_is_B_shape(self):
        """Two peaks with a valley between them → B-shape."""
        # Build a profile with two clusters: one around 100, one around 110
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
        """Normal bell curve → not B-shape."""
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
        shape = classify_tpo_shape(profile)
        assert shape != "B-shape"


class TestDetectExcessTickCounts:
    def test_returns_int_counts(self):
        """detect_excess now returns (int, int) not (bool, bool)."""
        letters = {
            100.0: ["A"],          # single print at bottom
            100.25: ["A"],         # single print
            100.5: ["A", "B", "C"],
            100.75: ["A", "B"],
            101.0: ["A"],          # single print at top
        }
        profile = TPOProfile(
            letters=letters, poc=100.5, vah=100.75, val=100.25,
            single_prints=[100.0, 100.25, 101.0], ledges=[],
            poor_high=False, poor_low=False, ib_tpo_count=0,
        )
        upper, lower = detect_excess(profile)
        assert isinstance(upper, int)
        assert isinstance(lower, int)
        assert upper == 1   # 101.0 only
        assert lower == 2   # 100.0, 100.25

    def test_truthy_compat(self):
        """Non-zero int is truthy, so existing `if excess_high:` still works."""
        letters = {100.0: ["A"], 100.25: ["A", "B"]}
        profile = TPOProfile(
            letters=letters, poc=100.25, vah=100.25, val=100.0,
            single_prints=[100.0], ledges=[],
            poor_high=False, poor_low=False, ib_tpo_count=0,
        )
        upper, lower = detect_excess(profile)
        assert not upper  # 100.25 at top has 2 letters
        assert lower      # 100.0 at bottom has 1 letter
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_tpo_extended.py::TestClassifyTPOShapeBShape tests/test_tpo_extended.py::TestDetectExcessTickCounts -v`
Expected: FAIL

- [ ] **Step 3: Implement B-shape detection and update detect_excess**

In `backend/src/market_data/tpo.py`:

Update `classify_tpo_shape` — add B-shape check BEFORE existing p/b/d checks:

```python
def classify_tpo_shape(profile: TPOProfile) -> str:
    if not profile.letters:
        return "balanced"

    sorted_prices = sorted(profile.letters.keys())
    n = len(sorted_prices)
    total_tpos = sum(len(v) for v in profile.letters.values())

    if total_tpos == 0:
        return "balanced"

    # B-shape detection: two peaks with valley between them
    counts = [len(profile.letters[p]) for p in sorted_prices]
    peak_count = max(counts)

    if peak_count >= 3 and n >= 10:
        # Scan for valley below 40% of peak
        valley_threshold = peak_count * 0.40
        peak_threshold = peak_count * 0.60

        for i in range(2, n - 2):
            if counts[i] <= valley_threshold:
                # Check for peaks on both sides
                left_peak = max(counts[:i])
                right_peak = max(counts[i + 1:])
                if left_peak >= peak_threshold and right_peak >= peak_threshold:
                    return "B-shape"

    # Existing logic unchanged
    midpoint = (sorted_prices[0] + sorted_prices[-1]) / 2
    above_count = sum(len(profile.letters[p]) for p in sorted_prices if p > midpoint)
    below_count = sum(len(profile.letters[p]) for p in sorted_prices if p < midpoint)

    if above_count / total_tpos > 0.65:
        return "p-shape"
    if below_count / total_tpos > 0.65:
        return "b-shape"
    if n > 30:
        return "d-shape"
    return "balanced"
```

Update `detect_excess` to return `tuple[int, int]`:

```python
def detect_excess(profile: TPOProfile) -> tuple[int, int]:
    """Detect excess at session extremes. Returns (upper_ticks, lower_ticks)."""
    if not profile.letters:
        return (0, 0)

    sorted_prices = sorted(profile.letters.keys())

    upper = 0
    for p in reversed(sorted_prices):
        if len(profile.letters[p]) == 1:
            upper += 1
        else:
            break

    lower = 0
    for p in sorted_prices:
        if len(profile.letters[p]) == 1:
            lower += 1
        else:
            break

    return (upper, lower)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_tpo_extended.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add backend/src/market_data/tpo.py backend/tests/test_tpo_extended.py
git commit -m "feat(tpo): add B-shape detection and update detect_excess to return tick counts"
```

---

## Task 4: Opening type classification

**Files:**
- Modify: `backend/src/market_data/tpo.py`
- Test: `backend/tests/test_tpo_extended.py`

- [ ] **Step 1: Write failing tests for `classify_opening_type`**

Add to `backend/tests/test_tpo_extended.py`:

```python
from src.market_data.tpo import classify_opening_type


class TestClassifyOpeningType:
    def test_open_drive_up(self):
        """A opens at low, B extends up, C holds → OD up."""
        bars = [
            _make_bar(100, 105, 99, 104),    # A: strong up
            _make_bar(104, 110, 103, 109),    # B: extends up, no retrace below A.low
            _make_bar(109, 112, 107, 111),    # C: continues, no retrace > 50%
            _make_bar(111, 113, 110, 112),    # D
        ]
        otype, direction = classify_opening_type(bars)
        assert otype == "OD"
        assert direction == "up"

    def test_open_test_drive(self):
        """A up, B retraces into A, C drives above A's high → OTD."""
        bars = [
            _make_bar(100, 105, 99, 104),    # A: up
            _make_bar(104, 104, 100, 101),   # B: retraces into A, but not below A.low
            _make_bar(101, 108, 101, 107),   # C: drives above A.high (105)
            _make_bar(107, 109, 106, 108),   # D
        ]
        otype, direction = classify_opening_type(bars)
        assert otype == "OTD"
        assert direction == "up"

    def test_open_rejection_reverse(self):
        """A up, B extends up, C/D reverse below A.low → ORR."""
        bars = [
            _make_bar(100, 105, 99, 104),    # A: up
            _make_bar(104, 108, 103, 107),   # B: extends A's direction
            _make_bar(107, 107, 97, 98),     # C: reverses, breaks below A.low (99)
            _make_bar(98, 99, 95, 96),       # D: continues down
        ]
        otype, direction = classify_opening_type(bars)
        assert otype == "ORR"

    def test_open_auction(self):
        """Overlapping, rotational periods → OA."""
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_tpo_extended.py::TestClassifyOpeningType -v`
Expected: FAIL — `classify_opening_type` not defined

- [ ] **Step 3: Implement `classify_opening_type`**

Add to `backend/src/market_data/tpo.py`:

```python
def classify_opening_type(bars_30m: list[dict]) -> tuple[str, str]:
    """Classify the session opening type from first 4 periods.

    Returns (opening_type, direction) where:
    - opening_type: "OD", "OTD", "ORR", "OA"
    - direction: "up", "down", "neutral"
    """
    if len(bars_30m) < 4:
        return ("OA", "neutral")

    a, b, c, d = bars_30m[0], bars_30m[1], bars_30m[2], bars_30m[3]

    # Direction of period A
    if a["close"] > a["open"]:
        a_dir = "up"
    elif a["close"] < a["open"]:
        a_dir = "down"
    else:
        return ("OA", "neutral")

    session_range = max(b["high"] for b in bars_30m[:4]) - min(b["low"] for b in bars_30m[:4])
    if session_range == 0:
        return ("OA", "neutral")

    ab_range = max(a["high"], b["high"]) - min(a["low"], b["low"])

    if a_dir == "up":
        # OD: A opens near low, B extends up without retracing
        a_opens_near_extreme = (a["open"] - min(x["low"] for x in bars_30m[:4])) / session_range <= 0.25
        b_extends = b["high"] > a["high"] and b["low"] >= a["low"]
        c_holds = c["low"] >= min(a["low"], b["low"]) + ab_range * 0.50 if ab_range > 0 else False

        if a_opens_near_extreme and b_extends and c_holds:
            return ("OD", "up")

        # OTD: B retraces into A but not below A.low, C drives above A.high
        b_retraces = b["low"] < a["high"] and b["low"] >= a["low"]
        c_drives = c["high"] > a["high"]

        if b_retraces and c_drives:
            return ("OTD", "up")

        # ORR: B extends A's direction, C or D reverses below A.low
        b_continues = b["high"] >= a["high"]
        cd_reverses = c["low"] < a["low"] or d["low"] < a["low"]

        if b_continues and cd_reverses:
            return ("ORR", "down")

    else:  # a_dir == "down"
        a_opens_near_extreme = (max(x["high"] for x in bars_30m[:4]) - a["open"]) / session_range <= 0.25
        b_extends = b["low"] < a["low"] and b["high"] <= a["high"]
        c_holds = c["high"] <= max(a["high"], b["high"]) - ab_range * 0.50 if ab_range > 0 else False

        if a_opens_near_extreme and b_extends and c_holds:
            return ("OD", "down")

        b_retraces = b["high"] > a["low"] and b["high"] <= a["high"]
        c_drives = c["low"] < a["low"]

        if b_retraces and c_drives:
            return ("OTD", "down")

        b_continues = b["low"] <= a["low"]
        cd_reverses = c["high"] > a["high"] or d["high"] > a["high"]

        if b_continues and cd_reverses:
            return ("ORR", "up")

    return ("OA", a_dir)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_tpo_extended.py::TestClassifyOpeningType -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add backend/src/market_data/tpo.py backend/tests/test_tpo_extended.py
git commit -m "feat(tpo): add opening type classification (OD/OTD/ORR/OA)"
```

---

## Task 5: `build_full_tpo_profile`, remove old RF, extract `aggregate_bars_30m`

**Files:**
- Modify: `backend/src/market_data/tpo.py`
- Test: `backend/tests/test_tpo_extended.py`

- [ ] **Step 1: Write failing tests**

Add to `backend/tests/test_tpo_extended.py`:

```python
from src.market_data.tpo import build_full_tpo_profile, aggregate_bars_30m


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

        # Core profile fields
        assert profile.poc > 0
        assert profile.vah >= profile.poc >= profile.val or profile.vah >= profile.val

        # New enriched fields
        assert isinstance(profile.rotation_factor, int)
        assert profile.profile_shape in ("p-shape", "b-shape", "d-shape", "balanced", "B-shape")
        assert profile.opening_type in ("OD", "OTD", "ORR", "OA")
        assert profile.opening_direction in ("up", "down", "neutral")
        assert profile.ib_high == 112.0  # max(108, 112)
        assert profile.ib_low == 98.0    # min(98, 104)
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
        # 60 fake BarData-like objects
        class FakeBar:
            def __init__(self, i):
                self.open = 100 + i * 0.1
                self.high = 100 + i * 0.1 + 0.5
                self.low = 100 + i * 0.1 - 0.5
                self.close = 100 + i * 0.1 + 0.2
                self.volume = 10

        bars = [FakeBar(i) for i in range(60)]
        result = aggregate_bars_30m(bars)
        assert len(result) == 2  # 60 / 30 = 2 chunks
        assert "high" in result[0]
        assert "low" in result[0]
        assert result[0]["volume"] == 300  # 30 * 10

    def test_partial_chunk_dropped(self):
        class FakeBar:
            def __init__(self):
                self.open = self.high = self.low = self.close = 100
                self.volume = 10

        bars = [FakeBar() for _ in range(45)]
        result = aggregate_bars_30m(bars)
        assert len(result) == 1  # only first 30, tail of 15 dropped
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_tpo_extended.py::TestBuildFullTPOProfile tests/test_tpo_extended.py::TestAggregateBars30m -v`
Expected: FAIL — functions not defined

- [ ] **Step 3: Implement `build_full_tpo_profile` and `aggregate_bars_30m`, remove old RF**

In `backend/src/market_data/tpo.py`:

Add `aggregate_bars_30m` (extracted from `MarketService._aggregate_bars_30m`):

```python
def aggregate_bars_30m(bars) -> list[dict]:
    """Aggregate 1-min BarData objects into 30-min OHLCV dicts.

    Each bar must have .open, .high, .low, .close, .volume attributes.
    Partial trailing chunks (< 30 bars) are dropped.
    """
    result = []
    chunk = []
    for b in bars:
        chunk.append(b)
        if len(chunk) == 30:
            result.append({
                "high": max(c.high for c in chunk),
                "low": min(c.low for c in chunk),
                "open": chunk[0].open,
                "close": chunk[-1].close,
                "volume": sum(c.volume for c in chunk),
            })
            chunk = []
    return result
```

Add `build_full_tpo_profile`:

```python
from .metrics import compute_rotation_factor as _metrics_rf


def build_full_tpo_profile(
    bars_30m: list[dict],
    tick_size: float = 0.25,
) -> TPOProfile:
    """Build a fully enriched TPO profile from 30-min bars.

    Single entry point: computes base profile, rotation factor,
    profile shape, opening type, and excess. Used by live endpoint,
    backfill CLI, and replay engine.
    """
    profile = compute_tpo_profile(bars_30m, tick_size=tick_size)

    if not bars_30m:
        return profile

    # Rotation factor (signed, from metrics.py)
    highs = [b["high"] for b in bars_30m]
    lows = [b["low"] for b in bars_30m]
    profile.rotation_factor = _metrics_rf(highs, lows)

    # Profile shape
    profile.profile_shape = classify_tpo_shape(profile)

    # Opening type
    profile.opening_type, profile.opening_direction = classify_opening_type(bars_30m)

    # Excess (already computed in compute_tpo_profile, but re-derive via detect_excess for consistency)
    upper_ex, lower_ex = detect_excess(profile)
    profile.upper_excess = upper_ex
    profile.lower_excess = lower_ex

    return profile
```

**Delete** the old `compute_rotation_factor` function from `tpo.py` (lines 149-172). Also remove the now-unused `TPO_LETTERS` constant if no longer referenced.

- [ ] **Step 4: Run all tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_tpo_extended.py -v`
Expected: ALL PASS

Also run existing TPO tests to make sure nothing is broken:
Run: `cd backend && python -m pytest tests/ -k tpo -v`
Expected: ALL PASS (the old `test_rl_tpo_extensions.py` may need import updates — see Task 6)

- [ ] **Step 5: Commit**

```bash
git add backend/src/market_data/tpo.py backend/tests/test_tpo_extended.py
git commit -m "feat(tpo): add build_full_tpo_profile, aggregate_bars_30m, remove old RF"
```

---

## Task 6: Update callers (market_service, replay_engine, existing tests)

**Files:**
- Modify: `backend/src/services/market_service.py:22,278,742-750,1370-1389`
- Modify: `backend/src/rl/data/replay_engine.py:31-36,505-526`
- Modify: `backend/tests/test_rl_tpo_extensions.py` (update imports if needed)

- [ ] **Step 1: Update `market_service.py` imports and `compute_session`**

At line 22, change:
```python
# Old:
from ..market_data.tpo import compute_tpo_profile
# New:
from ..market_data.tpo import build_full_tpo_profile, aggregate_bars_30m
```

At line 278, change:
```python
# Old:
tpo = compute_tpo_profile(bars_30m)
# New:
tpo = build_full_tpo_profile(bars_30m)
```

At the `_aggregate_bars_30m` static method (around line 1370), replace with a call to the module-level function:
```python
@staticmethod
def _aggregate_bars_30m(bars) -> list[dict]:
    return aggregate_bars_30m(bars)
```

At lines 742-750 (TPO stub), add new fields with defaults:
```python
tpo = TPOProfile(
    letters={}, poc=sj.get("poc", 0) or 0,
    vah=sj.get("vah", 0) or 0, val=sj.get("val", 0) or 0,
    single_prints=[], ledges=[],
    poor_high=sj.get("poor_high", False),
    poor_low=sj.get("poor_low", False),
    ib_tpo_count=session_row.ib_tpo_count or 0,
    # New fields use dataclass defaults (rotation_factor=0, etc.)
)
```
No change needed here since new fields have defaults — the existing construction still works.

- [ ] **Step 2: Update `replay_engine.py`**

At lines 31-36, change imports:
```python
# Old:
from ...market_data.tpo import (
    compute_tpo_profile,
    classify_tpo_shape,
    compute_rotation_factor,
    detect_excess,
)
# New:
from ...market_data.tpo import build_full_tpo_profile
```

At lines 505-526, replace the TPO block:
```python
        # TPO profile from 30m bars
        tpo_profile_dict: dict | None = None
        if bars_30m:
            profile = build_full_tpo_profile(bars_30m, tick_size=TICK_SIZE)
            tpo_profile_dict = {
                "poc": profile.poc,
                "vah": profile.vah,
                "val": profile.val,
                "shape": profile.profile_shape,
                "rotation_factor": profile.rotation_factor,
                "rotation_count": profile.rotation_factor,
                "excess_high": profile.upper_excess > 0,
                "excess_low": profile.lower_excess > 0,
                "upper_excess_ticks": profile.upper_excess,
                "lower_excess_ticks": profile.lower_excess,
                "poor_high": profile.poor_high,
                "poor_low": profile.poor_low,
                "single_prints": profile.single_prints,
                "ledges": profile.ledges,
                "ib_tpo_count": profile.ib_tpo_count,
                "opening_type": profile.opening_type,
                "opening_direction": profile.opening_direction,
                "ib_high": profile.ib_high,
                "ib_low": profile.ib_low,
            }
```

- [ ] **Step 3: Update existing test imports and assertions**

In `backend/tests/test_rl_tpo_extensions.py`:

1. Update imports — remove `compute_rotation_factor` from `tpo.py` import, use `metrics.py` version if needed
2. **Fix `detect_excess` assertions** — change all `is True` / `is False` identity checks to `==` or truthiness checks, since `detect_excess` now returns `int` not `bool`:
   - `assert excess_high is True` → `assert excess_high > 0`
   - `assert excess_high is False` → `assert excess_high == 0`
   - Same for `excess_low`
   There are 8 assertions at lines 136, 137, 147, 158, 166, 172, 182, 183 that need updating.

- [ ] **Step 4: Run full test suite**

Run: `cd backend && python -m pytest tests/ -v --tb=short`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add backend/src/services/market_service.py backend/src/rl/data/replay_engine.py backend/tests/
git commit -m "refactor(tpo): migrate callers to build_full_tpo_profile"
```

---

## Task 7: DB model and storage

**Files:**
- Modify: `backend/src/db/models.py`
- Modify: `backend/src/services/market_service.py`

- [ ] **Step 1: Add `MarketTPOSession` model to `db/models.py`**

Add after the `MarketLevel` class (around line 1230):

```python
class MarketTPOSession(Base):
    """Pre-computed TPO profile for a Globex session."""
    __tablename__ = "market_tpo_sessions"

    id = Column(Integer, primary_key=True)
    symbol = Column(String, nullable=False)
    date = Column(String, nullable=False)  # YYYY-MM-DD (Globex open date, ET)
    poc = Column(Float, nullable=False)
    vah = Column(Float, nullable=False)
    val = Column(Float, nullable=False)
    ib_high = Column(Float, nullable=True)
    ib_low = Column(Float, nullable=True)
    rotation_factor = Column(Integer, nullable=True)
    profile_shape = Column(String, nullable=True)     # "b-shape", "p-shape", "d-shape", "balanced", "B-shape"
    opening_type = Column(String, nullable=True)       # "OD", "OTD", "ORR", "OA"
    opening_direction = Column(String, nullable=True)  # "up", "down", "neutral"
    upper_excess = Column(Integer, default=0)
    lower_excess = Column(Integer, default=0)
    session_high = Column(Float, nullable=True)
    session_low = Column(Float, nullable=True)
    session_json = Column(String, nullable=False)      # Full TPOProfile as JSON
    created_at = Column(DateTime, default=_utcnow)

    __table_args__ = (
        UniqueConstraint("symbol", "date", name="uq_market_tpo_session"),
        Index("ix_market_tpo_sessions_symbol_date", "symbol", "date"),
    )
```

- [ ] **Step 2: Add `store_tpo_session` and `get_tpo_history` to market_service.py**

Add methods to `MarketService`:

Note: `MarketService` uses `self.db` (the SQLAlchemy session injected via `__init__`), NOT `self.session_factory()`. Follow the same pattern as all other methods in the class.

```python
import json
from dataclasses import asdict

def store_tpo_session(self, profile: TPOProfile, symbol: str, date_str: str):
    """Store a completed TPO session profile to the DB."""
    from ..db.models import MarketTPOSession
    session_json = json.dumps(asdict(profile), default=str)

    existing = self.db.query(MarketTPOSession).filter_by(symbol=symbol, date=date_str).first()
    if existing:
        existing.poc = profile.poc
        existing.vah = profile.vah
        existing.val = profile.val
        existing.ib_high = profile.ib_high
        existing.ib_low = profile.ib_low
        existing.rotation_factor = profile.rotation_factor
        existing.profile_shape = profile.profile_shape
        existing.opening_type = profile.opening_type
        existing.opening_direction = profile.opening_direction
        existing.upper_excess = profile.upper_excess
        existing.lower_excess = profile.lower_excess
        existing.session_high = profile.session_high
        existing.session_low = profile.session_low
        existing.session_json = session_json
    else:
        self.db.add(MarketTPOSession(
            symbol=symbol, date=date_str,
            poc=profile.poc, vah=profile.vah, val=profile.val,
            ib_high=profile.ib_high, ib_low=profile.ib_low,
            rotation_factor=profile.rotation_factor,
            profile_shape=profile.profile_shape,
            opening_type=profile.opening_type,
            opening_direction=profile.opening_direction,
            upper_excess=profile.upper_excess,
            lower_excess=profile.lower_excess,
            session_high=profile.session_high,
            session_low=profile.session_low,
            session_json=session_json,
        ))
    self.db.commit()

def get_tpo_history(self, symbol: str = "NQ", days: int = 30) -> list[dict]:
    """Fetch historical TPO sessions for RL batch access."""
    from ..db.models import MarketTPOSession
    rows = (
        self.db.query(MarketTPOSession)
        .filter_by(symbol=symbol)
        .order_by(MarketTPOSession.date.desc())
        .limit(days)
        .all()
    )
    result = []
    for row in reversed(rows):  # chronological order
        data = json.loads(row.session_json)
        data["date"] = row.date
        result.append(data)
    return result
```

- [ ] **Step 3: Hook auto-store into `compute_session`**

In `compute_session()` (around line 278, after `tpo = build_full_tpo_profile(bars_30m)`), add:

```python
        # Store TPO session for RL batch access
        try:
            self.store_tpo_session(tpo, symbol, target_date)
        except Exception:
            logger.warning("Failed to store TPO session for %s/%s", symbol, target_date, exc_info=True)
```

- [ ] **Step 4: Run tests**

Run: `cd backend && python -m pytest tests/ -v --tb=short`
Expected: PASS (the table gets created via `init_db()` / `Base.metadata.create_all()`)

- [ ] **Step 5: Commit**

```bash
git add backend/src/db/models.py backend/src/services/market_service.py
git commit -m "feat(tpo): add MarketTPOSession model and auto-store on session compute"
```

---

## Task 8: API endpoints

**Files:**
- Modify: `backend/src/api/routes/market.py`
- Modify: `backend/src/services/market_service.py`

- [ ] **Step 1: Add `get_tpo_live` to market_service.py**

**Important:** `MarketRepo.get_candles(symbol, interval, start: datetime, end: datetime)` takes datetime objects, NOT string dates. Follow the same pattern used in `get_developing_vwap` and `get_candles`.

```python
_tpo_cache: dict[str, tuple[float, dict]] = {}

def get_tpo_live(self, symbol: str = "NQ") -> dict:
    """Compute today's developing TPO profile on the fly. Cached 60s."""
    import time
    from dataclasses import asdict
    cache_key = f"tpo_live_{symbol}"
    now = time.time()

    cached = _tpo_cache.get(cache_key)
    if cached and now - cached[0] < 60:
        return cached[1]

    # Fetch today's 1m candles (same pattern as get_developing_vwap)
    from datetime import datetime, timezone, timedelta
    end_dt = datetime.now(timezone.utc)
    start_dt = end_dt.replace(hour=0, minute=0, second=0) - timedelta(hours=6)  # cover Globex open

    repo = MarketRepo(self.db)
    rows = repo.get_candles(symbol, "1m", start_dt, end_dt)
    bars_30m = aggregate_bars_30m(rows)

    profile = build_full_tpo_profile(bars_30m, tick_size=0.25)
    result = asdict(profile)
    result["date"] = end_dt.strftime("%Y-%m-%d")

    _tpo_cache[cache_key] = (now, result)
    return result
```

- [ ] **Step 2: Add API routes to `market.py`**

Add to `backend/src/api/routes/market.py`. Use `Depends(_svc)` injection (the existing pattern — NOT `_get_svc()`):

```python
@router.get("/tpo")
async def get_tpo_history(
    symbol: str = Query("NQ"),
    days: int = Query(30, ge=1, le=365),
    svc: MarketService = Depends(_svc),
):
    """Historical TPO sessions for RL batch access."""
    sessions = svc.get_tpo_history(symbol=symbol, days=days)
    return {"sessions": sessions, "symbol": symbol, "count": len(sessions)}


@router.get("/tpo/live")
async def get_tpo_live(
    symbol: str = Query("NQ"),
    svc: MarketService = Depends(_svc),
):
    """Today's developing TPO profile."""
    return svc.get_tpo_live(symbol=symbol)
```

- [ ] **Step 3: Test the endpoints manually**

Run: `curl http://localhost:8000/api/trading/market/tpo/live?symbol=NQ`
Expected: JSON with TPO profile data (or empty profile if no candles today)

Run: `curl http://localhost:8000/api/trading/market/tpo?symbol=NQ&days=5`
Expected: JSON with `{"sessions": [...], "symbol": "NQ", "count": N}`

- [ ] **Step 4: Commit**

```bash
git add backend/src/api/routes/market.py backend/src/services/market_service.py
git commit -m "feat(tpo): add /tpo and /tpo/live API endpoints"
```

---

## Task 9: Backfill CLI command

**Files:**
- Modify: `backend/src/app.py`

- [ ] **Step 1: Add `backfill-tpo` command**

Add to `backend/src/app.py`:

```python
@app.command()
def backfill_tpo(
    days: int = typer.Option(90, help="Number of days to backfill"),
    symbol: str = typer.Option("NQ", help="Symbol"),
):
    """Backfill TPO session profiles from stored 1m candles."""
    from datetime import date, timedelta
    from src.db.models import init_db, get_session_factory, MarketTPOSession
    from src.market_data.tpo import build_full_tpo_profile, aggregate_bars_30m
    from src.repositories.market_repo import MarketRepo

    init_db()
    session_factory = get_session_factory()

    end_date = date.today()
    start_date = end_date - timedelta(days=days)

    with session_factory() as db:
        repo = MarketRepo(db)
        current = start_date
        stored = 0
        skipped = 0

        while current <= end_date:
            date_str = current.isoformat()

            # Skip if already exists
            existing = db.query(MarketTPOSession).filter_by(
                symbol=symbol, date=date_str
            ).first()
            if existing:
                skipped += 1
                current += timedelta(days=1)
                continue

            # Fetch 1m candles for this Globex session (datetime objects required)
            from datetime import datetime as dt_cls, timezone as tz
            session_start = dt_cls.strptime(date_str, "%Y-%m-%d").replace(hour=0, tzinfo=tz.utc) - timedelta(hours=6)
            session_end = dt_cls.strptime(date_str, "%Y-%m-%d").replace(hour=23, minute=59, tzinfo=tz.utc)
            bars = repo.get_candles(symbol, "1m", session_start, session_end)
            if not bars:
                current += timedelta(days=1)
                continue

            bars_30m = aggregate_bars_30m(bars)
            if not bars_30m:
                current += timedelta(days=1)
                continue

            profile = build_full_tpo_profile(bars_30m, tick_size=0.25)

            import json
            from dataclasses import asdict
            db.add(MarketTPOSession(
                symbol=symbol, date=date_str,
                poc=profile.poc, vah=profile.vah, val=profile.val,
                ib_high=profile.ib_high, ib_low=profile.ib_low,
                rotation_factor=profile.rotation_factor,
                profile_shape=profile.profile_shape,
                opening_type=profile.opening_type,
                opening_direction=profile.opening_direction,
                upper_excess=profile.upper_excess,
                lower_excess=profile.lower_excess,
                session_high=profile.session_high,
                session_low=profile.session_low,
                session_json=json.dumps(asdict(profile), default=str),
            ))
            db.commit()
            stored += 1
            current += timedelta(days=1)

    typer.echo(f"TPO backfill complete: {stored} stored, {skipped} skipped")
```

- [ ] **Step 2: Test the CLI**

Run: `cd backend && python -m src.app backfill-tpo --days 5 --symbol NQ`
Expected: Output like "TPO backfill complete: 3 stored, 2 skipped"

- [ ] **Step 3: Commit**

```bash
git add backend/src/app.py
git commit -m "feat(tpo): add backfill-tpo CLI command"
```

---

## Task 10: Frontend types and API functions

**Files:**
- Modify: `frontend/src/types/market.ts`
- Modify: `frontend/src/services/api.ts`

- [ ] **Step 1: Add `TPOLiveProfile` type**

Add to `frontend/src/types/market.ts`:

```typescript
export interface TPOLiveProfile {
  date?: string;
  poc: number;
  vah: number;
  val: number;
  ib_high: number;
  ib_low: number;
  rotation_factor: number;
  profile_shape: string;
  opening_type: string;
  opening_direction: string;
  upper_excess: number;
  lower_excess: number;
  session_high: number;
  session_low: number;
  tpo_counts: Record<string, number>;
  single_prints: number[];
  letters: Record<string, string[]>;
}
```

- [ ] **Step 2: Add API functions**

Add to `frontend/src/services/api.ts` in the API class:

```typescript
  async getTpoLive(symbol = 'NQ'): Promise<import('@/types/market').TPOLiveProfile> {
    return this.fetchJson(`/api/trading/market/tpo/live?symbol=${symbol}`);
  }

  async getTpoHistory(symbol = 'NQ', days = 30): Promise<{
    sessions: import('@/types/market').TPOLiveProfile[];
    symbol: string;
    count: number;
  }> {
    return this.fetchJson(`/api/trading/market/tpo?symbol=${symbol}&days=${days}`);
  }
```

- [ ] **Step 3: Verify TypeScript compiles**

Run: `cd frontend && npx tsc --noEmit`
Expected: No errors

- [ ] **Step 4: Commit**

```bash
git add frontend/src/types/market.ts frontend/src/services/api.ts
git commit -m "feat(tpo): add frontend TPO types and API functions"
```

---

## Task 11: L1Page — fetch and pass TPO data

**Files:**
- Modify: `frontend/src/components/Terminal/pages/L1Page.tsx`

- [ ] **Step 1: Add TPO fetch and state**

In `L1Page.tsx`, add state and fetch for TPO data:

```typescript
import type { TPOLiveProfile } from '@/types/market';

// Inside the component, add state:
const [tpo, setTpo] = useState<TPOLiveProfile | null>(null);

// In the existing useEffect that fetches session data on an interval, add:
api.getTpoLive('NQ').then(setTpo).catch(() => {});
```

Pass `tpo` to both child components:

```typescript
<CandleChart lastCandle={lastCandle} session={session} hiddenLevels={hiddenLevels} tpo={tpo} />
<BookSnapshot book={book} lastCandle={lastCandle} session={session} hiddenLevels={hiddenLevels} setHiddenLevels={setHiddenLevels} tpo={tpo} />
```

- [ ] **Step 2: Verify no TypeScript errors** (will warn about unknown props until Task 12/13)

Run: `cd frontend && npx tsc --noEmit`
Expected: May show prop type errors — that's fine, fixed in next tasks

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/Terminal/pages/L1Page.tsx
git commit -m "feat(tpo): fetch and pass TPO data in L1Page"
```

---

## Task 12: BookSnapshot — TPO stats section

**Files:**
- Modify: `frontend/src/components/Terminal/pages/BookSnapshot.tsx`

- [ ] **Step 1: Add TPO to LEVEL_GROUPS**

Add to the `LEVEL_GROUPS` object:

```typescript
tpo: ['t_poc', 't_vah', 't_val', 'vp_tpo'],
```

- [ ] **Step 2: Accept `tpo` prop and render stats section**

Add `tpo` to the component's props interface:

```typescript
tpo?: TPOLiveProfile | null;
```

Add a new section below the existing VP sections (follow the same pattern — collapsible group with eye toggles):

```typescript
{/* TPO Profile section */}
{tpo && tpo.poc > 0 && (
  <div className="mb-3">
    <div className="flex items-center justify-between mb-1">
      <span className="text-xs font-bold" style={{ color: '#ff6b35' }}>TPO Profile</span>
      <button onClick={() => toggleGroup('tpo')} className="opacity-50 hover:opacity-100">
        {isGroupHidden('tpo') ? '👁‍🗨' : '👁'}
      </button>
    </div>
    {!isGroupHidden('tpo') && (
      <div className="text-xs space-y-0.5">
        <div className="flex justify-between">
          <span className="text-zinc-500">tPOC</span>
          <div className="flex items-center gap-1">
            <span style={{ color: '#ff6b35' }}>{tpo.poc.toFixed(2)}</span>
            <button onClick={() => toggleLevel('t_poc')} className="opacity-40 hover:opacity-100 text-[10px]">
              {hiddenLevels.has('t_poc') ? '👁‍🗨' : '👁'}
            </button>
          </div>
        </div>
        <div className="flex justify-between">
          <span className="text-zinc-500">tVAH</span>
          <div className="flex items-center gap-1">
            <span style={{ color: '#ff6b35' }}>{tpo.vah.toFixed(2)}</span>
            <button onClick={() => toggleLevel('t_vah')} className="opacity-40 hover:opacity-100 text-[10px]">
              {hiddenLevels.has('t_vah') ? '👁‍🗨' : '👁'}
            </button>
          </div>
        </div>
        <div className="flex justify-between">
          <span className="text-zinc-500">tVAL</span>
          <div className="flex items-center gap-1">
            <span style={{ color: '#ff6b35' }}>{tpo.val.toFixed(2)}</span>
            <button onClick={() => toggleLevel('t_val')} className="opacity-40 hover:opacity-100 text-[10px]">
              {hiddenLevels.has('t_val') ? '👁‍🗨' : '👁'}
            </button>
          </div>
        </div>
        <div className="border-t border-zinc-800 my-1 pt-1">
          <div className="flex justify-between">
            <span className="text-zinc-500">Shape</span>
            <span className="text-green-400">{tpo.profile_shape}</span>
          </div>
          <div className="flex justify-between">
            <span className="text-zinc-500">Opening</span>
            <span className="text-green-400">
              {tpo.opening_type} {tpo.opening_direction === 'up' ? '↑' : tpo.opening_direction === 'down' ? '↓' : ''}
            </span>
          </div>
          <div className="flex justify-between">
            <span className="text-zinc-500">Rotation</span>
            <span className={tpo.rotation_factor > 0 ? 'text-green-400' : tpo.rotation_factor < 0 ? 'text-red-400' : 'text-zinc-400'}>
              {tpo.rotation_factor > 0 ? '+' : ''}{tpo.rotation_factor}
            </span>
          </div>
          <div className="flex justify-between">
            <span className="text-zinc-500">IB Range</span>
            <span className="text-zinc-400">{(tpo.ib_high - tpo.ib_low).toFixed(2)}</span>
          </div>
        </div>
        {(tpo.upper_excess > 0 || tpo.lower_excess > 0) && (
          <div className="border-t border-zinc-800 my-1 pt-1 text-zinc-500">
            {tpo.upper_excess > 0 && <div>Upper excess: {tpo.upper_excess} ticks</div>}
            {tpo.lower_excess > 0 && <div>Lower excess: {tpo.lower_excess} ticks</div>}
          </div>
        )}
        {tpo.single_prints.length > 0 && (
          <div className="text-zinc-600 text-[10px]">
            Singles: {tpo.single_prints.slice(0, 5).map(p => p.toFixed(2)).join(', ')}
            {tpo.single_prints.length > 5 && ` +${tpo.single_prints.length - 5}`}
          </div>
        )}
      </div>
    )}
  </div>
)}
```

- [ ] **Step 3: Verify TypeScript compiles and visual check**

Run: `cd frontend && npx tsc --noEmit`
Expected: No errors

Use Claude Preview or open browser to check the BookSnapshot panel renders the TPO section.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/Terminal/pages/BookSnapshot.tsx
git commit -m "feat(tpo): add TPO stats section to BookSnapshot with eye-toggles"
```

---

## Task 13: CandleChart — TPO histogram overlay and level lines

**Files:**
- Modify: `frontend/src/components/Terminal/pages/CandleChart.tsx`

- [ ] **Step 1: Accept `tpo` prop**

Add to CandleChart's props:

```typescript
tpo?: TPOLiveProfile | null;
```

- [ ] **Step 2: Add TPO to VP_OVERLAYS config or as separate overlay**

Add a TPO overlay config alongside the existing VP_OVERLAYS:

```typescript
const TPO_COLOR = { r: 255, g: 107, b: 53 }; // #ff6b35
```

- [ ] **Step 3: Add TPO histogram rendering in `drawOverlays`**

In the existing `drawOverlays` function (around lines 172-262), add TPO rendering AFTER the VP histogram rendering. Follow the same pattern:

```typescript
// TPO histogram overlay
if (tpo && tpo.tpo_counts && !hiddenLevels.has('vp_tpo')) {
  const counts = Object.entries(tpo.tpo_counts).map(([p, count]) => ({
    price: parseFloat(p),
    count: count as number,
  }));

  if (counts.length > 0) {
    const maxCount = Math.max(...counts.map(c => c.count));
    const maxBarWidth = chartW * 0.08; // 8% of chart width
    const { r, g, b } = TPO_COLOR;

    for (const { price, count } of counts) {
      const y = series.priceToCoordinate(price);
      if (y === null || y < 0 || y > chartH) continue;

      const barW = (count / maxCount) * maxBarWidth;
      const isPOC = Math.abs(price - tpo.poc) < 0.125;
      const isVA = price <= tpo.vah && price >= tpo.val;

      const alpha = isPOC ? 0.6 : isVA ? 0.35 : 0.2;
      ctx.fillStyle = `rgba(${r},${g},${b},${alpha})`;
      ctx.fillRect(xRight - barW, y - 1, barW, 2);
    }
  }
}
```

- [ ] **Step 4: Add TPO level lines**

Add price lines for tPOC, tVAH, tVAL using the same pattern as existing level lines (e.g., `series.createPriceLine()`):

```typescript
// TPO levels
if (tpo && tpo.poc > 0) {
  if (!hiddenLevels.has('t_poc')) {
    series.createPriceLine({
      price: tpo.poc,
      color: '#ff6b35',
      lineWidth: 1,
      lineStyle: 0, // Solid
      axisLabelVisible: true,
      title: 'tPOC',
    });
  }
  if (!hiddenLevels.has('t_vah')) {
    series.createPriceLine({
      price: tpo.vah,
      color: '#ff6b35',
      lineWidth: 1,
      lineStyle: 2, // Dashed
      axisLabelVisible: false,
      title: 'tVAH',
    });
  }
  if (!hiddenLevels.has('t_val')) {
    series.createPriceLine({
      price: tpo.val,
      color: '#ff6b35',
      lineWidth: 1,
      lineStyle: 2, // Dashed
      axisLabelVisible: false,
      title: 'tVAL',
    });
  }
}
```

Ensure these price lines are removed and re-created when `tpo` prop changes (same lifecycle as existing VP level lines).

- [ ] **Step 5: Verify visually**

Use Claude Preview or browser to verify:
- Orange TPO histogram appears on right edge of chart
- tPOC solid orange line visible
- tVAH/tVAL dashed orange lines visible
- Eye toggles in BookSnapshot hide/show them

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/Terminal/pages/CandleChart.tsx
git commit -m "feat(tpo): add TPO histogram overlay and level lines to CandleChart"
```

---

## Task 14: Final integration test and cleanup

- [ ] **Step 1: Run full backend test suite**

Run: `cd backend && python -m pytest tests/ -v --tb=short`
Expected: ALL PASS

- [ ] **Step 2: Run frontend type check**

Run: `cd frontend && npx tsc --noEmit`
Expected: No errors

- [ ] **Step 3: Start dev servers and verify end-to-end**

Start backend and frontend dev servers. Verify:
1. `/api/trading/market/tpo/live` returns TPO data
2. L1 page shows TPO overlay on chart
3. BookSnapshot shows TPO stats section
4. Eye toggles work for hide/show
5. Backfill CLI works: `cd backend && python -m src.app backfill-tpo --days 5`
6. `/api/trading/market/tpo?days=5` returns backfilled data

- [ ] **Step 4: Final commit**

```bash
git add -A
git commit -m "feat(tpo): complete TPO profile engine with overlay, stats, backfill, and API"
```
