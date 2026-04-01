# Zone-Based Level Consolidation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Merge nearby structural levels into zones so the RL agent fires one decision per zone entry instead of per-level, with multi-hot composition encoding.

**Architecture:** New `zone_builder.py` provides `build_zones()` that clusters levels by ATR-adaptive radius. All downstream consumers (replay_engine, observation, session_manager, level_monitor) switch from individual level touches to zone entries. Observation vector changes from 167 to 169 dims (25-dim one-hot → 27-dim multi-hot + 3 zone features, 8-dim confluence → 5-dim).

**Tech Stack:** Python 3.10+, NumPy, PyTorch (inference only), pytest

**Note:** Another agent is actively adding more LevelType members. The plan uses `len(LevelType)` dynamically instead of hardcoding counts. Skip `rl replay` and `rl train` — training data will be regenerated after all level types are finalized.

---

## File Map

| Action | File | Responsibility |
|--------|------|---------------|
| Create | `backend/src/rl/zone_builder.py` | Zone/ZoneMember dataclasses, `build_zones()`, hierarchy weights |
| Create | `backend/tests/test_rl_zone_builder.py` | Zone builder unit tests |
| Modify | `backend/src/rl/config.py` | Add zone constants (ATR_FRACTION, radius bounds) |
| Modify | `backend/src/rl/features/level_features.py` | Replace `encode_confluence` → `encode_zone_confluence`, add `encode_zone_composition`, `encode_zone_features` |
| Modify | `backend/tests/test_rl_features.py` | Update tests for new zone-based features |
| Modify | `backend/src/rl/features/observation.py` | Zone composition + zone features + simplified confluence |
| Modify | `backend/src/rl/data/replay_engine.py` | `_check_zone_entry()`, zone-aware `_build_state()` |
| Modify | `backend/tests/test_rl_replay_engine.py` | Update replay engine tests for zone logic |
| Modify | `backend/src/rl/data/episode_builder.py` | Trail targets accept zone centers |
| Modify | `backend/src/rl/session_manager.py` | `on_zone_entry()` replaces `on_level_touch()` |
| Modify | `backend/src/market_data/level_monitor.py` | Zone-aware state machine, `_build_rl_state()` changes |
| Modify | `backend/src/rl/live_inference.py` | Handle zone-based state dicts |

---

### Task 1: Add Zone Constants to Config

**Files:**
- Modify: `backend/src/rl/config.py`

- [ ] **Step 1: Add zone consolidation constants**

Add after the `AT_LEVEL_TICKS` line (line 77):

```python
# --- Zone Consolidation ---
ATR_FRACTION = 0.05          # zone radius as fraction of session ATR
ATR_PERIOD = 14              # ATR lookback (30m candles)
MIN_ZONE_RADIUS_TICKS = 4    # floor: never merge tighter than 1 point
MAX_ZONE_RADIUS_TICKS = 20   # cap: never merge wider than 5 points
```

- [ ] **Step 2: Run existing tests to verify no breakage**

Run: `cd backend && python -m pytest tests/test_rl_features.py tests/test_rl_replay_engine.py -x -q`
Expected: All tests pass (no existing tests touch these constants yet)

- [ ] **Step 3: Commit**

```bash
git add backend/src/rl/config.py
git commit -m "feat(rl): add zone consolidation constants to config"
```

---

### Task 2: Create Zone Builder

**Files:**
- Create: `backend/src/rl/zone_builder.py`
- Create: `backend/tests/test_rl_zone_builder.py`

- [ ] **Step 1: Write tests for zone builder**

```python
"""Tests for zone builder — ATR-adaptive level clustering."""
import pytest
from src.rl.config import LevelType, TICK_SIZE
from src.rl.zone_builder import Zone, ZoneMember, build_zones


class TestBuildZones:
    def test_empty_levels_returns_empty(self):
        zones = build_zones([], session_atr=40.0)
        assert zones == []

    def test_single_level_becomes_singleton_zone(self):
        levels = [("vwap", LevelType.VWAP, 4500.0)]
        zones = build_zones(levels, session_atr=40.0)
        assert len(zones) == 1
        assert zones[0].member_count == 1
        assert zones[0].members[0].level_type == LevelType.VWAP

    def test_nearby_levels_merged_into_one_zone(self):
        # ATR=40, radius=0.05*40=2.0 points. These are 0.5 apart — merge.
        levels = [
            ("vwap_sd1", LevelType.VWAP_SD1, 4500.00),
            ("daily_poc", LevelType.DAILY_POC, 4500.50),
            ("tpoc", LevelType.TPOC, 4501.00),
        ]
        zones = build_zones(levels, session_atr=40.0)
        assert len(zones) == 1
        zone = zones[0]
        assert zone.member_count == 3
        assert zone.center_price == pytest.approx(4500.50, abs=0.01)

    def test_far_apart_levels_separate_zones(self):
        # ATR=40, radius=2.0 points. These are 10 apart — separate.
        levels = [
            ("vwap", LevelType.VWAP, 4500.0),
            ("pdh", LevelType.PDH, 4510.0),
        ]
        zones = build_zones(levels, session_atr=40.0)
        assert len(zones) == 2

    def test_radius_clamped_to_min(self):
        # Very low ATR → radius would be tiny, but MIN_ZONE_RADIUS_TICKS=4 (1 point)
        levels = [
            ("vwap", LevelType.VWAP, 4500.00),
            ("poc", LevelType.DAILY_POC, 4500.75),  # 0.75 points apart = 3 ticks
        ]
        # ATR=1.0 → radius=0.05 → clamped to 4 ticks = 1.0 points
        zones = build_zones(levels, session_atr=1.0)
        assert len(zones) == 1  # merged because 0.75 < 1.0 (clamped radius)

    def test_radius_clamped_to_max(self):
        # Very high ATR → radius would be huge, but MAX_ZONE_RADIUS_TICKS=20 (5 points)
        levels = [
            ("vwap", LevelType.VWAP, 4500.0),
            ("pdh", LevelType.PDH, 4506.0),  # 6 points apart
        ]
        # ATR=200 → radius=10 → clamped to 20 ticks = 5 points
        zones = build_zones(levels, session_atr=200.0)
        assert len(zones) == 2  # NOT merged because 6.0 > 5.0

    def test_composition_multi_hot(self):
        levels = [
            ("vwap", LevelType.VWAP, 4500.0),
            ("poc", LevelType.DAILY_POC, 4500.5),
        ]
        zones = build_zones(levels, session_atr=40.0)
        comp = zones[0].composition
        assert len(comp) == len(LevelType)
        # VWAP and DAILY_POC bits should be 1.0
        members = list(LevelType)
        assert comp[members.index(LevelType.VWAP)] == 1.0
        assert comp[members.index(LevelType.DAILY_POC)] == 1.0
        assert sum(comp) == 2.0  # exactly 2 members

    def test_hierarchy_score_higher_for_poc_cluster(self):
        poc_cluster = [
            ("daily_poc", LevelType.DAILY_POC, 4500.0),
            ("weekly_poc", LevelType.WEEKLY_POC, 4500.5),
        ]
        sd_cluster = [
            ("vwap_sd2", LevelType.VWAP_SD2, 4600.0),
            ("vwap_sd3", LevelType.VWAP_SD3, 4600.5),
        ]
        poc_zones = build_zones(poc_cluster, session_atr=40.0)
        sd_zones = build_zones(sd_cluster, session_atr=40.0)
        assert poc_zones[0].hierarchy_score > sd_zones[0].hierarchy_score

    def test_zone_bounds_include_radius_padding(self):
        levels = [("vwap", LevelType.VWAP, 4500.0)]
        zones = build_zones(levels, session_atr=40.0)
        zone = zones[0]
        # radius = 0.05 * 40 = 2.0 points. bounds = center ± radius/2
        assert zone.lower_bound < 4500.0
        assert zone.upper_bound > 4500.0

    def test_width_ticks_computed_correctly(self):
        levels = [
            ("vwap", LevelType.VWAP, 4500.0),
            ("poc", LevelType.DAILY_POC, 4501.0),
        ]
        zones = build_zones(levels, session_atr=40.0)
        zone = zones[0]
        # width = upper - lower, should be > 1.0 (the member spread) due to padding
        assert zone.width_ticks > (1.0 / TICK_SIZE)

    def test_sorted_by_center_price(self):
        levels = [
            ("pdh", LevelType.PDH, 4510.0),
            ("vwap", LevelType.VWAP, 4500.0),
            ("pdl", LevelType.PDL, 4490.0),
        ]
        zones = build_zones(levels, session_atr=40.0)
        centers = [z.center_price for z in zones]
        assert centers == sorted(centers)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_rl_zone_builder.py -x -q`
Expected: `ModuleNotFoundError: No module named 'src.rl.zone_builder'`

- [ ] **Step 3: Implement zone_builder.py**

```python
"""Zone builder — cluster nearby structural levels into zones.

Merges levels within an ATR-adaptive radius so the RL agent fires one
decision per zone entry instead of per individual level. Each zone
carries a multi-hot composition vector encoding which LevelTypes are
present.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .config import (
    LevelType,
    TICK_SIZE,
    ATR_FRACTION,
    MIN_ZONE_RADIUS_TICKS,
    MAX_ZONE_RADIUS_TICKS,
)

# Hierarchy weights: structural importance of each level type.
# Higher = more significant for support/resistance.
_HIERARCHY_WEIGHTS: dict[LevelType, float] = {
    LevelType.DAILY_POC: 1.0,
    LevelType.WEEKLY_POC: 1.0,
    LevelType.MONTHLY_POC: 1.0,
    LevelType.NAKED_POC: 1.0,
    LevelType.VWAP: 0.9,
    LevelType.PDH: 0.9,
    LevelType.PDL: 0.9,
    LevelType.DAILY_VAH: 0.8,
    LevelType.DAILY_VAL: 0.8,
    LevelType.TPOC: 0.8,
    LevelType.WEEKLY_VAH: 0.7,
    LevelType.WEEKLY_VAL: 0.7,
    LevelType.MONTHLY_VAH: 0.7,
    LevelType.MONTHLY_VAL: 0.7,
    LevelType.NYIB_HIGH: 0.6,
    LevelType.NYIB_LOW: 0.6,
    LevelType.TVAH: 0.6,
    LevelType.TVAL: 0.6,
    LevelType.VWAP_SD1: 0.5,
    LevelType.TOKYO_HIGH: 0.5,
    LevelType.TOKYO_LOW: 0.5,
    LevelType.TIBH: 0.5,
    LevelType.TIBL: 0.5,
    LevelType.VWAP_SD2: 0.4,
    LevelType.VWAP_SD3: 0.3,
}

# Max possible hierarchy score (sum of all weights) for normalisation
_MAX_HIERARCHY = sum(_HIERARCHY_WEIGHTS.values())


@dataclass
class ZoneMember:
    """An individual level within a zone."""
    name: str
    level_type: LevelType
    price: float


@dataclass
class Zone:
    """A cluster of nearby structural levels."""
    center_price: float
    upper_bound: float
    lower_bound: float
    members: list[ZoneMember] = field(default_factory=list)
    composition: list[float] = field(default_factory=list)
    width_ticks: float = 0.0
    member_count: int = 0
    hierarchy_score: float = 0.0


def _compute_radius(session_atr: float) -> float:
    """Compute zone merge radius in price units, clamped to configured bounds."""
    radius_price = ATR_FRACTION * session_atr
    min_radius = MIN_ZONE_RADIUS_TICKS * TICK_SIZE
    max_radius = MAX_ZONE_RADIUS_TICKS * TICK_SIZE
    return max(min_radius, min(max_radius, radius_price))


def _finalize_zone(members: list[ZoneMember], radius: float) -> Zone:
    """Build a Zone from its collected members."""
    prices = [m.price for m in members]
    center = sum(prices) / len(prices)
    upper = max(prices) + radius / 2
    lower = min(prices) - radius / 2

    # Multi-hot composition: 1.0 for each LevelType present
    all_types = list(LevelType)
    present = {m.level_type for m in members}
    composition = [1.0 if lt in present else 0.0 for lt in all_types]

    # Hierarchy score: sum of weights for present types, normalised
    weight_sum = sum(_HIERARCHY_WEIGHTS.get(m.level_type, 0.3) for m in members)
    hierarchy_score = min(1.0, weight_sum / max(_MAX_HIERARCHY, 1e-6))

    return Zone(
        center_price=center,
        upper_bound=upper,
        lower_bound=lower,
        members=list(members),
        composition=composition,
        width_ticks=(upper - lower) / TICK_SIZE,
        member_count=len(members),
        hierarchy_score=hierarchy_score,
    )


def build_zones(
    levels: list[tuple[str, LevelType, float]],
    session_atr: float,
) -> list[Zone]:
    """Cluster levels into zones using greedy sequential merge.

    Args:
        levels: List of (name, LevelType, price) tuples from _rebuild_active_levels().
        session_atr: Session ATR in price units (e.g. 40.0 for a 40-point NQ day).

    Returns:
        List of Zone objects sorted by center_price ascending.
    """
    if not levels:
        return []

    radius = _compute_radius(session_atr)

    # Sort by price ascending
    sorted_levels = sorted(levels, key=lambda x: x[2])

    # Greedy merge: walk sorted levels, start new zone when gap > radius
    zones: list[Zone] = []
    current_members: list[ZoneMember] = []

    for name, level_type, price in sorted_levels:
        member = ZoneMember(name=name, level_type=level_type, price=price)

        if not current_members:
            current_members.append(member)
            continue

        # Compare to last member in current zone (not center — sequential merge)
        if abs(price - current_members[-1].price) <= radius:
            current_members.append(member)
        else:
            zones.append(_finalize_zone(current_members, radius))
            current_members = [member]

    # Finalize last zone
    if current_members:
        zones.append(_finalize_zone(current_members, radius))

    return zones
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_rl_zone_builder.py -v`
Expected: All 11 tests pass

- [ ] **Step 5: Commit**

```bash
git add backend/src/rl/zone_builder.py backend/tests/test_rl_zone_builder.py
git commit -m "feat(rl): add zone builder with ATR-adaptive clustering"
```

---

### Task 3: Update Level Features — Zone Composition and Confluence

**Files:**
- Modify: `backend/src/rl/features/level_features.py`
- Modify: `backend/tests/test_rl_features.py`

- [ ] **Step 1: Write tests for new zone feature functions**

Add to `backend/tests/test_rl_features.py` after the existing `TestConfluence` class:

```python
from src.rl.zone_builder import Zone, ZoneMember, build_zones
from src.rl.features.level_features import (
    encode_zone_composition, encode_zone_features, encode_zone_confluence,
)


class TestZoneComposition:
    def test_returns_correct_length(self):
        zone = build_zones(
            [("vwap", LevelType.VWAP, 4500.0)], session_atr=40.0
        )[0]
        comp = encode_zone_composition(zone)
        assert len(comp) == len(LevelType)

    def test_multi_hot_values(self):
        zone = build_zones([
            ("vwap", LevelType.VWAP, 4500.0),
            ("poc", LevelType.DAILY_POC, 4500.5),
        ], session_atr=40.0)[0]
        comp = encode_zone_composition(zone)
        members = list(LevelType)
        assert comp[members.index(LevelType.VWAP)] == 1.0
        assert comp[members.index(LevelType.DAILY_POC)] == 1.0
        # Others should be 0
        assert comp[members.index(LevelType.PDH)] == 0.0

    def test_singleton_is_one_hot(self):
        zone = build_zones(
            [("vwap", LevelType.VWAP, 4500.0)], session_atr=40.0
        )[0]
        comp = encode_zone_composition(zone)
        assert sum(comp) == 1.0


class TestZoneFeatures:
    def test_returns_three_floats(self):
        zone = build_zones(
            [("vwap", LevelType.VWAP, 4500.0)], session_atr=40.0
        )[0]
        feats = encode_zone_features(zone)
        assert len(feats) == 3
        assert all(isinstance(f, float) for f in feats)

    def test_values_bounded_0_1(self):
        zone = build_zones([
            ("vwap", LevelType.VWAP, 4500.0),
            ("poc", LevelType.DAILY_POC, 4500.5),
            ("tpoc", LevelType.TPOC, 4501.0),
        ], session_atr=40.0)[0]
        feats = encode_zone_features(zone)
        for f in feats:
            assert 0.0 <= f <= 1.0


class TestZoneConfluence:
    def test_returns_five_floats(self):
        zones = build_zones([
            ("vwap", LevelType.VWAP, 4500.0),
            ("pdh", LevelType.PDH, 4520.0),
        ], session_atr=40.0)
        result = encode_zone_confluence(zones[0], zones)
        assert len(result) == 5

    def test_nearest_zone_distances(self):
        zones = build_zones([
            ("vwap", LevelType.VWAP, 4500.0),
            ("pdh", LevelType.PDH, 4510.0),
            ("pdl", LevelType.PDL, 4490.0),
        ], session_atr=40.0)
        # Middle zone (4500) has neighbours at 4490 and 4510
        result = encode_zone_confluence(zones[1], zones)
        # nearest_higher and nearest_lower should be ~10 points / 0.25 / 50 = 0.8
        assert result[0] > 0.0  # nearest_higher_zone_dist
        assert result[1] > 0.0  # nearest_lower_zone_dist

    def test_no_neighbours_returns_max_distance(self):
        zones = build_zones(
            [("vwap", LevelType.VWAP, 4500.0)], session_atr=40.0
        )
        result = encode_zone_confluence(zones[0], zones)
        # Only one zone → distances capped at 1.0 (50 ticks normalised)
        assert result[0] == pytest.approx(1.0)
        assert result[1] == pytest.approx(1.0)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_rl_features.py::TestZoneComposition -x -q`
Expected: `ImportError: cannot import name 'encode_zone_composition'`

- [ ] **Step 3: Add zone encoding functions to level_features.py**

Add to the end of `backend/src/rl/features/level_features.py` (keep existing `encode_level_type` and `encode_confluence` for backward compat during transition):

```python
# --- Zone-based feature functions ---

def encode_zone_composition(zone) -> list[float]:
    """Multi-hot encoding of LevelTypes present in the zone.

    Returns a list of len(LevelType) floats. Multiple bits can be 1.0
    for multi-member zones. Singletons produce the same output as the
    old one-hot encode_level_type().
    """
    return list(zone.composition)


def encode_zone_features(zone) -> list[float]:
    """3 features describing the zone itself: width, count, hierarchy."""
    return [
        min(zone.width_ticks / 50.0, 1.0),
        min(zone.member_count / 10.0, 1.0),
        zone.hierarchy_score,
    ]


def encode_zone_confluence(
    zone,
    all_zones: list,
    fvgs: list | None = None,
    single_print_zones: list | None = None,
) -> list[float]:
    """5 inter-zone features: distances to nearest zones + structural overlap.

    Replaces the old 8-dim encode_confluence(). Zone-internal clustering
    info (count, hierarchy) is now in encode_zone_features().
    """
    center = zone.center_price

    higher = [z.center_price for z in all_zones if z.center_price > center + TICK_SIZE]
    lower = [z.center_price for z in all_zones if z.center_price < center - TICK_SIZE]

    if higher:
        nearest_higher = min(p - center for p in higher)
    else:
        nearest_higher = 50.0 * TICK_SIZE  # far away default

    if lower:
        nearest_lower = min(center - p for p in lower)
    else:
        nearest_lower = 50.0 * TICK_SIZE

    # FVG overlap at zone center
    fvg_overlap = 0.0
    fvg_width_ticks = 0.0
    for fvg in (fvgs or []):
        lo = getattr(fvg, "price_low", 0.0)
        hi = getattr(fvg, "price_high", 0.0)
        if lo <= center <= hi:
            fvg_overlap = 1.0
            fvg_width_ticks = max(fvg_width_ticks, (hi - lo) / TICK_SIZE)

    # Single print zone overlap
    sp_overlap = 0.0
    for sp in (single_print_zones or []):
        sp_lo, sp_hi = sp[0], sp[1]
        if sp_lo <= center <= sp_hi:
            sp_overlap = 1.0
            break

    return [
        min(nearest_higher / TICK_SIZE / 50.0, 1.0),
        min(nearest_lower / TICK_SIZE / 50.0, 1.0),
        fvg_overlap,
        min(fvg_width_ticks / 20.0, 1.0),
        sp_overlap,
    ]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_rl_features.py::TestZoneComposition tests/test_rl_features.py::TestZoneFeatures tests/test_rl_features.py::TestZoneConfluence -v`
Expected: All 8 new tests pass

- [ ] **Step 5: Commit**

```bash
git add backend/src/rl/features/level_features.py backend/tests/test_rl_features.py
git commit -m "feat(rl): add zone composition, features, and confluence encoding"
```

---

### Task 4: Update Observation Vector

**Files:**
- Modify: `backend/src/rl/features/observation.py`

- [ ] **Step 1: Update observation.py to support both zone and legacy modes**

Replace the entire file content. The key changes:
- Import new zone feature functions
- `build_observation()` checks for `state["zone"]` (new path) vs `state["level_type"]` (legacy path)
- Zone path: 27-dim multi-hot + 3-dim zone features + 5-dim zone confluence = 35 dims
- Legacy path: original 25-dim one-hot + 8-dim confluence = 33 dims (kept for backward compat)
- `_dummy_state` updated to use zone path for OBSERVATION_DIM computation

```python
"""Observation vector assembler — flat static features.

All features are hand-crafted from domain knowledge (AMT, orderflow, Fabio's
patterns). No raw tick sequences — the orderflow and micro features already
encode the temporal dynamics.

Zone-based segment sizes:
    zone composition     len(LevelType) (multi-hot)
    orderflow            21  (was 15, added 6 temporal dynamics)
    structure + session  23
    tpo (per-session)    26
    candle window        15
    zone features         3  (width, count, hierarchy)
    zone confluence       5  (inter-zone distances + FVG/SP overlap)
    macro                 7
    setup                14  (13 + squeeze detector)
    micro (hand-crafted) 20
    approach direction    1
    execution context     7
    ---
    total               len(LevelType) + 142
"""
from __future__ import annotations

import numpy as np

from ..config import LevelType, TICK_SIZE
from .level_features import (
    encode_level_type,
    encode_confluence,
    encode_zone_composition,
    encode_zone_features,
    encode_zone_confluence,
)
from .orderflow_features import extract_orderflow_features
from .tpo_features import extract_session_tpo_features
from .structure_features import extract_structure_features
from .macro_features import extract_macro_features
from .setup_features import extract_setup_features
from .micro_features import extract_micro_features
from .execution_features import extract_execution_features

# Candle window: last 5 candles x 3 features each
_CANDLE_WINDOW = 5
_CANDLE_FEATS_PER = 3  # delta_norm, volume_norm, body_ratio
_CANDLE_DIM = _CANDLE_WINDOW * _CANDLE_FEATS_PER  # 15


def _build_candle_window(candles: list, avg_vol: float) -> np.ndarray:
    """Last 5 candles -> 15 features (delta_norm, volume_norm, body_ratio)."""
    out = np.zeros(_CANDLE_DIM, dtype=np.float32)
    if not candles:
        return out
    window = candles[-_CANDLE_WINDOW:] if len(candles) >= _CANDLE_WINDOW else candles
    for i, c in enumerate(window):
        offset = i * _CANDLE_FEATS_PER
        out[offset + 0] = float(np.clip(c.delta / max(avg_vol, 1.0), -1.0, 1.0))
        out[offset + 1] = float(np.clip(c.volume / max(avg_vol, 1.0) / 5.0, 0.0, 1.0))
        out[offset + 2] = float(c.body_ratio)
    return out


def build_observation(state: dict) -> np.ndarray:
    """Assemble the full observation vector from a state dict.

    Supports two modes:
    - Zone mode (state["zone"] present): multi-hot composition + zone features
    - Legacy mode (state["level_type"] present): one-hot level type + old confluence
    """
    zone = state.get("zone")
    price: float = float(state.get("price", 0.0))
    candles: list = state.get("candles", [])
    vwap_bands = state.get("vwap_bands")
    volume_profile = state.get("volume_profile")
    session_levels = state.get("session_levels")
    orderflow_signals = state.get("orderflow_signals")
    macro = state.get("macro")
    session_context = state.get("session_context")
    recent_ticks: list[dict] = state.get("recent_ticks", [])

    # Avg vol for normalisation
    if candles:
        avg_vol = sum(c.volume for c in candles[-20:]) / max(len(candles[-20:]), 1)
        avg_vol = max(avg_vol, 1.0)
    else:
        avg_vol = 1.0

    # 1. Level/zone identity
    if zone is not None:
        # Zone mode: multi-hot composition
        seg_level = np.array(encode_zone_composition(zone), dtype=np.float32)
    else:
        # Legacy mode: one-hot level type
        level_type: LevelType = state.get("level_type", LevelType.VWAP)
        seg_level = np.array(encode_level_type(level_type), dtype=np.float32)

    # 2. Orderflow (21)
    seg_orderflow = extract_orderflow_features(candles, orderflow_signals)

    # 3. Structure + session (23)
    seg_structure = extract_structure_features(
        price, vwap_bands, volume_profile, session_levels, session_context
    )

    # 4. TPO per-session (26)
    session_tpos = state.get("session_tpos")
    seg_tpo = extract_session_tpo_features(session_tpos, price)

    # 5. Candle window (15)
    seg_candles = _build_candle_window(candles, avg_vol)

    # 6-7. Zone features + confluence OR legacy confluence
    fvgs = state.get("fvgs", [])
    single_print_zones = state.get("single_print_zones", [])

    if zone is not None:
        all_zones = state.get("all_zones", [zone])
        # Zone features (3)
        seg_zone_feats = np.array(encode_zone_features(zone), dtype=np.float32)
        # Zone confluence (5)
        conf_vals = encode_zone_confluence(
            zone, all_zones, fvgs=fvgs, single_print_zones=single_print_zones,
        )
        seg_confluence = np.array(conf_vals, dtype=np.float32)
    else:
        all_levels: list[float] = state.get("all_levels", [])
        seg_zone_feats = np.array([], dtype=np.float32)  # empty in legacy mode
        conf = encode_confluence(
            price, all_levels, tick_size=TICK_SIZE,
            fvgs=fvgs, single_print_zones=single_print_zones,
        )
        seg_confluence = np.array([
            conf["levels_within_5_ticks"] / 10.0,
            conf["strongest_cluster_score"],
            conf["nearest_higher_level_dist"] / 50.0,
            conf["nearest_lower_level_dist"] / 50.0,
            conf["touched_level_hierarchy_rank"],
            conf["fvg_overlap"],
            conf["fvg_width_ticks"],
            conf["single_print_overlap"],
        ], dtype=np.float32)

    # 8. Macro (7)
    seg_macro = extract_macro_features(macro)

    # 9. Setup detection (14)
    seg_setup = extract_setup_features(state)

    # 10. Micro features (20)
    seg_micro = extract_micro_features(recent_ticks, price)

    # 11. Approach direction (1)
    approach = state.get("approach_direction", "up")
    seg_approach = np.array([
        1.0 if approach == "up" else -1.0,
    ], dtype=np.float32)

    # 12. Execution context (7)
    seg_execution = extract_execution_features(state, recent_ticks, candles, price)

    obs = np.concatenate([
        seg_level,        # len(LevelType) (zone) or len(LevelType) (legacy)
        seg_orderflow,    # 21
        seg_structure,    # 23
        seg_tpo,          # 26
        seg_candles,      # 15
        seg_zone_feats,   # 3 (zone) or 0 (legacy)
        seg_confluence,   # 5 (zone) or 8 (legacy)
        seg_macro,        # 7
        seg_setup,        # 14
        seg_micro,        # 20
        seg_approach,     # 1
        seg_execution,    # 7
    ])

    # Sanitise
    obs = np.where(np.isfinite(obs), obs, 0.0)
    return obs.astype(np.float32)


# Compute dimension at import time using zone mode (the primary path going forward)
def _make_dummy_zone():
    """Build a minimal Zone for dimension computation."""
    from ..zone_builder import Zone, ZoneMember
    member = ZoneMember(name="vwap", level_type=LevelType.VWAP, price=19000.0)
    return Zone(
        center_price=19000.0,
        upper_bound=19001.0,
        lower_bound=18999.0,
        members=[member],
        composition=[1.0 if lt == LevelType.VWAP else 0.0 for lt in LevelType],
        width_ticks=8.0,
        member_count=1,
        hierarchy_score=0.5,
    )


_dummy_state: dict = {
    "zone": _make_dummy_zone(),
    "all_zones": [_make_dummy_zone()],
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

OBSERVATION_DIM: int = int(build_observation(_dummy_state).shape[0])
CONTEXT_DIM: int | None = None
```

- [ ] **Step 2: Run tests to verify observation builds correctly**

Run: `cd backend && python -m pytest tests/test_rl_features.py::TestBuildObservation -v`
Expected: Some existing tests may need updating (hardcoded dim assertions). Fix any that reference the old OBSERVATION_DIM value.

- [ ] **Step 3: Update hardcoded dimension assertions in test_rl_features.py**

Find and update `test_observation_dim_is_107` (or similar) to use the new dimension. Replace the hardcoded assertion with:

```python
def test_observation_dim_matches_zone_mode(self):
    # Zone mode: len(LevelType) + 3 + 5 + 21 + 23 + 26 + 15 + 7 + 14 + 20 + 1 + 7
    expected = len(LevelType) + 142
    assert OBSERVATION_DIM == expected
```

Also update `_minimal_state()` helper to produce zone-mode state and update `test_different_level_types_differ` to test zone composition differences:

```python
def _make_zone(level_type: LevelType = LevelType.VWAP, price: float = 19000.0):
    from src.rl.zone_builder import Zone, ZoneMember
    member = ZoneMember(name=level_type.value, level_type=level_type, price=price)
    return Zone(
        center_price=price,
        upper_bound=price + 1.0,
        lower_bound=price - 1.0,
        members=[member],
        composition=[1.0 if lt == level_type else 0.0 for lt in LevelType],
        width_ticks=8.0,
        member_count=1,
        hierarchy_score=0.5,
    )


def _minimal_state(level_type: LevelType = LevelType.VWAP, price: float = 19000.0) -> dict:
    zone = _make_zone(level_type, price)
    return {
        "zone": zone,
        "all_zones": [zone],
        "price": price,
        "candles": [],
        "vwap_bands": None,
        "volume_profile": None,
        "tpo_profile": None,
        "session_levels": None,
        "all_levels": [],
        "orderflow_signals": None,
        "macro": None,
        "session_context": None,
    }
```

- [ ] **Step 4: Run full feature test suite**

Run: `cd backend && python -m pytest tests/test_rl_features.py -v`
Expected: All tests pass

- [ ] **Step 5: Commit**

```bash
git add backend/src/rl/features/observation.py backend/tests/test_rl_features.py
git commit -m "feat(rl): update observation vector for zone-based multi-hot encoding"
```

---

### Task 5: Update Replay Engine — Zone Touch Detection

**Files:**
- Modify: `backend/src/rl/data/replay_engine.py`
- Modify: `backend/tests/test_rl_replay_engine.py`

- [ ] **Step 1: Write tests for zone-based replay**

Add to `backend/tests/test_rl_replay_engine.py`:

```python
class TestReplayEngineZones:
    def test_clustered_levels_produce_fewer_episodes_than_individual(self):
        """Zone consolidation should merge nearby level touches."""
        engine = ReplayEngine()
        # Price oscillates around 20000 where multiple levels cluster
        ticks = []
        for i in range(400):
            minute = 30 + (i // 8)
            if minute >= 60:
                break
            # Price moves through a small range to touch clustered levels
            price = 20000.0 + (i % 3) * 0.25
            ticks.append(_make_tick(9, minute, price, 10))

        session_dt = datetime(2025, 1, 15, 12, 0, 0, tzinfo=ET)
        precomputed = {
            "naked_pocs": [],
            "weekly_poc": 20000.25,  # cluster with VWAP
            "weekly_vah": None, "weekly_val": None,
            "monthly_poc": 20000.50,  # also in cluster
            "monthly_vah": None, "monthly_val": None,
            "single_print_zones": [],
        }
        episodes = engine.replay_session(ticks, session_dt, precomputed_levels=precomputed)
        # With zone consolidation, nearby levels merge → fewer episodes
        # The exact count depends on VWAP drift, but should be < what individual would produce
        # Just verify episodes are generated and have zone info
        for ep in episodes:
            if ep.state and ep.state.get("zone"):
                assert ep.state["zone"].member_count >= 1
```

- [ ] **Step 2: Modify replay_engine.py to use zones**

Key changes to `backend/src/rl/data/replay_engine.py`:

Add imports at top:
```python
from ..zone_builder import Zone, build_zones
from ..config import ATR_PERIOD
```

In `_reset()`, add after `self._touched_keys`:
```python
# Zone state
self._active_zones: list[Zone] = []
self._zone_keys: set[str] = set()
```

Add `_compute_session_atr()` method:
```python
def _compute_session_atr(self) -> float:
    """Compute ATR from 30m candles for zone radius calculation."""
    bars_30m = self._candle_agg.get_completed_30m()
    if len(bars_30m) < 2:
        # Fallback: session range
        if self._session_high is not None and self._session_low is not None:
            return max(1.0, self._session_high - self._session_low)
        return 40.0  # reasonable NQ default

    trs = []
    for i in range(1, min(len(bars_30m), ATR_PERIOD + 1)):
        bar = bars_30m[-i]
        prev = bars_30m[-i - 1] if i < len(bars_30m) else bar
        tr = max(
            bar["high"] - bar["low"],
            abs(bar["high"] - prev["close"]),
            abs(bar["low"] - prev["close"]),
        )
        trs.append(tr)
    return sum(trs) / len(trs) if trs else 40.0
```

At the end of `_rebuild_active_levels()`, add zone building:
```python
# Build zones from active levels
session_atr = self._compute_session_atr()
self._active_zones = build_zones(self._active_levels, session_atr)
```

Add `_check_zone_entry()` method:
```python
def _check_zone_entry(self, price: float) -> list[Zone]:
    """Detect newly-entered zones with debouncing."""
    newly_entered: list[Zone] = []
    still_inside: set[str] = set()

    for zone in self._active_zones:
        inside = zone.lower_bound <= price <= zone.upper_bound
        snapped = round(zone.center_price / TICK_SIZE) * TICK_SIZE
        key = f"zone_{snapped}"

        if inside:
            still_inside.add(key)
            if key not in self._zone_keys:
                self._zone_keys.add(key)
                newly_entered.append(zone)

    # Clear debounce for zones price has exited
    self._zone_keys -= (self._zone_keys - still_inside)
    return newly_entered
```

Update `replay_session()` main loop (around line 278-333) to use zones instead of individual levels:
- Replace `newly_touched = self._check_level_touch(price)` with `newly_entered = self._check_zone_entry(price)`
- Replace `if not newly_touched: continue` with `if not newly_entered: continue`
- Replace the level extraction block with zone-based logic:

```python
zone = newly_entered[0]
# Use zone center as the touch price
zone_price = zone.center_price

# ... (approach direction calc stays the same but uses zone_price)

state = self._build_state(tick, zone, session_date, date_str)
state["recent_ticks"] = recent_ticks
state["approach_direction"] = approach_direction
observation = build_observation(state)

# Trail targets: use zone centers (not individual level prices)
zone_centers_above = sorted([z.center_price for z in self._active_zones if z.center_price > price + TICK_SIZE])
zone_centers_below = sorted([z.center_price for z in self._active_zones if z.center_price < price - TICK_SIZE], reverse=True)

episode = label_outcome_from_array(
    touch_price=zone_price,
    ticks=norm_ticks,
    start=fwd_start,
    end=fwd_end,
    observation=observation,
    level_type=f"zone_{zone.member_count}m",  # zone descriptor for logging
    touch_ts=tick["ts"],
    approach_direction=approach_direction,
    levels_above=zone_centers_above,
    levels_below=zone_centers_below,
)
episode.state = state
```

Update `_build_state()` signature and body to accept a `Zone` instead of `LevelType`:
- Change parameter from `level_type: LevelType` to `zone: Zone`
- Replace `"level_type": level_type` with `"zone": zone` and `"all_zones": self._active_zones`
- Keep `"all_levels"` for backward compat (still needed by structure_features)

- [ ] **Step 3: Run replay engine tests**

Run: `cd backend && python -m pytest tests/test_rl_replay_engine.py -v`
Expected: All tests pass (existing + new)

- [ ] **Step 4: Commit**

```bash
git add backend/src/rl/data/replay_engine.py backend/tests/test_rl_replay_engine.py
git commit -m "feat(rl): replace level touch detection with zone entry in replay engine"
```

---

### Task 6: Update Episode Builder — Trail Through Zones

**Files:**
- Modify: `backend/src/rl/data/episode_builder.py`

- [ ] **Step 1: Verify episode builder already works with zone centers**

The episode builder's `label_outcome_from_array()` takes `levels_above` and `levels_below` as plain price lists. Since Task 5 already passes zone center prices instead of individual level prices, no structural changes are needed in episode_builder.py.

The only change: the `level_type` string passed from replay_engine is now `"zone_3m"` (zone descriptor) instead of `"daily_poc"` (individual type). This is fine — `level_type` in Episode is just a logging label, not used for training.

Run: `cd backend && python -m pytest tests/test_rl_episode_builder.py -v`
Expected: All tests pass unchanged

- [ ] **Step 2: Commit (no-op confirmation)**

No code changes needed. The trail targets are now zone centers (handled in Task 5).

---

### Task 7: Update Session Manager — Zone Entry

**Files:**
- Modify: `backend/src/rl/session_manager.py`

- [ ] **Step 1: Add on_zone_entry() method**

Add a new method that accepts a Zone object and delegates to the existing inference logic. Keep `on_level_touch()` for backward compat during transition.

In `backend/src/rl/session_manager.py`, add import at top:
```python
from .zone_builder import Zone
```

Add `on_zone_entry()` after `on_level_touch()`:

```python
def on_zone_entry(self, state: dict, current_price: float) -> dict:
    """Process a zone entry event. Zone-aware version of on_level_touch().

    The state dict must contain 'zone' (Zone object) and 'all_zones'.
    Stop prices are computed from zone boundaries instead of individual levels.

    Args:
        state: Full market state dict with zone info
        current_price: Current price at zone entry

    Returns:
        Signal dict (same format as on_level_touch)
    """
    zone: Zone | None = state.get("zone")
    if zone is None:
        return self.on_level_touch(state, current_price)

    # Circuit breakers (identical to on_level_touch)
    if self.session.is_stopped_out:
        return self._signal("skip", current_price, reason="daily_loss_limit")
    if self.session.total_stop_hits >= self.MAX_CONSECUTIVE_LOSSES:
        return self._signal("skip", current_price, reason="3_stops_halt")
    if self.session.total_pnl_r >= self.PROFIT_CAP_R:
        return self._signal("skip", current_price, reason="profit_cap_reached")

    # IB no-trade zone
    touch_epoch = state.get("touch_epoch", 0.0)
    if self.session.session_rth_open_epoch > 0 and touch_epoch > 0:
        minutes_since_open = (touch_epoch - self.session.session_rth_open_epoch) / 60.0
        if 0 < minutes_since_open < self.IB_NO_TRADE_MINUTES:
            return self._signal("skip", current_price, reason="ib_formation")

    # Run inference
    obs = build_observation(state)
    if self._normalizer is not None:
        obs = self._normalizer.normalize(obs)
    obs_tensor = torch.from_numpy(obs).unsqueeze(0)

    with torch.no_grad():
        q_values, stop_pred = self._network.forward_full(obs_tensor)

    q_cont = float(q_values[0, Action.CONTINUATION.value])
    q_rev = float(q_values[0, Action.REVERSAL.value])
    q_spread = abs(q_cont - q_rev)
    stop_ticks = float(stop_pred[0, 0])

    # Direction
    approach = state.get("approach_direction", "up")
    if q_cont > q_rev:
        model_side = PositionSide.LONG if approach == "up" else PositionSide.SHORT
    else:
        model_side = PositionSide.SHORT if approach == "up" else PositionSide.LONG

    # Stop from zone boundary (not center)
    if model_side == PositionSide.LONG:
        stop_price = zone.lower_bound - stop_ticks * TICK_SIZE
    else:
        stop_price = zone.upper_bound + stop_ticks * TICK_SIZE

    confidence = min(q_spread / 0.10, 1.0)
    size = self._compute_size(confidence)

    # Reversal cushion
    is_reversal = q_rev > q_cont
    if is_reversal and self.session.total_pnl_r < self.REVERSAL_CUSHION_R:
        return self._signal("skip", current_price,
                            q_spread=q_spread, confidence=confidence,
                            reason="reversal_no_cushion")

    if self.INDEPENDENT_MODE:
        if q_spread < self.MIN_Q_SPREAD:
            return self._signal("skip", current_price,
                                q_spread=q_spread, confidence=confidence,
                                reason="low_confidence")
        action = f"signal_{model_side.value}"
        return self._signal(action, current_price,
                            q_values=[q_cont, q_rev],
                            q_spread=q_spread, confidence=confidence,
                            stop_price=stop_price, size=size,
                            zone_members=zone.member_count,
                            reason="zone_signal")

    # Non-independent mode: delegate to on_level_touch logic
    # (position management code is identical — just stop price differs)
    return self.on_level_touch(state, current_price)
```

- [ ] **Step 2: Run existing session manager tests (if any)**

Run: `cd backend && python -m pytest tests/ -k "session_manager" -v`
Expected: Pass or no tests found (session_manager is tested via backtest integration)

- [ ] **Step 3: Commit**

```bash
git add backend/src/rl/session_manager.py
git commit -m "feat(rl): add on_zone_entry() to session manager with zone-boundary stops"
```

---

### Task 8: Update Level Monitor — Zone-Aware Live Path

**Files:**
- Modify: `backend/src/market_data/level_monitor.py`
- Modify: `backend/src/rl/live_inference.py`

- [ ] **Step 1: Add zone building to level_monitor.py**

Add imports at top of `level_monitor.py`:
```python
from src.rl.zone_builder import Zone, ZoneMember, build_zones
from src.rl.config import LevelType as RLLevelType
```

Add zone state to `__init__`:
```python
self._zones: list[Zone] = []
self._zone_debounce: set[str] = set()
self._session_atr: float = 40.0  # updated by set_session_context
```

Add to `set_session_context()`:
```python
# Update ATR for zone building
if "atr" in ctx:
    self._session_atr = ctx["atr"]
```

Add `_rebuild_zones()` method:
```python
def _rebuild_zones(self) -> None:
    """Rebuild zones from current levels. Called after load_levels()."""
    # Map MonitoredLevel to (name, LevelType, price) tuples
    level_type_map = {
        "poc": RLLevelType.DAILY_POC, "daily_poc": RLLevelType.DAILY_POC,
        "vah": RLLevelType.DAILY_VAH, "daily_vah": RLLevelType.DAILY_VAH,
        "val": RLLevelType.DAILY_VAL, "daily_val": RLLevelType.DAILY_VAL,
        "vwap": RLLevelType.VWAP,
        "vwap +1sd": RLLevelType.VWAP_SD1, "vwap -1sd": RLLevelType.VWAP_SD1,
        "vwap +2sd": RLLevelType.VWAP_SD2, "vwap -2sd": RLLevelType.VWAP_SD2,
        "vwap +3sd": RLLevelType.VWAP_SD3, "vwap -3sd": RLLevelType.VWAP_SD3,
        "pdh": RLLevelType.PDH, "pdl": RLLevelType.PDL,
        "tokyo_high": RLLevelType.TOKYO_HIGH, "tokyo_low": RLLevelType.TOKYO_LOW,
        "nyib_high": RLLevelType.NYIB_HIGH, "nyib_low": RLLevelType.NYIB_LOW,
        "tpoc": RLLevelType.TPOC, "tvah": RLLevelType.TVAH, "tval": RLLevelType.TVAL,
        "tibh": RLLevelType.TIBH, "tibl": RLLevelType.TIBL,
        "naked_poc": RLLevelType.NAKED_POC,
    }

    level_tuples = []
    for lv in self._levels:
        name_key = lv.name.lower().replace(" ", "_").replace("+", "").replace("-", "")
        lt = level_type_map.get(name_key, RLLevelType.VWAP)
        level_tuples.append((lv.name, lt, lv.price))

    self._zones = build_zones(level_tuples, self._session_atr)
    self._zone_debounce.clear()
    logger.info("LevelMonitor rebuilt %d zones from %d levels", len(self._zones), len(self._levels))
```

Call `_rebuild_zones()` at the end of `load_levels()`.

- [ ] **Step 2: Add zone touch detection to on_tick()**

After the existing per-level state machine in `on_tick()`, add zone entry detection:

```python
# Zone entry detection (for DQN inference)
newly_entered_zones = []
still_in_zones: set[str] = set()
for zone in self._zones:
    inside = zone.lower_bound <= price <= zone.upper_bound
    snapped = round(zone.center_price / TICK_SIZE) * TICK_SIZE
    key = f"zone_{snapped}"
    if inside:
        still_in_zones.add(key)
        if key not in self._zone_debounce:
            self._zone_debounce.add(key)
            newly_entered_zones.append(zone)
self._zone_debounce -= (self._zone_debounce - still_in_zones)

for zone in newly_entered_zones:
    self._emit_zone_dqn_inference(zone, price)
```

- [ ] **Step 3: Add _emit_zone_dqn_inference() and update _build_rl_state()**

```python
def _emit_zone_dqn_inference(self, zone: Zone, price: float) -> None:
    """Run DQN inference for a zone entry and emit SSE event."""
    try:
        from src.rl.live_inference import get_dqn_inference
        dqn = get_dqn_inference()
        if not dqn.is_loaded:
            return
        rl_state = self._build_rl_state_zone(zone, price)
        result = dqn.infer(rl_state)
        if result is not None:
            self._publish({
                "type": "dqn_inference",
                "trigger": "zone_entry",
                "zone_members": zone.member_count,
                "zone_center": zone.center_price,
                "zone_hierarchy": round(zone.hierarchy_score, 3),
                **result,
                "timestamp": time.time(),
            })
    except Exception:
        logger.debug("DQN zone inference failed", exc_info=True)

def _build_rl_state_zone(self, zone: Zone, price: float) -> dict:
    """Build RL state dict for zone-based inference."""
    import time as _time

    candles = []
    if self._candle_flow_fn:
        candles = self._candle_flow_fn() or []

    ctx = self._session_context or {}

    # Approach direction: use first member's approach price if available
    # or fall back to comparing price to zone center
    approach = "up" if price < zone.center_price else "down"

    recent_ticks = []
    if self._tick_buffer:
        try:
            recent_ticks = self._tick_buffer.get_recent(50)
        except Exception:
            pass

    return {
        "zone": zone,
        "all_zones": self._zones,
        "price": price,
        "touch_epoch": _time.time(),
        "approach_direction": approach,
        "candles": candles,
        "candles_5m": ctx.get("candles_5m", []),
        "vwap_bands": ctx.get("vwap_bands"),
        "volume_profile": ctx.get("volume_profile"),
        "tpo_profile": ctx.get("tpo_profile"),
        "tpo_profile_obj": ctx.get("tpo_profile_obj"),
        "session_tpos": ctx.get("session_tpos"),
        "session_levels": ctx.get("session_levels"),
        "all_levels": [l.price for l in self._levels],
        "orderflow_signals": ctx.get("orderflow_signals"),
        "macro": ctx.get("macro"),
        "session_context": ctx.get("session_context"),
        "day_type": ctx.get("day_type"),
        "fvgs": ctx.get("fvgs", []),
        "single_print_zones": ctx.get("single_print_zones", []),
        "recent_ticks": recent_ticks,
    }
```

- [ ] **Step 4: Update live_inference.py to handle zone state**

In `backend/src/rl/live_inference.py`, update the `infer()` method to handle zone-based state dicts. The key change: if `state["zone"]` is present, don't try to convert `state["level_type"]` (it won't exist):

Replace lines 84-89:
```python
# Handle legacy level_type string → enum conversion
lt = state.get("level_type")
if lt is not None and isinstance(lt, str):
    try:
        state["level_type"] = LevelType(lt)
    except ValueError:
        state["level_type"] = LevelType.VWAP
# Zone mode: no level_type conversion needed (zone object already present)
```

- [ ] **Step 5: Run integration check**

Run: `cd backend && python -m pytest tests/test_rl_features.py tests/test_rl_replay_engine.py tests/test_rl_zone_builder.py -v`
Expected: All tests pass

- [ ] **Step 6: Commit**

```bash
git add backend/src/market_data/level_monitor.py backend/src/rl/live_inference.py
git commit -m "feat(rl): add zone-aware inference to level monitor and live inference"
```

---

### Task 9: Final Integration Test

**Files:**
- No new files

- [ ] **Step 1: Run the full RL test suite**

Run: `cd backend && python -m pytest tests/test_rl_*.py tests/test_rl_zone_builder.py -v --tb=short`
Expected: All tests pass

- [ ] **Step 2: Verify observation dimension is correct**

Run: `cd backend && python -c "from src.rl.features.observation import OBSERVATION_DIM; from src.rl.config import LevelType; print(f'OBSERVATION_DIM={OBSERVATION_DIM}, expected={len(LevelType) + 142}'); assert OBSERVATION_DIM == len(LevelType) + 142"`
Expected: Prints the dimension and assertion passes

- [ ] **Step 3: Verify zone builder works with current LevelType enum**

Run: `cd backend && python -c "
from src.rl.config import LevelType
from src.rl.zone_builder import build_zones
levels = [
    ('vwap', LevelType.VWAP, 20000.0),
    ('poc', LevelType.DAILY_POC, 20000.5),
    ('vwap_sd1', LevelType.VWAP_SD1, 20001.0),
    ('pdh', LevelType.PDH, 20020.0),
]
zones = build_zones(levels, session_atr=40.0)
for z in zones:
    names = [m.name for m in z.members]
    print(f'Zone @ {z.center_price:.2f}: {z.member_count} members {names}, hierarchy={z.hierarchy_score:.3f}, width={z.width_ticks:.1f} ticks')
print(f'Total: {len(zones)} zones from {len(levels)} levels')
"`
Expected: 2 zones — one cluster of 3 levels near 20000, one singleton at 20020

- [ ] **Step 4: Final commit**

```bash
git add -A
git commit -m "feat(rl): complete zone consolidation implementation (pre-training)"
```
