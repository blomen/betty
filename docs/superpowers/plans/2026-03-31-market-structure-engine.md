# Market Structure Engine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace fractal pivot detection with a proper Dow Theory market structure state machine that confirms swings via close-beyond-level, detects BOS/CHoCH events, and provides 5-state trend classification across daily/weekly/monthly timeframes.

**Architecture:** A `MarketStructureEngine` class processes candles chronologically through a SEEKING_HIGH/SEEKING_LOW state machine. Swings are confirmed when price closes beyond the opposing swing level. BOS (trend continuation) and CHoCH (trend reversal) events are detected at each confirmation. The engine runs independently per timeframe (daily/weekly/monthly) on candles built from session summaries (795 sessions back to 2011). Results feed into the DQN STRUCTURE segment (38 features) and chart rendering.

**Tech Stack:** Python 3.10+ / NumPy / dataclasses / TypeScript

---

### Task 1: MarketStructureEngine — Core State Machine

**Files:**
- Create: `backend/src/market_data/structure.py`
- Test: `backend/tests/test_market_structure.py`

This is the core algorithm. New file to keep it focused — `levels.py` is already large.

- [ ] **Step 1: Write failing tests for the engine**

```python
# backend/tests/test_market_structure.py
from src.market_data.structure import MarketStructureEngine, StructureEvent, SwingLevel


def _make_uptrend_candles() -> list[dict]:
    """Clear uptrend: SL1 → SH1 → SL2(HL) → SH2(HH) → SL3(HL)

    The engine confirms swings when price closes beyond the opposing level.
    So SL1 is confirmed when close > SH1, and SH1 is confirmed when close < SL1.
    We need enough candles to trigger at least 2 full cycles.
    """
    return [
        # Initial range to establish first swing high
        {"high": 100, "low": 90, "close": 95, "ts": 1000},
        {"high": 105, "low": 95, "close": 100, "ts": 2000},
        {"high": 110, "low": 100, "close": 108, "ts": 3000},  # potential SH1=110
        {"high": 108, "low": 98, "close": 100, "ts": 4000},
        {"high": 103, "low": 88, "close": 89, "ts": 5000},    # close < 90 (potential SL) → confirms SH1
        # Now SEEKING_LOW, potential_low tracking
        {"high": 95, "low": 85, "close": 87, "ts": 6000},     # potential SL1=85
        {"high": 100, "low": 90, "close": 98, "ts": 7000},
        {"high": 112, "low": 102, "close": 111, "ts": 8000},  # close > 110 (SH1) → confirms SL1, BOS bullish
        # Now SEEKING_HIGH, uptrend started
        {"high": 120, "low": 108, "close": 118, "ts": 9000},  # potential SH2=120
        {"high": 115, "low": 105, "close": 107, "ts": 10000},
        {"high": 108, "low": 90, "close": 92, "ts": 11000},   # potential SL2=90
        {"high": 95, "low": 88, "close": 89, "ts": 12000},    # close < 85 (SL1)? No, 89 > 85
        {"high": 105, "low": 92, "close": 102, "ts": 13000},
        {"high": 122, "low": 110, "close": 121, "ts": 14000}, # close > 120 (SH2) → confirms SL2, BOS bullish
        # SL2=88 > SL1=85 → HL. SH confirmed at next break.
        {"high": 130, "low": 118, "close": 128, "ts": 15000}, # potential SH3=130
        {"high": 125, "low": 115, "close": 117, "ts": 16000},
    ]


def _make_choch_candles() -> list[dict]:
    """Uptrend that reverses: establish uptrend, then CHoCH below swing low."""
    return [
        # Build uptrend (same as above start)
        {"high": 100, "low": 90, "close": 95, "ts": 1000},
        {"high": 110, "low": 95, "close": 108, "ts": 2000},   # potential SH=110
        {"high": 105, "low": 85, "close": 87, "ts": 3000},    # close < 90 → confirms SH
        {"high": 95, "low": 80, "close": 82, "ts": 4000},     # potential SL=80
        {"high": 100, "low": 88, "close": 98, "ts": 5000},
        {"high": 115, "low": 100, "close": 112, "ts": 6000},  # close > 110 → confirms SL=80, BOS bullish
        {"high": 125, "low": 110, "close": 122, "ts": 7000},  # potential SH2=125
        {"high": 120, "low": 105, "close": 108, "ts": 8000},
        {"high": 110, "low": 95, "close": 97, "ts": 9000},    # potential SL2=95
        # CHoCH: close below SL1=80
        {"high": 100, "low": 75, "close": 78, "ts": 10000},   # close=78 < 80 → CHoCH bearish!
        {"high": 85, "low": 70, "close": 72, "ts": 11000},    # continue down
    ]


def test_engine_empty():
    engine = MarketStructureEngine()
    result = engine.process([])
    assert result.structure == "ranging"
    assert result.swing_highs == []
    assert result.swing_lows == []
    assert result.last_bos is None
    assert result.last_choch is None


def test_engine_insufficient():
    engine = MarketStructureEngine()
    candles = [{"high": 100, "low": 90, "close": 95, "ts": i} for i in range(3)]
    result = engine.process(candles)
    assert result.structure == "ranging"


def test_engine_uptrend():
    engine = MarketStructureEngine()
    result = engine.process(_make_uptrend_candles())
    assert result.structure == "uptrend"
    assert len(result.swing_highs) >= 1
    assert len(result.swing_lows) >= 1
    assert result.last_bos is not None
    assert "bullish" in result.last_bos.event_type


def test_engine_choch():
    engine = MarketStructureEngine()
    result = engine.process(_make_choch_candles())
    assert result.last_choch is not None
    assert "bearish" in result.last_choch.event_type
    assert result.structure in ("reversing_down", "downtrend")


def test_engine_close_only():
    """A wick through the level without close should NOT confirm."""
    engine = MarketStructureEngine()
    candles = [
        {"high": 100, "low": 90, "close": 95, "ts": 1000},
        {"high": 110, "low": 95, "close": 105, "ts": 2000},   # potential SH=110
        {"high": 105, "low": 85, "close": 87, "ts": 3000},    # close < 90 → confirms SH
        {"high": 95, "low": 80, "close": 82, "ts": 4000},     # potential SL=80
        # Wick above 110 but close below it — should NOT confirm SL
        {"high": 112, "low": 95, "close": 108, "ts": 5000},   # high > 110 but close < 110
    ]
    result = engine.process(candles)
    # SL should NOT be confirmed because close (108) didn't exceed SH (110)
    confirmed_lows = [s for s in result.swing_lows]
    # Only swings confirmed by close-beyond should appear
    assert all(s.price != 80 for s in confirmed_lows) or len(confirmed_lows) == 0


def test_engine_bos_active():
    engine = MarketStructureEngine(recency_window=5)
    result = engine.process(_make_uptrend_candles())
    # The uptrend candles end with recent BOS
    assert result.bos_active is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_market_structure.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.market_data.structure'`

- [ ] **Step 3: Implement `MarketStructureEngine`**

Create `backend/src/market_data/structure.py`:

```python
"""Market structure engine — Dow Theory swing confirmation with BOS/CHoCH detection.

Replaces fractal pivot detection with a state machine that confirms swings
only when price closes beyond the opposing level. This filters noise and
captures real structural reversals.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class StructureEvent:
    """A structural event: BOS or CHoCH."""
    price: float            # price of the candle close that triggered the event
    timestamp: int          # epoch seconds of triggering candle
    event_type: str         # "bos_bullish", "bos_bearish", "choch_bullish", "choch_bearish"
    swing_type: str         # "swing_high" or "swing_low" that was confirmed
    swing_price: float      # price of the confirmed swing


@dataclass
class SwingLevel:
    """A confirmed structural swing point."""
    price: float
    timestamp: int          # epoch seconds
    type: str               # "swing_high" or "swing_low"
    timeframe: str = ""     # set by caller: "daily", "weekly", "monthly"


@dataclass
class StructureResult:
    """Output of MarketStructureEngine.process()."""
    structure: str          # "uptrend", "downtrend", "reversing_up", "reversing_down", "ranging"
    swing_highs: list[SwingLevel] = field(default_factory=list)   # confirmed, newest first, max 3
    swing_lows: list[SwingLevel] = field(default_factory=list)    # confirmed, newest first, max 3
    last_bos: StructureEvent | None = None
    last_choch: StructureEvent | None = None
    bos_active: bool = False
    choch_active: bool = False
    events: list[StructureEvent] = field(default_factory=list)    # all events in order


class MarketStructureEngine:
    """Dow Theory market structure state machine.

    Processes candles chronologically. Confirms swings when price closes
    beyond the opposing swing level. Detects BOS and CHoCH events.

    Usage:
        engine = MarketStructureEngine()
        result = engine.process(daily_candles)
        # result.structure = "uptrend" / "downtrend" / etc.
        # result.swing_highs = [SwingLevel, ...] (confirmed, newest first)
    """

    def __init__(self, recency_window: int = 5, max_swings: int = 3):
        self._recency = recency_window
        self._max_swings = max_swings

    def process(self, candles: list[dict]) -> StructureResult:
        """Process a chronological list of OHLC candles and return structure."""
        if len(candles) < 3:
            return StructureResult(structure="ranging")

        # Internal state
        state = "SEEKING_HIGH"  # start by looking for first swing high
        trend = "ranging"

        # Potential (unconfirmed) swing tracking
        potential_high = candles[0]["high"]
        potential_high_ts = candles[0].get("ts", 0)
        potential_low = candles[0]["low"]
        potential_low_ts = candles[0].get("ts", 0)

        # Confirmed swings (chronological during processing, reversed at end)
        confirmed_highs: list[SwingLevel] = []
        confirmed_lows: list[SwingLevel] = []
        events: list[StructureEvent] = []

        # Last confirmed levels for comparison
        last_sh_price: float | None = None
        last_sl_price: float | None = None

        for i, candle in enumerate(candles):
            high = candle["high"]
            low = candle["low"]
            close = candle["close"]
            ts = candle.get("ts", 0)
            if hasattr(ts, "timestamp"):
                ts = int(ts.timestamp())

            if state == "SEEKING_HIGH":
                # Track potential high
                if high > potential_high:
                    potential_high = high
                    potential_high_ts = ts

                # Track potential low for after we switch
                if low < potential_low:
                    potential_low = low
                    potential_low_ts = ts

                # Check: does close break below the last confirmed swing low?
                if last_sl_price is not None and close < last_sl_price:
                    # Confirm the potential high as a swing high
                    sh = SwingLevel(price=potential_high, timestamp=potential_high_ts, type="swing_high")
                    confirmed_highs.append(sh)

                    # Determine event type
                    if last_sh_price is not None:
                        if trend in ("downtrend", "reversing_down"):
                            # Breaking below SL while already bearish = BOS bearish
                            event = StructureEvent(
                                price=close, timestamp=ts,
                                event_type="bos_bearish",
                                swing_type="swing_high", swing_price=potential_high,
                            )
                            events.append(event)
                            trend = "downtrend"
                        elif trend in ("uptrend", "reversing_up"):
                            # Breaking below SL while bullish = CHoCH bearish
                            event = StructureEvent(
                                price=close, timestamp=ts,
                                event_type="choch_bearish",
                                swing_type="swing_high", swing_price=potential_high,
                            )
                            events.append(event)
                            trend = "reversing_down"
                        elif trend == "ranging":
                            event = StructureEvent(
                                price=close, timestamp=ts,
                                event_type="bos_bearish",
                                swing_type="swing_high", swing_price=potential_high,
                            )
                            events.append(event)
                            trend = "downtrend"

                    last_sh_price = potential_high
                    state = "SEEKING_LOW"
                    potential_low = low
                    potential_low_ts = ts

            elif state == "SEEKING_LOW":
                # Track potential low
                if low < potential_low:
                    potential_low = low
                    potential_low_ts = ts

                # Track potential high for after we switch
                if high > potential_high:
                    potential_high = high
                    potential_high_ts = ts

                # Check: does close break above the last confirmed swing high?
                if last_sh_price is not None and close > last_sh_price:
                    # Confirm the potential low as a swing low
                    sl = SwingLevel(price=potential_low, timestamp=potential_low_ts, type="swing_low")
                    confirmed_lows.append(sl)

                    # Determine event type
                    if last_sl_price is not None:
                        if trend in ("uptrend", "reversing_up"):
                            # Breaking above SH while already bullish = BOS bullish
                            event = StructureEvent(
                                price=close, timestamp=ts,
                                event_type="bos_bullish",
                                swing_type="swing_low", swing_price=potential_low,
                            )
                            events.append(event)
                            trend = "uptrend"
                        elif trend in ("downtrend", "reversing_down"):
                            # Breaking above SH while bearish = CHoCH bullish
                            event = StructureEvent(
                                price=close, timestamp=ts,
                                event_type="choch_bullish",
                                swing_type="swing_low", swing_price=potential_low,
                            )
                            events.append(event)
                            trend = "reversing_up"
                        elif trend == "ranging":
                            event = StructureEvent(
                                price=close, timestamp=ts,
                                event_type="bos_bullish",
                                swing_type="swing_low", swing_price=potential_low,
                            )
                            events.append(event)
                            trend = "uptrend"

                    last_sl_price = potential_low
                    state = "SEEKING_HIGH"
                    potential_high = high
                    potential_high_ts = ts

        # Build result — newest first, capped
        sh_list = confirmed_highs[-self._max_swings:][::-1]
        sl_list = confirmed_lows[-self._max_swings:][::-1]

        # BOS / CHoCH recency
        last_bos = None
        last_choch = None
        for ev in reversed(events):
            if "bos" in ev.event_type and last_bos is None:
                last_bos = ev
            if "choch" in ev.event_type and last_choch is None:
                last_choch = ev
            if last_bos and last_choch:
                break

        n_candles = len(candles)
        bos_active = False
        choch_active = False
        if last_bos and events:
            bos_idx = events.index(last_bos)
            bos_active = (len(events) - 1 - bos_idx) < self._recency or (n_candles - 1) < self._recency * 2
        if last_choch and events:
            choch_idx = events.index(last_choch)
            choch_active = (len(events) - 1 - choch_idx) < self._recency or (n_candles - 1) < self._recency * 2

        return StructureResult(
            structure=trend,
            swing_highs=sh_list,
            swing_lows=sl_list,
            last_bos=last_bos,
            last_choch=last_choch,
            bos_active=bos_active,
            choch_active=choch_active,
            events=events,
        )
```

- [ ] **Step 4: Run tests**

Run: `cd backend && python -m pytest tests/test_market_structure.py -v`
Expected: All PASSED

- [ ] **Step 5: Commit**

```bash
git add backend/src/market_data/structure.py backend/tests/test_market_structure.py
git commit -m "feat(structure): add MarketStructureEngine state machine with BOS/CHoCH"
```

---

### Task 2: Update Dataclasses and `compute_multi_tf_swings`

**Files:**
- Modify: `backend/src/market_data/levels.py`
- Test: `backend/tests/test_market_structure.py` (add tests)

Replace the old fractal-based `compute_multi_tf_swings` with one that uses `MarketStructureEngine`.

- [ ] **Step 1: Add integration test**

Add to `backend/tests/test_market_structure.py`:

```python
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from src.market_data.levels import compute_multi_tf_swings, SwingStructure, TimeframeSwings

CET = ZoneInfo("Europe/Stockholm")


def _make_session_bars(days: int = 30) -> list[dict]:
    """Synthetic 1-bar-per-day data with a zigzag pattern for swing detection."""
    import math
    bars = []
    start = datetime(2026, 1, 5, 12, 0, tzinfo=CET)  # Monday
    for d in range(days):
        dt = start + timedelta(days=d)
        if dt.weekday() >= 5:
            continue
        ts = dt.astimezone(timezone.utc)
        # Zigzag: 5 days up, 5 days down, with macro uptrend
        cycle = d % 10
        if cycle < 5:
            base = 19000 + cycle * 80 + d * 10
        else:
            base = 19000 + (10 - cycle) * 80 + d * 10
        bars.append({
            "ts": ts,
            "open": base,
            "high": base + 100,
            "low": base - 100,
            "close": base + 20,
        })
    return bars


def test_compute_multi_tf_swings_with_engine():
    bars = _make_session_bars(days=60)
    result = compute_multi_tf_swings(bars)
    assert isinstance(result, SwingStructure)
    assert result.daily.timeframe == "daily"
    assert result.daily.structure in ("uptrend", "downtrend", "reversing_up", "reversing_down", "ranging")
    assert len(result.daily.swing_highs) <= 3
    assert -1.0 <= result.trend_alignment <= 1.0
    # Should have BOS or CHoCH events from the zigzag
    assert result.daily.last_bos is not None or result.daily.last_choch is not None


def test_compute_multi_tf_swings_empty():
    result = compute_multi_tf_swings([])
    assert result.daily.structure == "ranging"
    assert result.trend_alignment == 0.0


def test_compute_multi_tf_swings_has_bos_choch_fields():
    bars = _make_session_bars(days=60)
    result = compute_multi_tf_swings(bars)
    # TimeframeSwings should have bos_active and choch_active
    assert isinstance(result.daily.bos_active, bool)
    assert isinstance(result.daily.choch_active, bool)
```

- [ ] **Step 2: Update dataclasses in `levels.py`**

In `backend/src/market_data/levels.py`, update the `TimeframeSwings` and `SwingStructure` dataclasses. Import `StructureEvent` from the new module:

Replace the existing `TimeframeSwings`:
```python
from .structure import StructureEvent, SwingLevel, MarketStructureEngine

@dataclass
class TimeframeSwings:
    """Swing detection result for a single timeframe."""
    timeframe: str
    structure: str           # "uptrend", "downtrend", "reversing_up", "reversing_down", "ranging"
    swing_highs: list[SwingLevel] = field(default_factory=list)
    swing_lows: list[SwingLevel] = field(default_factory=list)
    last_bos: StructureEvent | None = None
    last_choch: StructureEvent | None = None
    bos_active: bool = False
    choch_active: bool = False
```

Remove the `SwingLevel` class from `levels.py` (it's now in `structure.py`). Remove `detect_fractal_pivots`, `_classify_structure`, `_TF_CONFIG`.

- [ ] **Step 3: Rewrite `compute_multi_tf_swings`**

Replace the existing function in `levels.py`:

```python
_TF_RECENCY = {"daily": 5, "weekly": 3, "monthly": 2}


def compute_multi_tf_swings(bars_1m: list[dict]) -> SwingStructure:
    """Compute swing structure across daily, weekly, and monthly timeframes.

    Uses MarketStructureEngine (Dow Theory state machine) instead of fractal pivots.
    Aggregates bars into higher-TF candles, runs engine per timeframe.
    """
    def empty_tf(tf: str) -> TimeframeSwings:
        return TimeframeSwings(timeframe=tf, structure="ranging")

    if not bars_1m:
        return SwingStructure(
            daily=empty_tf("daily"),
            weekly=empty_tf("weekly"),
            monthly=empty_tf("monthly"),
            trend_alignment=0.0,
        )

    trend_scores = {
        "uptrend": 1.0, "reversing_up": 0.5,
        "ranging": 0.0,
        "reversing_down": -0.5, "downtrend": -1.0,
    }

    results: dict[str, TimeframeSwings] = {}
    for tf in ("daily", "weekly", "monthly"):
        candles = aggregate_to_timeframe(bars_1m, tf)
        if len(candles) < 5:
            results[tf] = empty_tf(tf)
            continue

        engine = MarketStructureEngine(recency_window=_TF_RECENCY[tf])
        sr = engine.process(candles)

        for s in sr.swing_highs:
            s.timeframe = tf
        for s in sr.swing_lows:
            s.timeframe = tf

        results[tf] = TimeframeSwings(
            timeframe=tf,
            structure=sr.structure,
            swing_highs=sr.swing_highs,
            swing_lows=sr.swing_lows,
            last_bos=sr.last_bos,
            last_choch=sr.last_choch,
            bos_active=sr.bos_active,
            choch_active=sr.choch_active,
        )

    alignment = sum(
        trend_scores.get(results[tf].structure, 0.0) for tf in ("daily", "weekly", "monthly")
    ) / 3.0

    return SwingStructure(
        daily=results["daily"],
        weekly=results["weekly"],
        monthly=results["monthly"],
        trend_alignment=round(alignment, 2),
    )
```

- [ ] **Step 4: Fix imports throughout codebase**

Any file importing `SwingLevel` from `levels.py` now needs to import from `structure.py`. Search and update:
- `backend/src/rl/features/structure_features.py` — change import
- `backend/src/rl/data/session_store.py` — change import
- `backend/tests/test_swing_multi_tf.py` — update imports

For each, replace:
```python
from src.market_data.levels import SwingLevel, ...
```
With:
```python
from src.market_data.structure import SwingLevel, StructureEvent
from src.market_data.levels import SwingStructure, TimeframeSwings, ...
```

- [ ] **Step 5: Run tests**

Run: `cd backend && python -m pytest tests/test_market_structure.py tests/test_swing_multi_tf.py -v`
Expected: All PASSED

- [ ] **Step 6: Commit**

```bash
git add backend/src/market_data/levels.py backend/src/market_data/structure.py backend/src/rl/features/structure_features.py backend/src/rl/data/session_store.py backend/tests/test_market_structure.py backend/tests/test_swing_multi_tf.py
git commit -m "feat(structure): replace fractal pivots with MarketStructureEngine in compute_multi_tf_swings"
```

---

### Task 3: Expand STRUCTURE Features (32 → 38) with BOS/CHoCH

**Files:**
- Modify: `backend/src/rl/features/structure_features.py`
- Modify: `backend/src/rl/features/observation.py`
- Test: `backend/tests/test_market_structure.py`

- [ ] **Step 1: Add feature tests**

Add to `backend/tests/test_market_structure.py`:

```python
import numpy as np
from src.rl.features.structure_features import extract_structure_features
from src.market_data.levels import SwingStructure, TimeframeSwings
from src.market_data.structure import SwingLevel, StructureEvent


def _make_test_swing_structure() -> SwingStructure:
    return SwingStructure(
        daily=TimeframeSwings(
            timeframe="daily", structure="uptrend",
            swing_highs=[SwingLevel(19500, 1000, "swing_high", "daily")],
            swing_lows=[SwingLevel(19200, 900, "swing_low", "daily")],
            last_bos=StructureEvent(19510, 1100, "bos_bullish", "swing_low", 19200),
            last_choch=None,
            bos_active=True, choch_active=False,
        ),
        weekly=TimeframeSwings(
            timeframe="weekly", structure="downtrend",
            swing_highs=[], swing_lows=[],
            last_bos=None,
            last_choch=StructureEvent(19100, 500, "choch_bearish", "swing_high", 19600),
            bos_active=False, choch_active=True,
        ),
        monthly=TimeframeSwings(timeframe="monthly", structure="ranging"),
        trend_alignment=0.0,
    )


def test_structure_features_38_with_bos_choch():
    swing = _make_test_swing_structure()
    feats = extract_structure_features(
        price=19400.0, vwap_bands=None, volume_profile=None,
        session_levels=None, session_context=None,
        swing_structure=swing,
    )
    assert feats.shape == (38,)
    # Trend: daily=uptrend(+1), weekly=downtrend(-1), monthly=ranging(0)
    assert feats[23] == 1.0
    assert feats[24] == -1.0
    assert feats[25] == 0.0
    # BOS flags: daily=1, weekly=0, monthly=0
    assert feats[32] == 1.0
    assert feats[33] == 0.0
    assert feats[34] == 0.0
    # CHoCH flags: daily=0, weekly=1, monthly=0
    assert feats[35] == 0.0
    assert feats[36] == 1.0
    assert feats[37] == 0.0
    assert all(np.isfinite(feats))


def test_structure_features_38_without_swings():
    feats = extract_structure_features(
        price=19400.0, vwap_bands=None, volume_profile=None,
        session_levels=None, session_context=None,
        swing_structure=None,
    )
    assert feats.shape == (38,)
    assert all(feats[23:38] == 0.0)
```

- [ ] **Step 2: Update `structure_features.py`**

Change `_N_FEATURES` from 32 to 38. Update `_extract_swing_features` to return 15 features instead of 9:

```python
_N_FEATURES = 38


def _extract_swing_features(
    price: float,
    swing: SwingStructure | None,
) -> np.ndarray:
    """Extract 15 swing structure features (indices 23-37)."""
    feats = np.zeros(15, dtype=np.float32)
    if swing is None:
        return feats

    trend_map = {
        "uptrend": 1.0, "reversing_up": 0.5,
        "ranging": 0.0,
        "reversing_down": -0.5, "downtrend": -1.0,
    }

    for i, tf_swings in enumerate([swing.daily, swing.weekly, swing.monthly]):
        # Trend direction (feats 0-2 → indices 23-25)
        feats[i] = trend_map.get(tf_swings.structure, 0.0)

        # Distance to nearest swing level (feats 3-5 → indices 26-28)
        all_prices = [s.price for s in tf_swings.swing_highs + tf_swings.swing_lows]
        if all_prices:
            nearest = min(all_prices, key=lambda p: abs(p - price))
            dist_ticks = (price - nearest) / TICK_SIZE
            feats[3 + i] = float(np.clip(dist_ticks / 200.0, -1.0, 1.0))

        # Position in swing range (feats 6-8 → indices 29-31)
        if all_prices:
            range_high = max(all_prices)
            range_low = min(all_prices)
            span = range_high - range_low
            if span > 0:
                feats[6 + i] = float(np.clip((price - range_low) / span, 0.0, 1.0))
            else:
                feats[6 + i] = 0.5

        # BOS active (feats 9-11 → indices 32-34)
        feats[9 + i] = 1.0 if tf_swings.bos_active else 0.0

        # CHoCH active (feats 12-14 → indices 35-37)
        feats[12 + i] = 1.0 if tf_swings.choch_active else 0.0

    return feats
```

Update the docstring and the assignment line:
```python
    # --- Swing Structure (feats 23-37) ---
    feats[23:38] = _extract_swing_features(price, swing_structure)
```

- [ ] **Step 3: Update `observation.py`**

Change the structure comment from `# 3. Structure + session (32)` to `# 3. Structure + session (38)`. Update docstring totals.

- [ ] **Step 4: Run tests**

Run: `cd backend && python -m pytest tests/test_market_structure.py -v -k "structure_features"`
Expected: PASSED

Verify dimension:
Run: `cd backend && python -c "from src.rl.features.observation import OBSERVATION_DIM; print(OBSERVATION_DIM)"`
Expected: Previous value + 6

- [ ] **Step 5: Commit**

```bash
git add backend/src/rl/features/structure_features.py backend/src/rl/features/observation.py backend/tests/test_market_structure.py
git commit -m "feat(rl): expand STRUCTURE segment to 38 features with BOS/CHoCH flags"
```

---

### Task 4: Live Path — Load Session Summaries

**Files:**
- Modify: `backend/src/services/market_service.py`

Replace `_get_swing_bars` (fetches 1m bars from DB) with loading session summaries from the RL data file for swing detection.

- [ ] **Step 1: Add `_load_swing_structure` method**

Add to `MarketService` class, replacing `_get_swing_bars`:

```python
    def _load_swing_structure(self):
        """Load swing structure from RL session summaries (795 sessions back to 2011).

        Uses the same data as the replay engine for consistency between
        live inference and training.
        """
        import json
        from pathlib import Path
        from ..market_data.levels import compute_multi_tf_swings

        summaries_path = Path(__file__).resolve().parents[2] / "data" / "rl" / "session_summaries.json"
        if not summaries_path.exists():
            logger.warning("Session summaries not found at %s", summaries_path)
            return None

        try:
            with open(summaries_path) as f:
                raw = json.load(f)
        except Exception as e:
            logger.warning("Failed to load session summaries: %s", e)
            return None

        from zoneinfo import ZoneInfo
        CET = ZoneInfo("Europe/Stockholm")

        synth_bars: list[dict] = []
        for date_str in sorted(raw.keys()):
            s = raw[date_str]
            rth_high = s.get("rth_high")
            rth_low = s.get("rth_low")
            if rth_high is None or rth_low is None:
                continue
            dt = datetime.strptime(date_str, "%Y-%m-%d")
            ts = dt.replace(hour=12, tzinfo=CET).astimezone(timezone.utc)
            synth_bars.append({
                "ts": ts,
                "open": s.get("poc", rth_high),
                "high": rth_high,
                "low": rth_low,
                "close": s.get("poc", rth_low),
            })

        if len(synth_bars) < 5:
            return None

        return compute_multi_tf_swings(synth_bars)
```

- [ ] **Step 2: Update `_enrich_with_bars` to use session summaries**

In `_enrich_with_bars`, replace the `_get_swing_bars` + `compute_multi_tf_swings` block:

Replace:
```python
        # Multi-timeframe swing detection
        from ..market_data.levels import compute_multi_tf_swings
        try:
            swing_bars = await self._get_swing_bars(symbol)
            swing_structure = compute_multi_tf_swings(swing_bars)
        except Exception as e:
            logger.warning("Swing detection failed: %s", e)
            swing_structure = None
```

With:
```python
        # Multi-timeframe swing detection — use session summaries for full history
        try:
            swing_structure = self._load_swing_structure()
        except Exception as e:
            logger.warning("Swing detection failed: %s", e)
            swing_structure = None
```

- [ ] **Step 3: Update `_serialize_swing_structure` for new fields**

Update the module-level `_serialize_swing_structure` function to include BOS/CHoCH:

```python
def _serialize_swing_structure(swing) -> dict:
    """Serialize SwingStructure to JSON-safe dict."""
    def _ev(ev):
        if ev is None:
            return None
        return {
            "price": ev.price, "timestamp": ev.timestamp,
            "event_type": ev.event_type, "swing_type": ev.swing_type,
            "swing_price": ev.swing_price,
        }

    def _tf(tf_swings):
        return {
            "timeframe": tf_swings.timeframe,
            "structure": tf_swings.structure,
            "swing_highs": [
                {"price": s.price, "timestamp": s.timestamp,
                 "type": s.type, "timeframe": s.timeframe}
                for s in tf_swings.swing_highs
            ],
            "swing_lows": [
                {"price": s.price, "timestamp": s.timestamp,
                 "type": s.type, "timeframe": s.timeframe}
                for s in tf_swings.swing_lows
            ],
            "last_bos": _ev(tf_swings.last_bos),
            "last_choch": _ev(tf_swings.last_choch),
            "bos_active": tf_swings.bos_active,
            "choch_active": tf_swings.choch_active,
        }

    return {
        "daily": _tf(swing.daily),
        "weekly": _tf(swing.weekly),
        "monthly": _tf(swing.monthly),
        "trend_alignment": swing.trend_alignment,
    }
```

- [ ] **Step 4: Update `build_expanded_session` level injection**

The existing code that adds swing levels to `levels_list` references `tf_swings.swing_highs`. This still works since the new `TimeframeSwings` has the same fields. But update to only add confirmed swings (they all are now). No code change needed — verify it still works.

- [ ] **Step 5: Update `get_session_levels` to use session summaries too**

In `get_session_levels`, replace the per-day `compute_multi_tf_swings(bars)` call with a single call using session summaries. The swing levels are the same for all displayed days (they're global structural levels, not per-day).

After the `rows` fetch and before the `for date_str in sorted_dates:` loop, add:

```python
        # Compute swing levels once from session summaries (global structural levels)
        swing = self._load_swing_structure()
        swing_data = {
            "daily_swing_high": swing.daily.swing_highs[0].price if swing and swing.daily.swing_highs else None,
            "daily_swing_low": swing.daily.swing_lows[0].price if swing and swing.daily.swing_lows else None,
            "weekly_swing_high": swing.weekly.swing_highs[0].price if swing and swing.weekly.swing_highs else None,
            "weekly_swing_low": swing.weekly.swing_lows[0].price if swing and swing.weekly.swing_lows else None,
            "monthly_swing_high": swing.monthly.swing_highs[0].price if swing and swing.monthly.swing_highs else None,
            "monthly_swing_low": swing.monthly.swing_lows[0].price if swing and swing.monthly.swing_lows else None,
        }
```

Then in the `result_days.append({...})`, replace the per-day swing computation with:
```python
                **swing_data,
```

Remove the old per-day `compute_multi_tf_swings(bars)` call.

- [ ] **Step 6: Delete `_get_swing_bars` method**

It's no longer needed — remove it from `MarketService`.

- [ ] **Step 7: Commit**

```bash
git add backend/src/services/market_service.py
git commit -m "feat(market): use session summaries for swing detection instead of 1m bars"
```

---

### Task 5: Replay Engine — Use MarketStructureEngine

**Files:**
- Modify: `backend/src/rl/data/session_store.py`

- [ ] **Step 1: Update `_compute_swing_from_summaries`**

Replace the existing function to use `MarketStructureEngine` via `compute_multi_tf_swings`:

```python
def _compute_swing_from_summaries(
    summaries: dict[str, SessionSummary],
    current_date: str,
) -> "SwingStructure | None":
    """Build SwingStructure from session summaries for backtesting."""
    from src.market_data.levels import compute_multi_tf_swings
    from datetime import timezone
    from zoneinfo import ZoneInfo

    CET = ZoneInfo("Europe/Stockholm")
    prior_dates = sorted(d for d in summaries if d < current_date)
    if not prior_dates:
        return None

    synth_bars: list[dict] = []
    for d in prior_dates:
        s = summaries[d]
        if s.rth_high is None or s.rth_low is None:
            continue
        dt = datetime.strptime(d, "%Y-%m-%d")
        ts = dt.replace(hour=12, tzinfo=CET).astimezone(timezone.utc)
        synth_bars.append({
            "ts": ts,
            "open": s.poc,
            "high": s.rth_high,
            "low": s.rth_low,
            "close": s.poc,
        })

    if len(synth_bars) < 5:
        return None

    return compute_multi_tf_swings(synth_bars)
```

This is essentially the same as before but now `compute_multi_tf_swings` uses `MarketStructureEngine` internally.

- [ ] **Step 2: Commit**

```bash
git add backend/src/rl/data/session_store.py
git commit -m "feat(rl): replay engine swing detection uses MarketStructureEngine"
```

---

### Task 6: Frontend Types and dqnConfig

**Files:**
- Modify: `frontend/src/types/market.ts`
- Modify: `frontend/src/components/Terminal/pages/dqnConfig.ts`

- [ ] **Step 1: Update `TimeframeSwings` type**

In `frontend/src/types/market.ts`, update the `TimeframeSwings` interface:

```typescript
export interface StructureEvent {
  price: number;
  timestamp: number;
  event_type: 'bos_bullish' | 'bos_bearish' | 'choch_bullish' | 'choch_bearish';
  swing_type: 'swing_high' | 'swing_low';
  swing_price: number;
}

export interface TimeframeSwings {
  timeframe: string;
  structure: 'uptrend' | 'downtrend' | 'reversing_up' | 'reversing_down' | 'ranging';
  swing_highs: SwingLevel[];
  swing_lows: SwingLevel[];
  last_bos: StructureEvent | null;
  last_choch: StructureEvent | null;
  bos_active: boolean;
  choch_active: boolean;
}
```

- [ ] **Step 2: Update `dqnConfig.ts` STRUCTURE array**

Add 6 new feature names to the STRUCTURE array:

```typescript
  // BOS / CHoCH flags
  'bos_d', 'bos_w', 'bos_m',
  'choch_d', 'choch_w', 'choch_m',
```

Update `DQN_SEGMENTS` boundaries — STRUCTURE end shifts from 78 to 84, and all downstream segments shift by +6.

Update the `DQN_INPUTS` builder offsets to match new segment starts.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/types/market.ts frontend/src/components/Terminal/pages/dqnConfig.ts
git commit -m "feat(frontend): add BOS/CHoCH types and update dqnConfig segment boundaries"
```

---

### Task 7: Integration Tests and Verification

**Files:**
- Modify: `backend/tests/test_market_structure.py`
- Modify: `backend/tests/test_swing_multi_tf.py`

- [ ] **Step 1: Add observation dimension test**

Add to `backend/tests/test_market_structure.py`:

```python
def test_observation_dim_with_bos_choch():
    from src.rl.features.observation import build_observation, OBSERVATION_DIM
    from src.rl.config import LevelType
    from src.rl.zone_builder import Zone, ZoneMember

    dummy_member = ZoneMember(name="vwap", level_type=LevelType.VWAP, price=19000.0)
    dummy_zone = Zone(
        center_price=19000.0, upper_bound=19001.0, lower_bound=18999.0,
        members=[dummy_member],
        composition=[1.0 if lt == LevelType.VWAP else 0.0 for lt in LevelType],
        width_ticks=8.0, member_count=1, hierarchy_score=0.5,
    )
    state = {
        "zone": dummy_zone, "all_zones": [dummy_zone],
        "price": 19000.0, "candles": [],
        "vwap_bands": None, "volume_profile": None,
        "session_tpos": None, "session_levels": None,
        "all_levels": [], "orderflow_signals": None,
        "macro": None, "session_context": None,
        "recent_ticks": [],
        "swing_structure": _make_test_swing_structure(),
    }
    obs = build_observation(state)
    assert obs.shape[0] == OBSERVATION_DIM
    assert all(np.isfinite(obs))
```

- [ ] **Step 2: Run full test suite**

Run: `cd backend && python -m pytest tests/test_market_structure.py tests/test_swing_multi_tf.py tests/test_swing_points.py -v`
Expected: All PASSED

- [ ] **Step 3: Verify with real data**

Run: `cd backend && python -c "
import json
from src.market_data.levels import compute_multi_tf_swings
from zoneinfo import ZoneInfo
from datetime import datetime, timezone

CET = ZoneInfo('Europe/Stockholm')
with open('data/rl/session_summaries.json') as f:
    raw = json.load(f)

bars = []
for d in sorted(raw.keys()):
    s = raw[d]
    if s.get('rth_high') and s.get('rth_low'):
        dt = datetime.strptime(d, '%Y-%m-%d')
        ts = dt.replace(hour=12, tzinfo=CET).astimezone(timezone.utc)
        bars.append({'ts': ts, 'open': s['poc'], 'high': s['rth_high'], 'low': s['rth_low'], 'close': s['poc']})

result = compute_multi_tf_swings(bars)
for tf in ['daily', 'weekly', 'monthly']:
    t = getattr(result, tf)
    print(f'{tf}: {t.structure}, SH={len(t.swing_highs)}, SL={len(t.swing_lows)}, BOS={t.bos_active}, CHoCH={t.choch_active}')
    if t.last_bos:
        print(f'  last BOS: {t.last_bos.event_type} at {t.last_bos.price}')
    if t.last_choch:
        print(f'  last CHoCH: {t.last_choch.event_type} at {t.last_choch.price}')
print(f'alignment: {result.trend_alignment}')
"`

Expected: Real structure data with BOS/CHoCH events detected across D/W/M timeframes.

- [ ] **Step 4: Commit**

```bash
git add backend/tests/test_market_structure.py backend/tests/test_swing_multi_tf.py
git commit -m "test: add integration tests for MarketStructureEngine with BOS/CHoCH"
```

---

### Task 8: Clean Up Old Code

**Files:**
- Modify: `backend/src/market_data/levels.py`
- Delete old test expectations if needed

- [ ] **Step 1: Remove dead code from `levels.py`**

Remove `detect_fractal_pivots` and `_classify_structure` functions — they've been replaced by `MarketStructureEngine`. Also remove the old `_TF_CONFIG` dict if still present.

Keep `aggregate_to_timeframe` — it's still used.

- [ ] **Step 2: Update `test_swing_multi_tf.py`**

Remove tests that reference `detect_fractal_pivots` directly (they test the old API). The engine tests in `test_market_structure.py` cover the same functionality now.

Keep the `aggregate_to_timeframe` tests and `compute_multi_tf_swings` integration tests.

- [ ] **Step 3: Run full test suite**

Run: `cd backend && python -m pytest tests/test_market_structure.py tests/test_swing_multi_tf.py tests/test_swing_points.py -v`
Expected: All PASSED

- [ ] **Step 4: Commit**

```bash
git add backend/src/market_data/levels.py backend/tests/test_swing_multi_tf.py
git commit -m "chore: remove dead fractal pivot code, clean up tests"
```
