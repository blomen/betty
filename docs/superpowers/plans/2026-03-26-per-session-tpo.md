# Per-Session TPO Profiles Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the single composite TPO feature vector (13 features) with per-session TPO profiles (Tokyo/London/NY) producing 26 features for the RL observation vector.

**Architecture:** Add `SessionTPO` and `SessionTPOSet` dataclasses to `tpo.py`. New `compute_session_tpos()` splits bars by CET session boundaries, builds independent TPO profiles per session, and computes POC migration deltas. A new feature extractor produces 26 features (8 per session + 2 migrations) replacing the old 13. The composite TPO profile stays for frontend/setup detectors.

**Tech Stack:** Python 3.10+, NumPy, dataclasses, zoneinfo (CET timezone)

---

### Task 1: Add SessionTPO dataclasses and compute_session_tpos() to tpo.py

**Files:**
- Modify: `backend/src/market_data/tpo.py`
- Test: `backend/tests/test_rl_tpo_extensions.py`

- [ ] **Step 1: Write failing tests for compute_session_tpos**

Add to `backend/tests/test_rl_tpo_extensions.py`:

```python
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from src.market_data.tpo import (
    SessionTPO,
    SessionTPOSet,
    compute_session_tpos,
)

CET = ZoneInfo("Europe/Stockholm")


def _bar_30m(ts_hour_cet: int, ts_min_cet: int, high: float, low: float) -> dict:
    """Create a 30m bar with a CET timestamp."""
    ts = datetime(2026, 3, 25, ts_hour_cet, ts_min_cet, tzinfo=CET).astimezone(timezone.utc)
    return {"ts": ts, "high": high, "low": low, "open": low, "close": high, "volume": 100}


class TestComputeSessionTpos:
    def test_empty_bars_returns_all_none(self):
        result = compute_session_tpos([], tick_size=0.25)
        assert result.tokyo is None
        assert result.london is None
        assert result.ny is None
        assert result.poc_migration_tokyo_london == 0.0
        assert result.poc_migration_london_ny == 0.0

    def test_tokyo_only_session(self):
        """Bars only in Tokyo window (00:00-08:00 CET) -> london/ny are None."""
        bars = [
            _bar_30m(0, 0, 19810.0, 19800.0),
            _bar_30m(0, 30, 19815.0, 19805.0),
            _bar_30m(1, 0, 19820.0, 19810.0),
            _bar_30m(1, 30, 19825.0, 19815.0),
        ]
        result = compute_session_tpos(bars, tick_size=0.25)
        assert result.tokyo is not None
        assert result.tokyo.session == "tokyo"
        assert result.tokyo.poc > 0
        assert result.tokyo.vah >= result.tokyo.val
        assert result.london is None
        assert result.ny is None

    def test_all_three_sessions(self):
        """Bars across all sessions produce three profiles + migrations."""
        bars = [
            # Tokyo: 00:00-08:00
            _bar_30m(0, 0, 19810.0, 19800.0),
            _bar_30m(0, 30, 19815.0, 19805.0),
            _bar_30m(1, 0, 19812.0, 19802.0),
            # London: 08:00-15:30
            _bar_30m(8, 0, 19850.0, 19840.0),
            _bar_30m(8, 30, 19855.0, 19845.0),
            _bar_30m(9, 0, 19852.0, 19842.0),
            # NY: 15:30-22:00
            _bar_30m(15, 30, 19890.0, 19880.0),
            _bar_30m(16, 0, 19895.0, 19885.0),
            _bar_30m(16, 30, 19892.0, 19882.0),
        ]
        result = compute_session_tpos(bars, tick_size=0.25)
        assert result.tokyo is not None
        assert result.london is not None
        assert result.ny is not None
        # POC migration: london POC should be higher than tokyo POC
        assert result.poc_migration_tokyo_london > 0
        # NY POC should be higher than london POC
        assert result.poc_migration_london_ny > 0

    def test_letters_restart_at_A_per_session(self):
        """Each session's TPO letters start at A, not continuing from prior session."""
        bars = [
            _bar_30m(0, 0, 19810.0, 19800.0),   # Tokyo A
            _bar_30m(0, 30, 19815.0, 19805.0),   # Tokyo B
            _bar_30m(8, 0, 19850.0, 19840.0),    # London A (not C)
            _bar_30m(8, 30, 19855.0, 19845.0),   # London B (not D)
        ]
        result = compute_session_tpos(bars, tick_size=0.25)
        # Verify by checking internal profile has letter A at first price
        tokyo_profile = result.tokyo
        london_profile = result.london
        assert tokyo_profile is not None
        assert london_profile is not None
        # Both sessions should have shape (computed from their independent profiles)
        assert tokyo_profile.shape in ("p-shape", "b-shape", "d-shape", "balanced", "B-shape")
        assert london_profile.shape in ("p-shape", "b-shape", "d-shape", "balanced", "B-shape")

    def test_ib_valid_false_for_low_volume_tokyo(self):
        """Tokyo IB with very narrow bars -> ib_valid should be False."""
        # Two IB bars touching only 1 price level each (< MIN_IB_TPO_COUNT)
        bars = [
            _bar_30m(0, 0, 19800.0, 19800.0),   # single price
            _bar_30m(0, 30, 19800.0, 19800.0),   # single price
        ]
        result = compute_session_tpos(bars, tick_size=0.25)
        assert result.tokyo is not None
        assert result.tokyo.ib_valid is False

    def test_ib_valid_true_for_normal_session(self):
        """London with normal IB range -> ib_valid should be True."""
        bars = [
            _bar_30m(8, 0, 19860.0, 19840.0),    # 80 ticks wide
            _bar_30m(8, 30, 19865.0, 19845.0),   # 80 ticks wide
            _bar_30m(9, 0, 19855.0, 19850.0),
        ]
        result = compute_session_tpos(bars, tick_size=0.25)
        assert result.london is not None
        assert result.london.ib_valid is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_rl_tpo_extensions.py::TestComputeSessionTpos -v`
Expected: FAIL with `ImportError: cannot import name 'SessionTPO'`

- [ ] **Step 3: Implement SessionTPO, SessionTPOSet, and compute_session_tpos**

Add to `backend/src/market_data/tpo.py` after the existing `TPOProfile` dataclass:

```python
from zoneinfo import ZoneInfo
from datetime import time as _time

_CET = ZoneInfo("Europe/Stockholm")

# Non-overlapping session boundaries (CET)
_SESSION_BOUNDS = {
    "tokyo":  (_time(0, 0), _time(8, 0)),
    "london": (_time(8, 0), _time(15, 30)),
    "ny":     (_time(15, 30), _time(22, 0)),
}

# Minimum unique price levels in IB bars for IB to be considered valid
MIN_IB_TPO_COUNT = 4


@dataclass
class SessionTPO:
    """Lightweight per-session TPO profile for RL features."""
    session: str       # "tokyo" | "london" | "ny"
    poc: float
    vah: float
    val: float
    shape: str         # "p-shape" | "b-shape" | "d-shape" | "balanced" | "B-shape"
    ib_high: float
    ib_low: float
    ib_valid: bool     # False if IB bars have < MIN_IB_TPO_COUNT price levels
    poor_high: bool
    poor_low: bool


@dataclass
class SessionTPOSet:
    """Container for per-session TPO profiles + cross-session features."""
    tokyo: SessionTPO | None
    london: SessionTPO | None
    ny: SessionTPO | None
    poc_migration_tokyo_london: float  # (london.poc - tokyo.poc) / tick_size
    poc_migration_london_ny: float     # (ny.poc - london.poc) / tick_size


def _split_bars_by_session(
    bars_30m: list[dict],
) -> dict[str, list[dict]]:
    """Split 30m bars into session slices by CET time.

    Each bar must have a "ts" key (datetime, UTC or timezone-aware).
    Returns {"tokyo": [...], "london": [...], "ny": [...]}.
    """
    result: dict[str, list[dict]] = {"tokyo": [], "london": [], "ny": []}
    for bar in bars_30m:
        bar_ts = bar["ts"]
        if bar_ts.tzinfo is None:
            from datetime import timezone as _tz
            bar_ts = bar_ts.replace(tzinfo=_tz.utc)
        bar_cet = bar_ts.astimezone(_CET)
        bar_time = bar_cet.time()
        for session_name, (start, end) in _SESSION_BOUNDS.items():
            if start <= bar_time < end:
                result[session_name].append(bar)
                break
    return result


def _build_session_tpo(
    session_name: str,
    bars_30m: list[dict],
    tick_size: float,
) -> SessionTPO | None:
    """Build a SessionTPO from a slice of 30m bars for one session.

    Returns None if bars_30m is empty.
    """
    if not bars_30m:
        return None

    profile = compute_tpo_profile(bars_30m, tick_size=tick_size)
    shape = classify_tpo_shape(profile)

    # IB validity: check if first 2 bars touch enough price levels
    ib_bars = bars_30m[:2]
    ib_prices: set[float] = set()
    for bar in ib_bars:
        low_tick = round(bar["low"] / tick_size) * tick_size
        high_tick = round(bar["high"] / tick_size) * tick_size
        price = low_tick
        while price <= high_tick + tick_size / 2:
            ib_prices.add(round(price / tick_size) * tick_size)
            price += tick_size
    ib_valid = len(ib_prices) >= MIN_IB_TPO_COUNT

    return SessionTPO(
        session=session_name,
        poc=profile.poc,
        vah=profile.vah,
        val=profile.val,
        shape=shape,
        ib_high=profile.ib_high,
        ib_low=profile.ib_low,
        ib_valid=ib_valid,
        poor_high=profile.poor_high,
        poor_low=profile.poor_low,
    )


def compute_session_tpos(
    bars_30m: list[dict],
    tick_size: float = 0.25,
) -> SessionTPOSet:
    """Build per-session TPO profiles from 30m bars with timestamps.

    Each bar must have a "ts" key (datetime). Bars are split by CET time
    into Tokyo (00:00-08:00), London (08:00-15:30), NY (15:30-22:00).
    Letters restart at A for each session (slices are re-indexed from 0).
    """
    if not bars_30m:
        return SessionTPOSet(
            tokyo=None, london=None, ny=None,
            poc_migration_tokyo_london=0.0,
            poc_migration_london_ny=0.0,
        )

    slices = _split_bars_by_session(bars_30m)

    tokyo = _build_session_tpo("tokyo", slices["tokyo"], tick_size)
    london = _build_session_tpo("london", slices["london"], tick_size)
    ny = _build_session_tpo("ny", slices["ny"], tick_size)

    # POC migration deltas (in ticks)
    migration_tl = 0.0
    if tokyo and london and tokyo.poc and london.poc:
        migration_tl = (london.poc - tokyo.poc) / tick_size
    migration_ln = 0.0
    if london and ny and london.poc and ny.poc:
        migration_ln = (ny.poc - london.poc) / tick_size

    return SessionTPOSet(
        tokyo=tokyo, london=london, ny=ny,
        poc_migration_tokyo_london=migration_tl,
        poc_migration_london_ny=migration_ln,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_rl_tpo_extensions.py::TestComputeSessionTpos -v`
Expected: All 7 tests PASS

- [ ] **Step 5: Commit**

```bash
cd backend
git add src/market_data/tpo.py tests/test_rl_tpo_extensions.py
git commit -m "feat(tpo): add per-session TPO profiles (Tokyo/London/NY)"
```

---

### Task 2: Replace TPO feature extractor with per-session version

**Files:**
- Modify: `backend/src/rl/features/tpo_features.py`
- Test: `backend/tests/test_rl_tpo_extensions.py`

- [ ] **Step 1: Write failing tests for extract_session_tpo_features**

Add to `backend/tests/test_rl_tpo_extensions.py`:

```python
import numpy as np
from src.rl.features.tpo_features import extract_session_tpo_features
from src.market_data.tpo import SessionTPO, SessionTPOSet


class TestExtractSessionTpoFeatures:
    def test_none_returns_26_zeros(self):
        result = extract_session_tpo_features(None, current_price=19850.0)
        assert result.shape == (26,)
        assert np.all(result == 0.0)

    def test_all_sessions_populated(self):
        tpo_set = SessionTPOSet(
            tokyo=SessionTPO("tokyo", poc=19800.0, vah=19820.0, val=19780.0,
                             shape="balanced", ib_high=19810.0, ib_low=19795.0,
                             ib_valid=False, poor_high=False, poor_low=True),
            london=SessionTPO("london", poc=19850.0, vah=19870.0, val=19830.0,
                              shape="p-shape", ib_high=19860.0, ib_low=19840.0,
                              ib_valid=True, poor_high=True, poor_low=False),
            ny=SessionTPO("ny", poc=19890.0, vah=19910.0, val=19870.0,
                          shape="b-shape", ib_high=19900.0, ib_low=19880.0,
                          ib_valid=True, poor_high=False, poor_low=False),
            poc_migration_tokyo_london=200.0,  # 50 points / 0.25 tick
            poc_migration_london_ny=160.0,
        )
        result = extract_session_tpo_features(tpo_set, current_price=19850.0)
        assert result.shape == (26,)
        assert result.dtype == np.float32

        # Tokyo IB features should be zeroed (ib_valid=False)
        assert result[4] == 0.0  # ib_range
        assert result[5] == 0.0  # price_vs_ib_mid

        # London shape should be +1 (p-shape)
        assert result[8 + 3] == 1.0

        # NY shape should be -1 (b-shape -> maps to d? no, b maps to... check)
        # b-shape -> ordinal not in p/d -> 0 (balanced)
        # Wait: spec says p=+1, d=-1, balanced=0. b-shape is NOT in the ordinal map.
        # b-shape is bearish bias which maps to... let's check the spec again.
        # The spec says shape ordinal: p=+1, d=-1, balanced=0
        # b-shape (concentration at bottom) should map differently.
        # Actually the user said: "P=bullish bias (+1), D=bearish bias (-1), B=balanced (0)"
        # So b-shape = 0 (balanced bucket)

        # Migration features
        assert result[24] != 0.0  # poc_migration_tokyo_london
        assert result[25] != 0.0  # poc_migration_london_ny

    def test_partial_sessions_zeros_for_missing(self):
        """Only tokyo populated -> london/ny features should be zeros."""
        tpo_set = SessionTPOSet(
            tokyo=SessionTPO("tokyo", poc=19800.0, vah=19820.0, val=19780.0,
                             shape="p-shape", ib_high=19810.0, ib_low=19795.0,
                             ib_valid=True, poor_high=False, poor_low=False),
            london=None,
            ny=None,
            poc_migration_tokyo_london=0.0,
            poc_migration_london_ny=0.0,
        )
        result = extract_session_tpo_features(tpo_set, current_price=19800.0)
        assert result.shape == (26,)
        # Tokyo features should be non-zero
        assert not np.all(result[0:8] == 0.0)
        # London and NY features should be all zeros
        assert np.all(result[8:16] == 0.0)
        assert np.all(result[16:24] == 0.0)

    def test_price_position_in_va_within(self):
        """Price at midpoint of VA -> price_position_in_va ≈ 0.0."""
        tpo_set = SessionTPOSet(
            tokyo=SessionTPO("tokyo", poc=19800.0, vah=19820.0, val=19780.0,
                             shape="balanced", ib_high=19810.0, ib_low=19790.0,
                             ib_valid=True, poor_high=False, poor_low=False),
            london=None, ny=None,
            poc_migration_tokyo_london=0.0, poc_migration_london_ny=0.0,
        )
        result = extract_session_tpo_features(tpo_set, current_price=19800.0)
        # price=19800, val=19780, vah=19820 -> (19800-19780)/(19820-19780) - 0.5 = 0.0
        assert abs(result[7]) < 0.01

    def test_price_position_above_va(self):
        """Price above VAH -> price_position_in_va > 0."""
        tpo_set = SessionTPOSet(
            tokyo=SessionTPO("tokyo", poc=19800.0, vah=19820.0, val=19780.0,
                             shape="balanced", ib_high=19810.0, ib_low=19790.0,
                             ib_valid=True, poor_high=False, poor_low=False),
            london=None, ny=None,
            poc_migration_tokyo_london=0.0, poc_migration_london_ny=0.0,
        )
        result = extract_session_tpo_features(tpo_set, current_price=19840.0)
        # (19840-19820) / (19820-19780) = 0.5
        assert result[7] > 0.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_rl_tpo_extensions.py::TestExtractSessionTpoFeatures -v`
Expected: FAIL with `ImportError: cannot import name 'extract_session_tpo_features'`

- [ ] **Step 3: Implement extract_session_tpo_features**

Replace the contents of `backend/src/rl/features/tpo_features.py`:

```python
"""Per-session TPO feature extraction for RL observation vector.

Replaces the old 13-feature composite TPO extractor with 26 features:
8 per session (Tokyo/London/NY) + 2 POC migration deltas.
"""
from __future__ import annotations

import numpy as np

from ..config import TICK_SIZE
from ...market_data.tpo import SessionTPO, SessionTPOSet

_FEATURES_PER_SESSION = 8
_N_SESSIONS = 3
_N_MIGRATION = 2
_N_FEATURES = _FEATURES_PER_SESSION * _N_SESSIONS + _N_MIGRATION  # 26

# Shape ordinal: p-shape = bullish (+1), d-shape = bearish (-1), all else = 0
_SHAPE_ORDINAL = {"p-shape": 1.0, "d-shape": -1.0}


def _extract_single_session(
    session: SessionTPO | None,
    current_price: float,
) -> np.ndarray:
    """Extract 8 features from a single session TPO profile."""
    out = np.zeros(_FEATURES_PER_SESSION, dtype=np.float32)
    if session is None:
        return out

    poc, vah, val = session.poc, session.vah, session.val
    va_width = vah - val

    # 0: price_vs_poc (ticks, normalised to ~[-1, 1])
    out[0] = np.clip((current_price - poc) / TICK_SIZE / 200.0, -1.0, 1.0)
    # 1: price_vs_vah
    out[1] = np.clip((current_price - vah) / TICK_SIZE / 200.0, -1.0, 1.0)
    # 2: price_vs_val
    out[2] = np.clip((current_price - val) / TICK_SIZE / 200.0, -1.0, 1.0)
    # 3: shape ordinal
    out[3] = _SHAPE_ORDINAL.get(session.shape, 0.0)
    # 4: ib_range (zeroed if not valid)
    if session.ib_valid:
        out[4] = np.clip((session.ib_high - session.ib_low) / TICK_SIZE / 200.0, 0.0, 1.0)
        # 5: price_vs_ib_mid
        ib_mid = (session.ib_high + session.ib_low) / 2.0
        out[5] = np.clip((current_price - ib_mid) / TICK_SIZE / 200.0, -1.0, 1.0)
    # 6: poor_signal
    out[6] = float(session.poor_high) - float(session.poor_low)
    # 7: price_position_in_va (continuous)
    if va_width > 0:
        if current_price > vah:
            out[7] = (current_price - vah) / va_width
        elif current_price < val:
            out[7] = (current_price - val) / va_width
        else:
            out[7] = (current_price - val) / va_width - 0.5

    return out


def extract_session_tpo_features(
    session_tpos: SessionTPOSet | None,
    current_price: float,
) -> np.ndarray:
    """Extract 26 features from per-session TPO profiles.

    Feature layout:
      0-7:   Tokyo  (price_vs_poc, price_vs_vah, price_vs_val, shape,
                      ib_range, price_vs_ib_mid, poor_signal, price_position_in_va)
      8-15:  London (same 8)
      16-23: NY     (same 8)
      24:    poc_migration_tokyo_london (ticks / 200)
      25:    poc_migration_london_ny    (ticks / 200)

    Returns zeros(26) if session_tpos is None.
    """
    if session_tpos is None:
        return np.zeros(_N_FEATURES, dtype=np.float32)

    tokyo_feats = _extract_single_session(session_tpos.tokyo, current_price)
    london_feats = _extract_single_session(session_tpos.london, current_price)
    ny_feats = _extract_single_session(session_tpos.ny, current_price)

    migrations = np.array([
        np.clip(session_tpos.poc_migration_tokyo_london / 200.0, -1.0, 1.0),
        np.clip(session_tpos.poc_migration_london_ny / 200.0, -1.0, 1.0),
    ], dtype=np.float32)

    return np.concatenate([tokyo_feats, london_feats, ny_feats, migrations])


# Keep backward-compatible alias so any remaining callers don't break at import
def extract_tpo_features(
    tpo_profile: dict | None,
    current_price: float,
    bars_30m: list[dict] | None = None,
) -> np.ndarray:
    """Deprecated: returns zeros(26). Use extract_session_tpo_features instead."""
    return np.zeros(_N_FEATURES, dtype=np.float32)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_rl_tpo_extensions.py::TestExtractSessionTpoFeatures -v`
Expected: All 5 tests PASS

- [ ] **Step 5: Commit**

```bash
cd backend
git add src/rl/features/tpo_features.py tests/test_rl_tpo_extensions.py
git commit -m "feat(rl): per-session TPO feature extractor (26 features)"
```

---

### Task 3: Update observation.py to use per-session TPO features

**Files:**
- Modify: `backend/src/rl/features/observation.py`
- Test: `backend/tests/test_rl_tpo_extensions.py`

- [ ] **Step 1: Write failing test for new observation dimension**

Add to `backend/tests/test_rl_tpo_extensions.py`:

```python
from src.rl.features.observation import build_observation, OBSERVATION_DIM
from src.rl.config import LevelType


class TestObservationDimension:
    def test_observation_dim_is_159(self):
        """After per-session TPO: 146 - 13 + 26 = 159."""
        assert OBSERVATION_DIM == 159

    def test_build_observation_returns_159(self):
        state = {
            "level_type": LevelType.VWAP,
            "price": 19000.0,
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
        }
        obs = build_observation(state)
        assert obs.shape == (159,)
        assert obs.dtype == np.float32
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_rl_tpo_extensions.py::TestObservationDimension -v`
Expected: FAIL — `OBSERVATION_DIM` is still 146 (or 13 if old `extract_tpo_features` returns 26 zeros but observation.py still calls the old function)

- [ ] **Step 3: Update observation.py**

Edit `backend/src/rl/features/observation.py`:

1. Change the import:
```python
# Old:
from .tpo_features import extract_tpo_features
# New:
from .tpo_features import extract_session_tpo_features
```

2. Update the docstring segment sizes:
```python
# Old:
#    tpo                  13
#    ---
#    total               146
# New:
#    tpo (per-session)    26
#    ---
#    total               159
```

3. Change the TPO segment in `build_observation()`:
```python
    # Old:
    # 4. TPO (13)
    seg_tpo = extract_tpo_features(tpo_profile, price)

    # New:
    # 4. TPO per-session (26)
    session_tpos = state.get("session_tpos")
    seg_tpo = extract_session_tpo_features(session_tpos, price)
```

4. Update the inline comment in `np.concatenate`:
```python
        seg_tpo,          # 26 (was 13)
```

5. Add `"session_tpos": None` to `_dummy_state` dict (keep `"tpo_profile"` for backward compat with setup detectors):
```python
_dummy_state: dict = {
    ...
    "session_tpos": None,
    "tpo_profile": None,
    "tpo_profile_obj": None,
    ...
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_rl_tpo_extensions.py::TestObservationDimension -v`
Expected: PASS — `OBSERVATION_DIM` is now 159

- [ ] **Step 5: Commit**

```bash
cd backend
git add src/rl/features/observation.py tests/test_rl_tpo_extensions.py
git commit -m "feat(rl): observation vector 146->159 with per-session TPO"
```

---

### Task 4: Update replay_engine.py to build per-session TPO state

**Files:**
- Modify: `backend/src/rl/data/replay_engine.py`

- [ ] **Step 1: Add import for compute_session_tpos**

At the top of `replay_engine.py`, alongside the existing import:

```python
# Old:
from ...market_data.tpo import build_full_tpo_profile
# New:
from ...market_data.tpo import build_full_tpo_profile, compute_session_tpos
```

- [ ] **Step 2: Update _build_state to compute per-session TPOs**

In the `_build_state` method, after the existing composite TPO computation block (lines ~534-569), add the per-session computation. The composite stays for setup detectors:

```python
        # TPO profile from 30m bars
        tpo_profile_dict: dict | None = None
        tpo_profile_obj = None  # TPOProfile object for setup detection
        session_tpos = None     # Per-session TPO for RL features
        if bars_30m:
            profile = build_full_tpo_profile(bars_30m, tick_size=TICK_SIZE)
            tpo_profile_obj = profile  # Keep object for setup detection

            # --- Composite dict stays for backward compat (setup detectors) ---
            rotation_count = 0
            if len(bars_30m) >= 2:
                for j in range(1, len(bars_30m)):
                    prev_dir = bars_30m[j - 1]["close"] - bars_30m[j - 1]["open"]
                    curr_dir = bars_30m[j]["close"] - bars_30m[j]["open"]
                    if (prev_dir > 0 and curr_dir < 0) or (prev_dir < 0 and curr_dir > 0):
                        rotation_count += 1

            tpo_profile_dict = {
                "poc": profile.poc,
                "vah": profile.vah,
                "val": profile.val,
                "shape": profile.profile_shape,
                "rotation_factor": profile.rotation_factor,
                "rotation_count": rotation_count,
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

            # --- Per-session TPO (for RL observation) ---
            # bars_30m from CandleAggregator have "ts" field (datetime)
            session_tpos = compute_session_tpos(bars_30m, tick_size=TICK_SIZE)
```

Then add `"session_tpos": session_tpos` to the returned state dict:

```python
        return {
            ...
            "tpo_profile": tpo_profile_dict,
            "tpo_profile_obj": tpo_profile_obj,
            "session_tpos": session_tpos,
            "session_levels": self._session_levels,
            ...
        }
```

- [ ] **Step 3: Run existing replay tests to verify nothing breaks**

Run: `cd backend && python -m pytest tests/ -k "replay" -v --timeout=30`
Expected: All existing tests PASS

- [ ] **Step 4: Commit**

```bash
cd backend
git add src/rl/data/replay_engine.py
git commit -m "feat(rl): compute per-session TPOs in replay engine"
```

---

### Task 5: Update market_service.py to compute and store per-session TPOs

**Files:**
- Modify: `backend/src/services/market_service.py`

- [ ] **Step 1: Add import**

At the top, alongside existing tpo imports:

```python
# Old:
from ..market_data.tpo import build_full_tpo_profile, aggregate_bars_30m
# New:
from ..market_data.tpo import build_full_tpo_profile, aggregate_bars_30m, compute_session_tpos
```

- [ ] **Step 2: Update compute_session to include session_tpos in session_json**

In `compute_session()`, after line 304 (`tpo = build_full_tpo_profile(bars_30m)`), add:

```python
        tpo = build_full_tpo_profile(bars_30m)

        # Per-session TPO profiles
        # bars from _aggregate_bars_30m don't have timestamps, but bars (BarData) do.
        # Build timestamped 30m bars from the 1m BarData objects.
        bars_30m_ts = []
        chunk = []
        for b in bars:
            chunk.append(b)
            if len(chunk) == 30:
                bars_30m_ts.append({
                    "ts": chunk[0].timestamp,
                    "high": max(c.high for c in chunk),
                    "low": min(c.low for c in chunk),
                    "open": chunk[0].open,
                    "close": chunk[-1].close,
                    "volume": sum(c.volume for c in chunk),
                })
                chunk = []
        session_tpo_set = compute_session_tpos(bars_30m_ts, tick_size=tick_size)
```

Then embed the per-session data in `session_data` before persisting:

```python
        # Embed per-session TPO profiles in session_json
        from dataclasses import asdict as _asdict
        session_data["session_tpos"] = _asdict(session_tpo_set) if session_tpo_set else None
```

- [ ] **Step 3: Update get_tpo_live to include session_tpos**

In `get_tpo_live()`, after line 1631 (`profile = build_full_tpo_profile(bars_30m, tick_size=0.25)`), the `aggregate_bars_30m` call strips timestamps. We need timestamped bars. The 1m bars from DB (`rows`) have timestamps via `r.ts` (the candle model's timestamp column).

Add after `bars_30m = aggregate_bars_30m([_Bar(r) for r in rows])`:

```python
        # Build timestamped 30m bars for per-session split
        chunk = []
        bars_30m_ts = []
        for r in rows:
            chunk.append(r)
            if len(chunk) == 30:
                bars_30m_ts.append({
                    "ts": chunk[0].ts,
                    "high": max(c.h for c in chunk),
                    "low": min(c.l for c in chunk),
                    "open": chunk[0].o,
                    "close": chunk[-1].c,
                    "volume": sum(c.v for c in chunk),
                })
                chunk = []
        session_tpo_set = compute_session_tpos(bars_30m_ts, tick_size=0.25)
        from dataclasses import asdict as _asdict
        result["session_tpos"] = _asdict(session_tpo_set) if session_tpo_set else None
```

- [ ] **Step 4: Update backfill_tpo_sessions to include session_tpos**

In `backfill_tpo_sessions()`, after `bars_30m = aggregate_bars_30m([_Bar(r) for r in rows])` (line 1691), add the same pattern:

```python
            # Timestamped 30m bars for per-session split
            chunk = []
            bars_30m_ts = []
            for r in rows:
                chunk.append(r)
                if len(chunk) == 30:
                    bars_30m_ts.append({
                        "ts": chunk[0].ts,
                        "high": max(c.h for c in chunk),
                        "low": min(c.l for c in chunk),
                        "open": chunk[0].o,
                        "close": chunk[-1].c,
                        "volume": sum(c.v for c in chunk),
                    })
                    chunk = []
            session_tpo_set = compute_session_tpos(bars_30m_ts, tick_size=0.25)
```

Then in `store_tpo_session`, the `session_json` already serializes the full profile via `asdict(profile)`. We need to add `session_tpos` alongside. Update the `session_json` construction in `store_tpo_session` — or more simply, after `store_tpo_session(profile, ...)`, update the stored row's `session_json` to include the per-session data:

```python
            profile = build_full_tpo_profile(bars_30m, tick_size=0.25)
            try:
                self.store_tpo_session(profile, symbol, date_str)
                # Append per-session TPO to stored session_json
                from ..db.models import MarketTPOSession
                row = self.db.query(MarketTPOSession).filter_by(
                    symbol=symbol, date=date_str
                ).first()
                if row and session_tpo_set:
                    import json as _json
                    from dataclasses import asdict as _asdict
                    sj = _json.loads(row.session_json) if isinstance(row.session_json, str) else row.session_json
                    sj["session_tpos"] = _asdict(session_tpo_set)
                    row.session_json = _json.dumps(sj, default=str)
                    self.db.commit()
                stored += 1
```

- [ ] **Step 5: Run tests**

Run: `cd backend && python -m pytest tests/ -k "market_service or tpo" -v --timeout=60`
Expected: All tests PASS

- [ ] **Step 6: Commit**

```bash
cd backend
git add src/services/market_service.py
git commit -m "feat(market): compute and store per-session TPOs in session_json"
```

---

### Task 6: Update network input dimension

**Files:**
- Modify: `backend/src/rl/agent/network.py`

- [ ] **Step 1: Verify network accepts dynamic input_dim**

The `DQNetwork.__init__` already takes `input_dim` as a parameter — no hardcoded 146. The `OBSERVATION_DIM` constant in `observation.py` is computed dynamically and passed to the network at instantiation. So no code change is needed in `network.py`.

Verify by running:

Run: `cd backend && python -c "from src.rl.features.observation import OBSERVATION_DIM; from src.rl.agent.network import DQNetwork; net = DQNetwork(input_dim=OBSERVATION_DIM); print(f'Input dim: {OBSERVATION_DIM}, first layer: {net.encoder[0].in_features}')"`
Expected: `Input dim: 159, first layer: 159`

- [ ] **Step 2: Update network docstring**

Edit the docstring in `backend/src/rl/agent/network.py` to reflect the new dimension:

```python
# Old:
#   Input (obs_dim) → 256 (LayerNorm, ReLU) → 256 (LayerNorm, ReLU)
# This is fine as-is since it uses obs_dim, not 146. No change needed.
```

No code change required. Mark as complete.

- [ ] **Step 3: Commit (skip if no changes)**

No commit needed — network is already dynamic.

---

### Task 7: Run full test suite and verify

**Files:**
- All modified files

- [ ] **Step 1: Run the full test suite**

Run: `cd backend && python -m pytest tests/ -v --timeout=120`
Expected: All tests PASS. `OBSERVATION_DIM` is 159.

- [ ] **Step 2: Verify observation dimension end-to-end**

Run: `cd backend && python -c "from src.rl.features.observation import OBSERVATION_DIM; print(f'OBSERVATION_DIM = {OBSERVATION_DIM}'); assert OBSERVATION_DIM == 159, f'Expected 159, got {OBSERVATION_DIM}'"`
Expected: `OBSERVATION_DIM = 159`

- [ ] **Step 3: Run TPO-specific tests**

Run: `cd backend && python -m pytest tests/test_rl_tpo_extensions.py -v`
Expected: All tests PASS (TestClassifyTpoShape, TestDetectExcess, TestComputeSessionTpos, TestExtractSessionTpoFeatures, TestObservationDimension)

- [ ] **Step 4: Final commit with all files**

```bash
cd backend
git add -A
git status
# If there are any remaining unstaged changes, add them
git commit -m "feat(rl): per-session TPO profiles — complete implementation"
```
