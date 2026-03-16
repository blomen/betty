# Composite Volume Profile Levels Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Weekly and Monthly composite volume profile levels (POC, VAH, VAL) to the level monitor so traders see multi-timeframe value area context.

**Architecture:** Compute weekly/monthly VPs from cached bar data in `MarketService`, persist as `MarketLevel` DB rows via `_session_levels_to_rows()`, and expose through `build_expanded_session()` REST response. No frontend changes — levels flow through the existing pipeline automatically.

**Tech Stack:** Python, `compute_volume_profile()` from `levels.py`, Databento `ohlcv-1h` bars, SQLite `market_levels` table.

**Spec:** `docs/superpowers/specs/2026-03-16-composite-volume-profiles-design.md`

---

## Chunk 1: Core Implementation

### Task 1: Add `_fetch_monthly_bars()` method

**Files:**
- Modify: `backend/src/services/market_service.py` (after `_fetch_weekly_bars` at line ~1066)

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_composite_vp.py`:

```python
"""Tests for weekly/monthly composite volume profile levels."""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from datetime import date, datetime, timedelta
from src.market_data.levels import compute_volume_profile, VolumeProfile


def _make_bars(n_days: int, base_price: float = 20000, vol: int = 100) -> list[dict]:
    """Create synthetic bar dicts for n trading days of 1-hour RTH bars (7 per day)."""
    bars = []
    for d in range(n_days):
        for h in range(7):
            price = base_price + d * 10 + h
            bars.append({"high": price + 2, "low": price - 2, "close": price, "volume": vol})
    return bars


def test_compute_volume_profile_from_bar_dicts():
    """Verify compute_volume_profile works with bar-derived trade dicts."""
    bars = _make_bars(5)
    trades = [{"price": b["close"], "size": b.get("volume", 1)} for b in bars]
    vp = compute_volume_profile(trades)
    assert vp.poc > 0
    assert vp.vah >= vp.poc >= vp.val
    assert vp.val > 0


def test_weekly_vp_minimum_threshold():
    """Weekly VP should require >= 780 bars (~2 days of 1-min data)."""
    short_bars = _make_bars(1)  # Only 7 bars (1 day of 1h)
    assert len(short_bars) < 780  # Below threshold

    adequate_bars = [{"close": 20000 + i, "volume": 100} for i in range(800)]
    assert len(adequate_bars) >= 780  # Above threshold


def test_monthly_vp_minimum_threshold():
    """Monthly VP should require >= 35 bars (~5 days of 1h data)."""
    short_bars = _make_bars(4)  # 28 bars
    assert len(short_bars) < 35  # Below threshold

    adequate_bars = _make_bars(5)  # 35 bars
    assert len(adequate_bars) >= 35  # At threshold
```

- [ ] **Step 2: Run tests to verify they pass** (these are threshold/helper tests)

Run: `cd backend && python -m pytest tests/test_composite_vp.py -v`
Expected: 3 PASS

- [ ] **Step 3: Implement `_fetch_monthly_bars()`**

Add after `_fetch_weekly_bars()` (around line 1066) in `backend/src/services/market_service.py`:

```python
async def _fetch_monthly_bars(self, symbol: str) -> list[dict]:
    """Fetch 1-hour RTH bars for current calendar month (day-by-day for cache correctness)."""
    today = date.today()
    first_of_month = today.replace(day=1)
    try:
        provider = _get_provider()
        config = get_market_data_config()
        full_symbol = config.get("symbol", "NQ.FUT")
        sessions_cfg = config.get("sessions", {})
        bars_all = []
        current = first_of_month
        while current <= today:
            dt = datetime.combine(current, datetime.strptime(sessions_cfg.get("rth_open", "09:30"), "%H:%M").time())
            dt_close = datetime.combine(current, datetime.strptime(sessions_cfg.get("rth_close", "16:00"), "%H:%M").time())
            day_bars = await provider.get_bars(full_symbol, "1h", dt, dt_close)
            if day_bars:
                bars_all.extend([{"high": b.high, "low": b.low, "close": b.close, "volume": b.volume} for b in day_bars])
            current += timedelta(days=1)
        return bars_all
    except Exception as e:
        logger.warning("Failed to fetch monthly bars: %s", e)
        return []
```

- [ ] **Step 4: Commit**

```bash
git add backend/tests/test_composite_vp.py backend/src/services/market_service.py
git commit -m "feat(trading): add _fetch_monthly_bars and composite VP tests"
```

---

### Task 2: Add composite VPs to `_session_levels_to_rows()`

**Files:**
- Modify: `backend/src/services/market_service.py:985-1017` (`_session_levels_to_rows`)
- Test: `backend/tests/test_composite_vp.py`

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_composite_vp.py`:

```python
from src.market_data.levels import VolumeProfile


def test_session_levels_to_rows_includes_weekly_vp():
    """Weekly VP levels should appear in level rows when provided."""
    from src.services.market_service import MarketService
    from src.market_data.levels import SessionLevels

    levels = SessionLevels(pdh=20100, pdl=19900)
    session_data = {"poc": 20000, "vah": 20050, "val": 19950, "vwap": 20010}
    weekly_vp = VolumeProfile(poc=20020, vah=20080, val=19920)

    rows = MarketService._session_levels_to_rows(levels, session_data, weekly_vp=weekly_vp)

    types = [r["level_type"] for r in rows]
    assert "weekly_poc" in types
    assert "weekly_vah" in types
    assert "weekly_val" in types

    weekly_poc_row = next(r for r in rows if r["level_type"] == "weekly_poc")
    assert weekly_poc_row["price_low"] == 20020
    assert weekly_poc_row["session"] == "weekly"
    assert weekly_poc_row["direction"] is None

    weekly_vah_row = next(r for r in rows if r["level_type"] == "weekly_vah")
    assert weekly_vah_row["direction"] == "resistance"


def test_session_levels_to_rows_includes_monthly_vp():
    """Monthly VP levels should appear in level rows when provided."""
    from src.services.market_service import MarketService
    from src.market_data.levels import SessionLevels

    levels = SessionLevels()
    session_data = {"poc": 20000, "vah": 20050, "val": 19950, "vwap": 20010}
    monthly_vp = VolumeProfile(poc=19800, vah=20200, val=19400)

    rows = MarketService._session_levels_to_rows(levels, session_data, monthly_vp=monthly_vp)

    types = [r["level_type"] for r in rows]
    assert "monthly_poc" in types
    assert "monthly_vah" in types
    assert "monthly_val" in types

    monthly_val_row = next(r for r in rows if r["level_type"] == "monthly_val")
    assert monthly_val_row["price_low"] == 19400
    assert monthly_val_row["session"] == "monthly"
    assert monthly_val_row["direction"] == "support"


def test_session_levels_to_rows_no_composite_when_none():
    """No composite levels when VPs are None."""
    from src.services.market_service import MarketService
    from src.market_data.levels import SessionLevels

    levels = SessionLevels()
    session_data = {"poc": 20000, "vah": 20050, "val": 19950, "vwap": 20010}

    rows = MarketService._session_levels_to_rows(levels, session_data)

    types = [r["level_type"] for r in rows]
    assert "weekly_poc" not in types
    assert "monthly_poc" not in types
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_composite_vp.py::test_session_levels_to_rows_includes_weekly_vp -v`
Expected: FAIL — `_session_levels_to_rows()` doesn't accept `weekly_vp` parameter yet.

- [ ] **Step 3: Update `_session_levels_to_rows()` signature and add composite levels**

In `backend/src/services/market_service.py`, modify the method at line 985:

Change the signature from:
```python
@staticmethod
def _session_levels_to_rows(levels: SessionLevels, session_data: dict) -> list[dict]:
```

To:
```python
@staticmethod
def _session_levels_to_rows(
    levels: SessionLevels,
    session_data: dict,
    weekly_vp: VolumeProfile | None = None,
    monthly_vp: VolumeProfile | None = None,
) -> list[dict]:
```

Add before the `return rows` statement (after the existing `_add("vwap", ...)` line):

```python
        # Weekly composite VP levels
        if weekly_vp and weekly_vp.poc:
            _add("weekly_poc", weekly_vp.poc, None, "weekly")
            _add("weekly_vah", weekly_vp.vah, "resistance", "weekly")
            _add("weekly_val", weekly_vp.val, "support", "weekly")

        # Monthly composite VP levels
        if monthly_vp and monthly_vp.poc:
            _add("monthly_poc", monthly_vp.poc, None, "monthly")
            _add("monthly_vah", monthly_vp.vah, "resistance", "monthly")
            _add("monthly_val", monthly_vp.val, "support", "monthly")
```

Also add the import at top of file if not already present:
```python
from ..market_data.levels import ..., VolumeProfile
```

(Check existing imports — `VolumeProfile` is already imported on line 14.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_composite_vp.py -v`
Expected: All 6 tests PASS

- [ ] **Step 5: Commit**

```bash
git add backend/tests/test_composite_vp.py backend/src/services/market_service.py
git commit -m "feat(trading): add weekly/monthly VP levels to _session_levels_to_rows"
```

---

### Task 3: Wire composite VPs into `compute_session()`

**Files:**
- Modify: `backend/src/services/market_service.py:204-208` (in `compute_session()`)

- [ ] **Step 1: Add composite VP computation before `_session_levels_to_rows` call**

In `backend/src/services/market_service.py`, replace line 208:

```python
level_rows = self._session_levels_to_rows(session_levels, session_data)
```

With:

```python
        # Weekly composite VP (min 2 trading days of 1-min bars)
        weekly_bars = await self._fetch_weekly_bars(symbol)
        weekly_vp = None
        if weekly_bars and len(weekly_bars) >= 780:
            wb_trades = [{"price": b["close"], "size": b.get("volume", 1)} for b in weekly_bars]
            weekly_vp = compute_volume_profile(wb_trades)

        # Monthly composite VP (min 5 trading days of 1-hour bars)
        monthly_bars = await self._fetch_monthly_bars(symbol)
        monthly_vp = None
        if monthly_bars and len(monthly_bars) >= 35:
            mb_trades = [{"price": b["close"], "size": b.get("volume", 1)} for b in monthly_bars]
            monthly_vp = compute_volume_profile(mb_trades)

        level_rows = self._session_levels_to_rows(session_levels, session_data, weekly_vp, monthly_vp)
```

- [ ] **Step 2: Run full test suite to verify nothing broke**

Run: `cd backend && python -m pytest tests/test_composite_vp.py -v`
Expected: All PASS

- [ ] **Step 3: Commit**

```bash
git add backend/src/services/market_service.py
git commit -m "feat(trading): compute weekly/monthly composite VPs in compute_session"
```

---

### Task 4: Add monthly VP and weekly threshold to `build_expanded_session()`

**Files:**
- Modify: `backend/src/services/market_service.py:277-283` (in `build_expanded_session()`, weekly + monthly profile blocks)

- [ ] **Step 1: Add weekly threshold and monthly profile computation**

In `backend/src/services/market_service.py`, the existing weekly block (line 277-282) has no minimum bar count. Update it to match `compute_session()` threshold, and add monthly block after. Replace lines 277-282:

```python
        # Weekly composite
        weekly_bars = await self._fetch_weekly_bars(symbol)
        if weekly_bars:
            from ..market_data.levels import compute_volume_profile as compute_vp_levels
            wb_trades = [{"price": b.get("close", 0), "size": b.get("volume", 1)} for b in weekly_bars]
            weekly_vp = compute_vp_levels(wb_trades)
            profiles["weekly"] = {"poc": weekly_vp.poc, "vah": weekly_vp.vah, "val": weekly_vp.val}
```

With:

```python
        # Weekly composite (min 2 trading days of 1-min bars)
        weekly_bars = await self._fetch_weekly_bars(symbol)
        if weekly_bars and len(weekly_bars) >= 780:
            from ..market_data.levels import compute_volume_profile as compute_vp_levels
            wb_trades = [{"price": b.get("close", 0), "size": b.get("volume", 1)} for b in weekly_bars]
            weekly_vp = compute_vp_levels(wb_trades)
            profiles["weekly"] = {"poc": weekly_vp.poc, "vah": weekly_vp.vah, "val": weekly_vp.val}

        # Monthly composite (min 5 trading days of 1-hour bars)
        monthly_bars = await self._fetch_monthly_bars(symbol)
        if monthly_bars and len(monthly_bars) >= 35:
            from ..market_data.levels import compute_volume_profile as compute_vp_levels
            mb_trades = [{"price": b.get("close", 0), "size": b.get("volume", 1)} for b in monthly_bars]
            monthly_vp = compute_vp_levels(mb_trades)
            profiles["monthly"] = {"poc": monthly_vp.poc, "vah": monthly_vp.vah, "val": monthly_vp.val}
```

- [ ] **Step 2: Run tests**

Run: `cd backend && python -m pytest tests/test_composite_vp.py -v`
Expected: All PASS

- [ ] **Step 3: Commit**

```bash
git add backend/src/services/market_service.py
git commit -m "feat(trading): add monthly VP to build_expanded_session REST response"
```

---

### Task 5: Verify end-to-end with existing tests

- [ ] **Step 1: Run full backend test suite**

Run: `cd backend && python -m pytest tests/ -v --tb=short`
Expected: All existing tests still pass, no regressions.

- [ ] **Step 2: Final commit if any fixups needed**

```bash
git add -u
git commit -m "fix(trading): fixups from composite VP integration"
```
