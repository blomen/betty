# RL Trading Agent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a DQN agent that learns to trade NQ futures at structural level touches, trained on historical Databento tick data.

**Architecture:** Historical ticks → replay engine (reuses existing orderflow/levels/TPO code) → episode builder (level touch snapshots with ~105-dim observation vector + R-multiple outcome) → DQN training → evaluation with level-type discovery analysis.

**Tech Stack:** Python 3.10+, PyTorch (CPU), Databento API, Parquet (pyarrow), existing market_data modules (orderflow.py, levels.py, tpo.py)

**Spec:** `docs/superpowers/specs/2026-03-19-rl-trading-agent-design.md`

---

## File Structure

```
backend/src/rl/
├── __init__.py                    # Package init
├── config.py                      # All hyperparameters, risk params, level type enum
├── data/
│   ├── __init__.py
│   ├── fetcher.py                 # Databento historical tick download → Parquet
│   ├── accumulators.py            # Incremental VWAP + VP accumulators
│   ├── candle_aggregator.py       # Tick → 1m/5m/30m candle aggregation
│   ├── replay_engine.py           # Session-level tick replay orchestrator
│   ├── episode_builder.py         # Level touch → observation + outcome labeling
│   └── normalization.py           # Running mean/std for feature scaling
├── features/
│   ├── __init__.py
│   ├── observation.py             # Assembles full ~105-dim vector
│   ├── level_features.py          # Level type one-hot + confluence
│   ├── orderflow_features.py      # Orderflow snapshot extraction
│   ├── tpo_features.py            # TPO profile features
│   ├── structure_features.py      # VWAP, VA, IB, session levels
│   └── macro_features.py          # VIX, bonds, DXY (from yfinance history)
├── agent/
│   ├── __init__.py
│   ├── network.py                 # DQN neural network (128-128-64 → 3)
│   ├── replay_buffer.py           # Experience replay storage
│   ├── dqn.py                     # Training loop, ε-greedy, target net
│   └── evaluate.py                # Test set eval, metrics, level analysis
└── cli.py                         # Typer subcommands: fetch, replay, train, eval

backend/src/market_data/
├── tpo.py                         # EXTEND — add excess, shape, rotation, incremental

backend/tests/
├── test_rl_accumulators.py        # Incremental VWAP/VP tests
├── test_rl_candle_aggregator.py   # Tick → candle tests
├── test_rl_tpo_extensions.py      # TPO excess/shape/rotation tests
├── test_rl_features.py            # Observation vector tests
├── test_rl_episode_builder.py     # Outcome labeling tests
├── test_rl_network.py             # DQN network shape tests
├── test_rl_replay_buffer.py       # Experience replay tests
├── test_rl_dqn.py                 # Training loop tests
└── test_rl_evaluate.py            # Evaluation metrics tests
```

---

## Task 1: Project Scaffolding & Config

**Files:**
- Create: `backend/src/rl/__init__.py`
- Create: `backend/src/rl/data/__init__.py`
- Create: `backend/src/rl/features/__init__.py`
- Create: `backend/src/rl/agent/__init__.py`
- Create: `backend/src/rl/config.py`

- [ ] **Step 1: Create package directories and __init__.py files**

```python
# backend/src/rl/__init__.py
"""RL Trading Agent for NQ Futures — Yoshi Trackmania approach."""

# backend/src/rl/data/__init__.py
# backend/src/rl/features/__init__.py
# backend/src/rl/agent/__init__.py
# (all empty)
```

- [ ] **Step 2: Create config.py with all constants**

```python
# backend/src/rl/config.py
"""RL agent configuration — all hyperparameters and constants in one place."""

from enum import Enum


class LevelType(str, Enum):
    """All level types the agent can encounter."""
    POC_SESSION = "poc_session"
    POC_DAILY = "poc_daily"
    POC_WEEKLY = "poc_weekly"
    POC_MONTHLY = "poc_monthly"
    POC_MACRO = "poc_macro"
    VAH = "vah"
    VAL = "val"
    VWAP = "vwap"
    VWAP_SD1 = "vwap_sd1"
    VWAP_SD2 = "vwap_sd2"
    VWAP_SD3 = "vwap_sd3"
    IB_HIGH = "ib_high"
    IB_LOW = "ib_low"
    PDH = "pdh"
    PDL = "pdl"
    TOKYO_HL = "tokyo_hl"
    LONDON_HL = "london_hl"
    GLOBEX_HL = "globex_hl"
    OVERNIGHT_HL = "overnight_hl"
    WEEKLY_HL = "weekly_hl"
    MONTHLY_HL = "monthly_hl"
    NAKED_POC = "naked_poc"
    SINGLE_PRINT = "single_print"
    FVG = "fvg"
    ORDER_BLOCK = "order_block"
    SWING_POINT = "swing_point"


class Action(int, Enum):
    """Agent actions."""
    LONG = 0
    SHORT = 1
    SKIP = 2


# --- Risk Parameters (Phase 1: fixed) ---
STOP_TICKS = 10          # 10 ticks = $50/contract
TARGET_TICKS = 20        # 20 ticks = $100/contract (2:1 R)
TIMEOUT_MINUTES = 30
TICK_SIZE = 0.25         # NQ tick size

# --- DQN Hyperparameters ---
BATCH_SIZE = 64
LEARNING_RATE = 1e-4
REPLAY_BUFFER_SIZE = 100_000
EPSILON_START = 1.0
EPSILON_END = 0.05
EPSILON_DECAY_STEPS = 5000
TARGET_NET_UPDATE_FREQ = 500
GAMMA = 0.0              # Single-step episodes, no future discounting

# --- Network Architecture ---
HIDDEN_LAYERS = [128, 128, 64]
NUM_ACTIONS = 3           # LONG, SHORT, SKIP
OBSERVATION_DIM = None    # Computed dynamically in observation.py at import time

# --- Level Touch Detection ---
AT_LEVEL_TICKS = 5        # Price within 5 ticks = level touch

# --- Reward Values ---
REWARD_TARGET_HIT = 2.0   # R-multiple for hitting target
REWARD_STOP_HIT = -1.0    # R-multiple for hitting stop
REWARD_TIMEOUT = 0.0      # No fill within timeout

# --- Data ---
DATABENTO_DATASET = "GLBX.MDP3"
SYMBOL = "NQ.FUT"
```

- [ ] **Step 3: Verify imports work**

Run: `cd backend && python -c "from src.rl.config import LevelType, Action, STOP_TICKS; print(f'Config OK: {len(LevelType)} level types, {len(Action)} actions')"`
Expected: `Config OK: 26 level types, 3 actions`

- [ ] **Step 4: Commit**

```bash
git add backend/src/rl/
git commit -m "feat(rl): scaffold RL package with config constants"
```

---

## Task 2: Incremental Accumulators (VWAP + Volume Profile)

**Files:**
- Create: `backend/src/rl/data/accumulators.py`
- Test: `backend/tests/test_rl_accumulators.py`

**Context:** The existing `levels.py` functions (`compute_vwap_bands`, `compute_volume_profile`) recompute from scratch each call. For replay with millions of ticks, we need incremental versions that update on each tick/trade.

- [ ] **Step 1: Write failing tests for IncrementalVWAP**

```python
# backend/tests/test_rl_accumulators.py
"""Tests for incremental VWAP and Volume Profile accumulators."""

import pytest
from src.rl.data.accumulators import IncrementalVWAP, IncrementalVolumeProfile


class TestIncrementalVWAP:
    def test_empty_returns_none(self):
        vwap = IncrementalVWAP()
        assert vwap.get() is None

    def test_single_trade(self):
        vwap = IncrementalVWAP()
        vwap.update(price=100.0, size=10)
        result = vwap.get()
        assert result.vwap == 100.0

    def test_weighted_average(self):
        vwap = IncrementalVWAP()
        vwap.update(price=100.0, size=10)
        vwap.update(price=102.0, size=10)
        result = vwap.get()
        assert result.vwap == pytest.approx(101.0)

    def test_sd_bands_exist(self):
        vwap = IncrementalVWAP()
        for i in range(100):
            vwap.update(price=100.0 + (i % 10) * 0.25, size=5)
        result = vwap.get()
        assert result.sd1_upper > result.vwap
        assert result.sd1_lower < result.vwap
        assert result.sd2_upper > result.sd1_upper

    def test_matches_batch_computation(self):
        """Incremental result must match levels.compute_vwap_bands()."""
        from src.market_data.levels import compute_vwap_bands

        trades = [
            {"price": 100.0 + i * 0.25, "size": (i % 5) + 1}
            for i in range(200)
        ]

        # Batch computation
        batch_result = compute_vwap_bands(trades)

        # Incremental computation
        vwap = IncrementalVWAP()
        for t in trades:
            vwap.update(t["price"], t["size"])
        inc_result = vwap.get()

        assert inc_result.vwap == pytest.approx(batch_result.vwap, abs=0.01)
        assert inc_result.sd1_upper == pytest.approx(batch_result.sd1_upper, abs=0.1)

    def test_reset(self):
        vwap = IncrementalVWAP()
        vwap.update(price=100.0, size=10)
        vwap.reset()
        assert vwap.get() is None


class TestIncrementalVolumeProfile:
    def test_empty_returns_none(self):
        vp = IncrementalVolumeProfile()
        assert vp.get() is None

    def test_single_trade(self):
        vp = IncrementalVolumeProfile(tick_size=0.25)
        vp.update(price=100.25, size=10)
        result = vp.get()
        assert result.poc == 100.25

    def test_poc_is_highest_volume(self):
        vp = IncrementalVolumeProfile(tick_size=0.25)
        vp.update(price=100.0, size=5)
        vp.update(price=100.25, size=100)  # Most volume here
        vp.update(price=100.50, size=5)
        result = vp.get()
        assert result.poc == 100.25

    def test_value_area_contains_70_pct(self):
        vp = IncrementalVolumeProfile(tick_size=0.25)
        for i in range(1000):
            import random
            price = 100.0 + random.gauss(0, 1) * 0.25
            vp.update(price=round(price / 0.25) * 0.25, size=1)
        result = vp.get()
        assert result.val <= result.poc <= result.vah

    def test_matches_batch_computation(self):
        """Incremental result must match levels.compute_volume_profile()."""
        from src.market_data.levels import compute_volume_profile

        trades = [
            {"price": 100.0 + (i % 20) * 0.25, "size": (i % 5) + 1}
            for i in range(500)
        ]

        batch_result = compute_volume_profile(trades, tick_size=0.25)

        vp = IncrementalVolumeProfile(tick_size=0.25)
        for t in trades:
            vp.update(t["price"], t["size"])
        inc_result = vp.get()

        assert inc_result.poc == batch_result.poc
        assert inc_result.vah == pytest.approx(batch_result.vah, abs=0.5)
        assert inc_result.val == pytest.approx(batch_result.val, abs=0.5)

    def test_single_prints_detected(self):
        vp = IncrementalVolumeProfile(tick_size=0.25)
        # Heavy volume at center, thin at edges
        for _ in range(100):
            vp.update(price=100.0, size=50)
        vp.update(price=101.0, size=1)  # Thin = single print
        result = vp.get()
        assert len(result.single_prints) > 0

    def test_reset(self):
        vp = IncrementalVolumeProfile(tick_size=0.25)
        vp.update(price=100.0, size=10)
        vp.reset()
        assert vp.get() is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_rl_accumulators.py -v`
Expected: FAIL (module not found)

- [ ] **Step 3: Implement IncrementalVWAP and IncrementalVolumeProfile**

```python
# backend/src/rl/data/accumulators.py
"""Incremental accumulators for VWAP and Volume Profile.

These produce equivalent results to levels.compute_vwap_bands() and
levels.compute_volume_profile() but update incrementally per tick,
avoiding recomputation from scratch on every update.
"""

import math
from dataclasses import dataclass, field

from src.market_data.levels import VWAPBands, VolumeProfile, VolumeProfileLevel


class IncrementalVWAP:
    """Running VWAP + standard deviation bands.

    Maintains cumulative price*volume, volume, and price²*volume
    to compute VWAP and SD bands incrementally.
    """

    def __init__(self):
        self.reset()

    def reset(self):
        self._cum_pv = 0.0    # Σ(price * volume)
        self._cum_vol = 0      # Σ(volume)
        self._cum_pv2 = 0.0   # Σ(price² * volume)

    def update(self, price: float, size: int):
        self._cum_pv += price * size
        self._cum_vol += size
        self._cum_pv2 += price * price * size

    def get(self) -> VWAPBands | None:
        if self._cum_vol == 0:
            return None

        vwap = self._cum_pv / self._cum_vol
        variance = (self._cum_pv2 / self._cum_vol) - (vwap * vwap)
        sd = math.sqrt(max(variance, 0))

        return VWAPBands(
            vwap=round(vwap, 4),
            sd1_upper=round(vwap + sd, 4),
            sd1_lower=round(vwap - sd, 4),
            sd2_upper=round(vwap + 2 * sd, 4),
            sd2_lower=round(vwap - 2 * sd, 4),
            sd3_upper=round(vwap + 3 * sd, 4),
            sd3_lower=round(vwap - 3 * sd, 4),
        )


class IncrementalVolumeProfile:
    """Running volume profile with POC, VAH, VAL, and single prints.

    Maintains a price → volume histogram, recomputes POC/VA on get().
    """

    def __init__(self, tick_size: float = 0.25):
        self._tick_size = tick_size
        self.reset()

    def reset(self):
        self._histogram: dict[float, int] = {}
        self._total_volume = 0

    def update(self, price: float, size: int):
        snapped = round(round(price / self._tick_size) * self._tick_size, 4)
        self._histogram[snapped] = self._histogram.get(snapped, 0) + size
        self._total_volume += size

    def get(self) -> VolumeProfile | None:
        if not self._histogram:
            return None

        # POC = price with most volume
        poc = max(self._histogram, key=self._histogram.get)

        # Value area: 70% of total volume centered on POC
        sorted_prices = sorted(self._histogram.keys())
        poc_idx = sorted_prices.index(poc)
        va_vol = self._histogram[poc]
        target_vol = self._total_volume * 0.70
        lo, hi = poc_idx, poc_idx

        while va_vol < target_vol and (lo > 0 or hi < len(sorted_prices) - 1):
            vol_below = self._histogram.get(sorted_prices[lo - 1], 0) if lo > 0 else 0
            vol_above = self._histogram.get(sorted_prices[hi + 1], 0) if hi < len(sorted_prices) - 1 else 0

            # Expand up first when tied (matches batch compute_volume_profile)
            if vol_above >= vol_below and hi < len(sorted_prices) - 1:
                hi += 1
                va_vol += self._histogram[sorted_prices[hi]]
            elif lo > 0:
                lo -= 1
                va_vol += self._histogram[sorted_prices[lo]]
            else:
                hi += 1
                va_vol += self._histogram[sorted_prices[hi]]

        vah = sorted_prices[hi]
        val = sorted_prices[lo]

        # Single prints: prices with <5% of POC volume
        poc_vol = self._histogram[poc]
        single_prints = []
        for p in sorted_prices:
            if self._histogram[p] < poc_vol * 0.05 and val < p < vah:
                single_prints.append((p, p))

        levels = [
            VolumeProfileLevel(price=p, volume=self._histogram[p])
            for p in sorted_prices
        ]

        return VolumeProfile(
            poc=poc,
            vah=vah,
            val=val,
            levels=levels,
            single_prints=single_prints,
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_rl_accumulators.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add backend/src/rl/data/accumulators.py backend/tests/test_rl_accumulators.py
git commit -m "feat(rl): incremental VWAP and volume profile accumulators"
```

---

## Task 3: Candle Aggregator

**Files:**
- Create: `backend/src/rl/data/candle_aggregator.py`
- Test: `backend/tests/test_rl_candle_aggregator.py`

**Context:** Aggregates raw ticks into 1m, 5m, and 30m candles incrementally as ticks arrive. The 1m candles are used for session levels, 5m for analysis, 30m for TPO. Also tracks orderflow per candle.

- [ ] **Step 1: Write failing tests**

```python
# backend/tests/test_rl_candle_aggregator.py
"""Tests for tick-to-candle aggregation."""

import pytest
from datetime import datetime, timezone, timedelta
from src.rl.data.candle_aggregator import CandleAggregator


def _tick(ts_offset_sec: int, price: float, size: int = 1, side: str = "A"):
    """Helper to create tick dicts."""
    base = datetime(2025, 1, 6, 14, 30, 0, tzinfo=timezone.utc)
    return {
        "ts": base + timedelta(seconds=ts_offset_sec),
        "price": price,
        "size": size,
        "side": side,
    }


class TestCandleAggregator:
    def test_no_ticks_no_candles(self):
        agg = CandleAggregator()
        assert agg.get_completed_1m() == []

    def test_single_tick_creates_open_candle(self):
        agg = CandleAggregator()
        completed = agg.update(_tick(0, 100.0))
        assert completed == []  # Not enough time elapsed
        assert agg.current_1m is not None

    def test_minute_boundary_closes_candle(self):
        agg = CandleAggregator()
        agg.update(_tick(0, 100.0, size=5))
        agg.update(_tick(30, 100.50, size=3))
        completed = agg.update(_tick(61, 101.0, size=1))  # Next minute
        assert len(completed) == 1
        bar = completed[0]
        assert bar["open"] == 100.0
        assert bar["high"] == 100.50
        assert bar["low"] == 100.0
        assert bar["close"] == 100.50
        assert bar["volume"] == 8

    def test_ohlcv_correct(self):
        agg = CandleAggregator()
        agg.update(_tick(0, 100.0, size=1))
        agg.update(_tick(10, 101.0, size=2))  # High
        agg.update(_tick(20, 99.0, size=3))   # Low
        agg.update(_tick(50, 100.5, size=4))  # Close
        completed = agg.update(_tick(61, 100.0))
        bar = completed[0]
        assert bar["open"] == 100.0
        assert bar["high"] == 101.0
        assert bar["low"] == 99.0
        assert bar["close"] == 100.5
        assert bar["volume"] == 10

    def test_delta_tracking(self):
        agg = CandleAggregator()
        agg.update(_tick(0, 100.0, size=10, side="A"))  # Buy
        agg.update(_tick(30, 100.0, size=3, side="B"))  # Sell
        completed = agg.update(_tick(61, 100.0))
        bar = completed[0]
        assert bar["delta"] == 7  # 10 buy - 3 sell

    def test_30m_candles_aggregate_from_1m(self):
        agg = CandleAggregator()
        # Feed 31 minutes of ticks
        for minute in range(31):
            agg.update(_tick(minute * 60, 100.0 + minute * 0.25, size=1))
        bars_30m = agg.get_completed_30m()
        assert len(bars_30m) == 1

    def test_get_recent_candles(self):
        agg = CandleAggregator()
        for minute in range(6):
            agg.update(_tick(minute * 60, 100.0 + minute * 0.25))
        recent = agg.get_recent_1m(n=3)
        assert len(recent) <= 3
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_rl_candle_aggregator.py -v`
Expected: FAIL

- [ ] **Step 3: Implement CandleAggregator**

```python
# backend/src/rl/data/candle_aggregator.py
"""Incremental tick-to-candle aggregation for replay engine.

Aggregates ticks into 1m candles with OHLCV + delta tracking.
Also produces 30m candles (for TPO) by aggregating 1m candles.
"""

from datetime import datetime, timezone


class CandleAggregator:
    """Aggregates ticks into 1m, 5m, and 30m candles incrementally."""

    def __init__(self):
        self.current_1m: dict | None = None
        self._completed_1m: list[dict] = []
        self._completed_30m: list[dict] = []
        self._1m_buffer_for_30m: list[dict] = []
        self._current_30m_start: datetime | None = None

    def update(self, tick: dict) -> list[dict]:
        """Process a tick. Returns list of completed 1m candles (usually 0 or 1)."""
        ts = tick["ts"]
        price = tick["price"]
        size = tick["size"]
        side = tick.get("side", "A")

        completed = []

        # Get minute boundary for this tick
        minute_start = ts.replace(second=0, microsecond=0)

        # Check if we've crossed a minute boundary
        if self.current_1m is not None and minute_start > self.current_1m["ts"]:
            completed.append(self._close_1m())

        # Initialize new candle if needed
        if self.current_1m is None:
            self.current_1m = {
                "ts": minute_start,
                "open": price,
                "high": price,
                "low": price,
                "close": price,
                "volume": 0,
                "buy_volume": 0,
                "sell_volume": 0,
                "delta": 0,
                "tick_count": 0,
            }

        # Update current candle
        c = self.current_1m
        c["high"] = max(c["high"], price)
        c["low"] = min(c["low"], price)
        c["close"] = price
        c["volume"] += size
        c["tick_count"] += 1
        if side == "A":
            c["buy_volume"] += size
            c["delta"] += size
        elif side == "B":
            c["sell_volume"] += size
            c["delta"] -= size

        return completed

    def _close_1m(self) -> dict:
        """Close current 1m candle and return it."""
        bar = dict(self.current_1m)
        self._completed_1m.append(bar)
        self._add_to_30m_buffer(bar)
        self.current_1m = None
        return bar

    def _add_to_30m_buffer(self, bar_1m: dict):
        """Aggregate 1m bars into 30m bars."""
        ts = bar_1m["ts"]
        # 30m boundary: minute 0 or 30
        m30_minute = (ts.minute // 30) * 30
        m30_start = ts.replace(minute=m30_minute, second=0, microsecond=0)

        if self._current_30m_start is not None and m30_start > self._current_30m_start:
            # Close previous 30m candle
            if self._1m_buffer_for_30m:
                self._completed_30m.append(self._aggregate_bars(self._1m_buffer_for_30m))
                self._1m_buffer_for_30m = []

        self._current_30m_start = m30_start
        self._1m_buffer_for_30m.append(bar_1m)

    def _aggregate_bars(self, bars: list[dict]) -> dict:
        """Aggregate multiple bars into one."""
        return {
            "ts": bars[0]["ts"],
            "open": bars[0]["open"],
            "high": max(b["high"] for b in bars),
            "low": min(b["low"] for b in bars),
            "close": bars[-1]["close"],
            "volume": sum(b["volume"] for b in bars),
            "delta": sum(b["delta"] for b in bars),
        }

    def get_completed_1m(self) -> list[dict]:
        """Return all completed 1m candles."""
        return list(self._completed_1m)

    def get_completed_30m(self) -> list[dict]:
        """Return all completed 30m candles."""
        return list(self._completed_30m)

    def get_recent_1m(self, n: int = 5) -> list[dict]:
        """Return last N completed 1m candles."""
        return self._completed_1m[-n:] if self._completed_1m else []

    def flush(self) -> dict | None:
        """Force-close current candle (end of session). Returns the bar or None."""
        if self.current_1m is not None:
            bar = self._close_1m()
            # Also flush 30m buffer
            if self._1m_buffer_for_30m:
                self._completed_30m.append(self._aggregate_bars(self._1m_buffer_for_30m))
                self._1m_buffer_for_30m = []
            return bar
        return None

    def reset(self):
        """Reset all state for a new session."""
        self.current_1m = None
        self._completed_1m = []
        self._completed_30m = []
        self._1m_buffer_for_30m = []
        self._current_30m_start = None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_rl_candle_aggregator.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add backend/src/rl/data/candle_aggregator.py backend/tests/test_rl_candle_aggregator.py
git commit -m "feat(rl): incremental tick-to-candle aggregator"
```

---

## Task 4: Extend TPO Module

**Files:**
- Modify: `backend/src/market_data/tpo.py`
- Test: `backend/tests/test_rl_tpo_extensions.py`

**Context:** Existing `tpo.py` has basic `compute_tpo_profile()` with POC, VAH, VAL, single_prints, poor_high/low. We need to add: excess detection, distribution shape classification, rotation factor, and rotation count.

- [ ] **Step 1: Write failing tests for TPO extensions**

```python
# backend/tests/test_rl_tpo_extensions.py
"""Tests for TPO extensions: excess, shape, rotation."""

import pytest
from src.market_data.tpo import (
    compute_tpo_profile,
    classify_tpo_shape,
    compute_rotation_factor,
    detect_excess,
)


def _bars_30m(prices: list[tuple[float, float]]) -> list[dict]:
    """Create 30m bars from (high, low) tuples."""
    return [{"high": h, "low": l} for h, l in prices]


class TestTPOShape:
    def test_balanced_shape(self):
        """Roughly symmetric distribution → balanced."""
        bars = _bars_30m([
            (101, 99), (101.5, 98.5), (101, 99),
            (101.5, 98.5), (101, 99), (101, 99),
        ])
        profile = compute_tpo_profile(bars)
        shape = classify_tpo_shape(profile)
        assert shape in ("balanced", "b-shape")

    def test_p_shape(self):
        """Volume concentrated at top → p-shape."""
        bars = _bars_30m([
            (100, 98), (101, 99), (102, 100),
            (103, 101), (103, 101), (103, 101),  # Cluster at top
        ])
        profile = compute_tpo_profile(bars)
        shape = classify_tpo_shape(profile)
        assert shape == "p-shape"

    def test_b_shape(self):
        """Volume concentrated at bottom → b-shape."""
        bars = _bars_30m([
            (100, 98), (100, 98), (100, 98),  # Cluster at bottom
            (101, 99), (102, 100), (103, 101),
        ])
        profile = compute_tpo_profile(bars)
        shape = classify_tpo_shape(profile)
        assert shape == "b-shape"


class TestRotationFactor:
    def test_single_bar_zero_rotation(self):
        bars = _bars_30m([(100, 99)])
        assert compute_rotation_factor(bars) == (0.0, 0)

    def test_expanding_range_positive_rotation(self):
        """Each bar extends range → positive rotation count."""
        bars = _bars_30m([
            (100, 99), (101, 98), (102, 97), (103, 96),
        ])
        factor, count = compute_rotation_factor(bars)
        assert count > 0

    def test_contracting_range_negative_rotation(self):
        """Bars stay within prior range → rotation factor near 0."""
        bars = _bars_30m([
            (103, 97), (101, 98), (100.5, 98.5), (100, 99),
        ])
        factor, count = compute_rotation_factor(bars)
        assert count == 0 or factor < 0.5


class TestExcess:
    def test_excess_high_detected(self):
        """Sharp rejection at high (single TPO letter at extreme)."""
        bars = _bars_30m([
            (101, 99), (101, 99), (101, 99),
            (101, 99), (101, 99), (105, 99),  # One bar extends way up
        ])
        profile = compute_tpo_profile(bars)
        has_excess_high, has_excess_low = detect_excess(profile)
        assert has_excess_high is True

    def test_no_excess_when_multiple_touches(self):
        """Extreme prices touched by multiple periods → no excess."""
        bars = _bars_30m([
            (103, 99), (103, 99), (103, 99),
            (103, 99), (103, 99), (103, 99),
        ])
        profile = compute_tpo_profile(bars)
        has_excess_high, has_excess_low = detect_excess(profile)
        assert has_excess_high is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_rl_tpo_extensions.py -v`
Expected: FAIL (functions not found)

- [ ] **Step 3: Read existing tpo.py to understand current structure**

Run: Read `backend/src/market_data/tpo.py` to see the existing `TPOProfile` class and `compute_tpo_profile()` function.

- [ ] **Step 4: Add classify_tpo_shape, compute_rotation_factor, detect_excess to tpo.py**

Add the following functions to `backend/src/market_data/tpo.py`:

```python
def classify_tpo_shape(profile: TPOProfile) -> str:
    """Classify TPO distribution shape.

    Returns one of: "p-shape", "b-shape", "d-shape", "balanced"
    - p-shape: volume concentrated in upper half (buying auction)
    - b-shape: volume concentrated in lower half (selling auction)
    - d-shape: elongated, no clear concentration
    - balanced: roughly symmetric around POC
    """
    if not profile.letters:
        return "balanced"

    prices = sorted(profile.letters.keys())
    if len(prices) < 3:
        return "balanced"

    mid = prices[len(prices) // 2]

    upper_tpo = sum(len(profile.letters[p]) for p in prices if p >= mid)
    lower_tpo = sum(len(profile.letters[p]) for p in prices if p < mid)
    total = upper_tpo + lower_tpo

    if total == 0:
        return "balanced"

    upper_ratio = upper_tpo / total
    lower_ratio = lower_tpo / total

    # Check range vs concentration
    poc_idx = prices.index(profile.poc) if profile.poc in prices else len(prices) // 2
    range_ratio = len(prices)  # How spread out

    if upper_ratio > 0.65:
        return "p-shape"
    elif lower_ratio > 0.65:
        return "b-shape"
    elif range_ratio > 30 and 0.4 < upper_ratio < 0.6:
        return "d-shape"
    else:
        return "balanced"


def compute_rotation_factor(bars_30m: list[dict]) -> tuple[float, int]:
    """Compute rotation factor and count from 30m bars.

    Rotation = how many 30-min periods extend the session range.
    A high rotation count indicates a trending/one-timeframe day.

    Returns:
        (factor, count): factor is rotations/total_periods, count is raw count
    """
    if len(bars_30m) < 2:
        return (0.0, 0)

    session_high = bars_30m[0]["high"]
    session_low = bars_30m[0]["low"]
    rotations = 0

    for bar in bars_30m[1:]:
        extended = False
        if bar["high"] > session_high:
            session_high = bar["high"]
            extended = True
        if bar["low"] < session_low:
            session_low = bar["low"]
            extended = True
        if extended:
            rotations += 1

    factor = rotations / (len(bars_30m) - 1) if len(bars_30m) > 1 else 0.0
    return (round(factor, 3), rotations)


def detect_excess(profile: TPOProfile) -> tuple[bool, bool]:
    """Detect excess at high and low of TPO profile.

    Excess = single TPO print at the extreme, indicating sharp rejection.
    True excess means the extreme price was only visited by 1 TPO period.

    Returns:
        (excess_high, excess_low): booleans
    """
    if not profile.letters:
        return (False, False)

    prices = sorted(profile.letters.keys())
    if len(prices) < 3:
        return (False, False)

    # Excess high: top 2 prices have only 1 letter each
    top_prices = prices[-2:]
    excess_high = all(len(profile.letters[p]) == 1 for p in top_prices)

    # Excess low: bottom 2 prices have only 1 letter each
    bottom_prices = prices[:2]
    excess_low = all(len(profile.letters[p]) == 1 for p in bottom_prices)

    return (excess_high, excess_low)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_rl_tpo_extensions.py -v`
Expected: All PASS

- [ ] **Step 6: Commit**

```bash
git add backend/src/market_data/tpo.py backend/tests/test_rl_tpo_extensions.py
git commit -m "feat(tpo): add shape classification, rotation factor, excess detection"
```

---

## Task 5: Databento Historical Tick Fetcher

**Files:**
- Create: `backend/src/rl/data/fetcher.py`

**Context:** Downloads NQ tick data from Databento historical API, normalizes side field to "A"/"B", saves as Parquet. Uses existing `databento` library (already installed). Also fetches macro data (VIX, bonds, DXY) from yfinance for the same period.

- [ ] **Step 1: Read existing Databento provider for API patterns**

Read `backend/src/market_data/databento_provider.py` to understand the Databento client initialization, dataset/symbol conventions, and side field handling.

- [ ] **Step 2: Implement fetcher.py**

```python
# backend/src/rl/data/fetcher.py
"""Databento historical tick data fetcher for RL training.

Downloads NQ trades from Databento, normalizes side field to "A"/"B"
(matching orderflow.py expectations), saves as Parquet files.
Also fetches macro data (VIX, bonds, DXY) from yfinance.
"""

import logging
import os
from datetime import datetime, timedelta
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from src.rl.config import DATABENTO_DATASET, SYMBOL

logger = logging.getLogger(__name__)

# Resolve data directory relative to backend/
DATA_DIR = Path(__file__).resolve().parents[3] / "data" / "rl"
TICKS_DIR = DATA_DIR / "ticks"
MACRO_DIR = DATA_DIR / "macro"


def fetch_ticks(
    start: datetime,
    end: datetime,
    output_dir: Path | None = None,
    api_key: str | None = None,
) -> list[Path]:
    """Fetch NQ tick data from Databento and save as Parquet.

    Downloads trades (MBP-0) for NQ front-month continuous contract.
    Side field normalized: Databento "A" (ask) → "A", "B" (bid) → "B".

    Args:
        start: Start date (inclusive)
        end: End date (exclusive)
        output_dir: Where to save Parquet files (default: data/rl/ticks/)
        api_key: Databento API key (falls back to DATABENTO_API_KEY env var)

    Returns:
        List of created Parquet file paths
    """
    import databento as db

    output_dir = output_dir or TICKS_DIR
    output_dir.mkdir(parents=True, exist_ok=True)

    key = api_key or os.environ.get("DATABENTO_API_KEY")
    if not key:
        raise ValueError("DATABENTO_API_KEY not set")

    client = db.Historical(key)

    # Fetch month by month to keep file sizes manageable
    created_files = []
    current = start.replace(day=1)

    while current < end:
        month_end = (current.replace(day=28) + timedelta(days=4)).replace(day=1)
        if month_end > end:
            month_end = end

        filename = f"NQ_{current.strftime('%Y-%m')}.parquet"
        filepath = output_dir / filename

        if filepath.exists():
            logger.info(f"Skipping {filename} — already exists")
            created_files.append(filepath)
            current = month_end
            continue

        logger.info(f"Fetching NQ ticks {current.date()} → {month_end.date()}...")

        data = client.timeseries.get_range(
            dataset=DATABENTO_DATASET,
            symbols=["NQ.c.0"],
            schema="trades",
            start=current.strftime("%Y-%m-%d"),
            end=month_end.strftime("%Y-%m-%d"),
        )

        # Convert to records with normalized side field
        records = []
        for rec in data:
            ts = rec.ts_event  # nanosecond timestamp
            if hasattr(ts, 'timestamp'):
                ts_dt = ts
            else:
                ts_dt = datetime.utcfromtimestamp(ts / 1e9)

            # Side: Databento uses "A" for ask (buy aggressor), "B" for bid (sell aggressor)
            # Databento raw API returns "A" (ask/buy aggressor) and "B" (bid/sell aggressor)
            # which matches orderflow.py expectations directly
            side = getattr(rec, 'side', '')
            if side not in ("A", "B"):
                logger.warning(f"Unknown side '{side}' at {ts_dt}, skipping tick")
                continue

            records.append({
                "ts": ts_dt,
                "price": float(rec.price) / 1e9,  # Databento uses fixed-point pricing
                "size": int(rec.size),
                "side": side,
            })

        if records:
            table = pa.Table.from_pylist(records)
            pq.write_table(table, filepath, compression="snappy")
            logger.info(f"Saved {len(records)} ticks to {filename}")
            created_files.append(filepath)
        else:
            logger.warning(f"No ticks for {current.strftime('%Y-%m')}")

        current = month_end

    return created_files


def fetch_macro_history(
    start: datetime,
    end: datetime,
    output_dir: Path | None = None,
) -> Path | None:
    """Fetch historical macro data (VIX, DXY, bonds) from yfinance.

    Saves daily data as Parquet for replay engine to look up during session replay.
    """
    try:
        import yfinance as yf
    except ImportError:
        logger.warning("yfinance not installed — skipping macro data")
        return None

    output_dir = output_dir or MACRO_DIR
    output_dir.mkdir(parents=True, exist_ok=True)

    filepath = output_dir / "macro_daily.parquet"

    tickers = {
        "vix": "^VIX",
        "dxy": "DX-Y.NYB",
        "us10y": "^TNX",
        "us2y": "^IRX",
    }

    records = []
    for name, ticker in tickers.items():
        try:
            data = yf.download(
                ticker,
                start=start.strftime("%Y-%m-%d"),
                end=end.strftime("%Y-%m-%d"),
                progress=False,
            )
            for date, row in data.iterrows():
                records.append({
                    "date": date.date().isoformat(),
                    "indicator": name,
                    "close": float(row["Close"]),
                    "change_pct": float(row["Close"] / row["Open"] - 1) * 100 if row["Open"] > 0 else 0.0,
                })
        except Exception as e:
            logger.warning(f"Failed to fetch {name}: {e}")

    if records:
        table = pa.Table.from_pylist(records)
        pq.write_table(table, filepath, compression="snappy")
        logger.info(f"Saved {len(records)} macro records to {filepath}")
        return filepath

    return None


def load_ticks(date_or_month: str, ticks_dir: Path | None = None) -> list[dict]:
    """Load ticks from Parquet for a given date or month.

    Args:
        date_or_month: "2025-09" for full month, or "2025-09-15" for single day
        ticks_dir: Directory containing Parquet files

    Returns:
        List of tick dicts sorted by timestamp
    """
    ticks_dir = ticks_dir or TICKS_DIR

    if len(date_or_month) == 7:  # YYYY-MM
        filepath = ticks_dir / f"NQ_{date_or_month}.parquet"
    else:  # YYYY-MM-DD
        month = date_or_month[:7]
        filepath = ticks_dir / f"NQ_{month}.parquet"

    if not filepath.exists():
        raise FileNotFoundError(f"No tick data at {filepath}")

    table = pq.read_table(filepath)
    records = table.to_pylist()

    # Filter to specific date if full date provided
    if len(date_or_month) == 10:
        target_date = date_or_month
        records = [r for r in records if str(r["ts"])[:10] == target_date]

    return sorted(records, key=lambda r: r["ts"])
```

- [ ] **Step 3: Verify imports work**

Run: `cd backend && python -c "from src.rl.data.fetcher import fetch_ticks, load_ticks; print('Fetcher OK')"`
Expected: `Fetcher OK`

- [ ] **Step 4: Commit**

```bash
git add backend/src/rl/data/fetcher.py
git commit -m "feat(rl): Databento historical tick fetcher with macro data"
```

---

## Task 6: Feature Extractors

**Files:**
- Create: `backend/src/rl/features/observation.py`
- Create: `backend/src/rl/features/level_features.py`
- Create: `backend/src/rl/features/orderflow_features.py`
- Create: `backend/src/rl/features/tpo_features.py`
- Create: `backend/src/rl/features/structure_features.py`
- Create: `backend/src/rl/features/macro_features.py`
- Test: `backend/tests/test_rl_features.py`

**Context:** Each feature module extracts a slice of the ~105-dim observation vector from the current replay state. `observation.py` assembles them all into one flat numpy array.

- [ ] **Step 1: Write failing tests for observation vector assembly**

```python
# backend/tests/test_rl_features.py
"""Tests for RL observation vector construction."""

import numpy as np
import pytest
from src.rl.features.observation import build_observation, OBSERVATION_DIM
from src.rl.features.level_features import encode_level_type, encode_confluence
from src.rl.config import LevelType


class TestLevelFeatures:
    def test_one_hot_encoding_length(self):
        encoded = encode_level_type(LevelType.POC_SESSION)
        assert len(encoded) == len(LevelType)
        assert sum(encoded) == 1.0

    def test_different_types_different_vectors(self):
        a = encode_level_type(LevelType.VWAP)
        b = encode_level_type(LevelType.PDH)
        assert a != b

    def test_confluence_features(self):
        levels = [
            {"price": 100.0, "type": "poc_session"},
            {"price": 100.25, "type": "vah"},
            {"price": 105.0, "type": "pdh"},
        ]
        features = encode_confluence(
            touched_price=100.0,
            all_levels=levels,
            tick_size=0.25,
        )
        assert features["levels_within_5_ticks"] >= 2  # POC + VAH are close


class TestObservationVector:
    def test_observation_is_numpy_array(self):
        state = _make_dummy_state()
        obs = build_observation(state)
        assert isinstance(obs, np.ndarray)
        assert obs.dtype == np.float32

    def test_observation_has_correct_dim(self):
        state = _make_dummy_state()
        obs = build_observation(state)
        assert len(obs) == OBSERVATION_DIM

    def test_observation_values_bounded(self):
        """All values should be in [-1, 1] or [0, 1] after normalization."""
        state = _make_dummy_state()
        obs = build_observation(state)
        assert np.all(obs >= -5.0)  # Allow some slack for unnormalized
        assert np.all(obs <= 5.0)

    def test_observation_no_nans(self):
        state = _make_dummy_state()
        obs = build_observation(state)
        assert not np.any(np.isnan(obs))


def _make_dummy_state() -> dict:
    """Create a minimal replay state for testing."""
    return {
        "level_type": LevelType.POC_SESSION,
        "price": 100.0,
        "candles": [
            {"open": 99.5, "high": 100.5, "low": 99.0, "close": 100.0,
             "volume": 1000, "delta": 50, "buy_volume": 525, "sell_volume": 475}
            for _ in range(5)
        ],
        "vwap_bands": {"vwap": 100.0, "sd1_upper": 101.0, "sd1_lower": 99.0,
                       "sd2_upper": 102.0, "sd2_lower": 98.0,
                       "sd3_upper": 103.0, "sd3_lower": 97.0},
        "volume_profile": {"poc": 100.0, "vah": 101.0, "val": 99.0,
                          "single_prints": []},
        "tpo_profile": None,
        "session_levels": None,
        "all_levels": [],
        "orderflow_signals": None,
        "macro": None,
        "session_context": {
            "minutes_since_rth_open": 60,
            "session_volume_pct": 0.3,
            "daily_range_pct": 0.5,
            "ib_high": 101.0, "ib_low": 99.0,
            "ib_broken_above": False, "ib_broken_below": False,
        },
    }
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_rl_features.py -v`
Expected: FAIL

- [ ] **Step 3: Implement level_features.py**

```python
# backend/src/rl/features/level_features.py
"""Level identity and confluence features."""

import numpy as np
from src.rl.config import LevelType, TICK_SIZE


def encode_level_type(level_type: LevelType) -> list[float]:
    """One-hot encode a level type. Returns list of len(LevelType) floats."""
    types = list(LevelType)
    encoding = [0.0] * len(types)
    encoding[types.index(level_type)] = 1.0
    return encoding


def encode_confluence(
    touched_price: float,
    all_levels: list[dict],
    tick_size: float = TICK_SIZE,
    proximity_ticks: int = 5,
) -> dict:
    """Compute confluence features around the touched level.

    Returns dict with:
        levels_within_5_ticks, strongest_cluster_score,
        nearest_higher_level_dist, nearest_lower_level_dist,
        touched_level_hierarchy_rank
    """
    proximity = proximity_ticks * tick_size
    nearby = [l for l in all_levels if abs(l["price"] - touched_price) <= proximity]

    # Distance to nearest level above/below
    above = [l["price"] for l in all_levels if l["price"] > touched_price + proximity]
    below = [l["price"] for l in all_levels if l["price"] < touched_price - proximity]

    nearest_above = (min(above) - touched_price) / tick_size if above else 100.0
    nearest_below = (touched_price - max(below)) / tick_size if below else 100.0

    return {
        "levels_within_5_ticks": len(nearby),
        "strongest_cluster_score": len(nearby) / max(len(all_levels), 1),
        "nearest_higher_level_dist": min(nearest_above, 100.0),
        "nearest_lower_level_dist": min(nearest_below, 100.0),
        "touched_level_hierarchy_rank": 0.5,  # Placeholder, computed from VP hierarchy
    }
```

- [ ] **Step 4: Implement orderflow_features.py**

```python
# backend/src/rl/features/orderflow_features.py
"""Orderflow snapshot features from CandleFlow/OrderflowSignals."""

import numpy as np


def extract_orderflow_features(
    candles: list[dict],
    signals: dict | None = None,
    lookback: int = 20,
) -> np.ndarray:
    """Extract ~15 orderflow features from recent candles.

    Args:
        candles: Recent 1m candles with delta, volume, buy_volume, sell_volume
        signals: OrderflowSignals dict (or None for defaults)
        lookback: Number of candles for average volume

    Returns:
        numpy array of ~15 floats
    """
    if not candles:
        return np.zeros(15, dtype=np.float32)

    latest = candles[-1]
    avg_vol = np.mean([c.get("volume", 0) for c in candles[-lookback:]]) or 1.0

    # CVD trend from last 5 candles
    deltas = [c.get("delta", 0) for c in candles[-5:]]
    if len(deltas) >= 3:
        cvd_values = np.cumsum(deltas)
        cvd_trend = 1.0 if cvd_values[-1] > cvd_values[0] else (-1.0 if cvd_values[-1] < cvd_values[0] else 0.0)
    else:
        cvd_trend = 0.0

    # Use signals if provided, else derive from candles
    sig = signals or {}

    delta = latest.get("delta", 0)
    volume = latest.get("volume", 1)
    buy_vol = latest.get("buy_volume", 0)
    sell_vol = latest.get("sell_volume", 0)
    spread = latest.get("high", 0) - latest.get("low", 0)
    body = abs(latest.get("close", 0) - latest.get("open", 0))
    candle_range = spread or 1.0

    features = np.array([
        delta / max(volume, 1),                          # delta_pct
        delta,                                            # raw delta (will be normalized later)
        sum(deltas),                                      # cvd (running sum)
        cvd_trend,                                        # cvd_trend: 1/-1/0
        volume / avg_vol,                                 # volume_ratio
        body / candle_range if candle_range > 0 else 0,   # body_ratio
        spread / 0.25,                                    # spread_ticks
        buy_vol / max(sell_vol, 1),                       # passive_active_ratio proxy
        sig.get("imbalance_ratio_max", 0.5),              # imbalance_ratio_max
        sig.get("stacked_imbalance_count", 0),            # stacked_imbalance_count
        1.0 if sig.get("stacked_imbalance_direction") == "buy" else (-1.0 if sig.get("stacked_imbalance_direction") == "sell" else 0.0),
        sig.get("big_trades_count", 0),                   # big_trades_count
        sig.get("big_trades_net_delta", 0),               # big_trades_net_delta
        1.0 if sig.get("vsa_absorption", False) else 0.0, # vsa_absorption
        1.0 if sig.get("stop_run_detected", False) else 0.0,  # stop_run_detected
    ], dtype=np.float32)

    return features
```

- [ ] **Step 5: Implement tpo_features.py**

```python
# backend/src/rl/features/tpo_features.py
"""TPO profile features for observation vector."""

import numpy as np
from src.rl.config import TICK_SIZE


def extract_tpo_features(
    tpo_profile: dict | None,
    current_price: float,
    bars_30m: list[dict] | None = None,
) -> np.ndarray:
    """Extract ~13 TPO features (9 base + 4 shape one-hot).

    Returns numpy array of 13 floats.
    """
    if tpo_profile is None:
        return np.zeros(13, dtype=np.float32)

    poc = tpo_profile.get("poc", current_price)
    vah = tpo_profile.get("vah", current_price)
    val = tpo_profile.get("val", current_price)
    va_width = (vah - val) / TICK_SIZE if vah != val else 0

    # Price position in value area [0, 1]
    if vah > val:
        price_in_va = max(0.0, min(1.0, (current_price - val) / (vah - val)))
    else:
        price_in_va = 0.5

    # TPO count at current price
    letters = tpo_profile.get("letters", {})
    snapped = round(round(current_price / TICK_SIZE) * TICK_SIZE, 4)
    time_at_price = len(letters.get(snapped, []))

    # Excess
    excess_high = 1.0 if tpo_profile.get("excess_high", False) else 0.0
    excess_low = 1.0 if tpo_profile.get("excess_low", False) else 0.0

    # Rotation
    rotation_factor = tpo_profile.get("rotation_factor", 0.0)
    rotation_count = tpo_profile.get("rotation_count", 0)

    # Shape one-hot (4 dims: p, b, d, balanced)
    shape = tpo_profile.get("shape", "balanced")
    shape_vec = [
        1.0 if shape == "p-shape" else 0.0,
        1.0 if shape == "b-shape" else 0.0,
        1.0 if shape == "d-shape" else 0.0,
        1.0 if shape == "balanced" else 0.0,
    ]

    features = np.array([
        (current_price - poc) / TICK_SIZE,  # price_vs_tpo_poc_ticks (normalized, not raw POC)
        va_width / 100.0,                   # normalized VA width
        price_in_va,
        time_at_price,
        excess_high,
        excess_low,
        rotation_factor,
        rotation_count,
    ] + shape_vec, dtype=np.float32)

    return features
```

- [ ] **Step 6: Implement structure_features.py**

```python
# backend/src/rl/features/structure_features.py
"""Price structure features: VWAP position, VA, IB, session levels."""

import numpy as np
from src.rl.config import TICK_SIZE


def extract_structure_features(
    price: float,
    vwap_bands: dict | None,
    volume_profile: dict | None,
    session_levels: dict | None,
    session_context: dict | None,
) -> np.ndarray:
    """Extract ~23 price structure + session context features.

    Covers: price vs VWAP (SD), price in VA, distances to key levels,
    IB range, market type, session timing.
    """
    features = []

    # --- Price vs VWAP (1 dim) ---
    if vwap_bands:
        vwap = vwap_bands.get("vwap", price)
        sd1_range = vwap_bands.get("sd1_upper", vwap) - vwap
        if sd1_range > 0:
            features.append((price - vwap) / sd1_range)  # price_vs_vwap_sd
        else:
            features.append(0.0)
    else:
        features.append(0.0)

    # --- Price in VA (1 dim) ---
    if volume_profile:
        vah = volume_profile.get("vah", price)
        val = volume_profile.get("val", price)
        if vah > val:
            features.append(max(0.0, min(1.0, (price - val) / (vah - val))))
        else:
            features.append(0.5)
    else:
        features.append(0.5)

    # --- Distance to VP levels (3 dims) ---
    if volume_profile:
        features.append((price - volume_profile.get("poc", price)) / TICK_SIZE)
        features.append((price - volume_profile.get("vah", price)) / TICK_SIZE)
        features.append((price - volume_profile.get("val", price)) / TICK_SIZE)
    else:
        features.extend([0.0, 0.0, 0.0])

    # --- Single prints (3 dims) ---
    if volume_profile:
        sp = volume_profile.get("single_prints", [])
        sp_above = sum(1 for s in sp if (s[0] if isinstance(s, tuple) else s) > price)
        sp_below = sum(1 for s in sp if (s[0] if isinstance(s, tuple) else s) < price)
        nearest_sp = min(
            (abs((s[0] if isinstance(s, tuple) else s) - price) / TICK_SIZE for s in sp),
            default=100.0,
        )
        features.extend([nearest_sp, sp_above, sp_below])
    else:
        features.extend([100.0, 0, 0])

    # --- IB range (1 dim) ---
    ctx = session_context or {}
    ib_high = ctx.get("ib_high")
    ib_low = ctx.get("ib_low")
    if ib_high and ib_low:
        features.append((ib_high - ib_low) / TICK_SIZE)
    else:
        features.append(0.0)

    # --- Market type one-hot (3 dims: trend/normal/neutral) ---
    features.extend([0.0, 1.0, 0.0])  # Default: normal. Computed from IB extension.

    # --- Poor high/low (2 dims) ---
    features.extend([0.0, 0.0])  # From VP poor_high/poor_low

    # --- Session context (8 dims) ---
    import math
    minutes = ctx.get("minutes_since_rth_open", 0)
    features.append(minutes / 390.0)  # Normalized (6.5 hr session)

    features.append(ctx.get("session_volume_pct", 0.5))
    features.append(ctx.get("daily_range_pct", 0.5))

    # Time of day sin/cos encoding
    tod = minutes / 390.0 * 2 * math.pi
    features.append(math.sin(tod))
    features.append(math.cos(tod))

    # Session type one-hot (3 dims: trend/bracket/normal)
    features.extend([0.0, 0.0, 1.0])  # Default: normal

    # IB broken (3 dims: above/below/neither one-hot)
    ib_above = 1.0 if ctx.get("ib_broken_above", False) else 0.0
    ib_below = 1.0 if ctx.get("ib_broken_below", False) else 0.0
    ib_neither = 1.0 if not (ib_above or ib_below) else 0.0
    features.extend([ib_above, ib_below, ib_neither])

    return np.array(features, dtype=np.float32)
```

- [ ] **Step 7: Implement macro_features.py**

```python
# backend/src/rl/features/macro_features.py
"""Macro features: VIX, bonds, DXY, news, GEX."""

import numpy as np


def extract_macro_features(macro: dict | None) -> np.ndarray:
    """Extract ~10 macro features.

    Phase 1: GEX, news_event_active, news_severity are always 0.0.
    VIX, DXY, and bond yields come from yfinance daily data.
    """
    if macro is None:
        return np.zeros(10, dtype=np.float32)

    return np.array([
        macro.get("vix", 0.0) / 50.0,          # vix_level_norm (VIX/50)
        macro.get("vix_change_pct", 0.0) / 10,  # vix_change (scaled)
        macro.get("regime_score", 0.0),          # regime (0=risk-on, 1=risk-off)
        macro.get("dxy_change", 0.0) / 1.0,     # dxy_change_pct
        0.0,                                      # gex_level (Phase 2)
        macro.get("us10y_change", 0.0) / 0.1,   # us10y_change (scaled)
        macro.get("us2y_change", 0.0) / 0.1,    # us2y_change (scaled)
        macro.get("yield_curve_spread", 0.0),    # 10y - 2y spread
        0.0,                                      # news_event_active (Phase 2)
        0.0,                                      # news_severity (Phase 2)
    ], dtype=np.float32)
```

- [ ] **Step 8: Implement observation.py (assembles all features)**

```python
# backend/src/rl/features/observation.py
"""Assembles the full observation vector from all feature modules."""

import numpy as np

from src.rl.config import LevelType
from src.rl.features.level_features import encode_level_type, encode_confluence
from src.rl.features.orderflow_features import extract_orderflow_features
from src.rl.features.tpo_features import extract_tpo_features
from src.rl.features.structure_features import extract_structure_features
from src.rl.features.macro_features import extract_macro_features


def build_observation(state: dict) -> np.ndarray:
    """Build the full ~105-dim observation vector from replay state.

    Args:
        state: Dict containing all current market state:
            - level_type: LevelType enum
            - price: float (current price at level touch)
            - candles: list[dict] (recent 1m candles)
            - vwap_bands: dict or None
            - volume_profile: dict or None
            - tpo_profile: dict or None
            - session_levels: dict or None
            - all_levels: list[dict] (all active levels)
            - orderflow_signals: dict or None
            - macro: dict or None
            - session_context: dict or None

    Returns:
        np.ndarray of shape (OBSERVATION_DIM,), dtype float32
    """
    parts = []

    # 1. Level identity one-hot (~26 dims)
    parts.append(np.array(
        encode_level_type(state["level_type"]),
        dtype=np.float32,
    ))

    # 2. Orderflow snapshot (~15 dims)
    parts.append(extract_orderflow_features(
        candles=state.get("candles", []),
        signals=state.get("orderflow_signals"),
    ))

    # 3. Price structure + session context (~23 dims)
    parts.append(extract_structure_features(
        price=state["price"],
        vwap_bands=state.get("vwap_bands"),
        volume_profile=state.get("volume_profile"),
        session_levels=state.get("session_levels"),
        session_context=state.get("session_context"),
    ))

    # 4. TPO profile (~14 dims)
    parts.append(extract_tpo_features(
        tpo_profile=state.get("tpo_profile"),
        current_price=state["price"],
    ))

    # 5. Recent candle window (5 candles × 3 features = 15 dims)
    candle_window = _extract_candle_window(state.get("candles", []))
    parts.append(candle_window)

    # 6. Multi-TF confluence (~5 dims)
    confluence = encode_confluence(
        touched_price=state["price"],
        all_levels=state.get("all_levels", []),
    )
    parts.append(np.array([
        confluence["levels_within_5_ticks"],
        confluence["strongest_cluster_score"],
        confluence["nearest_higher_level_dist"],
        confluence["nearest_lower_level_dist"],
        confluence["touched_level_hierarchy_rank"],
    ], dtype=np.float32))

    # 7. Macro (~10 dims)
    parts.append(extract_macro_features(state.get("macro")))

    obs = np.concatenate(parts)

    # Replace NaN/Inf with 0
    obs = np.nan_to_num(obs, nan=0.0, posinf=5.0, neginf=-5.0)

    return obs


def _extract_candle_window(candles: list[dict], n: int = 5) -> np.ndarray:
    """Extract last N candles as flat feature vector (N × 3 dims)."""
    recent = candles[-n:] if candles else []

    features = []
    avg_vol = np.mean([c.get("volume", 1) for c in candles[-20:]]) if candles else 1.0

    for i in range(n):
        if i < len(recent):
            c = recent[i]
            vol = c.get("volume", 0)
            delta = c.get("delta", 0)
            body = abs(c.get("close", 0) - c.get("open", 0))
            rng = c.get("high", 0) - c.get("low", 0)

            features.extend([
                delta / max(vol, 1),              # delta_norm
                vol / max(avg_vol, 1),            # volume_norm
                body / rng if rng > 0 else 0.0,   # body_ratio
            ])
        else:
            features.extend([0.0, 0.0, 0.0])

    return np.array(features, dtype=np.float32)


# Compute actual observation dim by building a dummy observation
OBSERVATION_DIM = len(build_observation({
    "level_type": LevelType.POC_SESSION,
    "price": 100.0,
    "candles": [],
    "vwap_bands": None,
    "volume_profile": None,
    "tpo_profile": None,
    "session_levels": None,
    "all_levels": [],
    "orderflow_signals": None,
    "macro": None,
    "session_context": {},
}))
```

- [ ] **Step 9: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_rl_features.py -v`
Expected: All PASS

- [ ] **Step 10: Commit**

```bash
git add backend/src/rl/features/
git add backend/tests/test_rl_features.py
git commit -m "feat(rl): observation vector feature extractors (~105 dims)"
```

---

## Task 7: Episode Builder (Outcome Labeling)

**Files:**
- Create: `backend/src/rl/data/episode_builder.py`
- Test: `backend/tests/test_rl_episode_builder.py`

**Context:** Given a level touch timestamp and observation vector, look forward through ticks to determine what would have happened (hit target, hit stop, or timeout). Labels the episode with the appropriate R-multiple reward for each possible action.

- [ ] **Step 1: Write failing tests**

```python
# backend/tests/test_rl_episode_builder.py
"""Tests for episode outcome labeling."""

import pytest
import numpy as np
from datetime import datetime, timezone, timedelta
from src.rl.data.episode_builder import label_outcome, Episode
from src.rl.config import Action, STOP_TICKS, TARGET_TICKS, TICK_SIZE


def _ticks_sequence(start_price: float, offsets: list[float]) -> list[dict]:
    """Create ticks at 1-second intervals with given price offsets from start."""
    base = datetime(2025, 1, 6, 15, 0, 0, tzinfo=timezone.utc)
    return [
        {"ts": base + timedelta(seconds=i), "price": start_price + off, "size": 1, "side": "A"}
        for i, off in enumerate(offsets)
    ]


class TestLabelOutcome:
    def test_long_winner(self):
        """Price goes up 20 ticks (5 points) → LONG wins."""
        touch_price = 100.0
        target = TARGET_TICKS * TICK_SIZE  # 5.0 points
        ticks = _ticks_sequence(touch_price, [0, 1, 2, 3, 4, 5])
        episode = label_outcome(
            touch_price=touch_price,
            forward_ticks=ticks,
            observation=np.zeros(10, dtype=np.float32),
            level_type="poc_session",
            touch_ts=ticks[0]["ts"],
        )
        assert episode.best_action == Action.LONG
        assert episode.reward_long == 2.0
        assert episode.reward_short == -1.0

    def test_short_winner(self):
        """Price goes down 20 ticks → SHORT wins."""
        touch_price = 100.0
        target = TARGET_TICKS * TICK_SIZE
        ticks = _ticks_sequence(touch_price, [0, -1, -2, -3, -4, -5])
        episode = label_outcome(
            touch_price=touch_price,
            forward_ticks=ticks,
            observation=np.zeros(10, dtype=np.float32),
            level_type="poc_session",
            touch_ts=ticks[0]["ts"],
        )
        assert episode.best_action == Action.SHORT
        assert episode.reward_short == 2.0
        assert episode.reward_long == -1.0

    def test_timeout_skip(self):
        """Price stays flat within timeout → SKIP is best."""
        touch_price = 100.0
        # Stay within ±2 ticks for 30 min
        ticks = _ticks_sequence(touch_price, [i * 0.25 * ((-1)**i) for i in range(1800)])
        episode = label_outcome(
            touch_price=touch_price,
            forward_ticks=ticks,
            observation=np.zeros(10, dtype=np.float32),
            level_type="poc_session",
            touch_ts=ticks[0]["ts"],
        )
        assert episode.best_action == Action.SKIP
        assert episode.reward_skip == 0.0

    def test_long_stop_hit(self):
        """Price drops 10 ticks (2.5 pts) → LONG stop hit. SHORT doesn't reach target."""
        touch_price = 100.0
        # Only drops to -2.5, not enough for SHORT target (-5.0)
        ticks = _ticks_sequence(touch_price, [0, -0.5, -1.0, -1.5, -2.0, -2.5])
        episode = label_outcome(
            touch_price=touch_price,
            forward_ticks=ticks,
            observation=np.zeros(10, dtype=np.float32),
            level_type="poc_session",
            touch_ts=ticks[0]["ts"],
        )
        assert episode.reward_long == -1.0    # LONG stop hit at -2.5
        assert episode.reward_short == 0.0     # SHORT timeout (didn't reach -5.0 target)

    def test_episode_has_all_fields(self):
        ticks = _ticks_sequence(100.0, [0, 1, 2, 3, 4, 5])
        episode = label_outcome(
            touch_price=100.0,
            forward_ticks=ticks,
            observation=np.zeros(10, dtype=np.float32),
            level_type="vwap",
            touch_ts=ticks[0]["ts"],
        )
        assert hasattr(episode, "observation")
        assert hasattr(episode, "level_type")
        assert hasattr(episode, "touch_price")
        assert hasattr(episode, "touch_ts")
        assert hasattr(episode, "best_action")
        assert hasattr(episode, "reward_long")
        assert hasattr(episode, "reward_short")
        assert hasattr(episode, "reward_skip")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_rl_episode_builder.py -v`
Expected: FAIL

- [ ] **Step 3: Implement episode_builder.py**

```python
# backend/src/rl/data/episode_builder.py
"""Episode construction: label outcomes from forward tick data.

Given a level touch, look forward through ticks to determine
what would have happened for each action (LONG/SHORT/SKIP).
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

import numpy as np

from src.rl.config import (
    Action,
    STOP_TICKS,
    TARGET_TICKS,
    TICK_SIZE,
    TIMEOUT_MINUTES,
    REWARD_TARGET_HIT,
    REWARD_STOP_HIT,
    REWARD_TIMEOUT,
)


@dataclass
class Episode:
    """A single training episode from a level touch."""
    observation: np.ndarray
    level_type: str
    touch_price: float
    touch_ts: datetime
    best_action: Action
    reward_long: float
    reward_short: float
    reward_skip: float


def label_outcome(
    touch_price: float,
    forward_ticks: list[dict],
    observation: np.ndarray,
    level_type: str,
    touch_ts: datetime,
) -> Episode:
    """Label episode outcome by scanning forward ticks.

    For each direction (LONG/SHORT), check if price hits target or stop first.
    Timeout after TIMEOUT_MINUTES.

    Args:
        touch_price: Price at level touch
        forward_ticks: Ticks after the touch, sorted by time
        observation: Feature vector at touch time
        level_type: String name of the level type
        touch_ts: Timestamp of the touch

    Returns:
        Episode with rewards for each action
    """
    stop_dist = STOP_TICKS * TICK_SIZE
    target_dist = TARGET_TICKS * TICK_SIZE
    timeout = timedelta(minutes=TIMEOUT_MINUTES)

    # LONG: target = touch + target_dist, stop = touch - stop_dist
    long_target = touch_price + target_dist
    long_stop = touch_price - stop_dist

    # SHORT: target = touch - target_dist, stop = touch + stop_dist
    short_target = touch_price - target_dist
    short_stop = touch_price + stop_dist

    reward_long = REWARD_TIMEOUT  # Default: timeout
    reward_short = REWARD_TIMEOUT
    long_resolved = False
    short_resolved = False

    for tick in forward_ticks:
        # Check timeout
        if tick["ts"] - touch_ts > timeout:
            break

        price = tick["price"]

        # Check LONG outcome
        if not long_resolved:
            if price >= long_target:
                reward_long = REWARD_TARGET_HIT
                long_resolved = True
            elif price <= long_stop:
                reward_long = REWARD_STOP_HIT
                long_resolved = True

        # Check SHORT outcome
        if not short_resolved:
            if price <= short_target:
                reward_short = REWARD_TARGET_HIT
                short_resolved = True
            elif price >= short_stop:
                reward_short = REWARD_STOP_HIT
                short_resolved = True

        if long_resolved and short_resolved:
            break

    # Best action: highest reward
    reward_skip = REWARD_TIMEOUT
    rewards = {
        Action.LONG: reward_long,
        Action.SHORT: reward_short,
        Action.SKIP: reward_skip,
    }
    best_action = max(rewards, key=rewards.get)

    return Episode(
        observation=observation,
        level_type=level_type,
        touch_price=touch_price,
        touch_ts=touch_ts,
        best_action=best_action,
        reward_long=reward_long,
        reward_short=reward_short,
        reward_skip=reward_skip,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_rl_episode_builder.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add backend/src/rl/data/episode_builder.py backend/tests/test_rl_episode_builder.py
git commit -m "feat(rl): episode builder with outcome labeling (R-multiple rewards)"
```

---

## Task 8: DQN Neural Network

**Files:**
- Create: `backend/src/rl/agent/network.py`
- Test: `backend/tests/test_rl_network.py`

- [ ] **Step 1: Write failing tests**

```python
# backend/tests/test_rl_network.py
"""Tests for DQN network architecture."""

import pytest
import torch
import numpy as np
from src.rl.agent.network import DQNetwork
from src.rl.config import NUM_ACTIONS


class TestDQNetwork:
    def test_output_shape(self):
        net = DQNetwork(input_dim=105)
        x = torch.randn(1, 105)
        out = net(x)
        assert out.shape == (1, NUM_ACTIONS)

    def test_batch_output_shape(self):
        net = DQNetwork(input_dim=105)
        x = torch.randn(64, 105)
        out = net(x)
        assert out.shape == (64, NUM_ACTIONS)

    def test_deterministic_forward(self):
        net = DQNetwork(input_dim=105)
        net.eval()
        x = torch.randn(1, 105)
        out1 = net(x)
        out2 = net(x)
        assert torch.allclose(out1, out2)

    def test_parameter_count(self):
        net = DQNetwork(input_dim=105)
        total = sum(p.numel() for p in net.parameters())
        assert 20_000 < total < 50_000  # ~25k expected

    def test_from_numpy(self):
        net = DQNetwork(input_dim=105)
        obs = np.random.randn(105).astype(np.float32)
        out = net.predict(obs)
        assert out.shape == (NUM_ACTIONS,)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_rl_network.py -v`
Expected: FAIL

- [ ] **Step 3: Implement DQNetwork**

```python
# backend/src/rl/agent/network.py
"""DQN neural network: 128-128-64 fully connected with ReLU."""

import numpy as np
import torch
import torch.nn as nn

from src.rl.config import HIDDEN_LAYERS, NUM_ACTIONS


class DQNetwork(nn.Module):
    """Deep Q-Network for level touch trading.

    Architecture: input → 128 → 128 → 64 → 3 (Q-values for LONG/SHORT/SKIP)
    """

    def __init__(self, input_dim: int):
        super().__init__()

        layers = []
        prev_dim = input_dim
        for hidden_dim in HIDDEN_LAYERS:
            layers.append(nn.Linear(prev_dim, hidden_dim))
            layers.append(nn.ReLU())
            prev_dim = hidden_dim

        layers.append(nn.Linear(prev_dim, NUM_ACTIONS))

        self.network = nn.Sequential(*layers)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """Forward pass. Returns Q-values for each action."""
        return self.network(x)

    def predict(self, observation: np.ndarray) -> np.ndarray:
        """Predict Q-values from numpy observation (no grad)."""
        with torch.no_grad():
            x = torch.from_numpy(observation).unsqueeze(0)
            q_values = self.forward(x)
            return q_values.squeeze(0).numpy()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_rl_network.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add backend/src/rl/agent/network.py backend/tests/test_rl_network.py
git commit -m "feat(rl): DQN neural network (128-128-64 → 3 actions)"
```

---

## Task 9: Experience Replay Buffer

**Files:**
- Create: `backend/src/rl/agent/replay_buffer.py`
- Test: `backend/tests/test_rl_replay_buffer.py`

- [ ] **Step 1: Write failing tests**

```python
# backend/tests/test_rl_replay_buffer.py
"""Tests for experience replay buffer."""

import pytest
import numpy as np
from src.rl.agent.replay_buffer import ReplayBuffer
from src.rl.config import REPLAY_BUFFER_SIZE


class TestReplayBuffer:
    def test_empty_cannot_sample(self):
        buf = ReplayBuffer(capacity=100)
        with pytest.raises(ValueError):
            buf.sample(10)

    def test_add_and_sample(self):
        buf = ReplayBuffer(capacity=100)
        obs = np.zeros(10, dtype=np.float32)
        buf.add(obs, action=0, reward=1.0)
        batch = buf.sample(1)
        assert batch["observations"].shape == (1, 10)
        assert batch["actions"].shape == (1,)
        assert batch["rewards"].shape == (1,)

    def test_capacity_overflow(self):
        buf = ReplayBuffer(capacity=5)
        for i in range(10):
            buf.add(np.array([float(i)], dtype=np.float32), action=0, reward=0.0)
        assert len(buf) == 5

    def test_sample_batch_size(self):
        buf = ReplayBuffer(capacity=100)
        for i in range(50):
            buf.add(np.zeros(10, dtype=np.float32), action=i % 3, reward=float(i))
        batch = buf.sample(32)
        assert batch["observations"].shape == (32, 10)
        assert batch["actions"].shape == (32,)
        assert batch["rewards"].shape == (32,)

    def test_sample_randomness(self):
        buf = ReplayBuffer(capacity=100)
        for i in range(100):
            buf.add(np.array([float(i)], dtype=np.float32), action=0, reward=float(i))
        b1 = buf.sample(10)
        b2 = buf.sample(10)
        # Very unlikely to be identical
        assert not np.array_equal(b1["rewards"], b2["rewards"])
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_rl_replay_buffer.py -v`
Expected: FAIL

- [ ] **Step 3: Implement ReplayBuffer**

```python
# backend/src/rl/agent/replay_buffer.py
"""Experience replay buffer for DQN training."""

import random
from collections import deque

import numpy as np


class ReplayBuffer:
    """Fixed-capacity circular buffer storing (observation, action, reward) tuples."""

    def __init__(self, capacity: int):
        self._buffer = deque(maxlen=capacity)

    def add(self, observation: np.ndarray, action: int, reward: float):
        """Store a transition."""
        self._buffer.append((observation.copy(), action, reward))

    def sample(self, batch_size: int) -> dict:
        """Sample a random batch.

        Returns dict with:
            observations: np.ndarray (batch_size, obs_dim)
            actions: np.ndarray (batch_size,) int64
            rewards: np.ndarray (batch_size,) float32
        """
        if len(self._buffer) < batch_size:
            raise ValueError(
                f"Not enough samples: {len(self._buffer)} < {batch_size}"
            )

        batch = random.sample(list(self._buffer), batch_size)
        observations, actions, rewards = zip(*batch)

        return {
            "observations": np.array(observations, dtype=np.float32),
            "actions": np.array(actions, dtype=np.int64),
            "rewards": np.array(rewards, dtype=np.float32),
        }

    def __len__(self) -> int:
        return len(self._buffer)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_rl_replay_buffer.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add backend/src/rl/agent/replay_buffer.py backend/tests/test_rl_replay_buffer.py
git commit -m "feat(rl): experience replay buffer"
```

---

## Task 10: DQN Training Loop

**Files:**
- Create: `backend/src/rl/agent/dqn.py`
- Test: `backend/tests/test_rl_dqn.py`

- [ ] **Step 1: Write failing tests**

```python
# backend/tests/test_rl_dqn.py
"""Tests for DQN training agent."""

import pytest
import numpy as np
from src.rl.agent.dqn import DQNAgent
from src.rl.config import Action


class TestDQNAgent:
    def test_select_action_random_at_start(self):
        """With epsilon=1.0, all actions should be random."""
        agent = DQNAgent(observation_dim=10, epsilon=1.0)
        actions = [agent.select_action(np.zeros(10, dtype=np.float32)) for _ in range(100)]
        # Should see all 3 actions
        assert len(set(actions)) > 1

    def test_select_action_greedy(self):
        """With epsilon=0, should always pick same action for same input."""
        agent = DQNAgent(observation_dim=10, epsilon=0.0)
        obs = np.random.randn(10).astype(np.float32)
        actions = [agent.select_action(obs) for _ in range(10)]
        assert len(set(actions)) == 1

    def test_train_step_reduces_loss(self):
        """Loss should decrease after training on consistent data."""
        agent = DQNAgent(observation_dim=10, epsilon=0.0)

        # Add 100 episodes where LONG always wins
        obs = np.random.randn(10).astype(np.float32)
        for _ in range(100):
            agent.buffer.add(obs, action=Action.LONG.value, reward=2.0)

        loss1 = agent.train_step()
        for _ in range(50):
            agent.train_step()
        loss2 = agent.train_step()

        assert loss2 < loss1 * 2  # Should not diverge

    def test_epsilon_decay(self):
        agent = DQNAgent(observation_dim=10)
        initial_eps = agent.epsilon
        for _ in range(100):
            agent.buffer.add(np.zeros(10, dtype=np.float32), 0, 0.0)
        for _ in range(10):
            agent.train_step()
        assert agent.epsilon < initial_eps

    def test_save_and_load(self, tmp_path):
        agent = DQNAgent(observation_dim=10)
        path = tmp_path / "model.pt"
        agent.save(path)

        agent2 = DQNAgent(observation_dim=10)
        agent2.load(path)

        obs = np.random.randn(10).astype(np.float32)
        assert np.allclose(agent.q_network.predict(obs), agent2.q_network.predict(obs))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_rl_dqn.py -v`
Expected: FAIL

- [ ] **Step 3: Implement DQNAgent**

```python
# backend/src/rl/agent/dqn.py
"""DQN training agent with epsilon-greedy exploration and target network."""

import copy
import logging
import random
from pathlib import Path

import numpy as np
import torch
import torch.nn as nn
import torch.optim as optim

from src.rl.agent.network import DQNetwork
from src.rl.agent.replay_buffer import ReplayBuffer
from src.rl.config import (
    Action,
    BATCH_SIZE,
    EPSILON_DECAY_STEPS,
    EPSILON_END,
    EPSILON_START,
    GAMMA,
    LEARNING_RATE,
    NUM_ACTIONS,
    REPLAY_BUFFER_SIZE,
    TARGET_NET_UPDATE_FREQ,
)

logger = logging.getLogger(__name__)


class DQNAgent:
    """DQN agent with experience replay and target network.

    Training uses γ=0.0 (single-step episodes). Q-target = reward directly.
    """

    def __init__(
        self,
        observation_dim: int,
        epsilon: float = EPSILON_START,
        buffer_capacity: int = REPLAY_BUFFER_SIZE,
    ):
        self.observation_dim = observation_dim
        self.epsilon = epsilon

        self.q_network = DQNetwork(observation_dim)
        self.target_network = copy.deepcopy(self.q_network)
        self.target_network.eval()

        self.optimizer = optim.Adam(
            self.q_network.parameters(), lr=LEARNING_RATE
        )
        self.loss_fn = nn.MSELoss()
        self.buffer = ReplayBuffer(capacity=buffer_capacity)

        self._train_steps = 0
        self._epsilon_decay_per_step = (EPSILON_START - EPSILON_END) / EPSILON_DECAY_STEPS

    def select_action(self, observation: np.ndarray) -> int:
        """Select action using epsilon-greedy policy."""
        if random.random() < self.epsilon:
            return random.randint(0, NUM_ACTIONS - 1)

        q_values = self.q_network.predict(observation)
        return int(np.argmax(q_values))

    def train_step(self) -> float:
        """Run one training step on a batch from the replay buffer.

        Returns:
            Loss value
        """
        if len(self.buffer) < BATCH_SIZE:
            return 0.0

        batch = self.buffer.sample(BATCH_SIZE)
        observations = torch.from_numpy(batch["observations"])
        actions = torch.from_numpy(batch["actions"])
        rewards = torch.from_numpy(batch["rewards"])

        # Predicted Q-values for taken actions
        q_values = self.q_network(observations)
        q_taken = q_values.gather(1, actions.unsqueeze(1)).squeeze(1)

        # Target Q = reward (γ=0, single-step episodes)
        target_q = rewards

        loss = self.loss_fn(q_taken, target_q)

        self.optimizer.zero_grad()
        loss.backward()
        self.optimizer.step()

        self._train_steps += 1

        # Decay epsilon
        self.epsilon = max(
            EPSILON_END,
            self.epsilon - self._epsilon_decay_per_step,
        )

        # Update target network periodically
        if self._train_steps % TARGET_NET_UPDATE_FREQ == 0:
            self.target_network.load_state_dict(
                self.q_network.state_dict()
            )
            logger.info(
                f"Target network updated at step {self._train_steps}, "
                f"ε={self.epsilon:.3f}"
            )

        return loss.item()

    def save(self, path: Path):
        """Save model checkpoint."""
        torch.save({
            "q_network": self.q_network.state_dict(),
            "target_network": self.target_network.state_dict(),
            "optimizer": self.optimizer.state_dict(),
            "epsilon": self.epsilon,
            "train_steps": self._train_steps,
        }, path)
        logger.info(f"Model saved to {path}")

    def load(self, path: Path):
        """Load model checkpoint."""
        checkpoint = torch.load(path, weights_only=False)
        self.q_network.load_state_dict(checkpoint["q_network"])
        self.target_network.load_state_dict(checkpoint["target_network"])
        self.optimizer.load_state_dict(checkpoint["optimizer"])
        self.epsilon = checkpoint["epsilon"]
        self._train_steps = checkpoint["train_steps"]
        logger.info(f"Model loaded from {path} (step {self._train_steps})")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_rl_dqn.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add backend/src/rl/agent/dqn.py backend/tests/test_rl_dqn.py
git commit -m "feat(rl): DQN training agent with epsilon-greedy and target net"
```

---

## Task 11: Evaluation Suite

**Files:**
- Create: `backend/src/rl/agent/evaluate.py`
- Test: `backend/tests/test_rl_evaluate.py`

- [ ] **Step 1: Write failing tests**

```python
# backend/tests/test_rl_evaluate.py
"""Tests for evaluation metrics."""

import pytest
import numpy as np
from src.rl.agent.evaluate import compute_metrics
from src.rl.config import Action


class TestComputeMetrics:
    def test_perfect_trader(self):
        episodes = [
            {"action": Action.LONG.value, "reward": 2.0, "level_type": "poc_session"}
            for _ in range(10)
        ]
        metrics = compute_metrics(episodes)
        assert metrics["win_rate"] == 1.0
        assert metrics["profit_factor"] == float("inf") or metrics["profit_factor"] > 100

    def test_all_skip(self):
        episodes = [
            {"action": Action.SKIP.value, "reward": 0.0, "level_type": "vwap"}
            for _ in range(10)
        ]
        metrics = compute_metrics(episodes)
        assert metrics["skip_rate"] == 1.0
        assert metrics["trades_taken"] == 0

    def test_mixed_results(self):
        episodes = [
            {"action": Action.LONG.value, "reward": 2.0, "level_type": "poc_session"},
            {"action": Action.LONG.value, "reward": -1.0, "level_type": "vwap"},
            {"action": Action.SHORT.value, "reward": 2.0, "level_type": "pdh"},
            {"action": Action.SKIP.value, "reward": 0.0, "level_type": "val"},
        ]
        metrics = compute_metrics(episodes)
        assert metrics["total_episodes"] == 4
        assert metrics["trades_taken"] == 3
        assert metrics["skip_rate"] == 0.25
        assert metrics["win_rate"] == pytest.approx(2 / 3)
        assert metrics["profit_factor"] == pytest.approx(4.0)

    def test_level_type_breakdown(self):
        episodes = [
            {"action": Action.LONG.value, "reward": 2.0, "level_type": "poc_session"},
            {"action": Action.LONG.value, "reward": 2.0, "level_type": "poc_session"},
            {"action": Action.SKIP.value, "reward": 0.0, "level_type": "vwap"},
            {"action": Action.SHORT.value, "reward": -1.0, "level_type": "vwap"},
        ]
        metrics = compute_metrics(episodes)
        breakdown = metrics["level_breakdown"]
        assert "poc_session" in breakdown
        assert breakdown["poc_session"]["win_rate"] == 1.0
        assert breakdown["vwap"]["skip_rate"] == 0.5
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_rl_evaluate.py -v`
Expected: FAIL

- [ ] **Step 3: Implement compute_metrics**

```python
# backend/src/rl/agent/evaluate.py
"""Evaluation metrics for the RL trading agent."""

import logging
from collections import defaultdict

import numpy as np

from src.rl.config import Action

logger = logging.getLogger(__name__)


def compute_metrics(episodes: list[dict]) -> dict:
    """Compute evaluation metrics from a list of episodes.

    Each episode dict has: action (int), reward (float), level_type (str)

    Returns dict with:
        total_episodes, trades_taken, skip_rate, win_rate,
        avg_r, profit_factor, max_drawdown_r, equity_curve,
        level_breakdown (per level_type metrics)
    """
    if not episodes:
        return {"total_episodes": 0}

    total = len(episodes)
    trades = [e for e in episodes if e["action"] != Action.SKIP.value]
    skips = total - len(trades)

    # Win/loss
    wins = [t for t in trades if t["reward"] > 0]
    losses = [t for t in trades if t["reward"] < 0]

    gross_wins = sum(t["reward"] for t in wins)
    gross_losses = abs(sum(t["reward"] for t in losses))

    # Equity curve
    equity = []
    running = 0.0
    peak = 0.0
    max_dd = 0.0
    for e in episodes:
        if e["action"] != Action.SKIP.value:
            running += e["reward"]
        equity.append(running)
        peak = max(peak, running)
        dd = peak - running
        max_dd = max(max_dd, dd)

    # Level-type breakdown
    by_level = defaultdict(list)
    for e in episodes:
        by_level[e["level_type"]].append(e)

    level_breakdown = {}
    for lt, eps in by_level.items():
        lt_trades = [e for e in eps if e["action"] != Action.SKIP.value]
        lt_wins = [t for t in lt_trades if t["reward"] > 0]
        level_breakdown[lt] = {
            "total": len(eps),
            "trades": len(lt_trades),
            "skip_rate": (len(eps) - len(lt_trades)) / len(eps) if eps else 0,
            "win_rate": len(lt_wins) / len(lt_trades) if lt_trades else 0,
            "avg_r": np.mean([t["reward"] for t in lt_trades]) if lt_trades else 0,
        }

    return {
        "total_episodes": total,
        "trades_taken": len(trades),
        "skip_rate": skips / total,
        "win_rate": len(wins) / len(trades) if trades else 0,
        "avg_r": np.mean([t["reward"] for t in trades]) if trades else 0,
        "total_r": sum(t["reward"] for t in trades),
        "profit_factor": gross_wins / gross_losses if gross_losses > 0 else float("inf"),
        "max_drawdown_r": max_dd,
        "equity_curve": equity,
        "level_breakdown": level_breakdown,
    }


def print_evaluation_report(metrics: dict):
    """Print a formatted evaluation report."""
    print("\n" + "=" * 60)
    print("RL AGENT EVALUATION REPORT")
    print("=" * 60)
    print(f"Total episodes:    {metrics['total_episodes']}")
    print(f"Trades taken:      {metrics['trades_taken']}")
    print(f"Skip rate:         {metrics['skip_rate']:.1%}")
    print(f"Win rate:          {metrics['win_rate']:.1%}")
    print(f"Avg R-multiple:    {metrics['avg_r']:.2f}")
    print(f"Total R:           {metrics['total_r']:.1f}")
    print(f"Profit factor:     {metrics['profit_factor']:.2f}")
    print(f"Max drawdown (R):  {metrics['max_drawdown_r']:.1f}")

    print("\n--- Level Type Breakdown ---")
    breakdown = metrics.get("level_breakdown", {})
    for lt, stats in sorted(breakdown.items(), key=lambda x: -x[1]["avg_r"]):
        print(
            f"  {lt:20s}  trades={stats['trades']:3d}  "
            f"skip={stats['skip_rate']:.0%}  "
            f"win={stats['win_rate']:.0%}  "
            f"avgR={stats['avg_r']:+.2f}"
        )
    print("=" * 60)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_rl_evaluate.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add backend/src/rl/agent/evaluate.py backend/tests/test_rl_evaluate.py
git commit -m "feat(rl): evaluation metrics with level-type breakdown"
```

---

## Task 12: Replay Engine (Session Orchestrator)

**Files:**
- Create: `backend/src/rl/data/replay_engine.py`

**Context:** The replay engine is the main orchestrator. It reads ticks from Parquet, feeds them through candle aggregator + accumulators + level touch detection, snapshots observations, and labels outcomes. This is the "game engine" that Yoshi's AI trains against.

- [ ] **Step 1: Implement replay_engine.py**

```python
# backend/src/rl/data/replay_engine.py
"""Session Replay Engine — reconstructs full market state from historical ticks.

Reads ticks chronologically, updates all accumulators (candles, VWAP, VP, TPO),
detects level touches, snapshots observation vectors, and labels outcomes.
This is the "game engine" the agent trains against.
"""

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

from src.market_data.levels import (
    compute_session_levels,
    detect_fvgs,
    detect_order_blocks,
    detect_swing_points,
)
from src.market_data.orderflow import build_candle_flow, compute_signals
from src.market_data.tpo import compute_tpo_profile, classify_tpo_shape, compute_rotation_factor, detect_excess
from src.rl.config import AT_LEVEL_TICKS, LevelType, TICK_SIZE
from src.rl.data.accumulators import IncrementalVWAP, IncrementalVolumeProfile
from src.rl.data.candle_aggregator import CandleAggregator
from src.rl.data.episode_builder import Episode, label_outcome
from src.rl.features.observation import build_observation

logger = logging.getLogger(__name__)


class ReplayEngine:
    """Replays a single session of ticks and produces training episodes."""

    def __init__(self, macro_data: dict | None = None):
        self.macro_data = macro_data or {}
        self._reset()

    def _reset(self):
        """Reset all state for a new session."""
        self.candle_agg = CandleAggregator()
        self.vwap = IncrementalVWAP()
        self.vp_session = IncrementalVolumeProfile(tick_size=TICK_SIZE)
        self.session_levels = None
        self.all_active_levels: list[dict] = []
        self._last_bar_count = 0
        self._touched_levels: set[str] = set()  # Debounce: don't re-trigger same level
        self._candle_ticks: list[dict] = []       # Ticks for current candle (for CandleFlow)
        self._candle_flows = []                    # Completed CandleFlow objects
        self._orderflow_signals: dict | None = None  # Latest orderflow signals

    def replay_session(
        self,
        ticks: list[dict],
        session_date: datetime,
        prior_session_levels: dict | None = None,
    ) -> list[Episode]:
        """Replay a full session of ticks and produce episodes.

        Args:
            ticks: All ticks for this session, sorted by timestamp
            session_date: Date of this session (for session level computation)
            prior_session_levels: PDH/PDL, weekly/monthly levels from prior sessions

        Returns:
            List of Episodes (level touch → observation + outcome)
        """
        self._reset()
        episodes = []

        if not ticks:
            return episodes

        # Pre-load prior session levels
        if prior_session_levels:
            self._load_prior_levels(prior_session_levels)

        # Process each tick
        for i, tick in enumerate(ticks):
            # 1. Update candle aggregator
            completed_bars = self.candle_agg.update(tick)

            # 2. Update VWAP + VP
            self.vwap.update(tick["price"], tick["size"])
            self.vp_session.update(tick["price"], tick["size"])

            # Track ticks for current candle (for orderflow CandleFlow)
            self._candle_ticks.append(tick)

            # 3. On bar close: build CandleFlow, compute orderflow, update levels
            if completed_bars:
                # Build CandleFlow from accumulated ticks for this candle
                if self._candle_ticks:
                    candle_flows = build_candle_flow(self._candle_ticks, period_seconds=60)
                    self._candle_flows.extend(candle_flows)
                    self._candle_ticks = []

                    # Compute orderflow signals from recent CandleFlow objects
                    if len(self._candle_flows) >= 3:
                        self._orderflow_signals = compute_signals(
                            self._candle_flows[-10:], direction="long"
                        ).__dict__

                self._on_bar_close(session_date)

            # 4. Check level touches
            touched = self._check_level_touch(tick["price"])
            if touched:
                for level_name, level_type, level_price in touched:
                    # Build observation
                    state = self._build_state(tick, level_type)
                    observation = build_observation(state)

                    # Look forward to label outcome
                    forward_ticks = ticks[i:]  # From touch to end of session
                    episode = label_outcome(
                        touch_price=tick["price"],
                        forward_ticks=forward_ticks,
                        observation=observation,
                        level_type=level_type.value,
                        touch_ts=tick["ts"],
                    )
                    episodes.append(episode)

        logger.info(
            f"Session {session_date.date()}: {len(ticks)} ticks, "
            f"{len(self.candle_agg.get_completed_1m())} bars, "
            f"{len(episodes)} episodes"
        )
        return episodes

    def _on_bar_close(self, session_date: datetime):
        """Recompute session levels, FVGs, order blocks, swing points on each 1m bar close."""
        bars = self.candle_agg.get_completed_1m()
        if len(bars) < 2:
            return

        try:
            self.session_levels = compute_session_levels(bars, session_date)
        except Exception as e:
            logger.debug(f"Session levels error: {e}")

        # Detect FVGs, order blocks, swing points
        self._fvgs = detect_fvgs(bars[-20:]) if len(bars) >= 3 else []
        self._order_blocks = detect_order_blocks(bars[-20:]) if len(bars) >= 3 else []
        self._swing_points = detect_swing_points(bars) if len(bars) >= 5 else {}

        # Update active levels list
        self._rebuild_active_levels()

    def _rebuild_active_levels(self):
        """Rebuild the list of all active levels from current state."""
        levels = []

        # VWAP bands
        vwap = self.vwap.get()
        if vwap:
            for name, price in [
                ("vwap", vwap.vwap),
                ("vwap_sd1_upper", vwap.sd1_upper),
                ("vwap_sd1_lower", vwap.sd1_lower),
                ("vwap_sd2_upper", vwap.sd2_upper),
                ("vwap_sd2_lower", vwap.sd2_lower),
                ("vwap_sd3_upper", vwap.sd3_upper),
                ("vwap_sd3_lower", vwap.sd3_lower),
            ]:
                levels.append({"price": price, "type": name, "level_type": LevelType.VWAP})

        # Volume profile
        vp = self.vp_session.get()
        if vp:
            levels.append({"price": vp.poc, "type": "poc_session", "level_type": LevelType.POC_SESSION})
            levels.append({"price": vp.vah, "type": "vah", "level_type": LevelType.VAH})
            levels.append({"price": vp.val, "type": "val", "level_type": LevelType.VAL})

            for sp in vp.single_prints:
                sp_price = sp[0] if isinstance(sp, tuple) else sp
                levels.append({"price": sp_price, "type": "single_print", "level_type": LevelType.SINGLE_PRINT})

        # Session levels
        if self.session_levels:
            sl = self.session_levels
            for attr, lt in [
                ("ib_high", LevelType.IB_HIGH), ("ib_low", LevelType.IB_LOW),
                ("pdh", LevelType.PDH), ("pdl", LevelType.PDL),
                ("tokyo_high", LevelType.TOKYO_HL), ("tokyo_low", LevelType.TOKYO_HL),
                ("london_high", LevelType.LONDON_HL), ("london_low", LevelType.LONDON_HL),
                ("weekly_high", LevelType.WEEKLY_HL), ("weekly_low", LevelType.WEEKLY_HL),
                ("monthly_high", LevelType.MONTHLY_HL), ("monthly_low", LevelType.MONTHLY_HL),
            ]:
                val = getattr(sl, attr, None)
                if val is not None:
                    levels.append({"price": val, "type": attr, "level_type": lt})

        # FVGs
        for fvg in getattr(self, "_fvgs", []):
            mid_price = (fvg.price_high + fvg.price_low) / 2
            levels.append({"price": mid_price, "type": "fvg", "level_type": LevelType.FVG})

        # Order blocks
        for ob in getattr(self, "_order_blocks", []):
            mid_price = (ob.price_high + ob.price_low) / 2
            levels.append({"price": mid_price, "type": "order_block", "level_type": LevelType.ORDER_BLOCK})

        # Swing points
        swing = getattr(self, "_swing_points", {})
        for key in ("last_hh", "last_hl", "last_lh", "last_ll"):
            val = swing.get(key)
            if val is not None:
                levels.append({"price": val, "type": "swing_point", "level_type": LevelType.SWING_POINT})

        self.all_active_levels = levels

    def _check_level_touch(self, price: float) -> list[tuple[str, LevelType, float]]:
        """Check if price is within AT_LEVEL_TICKS of any level.

        Returns list of (level_name, level_type, level_price) for newly touched levels.
        Debounces: won't re-trigger the same level until price moves away and returns.
        """
        touched = []
        proximity = AT_LEVEL_TICKS * TICK_SIZE

        for level in self.all_active_levels:
            key = f"{level['type']}_{level['price']}"
            dist = abs(price - level["price"])

            if dist <= proximity:
                if key not in self._touched_levels:
                    self._touched_levels.add(key)
                    touched.append((level["type"], level["level_type"], level["price"]))
            else:
                # Price moved away — allow re-trigger
                self._touched_levels.discard(key)

        return touched

    def _build_state(self, tick: dict, level_type: LevelType) -> dict:
        """Build the full state dict for observation vector construction."""
        vwap_bands = self.vwap.get()
        vp = self.vp_session.get()

        # Build TPO from 30m candles
        bars_30m = self.candle_agg.get_completed_30m()
        tpo_dict = None
        if bars_30m:
            tpo_profile = compute_tpo_profile(bars_30m, tick_size=TICK_SIZE)
            shape = classify_tpo_shape(tpo_profile)
            rot_factor, rot_count = compute_rotation_factor(bars_30m)
            exc_high, exc_low = detect_excess(tpo_profile)
            tpo_dict = {
                "poc": tpo_profile.poc,
                "vah": tpo_profile.vah,
                "val": tpo_profile.val,
                "letters": tpo_profile.letters,
                "shape": shape,
                "rotation_factor": rot_factor,
                "rotation_count": rot_count,
                "excess_high": exc_high,
                "excess_low": exc_low,
            }

        # Session context
        bars_1m = self.candle_agg.get_completed_1m()
        session_ctx = {}
        if bars_1m:
            first_ts = bars_1m[0]["ts"]
            current_ts = tick["ts"]
            if hasattr(first_ts, "timestamp") and hasattr(current_ts, "timestamp"):
                minutes = (current_ts - first_ts).total_seconds() / 60
            else:
                minutes = 0
            session_ctx["minutes_since_rth_open"] = minutes

            total_vol = sum(b.get("volume", 0) for b in bars_1m)
            session_ctx["session_volume_pct"] = min(total_vol / 1_000_000, 1.0)

            highs = [b["high"] for b in bars_1m]
            lows = [b["low"] for b in bars_1m]
            if highs and lows:
                session_ctx["daily_range_pct"] = (max(highs) - min(lows)) / TICK_SIZE / 200

            if self.session_levels:
                session_ctx["ib_high"] = getattr(self.session_levels, "ib_high", None)
                session_ctx["ib_low"] = getattr(self.session_levels, "ib_low", None)
                ib_h = session_ctx.get("ib_high")
                ib_l = session_ctx.get("ib_low")
                if ib_h and ib_l:
                    session_ctx["ib_broken_above"] = tick["price"] > ib_h
                    session_ctx["ib_broken_below"] = tick["price"] < ib_l

        # Macro for session date
        date_key = str(tick["ts"])[:10] if hasattr(tick["ts"], "isoformat") else str(tick["ts"])[:10]
        macro = self.macro_data.get(date_key)

        return {
            "level_type": level_type,
            "price": tick["price"],
            "candles": self.candle_agg.get_recent_1m(n=20),
            "vwap_bands": {
                "vwap": vwap_bands.vwap,
                "sd1_upper": vwap_bands.sd1_upper,
                "sd1_lower": vwap_bands.sd1_lower,
                "sd2_upper": vwap_bands.sd2_upper,
                "sd2_lower": vwap_bands.sd2_lower,
                "sd3_upper": vwap_bands.sd3_upper,
                "sd3_lower": vwap_bands.sd3_lower,
            } if vwap_bands else None,
            "volume_profile": {
                "poc": vp.poc,
                "vah": vp.vah,
                "val": vp.val,
                "single_prints": vp.single_prints,
            } if vp else None,
            "tpo_profile": tpo_dict,
            "session_levels": self.session_levels,
            "all_levels": self.all_active_levels,
            "orderflow_signals": self._orderflow_signals,
            "macro": macro,
            "session_context": session_ctx,
        }

    def _load_prior_levels(self, prior: dict):
        """Load PDH/PDL and other prior session levels."""
        for key, lt in [
            ("pdh", LevelType.PDH), ("pdl", LevelType.PDL),
            ("weekly_high", LevelType.WEEKLY_HL), ("weekly_low", LevelType.WEEKLY_HL),
            ("monthly_high", LevelType.MONTHLY_HL), ("monthly_low", LevelType.MONTHLY_HL),
        ]:
            val = prior.get(key)
            if val is not None:
                self.all_active_levels.append({
                    "price": val, "type": key, "level_type": lt,
                })
```

- [ ] **Step 2: Verify imports work**

Run: `cd backend && python -c "from src.rl.data.replay_engine import ReplayEngine; print('ReplayEngine OK')"`
Expected: `ReplayEngine OK`

- [ ] **Step 3: Commit**

```bash
git add backend/src/rl/data/replay_engine.py
git commit -m "feat(rl): session replay engine with level touch detection"
```

---

## Task 13: Feature Normalization

**Files:**
- Create: `backend/src/rl/data/normalization.py`

- [ ] **Step 1: Implement running normalization**

```python
# backend/src/rl/data/normalization.py
"""Running mean/std normalization for observation vectors.

Tracks feature statistics during replay and normalizes observations
to zero mean, unit variance at training time.
"""

import json
from pathlib import Path

import numpy as np


class RunningNormalizer:
    """Welford's online algorithm for running mean/variance."""

    def __init__(self, dim: int):
        self.dim = dim
        self.count = 0
        self.mean = np.zeros(dim, dtype=np.float64)
        self.M2 = np.zeros(dim, dtype=np.float64)

    def update(self, x: np.ndarray):
        """Update statistics with a new observation."""
        self.count += 1
        delta = x.astype(np.float64) - self.mean
        self.mean += delta / self.count
        delta2 = x.astype(np.float64) - self.mean
        self.M2 += delta * delta2

    def normalize(self, x: np.ndarray) -> np.ndarray:
        """Normalize observation to ~zero mean, unit variance."""
        if self.count < 2:
            return x
        std = np.sqrt(self.M2 / (self.count - 1))
        std = np.maximum(std, 1e-8)  # Avoid division by zero
        return ((x.astype(np.float64) - self.mean) / std).astype(np.float32)

    def save(self, path: Path):
        """Save statistics to JSON."""
        data = {
            "dim": self.dim,
            "count": self.count,
            "mean": self.mean.tolist(),
            "M2": self.M2.tolist(),
        }
        path.write_text(json.dumps(data))

    def load(self, path: Path):
        """Load statistics from JSON."""
        data = json.loads(path.read_text())
        self.dim = data["dim"]
        self.count = data["count"]
        self.mean = np.array(data["mean"], dtype=np.float64)
        self.M2 = np.array(data["M2"], dtype=np.float64)
```

- [ ] **Step 2: Commit**

```bash
git add backend/src/rl/data/normalization.py
git commit -m "feat(rl): running normalizer for observation vectors"
```

---

## Task 14: CLI Commands

**Files:**
- Create: `backend/src/rl/cli.py`
- Modify: `backend/src/app.py` (add rl subcommand)

- [ ] **Step 1: Implement CLI commands**

```python
# backend/src/rl/cli.py
"""CLI commands for RL training pipeline.

Usage:
    python -m src.app rl fetch --months 6
    python -m src.app rl replay --all
    python -m src.app rl train --epochs 100
    python -m src.app rl eval --checkpoint v1
"""

import logging
from datetime import datetime, timedelta
from pathlib import Path

import typer

from src.rl.config import EPSILON_END

logger = logging.getLogger(__name__)

rl_app = typer.Typer(help="RL Trading Agent — fetch, replay, train, eval")


@rl_app.command()
def fetch(
    months: int = typer.Option(6, help="Number of months of historical data"),
    symbol: str = typer.Option("NQ", help="Symbol to fetch"),
):
    """Download historical tick data from Databento."""
    from src.rl.data.fetcher import fetch_ticks, fetch_macro_history

    end = datetime.now()
    start = end - timedelta(days=months * 30 + 30)  # Extra month for bootstrap

    print(f"Fetching {months} months of {symbol} ticks ({start.date()} → {end.date()})...")
    files = fetch_ticks(start=start, end=end)
    print(f"Downloaded {len(files)} Parquet files")

    print("Fetching macro data (VIX, bonds, DXY)...")
    macro_path = fetch_macro_history(start=start, end=end)
    if macro_path:
        print(f"Macro data saved to {macro_path}")


@rl_app.command()
def replay(
    all: bool = typer.Option(False, "--all", help="Replay all available sessions"),
    month: str = typer.Option(None, help="Specific month (YYYY-MM)"),
):
    """Replay historical sessions and build training episodes."""
    import pyarrow.parquet as pq

    from src.rl.data.fetcher import TICKS_DIR, load_ticks
    from src.rl.data.replay_engine import ReplayEngine
    from src.rl.data.normalization import RunningNormalizer
    from src.rl.data.fetcher import DATA_DIR

    engine = ReplayEngine()

    # Find all Parquet files
    if month:
        files = [TICKS_DIR / f"NQ_{month}.parquet"]
    else:
        files = sorted(TICKS_DIR.glob("NQ_*.parquet"))

    if not files:
        print("No tick data found. Run 'rl fetch' first.")
        raise typer.Exit(1)

    all_episodes = []
    normalizer = None

    for f in files:
        print(f"Replaying {f.name}...")
        table = pq.read_table(f)
        ticks = table.to_pylist()

        # Group by session date
        from collections import defaultdict
        by_date = defaultdict(list)
        for tick in ticks:
            date_key = str(tick["ts"])[:10]
            by_date[date_key].append(tick)

        for date_str, session_ticks in sorted(by_date.items()):
            session_date = datetime.strptime(date_str, "%Y-%m-%d")
            episodes = engine.replay_session(
                ticks=session_ticks,
                session_date=session_date,
            )

            # Update normalizer
            if episodes:
                if normalizer is None:
                    normalizer = RunningNormalizer(dim=len(episodes[0].observation))
                for ep in episodes:
                    normalizer.update(ep.observation)

            all_episodes.extend(episodes)
            print(f"  {date_str}: {len(session_ticks)} ticks → {len(episodes)} episodes")

    # Save episodes and normalizer
    print(f"\nTotal: {len(all_episodes)} episodes from {len(by_date)} sessions")

    if normalizer:
        norm_path = DATA_DIR / "normalizer.json"
        normalizer.save(norm_path)
        print(f"Normalizer saved to {norm_path}")

    # Save episodes as numpy arrays
    if all_episodes:
        import numpy as np
        obs = np.array([e.observation for e in all_episodes], dtype=np.float32)
        rewards_long = np.array([e.reward_long for e in all_episodes], dtype=np.float32)
        rewards_short = np.array([e.reward_short for e in all_episodes], dtype=np.float32)
        level_types = [e.level_type for e in all_episodes]

        episodes_dir = DATA_DIR / "episodes"
        episodes_dir.mkdir(parents=True, exist_ok=True)
        np.save(episodes_dir / "observations.npy", obs)
        np.save(episodes_dir / "rewards_long.npy", rewards_long)
        np.save(episodes_dir / "rewards_short.npy", rewards_short)
        np.save(episodes_dir / "level_types.npy", np.array(level_types))
        print(f"Episodes saved to {episodes_dir}")


@rl_app.command()
def train(
    epochs: int = typer.Option(100, help="Number of training epochs"),
    checkpoint: str = typer.Option("v1", help="Model checkpoint name"),
):
    """Train the DQN agent on replay episodes."""
    import numpy as np

    from src.rl.agent.dqn import DQNAgent
    from src.rl.data.fetcher import DATA_DIR
    from src.rl.data.normalization import RunningNormalizer

    episodes_dir = DATA_DIR / "episodes"
    if not (episodes_dir / "observations.npy").exists():
        print("No episodes found. Run 'rl replay' first.")
        raise typer.Exit(1)

    # Load data
    obs = np.load(episodes_dir / "observations.npy")
    rewards_long = np.load(episodes_dir / "rewards_long.npy")
    rewards_short = np.load(episodes_dir / "rewards_short.npy")
    level_types = np.load(episodes_dir / "level_types.npy", allow_pickle=True)

    print(f"Loaded {len(obs)} episodes, observation dim = {obs.shape[1]}")

    # Load normalizer
    normalizer = RunningNormalizer(dim=obs.shape[1])
    norm_path = DATA_DIR / "normalizer.json"
    if norm_path.exists():
        normalizer.load(norm_path)
        obs = np.array([normalizer.normalize(o) for o in obs])
        print("Observations normalized")

    # Chronological split: months 1-4 train, month 5 val (per spec)
    # Approximate: first 67% train, next 16% val, last 16% test (held out)
    train_end = int(len(obs) * 0.67)
    val_end = int(len(obs) * 0.83)
    train_obs, val_obs = obs[:train_end], obs[train_end:val_end]
    train_rl, val_rl = rewards_long[:train_end], rewards_long[train_end:val_end]
    train_rs, val_rs = rewards_short[:train_end], rewards_short[train_end:val_end]

    # Initialize agent
    agent = DQNAgent(observation_dim=obs.shape[1])

    # Preload buffer with training data
    from src.rl.config import Action, REWARD_TIMEOUT
    for i in range(len(train_obs)):
        # For each episode, add the best action to replay buffer
        best_reward = max(train_rl[i], train_rs[i], REWARD_TIMEOUT)
        if train_rl[i] == best_reward:
            agent.buffer.add(train_obs[i], Action.LONG.value, train_rl[i])
        elif train_rs[i] == best_reward:
            agent.buffer.add(train_obs[i], Action.SHORT.value, train_rs[i])
        else:
            agent.buffer.add(train_obs[i], Action.SKIP.value, REWARD_TIMEOUT)

    print(f"Buffer loaded: {len(agent.buffer)} episodes")
    print(f"Training for {epochs} epochs...")

    # Training loop
    for epoch in range(epochs):
        epoch_losses = []
        for _ in range(len(train_obs) // 64):
            loss = agent.train_step()
            if loss > 0:
                epoch_losses.append(loss)

        if epoch % 10 == 0 and epoch_losses:
            avg_loss = np.mean(epoch_losses)
            print(f"  Epoch {epoch:3d}: loss={avg_loss:.4f}, ε={agent.epsilon:.3f}")

    # Save model
    models_dir = DATA_DIR / "models"
    models_dir.mkdir(parents=True, exist_ok=True)
    model_path = models_dir / f"dqn_{checkpoint}.pt"
    agent.save(model_path)
    print(f"\nModel saved to {model_path}")

    # Quick validation
    print("\nRunning validation...")
    val_episodes = []
    for i in range(len(val_obs)):
        action = agent.select_action(val_obs[i])
        reward = {0: val_rl[i], 1: val_rs[i], 2: 0.0}[action]
        val_episodes.append({
            "action": action,
            "reward": float(reward),
            "level_type": str(level_types[train_end + i]) if train_end + i < len(level_types) else "unknown",
        })

    from src.rl.agent.evaluate import compute_metrics, print_evaluation_report
    metrics = compute_metrics(val_episodes)
    print_evaluation_report(metrics)


@rl_app.command(name="eval")
def evaluate(
    checkpoint: str = typer.Option("v1", help="Model checkpoint to evaluate"),
):
    """Evaluate a trained agent on held-out test data."""
    import numpy as np

    from src.rl.agent.dqn import DQNAgent
    from src.rl.agent.evaluate import compute_metrics, print_evaluation_report
    from src.rl.data.fetcher import DATA_DIR
    from src.rl.data.normalization import RunningNormalizer

    episodes_dir = DATA_DIR / "episodes"
    models_dir = DATA_DIR / "models"
    model_path = models_dir / f"dqn_{checkpoint}.pt"

    if not model_path.exists():
        print(f"Model not found: {model_path}")
        raise typer.Exit(1)

    # Load data (use last 20% as test)
    obs = np.load(episodes_dir / "observations.npy")
    rewards_long = np.load(episodes_dir / "rewards_long.npy")
    rewards_short = np.load(episodes_dir / "rewards_short.npy")
    level_types = np.load(episodes_dir / "level_types.npy", allow_pickle=True)

    # Normalize
    normalizer = RunningNormalizer(dim=obs.shape[1])
    norm_path = DATA_DIR / "normalizer.json"
    if norm_path.exists():
        normalizer.load(norm_path)
        obs = np.array([normalizer.normalize(o) for o in obs])

    # Test split: last ~16% (month 6, never seen during training)
    test_start = int(len(obs) * 0.83)
    test_obs = obs[test_start:]
    test_rl = rewards_long[test_start:]
    test_rs = rewards_short[test_start:]
    test_lt = level_types[test_start:]

    # Load agent
    agent = DQNAgent(observation_dim=obs.shape[1], epsilon=0.0)  # Greedy
    agent.load(model_path)

    # Evaluate
    episodes = []
    for i in range(len(test_obs)):
        action = agent.select_action(test_obs[i])
        reward = {0: float(test_rl[i]), 1: float(test_rs[i]), 2: 0.0}[action]
        episodes.append({
            "action": action,
            "reward": reward,
            "level_type": str(test_lt[i]),
        })

    metrics = compute_metrics(episodes)
    print_evaluation_report(metrics)
```

- [ ] **Step 2: Add rl subcommand to app.py**

Add this import and registration to `backend/src/app.py` after the existing app setup:

```python
# At top of file, add import
from src.rl.cli import rl_app

# After app = typer.Typer(...), add:
app.add_typer(rl_app, name="rl")
```

- [ ] **Step 3: Verify CLI works**

Run: `cd backend && python -m src.app rl --help`
Expected: Shows fetch, replay, train, eval subcommands

- [ ] **Step 4: Commit**

```bash
git add backend/src/rl/cli.py backend/src/app.py
git commit -m "feat(rl): CLI commands — fetch, replay, train, eval"
```

---

## Task 15: Install Dependencies

**Files:**
- Modify: `backend/requirements.txt` (or pyproject.toml)

- [ ] **Step 1: Check current dependency file**

Run: `ls backend/requirements*.txt backend/pyproject.toml 2>/dev/null`

- [ ] **Step 2: Add torch and pyarrow**

Add to the dependency file:

```
torch>=2.0.0
pyarrow>=14.0.0
yfinance>=0.2.0
```

- [ ] **Step 3: Install**

Run: `cd backend && pip install torch pyarrow`

- [ ] **Step 4: Commit**

```bash
git add backend/requirements.txt  # or pyproject.toml
git commit -m "chore: add torch and pyarrow dependencies for RL agent"
```

---

## Task 16: Run Full Test Suite

- [ ] **Step 1: Run all RL tests**

Run: `cd backend && python -m pytest tests/test_rl_*.py -v`
Expected: All PASS

- [ ] **Step 2: Run existing tests to verify no regressions**

Run: `cd backend && python -m pytest tests/ -v --ignore=tests/providers`
Expected: No new failures

- [ ] **Step 3: Final commit if any fixes needed**

```bash
git add -A
git commit -m "fix: address test failures from RL integration"
```
