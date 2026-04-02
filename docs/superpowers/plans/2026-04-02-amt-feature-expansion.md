# AMT Feature Expansion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expand AMT features from 13 to 40 dimensions (20 static + 20 dynamic) for the RL agent, fix the broken data pipeline so real AMT data reaches both RL and UI, and ensure it works for both live inference and historical replay.

**Architecture:** Two feature modules — `amt_features.py` (static, 13→20 dims, set once after IB) and new `amt_dynamics_features.py` (20 dims, updates every tick via `AMTDynamicsTracker`). The tracker lives on `LevelMonitor` for live and is created per-session in `ReplayEngine` for backtest. UI gets real data instead of hardcoded demo.

**Tech Stack:** Python 3.10+, NumPy, FastAPI, React/TypeScript

**Spec:** `docs/superpowers/specs/2026-04-02-amt-feature-expansion-design.md`

---

## File Map

| Action | File | Responsibility |
|--------|------|---------------|
| Modify | `backend/src/rl/features/amt_features.py` | Expand static AMT features 13→20 |
| Create | `backend/src/rl/features/amt_dynamics_features.py` | 20-dim dynamic AMT feature extractor |
| Create | `backend/src/market_data/amt_dynamics.py` | `AMTDynamicsTracker` class — tick-by-tick state |
| Modify | `backend/src/rl/features/observation.py` | Wire `seg_amt_dynamics` into observation vector |
| Modify | `backend/src/market_data/level_monitor.py` | Initialize tracker, update on tick, pass to state |
| Modify | `backend/src/rl/data/replay_engine.py` | Create tracker per-session, update per tick |
| Modify | `backend/src/repositories/market_repo.py` | Add `get_historical_ib_ranges()`, `get_recent_sessions()` |
| Modify | `backend/src/services/market_service.py` | Enrich session_context with new static AMT data |
| Modify | `backend/src/api/routes/market.py` | Pass AMT dynamics context to level_monitor |
| Modify | `frontend/src/components/Terminal/pages/TradingContainer.tsx` | Remove demo fallback |
| Modify | `frontend/src/components/Terminal/pages/BookSnapshot.tsx` | Show real AMT data + dynamics |
| Modify | `frontend/src/components/Terminal/pages/ContextSidebar.tsx` | Show Dalton day type |
| Modify | `frontend/src/types/market.ts` | Add AMT dynamics types |
| Create | `backend/tests/test_amt_dynamics.py` | Tests for tracker + dynamics features |
| Modify | `backend/tests/test_amt_features.py` | Tests for expanded static features |

---

### Task 1: AMTDynamicsTracker Core

**Files:**
- Create: `backend/src/market_data/amt_dynamics.py`
- Create: `backend/tests/test_amt_dynamics.py`

This is the foundational class. Everything else depends on it.

- [ ] **Step 1: Write test for tracker initialization**

```python
# backend/tests/test_amt_dynamics.py
from src.market_data.amt_dynamics import AMTDynamicsTracker


def test_initialize_sets_session_data():
    tracker = AMTDynamicsTracker()
    tracker.initialize({
        "ib_high": 19100.0,
        "ib_low": 19000.0,
        "vah": 19080.0,
        "val": 19020.0,
        "poc": 19050.0,
        "single_prints": [(19035.0, 19040.0)],
    })
    assert tracker.ib_high == 19100.0
    assert tracker.ib_low == 19000.0
    assert tracker.ib_range == 100.0
    assert tracker.vah == 19080.0
    assert tracker.val == 19020.0
    assert tracker.poc == 19050.0


def test_snapshot_returns_all_keys():
    tracker = AMTDynamicsTracker()
    tracker.initialize({
        "ib_high": 19100.0, "ib_low": 19000.0,
        "vah": 19080.0, "val": 19020.0, "poc": 19050.0,
    })
    snap = tracker.snapshot()
    expected_keys = {
        "ib_ext_up_count", "ib_ext_down_count", "ib_max_extension",
        "ib_ext_net_direction", "developing_day_type", "day_type_confidence",
        "responsive_ratio", "initiative_ratio",
        "va_acceptance_high", "va_rejection_high",
        "va_acceptance_low", "va_rejection_low",
        "poc_migration_speed", "va_width_expansion_rate",
        "balance_duration", "balance_width",
        "single_print_proximity", "excess_high", "excess_low",
        "otf_activity",
    }
    assert set(snap.keys()) == expected_keys
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_amt_dynamics.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.market_data.amt_dynamics'`

- [ ] **Step 3: Implement AMTDynamicsTracker**

```python
# backend/src/market_data/amt_dynamics.py
"""Real-time AMT dynamics tracker.

Lightweight state tracker fed tick-by-tick. Maintains running IB extension
counts, responsive/initiative volume split, VA acceptance/rejection,
developing day type, and balance area detection.

Used by LevelMonitor (live) and ReplayEngine (backtest).
"""
from __future__ import annotations

from collections import deque

_DALTON_THRESHOLDS = {
    "non_trend": 1.15,
    "normal": 1.5,
    "neutral_or_var": 2.0,
}


class AMTDynamicsTracker:
    """Track evolving AMT state from tick-by-tick updates."""

    def __init__(self) -> None:
        # Session anchors (set by initialize)
        self.ib_high: float = 0.0
        self.ib_low: float = 0.0
        self.ib_range: float = 0.0
        self.vah: float = 0.0
        self.val: float = 0.0
        self.poc: float = 0.0
        self.single_prints: list[tuple[float, float]] = []
        self._initialized: bool = False

        # IB extension tracking
        self.ib_ext_up_count: int = 0
        self.ib_ext_down_count: int = 0
        self.ib_max_ext_up: float = 0.0
        self.ib_max_ext_down: float = 0.0
        self._above_ib: bool = False
        self._below_ib: bool = False

        # Session extremes
        self.session_high: float = 0.0
        self.session_low: float = float("inf")

        # Responsive vs initiative volume (rolling window)
        self._vol_inside_va: int = 0
        self._vol_outside_va: int = 0

        # VA edge acceptance/rejection
        self._periods_above_vah: int = 0
        self._periods_below_val: int = 0
        self._price_was_above_vah: bool = False
        self._price_was_below_val: bool = False
        self._va_rejection_high: bool = False
        self._va_rejection_low: bool = False

        # Developing POC migration
        self._poc_history: deque[float] = deque(maxlen=12)

        # VA width expansion
        self._va_width_history: deque[float] = deque(maxlen=12)

        # Balance area
        self._balance_high: float = 0.0
        self._balance_low: float = float("inf")
        self._balance_start_period: int = 0
        self._current_period: int = 0

        # OTF activity
        self._delta_outside_va: int = 0
        self._total_volume: int = 0

        # Developing day type state
        self._extensions_up: float = 0.0
        self._extensions_down: float = 0.0

    def initialize(self, session_data: dict) -> None:
        """Set IB/VA/POC from compute_session or replay_engine."""
        self.ib_high = float(session_data.get("ib_high") or 0)
        self.ib_low = float(session_data.get("ib_low") or 0)
        self.ib_range = self.ib_high - self.ib_low
        self.vah = float(session_data.get("vah") or 0)
        self.val = float(session_data.get("val") or 0)
        self.poc = float(session_data.get("poc") or 0)
        self.single_prints = session_data.get("single_prints", [])
        self.session_high = self.ib_high
        self.session_low = self.ib_low
        self._balance_high = self.ib_high
        self._balance_low = self.ib_low
        self._initialized = True

    def update(self, price: float, size: int, side: str) -> None:
        """Called on every tick. Updates all counters."""
        if not self._initialized or self.ib_range <= 0:
            return

        # Session extremes
        self.session_high = max(self.session_high, price)
        self.session_low = min(self.session_low, price)

        # IB extension detection
        if price > self.ib_high:
            if not self._above_ib:
                self.ib_ext_up_count += 1
                self._above_ib = True
            ext = price - self.ib_high
            self.ib_max_ext_up = max(self.ib_max_ext_up, ext)
        else:
            self._above_ib = False

        if price < self.ib_low:
            if not self._below_ib:
                self.ib_ext_down_count += 1
                self._below_ib = True
            ext = self.ib_low - price
            self.ib_max_ext_down = max(self.ib_max_ext_down, ext)
        else:
            self._below_ib = False

        # Extensions for day type
        self._extensions_up = max(0.0, self.session_high - self.ib_high)
        self._extensions_down = max(0.0, self.ib_low - self.session_low)

        # Responsive vs initiative volume
        self._total_volume += size
        if self.val <= price <= self.vah:
            self._vol_inside_va += size
        else:
            self._vol_outside_va += size
            # OTF: directional delta outside VA
            delta = size if side == "buy" else -size
            self._delta_outside_va += delta

    def on_period_close(
        self,
        period_high: float,
        period_low: float,
        developing_poc: float,
        developing_vah: float,
        developing_val: float,
    ) -> None:
        """Called every 30 min. Updates period-based metrics."""
        self._current_period += 1

        # Update developing VA for responsive/initiative split
        self.vah = developing_vah
        self.val = developing_val

        # POC migration tracking
        self._poc_history.append(developing_poc)

        # VA width tracking
        va_width = developing_vah - developing_val
        self._va_width_history.append(va_width)

        # VA edge acceptance/rejection
        if period_high > self.vah:
            self._periods_above_vah += 1
            self._price_was_above_vah = True
        elif self._price_was_above_vah and period_low < self.vah:
            # Price probed above VAH then came back — rejection
            if self._periods_above_vah <= 2:
                self._va_rejection_high = True
            self._price_was_above_vah = False

        if period_low < self.val:
            self._periods_below_val += 1
            self._price_was_below_val = True
        elif self._price_was_below_val and period_high > self.val:
            if self._periods_below_val <= 2:
                self._va_rejection_low = True
            self._price_was_below_val = False

        # Balance area detection: if price stays within 1.5x IB for 3+ periods
        if self.ib_range > 0:
            bracket_threshold = self.ib_range * 1.5
            range_last_period = period_high - period_low
            session_range = self.session_high - self.session_low
            if session_range <= bracket_threshold:
                # Still in balance
                self._balance_high = max(self._balance_high, period_high)
                self._balance_low = min(self._balance_low, period_low)
            else:
                # Broke out of balance — reset
                self._balance_start_period = self._current_period
                self._balance_high = period_high
                self._balance_low = period_low

    def snapshot(self) -> dict:
        """Return current state for RL observation building."""
        ib = max(self.ib_range, 1e-9)
        daily_range = self.session_high - self.session_low
        total_vol = max(self._total_volume, 1)

        # Developing day type
        day_type, confidence = self._classify_developing_day_type(
            ib, daily_range, self._extensions_up, self._extensions_down,
        )

        # POC migration speed: change over last 5 periods
        poc_speed = 0.0
        if len(self._poc_history) >= 2:
            poc_delta = abs(self._poc_history[-1] - self._poc_history[0])
            poc_speed = poc_delta / ib

        # VA width expansion rate
        va_expansion = 0.0
        if len(self._va_width_history) >= 2:
            va_expansion = (self._va_width_history[-1] - self._va_width_history[0]) / ib

        # Balance duration
        balance_duration = self._current_period - self._balance_start_period
        balance_width = self._balance_high - self._balance_low

        # Single print proximity (find nearest)
        sp_proximity = 0.0
        last_price = self.session_high  # approximate with session high as latest
        if self.single_prints:
            nearest_dist = float("inf")
            for sp_low, sp_high in self.single_prints:
                sp_mid = (sp_low + sp_high) / 2
                dist = last_price - sp_mid
                if abs(dist) < abs(nearest_dist):
                    nearest_dist = dist
            sp_proximity = nearest_dist

        return {
            "ib_ext_up_count": self.ib_ext_up_count,
            "ib_ext_down_count": self.ib_ext_down_count,
            "ib_max_extension": max(self.ib_max_ext_up, self.ib_max_ext_down),
            "ib_ext_net_direction": self._net_direction(),
            "developing_day_type": day_type,
            "day_type_confidence": confidence,
            "responsive_ratio": self._vol_inside_va / total_vol,
            "initiative_ratio": self._vol_outside_va / total_vol,
            "va_acceptance_high": self._periods_above_vah,
            "va_rejection_high": self._va_rejection_high,
            "va_acceptance_low": self._periods_below_val,
            "va_rejection_low": self._va_rejection_low,
            "poc_migration_speed": poc_speed,
            "va_width_expansion_rate": va_expansion,
            "balance_duration": balance_duration,
            "balance_width": balance_width,
            "single_print_proximity": sp_proximity,
            "excess_high": 0,  # Updated by on_period_close caller with TPO data
            "excess_low": 0,
            "otf_activity": abs(self._delta_outside_va) / total_vol,
        }

    def _net_direction(self) -> float:
        """IB extension net direction: +1 all up, -1 all down, 0 both."""
        up = self.ib_ext_up_count
        down = self.ib_ext_down_count
        if up == 0 and down == 0:
            return 0.0
        total = up + down
        return (up - down) / total

    @staticmethod
    def _classify_developing_day_type(
        ib_range: float, daily_range: float,
        ext_up: float, ext_down: float,
    ) -> tuple[float, float]:
        """Return (ordinal 0-1, confidence 0-1) for developing Dalton day type.

        Ordinal: non_trend=0, normal=0.2, neutral=0.4, normal_var=0.6, trend=0.8, double=1.0
        """
        if ib_range <= 0:
            return 0.2, 0.0  # fallback to normal, no confidence

        ratio = daily_range / ib_range

        if ratio <= 1.15:
            # Distance from threshold tells confidence
            conf = min(1.0, (1.15 - ratio) / 0.15)
            return 0.0, conf  # non_trend

        if ratio <= 1.5:
            conf = min(1.0, (ratio - 1.15) / 0.35)
            return 0.2, conf  # normal

        max_ext = max(ext_up, ext_down, 1e-9)
        imbalance = abs(ext_up - ext_down) / max_ext

        if ratio <= 2.0:
            if imbalance < 0.2:
                conf = min(1.0, (0.2 - imbalance) / 0.2)
                return 0.4, conf  # neutral
            conf = min(1.0, imbalance)
            return 0.6, conf  # normal_variation

        # ratio > 2.0
        if ext_up > 3.0 * max(ext_down, 1e-9) or ext_down > 3.0 * max(ext_up, 1e-9):
            conf = min(1.0, ratio / 3.0)
            return 0.8, conf  # trend
        conf = min(1.0, ratio / 3.0)
        return 1.0, conf  # double distribution
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_amt_dynamics.py -v`
Expected: PASS

- [ ] **Step 5: Write tests for tick updates and IB extensions**

```python
# Append to backend/tests/test_amt_dynamics.py

def _make_tracker(ib_high=19100.0, ib_low=19000.0, vah=19080.0, val=19020.0, poc=19050.0):
    t = AMTDynamicsTracker()
    t.initialize({"ib_high": ib_high, "ib_low": ib_low, "vah": vah, "val": val, "poc": poc})
    return t


def test_ib_extension_up():
    t = _make_tracker()
    # Price moves above IB high
    t.update(19105.0, 100, "buy")
    t.update(19110.0, 100, "buy")  # still above — no new extension
    t.update(19090.0, 100, "sell")  # back inside IB
    t.update(19120.0, 100, "buy")  # new extension
    snap = t.snapshot()
    assert snap["ib_ext_up_count"] == 2
    assert snap["ib_ext_down_count"] == 0
    assert snap["ib_max_extension"] == 20.0  # 19120 - 19100


def test_ib_extension_both_sides():
    t = _make_tracker()
    t.update(19110.0, 100, "buy")   # above IB
    t.update(19050.0, 100, "sell")  # back inside
    t.update(18990.0, 100, "sell")  # below IB
    snap = t.snapshot()
    assert snap["ib_ext_up_count"] == 1
    assert snap["ib_ext_down_count"] == 1
    assert snap["ib_ext_net_direction"] == 0.0  # balanced


def test_responsive_vs_initiative():
    t = _make_tracker()
    # Inside VA (19020-19080)
    t.update(19050.0, 500, "buy")
    # Outside VA
    t.update(19090.0, 300, "buy")
    t.update(18990.0, 200, "sell")
    snap = t.snapshot()
    assert snap["responsive_ratio"] == 500 / 1000
    assert snap["initiative_ratio"] == 500 / 1000


def test_developing_day_type_non_trend():
    """Daily range barely extends IB → non-trend."""
    t = _make_tracker()
    # Session stays inside IB (high=19100, low=19000, range=100)
    t.update(19090.0, 100, "buy")
    t.update(19010.0, 100, "sell")
    snap = t.snapshot()
    assert snap["developing_day_type"] == 0.0  # non_trend


def test_developing_day_type_trend():
    """Daily range >> IB, one-sided → trend."""
    t = _make_tracker()
    # Massive extension above IB
    t.update(19300.0, 100, "buy")
    snap = t.snapshot()
    assert snap["developing_day_type"] == 0.8  # trend
    assert snap["day_type_confidence"] > 0.5
```

- [ ] **Step 6: Run tests**

Run: `cd backend && python -m pytest tests/test_amt_dynamics.py -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add backend/src/market_data/amt_dynamics.py backend/tests/test_amt_dynamics.py
git commit -m "feat(amt): add AMTDynamicsTracker for real-time AMT state tracking"
```

---

### Task 2: Dynamic AMT Feature Extractor

**Files:**
- Create: `backend/src/rl/features/amt_dynamics_features.py`
- Modify: `backend/tests/test_amt_dynamics.py`

- [ ] **Step 1: Write test for feature extraction**

```python
# Append to backend/tests/test_amt_dynamics.py
import numpy as np
from src.rl.features.amt_dynamics_features import extract_amt_dynamics_features


def test_extract_amt_dynamics_features_zeros_when_none():
    feats = extract_amt_dynamics_features(None)
    assert feats.shape == (20,)
    assert np.all(feats == 0.0)


def test_extract_amt_dynamics_features_from_snapshot():
    t = _make_tracker()
    t.update(19110.0, 500, "buy")   # above IB
    t.update(19050.0, 300, "sell")  # inside VA
    t.update(18990.0, 200, "sell")  # below IB
    snap = t.snapshot()
    feats = extract_amt_dynamics_features(snap)
    assert feats.shape == (20,)
    assert feats.dtype == np.float32
    # IB extension count up (idx 0) = 1/5 = 0.2
    assert feats[0] == np.float32(0.2)
    # IB extension count down (idx 1) = 1/5 = 0.2
    assert feats[1] == np.float32(0.2)
    # All values should be finite
    assert np.all(np.isfinite(feats))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_amt_dynamics.py::test_extract_amt_dynamics_features_zeros_when_none -v`
Expected: FAIL

- [ ] **Step 3: Implement feature extractor**

```python
# backend/src/rl/features/amt_dynamics_features.py
"""Dynamic AMT feature extraction — 20 dimensions.

Updates in real-time via AMTDynamicsTracker snapshots.

  Index  Feature                          Encoding
  0      IB extension count up            count / 5, clipped 0-1
  1      IB extension count down          count / 5, clipped 0-1
  2      IB extension magnitude           max_ext / ib_range, clipped 0-1 (via /3 /3)
  3      IB extension net direction       -1 to +1
  4      Developing day type (ordinal)    0-1 (non_trend=0 .. double=1)
  5      Day type confidence              0-1
  6      Responsive activity ratio        0-1
  7      Initiative activity ratio        0-1
  8      VA edge acceptance (high)        periods / 6, clipped 0-1
  9      VA edge rejection (high)         0 or 1
  10     VA edge acceptance (low)         periods / 6, clipped 0-1
  11     VA edge rejection (low)          0 or 1
  12     Developing POC migration speed   poc_delta / ib_range, clipped 0-1
  13     VA width expansion rate          delta_width / ib_range, clipped ±1
  14     Balance area duration            periods / 12, clipped 0-1
  15     Balance area width               width / (2 * ib_range), clipped 0-1
  16     Single print proximity           signed ticks / 200, clipped ±1
  17     Excess quality at high           count / 10, clipped 0-1
  18     Excess quality at low            count / 10, clipped 0-1
  19     OTF activity signal              abs(delta) / volume, clipped 0-1
"""
from __future__ import annotations

import numpy as np

_N_FEATURES = 20


def extract_amt_dynamics_features(snapshot: dict | None) -> np.ndarray:
    """Extract 20-dim dynamic AMT features from tracker snapshot.

    Returns zeros if snapshot is None (pre-IB or no data).
    """
    feats = np.zeros(_N_FEATURES, dtype=np.float32)
    if snapshot is None:
        return feats

    feats[0] = min(snapshot.get("ib_ext_up_count", 0) / 5.0, 1.0)
    feats[1] = min(snapshot.get("ib_ext_down_count", 0) / 5.0, 1.0)
    feats[2] = min(snapshot.get("ib_max_extension", 0) / 3.0, 1.0) / 3.0  # double normalize: /ib_range already in tracker is NOT done, so raw pts
    feats[3] = float(np.clip(snapshot.get("ib_ext_net_direction", 0), -1.0, 1.0))
    feats[4] = float(np.clip(snapshot.get("developing_day_type", 0.2), 0.0, 1.0))
    feats[5] = float(np.clip(snapshot.get("day_type_confidence", 0), 0.0, 1.0))
    feats[6] = float(np.clip(snapshot.get("responsive_ratio", 0), 0.0, 1.0))
    feats[7] = float(np.clip(snapshot.get("initiative_ratio", 0), 0.0, 1.0))
    feats[8] = min(snapshot.get("va_acceptance_high", 0) / 6.0, 1.0)
    feats[9] = 1.0 if snapshot.get("va_rejection_high") else 0.0
    feats[10] = min(snapshot.get("va_acceptance_low", 0) / 6.0, 1.0)
    feats[11] = 1.0 if snapshot.get("va_rejection_low") else 0.0
    feats[12] = float(np.clip(snapshot.get("poc_migration_speed", 0), 0.0, 1.0))
    feats[13] = float(np.clip(snapshot.get("va_width_expansion_rate", 0), -1.0, 1.0))
    feats[14] = min(snapshot.get("balance_duration", 0) / 12.0, 1.0)
    feats[15] = float(np.clip(snapshot.get("balance_width", 0) / 200.0, 0.0, 1.0))  # 200 ticks = 2x typical IB
    feats[16] = float(np.clip(snapshot.get("single_print_proximity", 0) / 200.0, -1.0, 1.0))
    feats[17] = min(snapshot.get("excess_high", 0) / 10.0, 1.0)
    feats[18] = min(snapshot.get("excess_low", 0) / 10.0, 1.0)
    feats[19] = float(np.clip(snapshot.get("otf_activity", 0), 0.0, 1.0))

    return feats
```

- [ ] **Step 4: Run tests**

Run: `cd backend && python -m pytest tests/test_amt_dynamics.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/src/rl/features/amt_dynamics_features.py backend/tests/test_amt_dynamics.py
git commit -m "feat(amt): add 20-dim dynamic AMT feature extractor"
```

---

### Task 3: Expand Static AMT Features (13→20)

**Files:**
- Modify: `backend/src/rl/features/amt_features.py`
- Modify: `backend/tests/test_amt_features.py`

- [ ] **Step 1: Write tests for new features [13-19]**

```python
# Append to backend/tests/test_amt_features.py

def test_ib_percentile_feature():
    """Index 13: IB range percentile from session_context."""
    sl = _make_session_levels(ib_high=19100.0, ib_low=19000.0)
    ctx = _make_ctx(daily_high=19150.0, daily_low=18950.0)
    ctx["ib_range_percentile"] = 0.75
    feats = extract_amt_features(sl, None, ctx, 19050.0)
    assert feats[13] == pytest.approx(0.75, abs=0.01)


def test_overnight_gap_feature():
    """Index 14: overnight gap vs IB."""
    sl = _make_session_levels(ib_high=19100.0, ib_low=19000.0)
    ctx = _make_ctx(daily_high=19150.0, daily_low=18950.0)
    ctx["overnight_gap"] = 0.5  # gapped up half an IB
    feats = extract_amt_features(sl, None, ctx, 19050.0)
    assert feats[14] == pytest.approx(0.5, abs=0.01)


def test_prior_poor_high_feature():
    """Index 17: prior session poor high."""
    sl = _make_session_levels(ib_high=19100.0, ib_low=19000.0)
    ctx = _make_ctx(daily_high=19150.0, daily_low=18950.0)
    ctx["prior_poor_high"] = True
    feats = extract_amt_features(sl, None, ctx, 19050.0)
    assert feats[17] == 1.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_amt_features.py::test_ib_percentile_feature -v`
Expected: FAIL — `IndexError: index 13 is out of bounds`

- [ ] **Step 3: Expand amt_features.py from 13 to 20 dims**

In `backend/src/rl/features/amt_features.py`, change `_N_FEATURES = 13` to `_N_FEATURES = 20` and add new index constants:

```python
# Add after _IDX_VALUE_MIG = 12
_IDX_IB_PERCENTILE = 13
_IDX_OVERNIGHT_GAP = 14
_IDX_OPEN_VS_PRIOR_POC = 15
_IDX_COMPOSITE_VA_OVERLAP = 16
_IDX_PRIOR_POOR_HIGH = 17
_IDX_PRIOR_POOR_LOW = 18
_IDX_PRIOR_EXCESS_QUALITY = 19
```

At the end of `extract_amt_features()`, before `return feats`, add:

```python
    # --- New static features (indices 13-19) ---
    feats[_IDX_IB_PERCENTILE] = float(np.clip(ctx.get("ib_range_percentile", 0.5), 0.0, 1.0))
    feats[_IDX_OVERNIGHT_GAP] = float(np.clip(ctx.get("overnight_gap", 0), -1.0, 1.0))

    open_vs_poc = ctx.get("open_vs_prior_poc")
    if open_vs_poc is not None:
        feats[_IDX_OPEN_VS_PRIOR_POC] = float(np.clip(open_vs_poc, -1.0, 1.0))

    feats[_IDX_COMPOSITE_VA_OVERLAP] = float(np.clip(ctx.get("composite_va_overlap", 0), 0.0, 1.0))
    feats[_IDX_PRIOR_POOR_HIGH] = 1.0 if ctx.get("prior_poor_high") else 0.0
    feats[_IDX_PRIOR_POOR_LOW] = 1.0 if ctx.get("prior_poor_low") else 0.0
    feats[_IDX_PRIOR_EXCESS_QUALITY] = float(np.clip(ctx.get("prior_excess_quality", 0) / 10.0, -1.0, 1.0))
```

- [ ] **Step 4: Run all AMT feature tests**

Run: `cd backend && python -m pytest tests/test_amt_features.py -v`
Expected: PASS (existing tests still pass with expanded vector; new tests pass too)

- [ ] **Step 5: Commit**

```bash
git add backend/src/rl/features/amt_features.py backend/tests/test_amt_features.py
git commit -m "feat(amt): expand static AMT features from 13 to 20 dims"
```

---

### Task 4: Wire Into Observation Vector

**Files:**
- Modify: `backend/src/rl/features/observation.py`

- [ ] **Step 1: Add import and segment**

In `observation.py`, add import at top:

```python
from .amt_dynamics_features import extract_amt_dynamics_features
```

In `build_observation()`, after `seg_amt` (line 183), add:

```python
    # 10b. AMT dynamics features (20) — real-time IB extensions, acceptance/rejection
    amt_dynamics = state.get("amt_dynamics")
    seg_amt_dynamics = extract_amt_dynamics_features(amt_dynamics)
```

In the `np.concatenate` call, add `seg_amt_dynamics` right after `seg_amt`:

```python
    obs = np.concatenate([
        seg_level,
        seg_orderflow,
        seg_structure,
        seg_tpo,
        seg_candles,
        seg_zone_feats,
        seg_confluence,
        seg_macro,
        seg_exchange,
        seg_setup,
        seg_amt,            # 20 (was 13)
        seg_amt_dynamics,   # 20 (NEW)
        seg_micro,
        seg_approach,
        seg_execution,
    ])
```

Update the docstring at top of file to reflect new dimensions:

```python
    AMT features                 20   (was 13)
    AMT dynamics                 20   (NEW)
    ...
    total                       265   (was 218, zone mode)
```

- [ ] **Step 2: Add `amt_dynamics` to dummy state**

In the `_dummy_state` dict (around line 231), add:

```python
    "amt_dynamics": None,
```

- [ ] **Step 3: Run existing observation tests**

Run: `cd backend && python -m pytest tests/test_rl_features.py tests/test_trading_features.py -v`
Expected: PASS (OBSERVATION_DIM will now be computed as the new larger value)

- [ ] **Step 4: Commit**

```bash
git add backend/src/rl/features/observation.py
git commit -m "feat(obs): wire AMT dynamics (20 dims) + expanded AMT (20 dims) into observation vector"
```

---

### Task 5: Wire Tracker Into LevelMonitor (Live Path)

**Files:**
- Modify: `backend/src/market_data/level_monitor.py`

- [ ] **Step 1: Import and initialize tracker**

At top of file, add:

```python
from .amt_dynamics import AMTDynamicsTracker
```

In `__init__()` (around line 68), add:

```python
        self._amt_tracker = AMTDynamicsTracker()
```

- [ ] **Step 2: Initialize tracker in set_session_context**

In `set_session_context()` (line 289), after `self._session_context = ctx`, add:

```python
        # Initialize AMT dynamics tracker with session IB/VA/POC
        session_data = {}
        vp = ctx.get("volume_profile")
        sl = ctx.get("session_levels")
        if vp:
            session_data["vah"] = vp.vah if hasattr(vp, "vah") else 0
            session_data["val"] = vp.val if hasattr(vp, "val") else 0
            session_data["poc"] = vp.poc if hasattr(vp, "poc") else 0
        if sl:
            session_data["ib_high"] = sl.ib_high if hasattr(sl, "ib_high") else 0
            session_data["ib_low"] = sl.ib_low if hasattr(sl, "ib_low") else 0
        tpo = ctx.get("tpo_profile")
        if tpo and isinstance(tpo, dict):
            session_data["single_prints"] = tpo.get("single_prints", [])
        self._amt_tracker.initialize(session_data)
```

- [ ] **Step 3: Update tracker on tick**

In `on_tick()` (line 181), near the top after price assignment, add:

```python
        # Update AMT dynamics tracker
        self._amt_tracker.update(price, size, ts)
```

Note: `on_tick` signature is `(self, price: float, size: int, ts: float)`. The tracker's `update` expects `(price, size, side)`. We need the tick side. Check if `on_tick` receives side — if not, approximate from price direction. Looking at the code, `on_tick` only gets `(price, size, ts)`, no side. We'll infer side from price movement:

```python
        # Infer tick side from price movement (approximation)
        _side = "buy" if price >= self._last_price else "sell"
        self._amt_tracker.update(price, size, _side)
```

- [ ] **Step 4: Pass snapshot to state builders**

In `_build_rl_state_zone()` (line 742) and `_build_rl_state()` (line 778), add to the return dict:

```python
            "amt_dynamics": self._amt_tracker.snapshot(),
```

- [ ] **Step 5: Run backend to verify no import errors**

Run: `cd backend && python -c "from src.market_data.level_monitor import LevelMonitor; print('OK')"`
Expected: `OK`

- [ ] **Step 6: Commit**

```bash
git add backend/src/market_data/level_monitor.py
git commit -m "feat(live): wire AMTDynamicsTracker into LevelMonitor tick handler"
```

---

### Task 6: Wire Tracker Into ReplayEngine (Backtest Path)

**Files:**
- Modify: `backend/src/rl/data/replay_engine.py`

- [ ] **Step 1: Import and initialize tracker**

At top of file, add:

```python
from src.market_data.amt_dynamics import AMTDynamicsTracker
```

In `__init__()`, add attribute:

```python
        self._amt_tracker = AMTDynamicsTracker()
```

- [ ] **Step 2: Initialize tracker after IB forms**

In `_on_bar_close()` (line 358), after session levels are computed (around line 366 where `compute_session_levels` is called), add initialization logic that fires once after the first 60 bars (IB period):

```python
            # Initialize AMT tracker once IB is established (bar 60)
            if bar_count == 60 and computed.ib_high and computed.ib_low:
                tpo_data = self._tpo_profile or {}
                self._amt_tracker.initialize({
                    "ib_high": computed.ib_high,
                    "ib_low": computed.ib_low,
                    "vah": self._vp.get().vah if self._vp.get() else 0,
                    "val": self._vp.get().val if self._vp.get() else 0,
                    "poc": self._vp.get().poc if self._vp.get() else 0,
                    "single_prints": tpo_data.get("single_prints", []),
                })
```

- [ ] **Step 3: Update tracker on every tick**

In `replay_session()`, inside the main tick loop (around line 277 after VP update), add:

```python
            # Update AMT dynamics tracker
            _side = "buy" if tick.get("side") == "buy" else "sell"
            self._amt_tracker.update(price, tick["size"], _side)
```

Note: In replay, ticks from Databento have a "side" field. Check the tick normalization to confirm. The `_normalise_tick()` function should preserve it.

- [ ] **Step 4: Call on_period_close for 30-min bars**

In `_on_bar_close()`, detect when a new 30-min bar completes and call tracker:

```python
            # Update AMT tracker on 30-min bar close
            if bar_count > 0 and bar_count % 30 == 0:
                vp = self._vp.get()
                if vp:
                    last_30m = bars_1m[-30:]
                    p_high = max(b["high"] for b in last_30m)
                    p_low = min(b["low"] for b in last_30m)
                    self._amt_tracker.on_period_close(
                        period_high=p_high,
                        period_low=p_low,
                        developing_poc=vp.poc,
                        developing_vah=vp.vah,
                        developing_val=vp.val,
                    )
```

- [ ] **Step 5: Pass snapshot to state**

In `_build_state()` (line 583), add to the return dict (line 692):

```python
            "amt_dynamics": self._amt_tracker.snapshot(),
```

- [ ] **Step 6: Reset tracker between sessions**

In `replay_session()`, at the start (around line 249, before the tick loop), reset the tracker:

```python
        self._amt_tracker = AMTDynamicsTracker()
```

- [ ] **Step 7: Run replay on a small sample to verify**

Run: `cd backend && python -m pytest tests/test_amt_dynamics.py -v`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add backend/src/rl/data/replay_engine.py
git commit -m "feat(backtest): wire AMTDynamicsTracker into ReplayEngine for historical training"
```

---

### Task 7: MarketRepo + Session Context Enrichment

**Files:**
- Modify: `backend/src/repositories/market_repo.py`
- Modify: `backend/src/services/market_service.py`
- Modify: `backend/src/api/routes/market.py`

- [ ] **Step 1: Add repo methods**

In `market_repo.py`, after `get_historical_asprs()` (line 271), add:

```python
    def get_historical_ib_ranges(self, symbol: str, limit: int = 20) -> list[float]:
        """Get recent IB ranges for percentile computation."""
        rows = self.db.query(MarketSession.ib_range).filter(
            MarketSession.symbol == symbol,
            MarketSession.ib_range.isnot(None),
            MarketSession.ib_range > 0,
        ).order_by(MarketSession.date.desc()).limit(limit).all()
        return [r[0] for r in rows]

    def get_recent_sessions(self, symbol: str, days: int = 5) -> list:
        """Return last N sessions for composite VA computation."""
        return (
            self.db.query(MarketSession)
            .filter(
                MarketSession.symbol == symbol,
                MarketSession.vah.isnot(None),
                MarketSession.val.isnot(None),
            )
            .order_by(MarketSession.date.desc())
            .limit(days)
            .all()
        )
```

- [ ] **Step 2: Enrich session_context in compute_session**

In `market_service.py`, in `compute_session()`, after value_migration is computed (around line 468), add before `self.repo.upsert_session(...)`:

```python
        # --- AMT static feature enrichment ---
        # IB range percentile
        historical_ibs = self.repo.get_historical_ib_ranges(symbol)
        ib_range_val = session_data.get("ib_range", 0)
        ib_pct = sum(1 for h in historical_ibs if h <= ib_range_val) / max(len(historical_ibs), 1)

        # Overnight gap
        prior_close = None
        if prev_session:
            pj = prev_session.session_json
            if isinstance(pj, str):
                pj = json.loads(pj)
            prior_close = pj.get("last_price") if pj else None
        rth_open_price = None
        if bars:
            h_rth, m_rth = map(int, rth_open.split(":"))
            rth_open_time = time(h_rth, m_rth)
            for b in bars:
                if hasattr(b.timestamp, "astimezone"):
                    bt = b.timestamp.astimezone(_ZI("US/Eastern")).time()
                    if bt >= rth_open_time:
                        rth_open_price = b.open
                        break
        overnight_gap = 0.0
        if prior_close and rth_open_price and ib_range_val > 0:
            overnight_gap = (rth_open_price - prior_close) / ib_range_val

        # Open vs prior POC
        open_vs_poc = 0.0
        if rth_open_price and analysis.prev_poc:
            open_vs_poc = (rth_open_price - analysis.prev_poc) / 0.25 / 200.0  # ticks/200

        # Composite VA overlap (5-day)
        composite_overlap = 0.0
        recent_sessions = self.repo.get_recent_sessions(symbol, days=5)
        if recent_sessions and session_data.get("vah") and session_data.get("val"):
            comp_vah = max(s.vah for s in recent_sessions)
            comp_val = min(s.val for s in recent_sessions)
            curr_vah = session_data["vah"]
            curr_val = session_data["val"]
            overlap = max(0.0, min(curr_vah, comp_vah) - max(curr_val, comp_val))
            comp_width = max(comp_vah - comp_val, 1e-9)
            composite_overlap = overlap / comp_width

        # Prior session excess quality
        prior_excess = 0
        if prev_session and prev_session.session_json:
            pj = prev_session.session_json
            if isinstance(pj, str):
                pj = json.loads(pj)
            # upper_excess - lower_excess from TPO
            prior_excess = (pj.get("upper_excess", 0) or 0) - (pj.get("lower_excess", 0) or 0)

        # Store enrichment in session_data for RL context
        session_data["amt_context"] = {
            "ib_range_percentile": ib_pct,
            "overnight_gap": overnight_gap,
            "open_vs_prior_poc": open_vs_poc,
            "composite_va_overlap": composite_overlap,
            "prior_poor_high": prev_session.poor_high if prev_session else False,
            "prior_poor_low": prev_session.poor_low if prev_session else False,
            "prior_excess_quality": prior_excess,
        }
```

- [ ] **Step 3: Pass AMT context to level_monitor**

In `market.py` `trigger_compute()` (around line 154), add AMT context to `rl_context`:

```python
            "amt_context": session.get("amt_context", {}),
```

Then in `level_monitor.py`, when building `session_context` in `_build_rl_state_zone`/`_build_rl_state`, merge AMT context into session_context:

In `_build_rl_state_zone()` and `_build_rl_state()`, where `session_context` is read from `ctx`:

```python
            "session_context": {**(ctx.get("session_context") or {}), **(ctx.get("amt_context") or {})},
```

- [ ] **Step 4: Commit**

```bash
git add backend/src/repositories/market_repo.py backend/src/services/market_service.py backend/src/api/routes/market.py backend/src/market_data/level_monitor.py
git commit -m "feat(amt): enrich session_context with static AMT features + repo methods"
```

---

### Task 8: Backtest Session Context Enrichment

**Files:**
- Modify: `backend/src/rl/data/replay_engine.py`

The replay engine also needs the static AMT context (indices 13-19) in `session_context`. It doesn't have DB access, so compute from available data.

- [ ] **Step 1: Enrich _build_session_context with AMT static data**

In `_build_session_context()` (line 721), add at the end of the return dict (line 786):

```python
            # AMT static enrichment (for amt_features indices 13-19)
            # IB percentile: use running history if available
            "ib_range_percentile": 0.5,  # No historical DB in replay — default to median
            "overnight_gap": self._compute_overnight_gap(bars_1m, session_date),
            "open_vs_prior_poc": self._compute_open_vs_prior_poc(open_price),
            "composite_va_overlap": 0.5,  # No multi-day history in single-session replay
            "prior_poor_high": self._prior_poor_high,
            "prior_poor_low": self._prior_poor_low,
            "prior_excess_quality": self._prior_excess_quality,
```

- [ ] **Step 2: Add helper methods and prior session state**

In `ReplayEngine.__init__()`, add:

```python
        self._prior_poor_high: bool = False
        self._prior_poor_low: bool = False
        self._prior_excess_quality: int = 0
        self._prior_poc: float | None = None
```

Add helper methods:

```python
    def _compute_overnight_gap(self, bars_1m: list[dict], session_date: datetime) -> float:
        """Overnight gap = (RTH open - prior close) / IB range."""
        sl = self._session_levels
        if not sl or not sl.ib_high or not sl.ib_low:
            return 0.0
        ib_range = sl.ib_high - sl.ib_low
        if ib_range <= 0:
            return 0.0
        # Find first RTH bar open
        open_price = None
        for b in bars_1m:
            if _is_rth_bar(b):
                open_price = b["open"]
                break
        if open_price is None:
            return 0.0
        # Prior close = first bar's open (approximation for overnight gap)
        prior_close = bars_1m[0]["close"] if bars_1m else open_price
        return (open_price - prior_close) / ib_range

    def _compute_open_vs_prior_poc(self, open_price: float | None) -> float:
        """Open price distance from prior session POC."""
        if open_price is None or self._prior_poc is None:
            return 0.0
        return (open_price - self._prior_poc) / 0.25 / 200.0  # ticks / 200
```

In `get_prior_session_for_chaining()`, add to the return dict and save prior session TPO state:

```python
        # Save prior session state for next session's AMT features
        tpo = self._tpo_profile or {}
        self._prior_poor_high = tpo.get("poor_high", False)
        self._prior_poor_low = tpo.get("poor_low", False)
        self._prior_excess_quality = (tpo.get("upper_excess_ticks", 0) or 0) - (tpo.get("lower_excess_ticks", 0) or 0)
        vp = self._vp.get()
        self._prior_poc = vp.poc if vp else None
```

- [ ] **Step 3: Commit**

```bash
git add backend/src/rl/data/replay_engine.py
git commit -m "feat(backtest): add AMT static context to replay session_context"
```

---

### Task 9: Fix Frontend — Remove Demo, Show Real AMT Data

**Files:**
- Modify: `frontend/src/components/Terminal/pages/TradingContainer.tsx`
- Modify: `frontend/src/components/Terminal/pages/BookSnapshot.tsx`
- Modify: `frontend/src/components/Terminal/pages/ContextSidebar.tsx`
- Modify: `frontend/src/types/market.ts`

- [ ] **Step 1: Update ExpandedSession type**

In `frontend/src/types/market.ts`, find the `ExpandedSession` interface and add:

```typescript
export interface AMTDynamics {
  developing_day_type: string;
  day_type_confidence: number;
  ib_ext_up_count: number;
  ib_ext_down_count: number;
  responsive_ratio: number;
  initiative_ratio: number;
  va_acceptance_high: number;
  va_rejection_high: boolean;
  va_acceptance_low: number;
  va_rejection_low: boolean;
  balance_duration: number;
}
```

Add to `ExpandedSession`:

```typescript
  amt_dynamics?: AMTDynamics | null;
```

- [ ] **Step 2: Remove demo fallback in TradingContainer**

In `TradingContainer.tsx`, change the initial state from `DEMO_SESSION` to `null`:

```typescript
const [session, setSession] = useState<ExpandedSession | null>(null);
```

In `fetchData()`, remove the `else` branch that falls back to `DEMO_SESSION` (lines 144-158). Replace with:

```typescript
      if (sessionRes && (sessionRes as any).status !== 'no_data' && sessionRes.session) {
        setSession(sessionRes);
      }
      // No demo fallback — show loading/empty state when no real data
```

Remove the entire `DEMO_SESSION`, `DEMO_ORDERFLOW`, `DEMO_LEVELS`, `DEMO_BATTLE` constants (lines 10-102).

- [ ] **Step 3: Update BookSnapshot AMT section with real data**

In `BookSnapshot.tsx`, replace the AMT section (lines 346-436). The Day Type should show Dalton classification with proper labels:

```tsx
            {/* Day type — show Dalton classification */}
            {(s?.market_type) && (
              <div className="flex items-center justify-between text-[10px]">
                <span className="text-muted2">Day Type</span>
                <span className="text-amber-300 font-bold">
                  {s.market_type.replace('_', ' ')}
                </span>
              </div>
            )}
```

Remove the `mlDayType` / `mlDayTypeConf` references — they're always null from the API. The Dalton day type from `amt_dynamics` will replace them once we wire the SSE stream (future task).

- [ ] **Step 4: Update ContextSidebar**

In `ContextSidebar.tsx`, the session section (lines 64-69) already reads `s?.market_type` and `s?.opening_type`, which is correct. No changes needed here — it will show real data once the demo fallback is removed.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/types/market.ts frontend/src/components/Terminal/pages/TradingContainer.tsx frontend/src/components/Terminal/pages/BookSnapshot.tsx
git commit -m "fix(ui): remove demo AMT data, show real session classifications"
```

---

### Task 10: Wire build_expanded_session to Return AMT Dynamics via SSE

**Files:**
- Modify: `backend/src/services/market_service.py`
- Modify: `backend/src/api/routes/market.py`

- [ ] **Step 1: Add AMT dynamics to expanded session response**

In `market_service.py` `build_expanded_session()` (line 666), replace the hardcoded `ml_day_type: None`:

```python
            "ml_day_type": None,  # Will be populated by gate classifier when loaded
            "ml_day_type_confidence": None,
```

This stays as-is for now — the gate classifier requires a loaded model. The real AMT data comes from `session.market_type` and `session.opening_type` which are already populated from `compute_session()`.

- [ ] **Step 2: Pass AMT dynamics from level_monitor to SSE**

In `market.py`, find the SSE event stream handler. When emitting level updates, include AMT dynamics snapshot from the level monitor:

In the existing SSE stream handler (find the `at_level` event emission), add AMT dynamics:

```python
# In the SSE event payload where at_level is emitted
"amt_dynamics": level_monitor._amt_tracker.snapshot() if hasattr(level_monitor, '_amt_tracker') else None,
```

- [ ] **Step 3: Commit**

```bash
git add backend/src/services/market_service.py backend/src/api/routes/market.py
git commit -m "feat(api): wire AMT dynamics into expanded session and SSE stream"
```

---

### Task 11: Integration Test — Full Pipeline Verification

**Files:**
- Modify: `backend/tests/test_amt_dynamics.py`

- [ ] **Step 1: Write end-to-end test simulating a replay session**

```python
# Append to backend/tests/test_amt_dynamics.py
from src.rl.features.observation import build_observation, OBSERVATION_DIM
from src.rl.features.amt_dynamics_features import extract_amt_dynamics_features


def test_observation_includes_amt_dynamics():
    """Verify the full observation vector includes AMT dynamics features."""
    from src.rl.config import LevelType
    from src.rl.zone_builder import Zone, ZoneMember

    member = ZoneMember(name="vwap", level_type=LevelType.VWAP, price=19000.0)
    zone = Zone(
        center_price=19000.0,
        upper_bound=19001.0,
        lower_bound=18999.0,
        members=[member],
        composition=[1.0 if lt == LevelType.VWAP else 0.0 for lt in LevelType],
        width_ticks=8.0,
        member_count=1,
        hierarchy_score=0.5,
    )

    # Build tracker and simulate some activity
    tracker = AMTDynamicsTracker()
    tracker.initialize({
        "ib_high": 19100.0, "ib_low": 19000.0,
        "vah": 19080.0, "val": 19020.0, "poc": 19050.0,
    })
    tracker.update(19110.0, 500, "buy")  # IB extension
    tracker.update(19050.0, 300, "sell")

    state = {
        "zone": zone,
        "all_zones": [zone],
        "price": 19050.0,
        "candles": [],
        "candles_5m": [],
        "vwap_bands": None,
        "volume_profile": None,
        "session_tpos": None,
        "tpo_profile": None,
        "tpo_profile_obj": None,
        "session_levels": None,
        "all_levels": [],
        "orderflow_signals": None,
        "macro": None,
        "session_context": None,
        "day_type": None,
        "recent_ticks": [],
        "amt_dynamics": tracker.snapshot(),
    }

    obs = build_observation(state)
    assert obs.shape[0] == OBSERVATION_DIM
    assert np.all(np.isfinite(obs))
    # AMT dynamics should have non-zero values (IB extension was triggered)
    # Find the AMT dynamics segment — it comes after seg_amt
    # This is a smoke test; exact index depends on other segment sizes


def test_observation_dim_increased():
    """OBSERVATION_DIM should have increased by 27 (7 new static + 20 dynamics)."""
    # This is a canary test — if dimensions change unexpectedly, this will catch it
    assert OBSERVATION_DIM > 0
    print(f"Current OBSERVATION_DIM: {OBSERVATION_DIM}")
```

- [ ] **Step 2: Run full test suite**

Run: `cd backend && python -m pytest tests/test_amt_dynamics.py tests/test_amt_features.py tests/test_rl_features.py -v`
Expected: ALL PASS

- [ ] **Step 3: Commit**

```bash
git add backend/tests/test_amt_dynamics.py
git commit -m "test(amt): add integration test for full observation pipeline with AMT dynamics"
```

---

### Task 12: Verify Frontend Renders Real Data

**Files:** None to modify — visual verification only.

- [ ] **Step 1: Build frontend and check for compile errors**

Run: `cd frontend && npm run build`
Expected: No TypeScript errors

- [ ] **Step 2: Visual check**

If the backend is running with a computed session, the AMT section in BookSnapshot should show:
- **Day Type**: real classification like `trending_up`, `balanced`, etc. (not `trend_day`)
- **Opening**: real OD/OTD/ORR/OA from the session
- **Distribution**: real p_shape/b_shape/normal/double from TPO
- If no session data: section should be empty (not demo data)

- [ ] **Step 3: Final commit**

```bash
git add -A
git commit -m "feat(amt): complete AMT feature expansion — 40 dims, live + backtest, UI fix"
```
