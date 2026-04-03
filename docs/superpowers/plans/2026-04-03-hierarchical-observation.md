# Hierarchical Observation Architecture Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restructure the RL observation vector into a two-stage architecture — a slow Narrative Layer (macro/structure/TPO/AMT compressed into 15 named signals + 8 setup probabilities) feeding a fast Trigger Layer (micro/orderflow/candles/zone + narrative context).

**Architecture:** Stage 1 (Narrative GBT) runs every 30min or on structural events, producing 23 interpretable signals. Stage 2 (Trigger GBT + DQN) fires at each zone touch using those signals + micro features + 10 high-importance structural passthrough features. Total trigger observation: ~141 dims (down from 292).

**Tech Stack:** Python 3.10, NumPy, LightGBM, PyTorch, HDBSCAN, existing RL pipeline infrastructure.

---

## File Map

| Action | File | Responsibility |
|--------|------|---------------|
| CREATE | `backend/src/rl/labeling/setup_types.py` | SetupType enum + priority resolution |
| CREATE | `backend/src/rl/labeling/setup_labeler.py` | Rule-based labels for 5 mechanical setups |
| CREATE | `backend/src/rl/labeling/setup_clusterer.py` | HDBSCAN clustering for 3 soft setups |
| CREATE | `backend/src/rl/labeling/__init__.py` | Package init |
| CREATE | `backend/src/rl/features/narrative_features.py` | 15 named narrative signals from slow features |
| CREATE | `backend/src/rl/features/passthrough_features.py` | Top 10 structure/TPO passthrough selection |
| CREATE | `backend/src/rl/features/trigger_features.py` | Trigger observation assembler (~141 dims) |
| CREATE | `backend/src/rl/agent/narrative_gbt.py` | Narrative GBT (day_type, regime, setup_probs) |
| CREATE | `backend/src/rl/agent/trigger_gbt.py` | Trigger GBT (direction, expected R, etc.) |
| CREATE | `backend/tests/test_setup_labeler.py` | Tests for setup labeling |
| CREATE | `backend/tests/test_narrative_features.py` | Tests for narrative feature extraction |
| CREATE | `backend/tests/test_trigger_features.py` | Tests for trigger observation assembly |
| MODIFY | `backend/src/rl/config.py` | New dims, SetupType import, narrative triggers |
| MODIFY | `backend/src/rl/features/observation.py` | Add `build_narrative()` and `build_trigger()` alongside existing `build_observation()` |
| MODIFY | `backend/src/rl/cli.py` | New CLI commands: `label-setups`, `train-narrative-gbt`, `train-trigger-gbt`; update `replay` and `train` |
| MODIFY | `backend/src/rl/live_inference.py` | Two-stage inference path |
| MODIFY | `backend/src/rl/session_manager.py` | Narrative update on structural events |
| MODIFY | `backend/scripts/rl_train_pipeline.sh` | New 10-step pipeline |

---

### Task 1: Setup Type Definitions

**Files:**
- Create: `backend/src/rl/labeling/__init__.py`
- Create: `backend/src/rl/labeling/setup_types.py`

- [ ] **Step 1: Create the labeling package**

```python
# backend/src/rl/labeling/__init__.py
```

- [ ] **Step 2: Write SetupType enum and priority resolution**

```python
# backend/src/rl/labeling/setup_types.py
"""Setup type taxonomy for AMT-based trade classification."""
from __future__ import annotations

from enum import Enum


class SetupType(str, Enum):
    """The 8 core setups the model learns to recognize."""
    # Rule-based (mechanical definitions)
    FAILED_AUCTION = "failed_auction"
    LOOK_ABOVE_BELOW_FAIL = "look_above_below_fail"
    IB_EXTENSION = "ib_extension"
    GAP_FILL = "gap_fill"
    SINGLE_PRINT_FILL = "single_print_fill"
    # Cluster-derived (softer definitions)
    ROTATION_TO_POC = "rotation_to_poc"
    EXCESS_TEST = "excess_test"
    BALANCE_BREAK = "balance_break"
    # Fallback
    UNKNOWN = "unknown"


# Priority order for conflict resolution (highest first)
SETUP_PRIORITY = [
    SetupType.FAILED_AUCTION,
    SetupType.LOOK_ABOVE_BELOW_FAIL,
    SetupType.IB_EXTENSION,
    SetupType.GAP_FILL,
    SetupType.SINGLE_PRINT_FILL,
    SetupType.ROTATION_TO_POC,
    SetupType.EXCESS_TEST,
    SetupType.BALANCE_BREAK,
]

NUM_SETUP_TYPES = len(SetupType) - 1  # exclude UNKNOWN
```

- [ ] **Step 3: Commit**

```bash
git add backend/src/rl/labeling/
git commit -m "feat(rl): add SetupType enum and priority resolution"
```

---

### Task 2: Rule-Based Setup Labeler

**Files:**
- Create: `backend/src/rl/labeling/setup_labeler.py`
- Create: `backend/tests/test_setup_labeler.py`

- [ ] **Step 1: Write failing tests for each rule-based setup**

```python
# backend/tests/test_setup_labeler.py
"""Tests for rule-based setup labeling."""
import numpy as np
import pytest
from datetime import datetime, time
from zoneinfo import ZoneInfo

from src.rl.labeling.setup_types import SetupType
from src.rl.labeling.setup_labeler import label_episode

ET = ZoneInfo("US/Eastern")


def _make_episode(
    zone_types: list[str],
    approach_dir: str = "up",
    reward_cont: float = 0.5,
    reward_rev: float = -0.3,
    touch_time: time = time(11, 0),
    price_vs_value: float = 0.0,  # -1 below VAL, 0 inside, +1 above VAH
    has_gap: bool = False,
    has_single_print: bool = False,
    ib_closed: bool = True,
    delta_ratio: float = 0.3,
    forward_reversal_speed: float = 0.0,  # ticks reversed in 60s
):
    """Build a minimal episode dict for labeling."""
    return {
        "zone_types": zone_types,
        "approach_direction": approach_dir,
        "reward_cont": reward_cont,
        "reward_rev": reward_rev,
        "touch_time_et": datetime(2025, 1, 15, touch_time.hour, touch_time.minute, tzinfo=ET),
        "price_vs_value": price_vs_value,
        "has_gap": has_gap,
        "has_single_print": has_single_print,
        "ib_closed": ib_closed,
        "delta_ratio": delta_ratio,
        "forward_reversal_speed": forward_reversal_speed,
    }


def test_failed_auction_at_pdh():
    ep = _make_episode(
        zone_types=["pdh"],
        approach_dir="up",
        reward_cont=-0.5,
        reward_rev=1.2,
        forward_reversal_speed=8.0,
    )
    assert label_episode(ep) == SetupType.FAILED_AUCTION


def test_ib_extension():
    ep = _make_episode(
        zone_types=["nyib_high"],
        approach_dir="up",
        reward_cont=1.5,
        reward_rev=-0.3,
        touch_time=time(11, 30),
        ib_closed=True,
        delta_ratio=0.7,
    )
    assert label_episode(ep) == SetupType.IB_EXTENSION


def test_gap_fill():
    ep = _make_episode(
        zone_types=["daily_vah"],
        approach_dir="down",
        touch_time=time(10, 15),
        has_gap=True,
        price_vs_value=0.8,
    )
    assert label_episode(ep) == SetupType.GAP_FILL


def test_single_print_fill():
    ep = _make_episode(
        zone_types=["naked_poc"],
        has_single_print=True,
    )
    assert label_episode(ep) == SetupType.SINGLE_PRINT_FILL


def test_look_above_and_fail():
    ep = _make_episode(
        zone_types=["daily_vah"],
        approach_dir="up",
        reward_cont=-0.4,
        reward_rev=0.9,
        price_vs_value=1.0,
        forward_reversal_speed=6.0,
    )
    assert label_episode(ep) == SetupType.LOOK_ABOVE_BELOW_FAIL


def test_unknown_when_no_rule_matches():
    ep = _make_episode(
        zone_types=["vwap"],
        approach_dir="up",
        reward_cont=0.1,
        reward_rev=0.1,
    )
    assert label_episode(ep) == SetupType.UNKNOWN


def test_priority_failed_auction_over_look_above():
    """Failed auction at VAH should be FAILED_AUCTION, not LOOK_ABOVE_FAIL."""
    ep = _make_episode(
        zone_types=["daily_vah", "pdh"],
        approach_dir="up",
        reward_cont=-0.5,
        reward_rev=1.5,
        price_vs_value=1.0,
        forward_reversal_speed=10.0,
    )
    assert label_episode(ep) == SetupType.FAILED_AUCTION
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd backend && pytest tests/test_setup_labeler.py -v
```
Expected: FAIL — `ModuleNotFoundError: No module named 'src.rl.labeling.setup_labeler'`

- [ ] **Step 3: Implement the setup labeler**

```python
# backend/src/rl/labeling/setup_labeler.py
"""Rule-based setup labeling for 5 mechanical setups.

Labels episodes based on structural context at the zone touch.
Each rule checks zone composition, approach direction, outcome,
and session context to classify the setup.
"""
from __future__ import annotations

from datetime import time

from .setup_types import SetupType

# Session extreme level types that qualify for failed auctions
_SESSION_EXTREMES = {
    "pdh", "pdl", "nyib_high", "nyib_low",
    "tokyo_high", "tokyo_low",
    "daily_swing_high", "daily_swing_low",
    "weekly_swing_high", "weekly_swing_low",
    "monthly_swing_high", "monthly_swing_low",
}

# Value area edge types for look-above/below-fail
_VA_EDGES = {"daily_vah", "daily_val", "tvah", "tval"}

_IB_CLOSE = time(10, 30)
_GAP_CUTOFF = time(12, 0)  # gap fills only in first 2 hours


def _is_failed_auction(ep: dict) -> bool:
    """Price probed beyond session extreme, failed to attract follow-through."""
    zone_types = set(ep["zone_types"])
    if not zone_types & _SESSION_EXTREMES:
        return False
    # Reversal must be the better outcome
    if ep["reward_rev"] <= ep["reward_cont"]:
        return False
    # Price must have reversed quickly (speed > 5 ticks in 60s)
    if ep.get("forward_reversal_speed", 0) < 5.0:
        return False
    return True


def _is_look_above_below_fail(ep: dict) -> bool:
    """Price pushed outside value area, rejected back inside."""
    zone_types = set(ep["zone_types"])
    if not zone_types & _VA_EDGES:
        return False
    # Must be at or beyond value area edge
    pvv = ep.get("price_vs_value", 0)
    if abs(pvv) < 0.8:
        return False
    # Reversal must be better
    if ep["reward_rev"] <= ep["reward_cont"]:
        return False
    # Quick reversal
    if ep.get("forward_reversal_speed", 0) < 4.0:
        return False
    return True


def _is_ib_extension(ep: dict) -> bool:
    """Breakout from initial balance with initiative activity."""
    zone_types = set(ep["zone_types"])
    if not zone_types & {"nyib_high", "nyib_low"}:
        return False
    if not ep.get("ib_closed", False):
        return False
    t = ep.get("touch_time_et")
    if t and t.time() < _IB_CLOSE:
        return False
    # Continuation must be better (breakout succeeds)
    if ep["reward_cont"] <= ep["reward_rev"]:
        return False
    # Strong initiative flow
    if abs(ep.get("delta_ratio", 0)) < 0.5:
        return False
    return True


def _is_gap_fill(ep: dict) -> bool:
    """Opening gap, price moving back to fill it."""
    if not ep.get("has_gap", False):
        return False
    t = ep.get("touch_time_et")
    if t and t.time() > _GAP_CUTOFF:
        return False
    return True


def _is_single_print_fill(ep: dict) -> bool:
    """Price returning to fill single-print zone or naked POC."""
    if ep.get("has_single_print", False):
        return True
    zone_types = set(ep["zone_types"])
    return "naked_poc" in zone_types


def label_episode(ep: dict) -> SetupType:
    """Label a single episode with its setup type.

    Applies rules in priority order. Returns SetupType.UNKNOWN
    if no rule matches (candidate for cluster labeling).
    """
    # Priority order: most specific first
    if _is_failed_auction(ep):
        return SetupType.FAILED_AUCTION
    if _is_look_above_below_fail(ep):
        return SetupType.LOOK_ABOVE_BELOW_FAIL
    if _is_ib_extension(ep):
        return SetupType.IB_EXTENSION
    if _is_gap_fill(ep):
        return SetupType.GAP_FILL
    if _is_single_print_fill(ep):
        return SetupType.SINGLE_PRINT_FILL
    return SetupType.UNKNOWN
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd backend && pytest tests/test_setup_labeler.py -v
```
Expected: All 7 tests PASS

- [ ] **Step 5: Commit**

```bash
git add backend/src/rl/labeling/setup_labeler.py backend/tests/test_setup_labeler.py
git commit -m "feat(rl): rule-based setup labeler for 5 mechanical AMT setups"
```

---

### Task 3: Setup Clusterer for Soft Setups

**Files:**
- Create: `backend/src/rl/labeling/setup_clusterer.py`

- [ ] **Step 1: Implement HDBSCAN-based clustering for unlabeled episodes**

```python
# backend/src/rl/labeling/setup_clusterer.py
"""Cluster-based setup labeling for soft setups (rotation, excess test, balance break).

Runs HDBSCAN on narrative features of episodes that didn't match any rule-based setup.
Clusters are then mapped to setup types based on their centroid characteristics.
"""
from __future__ import annotations

import logging

import numpy as np

from .setup_types import SetupType

log = logging.getLogger(__name__)

# Cluster-to-setup mapping thresholds
_POC_ZONE_TYPES = {"daily_poc", "weekly_poc", "monthly_poc", "tpoc"}
_EXCESS_ZONE_TYPES = {"naked_poc"}


def cluster_and_label(
    observations: np.ndarray,
    zone_types_list: list[list[str]],
    rewards_cont: np.ndarray,
    rewards_rev: np.ndarray,
    price_vs_value: np.ndarray,
    balance_widths: np.ndarray,
    min_cluster_size: int = 200,
) -> np.ndarray:
    """Cluster unlabeled episodes and assign soft setup labels.

    Args:
        observations: (N, obs_dim) observation vectors (narrative features subset).
        zone_types_list: List of zone type string lists per episode.
        rewards_cont: (N,) continuation rewards.
        rewards_rev: (N,) reversal rewards.
        price_vs_value: (N,) price position relative to value area.
        balance_widths: (N,) developing balance width.
        min_cluster_size: HDBSCAN minimum cluster size.

    Returns:
        (N,) array of SetupType string values.
    """
    try:
        from hdbscan import HDBSCAN
    except ImportError:
        log.warning("hdbscan not installed — falling back to heuristic labeling")
        return _heuristic_label(zone_types_list, rewards_cont, rewards_rev,
                                price_vs_value, balance_widths)

    n = len(observations)
    clusterer = HDBSCAN(min_cluster_size=min_cluster_size, min_samples=50)
    cluster_ids = clusterer.fit_predict(observations)

    labels = np.full(n, SetupType.UNKNOWN.value, dtype=object)

    for cid in set(cluster_ids):
        if cid == -1:
            continue
        mask = cluster_ids == cid
        labels[mask] = _classify_cluster(
            zone_types_list=[zone_types_list[i] for i in np.where(mask)[0]],
            rewards_cont=rewards_cont[mask],
            rewards_rev=rewards_rev[mask],
            price_vs_value=price_vs_value[mask],
            balance_widths=balance_widths[mask],
        )

    log.info("Clustered %d episodes: %d clusters, %d noise",
             n, len(set(cluster_ids) - {-1}), (cluster_ids == -1).sum())
    return labels


def _classify_cluster(
    zone_types_list: list[list[str]],
    rewards_cont: np.ndarray,
    rewards_rev: np.ndarray,
    price_vs_value: np.ndarray,
    balance_widths: np.ndarray,
) -> str:
    """Map a cluster to a setup type based on centroid characteristics."""
    n = len(rewards_cont)

    # Check for POC presence
    poc_count = sum(1 for zt in zone_types_list if set(zt) & _POC_ZONE_TYPES)
    poc_ratio = poc_count / max(n, 1)

    # Check for excess/naked levels
    excess_count = sum(1 for zt in zone_types_list if set(zt) & _EXCESS_ZONE_TYPES)
    excess_ratio = excess_count / max(n, 1)

    # Mean reversion signal: price was at edge, reward favors return to center
    avg_pvv = np.abs(price_vs_value).mean()
    rev_better = (rewards_rev > rewards_cont).mean()

    # Balance break: wide balance + continuation wins
    avg_balance = balance_widths.mean()
    cont_better = (rewards_cont > rewards_rev).mean()

    # Classification logic
    if poc_ratio > 0.3 and avg_pvv > 0.5 and rev_better > 0.55:
        return SetupType.ROTATION_TO_POC.value
    if excess_ratio > 0.2:
        return SetupType.EXCESS_TEST.value
    if avg_balance > 0.4 and cont_better > 0.55:
        return SetupType.BALANCE_BREAK.value

    return SetupType.UNKNOWN.value


def _heuristic_label(
    zone_types_list: list[list[str]],
    rewards_cont: np.ndarray,
    rewards_rev: np.ndarray,
    price_vs_value: np.ndarray,
    balance_widths: np.ndarray,
) -> np.ndarray:
    """Simple heuristic fallback when HDBSCAN is not available."""
    n = len(rewards_cont)
    labels = np.full(n, SetupType.UNKNOWN.value, dtype=object)

    for i in range(n):
        zt = set(zone_types_list[i])
        if zt & _POC_ZONE_TYPES and abs(price_vs_value[i]) > 0.5:
            labels[i] = SetupType.ROTATION_TO_POC.value
        elif zt & _EXCESS_ZONE_TYPES:
            labels[i] = SetupType.EXCESS_TEST.value
        elif balance_widths[i] > 0.4 and rewards_cont[i] > rewards_rev[i]:
            labels[i] = SetupType.BALANCE_BREAK.value

    return labels
```

- [ ] **Step 2: Commit**

```bash
git add backend/src/rl/labeling/setup_clusterer.py
git commit -m "feat(rl): HDBSCAN setup clusterer for soft AMT setups"
```

---

### Task 4: Narrative Feature Extractor

**Files:**
- Create: `backend/src/rl/features/narrative_features.py`
- Create: `backend/tests/test_narrative_features.py`

- [ ] **Step 1: Write failing tests**

```python
# backend/tests/test_narrative_features.py
"""Tests for narrative feature extraction — 15 named signals."""
import numpy as np
import pytest

from src.rl.features.narrative_features import extract_narrative_features, NARRATIVE_DIM, NARRATIVE_NAMES


def test_output_shape():
    state = {}  # empty state → zeros
    result = extract_narrative_features(state)
    assert result.shape == (NARRATIVE_DIM,)
    assert result.dtype == np.float32


def test_dimension_matches_names():
    assert len(NARRATIVE_NAMES) == NARRATIVE_DIM


def test_regime_score_from_macro():
    state = {
        "macro": {"vix": 18.0, "vix_avg": 20.0, "dxy": 104.0, "us10y": 4.2},
    }
    result = extract_narrative_features(state)
    idx = NARRATIVE_NAMES.index("regime_score")
    # Low VIX relative to avg → risk-on → positive
    assert result[idx] > 0


def test_session_phase_progression():
    from datetime import time
    for t, expected_phase in [
        (time(8, 0), "pre_ib"),
        (time(10, 0), "ib_forming"),
        (time(11, 0), "post_ib_early"),
        (time(14, 0), "post_ib_late"),
        (time(15, 45), "close"),
    ]:
        state = {"session_context": {"time_et": t, "ib_closed": t >= time(10, 30)}}
        result = extract_narrative_features(state)
        idx = NARRATIVE_NAMES.index("session_phase")
        # Each phase maps to a different ordinal value
        assert -1.0 <= result[idx] <= 1.0


def test_all_signals_bounded():
    """All narrative signals should be in [-1, 1] range."""
    state = {
        "macro": {"vix": 30.0, "vix_avg": 20.0, "dxy": 110.0, "us10y": 5.0},
        "session_context": {"time_et": None},
    }
    result = extract_narrative_features(state)
    assert np.all(result >= -1.0) and np.all(result <= 1.0)
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd backend && pytest tests/test_narrative_features.py -v
```
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement narrative feature extractor**

```python
# backend/src/rl/features/narrative_features.py
"""Narrative feature extraction — 15 named signals from slow-moving features.

These signals update every 30 minutes or on structural events, compressing
macro/structure/TPO/AMT into an interpretable session context that the
trigger layer consumes.

Signal layout (15 dims):
  Market Regime (3):
    0  regime_score          — risk-on/off from VIX/DXY/yields
    1  htf_trend             — weekly/daily swing alignment
    2  volatility_regime     — low/normal/high ATR percentile

  Session Context (7):
    3  day_type              — trend_up/down, normal, normal_var, neutral
    4  opening_type          — OTD/ORR/OD ordinal
    5  ib_type               — wide/narrow/normal
    6  value_migration       — today's VA vs yesterday's
    7  session_phase         — pre_ib / ib_forming / post_ib_early / late / close
    8  initiative_direction  — buyers vs sellers in control
    9  balance_width         — developing balance range / ATR

  Structural Position (5):
    10 price_vs_value        — below VAL (-1) to above VAH (+1)
    11 price_vs_poc          — signed distance to POC
    12 price_vs_ib           — below IB (-1) to above IB (+1)
    13 trend_alignment       — daily/weekly/monthly agreement
    14 excess_nearby         — unfilled excess within 1 ATR
"""
from __future__ import annotations

from datetime import time

import numpy as np

from ..config import TICK_SIZE

NARRATIVE_DIM = 15

NARRATIVE_NAMES = [
    "regime_score", "htf_trend", "volatility_regime",
    "day_type", "opening_type", "ib_type", "value_migration",
    "session_phase", "initiative_direction", "balance_width",
    "price_vs_value", "price_vs_poc", "price_vs_ib",
    "trend_alignment", "excess_nearby",
]

_DIST_NORM = 200.0

# Day type ordinals
_DAY_TYPE_MAP = {
    "trend": 1.0, "trend_up": 1.0, "trend_down": -1.0,
    "normal_variation": 0.5, "normal": 0.0,
    "neutral": -0.25, "non_trend": -0.5,
    "double_distribution": 0.25,
}

# Opening type ordinals
_OPENING_MAP = {"OD": 1.0, "OTD": 0.5, "ORR": -0.5, "OA": 0.0}


def extract_narrative_features(state: dict) -> np.ndarray:
    """Extract 15 named narrative signals from session state.

    Consumes the same state dict as build_observation() but only
    reads slow-moving context fields.
    """
    out = np.zeros(NARRATIVE_DIM, dtype=np.float32)

    # --- Market Regime (0-2) ---
    macro = state.get("macro") or {}
    vix = macro.get("vix", 0)
    vix_avg = macro.get("vix_avg", 20)
    dxy = macro.get("dxy", 0)
    us10y = macro.get("us10y", 0)

    # regime_score: low VIX relative to avg = risk-on (+), high = risk-off (-)
    if vix_avg > 0:
        out[0] = np.clip((vix_avg - vix) / max(vix_avg, 1) * 2, -1, 1)

    # htf_trend: from swing structure
    swing = state.get("swing_structure")
    if swing:
        trends = []
        for tf in ["daily", "weekly", "monthly"]:
            t = getattr(swing, f"{tf}_trend", None)
            if t:
                trends.append(_DAY_TYPE_MAP.get(t, 0))
        if trends:
            out[1] = np.clip(np.mean(trends), -1, 1)

    # volatility_regime: from session context ATR percentile
    ctx = state.get("session_context") or {}
    out[2] = np.clip(ctx.get("atr_percentile", 0.5), 0, 1)

    # --- Session Context (3-9) ---
    # day_type from AMT dynamics
    amt_dyn = state.get("amt_dynamics") or {}
    dt = amt_dyn.get("developing_day_type", "")
    if isinstance(dt, str):
        out[3] = _DAY_TYPE_MAP.get(dt, 0)
    elif isinstance(dt, (int, float)):
        out[3] = np.clip(float(dt), -1, 1)

    # opening_type
    tpo = state.get("session_tpos")
    if tpo and hasattr(tpo, "ny") and tpo.ny:
        ot = getattr(tpo.ny, "opening_type", "")
        out[4] = _OPENING_MAP.get(ot, 0)

    # ib_type: IB range vs avg
    sl = state.get("session_levels")
    if sl and sl.ib_high and sl.ib_low:
        ib_range = (sl.ib_high - sl.ib_low) / TICK_SIZE
        avg_ib = ctx.get("avg_ib_range", 40)
        if avg_ib > 0:
            ratio = ib_range / avg_ib
            if ratio > 1.3:
                out[5] = 1.0  # wide
            elif ratio < 0.7:
                out[5] = -1.0  # narrow
            else:
                out[5] = 0.0  # normal

    # value_migration
    out[6] = np.clip(ctx.get("value_migration", 0), -1, 1)

    # session_phase
    t = ctx.get("time_et")
    ib_closed = ctx.get("ib_closed", False)
    if t:
        if isinstance(t, time):
            t_val = t
        else:
            t_val = t.time() if hasattr(t, "time") else time(12, 0)
        if t_val < time(9, 30):
            out[7] = -1.0  # pre_ib
        elif not ib_closed and t_val < time(10, 30):
            out[7] = -0.5  # ib_forming
        elif t_val < time(12, 30):
            out[7] = 0.0  # post_ib_early
        elif t_val < time(15, 30):
            out[7] = 0.5  # post_ib_late
        else:
            out[7] = 1.0  # close

    # initiative_direction
    init = amt_dyn.get("initiative_ratio", 0.5)
    resp = amt_dyn.get("responsive_ratio", 0.5)
    out[8] = np.clip(init - resp, -1, 1)

    # balance_width
    out[9] = np.clip(amt_dyn.get("balance_width", 0) / 200.0, 0, 1)

    # --- Structural Position (10-14) ---
    price = state.get("price", 0)
    vp = state.get("volume_profile")
    if vp and vp.vah and vp.val:
        va_range = max(vp.vah - vp.val, TICK_SIZE)
        if price > vp.vah:
            out[10] = min((price - vp.vah) / va_range + 0.5, 1.0)
        elif price < vp.val:
            out[10] = max((price - vp.val) / va_range - 0.5, -1.0)
        else:
            out[10] = (price - vp.val) / va_range - 0.5  # -0.5 to +0.5 inside VA

    if vp and vp.poc:
        out[11] = np.clip((price - vp.poc) / TICK_SIZE / _DIST_NORM, -1, 1)

    if sl and sl.ib_high and sl.ib_low:
        ib_mid = (sl.ib_high + sl.ib_low) / 2
        ib_range = max(sl.ib_high - sl.ib_low, TICK_SIZE)
        out[12] = np.clip((price - ib_mid) / ib_range, -1, 1)

    # trend_alignment: reuse swing structure
    if swing and hasattr(swing, "trend_alignment"):
        out[13] = np.clip(swing.trend_alignment, -1, 1)

    # excess_nearby
    single_prints = state.get("single_print_zones") or []
    if single_prints and price:
        for sp in single_prints:
            dist = abs(price - sp.get("price", price)) / TICK_SIZE
            if dist < _DIST_NORM:
                out[14] = 1.0
                break

    return out
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd backend && pytest tests/test_narrative_features.py -v
```
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/src/rl/features/narrative_features.py backend/tests/test_narrative_features.py
git commit -m "feat(rl): narrative feature extractor — 15 named session signals"
```

---

### Task 5: Passthrough Feature Selector

**Files:**
- Create: `backend/src/rl/features/passthrough_features.py`

- [ ] **Step 1: Implement top-10 structural passthrough selection**

Based on the GBT v3 feature importance analysis, these are the highest-importance structure/TPO features that pass through to the trigger layer:

```python
# backend/src/rl/features/passthrough_features.py
"""Structural passthrough — top 10 raw features that bypass narrative compression.

These are the highest-importance structure/TPO/AMT-dynamics features from
GBT v3's feature importance analysis. They carry specific distance/position
information that would be lost in the narrative compression.
"""
from __future__ import annotations

import numpy as np

PASSTHROUGH_DIM = 10

# Indices into the 276-dim base observation vector (zone mode)
# Mapped from the GBT importance analysis:
#   struct_3 (imp 473) = obs index 31+21+3 = 55  → dist to swing high
#   struct_4 (imp 374) = obs index 56               → dist to swing low
#   struct_2 (imp 341) = obs index 54               → VWAP position
#   struct_5 (imp 322) = obs index 57               → IB distance
#   struct_0 (imp 296) = obs index 52               → price vs VWAP in SD
#   tpo top 3 by importance (from session TPO)
#   amtdyn top 2 by importance
_PASSTHROUGH_INDICES = [
    52,   # struct_0: price_vs_vwap (SD units)
    54,   # struct_2: VWAP position
    55,   # struct_3: dist to swing high
    56,   # struct_4: dist to swing low
    57,   # struct_5: IB distance
    117,  # tpo: NY price_vs_poc (most important TPO)
    118,  # tpo: NY price_vs_vah
    119,  # tpo: NY price_vs_val
    157,  # amtdyn_4: developing_day_type
    163,  # amtdyn_12: poc_migration_speed (from AMT dynamics at index 12)
]

PASSTHROUGH_NAMES = [
    "pt_price_vs_vwap_sd", "pt_vwap_position", "pt_dist_swing_high",
    "pt_dist_swing_low", "pt_ib_distance", "pt_ny_price_vs_poc",
    "pt_ny_price_vs_vah", "pt_ny_price_vs_val",
    "pt_developing_day_type", "pt_poc_migration_speed",
]


def extract_passthrough(base_observation: np.ndarray) -> np.ndarray:
    """Extract the 10 highest-importance structural features from a base observation.

    Args:
        base_observation: The full 276-dim observation vector.

    Returns:
        10-dim float32 array of passthrough features.
    """
    out = np.zeros(PASSTHROUGH_DIM, dtype=np.float32)
    for i, idx in enumerate(_PASSTHROUGH_INDICES):
        if idx < len(base_observation):
            out[i] = base_observation[idx]
    return out
```

- [ ] **Step 2: Commit**

```bash
git add backend/src/rl/features/passthrough_features.py
git commit -m "feat(rl): structural passthrough — top 10 features bypass narrative"
```

---

### Task 6: Trigger Feature Assembler

**Files:**
- Create: `backend/src/rl/features/trigger_features.py`
- Create: `backend/tests/test_trigger_features.py`

- [ ] **Step 1: Write failing tests**

```python
# backend/tests/test_trigger_features.py
"""Tests for trigger observation assembly."""
import numpy as np

from src.rl.features.trigger_features import (
    build_trigger_observation,
    TRIGGER_DIM,
    TRIGGER_SEGMENTS,
)
from src.rl.features.narrative_features import NARRATIVE_DIM


def test_output_shape():
    narrative = np.zeros(NARRATIVE_DIM, dtype=np.float32)
    setup_probs = np.zeros(8, dtype=np.float32)
    state = {}
    base_obs = np.zeros(276, dtype=np.float32)
    result = build_trigger_observation(narrative, setup_probs, state, base_obs)
    assert result.shape == (TRIGGER_DIM,)
    assert result.dtype == np.float32


def test_segments_sum_to_total():
    total = sum(TRIGGER_SEGMENTS.values())
    assert total == TRIGGER_DIM


def test_narrative_signals_at_front():
    narrative = np.ones(NARRATIVE_DIM, dtype=np.float32) * 0.5
    setup_probs = np.ones(8, dtype=np.float32) * 0.3
    state = {}
    base_obs = np.zeros(276, dtype=np.float32)
    result = build_trigger_observation(narrative, setup_probs, state, base_obs)
    # First NARRATIVE_DIM values should be the narrative signals
    np.testing.assert_array_almost_equal(result[:NARRATIVE_DIM], narrative)
    # Next 8 should be setup probs
    np.testing.assert_array_almost_equal(result[NARRATIVE_DIM:NARRATIVE_DIM + 8], setup_probs)
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd backend && pytest tests/test_trigger_features.py -v
```
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement trigger observation assembler**

```python
# backend/src/rl/features/trigger_features.py
"""Trigger observation assembler — fast features for the DQN decision layer.

Assembles the observation vector that the trigger DQN consumes at each
zone touch. Combines narrative context (pre-computed) with real-time
micro/orderflow/candle/zone features.

Layout (~141 dims):
    narrative signals      15
    setup probabilities     8
    structural passthrough 10
    micro features         20
    orderflow              21
    candles                15
    zone features           4
    zone confluence         5
    zone composition       31
    approach direction      1
    trigger GBT forecast    8
    execution passthrough   3
    ─────────────────────────
    total                 141
"""
from __future__ import annotations

import numpy as np

from ..config import LevelType, TICK_SIZE
from .narrative_features import NARRATIVE_DIM
from .passthrough_features import PASSTHROUGH_DIM, extract_passthrough
from .micro_features import extract_micro_features
from .orderflow_features import extract_orderflow_features
from .level_features import encode_zone_composition, encode_zone_features, encode_zone_confluence

_CANDLE_WINDOW = 5
_CANDLE_FEATS_PER = 3
_CANDLE_DIM = _CANDLE_WINDOW * _CANDLE_FEATS_PER

SETUP_PROB_DIM = 8
TRIGGER_GBT_DIM = 8
EXEC_PASSTHROUGH_DIM = 3  # trades_today, time_to_close, session_pnl

TRIGGER_DIM = (
    NARRATIVE_DIM           # 15
    + SETUP_PROB_DIM        # 8
    + PASSTHROUGH_DIM       # 10
    + 20                    # micro
    + 21                    # orderflow
    + _CANDLE_DIM           # 15
    + 4                     # zone features
    + 5                     # zone confluence
    + len(LevelType)        # 31 zone composition
    + 1                     # approach direction
    + TRIGGER_GBT_DIM       # 8
    + EXEC_PASSTHROUGH_DIM  # 3
)  # = 141

TRIGGER_SEGMENTS = {
    "narrative": NARRATIVE_DIM,
    "setup_probs": SETUP_PROB_DIM,
    "passthrough": PASSTHROUGH_DIM,
    "micro": 20,
    "orderflow": 21,
    "candles": _CANDLE_DIM,
    "zone_features": 4,
    "zone_confluence": 5,
    "zone_composition": len(LevelType),
    "approach_dir": 1,
    "trigger_gbt": TRIGGER_GBT_DIM,
    "exec_passthrough": EXEC_PASSTHROUGH_DIM,
}


def _build_candle_window(candles: list, avg_vol: float) -> np.ndarray:
    """Last 5 candles → 15 features (delta_norm, volume_norm, body_ratio)."""
    out = np.zeros(_CANDLE_DIM, dtype=np.float32)
    if not candles:
        return out
    window = candles[-_CANDLE_WINDOW:] if len(candles) >= _CANDLE_WINDOW else candles
    for i, c in enumerate(window):
        offset = i * _CANDLE_FEATS_PER
        vol = c.get("volume", 0)
        body = abs(c.get("close", 0) - c.get("open", 0))
        spread = max(c.get("high", 0) - c.get("low", 0), TICK_SIZE)
        delta = c.get("close", 0) - c.get("open", 0)
        out[offset] = np.clip(delta / spread, -1, 1) if spread > 0 else 0
        out[offset + 1] = np.clip(vol / max(avg_vol, 1), 0, 5) / 5.0
        out[offset + 2] = np.clip(body / spread, 0, 1) if spread > 0 else 0
    return out


def build_trigger_observation(
    narrative: np.ndarray,
    setup_probs: np.ndarray,
    state: dict,
    base_observation: np.ndarray,
    trigger_gbt_forecast: np.ndarray | None = None,
) -> np.ndarray:
    """Assemble the trigger observation vector.

    Args:
        narrative: 15-dim narrative signals (pre-computed by Stage 1).
        setup_probs: 8-dim setup probabilities for this zone.
        state: Full market state dict (same as build_observation uses).
        base_observation: 276-dim base observation (for passthrough extraction).
        trigger_gbt_forecast: 8-dim trigger GBT output (optional, zeros if not available).

    Returns:
        ~141-dim float32 trigger observation.
    """
    segments = []

    # 1. Narrative signals (15)
    segments.append(narrative)

    # 2. Setup probabilities (8)
    segments.append(setup_probs if setup_probs is not None else np.zeros(SETUP_PROB_DIM, dtype=np.float32))

    # 3. Structural passthrough (10)
    segments.append(extract_passthrough(base_observation))

    # 4. Micro features (20)
    recent_ticks = state.get("recent_ticks", [])
    touch_price = state.get("price", 0)
    segments.append(extract_micro_features(recent_ticks, touch_price))

    # 5. Orderflow (21)
    candle_flows = state.get("candle_flows", [])
    of_signals = state.get("orderflow_signals")
    segments.append(extract_orderflow_features(candle_flows, of_signals))

    # 6. Candles (15)
    candles = state.get("candles", [])
    avg_vol = state.get("avg_volume", 1000)
    segments.append(_build_candle_window(candles, avg_vol))

    # 7. Zone features (4)
    zone = state.get("zone")
    if zone:
        segments.append(encode_zone_features(zone))
    else:
        segments.append(np.zeros(4, dtype=np.float32))

    # 8. Zone confluence (5)
    if zone:
        segments.append(encode_zone_confluence(zone, state.get("all_zones", [])))
    else:
        segments.append(np.zeros(5, dtype=np.float32))

    # 9. Zone composition (31)
    if zone:
        segments.append(encode_zone_composition(zone))
    else:
        segments.append(np.zeros(len(LevelType), dtype=np.float32))

    # 10. Approach direction (1)
    approach = state.get("approach_direction", "up")
    segments.append(np.array([1.0 if approach == "up" else -1.0], dtype=np.float32))

    # 11. Trigger GBT forecast (8)
    if trigger_gbt_forecast is not None:
        segments.append(trigger_gbt_forecast)
    else:
        segments.append(np.zeros(TRIGGER_GBT_DIM, dtype=np.float32))

    # 12. Execution passthrough (3)
    ctx = state.get("session_context") or {}
    exec_pass = np.zeros(EXEC_PASSTHROUGH_DIM, dtype=np.float32)
    exec_pass[0] = np.clip(ctx.get("trades_today", 0) / 20.0, 0, 1)
    exec_pass[1] = np.clip(ctx.get("time_to_close_hours", 6.5) / 6.5, 0, 1)
    exec_pass[2] = np.clip(ctx.get("session_pnl_r", 0) / 10.0, -1, 1)
    segments.append(exec_pass)

    return np.concatenate(segments)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd backend && pytest tests/test_trigger_features.py -v
```
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/src/rl/features/trigger_features.py backend/tests/test_trigger_features.py
git commit -m "feat(rl): trigger observation assembler — 141-dim fast features"
```

---

### Task 7: Narrative GBT Model

**Files:**
- Create: `backend/src/rl/agent/narrative_gbt.py`

- [ ] **Step 1: Implement Narrative GBT**

```python
# backend/src/rl/agent/narrative_gbt.py
"""Narrative GBT — predicts day type, regime, and setup probabilities.

Trained on slow-moving features (structure, TPO, AMT, macro).
Outputs feed into the trigger layer as pre-computed context.
"""
from __future__ import annotations

import logging
from pathlib import Path

import numpy as np

from ..labeling.setup_types import SetupType, NUM_SETUP_TYPES

log = logging.getLogger(__name__)

# Narrative GBT outputs
NARRATIVE_GBT_OUTPUTS = ["day_type", "regime"] + [f"p_{st.value}" for st in SetupType if st != SetupType.UNKNOWN]


class NarrativeGBT:
    """Multi-target GBT for session narrative prediction."""

    def __init__(self) -> None:
        self.day_type_model = None
        self.setup_models: dict = {}  # one binary classifier per setup
        self._alive_mask: np.ndarray | None = None

    def train(
        self,
        X: np.ndarray,
        day_type_labels: np.ndarray,
        setup_labels: np.ndarray,
        n_estimators: int = 500,
        max_depth: int = 5,
        learning_rate: float = 0.05,
    ) -> dict:
        """Train narrative GBT on labeled episodes.

        Args:
            X: (N, narrative_feature_dim) — slow features only.
            day_type_labels: (N,) integer day type labels.
            setup_labels: (N,) string setup type labels.
        """
        try:
            from lightgbm import LGBMClassifier
            _Cls = LGBMClassifier
        except ImportError:
            from sklearn.ensemble import GradientBoostingClassifier
            _Cls = GradientBoostingClassifier

        # Remove dead features
        stds = X.std(axis=0)
        self._alive_mask = stds > 1e-8
        X_alive = X[:, self._alive_mask]
        log.info("Narrative GBT: %d alive of %d features", self._alive_mask.sum(), len(self._alive_mask))

        # Day type classifier
        self.day_type_model = _Cls(
            n_estimators=n_estimators, max_depth=max_depth,
            learning_rate=learning_rate, subsample=0.8, verbose=-1,
        )
        self.day_type_model.fit(X_alive, day_type_labels)

        # Per-setup binary classifiers
        metrics = {}
        for st in SetupType:
            if st == SetupType.UNKNOWN:
                continue
            binary = (setup_labels == st.value).astype(int)
            pos_count = binary.sum()
            if pos_count < 50:
                log.info("Skipping %s — only %d positive samples", st.value, pos_count)
                continue
            model = _Cls(
                n_estimators=min(n_estimators, 300), max_depth=min(max_depth, 4),
                learning_rate=learning_rate, subsample=0.8, verbose=-1,
                scale_pos_weight=max(1, (len(binary) - pos_count) / max(pos_count, 1)),
            )
            model.fit(X_alive, binary)
            self.setup_models[st.value] = model
            acc = (model.predict(X_alive) == binary).mean()
            metrics[st.value] = {"accuracy": round(acc, 3), "positive_samples": int(pos_count)}

        return metrics

    def predict_setup_probs(self, obs: np.ndarray) -> np.ndarray:
        """Predict setup probabilities for a single observation.

        Returns 8-dim array of probabilities, one per setup type.
        """
        probs = np.zeros(NUM_SETUP_TYPES, dtype=np.float32)
        if self._alive_mask is None:
            return probs

        x = obs[self._alive_mask].reshape(1, -1)
        for i, st in enumerate(SetupType):
            if st == SetupType.UNKNOWN:
                continue
            model = self.setup_models.get(st.value)
            if model is not None:
                probs[i] = model.predict_proba(x)[0, 1]
        return probs

    def predict_setup_probs_batch(self, obs: np.ndarray) -> np.ndarray:
        """Batch predict setup probabilities. Returns (N, 8)."""
        n = len(obs)
        probs = np.zeros((n, NUM_SETUP_TYPES), dtype=np.float32)
        if self._alive_mask is None:
            return probs

        X = obs[:, self._alive_mask]
        for i, st in enumerate(SetupType):
            if st == SetupType.UNKNOWN:
                continue
            model = self.setup_models.get(st.value)
            if model is not None:
                probs[:, i] = model.predict_proba(X)[:, 1]
        return probs

    def save(self, path: Path) -> None:
        import joblib
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        joblib.dump({
            "day_type_model": self.day_type_model,
            "setup_models": self.setup_models,
            "alive_mask": self._alive_mask,
            "version": "v5_narrative",
        }, path)

    @classmethod
    def load(cls, path: Path) -> NarrativeGBT:
        import joblib
        data = joblib.load(path)
        obj = cls()
        obj.day_type_model = data["day_type_model"]
        obj.setup_models = data.get("setup_models", {})
        obj._alive_mask = data.get("alive_mask")
        return obj
```

- [ ] **Step 2: Commit**

```bash
git add backend/src/rl/agent/narrative_gbt.py
git commit -m "feat(rl): narrative GBT — day type + setup probability prediction"
```

---

### Task 8: Trigger GBT Model

**Files:**
- Create: `backend/src/rl/agent/trigger_gbt.py`

- [ ] **Step 1: Implement Trigger GBT**

This is a refactored version of the existing `gbt_model.py` that operates on trigger-layer features instead of the full 276-dim observation.

```python
# backend/src/rl/agent/trigger_gbt.py
"""Trigger GBT — direction/reward prediction on trigger-layer features.

Same multi-target structure as the original GBTModel but trained on
the trigger observation (~133 dims without its own forecast) instead
of the full 276-dim base observation.

Outputs 8-dim forecast consumed by the trigger DQN.
"""
from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
from sklearn.preprocessing import StandardScaler

log = logging.getLogger(__name__)

TRIGGER_GBT_FORECAST_DIM = 8


class TriggerGBT:
    """Multi-target GBT for trigger-layer predictions."""

    def __init__(self) -> None:
        self.direction_model = None
        self.expected_best_r_model = None
        self.expected_worst_r_model = None
        self.breakeven_model = None
        self.levels_model = None
        self.stop_model = None
        self.scaler: StandardScaler | None = None
        self._alive_mask: np.ndarray | None = None

    def train(
        self,
        X: np.ndarray,
        y_direction: np.ndarray,
        rewards_cont: np.ndarray,
        rewards_rev: np.ndarray,
        stop_targets: np.ndarray,
        breakeven_reached: np.ndarray | None = None,
        levels_captured: np.ndarray | None = None,
        n_estimators: int = 500,
        max_depth: int = 5,
        learning_rate: float = 0.05,
    ) -> dict:
        """Train all sub-models on trigger-layer features."""
        try:
            from lightgbm import LGBMClassifier, LGBMRegressor
            _Cls, _Reg = LGBMClassifier, LGBMRegressor
        except ImportError:
            from sklearn.ensemble import GradientBoostingClassifier, GradientBoostingRegressor
            _Cls, _Reg = GradientBoostingClassifier, GradientBoostingRegressor

        stds = X.std(axis=0)
        self._alive_mask = stds > 1e-8
        X_alive = X[:, self._alive_mask]

        self.scaler = StandardScaler()
        X_scaled = self.scaler.fit_transform(X_alive)

        common = dict(n_estimators=n_estimators, max_depth=max_depth,
                      learning_rate=learning_rate, subsample=0.8, verbose=-1)

        # Direction
        self.direction_model = _Cls(**common)
        self.direction_model.fit(X_scaled, y_direction)

        # Expected rewards
        best_r = np.maximum(rewards_cont, rewards_rev)
        worst_r = np.minimum(rewards_cont, rewards_rev)
        self.expected_best_r_model = _Reg(**common)
        self.expected_best_r_model.fit(X_scaled, best_r)
        self.expected_worst_r_model = _Reg(**common)
        self.expected_worst_r_model.fit(X_scaled, worst_r)

        # Breakeven
        if breakeven_reached is not None:
            self.breakeven_model = _Cls(**common)
            self.breakeven_model.fit(X_scaled, breakeven_reached.astype(int))

        # Levels captured
        if levels_captured is not None:
            self.levels_model = _Reg(**common)
            self.levels_model.fit(X_scaled, levels_captured)

        # Stop distance
        self.stop_model = _Reg(**common)
        self.stop_model.fit(X_scaled, stop_targets)

        acc = (self.direction_model.predict(X_scaled) == y_direction).mean()
        return {"direction_accuracy": round(acc, 3), "alive_features": int(self._alive_mask.sum())}

    def predict_full(self, obs: np.ndarray) -> np.ndarray:
        """8-dim forecast from a single trigger observation."""
        if self._alive_mask is None:
            return np.zeros(TRIGGER_GBT_FORECAST_DIM, dtype=np.float32)
        x = obs[self._alive_mask].reshape(1, -1)
        x = self.scaler.transform(x)

        prob = self.direction_model.predict_proba(x)[0]
        p_cont = prob[1] if len(prob) > 1 else 0.5
        p_rev = 1.0 - p_cont
        conf = abs(p_cont - p_rev)
        best_r = self.expected_best_r_model.predict(x)[0]
        worst_r = self.expected_worst_r_model.predict(x)[0]
        be = self.breakeven_model.predict_proba(x)[0, 1] if self.breakeven_model else 0.5
        levels = self.levels_model.predict(x)[0] if self.levels_model else 0.0
        stop = self.stop_model.predict(x)[0]

        return np.array([p_cont, p_rev, conf, best_r, worst_r, be, levels, stop], dtype=np.float32)

    def predict_full_batch(self, obs: np.ndarray) -> np.ndarray:
        """Batch 8-dim forecast. Returns (N, 8)."""
        if self._alive_mask is None:
            return np.zeros((len(obs), TRIGGER_GBT_FORECAST_DIM), dtype=np.float32)
        X = self.scaler.transform(obs[:, self._alive_mask])

        prob = self.direction_model.predict_proba(X)
        p_cont = prob[:, 1] if prob.shape[1] > 1 else np.full(len(X), 0.5)
        p_rev = 1.0 - p_cont
        conf = np.abs(p_cont - p_rev)
        best_r = self.expected_best_r_model.predict(X)
        worst_r = self.expected_worst_r_model.predict(X)
        be = self.breakeven_model.predict_proba(X)[:, 1] if self.breakeven_model else np.full(len(X), 0.5)
        levels = self.levels_model.predict(X) if self.levels_model else np.zeros(len(X))
        stop = self.stop_model.predict(X)

        return np.column_stack([p_cont, p_rev, conf, best_r, worst_r, be, levels, stop]).astype(np.float32)

    def save(self, path: Path) -> None:
        import joblib
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        joblib.dump({
            "direction_model": self.direction_model,
            "expected_best_r_model": self.expected_best_r_model,
            "expected_worst_r_model": self.expected_worst_r_model,
            "breakeven_model": self.breakeven_model,
            "levels_model": self.levels_model,
            "stop_model": self.stop_model,
            "scaler": self.scaler,
            "alive_mask": self._alive_mask,
            "version": "v5_trigger",
        }, path)

    @classmethod
    def load(cls, path: Path) -> TriggerGBT:
        import joblib
        data = joblib.load(path)
        obj = cls()
        obj.direction_model = data["direction_model"]
        obj.expected_best_r_model = data["expected_best_r_model"]
        obj.expected_worst_r_model = data["expected_worst_r_model"]
        obj.breakeven_model = data.get("breakeven_model")
        obj.levels_model = data.get("levels_model")
        obj.stop_model = data.get("stop_model")
        obj.scaler = data.get("scaler")
        obj._alive_mask = data.get("alive_mask")
        return obj
```

- [ ] **Step 2: Commit**

```bash
git add backend/src/rl/agent/trigger_gbt.py
git commit -m "feat(rl): trigger GBT — direction/reward prediction on trigger features"
```

---

### Task 9: Update observation.py — Add Narrative/Trigger Builders

**Files:**
- Modify: `backend/src/rl/features/observation.py`

- [ ] **Step 1: Add build_narrative and build_trigger functions alongside existing build_observation**

Add these imports and functions at the end of `observation.py` (after the existing code, preserving backward compatibility):

```python
# Add to the end of backend/src/rl/features/observation.py

from .narrative_features import extract_narrative_features, NARRATIVE_DIM
from .trigger_features import build_trigger_observation, TRIGGER_DIM
from .passthrough_features import PASSTHROUGH_DIM

# V5 dimensions
NARRATIVE_OBSERVATION_DIM = NARRATIVE_DIM  # 15
TRIGGER_OBSERVATION_DIM = TRIGGER_DIM     # 141


def build_narrative(state: dict) -> np.ndarray:
    """Build the narrative observation (Stage 1 input).

    This is a convenience wrapper — the narrative GBT consumes the
    slow-moving features extracted from the same state dict.
    """
    return extract_narrative_features(state)


def build_trigger(
    narrative: np.ndarray,
    setup_probs: np.ndarray,
    state: dict,
    trigger_gbt_forecast: np.ndarray | None = None,
) -> np.ndarray:
    """Build the trigger observation (Stage 2 input).

    Requires the base observation for passthrough extraction.
    """
    base_obs = build_observation(state)
    return build_trigger_observation(
        narrative, setup_probs, state, base_obs, trigger_gbt_forecast,
    )
```

- [ ] **Step 2: Commit**

```bash
git add backend/src/rl/features/observation.py
git commit -m "feat(rl): add build_narrative/build_trigger to observation.py"
```

---

### Task 10: Update Config — New Dimensions and Setup Types

**Files:**
- Modify: `backend/src/rl/config.py`

- [ ] **Step 1: Add v5 constants**

Add after the existing constants in `config.py`:

```python
# --- V5 Hierarchical Architecture ---
from src.rl.labeling.setup_types import SetupType, NUM_SETUP_TYPES  # noqa: E402

NARRATIVE_UPDATE_INTERVAL_S = 1800  # 30 minutes
NARRATIVE_STRUCTURAL_TRIGGERS = [
    "ib_close",           # 10:30 ET
    "new_swing_high",
    "new_swing_low",
    "value_area_breach",  # price acceptance above VAH / below VAL
    "single_print_created",
]
```

- [ ] **Step 2: Commit**

```bash
git add backend/src/rl/config.py
git commit -m "feat(rl): add v5 hierarchical architecture constants"
```

---

### Task 11: CLI Commands for Setup Labeling and Narrative Training

**Files:**
- Modify: `backend/src/rl/cli.py`

- [ ] **Step 1: Add `label-setups` CLI command**

Add to `cli.py` after the existing commands:

```python
@rl_app.command("label-setups")
def label_setups() -> None:
    """Label all episodes with setup types (rule-based + clustering)."""
    import numpy as np

    from src.rl.labeling.setup_labeler import label_episode
    from src.rl.labeling.setup_types import SetupType

    episodes_dir = _EPISODES_DIR

    obs = np.load(episodes_dir / "observations.npy")
    rc = np.load(episodes_dir / "rewards_cont.npy")
    rr = np.load(episodes_dir / "rewards_rev.npy")
    lt = np.load(episodes_dir / "level_types.npy", allow_pickle=True)

    n = len(obs)
    typer.echo(f"Labeling {n} episodes...")

    labels = np.full(n, SetupType.UNKNOWN.value, dtype=object)
    rule_counts = {}

    for i in range(n):
        # Build minimal episode dict for labeler
        zone_types = []
        if isinstance(lt[i], str):
            zone_types = [lt[i]]
        elif hasattr(lt[i], "__iter__"):
            zone_types = list(lt[i])

        ep = {
            "zone_types": zone_types,
            "approach_direction": "up" if obs[i][-8] > 0 else "down",  # approach_dir index
            "reward_cont": float(rc[i]),
            "reward_rev": float(rr[i]),
            "has_single_print": "naked_poc" in zone_types,
            "has_gap": False,  # TODO: detect from session context
            "ib_closed": True,  # conservative default
            "delta_ratio": 0.3,  # default, would need orderflow
            "forward_reversal_speed": abs(float(rr[i])) * 5 if rr[i] > rc[i] else 0,
        }
        label = label_episode(ep)
        labels[i] = label.value
        rule_counts[label.value] = rule_counts.get(label.value, 0) + 1

    # Save labels
    np.save(episodes_dir / "setup_labels.npy", labels)

    typer.echo("\nSetup label distribution:")
    for setup, count in sorted(rule_counts.items(), key=lambda x: -x[1]):
        typer.echo(f"  {setup:30s}  {count:>6}  ({count/n*100:.1f}%)")

    # Run clustering on unknowns if enough data
    unknown_mask = labels == SetupType.UNKNOWN.value
    unknown_count = unknown_mask.sum()
    typer.echo(f"\nUnknown (for clustering): {unknown_count} ({unknown_count/n*100:.1f}%)")

    if unknown_count > 1000:
        from src.rl.labeling.setup_clusterer import cluster_and_label
        # Use structure/TPO portion of observation for clustering
        cluster_obs = obs[unknown_mask, 52:152]  # struct + TPO + AMT range
        pvv = obs[unknown_mask, 52]  # price_vs_vwap approximation
        bw = obs[unknown_mask, 163]  # balance_width from AMT dynamics

        zt_list = []
        for i in np.where(unknown_mask)[0]:
            if isinstance(lt[i], str):
                zt_list.append([lt[i]])
            elif hasattr(lt[i], "__iter__"):
                zt_list.append(list(lt[i]))
            else:
                zt_list.append([])

        cluster_labels = cluster_and_label(
            cluster_obs, zt_list, rc[unknown_mask], rr[unknown_mask], pvv, bw,
        )
        labels[unknown_mask] = cluster_labels

        # Recount
        final_counts = {}
        for l in labels:
            final_counts[l] = final_counts.get(l, 0) + 1
        typer.echo("\nFinal distribution (after clustering):")
        for setup, count in sorted(final_counts.items(), key=lambda x: -x[1]):
            typer.echo(f"  {setup:30s}  {count:>6}  ({count/n*100:.1f}%)")

    np.save(episodes_dir / "setup_labels.npy", labels)
    typer.echo(f"\nSaved to {episodes_dir / 'setup_labels.npy'}")
```

- [ ] **Step 2: Add `train-narrative-gbt` CLI command**

```python
@rl_app.command("train-narrative-gbt")
def train_narrative_gbt(
    checkpoint: str = typer.Option("v5", help="Checkpoint name"),
    trees: int = typer.Option(500, help="Number of trees"),
    depth: int = typer.Option(5, help="Max depth"),
    lr: float = typer.Option(0.05, help="Learning rate"),
) -> None:
    """Train the Narrative GBT on slow features → day type + setup probs."""
    import numpy as np
    from src.rl.agent.narrative_gbt import NarrativeGBT
    from src.rl.features.narrative_features import extract_narrative_features

    episodes_dir = _EPISODES_DIR
    models_dir = _MODELS_DIR
    models_dir.mkdir(parents=True, exist_ok=True)

    obs = np.load(episodes_dir / "observations.npy")
    setup_labels = np.load(episodes_dir / "setup_labels.npy", allow_pickle=True)

    n = len(obs)
    typer.echo(f"Loaded {n} episodes ({obs.shape[1]}-dim)")

    # Extract narrative-relevant features (structure + TPO + AMT + macro portions)
    # Indices: structure=52:116, TPO=116:154, AMT=168:188, AMT_dyn=188:208, macro=159:170
    narrative_features = np.column_stack([
        obs[:, 52:116],   # structure (64)
        obs[:, 116:154],  # TPO (38)
        obs[:, 159:170],  # macro (11)
        obs[:, 168:188],  # AMT (20)
        obs[:, 188:208],  # AMT dynamics (20)
    ])
    typer.echo(f"Narrative features shape: {narrative_features.shape}")

    # Day type labels from AMT features (index 168-173 is day type one-hot)
    day_type_labels = obs[:, 168:174].argmax(axis=1)

    model = NarrativeGBT()
    metrics = model.train(narrative_features, day_type_labels, setup_labels,
                          n_estimators=trees, max_depth=depth, learning_rate=lr)

    typer.echo(f"\nMetrics: {metrics}")

    path = models_dir / f"narrative_gbt_{checkpoint}.joblib"
    model.save(path)
    typer.echo(f"Saved to {path}")
```

- [ ] **Step 3: Add `train-trigger-gbt` CLI command**

```python
@rl_app.command("train-trigger-gbt")
def train_trigger_gbt(
    checkpoint: str = typer.Option("v5", help="Checkpoint name"),
    trees: int = typer.Option(1000, help="Number of trees"),
    depth: int = typer.Option(6, help="Max depth"),
    lr: float = typer.Option(0.05, help="Learning rate"),
) -> None:
    """Train the Trigger GBT on trigger-layer features → direction/reward forecast."""
    import numpy as np
    from src.rl.agent.trigger_gbt import TriggerGBT

    episodes_dir = _EPISODES_DIR
    models_dir = _MODELS_DIR

    obs = np.load(episodes_dir / "observations.npy")
    rc = np.load(episodes_dir / "rewards_cont.npy")
    rr = np.load(episodes_dir / "rewards_rev.npy")
    st = np.load(episodes_dir / "stop_targets.npy")
    be_path = episodes_dir / "breakeven_reached.npy"
    lc_path = episodes_dir / "levels_captured.npy"
    be = np.load(be_path) if be_path.exists() else None
    lc = np.load(lc_path) if lc_path.exists() else None

    n = len(obs)
    typer.echo(f"Loaded {n} episodes")

    # Trigger features = everything EXCEPT slow features (which are in narrative)
    # Micro(20) + Orderflow(21) + Candles(15) + Zone(4+5+31) + Approach(1) + ExecPassthrough(3)
    # These are at the end of the 276-dim vector
    # Zone comp: 0:31, Orderflow: 31:52, Candles: 154:169 (approx), Micro: 208:228, etc.
    # For training, we use the full obs — the trigger GBT learns which features matter
    # But we prepend the narrative GBT outputs

    # Load narrative GBT and augment
    narrative_gbt_path = models_dir / f"narrative_gbt_{checkpoint}.joblib"
    narrative_augment = np.zeros((n, 8 + 15), dtype=np.float32)  # setup_probs + narrative signals
    if narrative_gbt_path.exists():
        from src.rl.agent.narrative_gbt import NarrativeGBT
        from src.rl.features.passthrough_features import extract_passthrough
        ngbt = NarrativeGBT.load(narrative_gbt_path)

        narrative_feats = np.column_stack([
            obs[:, 52:116], obs[:, 116:154], obs[:, 159:170],
            obs[:, 168:188], obs[:, 188:208],
        ])
        setup_probs = ngbt.predict_setup_probs_batch(narrative_feats)
        narrative_augment[:, :8] = setup_probs
        # Narrative signals would come from extract_narrative_features per episode
        # For bulk training, approximate from the raw features
        typer.echo(f"Augmented with narrative GBT setup probabilities")

    # Build trigger input: narrative augment + passthrough + micro/of/candle/zone portions
    from src.rl.features.passthrough_features import extract_passthrough, PASSTHROUGH_DIM
    passthrough = np.stack([extract_passthrough(o) for o in obs])

    # Trigger-relevant raw features
    trigger_raw = np.column_stack([
        obs[:, 0:31],      # zone composition (31)
        obs[:, 31:52],      # orderflow (21)
        obs[:, 154:169],    # candles (15) — approximate index
        obs[:, 169:173],    # zone features (4)
        obs[:, 173:178],    # zone confluence (5)
        obs[:, 208:228],    # micro (20)
        obs[:, 228:229],    # approach dir (1)
    ])

    X = np.column_stack([narrative_augment, passthrough, trigger_raw])
    typer.echo(f"Trigger training features: {X.shape}")

    y_dir = (rc > rr).astype(int)  # 1=continuation, 0=reversal

    model = TriggerGBT()
    metrics = model.train(X, y_dir, rc, rr, st, be, lc,
                          n_estimators=trees, max_depth=depth, learning_rate=lr)
    typer.echo(f"Metrics: {metrics}")

    path = models_dir / f"trigger_gbt_{checkpoint}.joblib"
    model.save(path)
    typer.echo(f"Saved to {path}")
```

- [ ] **Step 4: Commit**

```bash
git add backend/src/rl/cli.py
git commit -m "feat(rl): CLI commands for setup labeling + narrative/trigger GBT training"
```

---

### Task 12: Update Training Pipeline Script

**Files:**
- Modify: `backend/scripts/rl_train_pipeline.sh`

- [ ] **Step 1: Update to 10-step pipeline**

```bash
# Replace the contents of backend/scripts/rl_train_pipeline.sh
#!/bin/bash
# Full RL training pipeline v5 — hierarchical observation architecture.
#
# Pipeline:
#   0. Merge live episodes
#   1. Replay historical ticks → base episodes (parallel)
#   2. Label episodes with setup types (rule-based + clustering)
#   3. Train Narrative GBT (day type + setup probs)
#   4. Train Trigger GBT (direction/reward on trigger features)
#   5. Re-replay with both GBTs → hybrid trigger episodes (parallel)
#   6. Train Trigger DQN on hybrid episodes
#   7. Evaluate
#   8. Deploy models

set -e
LOG=/app/data/rl/pipeline.log
exec > >(tee -a "$LOG") 2>&1

renice -n 19 $$ >/dev/null 2>&1 || true

echo "=========================================="
echo "  RL TRAINING PIPELINE v5 — $(date -u '+%Y-%m-%d %H:%M UTC')"
echo "  PID: $$ (nice 19 — low priority)"
echo "=========================================="

cd /app/backend

# Step 0: Merge live episodes
echo ""
echo "[0/8] Merging live episodes..."
python -m src.app rl merge-live 2>&1 || echo "  No live episodes to merge."

# Step 1: Parallel replay → base episodes
echo ""
echo "[1/8] Replaying historical ticks → base episodes..."
nice -n 19 python -m src.app rl replay --all
echo "[1/8] Replay complete."

# Step 2: Label setups
echo ""
echo "[2/8] Labeling episodes with setup types..."
python -m src.app rl label-setups
echo "[2/8] Setup labeling complete."

# Step 3: Train Narrative GBT
echo ""
echo "[3/8] Training Narrative GBT v5..."
nice -n 19 python -m src.app rl train-narrative-gbt --checkpoint v5 --trees 500 --depth 5 --lr 0.05
echo "[3/8] Narrative GBT trained."

# Step 4: Train Trigger GBT
echo ""
echo "[4/8] Training Trigger GBT v5..."
nice -n 19 python -m src.app rl train-trigger-gbt --checkpoint v5 --trees 1000 --depth 6 --lr 0.05
echo "[4/8] Trigger GBT trained."

# Step 5: Re-replay with GBT augmentation → hybrid trigger episodes
echo ""
echo "[5/8] Re-replaying with GBT augmentation → hybrid trigger episodes..."
nice -n 19 python -m src.app rl replay --all --gbt trigger_gbt_v5.joblib
echo "[5/8] Augmented replay complete."

# Step 6: Train Trigger DQN
echo ""
echo "[6/8] Training Trigger DQN v5 (30 epochs, batch 4096)..."
nice -n 19 python -m src.app rl train --epochs 30 --checkpoint v5
echo "[6/8] DQN v5 trained."

# Step 7: Evaluate
echo ""
echo "[7/8] Evaluating DQN v5..."
python -m src.app rl eval --checkpoint v5 --skip-threshold 0.15
echo "[7/8] Evaluation complete."

# Step 8: Deploy
echo ""
echo "[8/8] Deploying v5 models..."
cp -f /app/backend/data/rl/models/narrative_gbt_v5.joblib /app/backend/data/rl/models/narrative_gbt_latest.joblib 2>/dev/null || true
cp -f /app/backend/data/rl/models/trigger_gbt_v5.joblib /app/backend/data/rl/models/trigger_gbt_latest.joblib 2>/dev/null || true
cp -f /app/backend/data/rl/models/dqn_v5.pt /app/backend/data/rl/models/dqn_latest.pt 2>/dev/null || true
echo "[8/8] Models deployed."

echo ""
echo "=========================================="
echo "  PIPELINE v5 COMPLETE — $(date -u '+%Y-%m-%d %H:%M UTC')"
echo "=========================================="
```

- [ ] **Step 2: Commit**

```bash
git add backend/scripts/rl_train_pipeline.sh
git commit -m "feat(rl): v5 training pipeline — hierarchical observation architecture"
```

---

### Task 13: Update Live Inference — Two-Stage Path

**Files:**
- Modify: `backend/src/rl/live_inference.py`

- [ ] **Step 1: Add v5 two-stage inference alongside existing v4 path**

Add to `live_inference.py` — a new `LiveInferenceV5` class that wraps the two-stage flow while keeping the existing `LiveInference` class intact for backward compatibility:

```python
# Add to backend/src/rl/live_inference.py

class LiveInferenceV5:
    """Two-stage inference: narrative (slow) → trigger (fast)."""

    def __init__(self) -> None:
        self._narrative_gbt = None
        self._trigger_gbt = None
        self._dqn = None
        self._normalizer = None
        self._narrative_cache: np.ndarray | None = None
        self._last_narrative_update = 0.0

    def try_load(self) -> bool:
        from src.rl.agent.narrative_gbt import NarrativeGBT
        from src.rl.agent.trigger_gbt import TriggerGBT

        for search_dir in _MODEL_SEARCH_DIRS:
            ngbt_path = search_dir / "narrative_gbt_latest.joblib"
            tgbt_path = search_dir / "trigger_gbt_latest.joblib"
            if ngbt_path.exists() and tgbt_path.exists():
                self._narrative_gbt = NarrativeGBT.load(ngbt_path)
                self._trigger_gbt = TriggerGBT.load(tgbt_path)
                log.info("Loaded v5 models: %s, %s", ngbt_path, tgbt_path)
                return True
        return False

    def update_narrative(self, state: dict) -> None:
        """Update narrative signals (call every 30min or on structural events)."""
        from src.rl.features.narrative_features import extract_narrative_features
        self._narrative_cache = extract_narrative_features(state)

    def infer(self, state: dict) -> dict | None:
        """Run two-stage inference at a zone touch."""
        if self._narrative_gbt is None or self._trigger_gbt is None:
            return None

        from src.rl.features.observation import build_observation
        from src.rl.features.trigger_features import build_trigger_observation

        # Ensure narrative is up to date
        if self._narrative_cache is None:
            self.update_narrative(state)

        narrative = self._narrative_cache

        # Get setup probs for this zone
        # Extract narrative-relevant features for GBT
        base_obs = build_observation(state)
        narrative_feats = np.concatenate([
            base_obs[52:116], base_obs[116:154], base_obs[159:170],
            base_obs[168:188], base_obs[188:208],
        ])
        setup_probs = self._narrative_gbt.predict_setup_probs(narrative_feats)

        # Build trigger observation (without GBT forecast first)
        trigger_obs_no_gbt = build_trigger_observation(
            narrative, setup_probs, state, base_obs, trigger_gbt_forecast=None,
        )

        # Get trigger GBT forecast
        # The trigger obs without GBT forecast is 133 dims (141 - 8)
        tgbt_forecast = self._trigger_gbt.predict_full(trigger_obs_no_gbt)

        # Rebuild with GBT forecast included
        trigger_obs = build_trigger_observation(
            narrative, setup_probs, state, base_obs, trigger_gbt_forecast=tgbt_forecast,
        )

        action_idx = int(np.argmax(tgbt_forecast[:2]))  # CONT vs REV from trigger GBT
        confidence = float(tgbt_forecast[2])
        stop_ticks = float(tgbt_forecast[7])

        from src.rl.config import Action
        action_name = Action(action_idx).name

        return {
            "action": action_name,
            "confidence": confidence,
            "stop_ticks": stop_ticks,
            "setup_probs": {st.value: float(p) for st, p in zip(
                [s for s in __import__('src.rl.labeling.setup_types', fromlist=['SetupType']).SetupType if s.value != 'unknown'],
                setup_probs,
            )},
            "narrative": {name: float(v) for name, v in zip(
                __import__('src.rl.features.narrative_features', fromlist=['NARRATIVE_NAMES']).NARRATIVE_NAMES,
                narrative,
            )},
            "model_type": "v5_hierarchical",
        }
```

- [ ] **Step 2: Commit**

```bash
git add backend/src/rl/live_inference.py
git commit -m "feat(rl): two-stage live inference for v5 hierarchical architecture"
```

---

### Task 14: Update Session Manager — Narrative Event Triggers

**Files:**
- Modify: `backend/src/rl/session_manager.py`

- [ ] **Step 1: Add narrative update triggers**

Add to the session manager's event handling — call `update_narrative()` on the v5 inference object when structural events occur:

```python
# Add method to SessionManager class in session_manager.py

def on_structural_event(self, event_type: str, state: dict) -> None:
    """Update narrative context when a structural event occurs.

    Events: ib_close, new_swing_high, new_swing_low, value_area_breach,
            single_print_created.
    """
    if hasattr(self, '_inference_v5') and self._inference_v5 is not None:
        self._inference_v5.update_narrative(state)
        log.info("Narrative updated on %s", event_type)
```

- [ ] **Step 2: Commit**

```bash
git add backend/src/rl/session_manager.py
git commit -m "feat(rl): narrative update triggers on structural events"
```

---

### Task 15: Integration Test — Full Pipeline Smoke Test

**Files:**
- Create: `backend/tests/test_hierarchical_pipeline.py`

- [ ] **Step 1: Write integration test**

```python
# backend/tests/test_hierarchical_pipeline.py
"""Smoke test for the hierarchical observation pipeline."""
import numpy as np
import pytest

from src.rl.features.narrative_features import extract_narrative_features, NARRATIVE_DIM
from src.rl.features.trigger_features import build_trigger_observation, TRIGGER_DIM
from src.rl.features.passthrough_features import extract_passthrough, PASSTHROUGH_DIM
from src.rl.features.observation import build_observation
from src.rl.labeling.setup_types import SetupType, NUM_SETUP_TYPES
from src.rl.labeling.setup_labeler import label_episode


def test_full_pipeline_dimensions():
    """Verify the full observation pipeline produces correct dimensions."""
    state = {}  # minimal state → all zeros

    # Stage 1: narrative
    narrative = extract_narrative_features(state)
    assert narrative.shape == (NARRATIVE_DIM,)

    # Setup probs (would come from narrative GBT)
    setup_probs = np.zeros(NUM_SETUP_TYPES, dtype=np.float32)

    # Base observation (for passthrough)
    base_obs = np.zeros(276, dtype=np.float32)

    # Stage 2: trigger
    trigger = build_trigger_observation(narrative, setup_probs, state, base_obs)
    assert trigger.shape == (TRIGGER_DIM,)

    # Passthrough
    pt = extract_passthrough(base_obs)
    assert pt.shape == (PASSTHROUGH_DIM,)


def test_narrative_inside_trigger():
    """Narrative signals should appear at the start of trigger observation."""
    narrative = np.random.randn(NARRATIVE_DIM).astype(np.float32)
    setup_probs = np.random.randn(NUM_SETUP_TYPES).astype(np.float32)
    state = {}
    base_obs = np.zeros(276, dtype=np.float32)

    trigger = build_trigger_observation(narrative, setup_probs, state, base_obs)

    np.testing.assert_array_almost_equal(trigger[:NARRATIVE_DIM], narrative)
    np.testing.assert_array_almost_equal(
        trigger[NARRATIVE_DIM:NARRATIVE_DIM + NUM_SETUP_TYPES], setup_probs,
    )


def test_setup_labeler_returns_valid_types():
    ep = {
        "zone_types": ["vwap"],
        "approach_direction": "up",
        "reward_cont": 0.5,
        "reward_rev": -0.3,
    }
    label = label_episode(ep)
    assert isinstance(label, SetupType)


def test_backward_compatible_observation():
    """Old build_observation still works and produces 276 dims."""
    state = {}
    obs = build_observation(state)
    assert obs.shape == (276,)
```

- [ ] **Step 2: Run all tests**

```bash
cd backend && pytest tests/test_hierarchical_pipeline.py tests/test_setup_labeler.py tests/test_narrative_features.py tests/test_trigger_features.py -v
```
Expected: All PASS

- [ ] **Step 3: Commit**

```bash
git add backend/tests/test_hierarchical_pipeline.py
git commit -m "test(rl): integration tests for hierarchical observation pipeline"
```

---

## Verification Checklist

After all tasks complete:

- [ ] `pytest backend/tests/test_setup_labeler.py -v` — all pass
- [ ] `pytest backend/tests/test_narrative_features.py -v` — all pass
- [ ] `pytest backend/tests/test_trigger_features.py -v` — all pass
- [ ] `pytest backend/tests/test_hierarchical_pipeline.py -v` — all pass
- [ ] Old `build_observation()` still returns 276-dim vectors (backward compatible)
- [ ] New `build_trigger_observation()` returns 141-dim vectors
- [ ] Pipeline script updated to v5 10-step flow
- [ ] `rl label-setups` CLI command works
- [ ] `rl train-narrative-gbt` CLI command works
- [ ] `rl train-trigger-gbt` CLI command works
- [ ] v4 models still loadable for inference (backward compatible)
