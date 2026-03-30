# Multi-Timeframe Swing Detection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add daily/weekly/monthly swing level detection with market structure classification (HH/HL/LH/LL) to the DQN agent, level monitor, and trading chart.

**Architecture:** Aggregate 1m bars from `market_candles` into D/W/M OHLC candles, detect fractal pivots per timeframe, classify structure (Dow Theory), feed into DQN via expanded STRUCTURE features (23→32) and new MonitoredLevel types (6 new swing level types). Render on CandleChart as colored dashed lines.

**Tech Stack:** Python 3.10 / NumPy / SQLAlchemy / TypeScript / React / lightweight-charts

---

### Task 1: Backend — Dataclasses and Bar Aggregation

**Files:**
- Modify: `backend/src/market_data/levels.py` (add after `SessionLevels` dataclass, ~line 54)
- Test: `backend/tests/test_swing_multi_tf.py`

- [ ] **Step 1: Write failing test for `aggregate_to_timeframe`**

```python
# backend/tests/test_swing_multi_tf.py
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

from src.market_data.levels import aggregate_to_timeframe, SwingLevel, TimeframeSwings, SwingStructure

CET = ZoneInfo("Europe/Stockholm")


def _make_1m_bars(days: int = 5, base_price: float = 19000.0) -> list[dict]:
    """Generate synthetic 1m bars across multiple trading days (00:00-22:00 CET)."""
    bars = []
    start = datetime(2026, 3, 23, 0, 0, tzinfo=CET)  # Monday
    for d in range(days):
        day_start = start + timedelta(days=d)
        if day_start.weekday() >= 5:  # skip weekends
            continue
        for minute in range(0, 22 * 60, 1):  # 00:00 to 22:00
            ts = day_start + timedelta(minutes=minute)
            # Simple sine wave for price variation
            import math
            progress = d * 22 * 60 + minute
            noise = math.sin(progress / 60.0) * 20
            price = base_price + d * 50 + noise
            bars.append({
                "ts": ts.astimezone(timezone.utc),
                "high": price + 5,
                "low": price - 5,
                "open": price - 2,
                "close": price + 2,
            })
    return bars


def test_aggregate_daily():
    bars = _make_1m_bars(days=5)
    daily = aggregate_to_timeframe(bars, "daily")
    assert len(daily) >= 3  # at least 3 trading days
    assert all(d["high"] >= d["low"] for d in daily)
    assert all(d["open"] > 0 for d in daily)
    assert all("date" in d and "ts" in d for d in daily)
    # Chronological order
    assert daily[0]["ts"] <= daily[-1]["ts"]


def test_aggregate_weekly():
    bars = _make_1m_bars(days=12)  # ~2 weeks
    weekly = aggregate_to_timeframe(bars, "weekly")
    assert len(weekly) >= 1
    assert weekly[0]["high"] >= weekly[0]["low"]


def test_aggregate_monthly():
    bars = _make_1m_bars(days=30)
    monthly = aggregate_to_timeframe(bars, "monthly")
    assert len(monthly) >= 1


def test_aggregate_empty():
    result = aggregate_to_timeframe([], "daily")
    assert result == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_swing_multi_tf.py::test_aggregate_daily -v`
Expected: FAIL with `ImportError: cannot import name 'aggregate_to_timeframe'`

- [ ] **Step 3: Implement dataclasses and `aggregate_to_timeframe`**

Add to `backend/src/market_data/levels.py` after the `SessionLevels` dataclass (after line 53):

```python
@dataclass
class SwingLevel:
    """A detected swing point (fractal pivot)."""
    price: float
    timestamp: int       # epoch seconds
    type: str            # "swing_high" or "swing_low"
    timeframe: str       # "daily", "weekly", "monthly"


@dataclass
class TimeframeSwings:
    """Swing detection result for a single timeframe."""
    timeframe: str       # "daily", "weekly", "monthly"
    structure: str       # "uptrend", "downtrend", "ranging"
    swing_highs: list[SwingLevel] = field(default_factory=list)  # newest first
    swing_lows: list[SwingLevel] = field(default_factory=list)   # newest first


@dataclass
class SwingStructure:
    """Multi-timeframe swing analysis result."""
    daily: TimeframeSwings
    weekly: TimeframeSwings
    monthly: TimeframeSwings
    trend_alignment: float  # -1.0 (all down) to +1.0 (all up)


def aggregate_to_timeframe(
    bars_1m: list[dict],
    timeframe: str,
) -> list[dict]:
    """Aggregate 1m bars into daily/weekly/monthly OHLC candles.

    Uses CET session boundaries:
    - Daily: 00:00-22:00 CET
    - Weekly: Monday 00:00 to Friday 22:00 CET
    - Monthly: 1st 00:00 to last trading day 22:00 CET

    Returns list of {"date": str, "open": float, "high": float, "low": float,
    "close": float, "ts": int} sorted chronologically.
    """
    if not bars_1m:
        return []

    from collections import OrderedDict

    buckets: OrderedDict[str, list[dict]] = OrderedDict()

    for bar in bars_1m:
        bar_ts = bar["ts"]
        if isinstance(bar_ts, str):
            bar_ts = datetime.fromisoformat(bar_ts)
        if bar_ts.tzinfo is None:
            bar_ts = bar_ts.replace(tzinfo=timezone.utc)
        bar_cet = bar_ts.astimezone(CET)

        # Skip bars outside trading hours (after 22:00 CET)
        if bar_cet.hour >= 22:
            continue

        if timeframe == "daily":
            key = bar_cet.date().isoformat()
        elif timeframe == "weekly":
            # ISO week: Monday = 0
            week_start = bar_cet.date() - timedelta(days=bar_cet.weekday())
            key = week_start.isoformat()
        elif timeframe == "monthly":
            key = f"{bar_cet.year}-{bar_cet.month:02d}"
        else:
            raise ValueError(f"Unknown timeframe: {timeframe}")

        if key not in buckets:
            buckets[key] = []
        buckets[key].append(bar)

    result = []
    for key, group in buckets.items():
        highs = [b["high"] for b in group]
        lows = [b["low"] for b in group]
        first_ts = group[0]["ts"]
        if isinstance(first_ts, str):
            first_ts = datetime.fromisoformat(first_ts)
        if first_ts.tzinfo is None:
            first_ts = first_ts.replace(tzinfo=timezone.utc)

        result.append({
            "date": key,
            "open": group[0].get("open", group[0].get("close", highs[0])),
            "high": max(highs),
            "low": min(lows),
            "close": group[-1].get("close", group[-1].get("open", lows[-1])),
            "ts": int(first_ts.timestamp()),
        })

    return result
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_swing_multi_tf.py -v -k "aggregate"`
Expected: 4 PASSED

- [ ] **Step 5: Commit**

```bash
git add backend/src/market_data/levels.py backend/tests/test_swing_multi_tf.py
git commit -m "feat(swing): add dataclasses and bar aggregation for multi-TF swings"
```

---

### Task 2: Backend — Fractal Pivot Detection

**Files:**
- Modify: `backend/src/market_data/levels.py`
- Test: `backend/tests/test_swing_multi_tf.py`

- [ ] **Step 1: Write failing test for `detect_fractal_pivots`**

Add to `backend/tests/test_swing_multi_tf.py`:

```python
from src.market_data.levels import detect_fractal_pivots


def _make_uptrend_candles() -> list[dict]:
    """Candles with clear HH/HL pattern. Lookback=3 needs 7 bars per pivot."""
    return [
        # Rise to swing high 1 at index 4 (high=110)
        {"high": 100, "low": 95, "close": 98, "ts": 1000},
        {"high": 103, "low": 98, "close": 101, "ts": 2000},
        {"high": 106, "low": 101, "close": 104, "ts": 3000},
        {"high": 109, "low": 104, "close": 107, "ts": 4000},
        {"high": 110, "low": 105, "close": 108, "ts": 5000},  # SH1
        # Fall to swing low 1 at index 8 (low=93)
        {"high": 107, "low": 100, "close": 103, "ts": 6000},
        {"high": 104, "low": 97, "close": 100, "ts": 7000},
        {"high": 101, "low": 94, "close": 97, "ts": 8000},
        {"high": 98, "low": 93, "close": 95, "ts": 9000},   # SL1
        # Rise to swing high 2 at index 13 (high=122, HH)
        {"high": 102, "low": 96, "close": 100, "ts": 10000},
        {"high": 108, "low": 102, "close": 106, "ts": 11000},
        {"high": 114, "low": 108, "close": 112, "ts": 12000},
        {"high": 119, "low": 113, "close": 117, "ts": 13000},
        {"high": 122, "low": 116, "close": 120, "ts": 14000}, # SH2 (HH)
        # Fall to swing low 2 at index 18 (low=101, HL)
        {"high": 118, "low": 112, "close": 115, "ts": 15000},
        {"high": 114, "low": 108, "close": 111, "ts": 16000},
        {"high": 110, "low": 104, "close": 107, "ts": 17000},
        {"high": 106, "low": 101, "close": 103, "ts": 18000}, # SL2 (HL)
        # Trailing bars for lookback confirmation
        {"high": 109, "low": 103, "close": 107, "ts": 19000},
        {"high": 112, "low": 106, "close": 110, "ts": 20000},
        {"high": 115, "low": 109, "close": 113, "ts": 21000},
    ]


def test_detect_fractal_pivots_uptrend():
    candles = _make_uptrend_candles()
    highs, lows = detect_fractal_pivots(candles, lookback=3, max_pivots=3)
    assert len(highs) >= 2
    assert len(lows) >= 2
    # Newest first
    assert highs[0].price >= highs[1].price  # HH
    assert lows[0].price >= lows[1].price    # HL
    assert highs[0].timestamp > highs[1].timestamp


def test_detect_fractal_pivots_max_3():
    candles = _make_uptrend_candles()
    highs, lows = detect_fractal_pivots(candles, lookback=3, max_pivots=3)
    assert len(highs) <= 3
    assert len(lows) <= 3


def test_detect_fractal_pivots_empty():
    highs, lows = detect_fractal_pivots([], lookback=3, max_pivots=3)
    assert highs == []
    assert lows == []


def test_detect_fractal_pivots_insufficient():
    candles = [{"high": 100, "low": 95, "close": 98, "ts": i} for i in range(5)]
    highs, lows = detect_fractal_pivots(candles, lookback=3, max_pivots=3)
    assert highs == []
    assert lows == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_swing_multi_tf.py::test_detect_fractal_pivots_uptrend -v`
Expected: FAIL with `ImportError: cannot import name 'detect_fractal_pivots'`

- [ ] **Step 3: Implement `detect_fractal_pivots`**

Add to `backend/src/market_data/levels.py` after `aggregate_to_timeframe`:

```python
def detect_fractal_pivots(
    candles: list[dict],
    lookback: int = 3,
    max_pivots: int = 3,
) -> tuple[list[SwingLevel], list[SwingLevel]]:
    """Detect fractal pivot highs and lows from candle data.

    A swing high at index i requires candles[i].high >= all candles[j].high
    for j in [i-lookback, i+lookback] where j != i. Mirror for swing low.

    Args:
        candles: List of dicts with "high", "low", "ts" keys.
        lookback: Number of bars on each side for pivot confirmation.
        max_pivots: Maximum number of pivots to return per side.

    Returns:
        (swing_highs, swing_lows) — each a list of SwingLevel, newest first.
    """
    n = len(candles)
    if n < 2 * lookback + 1:
        return [], []

    pivot_highs: list[SwingLevel] = []
    pivot_lows: list[SwingLevel] = []

    for i in range(lookback, n - lookback):
        high = candles[i]["high"]
        low = candles[i]["low"]
        ts = candles[i].get("ts", 0)
        if isinstance(ts, datetime):
            ts = int(ts.timestamp())

        is_pivot_high = all(
            high >= candles[j]["high"]
            for j in range(i - lookback, i + lookback + 1) if j != i
        )
        is_pivot_low = all(
            low <= candles[j]["low"]
            for j in range(i - lookback, i + lookback + 1) if j != i
        )

        if is_pivot_high:
            pivot_highs.append(SwingLevel(
                price=high, timestamp=ts,
                type="swing_high", timeframe="",
            ))
        if is_pivot_low:
            pivot_lows.append(SwingLevel(
                price=low, timestamp=ts,
                type="swing_low", timeframe="",
            ))

    # Return last max_pivots, newest first
    return pivot_highs[-max_pivots:][::-1], pivot_lows[-max_pivots:][::-1]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_swing_multi_tf.py -v -k "fractal"`
Expected: 4 PASSED

- [ ] **Step 5: Commit**

```bash
git add backend/src/market_data/levels.py backend/tests/test_swing_multi_tf.py
git commit -m "feat(swing): add fractal pivot detection for swing levels"
```

---

### Task 3: Backend — Structure Classification and `compute_multi_tf_swings`

**Files:**
- Modify: `backend/src/market_data/levels.py`
- Test: `backend/tests/test_swing_multi_tf.py`

- [ ] **Step 1: Write failing tests**

Add to `backend/tests/test_swing_multi_tf.py`:

```python
from src.market_data.levels import compute_multi_tf_swings


def test_compute_multi_tf_swings_uptrend():
    """With enough bars and an uptrend pattern, daily structure should be uptrend."""
    bars = _make_1m_bars(days=10, base_price=19000.0)
    # Add progressive price increase to create uptrend
    for i, bar in enumerate(bars):
        trend = i * 0.01  # gradual uptrend
        bar["high"] += trend
        bar["low"] += trend
        bar["close"] = bar.get("close", bar["high"] - 2) + trend
        bar["open"] = bar.get("open", bar["low"] + 2) + trend
    result = compute_multi_tf_swings(bars)
    assert isinstance(result, SwingStructure)
    assert result.daily.timeframe == "daily"
    assert result.daily.structure in ("uptrend", "downtrend", "ranging")
    assert len(result.daily.swing_highs) <= 3
    assert len(result.daily.swing_lows) <= 3
    assert -1.0 <= result.trend_alignment <= 1.0


def test_compute_multi_tf_swings_graceful_degradation():
    """With only a few days of bars, weekly/monthly should have empty swings."""
    bars = _make_1m_bars(days=5)
    result = compute_multi_tf_swings(bars)
    assert result.daily.timeframe == "daily"
    # Weekly needs ~60 days, monthly ~120 — both should degrade gracefully
    assert result.weekly.structure == "ranging"
    assert result.monthly.structure == "ranging"


def test_compute_multi_tf_swings_empty():
    result = compute_multi_tf_swings([])
    assert result.daily.structure == "ranging"
    assert result.weekly.structure == "ranging"
    assert result.monthly.structure == "ranging"
    assert result.trend_alignment == 0.0


def test_compute_multi_tf_swings_timeframe_labels():
    """All swing levels should have their timeframe set correctly."""
    bars = _make_1m_bars(days=10)
    result = compute_multi_tf_swings(bars)
    for sh in result.daily.swing_highs:
        assert sh.timeframe == "daily"
    for sl in result.daily.swing_lows:
        assert sl.timeframe == "daily"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_swing_multi_tf.py::test_compute_multi_tf_swings_empty -v`
Expected: FAIL with `ImportError: cannot import name 'compute_multi_tf_swings'`

- [ ] **Step 3: Implement `compute_multi_tf_swings`**

Add to `backend/src/market_data/levels.py` after `detect_fractal_pivots`:

```python
_TF_CONFIG = {
    "daily":   {"lookback": 3, "min_candles": 10},
    "weekly":  {"lookback": 2, "min_candles": 6},
    "monthly": {"lookback": 2, "min_candles": 6},
}


def _classify_structure(
    swing_highs: list[SwingLevel],
    swing_lows: list[SwingLevel],
) -> str:
    """Classify market structure from the last 2 swing highs and lows."""
    if len(swing_highs) < 2 or len(swing_lows) < 2:
        return "ranging"

    # swing_highs[0] is newest, [1] is second newest
    sh_new, sh_old = swing_highs[0].price, swing_highs[1].price
    sl_new, sl_old = swing_lows[0].price, swing_lows[1].price

    hh = sh_new > sh_old
    hl = sl_new > sl_old
    lh = sh_new < sh_old
    ll = sl_new < sl_old

    if hh and hl:
        return "uptrend"
    elif lh and ll:
        return "downtrend"
    return "ranging"


def compute_multi_tf_swings(bars_1m: list[dict]) -> SwingStructure:
    """Compute swing structure across daily, weekly, and monthly timeframes.

    Aggregates 1m bars into higher-timeframe candles, detects fractal pivots
    on each, classifies structure (HH/HL/LH/LL) per Dow Theory.

    Degrades gracefully: if not enough candles for a timeframe, returns
    ranging with no swing levels.
    """
    empty_tf = lambda tf: TimeframeSwings(timeframe=tf, structure="ranging")

    if not bars_1m:
        return SwingStructure(
            daily=empty_tf("daily"),
            weekly=empty_tf("weekly"),
            monthly=empty_tf("monthly"),
            trend_alignment=0.0,
        )

    results: dict[str, TimeframeSwings] = {}

    for tf, cfg in _TF_CONFIG.items():
        candles = aggregate_to_timeframe(bars_1m, tf)

        if len(candles) < cfg["min_candles"]:
            results[tf] = empty_tf(tf)
            continue

        highs, lows = detect_fractal_pivots(
            candles, lookback=cfg["lookback"], max_pivots=3,
        )

        # Tag timeframe on each swing level
        for sl in highs:
            sl.timeframe = tf
        for sl in lows:
            sl.timeframe = tf

        structure = _classify_structure(highs, lows)
        results[tf] = TimeframeSwings(
            timeframe=tf,
            structure=structure,
            swing_highs=highs,
            swing_lows=lows,
        )

    # Trend alignment: +1 up, 0 range, -1 down, averaged
    trend_scores = {
        "uptrend": 1.0, "downtrend": -1.0, "ranging": 0.0,
    }
    alignment = sum(
        trend_scores[results[tf].structure] for tf in ("daily", "weekly", "monthly")
    ) / 3.0

    return SwingStructure(
        daily=results["daily"],
        weekly=results["weekly"],
        monthly=results["monthly"],
        trend_alignment=round(alignment, 2),
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_swing_multi_tf.py -v`
Expected: All PASSED

- [ ] **Step 5: Commit**

```bash
git add backend/src/market_data/levels.py backend/tests/test_swing_multi_tf.py
git commit -m "feat(swing): add structure classification and compute_multi_tf_swings"
```

---

### Task 4: RL Config — Add Swing LevelType Entries

**Files:**
- Modify: `backend/src/rl/config.py`
- Modify: `backend/src/rl/zone_builder.py` (hierarchy weights)

- [ ] **Step 1: Add 6 new LevelType entries**

In `backend/src/rl/config.py`, add after `NAKED_POC = "naked_poc"` (line 39):

```python
    # Swing levels (daily/weekly/monthly)
    DAILY_SWING_HIGH = "daily_swing_high"
    DAILY_SWING_LOW = "daily_swing_low"
    WEEKLY_SWING_HIGH = "weekly_swing_high"
    WEEKLY_SWING_LOW = "weekly_swing_low"
    MONTHLY_SWING_HIGH = "monthly_swing_high"
    MONTHLY_SWING_LOW = "monthly_swing_low"
```

Update the docstring from `(27 total)` to `(31 total)`.

- [ ] **Step 2: Add hierarchy weights in `zone_builder.py`**

In `backend/src/rl/zone_builder.py`, add to `_HIERARCHY_WEIGHTS` dict (after the `LevelType.NAKED_POC` entry):

```python
    LevelType.DAILY_SWING_HIGH: 0.8,
    LevelType.DAILY_SWING_LOW: 0.8,
    LevelType.WEEKLY_SWING_HIGH: 0.9,
    LevelType.WEEKLY_SWING_LOW: 0.9,
    LevelType.MONTHLY_SWING_HIGH: 1.0,
    LevelType.MONTHLY_SWING_LOW: 1.0,
```

- [ ] **Step 3: Verify one-hot encoding auto-expands**

Run: `cd backend && python -c "from src.rl.config import LevelType; print(len(LevelType))"`
Expected: `31`

Run: `cd backend && python -c "from src.rl.features.level_features import encode_level_type; from src.rl.config import LevelType; print(len(encode_level_type(LevelType.DAILY_SWING_HIGH)))"`
Expected: `31`

- [ ] **Step 4: Commit**

```bash
git add backend/src/rl/config.py backend/src/rl/zone_builder.py
git commit -m "feat(rl): add 6 swing level types to LevelType enum and zone hierarchy"
```

---

### Task 5: RL Features — Expand STRUCTURE Segment

**Files:**
- Modify: `backend/src/rl/features/structure_features.py`
- Test: `backend/tests/test_swing_multi_tf.py`

- [ ] **Step 1: Write failing test for expanded structure features**

Add to `backend/tests/test_swing_multi_tf.py`:

```python
import numpy as np
from src.rl.features.structure_features import extract_structure_features
from src.market_data.levels import SwingStructure, TimeframeSwings, SwingLevel


def _make_test_swing_structure() -> SwingStructure:
    return SwingStructure(
        daily=TimeframeSwings(
            timeframe="daily", structure="uptrend",
            swing_highs=[
                SwingLevel(price=19500, timestamp=1000, type="swing_high", timeframe="daily"),
                SwingLevel(price=19300, timestamp=800, type="swing_high", timeframe="daily"),
            ],
            swing_lows=[
                SwingLevel(price=19200, timestamp=900, type="swing_low", timeframe="daily"),
                SwingLevel(price=19100, timestamp=700, type="swing_low", timeframe="daily"),
            ],
        ),
        weekly=TimeframeSwings(
            timeframe="weekly", structure="uptrend",
            swing_highs=[SwingLevel(price=19600, timestamp=500, type="swing_high", timeframe="weekly")],
            swing_lows=[SwingLevel(price=18900, timestamp=400, type="swing_low", timeframe="weekly")],
        ),
        monthly=TimeframeSwings(
            timeframe="monthly", structure="ranging",
            swing_highs=[], swing_lows=[],
        ),
        trend_alignment=0.67,
    )


def test_structure_features_with_swings():
    """Structure features should be 32 elements with swing data."""
    swing = _make_test_swing_structure()
    feats = extract_structure_features(
        price=19400.0,
        vwap_bands=None,
        volume_profile=None,
        session_levels=None,
        session_context=None,
        swing_structure=swing,
    )
    assert feats.shape == (32,)
    assert feats[23] == 1.0   # swing_trend_d = uptrend = +1
    assert feats[24] == 1.0   # swing_trend_w = uptrend = +1
    assert feats[25] == 0.0   # swing_trend_m = ranging = 0
    # swing_pos should be in [0, 1]
    assert 0.0 <= feats[29] <= 1.0  # swing_pos_d
    assert all(np.isfinite(feats))


def test_structure_features_without_swings():
    """Without swing data, features 23-31 should be zeros."""
    feats = extract_structure_features(
        price=19400.0,
        vwap_bands=None,
        volume_profile=None,
        session_levels=None,
        session_context=None,
        swing_structure=None,
    )
    assert feats.shape == (32,)
    assert all(feats[23:32] == 0.0)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_swing_multi_tf.py::test_structure_features_with_swings -v`
Expected: FAIL — `extract_structure_features() got unexpected keyword argument 'swing_structure'`

- [ ] **Step 3: Expand `extract_structure_features`**

Replace the full contents of `backend/src/rl/features/structure_features.py`:

```python
"""Market structure and session context feature extraction."""
from __future__ import annotations

import math
import numpy as np

from ...market_data.levels import VWAPBands, VolumeProfile, SessionLevels, SwingStructure
from ..config import TICK_SIZE

_N_FEATURES = 32


def _extract_swing_features(
    price: float,
    swing: SwingStructure | None,
) -> np.ndarray:
    """Extract 9 swing structure features (indices 23-31)."""
    feats = np.zeros(9, dtype=np.float32)
    if swing is None:
        return feats

    trend_map = {"uptrend": 1.0, "downtrend": -1.0, "ranging": 0.0}

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

    return feats


def extract_structure_features(
    price: float,
    vwap_bands: VWAPBands | None,
    volume_profile: VolumeProfile | None,
    session_levels: SessionLevels | None,
    session_context: dict | None,
    swing_structure: SwingStructure | None = None,
) -> np.ndarray:
    """Extract 32 market structure and session context features.

    Feature layout (indices 0-31):
    --- VWAP (0) ---
      0  price_vs_vwap_sd    — (price - vwap) / sd, clipped ±3

    --- Volume Profile (1-5) ---
      1  price_in_va         — 1 if price inside value area
      2  dist_to_poc_ticks   — |price - poc| / tick_size, normalised (÷200)
      3  dist_to_vah_ticks   — (price - vah) / tick_size, normalised (÷200)
      4  dist_to_val_ticks   — (price - val) / tick_size, normalised (÷200)
      5  va_width_ticks      — (vah - val) / tick_size, normalised (÷400)

    --- IB Range (6-8) ---
      6  ib_range_ticks      — (ib_high - ib_low) / tick_size, normalised (÷80)
      7  poor_high           — 1 if price above ib_high (IB extension up)
      8  poor_low            — 1 if price below ib_low (IB extension down)

    --- Market Type one-hot (9-11) ---
      9  trend_day           — 1 if daily_range_pct > 0.02
     10  range_day           — 1 if daily_range_pct < 0.008 + inside VA
     11  neutral_day         — else

    --- Session Context (12-22) ---
     12  minutes_since_rth_norm — minutes since 09:30 ET / 390
     13  session_volume_pct     — session volume as pct of daily expected (0-1)
     14  daily_range_pct        — (daily_high - daily_low) / price, rescaled (÷0.03)
     15  time_of_day_sin        — sin(2π * minute_of_day / 1440)
     16  time_of_day_cos        — cos(2π * minute_of_day / 1440)
     17  session_type_rth       — one-hot RTH
     18  session_type_globex    — one-hot Globex/overnight
     19  session_type_london    — one-hot London
     20  ib_broken_up           — 1 if IB high was broken
     21  ib_broken_down         — 1 if IB low was broken
     22  ib_broken_none         — 1 if IB intact

    --- Swing Structure (23-31) ---
     23  swing_trend_d          — daily structure: -1/0/+1
     24  swing_trend_w          — weekly structure: -1/0/+1
     25  swing_trend_m          — monthly structure: -1/0/+1
     26  swing_dist_d           — signed distance to nearest daily swing (ticks/200)
     27  swing_dist_w           — signed distance to nearest weekly swing (ticks/200)
     28  swing_dist_m           — signed distance to nearest monthly swing (ticks/200)
     29  swing_pos_d            — price position in daily swing range (0-1)
     30  swing_pos_w            — price position in weekly swing range (0-1)
     31  swing_pos_m            — price position in monthly swing range (0-1)

    Returns zeros(32) on fully missing inputs.
    """
    feats = np.zeros(_N_FEATURES, dtype=np.float32)

    # --- VWAP (feat 0) ---
    if vwap_bands is not None:
        vwap = vwap_bands.vwap
        sd = max(vwap_bands.sd1_upper - vwap, 1e-6)
        feats[0] = float(np.clip((price - vwap) / sd, -3.0, 3.0))

    # --- Volume Profile (feats 1-5) ---
    if volume_profile is not None:
        poc = volume_profile.poc
        vah = volume_profile.vah
        val = volume_profile.val

        feats[1] = 1.0 if val <= price <= vah else 0.0
        feats[2] = float(np.clip(abs(price - poc) / TICK_SIZE / 200.0, 0.0, 1.0))
        feats[3] = float(np.clip((price - vah) / TICK_SIZE / 200.0, -1.0, 1.0))
        feats[4] = float(np.clip((price - val) / TICK_SIZE / 200.0, -1.0, 1.0))
        va_width = max(vah - val, 0.0)
        feats[5] = float(np.clip(va_width / TICK_SIZE / 400.0, 0.0, 1.0))

    # --- IB Range (feats 6-8) ---
    ib_high: float | None = None
    ib_low: float | None = None
    if session_levels is not None:
        ib_high = session_levels.ib_high
        ib_low = session_levels.ib_low

    if ib_high is not None and ib_low is not None:
        ib_range = ib_high - ib_low
        feats[6] = min(ib_range / TICK_SIZE / 80.0, 1.0)
        feats[7] = 1.0 if price > ib_high else 0.0
        feats[8] = 1.0 if price < ib_low else 0.0

    # --- Market Type one-hot (feats 9-11) ---
    ctx = session_context or {}
    daily_range_pct = float(ctx.get("daily_range_pct", 0.5))
    price_in_va_bool = feats[1] > 0.5

    if daily_range_pct > 0.02:
        feats[9] = 1.0
    elif daily_range_pct < 0.008 and price_in_va_bool:
        feats[10] = 1.0
    else:
        feats[11] = 1.0

    # --- Session Context (feats 12-22) ---
    minutes_since_rth = float(ctx.get("minutes_since_rth", 0))
    feats[12] = min(minutes_since_rth / 390.0, 1.0)

    session_volume_pct = float(ctx.get("session_volume_pct", 0.5))
    feats[13] = min(max(session_volume_pct, 0.0), 1.0)

    feats[14] = float(np.clip(daily_range_pct / 0.03, 0.0, 1.0))

    minute_of_day = float(ctx.get("minute_of_day", 0))
    angle = 2.0 * math.pi * minute_of_day / 1440.0
    feats[15] = math.sin(angle)
    feats[16] = math.cos(angle)

    session_type = ctx.get("session_type", "rth")
    feats[17] = 1.0 if session_type == "rth" else 0.0
    feats[18] = 1.0 if session_type == "globex" else 0.0
    feats[19] = 1.0 if session_type == "london" else 0.0

    ib_broken = ctx.get("ib_broken", "none")
    feats[20] = 1.0 if ib_broken == "up" else 0.0
    feats[21] = 1.0 if ib_broken == "down" else 0.0
    feats[22] = 1.0 if ib_broken == "none" else 0.0

    # --- Swing Structure (feats 23-31) ---
    feats[23:32] = _extract_swing_features(price, swing_structure)

    return feats
```

- [ ] **Step 4: Update `observation.py` to pass `swing_structure`**

In `backend/src/rl/features/observation.py`, change the `seg_structure` call (line 120-122):

Replace:
```python
    # 3. Structure + session (23)
    seg_structure = extract_structure_features(
        price, vwap_bands, volume_profile, session_levels, session_context
    )
```

With:
```python
    # 3. Structure + session (32)
    swing_structure = state.get("swing_structure")
    seg_structure = extract_structure_features(
        price, vwap_bands, volume_profile, session_levels, session_context,
        swing_structure=swing_structure,
    )
```

Also update the docstring comments at the top of the file: change `structure + session  23` to `structure + session  32` in both zone mode and legacy mode sections. The total is computed dynamically by `OBSERVATION_DIM` so the docstring total is informational — update it to reflect the new structure size increase.

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_swing_multi_tf.py -v -k "structure_features"`
Expected: 2 PASSED

- [ ] **Step 6: Verify observation dimension changed**

Run: `cd backend && python -c "from src.rl.features.observation import OBSERVATION_DIM; print('OBSERVATION_DIM:', OBSERVATION_DIM)"`
Expected: OBSERVATION_DIM should be 9 greater than before (structure segment grew from 23 to 32)

- [ ] **Step 7: Commit**

```bash
git add backend/src/rl/features/structure_features.py backend/src/rl/features/observation.py backend/tests/test_swing_multi_tf.py
git commit -m "feat(rl): expand STRUCTURE segment with 9 swing features (23→32)"
```

---

### Task 6: LevelMonitor — Per-Level Approach Zones and Swing Level Support

**Files:**
- Modify: `backend/src/market_data/level_monitor.py`

- [ ] **Step 1: Add per-level zone fields to `MonitoredLevel`**

In `backend/src/market_data/level_monitor.py`, update the `MonitoredLevel` dataclass (line 26-34):

```python
@dataclass
class MonitoredLevel:
    """A structural level being tracked for proximity."""
    name: str
    price: float
    category: str
    status: LevelStatus = LevelStatus.WATCHING
    touched_at: float = 0.0
    cluster: list[str] = field(default_factory=list)
    approach_price: float | None = None
    approach_ticks: int = 15   # default, overridden for swing levels
    at_level_ticks: int = 5    # default
    reject_ticks: int = 20     # default

    def distance_ticks(self, price: float) -> float:
        return (price - self.price) / TICK_SIZE

    def abs_distance_ticks(self, price: float) -> float:
        return abs(self.distance_ticks(price))
```

- [ ] **Step 2: Update `on_tick` to use per-level thresholds**

In `on_tick()` method (around line 168-195), replace the class constant references:

Replace:
```python
            if dist <= self.AT_LEVEL_TICKS:
```
With:
```python
            if dist <= level.at_level_ticks:
```

Replace:
```python
            elif dist <= self.APPROACHING_TICKS:
```
With:
```python
            elif dist <= level.approach_ticks:
```

Replace:
```python
                if dist > self.REJECT_TICKS:
```
With:
```python
                if dist > level.reject_ticks:
```

- [ ] **Step 3: Add swing level types to `_rebuild_zones` level_type_map**

In `_rebuild_zones()` (line 111-125), add to the `level_type_map` dict:

```python
            "daily_swing_high": RLLevelType.DAILY_SWING_HIGH,
            "daily_swing_low": RLLevelType.DAILY_SWING_LOW,
            "weekly_swing_high": RLLevelType.WEEKLY_SWING_HIGH,
            "weekly_swing_low": RLLevelType.WEEKLY_SWING_LOW,
            "monthly_swing_high": RLLevelType.MONTHLY_SWING_HIGH,
            "monthly_swing_low": RLLevelType.MONTHLY_SWING_LOW,
```

- [ ] **Step 4: Add swing level types to `_build_rl_state` level_type_map**

In `_build_rl_state()` (line 751-765), add the same 6 entries to the `level_type_map` dict.

- [ ] **Step 5: Pass `swing_structure` in `_build_rl_state` and `_build_rl_state_zone`**

In both `_build_rl_state()` and `_build_rl_state_zone()`, add to the returned dict:

```python
            "swing_structure": ctx.get("swing_structure"),
```

- [ ] **Step 6: Update `load_levels` to set per-level approach zones for swing levels**

In `load_levels()`, after existing level loading (after line 107), add zone configuration:

```python
        # Set wider approach zones for swing levels
        _SWING_ZONES = {
            "daily_swing_high": (15, 5, 20),
            "daily_swing_low": (15, 5, 20),
            "weekly_swing_high": (25, 10, 35),
            "weekly_swing_low": (25, 10, 35),
            "monthly_swing_high": (40, 15, 50),
            "monthly_swing_low": (40, 15, 50),
        }
        for level in self._levels:
            zones = _SWING_ZONES.get(level.name)
            if zones:
                level.approach_ticks, level.at_level_ticks, level.reject_ticks = zones
```

- [ ] **Step 7: Commit**

```bash
git add backend/src/market_data/level_monitor.py
git commit -m "feat(monitor): add per-level approach zones and swing level type support"
```

---

### Task 7: MarketService — Compute and Expose Swing Structure

**Files:**
- Modify: `backend/src/services/market_service.py`

- [ ] **Step 1: Add `_get_swing_bars` method**

Add after `_get_session_bars` method (around line 170):

```python
    async def _get_swing_bars(self, symbol: str) -> list[dict]:
        """Get 120 days of 1m bars for swing level computation. DB only, no backfill."""
        from zoneinfo import ZoneInfo
        _CET = ZoneInfo("Europe/Stockholm")

        now = datetime.now(timezone.utc)
        today_cet = now.astimezone(_CET).date()
        start_date = today_cet - timedelta(days=120)
        d_start = datetime(start_date.year, start_date.month, start_date.day, tzinfo=_CET).astimezone(timezone.utc)

        rows = self._filter_halt(self.repo.get_candles(symbol, "1m", d_start, now))
        logger.info("Swing bars: %d from DB (%s to now)", len(rows), start_date)
        return [
            {"ts": r.ts if r.ts.tzinfo else r.ts.replace(tzinfo=timezone.utc),
             "high": r.h, "low": r.l, "open": r.o, "close": r.c}
            for r in rows
        ]
```

- [ ] **Step 2: Call `compute_multi_tf_swings` in `_enrich_with_bars`**

In `_enrich_with_bars` method (after line 555, after the existing `detect_swing_points` call), add:

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

Update the return to include swing_structure. Change:

```python
        return structure, profiles
```

To:

```python
        return structure, profiles, swing_structure
```

- [ ] **Step 3: Update `build_expanded_session` to handle swing_structure**

In `build_expanded_session`, update the `_enrich_with_bars` call and response assembly.

Change (around line 483-499):

```python
        structure = {}
        # Only use DB VP values if session_row is from today; stale previous-day values mislead
        is_today = session_row.date == today
        profiles = {
            "session": {"poc": session_row.poc, "vah": session_row.vah, "val": session_row.val} if is_today else {"poc": None, "vah": None, "val": None},
            "developing_poc": None,
            "developing_poc_direction": None,
            "naked_pocs": [],
        }

        try:
            structure, profiles = await asyncio.wait_for(
                self._enrich_with_bars(symbol, today, session_row, sj),
                timeout=30.0,
            )
        except (asyncio.TimeoutError, Exception) as e:
            logger.warning("Bar enrichment failed/timed out: %s", e)
```

To:

```python
        structure = {}
        swing_structure_data = None
        is_today = session_row.date == today
        profiles = {
            "session": {"poc": session_row.poc, "vah": session_row.vah, "val": session_row.val} if is_today else {"poc": None, "vah": None, "val": None},
            "developing_poc": None,
            "developing_poc_direction": None,
            "naked_pocs": [],
        }

        try:
            structure, profiles, swing_struct = await asyncio.wait_for(
                self._enrich_with_bars(symbol, today, session_row, sj),
                timeout=30.0,
            )
            if swing_struct is not None:
                swing_structure_data = _serialize_swing_structure(swing_struct)
                # Add swing levels to levels_list for LevelMonitor
                for tf_swings in [swing_struct.daily, swing_struct.weekly, swing_struct.monthly]:
                    if tf_swings.swing_highs:
                        sh = tf_swings.swing_highs[0]  # most recent
                        levels_list.append({
                            "type": f"{tf_swings.timeframe}_swing_high",
                            "price_low": sh.price,
                            "price_high": sh.price,
                            "direction": "resistance",
                            "session": tf_swings.timeframe,
                            "is_filled": False,
                        })
                    if tf_swings.swing_lows:
                        sl = tf_swings.swing_lows[0]
                        levels_list.append({
                            "type": f"{tf_swings.timeframe}_swing_low",
                            "price_low": sl.price,
                            "price_high": sl.price,
                            "direction": "support",
                            "session": tf_swings.timeframe,
                            "is_filled": False,
                        })
        except (asyncio.TimeoutError, Exception) as e:
            logger.warning("Bar enrichment failed/timed out: %s", e)
```

Add `swing_structure` to the return dict (after `"levels": levels_list,`):

```python
            "swing_structure": swing_structure_data,
```

- [ ] **Step 4: Add serialization helper**

Add before `build_expanded_session` (or as a module-level function):

```python
def _serialize_swing_structure(swing) -> dict:
    """Serialize SwingStructure to JSON-safe dict."""
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
        }
    return {
        "daily": _tf(swing.daily),
        "weekly": _tf(swing.weekly),
        "monthly": _tf(swing.monthly),
        "trend_alignment": swing.trend_alignment,
    }
```

- [ ] **Step 5: Pass swing_structure into session_context for DQN**

Find where `set_session_context` is called in the compute route (in `backend/src/api/routes/market.py`). Add to the `rl_context` dict:

```python
            "swing_structure": expanded.get("swing_structure"),
```

This ensures `_build_rl_state` / `_build_rl_state_zone` can access it via `ctx.get("swing_structure")`.

- [ ] **Step 6: Add swing levels to `get_session_levels` response**

In `get_session_levels` method (around line 1361-1386), after computing `sl = compute_session_levels(...)`, add swing level fields. After the existing `compute_session_levels` call, add:

```python
            # Compute swing levels for this date
            from ..market_data.levels import compute_multi_tf_swings
            swing = compute_multi_tf_swings(all_bars)
```

Then add to the `result_days.append({...})` dict:

```python
                "daily_swing_high": swing.daily.swing_highs[0].price if swing.daily.swing_highs else None,
                "daily_swing_low": swing.daily.swing_lows[0].price if swing.daily.swing_lows else None,
                "weekly_swing_high": swing.weekly.swing_highs[0].price if swing.weekly.swing_highs else None,
                "weekly_swing_low": swing.weekly.swing_lows[0].price if swing.weekly.swing_lows else None,
                "monthly_swing_high": swing.monthly.swing_highs[0].price if swing.monthly.swing_highs else None,
                "monthly_swing_low": swing.monthly.swing_lows[0].price if swing.monthly.swing_lows else None,
```

Note: `get_session_levels` already fetches multi-day bars with `pad_days`. For swing detection we need more history — update the `pad_days` calculation to fetch at least 120 days if `days` parameter is small:

Replace:
```python
        pad_days = days + (days // 5) * 2 + 3  # pad for weekends
```
With:
```python
        pad_days = max(days + (days // 5) * 2 + 3, 140)  # 140 days for swing detection
```

- [ ] **Step 7: Commit**

```bash
git add backend/src/services/market_service.py backend/src/api/routes/market.py
git commit -m "feat(market): compute and expose multi-TF swing structure in session and levels APIs"
```

---

### Task 8: Frontend Types

**Files:**
- Modify: `frontend/src/types/market.ts`

- [ ] **Step 1: Add SwingLevel and SwingStructure types**

Add after the existing `PriceStructure` interface (around line 225):

```typescript
/** Individual swing point from fractal pivot detection */
export interface SwingLevel {
  price: number;
  timestamp: number;
  type: 'swing_high' | 'swing_low';
  timeframe: 'daily' | 'weekly' | 'monthly';
}

/** Per-timeframe swing analysis */
export interface TimeframeSwings {
  timeframe: string;
  structure: 'uptrend' | 'downtrend' | 'ranging';
  swing_highs: SwingLevel[];
  swing_lows: SwingLevel[];
}

/** Multi-timeframe swing structure from compute_multi_tf_swings() */
export interface SwingStructure {
  daily: TimeframeSwings;
  weekly: TimeframeSwings;
  monthly: TimeframeSwings;
  trend_alignment: number;
}
```

- [ ] **Step 2: Add swing fields to `SessionLevelDay`**

In the `SessionLevelDay` interface (around line 331), add after the existing fields:

```typescript
  // Swing levels (most recent confirmed pivot per timeframe)
  daily_swing_high: number | null;
  daily_swing_low: number | null;
  weekly_swing_high: number | null;
  weekly_swing_low: number | null;
  monthly_swing_high: number | null;
  monthly_swing_low: number | null;
```

- [ ] **Step 3: Add `swing_structure` to `ExpandedSession` type**

Find the `ExpandedSession` or equivalent response type and add:

```typescript
  swing_structure?: SwingStructure;
```

- [ ] **Step 4: Commit**

```bash
git add frontend/src/types/market.ts
git commit -m "feat(types): add SwingLevel, SwingStructure types and expand SessionLevelDay"
```

---

### Task 9: Frontend — CandleChart Swing Level Rendering

**Files:**
- Modify: `frontend/src/components/Terminal/pages/CandleChart.tsx`

- [ ] **Step 1: Add swing level rendering after the PDH/PDL block**

In CandleChart.tsx, find the PDH/PDL rendering block (around line 361-387). After the closing `}` of that block, add:

```typescript
      // --- Swing levels from session-levels API ---
      if (latestSL) {
        const swingLevels: { key: string; price: number | null; label: string; color: string }[] = [
          { key: 'daily_swing_high', price: latestSL.daily_swing_high, label: 'D-SH', color: '#e2e8f0' },
          { key: 'daily_swing_low', price: latestSL.daily_swing_low, label: 'D-SL', color: '#e2e8f0' },
          { key: 'weekly_swing_high', price: latestSL.weekly_swing_high, label: 'W-SH', color: '#3b82f6' },
          { key: 'weekly_swing_low', price: latestSL.weekly_swing_low, label: 'W-SL', color: '#3b82f6' },
          { key: 'monthly_swing_high', price: latestSL.monthly_swing_high, label: 'M-SH', color: '#a855f7' },
          { key: 'monthly_swing_low', price: latestSL.monthly_swing_low, label: 'M-SL', color: '#a855f7' },
        ];

        for (const { key, price, label, color } of swingLevels) {
          if (price == null) continue;
          if (slHidden?.has(key)) continue;
          const y = pSeries.priceToCoordinate(price);
          if (y === null) continue;

          ctx.save();
          ctx.strokeStyle = color;
          ctx.lineWidth = 1;
          ctx.setLineDash([6, 3]);
          ctx.beginPath();
          ctx.moveTo(0, y);
          ctx.lineTo(rect.width, y);
          ctx.stroke();
          ctx.setLineDash([]);
          ctx.font = '9px monospace';
          ctx.fillStyle = color;
          ctx.textAlign = 'left';
          ctx.fillText(label, 3, y - 3);
          ctx.restore();
        }
      }
```

- [ ] **Step 2: Commit**

```bash
git add frontend/src/components/Terminal/pages/CandleChart.tsx
git commit -m "feat(chart): render daily/weekly/monthly swing levels as dashed lines"
```

---

### Task 10: Frontend — Sync dqnConfig.ts with Backend Model

**Files:**
- Modify: `frontend/src/components/Terminal/pages/dqnConfig.ts`

- [ ] **Step 1: Update LEVEL_TYPES array (25→31)**

Replace the existing `LEVEL_TYPES` array:

```typescript
const LEVEL_TYPES = [
  // Volume profile — daily
  'daily_poc', 'daily_vah', 'daily_val',
  // Volume profile — weekly
  'weekly_poc', 'weekly_vah', 'weekly_val',
  // Volume profile — monthly
  'monthly_poc', 'monthly_vah', 'monthly_val',
  // VWAP bands
  'vwap', 'vwap_sd1', 'vwap_sd2', 'vwap_sd3',
  // Session
  'pdh', 'pdl', 'tokyo_high', 'tokyo_low', 'nyib_high', 'nyib_low',
  // TPO
  'tpoc', 'tvah', 'tval', 'tibh', 'tibl',
  // Structure
  'naked_poc',
  // Swing levels
  'daily_swing_high', 'daily_swing_low',
  'weekly_swing_high', 'weekly_swing_low',
  'monthly_swing_high', 'monthly_swing_low',
];
```

- [ ] **Step 2: Update ORDERFLOW array (15→21)**

Replace the existing `ORDERFLOW` array to match backend:

```typescript
const ORDERFLOW = [
  'delta_pct', 'delta_norm', 'cvd_norm', 'cvd_trend',
  'vol_ratio', 'body_ratio', 'spread_ticks', 'pa_ratio',
  'imbal_max', 'stacked_cnt', 'stacked_dir',
  'big_cnt', 'big_net_delta', 'absorption', 'stop_run',
  'delta_accel', 'delta_persist', 'vol_accel', 'cvd_diverge', 'initiative', 'responsive',
];
```

- [ ] **Step 3: Update STRUCTURE array (23→32)**

Replace the existing `STRUCTURE` array:

```typescript
const STRUCTURE = [
  'vwap_sd', 'in_va', 'poc_dist', 'vah_dist', 'val_dist', 'single_prints',
  'ib_range', 'poor_high', 'poor_low',
  'mkt_trend', 'mkt_range', 'mkt_neutral',
  'min_since_rth', 'sess_vol%', 'daily_range%', 'tod_sin', 'tod_cos',
  'sess_rth', 'sess_globex', 'sess_london',
  'ib_break_up', 'ib_break_dn', 'ib_intact',
  // Swing structure
  'swing_trend_d', 'swing_trend_w', 'swing_trend_m',
  'swing_dist_d', 'swing_dist_w', 'swing_dist_m',
  'swing_pos_d', 'swing_pos_w', 'swing_pos_m',
];
```

- [ ] **Step 4: Update DQN_SEGMENTS boundaries**

Replace the `DQN_SEGMENTS` array to match actual backend observation.py:

```typescript
export const DQN_SEGMENTS: DQNSegment[] = [
  { name: 'LEVEL TYPE',  color: '#06b6d4', start: 0,   end: 31 },
  { name: 'ORDERFLOW',   color: '#10b981', start: 31,  end: 52 },
  { name: 'STRUCTURE',   color: '#8b5cf6', start: 52,  end: 84 },
  { name: 'TPO',         color: '#f59e0b', start: 84,  end: 110 },
  { name: 'CANDLES',     color: '#ec4899', start: 110, end: 125 },
  { name: 'CONFLUENCE',  color: '#14b8a6', start: 125, end: 133 },
  { name: 'MACRO',       color: '#ef4444', start: 133, end: 140 },
  { name: 'SETUP',       color: '#f97316', start: 140, end: 154 },
  { name: 'MICRO',       color: '#22d3ee', start: 154, end: 174 },
  { name: 'APPROACH',    color: '#64748b', start: 174, end: 175 },
  { name: 'EXECUTION',   color: '#a3e635', start: 175, end: 182 },
];
```

- [ ] **Step 5: Add TPO, CONFLUENCE, SETUP, MICRO, APPROACH, EXECUTION feature names**

Update the TPO array to match backend (26 features):

```typescript
const TPO = [
  'poc_dist', 'va_width', 'in_va', 'time_at_px',
  'excess_hi', 'excess_lo', 'rotation_f', 'rotation_n',
  'shape_p', 'shape_b', 'shape_d', 'shape_bal', 'reserved',
  // Per-session TPO (13 additional)
  'tokyo_poc_d', 'tokyo_va_w', 'tokyo_excess',
  'london_poc_d', 'london_va_w', 'london_excess',
  'ny_poc_d', 'ny_va_w', 'ny_excess',
  'sess_rotation', 'migration_dir', 'va_overlap', 'poc_convergence',
];
```

Add CONFLUENCE (zone mode uses 5, but frontend shows 8 legacy — keep matching legacy for viz since zone mode has same total dim):

```typescript
const CONFLUENCE = [
  'levels_near', 'cluster_score', 'dist_higher', 'dist_lower', 'hierarchy',
  'fvg_overlap', 'fvg_width', 'sp_overlap',
];
```

Add APPROACH and EXECUTION:

```typescript
const APPROACH = ['approach_dir'];

const EXECUTION = [
  'follow_thru', 'responsive', 'initiative', 'atr_norm', 'vol_anomaly', 'time_at_level', 'urgency',
];
```

- [ ] **Step 6: Update the DQN_INPUTS builder**

Replace the builder at the bottom:

```typescript
export const DQN_INPUTS: DQNInputDef[] = [
  ...LEVEL_TYPES.map((label, i) => ({ index: i, label, segment: 'LEVEL TYPE' })),
  ...ORDERFLOW.map((label, i) => ({ index: 31 + i, label, segment: 'ORDERFLOW' })),
  ...STRUCTURE.map((label, i) => ({ index: 52 + i, label, segment: 'STRUCTURE' })),
  ...TPO.map((label, i) => ({ index: 84 + i, label, segment: 'TPO' })),
  ...CANDLES.map((label, i) => ({ index: 110 + i, label, segment: 'CANDLES' })),
  ...CONFLUENCE.map((label, i) => ({ index: 125 + i, label, segment: 'CONFLUENCE' })),
  ...MACRO.map((label, i) => ({ index: 133 + i, label, segment: 'MACRO' })),
  ...SETUP.map((label, i) => ({ index: 140 + i, label, segment: 'SETUP' })),
  ...MICRO.map((label, i) => ({ index: 154 + i, label, segment: 'MICRO' })),
  ...APPROACH.map((label, i) => ({ index: 174 + i, label, segment: 'APPROACH' })),
  ...EXECUTION.map((label, i) => ({ index: 175 + i, label, segment: 'EXECUTION' })),
];
```

- [ ] **Step 7: Commit**

```bash
git add frontend/src/components/Terminal/pages/dqnConfig.ts
git commit -m "feat(dqn-viz): sync dqnConfig.ts with backend 176-dim observation vector"
```

---

### Task 11: Final Integration Test

**Files:**
- Test: `backend/tests/test_swing_multi_tf.py`

- [ ] **Step 1: Write integration test for observation dimension**

Add to `backend/tests/test_swing_multi_tf.py`:

```python
def test_observation_dim_with_swings():
    """Full observation vector should be 176 with swing structure."""
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
        "zone": dummy_zone,
        "all_zones": [dummy_zone],
        "price": 19000.0,
        "candles": [],
        "vwap_bands": None,
        "volume_profile": None,
        "session_tpos": None,
        "session_levels": None,
        "all_levels": [],
        "orderflow_signals": None,
        "macro": None,
        "session_context": None,
        "recent_ticks": [],
        "swing_structure": _make_test_swing_structure(),
    }
    obs = build_observation(state)
    # OBSERVATION_DIM is computed dynamically from actual segment sizes
    assert obs.shape[0] == OBSERVATION_DIM
    # Verify structure segment grew by 9 (was 23, now 32)
    assert all(np.isfinite(obs))


def test_level_type_enum_count():
    from src.rl.config import LevelType
    assert len(LevelType) == 31
```

- [ ] **Step 2: Run all tests**

Run: `cd backend && python -m pytest tests/test_swing_multi_tf.py -v`
Expected: All PASSED

Run: `cd backend && python -m pytest tests/test_swing_points.py -v`
Expected: All PASSED (existing tests still work)

- [ ] **Step 3: Commit**

```bash
git add backend/tests/test_swing_multi_tf.py
git commit -m "test: add integration tests for full observation vector with swing features"
```

---

### Task 12: Update RL Network Architecture

**Files:**
- Modify: `backend/src/rl/agent/network.py` (if input dim is hardcoded)
- Modify: `backend/src/rl/config.py`

- [ ] **Step 1: Verify OBSERVATION_DIM is dynamic**

Run: `cd backend && python -c "from src.rl.features.observation import OBSERVATION_DIM; print('OBSERVATION_DIM:', OBSERVATION_DIM)"`
Expected: `OBSERVATION_DIM: 176`

The network should auto-detect input dim from `OBSERVATION_DIM`. Verify:

Run: `cd backend && grep -n "OBSERVATION_DIM\|input_dim\|176\|167" src/rl/agent/network.py | head -20`

If the network uses `OBSERVATION_DIM` dynamically (imported from observation.py), no changes needed. If it hardcodes 167, update to use the import.

- [ ] **Step 2: Delete any stale model checkpoints**

Existing model weights are incompatible with the new 176-dim input. The model will reinitialize on next training run. No action needed unless there's a checkpoint that would fail to load — check and warn.

Run: `ls backend/data/rl/checkpoints/ 2>/dev/null || echo "No checkpoints dir"`

- [ ] **Step 3: Commit (if any changes)**

```bash
git add -A && git commit -m "chore(rl): ensure network uses dynamic OBSERVATION_DIM (176)"
```
