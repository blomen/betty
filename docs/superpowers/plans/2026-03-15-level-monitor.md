# Level Monitor Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a real-time LevelMonitor that watches the tick stream, detects when price touches key levels, classifies setups (SFP, Spring, Poor Extreme, IB Break, Exhaustion), scores orderflow confirmations, and pushes alerts via SSE.

**Architecture:** `LevelMonitor` subscribes to the existing `DatabentoLiveStream`, checks each tick against pre-computed levels (refreshed every 60s via background cache). The hot path is synchronous (reads only from cached session + orderflow). Alerts are pushed to frontend via SSE. The existing `MarketScanner` continues running alongside.

**Tech Stack:** Python 3.10+ / FastAPI / asyncio / SSE (sse-starlette) / React 19 / TypeScript

**Spec:** `docs/superpowers/specs/2026-03-15-level-monitor-design.md`

---

## File Structure

| File | Responsibility |
|------|---------------|
| `backend/src/market_data/level_monitor.py` | **NEW** — LevelMonitor class, MonitoredLevel, LevelAlert, Confirmation, setup classification, confirmation scoring, trade plan computation |
| `backend/tests/market_data/test_level_monitor.py` | **NEW** — Unit tests for level monitor |
| `backend/src/api/routes/market.py` | **MODIFY** — Add SSE endpoint `/market/level-alerts` |
| `backend/src/api/__init__.py` | **MODIFY** — Start/stop LevelMonitor in lifespan |
| `frontend/src/hooks/useLevelAlerts.ts` | **NEW** — SSE hook for level alerts |
| `frontend/src/types/market.ts` | **MODIFY** — Add LevelAlert, Confirmation types |
| `frontend/src/components/Terminal/pages/TradingIntradayPage.tsx` | **MODIFY** — Replace panels with alert feed |

---

## Chunk 1: Backend — Data Models & Level Building

### Task 1: Create level_monitor.py with dataclasses

**Files:**
- Create: `backend/src/market_data/level_monitor.py`
- Test: `backend/tests/market_data/test_level_monitor.py`

- [ ] **Step 1: Write tests for MonitoredLevel and LevelAlert**

```python
# backend/tests/market_data/test_level_monitor.py
import pytest
from datetime import datetime
from backend.src.market_data.level_monitor import (
    MonitoredLevel, LevelAlert, Confirmation, SetupClassification,
    PROXIMITY_THRESHOLDS, CONFIRMATION_WEIGHTS,
)


def make_level(key="session_vah", price=19865.0, category="value_area", label="Session VAH"):
    return MonitoredLevel(
        key=key, price=price, label=label, category=category,
        proximity_threshold=PROXIMITY_THRESHOLDS.get(category, 3.0),
        setups=["sfp", "rejection"],
    )


def make_confirmations(met_names: list[str]) -> list[Confirmation]:
    all_names = list(CONFIRMATION_WEIGHTS.keys())
    return [Confirmation(name=n, met=(n in met_names), detail=None) for n in all_names]


class TestMonitoredLevel:
    def test_basic_creation(self):
        level = make_level()
        assert level.price == 19865.0
        assert level.category == "value_area"
        assert level.proximity_threshold == 2.0

    def test_proximity_thresholds_per_category(self):
        assert PROXIMITY_THRESHOLDS["value_area"] == 2.0
        assert PROXIMITY_THRESHOLDS["vwap_extension"] == 5.0
        assert PROXIMITY_THRESHOLDS["swing"] == 3.0


class TestLevelAlert:
    def test_to_dict_serialization(self):
        level = make_level()
        confs = make_confirmations(["absorption", "away_from_fair_value"])
        alert = LevelAlert(
            id="test-123", level=level, setup_type="sfp",
            setup_name="Swing Failure Pattern", direction="short",
            score=40.0, state="monitoring",
            confirmations=confs, price_at_touch=19866.0,
            fair_value_distance=46.0, fair_value_side="above",
            suggested_entry=19860.0, suggested_stop=19880.0,
            suggested_target=19820.0, rr=2.0,
            timestamp=datetime(2026, 3, 15, 12, 0, 0),
            updated_at=datetime(2026, 3, 15, 12, 0, 0),
            _setup=SetupClassification("sfp", "SFP", "short"),
        )
        d = alert.to_dict()
        assert d["id"] == "test-123"
        assert d["level_key"] == "session_vah"
        assert d["score"] == 40.0
        assert len(d["confirmations"]) == 7
        assert d["confirmations"][0]["name"] == "absorption"
        assert d["confirmations"][0]["met"] is True


class TestScoring:
    def test_all_confirmations_met_gives_100(self):
        from backend.src.market_data.level_monitor import LevelMonitor
        confs = make_confirmations(list(CONFIRMATION_WEIGHTS.keys()))
        score = LevelMonitor._compute_score(None, confs)
        assert score == 100.0

    def test_no_confirmations_gives_0(self):
        from backend.src.market_data.level_monitor import LevelMonitor
        confs = make_confirmations([])
        score = LevelMonitor._compute_score(None, confs)
        assert score == 0.0

    def test_absorption_plus_fair_value_gives_40(self):
        from backend.src.market_data.level_monitor import LevelMonitor
        confs = make_confirmations(["absorption", "away_from_fair_value"])
        score = LevelMonitor._compute_score(None, confs)
        assert score == 40.0  # 20 + 20

    def test_state_confirmed_at_75(self):
        from backend.src.market_data.level_monitor import LevelMonitor
        assert LevelMonitor._derive_state(None, 75) == "confirmed"
        assert LevelMonitor._derive_state(None, 74) == "developing"
        assert LevelMonitor._derive_state(None, 50) == "developing"
        assert LevelMonitor._derive_state(None, 49) == "monitoring"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/market_data/test_level_monitor.py -v`
Expected: FAIL — module not found

- [ ] **Step 3: Implement dataclasses and scoring**

```python
# backend/src/market_data/level_monitor.py
"""Real-time level monitoring with orderflow confirmation scoring."""
from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from uuid import uuid4

from .orderflow import OrderflowSignals

# ── Proximity thresholds (NQ, in points) ─────────────────────────────────────

PROXIMITY_THRESHOLDS = {
    "value_area": 2.0,
    "ib": 2.0,
    "swing": 3.0,
    "vwap": 3.0,
    "vwap_extension": 5.0,
    "structural": 3.0,
    "naked": 2.0,
}

# ── Confirmation weights ─────────────────────────────────────────────────────

CONFIRMATION_WEIGHTS = {
    "absorption": 20,
    "delta_divergence": 15,
    "cvd_reversal": 15,
    "big_trades": 10,
    "away_from_fair_value": 20,
    "trapped_traders": 10,
    "momentum_aligned": 10,
}


# ── Dataclasses ──────────────────────────────────────────────────────────────

@dataclass
class MonitoredLevel:
    key: str
    price: float
    label: str
    category: str
    proximity_threshold: float
    setups: list[str]


@dataclass
class Confirmation:
    name: str
    met: bool
    detail: str | None


@dataclass
class SetupClassification:
    type: str
    name: str
    direction: str


@dataclass
class TradePlan:
    entry: float | None
    stop: float | None
    target: float | None
    rr: float | None


@dataclass
class LevelAlert:
    id: str
    level: MonitoredLevel
    setup_type: str
    setup_name: str
    direction: str
    score: float
    state: str
    confirmations: list[Confirmation]
    price_at_touch: float
    fair_value_distance: float
    fair_value_side: str
    suggested_entry: float | None
    suggested_stop: float | None
    suggested_target: float | None
    rr: float | None
    timestamp: datetime
    updated_at: datetime
    _setup: SetupClassification = field(repr=False)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "level_key": self.level.key,
            "level_label": self.level.label,
            "level_price": self.level.price,
            "setup_type": self.setup_type,
            "setup_name": self.setup_name,
            "direction": self.direction,
            "score": self.score,
            "state": self.state,
            "confirmations": [
                {"name": c.name, "met": c.met, "detail": c.detail}
                for c in self.confirmations
            ],
            "price_at_touch": self.price_at_touch,
            "fair_value_distance": self.fair_value_distance,
            "fair_value_side": self.fair_value_side,
            "suggested_entry": self.suggested_entry,
            "suggested_stop": self.suggested_stop,
            "suggested_target": self.suggested_target,
            "rr": self.rr,
            "timestamp": self.timestamp.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/market_data/test_level_monitor.py -v`
Expected: PASS (all 7 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/src/market_data/level_monitor.py backend/tests/market_data/test_level_monitor.py
git commit -m "feat(trading): add level monitor dataclasses and scoring"
```

---

### Task 2: Level building from SessionAnalysis

**Files:**
- Modify: `backend/src/market_data/level_monitor.py`
- Test: `backend/tests/market_data/test_level_monitor.py`

- [ ] **Step 1: Write tests for level building**

```python
# Add to test_level_monitor.py
from unittest.mock import MagicMock
from backend.src.market_data.level_monitor import LevelMonitor


def make_mock_session():
    """Create a mock SessionAnalysis with realistic NQ levels."""
    s = MagicMock()
    # Volume profile
    s.volume_profile.poc = 19820.0
    s.volume_profile.vah = 19865.0
    s.volume_profile.val = 19780.0
    # VWAP bands
    s.vwap_bands.vwap = 19822.5
    s.vwap_bands.sd1_upper = 19850.5
    s.vwap_bands.sd1_lower = 19794.5
    s.vwap_bands.sd2_upper = 19878.5
    s.vwap_bands.sd2_lower = 19766.5
    s.vwap_bands.sd3_upper = 19906.5
    s.vwap_bands.sd3_lower = 19738.5
    # IB
    s.initial_balance.high = 19860.0
    s.initial_balance.low = 19818.0
    s.initial_balance.range = 42.0
    # Previous day
    s.prev_poc = 19790.0
    s.prev_vah = 19830.0
    s.prev_val = 19750.0
    # Overnight
    s.overnight_high = 19810.0
    s.overnight_low = 19795.0
    # Classifications
    s.poor_high = False
    s.poor_low = True
    return s


class TestLevelBuilding:
    def test_builds_value_area_levels(self):
        session = make_mock_session()
        levels = LevelMonitor._build_monitored_levels(None, session)
        keys = [l.key for l in levels]
        assert "session_poc" in keys
        assert "session_vah" in keys
        assert "session_val" in keys

    def test_builds_vwap_levels(self):
        session = make_mock_session()
        levels = LevelMonitor._build_monitored_levels(None, session)
        keys = [l.key for l in levels]
        assert "vwap" in keys
        assert "vwap_2sd_upper" in keys
        assert "vwap_3sd_lower" in keys

    def test_builds_ib_levels(self):
        session = make_mock_session()
        levels = LevelMonitor._build_monitored_levels(None, session)
        keys = [l.key for l in levels]
        assert "ib_high" in keys
        assert "ib_low" in keys

    def test_proximity_varies_by_category(self):
        session = make_mock_session()
        levels = LevelMonitor._build_monitored_levels(None, session)
        by_key = {l.key: l for l in levels}
        assert by_key["session_vah"].proximity_threshold == 2.0
        assert by_key["vwap_2sd_upper"].proximity_threshold == 5.0

    def test_handles_missing_ib(self):
        session = make_mock_session()
        session.initial_balance = None
        levels = LevelMonitor._build_monitored_levels(None, session)
        keys = [l.key for l in levels]
        assert "ib_high" not in keys

    def test_total_level_count(self):
        session = make_mock_session()
        levels = LevelMonitor._build_monitored_levels(None, session)
        # POC + VAH + VAL + VWAP + 6 SD bands + IB high/low + prev POC/VAH/VAL + ON high/low = 17
        assert len(levels) >= 15
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/market_data/test_level_monitor.py::TestLevelBuilding -v`
Expected: FAIL — `_build_monitored_levels` not implemented

- [ ] **Step 3: Implement _build_monitored_levels**

Add to `LevelMonitor` class in `level_monitor.py`:

```python
    @staticmethod
    def _build_monitored_levels(session) -> list[MonitoredLevel]:
        """Convert SessionAnalysis into MonitoredLevel objects."""
        if not session:
            return []
        levels: list[MonitoredLevel] = []

        def _add(key: str, price: float | None, label: str, category: str, setups: list[str]):
            if price is None:
                return
            levels.append(MonitoredLevel(
                key=key, price=price, label=label, category=category,
                proximity_threshold=PROXIMITY_THRESHOLDS.get(category, 3.0),
                setups=setups,
            ))

        # Value area
        vp = session.volume_profile
        if vp:
            _add("session_poc", vp.poc, "Session POC", "value_area", ["rejection", "spring"])
            _add("session_vah", vp.vah, "Session VAH", "value_area", ["sfp", "rejection", "spring", "poor_extreme"])
            _add("session_val", vp.val, "Session VAL", "value_area", ["sfp", "rejection", "spring", "poor_extreme"])

        # VWAP bands
        vw = session.vwap_bands
        if vw:
            _add("vwap", vw.vwap, "VWAP", "vwap", ["rejection"])
            _add("vwap_1sd_upper", vw.sd1_upper, "+1 SD", "vwap", ["rejection"])
            _add("vwap_1sd_lower", vw.sd1_lower, "-1 SD", "vwap", ["rejection"])
            _add("vwap_2sd_upper", vw.sd2_upper, "+2 SD", "vwap_extension", ["exhaustion"])
            _add("vwap_2sd_lower", vw.sd2_lower, "-2 SD", "vwap_extension", ["exhaustion"])
            _add("vwap_3sd_upper", vw.sd3_upper, "+3 SD", "vwap_extension", ["exhaustion"])
            _add("vwap_3sd_lower", vw.sd3_lower, "-3 SD", "vwap_extension", ["exhaustion"])

        # Initial balance
        ib = session.initial_balance
        if ib:
            _add("ib_high", ib.high, "IB High", "ib", ["ib_break", "sfp", "spring"])
            _add("ib_low", ib.low, "IB Low", "ib", ["ib_break", "sfp", "spring"])

        # Previous day
        _add("prev_poc", session.prev_poc, "Prev POC", "structural", ["rejection"])
        _add("prev_vah", session.prev_vah, "Prev VAH", "structural", ["sfp", "rejection"])
        _add("prev_val", session.prev_val, "Prev VAL", "structural", ["sfp", "rejection"])

        # Overnight
        _add("on_high", session.overnight_high, "ON High", "structural", ["sfp", "rejection"])
        _add("on_low", session.overnight_low, "ON Low", "structural", ["sfp", "rejection"])

        return levels
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/market_data/test_level_monitor.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/src/market_data/level_monitor.py backend/tests/market_data/test_level_monitor.py
git commit -m "feat(trading): add level building from SessionAnalysis"
```

---

### Task 3: Setup classification with price history

**Files:**
- Modify: `backend/src/market_data/level_monitor.py`
- Test: `backend/tests/market_data/test_level_monitor.py`

- [ ] **Step 1: Write tests for setup classification**

```python
# Add to test_level_monitor.py
from collections import deque


class TestSetupClassification:
    def _make_monitor_with_history(self, prices: list[float]) -> LevelMonitor:
        """Create a LevelMonitor with pre-filled price history."""
        monitor = LevelMonitor.__new__(LevelMonitor)
        monitor._price_history = deque(
            [(p, datetime(2026, 3, 15, 12, 0, i)) for i, p in enumerate(prices)],
            maxlen=200,
        )
        monitor._cached_session = make_mock_session()
        return monitor

    def test_sfp_at_swing_high(self):
        """Price breaks above level then returns below → SFP."""
        level = make_level(key="swing_high_19905", price=19905.0, category="swing", label="Swing High")
        # Price went to 19910 (above 19905 + 3pt threshold), now back at 19904
        monitor = self._make_monitor_with_history([19900, 19910, 19912, 19911, 19908, 19904])
        setup = monitor._classify_setup(level, 19904.0, monitor._cached_session, None)
        assert setup.type == "sfp"
        assert setup.direction == "short"

    def test_no_sfp_without_break(self):
        """Price approaches level but never breaks → not SFP."""
        level = make_level(key="swing_high_19905", price=19905.0, category="swing", label="Swing High")
        monitor = self._make_monitor_with_history([19900, 19902, 19904, 19903])
        setup = monitor._classify_setup(level, 19903.0, monitor._cached_session, None)
        assert setup.type != "sfp"

    def test_spring_at_val(self):
        """Price breaks below VAL then springs back → Spring."""
        level = make_level(key="session_val", price=19780.0, category="value_area", label="Session VAL")
        monitor = self._make_monitor_with_history([19785, 19775, 19773, 19774, 19778, 19781])
        setup = monitor._classify_setup(level, 19781.0, monitor._cached_session, None)
        assert setup.type == "spring"
        assert setup.direction == "long"

    def test_poor_extreme_at_val(self):
        """Price at VAL when poor_low is True → Poor Extreme."""
        level = make_level(key="session_val", price=19780.0, category="value_area", label="Session VAL")
        # No break-and-return pattern, just touching the level
        monitor = self._make_monitor_with_history([19785, 19783, 19781])
        setup = monitor._classify_setup(level, 19781.0, monitor._cached_session, None)
        # Spring takes priority if there's a break pattern, but here there isn't one
        # poor_low is True in mock, so poor_extreme
        assert setup.type == "poor_extreme"

    def test_ib_break(self):
        """Price at IB high → IB Break."""
        level = make_level(key="ib_high", price=19860.0, category="ib", label="IB High")
        monitor = self._make_monitor_with_history([19855, 19858, 19860])
        setup = monitor._classify_setup(level, 19860.0, monitor._cached_session, None)
        assert setup.type == "ib_break"

    def test_exhaustion_at_vwap_extension(self):
        """Price at +2 SD → Exhaustion."""
        level = make_level(key="vwap_2sd_upper", price=19878.5, category="vwap_extension", label="+2 SD")
        monitor = self._make_monitor_with_history([19870, 19875, 19878])
        setup = monitor._classify_setup(level, 19878.0, monitor._cached_session, None)
        assert setup.type == "exhaustion"
        assert setup.direction == "short"

    def test_default_rejection(self):
        """Price at a structural level with no special pattern → rejection."""
        level = make_level(key="prev_poc", price=19790.0, category="structural", label="Prev POC")
        monitor = self._make_monitor_with_history([19795, 19792, 19790])
        setup = monitor._classify_setup(level, 19790.0, monitor._cached_session, None)
        assert setup.type == "rejection"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/market_data/test_level_monitor.py::TestSetupClassification -v`
Expected: FAIL

- [ ] **Step 3: Implement setup classification**

Add to `LevelMonitor` class in `level_monitor.py`:

```python
    def _classify_setup(self, level: MonitoredLevel, price: float,
                        session, of: OrderflowSignals | None) -> SetupClassification:
        """Determine which setup pattern matches the level touch."""
        direction = "short" if price > level.price else "long"

        # SFP: price broke level then returned
        if self._detected_break_and_return(level, price):
            return SetupClassification("sfp", "Swing Failure Pattern", direction)

        # Spring: aggressive break from value area, immediate reversal
        if level.category == "value_area" and self._detected_spring_pattern(level, price):
            return SetupClassification("spring", "Spring", direction)

        # Poor Extreme: test of low-volume extreme
        if level.category == "value_area" and session:
            if "val" in level.key and getattr(session, "poor_low", False) and direction == "long":
                return SetupClassification("poor_extreme", "Poor Extreme Test", direction)
            if "vah" in level.key and getattr(session, "poor_high", False) and direction == "short":
                return SetupClassification("poor_extreme", "Poor Extreme Test", direction)

        # IB Break
        if level.category == "ib":
            return SetupClassification("ib_break", "IB Break", direction)

        # Exhaustion at VWAP extension
        if level.category == "vwap_extension":
            d = "short" if "upper" in level.key else "long"
            return SetupClassification("exhaustion", "VWAP Extension", d)

        # Default: rejection
        return SetupClassification("rejection", f"Level Test: {level.label}", direction)

    def _detected_break_and_return(self, level: MonitoredLevel, current_price: float) -> bool:
        """SFP: did price break beyond level and return in recent ticks?"""
        if not self._price_history:
            return False

        threshold = level.proximity_threshold
        above_count = sum(1 for p, _ in self._price_history if p > level.price + threshold)
        below_count = sum(1 for p, _ in self._price_history if p < level.price - threshold)

        # High-type levels: price was above (broke out), now at/below
        if level.category in ("swing", "structural", "ib") and current_price <= level.price:
            return above_count >= 3
        # Low-type levels: price was below (broke out), now at/above
        if level.category in ("swing", "structural", "ib") and current_price >= level.price:
            return below_count >= 3
        return False

    def _detected_spring_pattern(self, level: MonitoredLevel, current_price: float) -> bool:
        """Spring: aggressive break from value area with fast reversal."""
        if not self._price_history or len(self._price_history) < 5:
            return False

        threshold = level.proximity_threshold
        recent = list(self._price_history)[-50:]
        broke_above = any(p > level.price + threshold for p, _ in recent)
        broke_below = any(p < level.price - threshold for p, _ in recent)

        if "val" in level.key.lower() and broke_below and current_price >= level.price:
            return True
        if "vah" in level.key.lower() and broke_above and current_price <= level.price:
            return True
        return False
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/market_data/test_level_monitor.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/src/market_data/level_monitor.py backend/tests/market_data/test_level_monitor.py
git commit -m "feat(trading): add setup classification with SFP/spring/poor extreme detection"
```

---

### Task 4: Confirmation scoring and trade plan

**Files:**
- Modify: `backend/src/market_data/level_monitor.py`
- Test: `backend/tests/market_data/test_level_monitor.py`

- [ ] **Step 1: Write tests for confirmation scoring and trade plan**

```python
# Add to test_level_monitor.py

def make_mock_orderflow(
    absorption=False, divergence=False, cvd_trend="flat",
    big_count=0, big_net=0, trapped=False, aligned=False, delta=0,
    passive_active=0.5,
):
    of = MagicMock()
    of.vsa_absorption = absorption
    of.delta_divergence = divergence
    of.cvd_trend = cvd_trend
    of.big_trades_count = big_count
    of.big_trades_net_delta = big_net
    of.trapped_traders = trapped
    of.delta_aligned = aligned
    of.delta = delta
    of.passive_active_ratio = passive_active
    return of


class TestConfirmationScoring:
    def _make_monitor(self):
        monitor = LevelMonitor.__new__(LevelMonitor)
        monitor._price_history = deque(maxlen=200)
        return monitor

    def test_all_met_gives_100(self):
        monitor = self._make_monitor()
        of = make_mock_orderflow(
            absorption=True, divergence=True, cvd_trend="falling",
            big_count=3, big_net=-500, trapped=True, aligned=True, delta=-1200,
        )
        session = make_mock_session()
        setup = SetupClassification("sfp", "SFP", "short")
        confs = monitor._score_confirmations(setup, make_level(), 19870.0, session, of)
        score = monitor._compute_score(confs)
        assert score == 100.0

    def test_absorption_only_gives_20(self):
        monitor = self._make_monitor()
        of = make_mock_orderflow(absorption=True)
        session = make_mock_session()
        # Price at 19820 (POC), so NOT away from fair value
        setup = SetupClassification("rejection", "Test", "long")
        confs = monitor._score_confirmations(setup, make_level(price=19820.0), 19820.0, session, of)
        score = monitor._compute_score(confs)
        assert score == 20.0  # only absorption

    def test_away_from_fair_value_when_distant(self):
        monitor = self._make_monitor()
        of = make_mock_orderflow()
        session = make_mock_session()  # POC at 19820
        setup = SetupClassification("sfp", "SFP", "short")
        confs = monitor._score_confirmations(setup, make_level(price=19865.0), 19866.0, session, of)
        fv_conf = next(c for c in confs if c.name == "away_from_fair_value")
        assert fv_conf.met is True  # 19866 is 46pts from POC


class TestTradePlan:
    def _make_monitor(self):
        monitor = LevelMonitor.__new__(LevelMonitor)
        return monitor

    def test_short_sfp_targets_poc(self):
        monitor = self._make_monitor()
        session = make_mock_session()  # POC=19820, IB range=42
        setup = SetupClassification("sfp", "SFP", "short")
        level = make_level(price=19865.0)
        plan = monitor._compute_trade_plan(setup, level, 19866.0, session)
        assert plan.entry == 19865.0
        assert plan.stop > 19865.0  # stop above for short
        assert plan.target == 19820.0  # POC
        assert plan.rr > 1.0

    def test_long_spring_targets_poc(self):
        monitor = self._make_monitor()
        session = make_mock_session()
        setup = SetupClassification("spring", "Spring", "long")
        level = make_level(key="session_val", price=19780.0)
        plan = monitor._compute_trade_plan(setup, level, 19779.0, session)
        assert plan.entry == 19780.0
        assert plan.stop < 19780.0  # stop below for long
        assert plan.target == 19820.0  # POC
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/market_data/test_level_monitor.py::TestConfirmationScoring -v`
Expected: FAIL

- [ ] **Step 3: Implement confirmation scoring and trade plan**

Add to `LevelMonitor` class:

```python
    def _score_confirmations(self, setup: SetupClassification, level: MonitoredLevel,
                             price: float, session, of: OrderflowSignals | None) -> list[Confirmation]:
        """Score the Fabio/OrderFlowHorse confirmation checklist."""
        poc = session.volume_profile.poc if session and session.volume_profile else None
        fv_dist = abs(price - poc) if poc else 0

        return [
            Confirmation(
                name="absorption",
                met=bool(of and of.vsa_absorption),
                detail=f"Passive/active: {of.passive_active_ratio:.1f}" if of and of.vsa_absorption else None,
            ),
            Confirmation(
                name="delta_divergence",
                met=bool(of and of.delta_divergence),
                detail="Price vs delta disagree" if of and of.delta_divergence else None,
            ),
            Confirmation(
                name="cvd_reversal",
                met=bool(of and self._cvd_supports_direction(of, setup.direction)),
                detail=f"CVD trend: {of.cvd_trend}" if of else None,
            ),
            Confirmation(
                name="big_trades",
                met=bool(of and of.big_trades_count > 0 and self._big_trades_aligned(of, setup.direction)),
                detail=f"x{of.big_trades_count} net Δ{of.big_trades_net_delta:+d}" if of and of.big_trades_count > 0 else None,
            ),
            Confirmation(
                name="away_from_fair_value",
                met=fv_dist > 10,
                detail=f"POC {poc:.0f}, price {price - poc:+.0f}pts" if poc else None,
            ),
            Confirmation(
                name="trapped_traders",
                met=bool(of and of.trapped_traders),
                detail="Trapped traders detected" if of and of.trapped_traders else None,
            ),
            Confirmation(
                name="momentum_aligned",
                met=bool(of and of.delta_aligned),
                detail=f"Delta {of.delta:+d} aligned" if of and of.delta_aligned else None,
            ),
        ]

    @staticmethod
    def _cvd_supports_direction(of: OrderflowSignals, direction: str) -> bool:
        if direction == "long":
            return of.cvd_trend == "rising"
        return of.cvd_trend == "falling"

    @staticmethod
    def _big_trades_aligned(of: OrderflowSignals, direction: str) -> bool:
        if direction == "long":
            return of.big_trades_net_delta > 0
        return of.big_trades_net_delta < 0

    @staticmethod
    def _compute_score(confirmations: list[Confirmation]) -> float:
        return sum(CONFIRMATION_WEIGHTS.get(c.name, 0) for c in confirmations if c.met)

    @staticmethod
    def _derive_state(score: float) -> str:
        if score >= 75:
            return "confirmed"
        if score >= 50:
            return "developing"
        return "monitoring"

    def _fair_value_distance(self, price: float, session) -> tuple[float, str]:
        poc = session.volume_profile.poc if session and session.volume_profile else price
        dist = price - poc
        return abs(dist), "above" if dist > 0 else "below"

    def _stop_distance(self, setup: SetupClassification, session) -> float:
        ib_range = session.initial_balance.range if session and session.initial_balance else 20.0
        stops = {
            "sfp": max(ib_range * 0.3, 8.0),
            "spring": max(ib_range * 0.4, 10.0),
            "poor_extreme": max(ib_range * 0.3, 8.0),
            "ib_break": ib_range * 0.5,
            "exhaustion": max(ib_range * 0.5, 15.0),
            "rejection": max(ib_range * 0.3, 8.0),
        }
        return stops.get(setup.type, 10.0)

    def _compute_trade_plan(self, setup: SetupClassification, level: MonitoredLevel,
                            price: float, session) -> TradePlan:
        poc = session.volume_profile.poc if session and session.volume_profile else None
        if not poc:
            return TradePlan(entry=None, stop=None, target=None, rr=None)

        stop_dist = self._stop_distance(setup, session)

        if setup.direction == "long":
            entry = level.price
            stop = entry - stop_dist
            target = poc
        else:
            entry = level.price
            stop = entry + stop_dist
            target = poc

        risk = abs(entry - stop)
        reward = abs(target - entry)
        rr = reward / risk if risk > 0 else 0

        return TradePlan(entry=entry, stop=stop, target=target, rr=round(rr, 1))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/market_data/test_level_monitor.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/src/market_data/level_monitor.py backend/tests/market_data/test_level_monitor.py
git commit -m "feat(trading): add confirmation scoring, trade plan computation"
```

---

## Chunk 2: Backend — Monitor Loop, Caching & SSE

### Task 5: LevelMonitor async loop with cache refresh

**Files:**
- Modify: `backend/src/market_data/level_monitor.py`
- Test: `backend/tests/market_data/test_level_monitor.py`

- [ ] **Step 1: Write tests for price checking and cooldown**

```python
# Add to test_level_monitor.py
class TestPriceChecking:
    def _make_monitor(self, levels=None):
        monitor = LevelMonitor.__new__(LevelMonitor)
        monitor.levels = levels or []
        monitor.active_alerts = {}
        monitor.alert_history = []
        monitor.subscribers = []
        monitor._cooldowns = {}
        monitor._price_history = deque(maxlen=200)
        monitor._cached_session = make_mock_session()
        monitor._cached_orderflow = make_mock_orderflow()
        return monitor

    def test_creates_alert_when_price_at_level(self):
        level = make_level(price=19865.0)
        monitor = self._make_monitor(levels=[level])
        # Add some price history
        for p in [19870, 19868, 19866]:
            monitor._price_history.append((p, datetime(2026, 3, 15, 12, 0, 0)))
        monitor._check_price(19866.0)
        assert level.key in monitor.active_alerts
        assert len(monitor.alert_history) == 1

    def test_no_alert_when_price_far_from_level(self):
        level = make_level(price=19865.0)
        monitor = self._make_monitor(levels=[level])
        monitor._check_price(19850.0)  # 15 pts away, threshold is 2
        assert len(monitor.active_alerts) == 0

    def test_cooldown_prevents_duplicate(self):
        level = make_level(price=19865.0)
        monitor = self._make_monitor(levels=[level])
        for p in [19870, 19868, 19866]:
            monitor._price_history.append((p, datetime(2026, 3, 15, 12, 0, 0)))
        monitor._check_price(19866.0)
        assert len(monitor.alert_history) == 1
        # Second touch — should update, not create new
        monitor._check_price(19865.5)
        assert len(monitor.alert_history) == 1  # still 1

    def test_updates_existing_alert(self):
        level = make_level(price=19865.0)
        monitor = self._make_monitor(levels=[level])
        for p in [19870, 19868, 19866]:
            monitor._price_history.append((p, datetime(2026, 3, 15, 12, 0, 0)))
        monitor._check_price(19866.0)
        old_updated = monitor.active_alerts[level.key].updated_at
        import time; time.sleep(0.01)
        monitor._check_price(19865.5)
        new_updated = monitor.active_alerts[level.key].updated_at
        assert new_updated >= old_updated
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/market_data/test_level_monitor.py::TestPriceChecking -v`
Expected: FAIL

- [ ] **Step 3: Implement _check_price, _update_alert, cooldown, and the full __init__**

Add the full `__init__`, `_check_price`, `_update_alert`, cooldown methods, and async loops to `LevelMonitor`:

```python
    def __init__(self, stream, db_session_factory):
        self.stream = stream
        self.db_session_factory = db_session_factory
        self.levels: list[MonitoredLevel] = []
        self.active_alerts: dict[str, LevelAlert] = {}
        self.alert_history: list[LevelAlert] = []
        self.subscribers: list[asyncio.Queue] = []
        self._cooldowns: dict[str, datetime] = {}
        self._price_history: deque[tuple[float, datetime]] = deque(maxlen=200)
        self._cached_session = None
        self._cached_orderflow: OrderflowSignals | None = None
        self._task: asyncio.Task | None = None
        self._cache_task: asyncio.Task | None = None
        self._expiry_task: asyncio.Task | None = None

    async def start(self):
        await self._refresh_caches()
        self._task = asyncio.create_task(self._monitor_loop())
        self._cache_task = asyncio.create_task(self._cache_refresh_loop())
        self._expiry_task = asyncio.create_task(self._expiry_sweep_loop())

    async def stop(self):
        for task in [self._task, self._cache_task, self._expiry_task]:
            if task:
                task.cancel()

    async def _cache_refresh_loop(self):
        tick = 0
        while True:
            await asyncio.sleep(5)
            tick += 1
            try:
                await self._refresh_caches()
                if tick % 12 == 0:
                    self.levels = self._build_monitored_levels(self._cached_session)
            except Exception as e:
                import logging
                logging.getLogger(__name__).warning("Cache refresh failed: %s", e)

    async def _refresh_caches(self):
        """Refresh cached session and orderflow using a fresh DB session."""
        from .amt import build_session_analysis
        from .orderflow import build_candle_flow, compute_signals
        db = self.db_session_factory()
        try:
            from ..services.market_service import MarketService
            svc = MarketService(db)
            session_data = await svc.compute_session()
            if session_data:
                self._cached_session = session_data.get("_analysis")
            of = svc._compute_live_orderflow(symbol="NQ", session_data=session_data or {})
            if of:
                self._cached_orderflow = of
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning("Cache refresh error: %s", e)
        finally:
            db.close()
        if self._cached_session and not self.levels:
            self.levels = self._build_monitored_levels(self._cached_session)

    async def _expiry_sweep_loop(self):
        while True:
            await asyncio.sleep(30)
            now = datetime.utcnow()
            current_price = self._price_history[-1][0] if self._price_history else None
            if current_price is None:
                continue
            expired = []
            for key, alert in self.active_alerts.items():
                age = (now - alert.timestamp).total_seconds()
                dist = abs(current_price - alert.level.price)
                if age > 300 or dist > alert.level.proximity_threshold * 3:
                    expired.append(key)
            for key in expired:
                self.active_alerts.pop(key)
                asyncio.create_task(self._notify_subscribers({"event": "expire", "id": key}))

    async def _monitor_loop(self):
        queue = self.stream.subscribe()
        try:
            while True:
                event = await queue.get()
                if event.get("type") != "tick":
                    continue
                price = event["price"]
                self._price_history.append((price, datetime.utcnow()))
                self._check_price(price)
        except asyncio.CancelledError:
            pass
        finally:
            self.stream.unsubscribe(queue)

    def _check_price(self, price: float):
        levels = self.levels
        for level in levels:
            if abs(price - level.price) > level.proximity_threshold:
                continue
            if self._on_cooldown(level.key):
                continue
            if level.key in self.active_alerts:
                self._update_alert(level, price)
                continue
            alert = self._evaluate_touch(level, price)
            if alert:
                self._set_cooldown(level.key, seconds=30)
                self.active_alerts[level.key] = alert
                self.alert_history.insert(0, alert)
                if len(self.alert_history) > 50:
                    self.alert_history = self.alert_history[:50]
                asyncio.create_task(self._notify_subscribers({"event": "alert", "data": alert.to_dict()}))

    def _update_alert(self, level: MonitoredLevel, price: float):
        alert = self.active_alerts[level.key]
        old_state = alert.state
        alert.confirmations = self._score_confirmations(
            alert._setup, level, price, self._cached_session, self._cached_orderflow
        )
        alert.score = self._compute_score(alert.confirmations)
        alert.state = self._derive_state(alert.score)
        alert.updated_at = datetime.utcnow()
        if alert.state != old_state:
            asyncio.create_task(self._notify_subscribers({"event": "alert", "data": alert.to_dict()}))

    def _evaluate_touch(self, level: MonitoredLevel, price: float) -> LevelAlert | None:
        session = self._cached_session
        of = self._cached_orderflow
        if not session:
            return None
        setup = self._classify_setup(level, price, session, of)
        confirmations = self._score_confirmations(setup, level, price, session, of)
        score = self._compute_score(confirmations)
        fv_dist, fv_side = self._fair_value_distance(price, session)
        plan = self._compute_trade_plan(setup, level, price, session)
        return LevelAlert(
            id=str(uuid4()), level=level, setup_type=setup.type,
            setup_name=setup.name, direction=setup.direction,
            score=score, state=self._derive_state(score),
            confirmations=confirmations, price_at_touch=price,
            fair_value_distance=fv_dist, fair_value_side=fv_side,
            suggested_entry=plan.entry, suggested_stop=plan.stop,
            suggested_target=plan.target, rr=plan.rr,
            timestamp=datetime.utcnow(), updated_at=datetime.utcnow(),
            _setup=setup,
        )

    def _on_cooldown(self, key: str) -> bool:
        if key not in self._cooldowns:
            return False
        return (datetime.utcnow() - self._cooldowns[key]).total_seconds() < 30

    def _set_cooldown(self, key: str, seconds: int = 30):
        self._cooldowns[key] = datetime.utcnow()

    async def _notify_subscribers(self, msg: dict):
        for q in self.subscribers:
            try:
                q.put_nowait(msg)
            except asyncio.QueueFull:
                pass

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=100)
        self.subscribers.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue):
        if q in self.subscribers:
            self.subscribers.remove(q)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/market_data/test_level_monitor.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/src/market_data/level_monitor.py backend/tests/market_data/test_level_monitor.py
git commit -m "feat(trading): add monitor loop, cache refresh, cooldown, alert lifecycle"
```

---

### Task 6: SSE endpoint and lifespan integration

**Files:**
- Modify: `backend/src/api/routes/market.py`
- Modify: `backend/src/api/__init__.py`

- [ ] **Step 1: Add SSE endpoint to market routes**

In `backend/src/api/routes/market.py`, add at the end (before any closing code):

```python
@router.get("/level-alerts")
async def stream_level_alerts(request: Request):
    """SSE stream of real-time level alerts."""
    monitor = getattr(request.app.state, "level_monitor", None)
    if not monitor:
        return {"error": "Level monitor not available"}

    queue = monitor.subscribe()

    async def event_generator():
        try:
            # Send current active alerts on connect
            for alert in monitor.active_alerts.values():
                yield {"event": "alert", "data": json.dumps(alert.to_dict())}
            # Stream updates
            while True:
                try:
                    msg = await asyncio.wait_for(queue.get(), timeout=30)
                    event_type = msg.get("event", "alert")
                    data = msg.get("data", msg.get("id", ""))
                    yield {"event": event_type, "data": json.dumps(data) if isinstance(data, dict) else json.dumps(data)}
                except asyncio.TimeoutError:
                    yield {"event": "heartbeat", "data": "{}"}
        except asyncio.CancelledError:
            pass
        finally:
            monitor.unsubscribe(queue)

    return EventSourceResponse(event_generator())
```

- [ ] **Step 2: Add LevelMonitor to lifespan startup**

In `backend/src/api/__init__.py`, after the `DatabentoLiveStream` startup block (after `app.state.databento_stream = _databento_stream`), add:

```python
        # Start level monitor
        from ..market_data.level_monitor import LevelMonitor
        _level_monitor = LevelMonitor(
            stream=_databento_stream,
            db_session_factory=_get_db_session,
        )
        await _level_monitor.start()
        app.state.level_monitor = _level_monitor
```

In the shutdown section (before `if _databento_stream:`), add:

```python
    _level_monitor = getattr(app.state, "level_monitor", None)
    if _level_monitor:
        await _level_monitor.stop()
```

- [ ] **Step 3: Verify the backend starts**

Run: `cd backend && python -m src.app serve --port 8000`
Expected: Server starts without errors, LevelMonitor logs initialization

- [ ] **Step 4: Commit**

```bash
git add backend/src/api/routes/market.py backend/src/api/__init__.py
git commit -m "feat(trading): add level alerts SSE endpoint and lifespan integration"
```

---

## Chunk 3: Frontend — Alert Feed UI

### Task 7: TypeScript types and SSE hook

**Files:**
- Modify: `frontend/src/types/market.ts`
- Create: `frontend/src/hooks/useLevelAlerts.ts`

- [ ] **Step 1: Add types to market.ts**

Add at the end of `frontend/src/types/market.ts`:

```ts
// ── Level Monitor ──────────────────────────────────────────────────────────

export interface LevelAlertConfirmation {
  name: string;
  met: boolean;
  detail: string | null;
}

export interface LevelAlert {
  id: string;
  level_key: string;
  level_label: string;
  level_price: number;
  setup_type: string;
  setup_name: string;
  direction: 'long' | 'short';
  score: number;
  state: 'monitoring' | 'developing' | 'confirmed';
  confirmations: LevelAlertConfirmation[];
  price_at_touch: number;
  fair_value_distance: number;
  fair_value_side: 'above' | 'below';
  suggested_entry: number | null;
  suggested_stop: number | null;
  suggested_target: number | null;
  rr: number | null;
  timestamp: string;
  updated_at: string;
}
```

- [ ] **Step 2: Create useLevelAlerts hook**

```ts
// frontend/src/hooks/useLevelAlerts.ts
import { useState, useEffect, useMemo } from 'react';
import type { LevelAlert } from '@/types/market';

export function useLevelAlerts() {
  const [alerts, setAlerts] = useState<LevelAlert[]>([]);
  const [connected, setConnected] = useState(false);

  useEffect(() => {
    const es = new EventSource('/api/trading/market/level-alerts');

    es.onopen = () => setConnected(true);
    es.onerror = () => setConnected(false);

    es.addEventListener('alert', (e) => {
      const alert: LevelAlert = JSON.parse(e.data);
      setAlerts(prev => {
        const idx = prev.findIndex(a => a.id === alert.id);
        if (idx >= 0) {
          const next = [...prev];
          next[idx] = alert;
          return next;
        }
        return [alert, ...prev];
      });
    });

    es.addEventListener('expire', (e) => {
      const id = JSON.parse(e.data);
      setAlerts(prev => prev.filter(a => a.id !== id));
    });

    return () => es.close();
  }, []);

  const sorted = useMemo(() =>
    [...alerts].sort((a, b) => {
      const stateOrder: Record<string, number> = { confirmed: 0, developing: 1, monitoring: 2 };
      const sd = (stateOrder[a.state] ?? 3) - (stateOrder[b.state] ?? 3);
      return sd !== 0 ? sd : b.score - a.score;
    }),
    [alerts],
  );

  return { alerts: sorted, connected };
}
```

- [ ] **Step 3: Verify TypeScript compiles**

Run: `cd frontend && npx tsc --noEmit`
Expected: No errors

- [ ] **Step 4: Commit**

```bash
git add frontend/src/types/market.ts frontend/src/hooks/useLevelAlerts.ts
git commit -m "feat(trading): add level alert types and SSE hook"
```

---

### Task 8: Alert feed UI in TradingIntradayPage

**Files:**
- Modify: `frontend/src/components/Terminal/pages/TradingIntradayPage.tsx`

- [ ] **Step 1: Add LevelAlertRow component**

Add after the `SignalRow` component in `TradingIntradayPage.tsx`:

```tsx
function LevelAlertRow({ alert, expanded, onToggle, onTakeTrade, connected, lastTick }: {
  alert: LevelAlert;
  expanded: boolean;
  onToggle: () => void;
  onTakeTrade: (alert: LevelAlert, price: string) => void;
  connected: boolean;
  lastTick: any;
}) {
  const [taking, setTaking] = useState(false);
  const [entryPrice, setEntryPrice] = useState(alert.suggested_entry?.toFixed(2) || '');

  const stateIcon = alert.state === 'confirmed' ? '●' : alert.state === 'developing' ? '◐' : '○';
  const stateColor = alert.state === 'confirmed'
    ? (alert.direction === 'long' ? 'text-green-400' : 'text-red-400')
    : alert.state === 'developing' ? 'text-yellow-400' : 'text-zinc-500';

  const metCount = alert.confirmations.filter(c => c.met).length;

  return (
    <div className="border-b border-zinc-800/50 last:border-0">
      <button onClick={onToggle}
        className="w-full flex items-center gap-2 px-3 py-2.5 text-left hover:bg-zinc-800/30 transition-colors">
        {/* State + Score */}
        <div className="flex items-center gap-1.5 w-14 flex-shrink-0">
          <span className={`text-sm ${stateColor}`}>{stateIcon}</span>
          <span className={`text-sm font-mono font-bold ${
            alert.score >= 75 ? 'text-green-400' : alert.score >= 50 ? 'text-yellow-400' : 'text-zinc-500'
          }`}>{alert.score.toFixed(0)}</span>
        </div>

        {/* Setup name + level */}
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-1.5">
            <span className="text-xs text-text font-medium truncate">{alert.setup_name}</span>
            <span className={`text-[10px] px-1 py-0.5 rounded ${
              alert.direction === 'long' ? 'bg-green-500/15 text-green-400 border border-green-500/30' :
              'bg-red-500/15 text-red-400 border border-red-500/30'
            }`}>{alert.direction.toUpperCase()}</span>
          </div>
          <div className="text-[10px] text-zinc-500 mt-0.5">
            at {alert.level_label} ({alert.level_price.toFixed(0)})
          </div>
        </div>

        {/* Confirmations summary */}
        <div className="text-[10px] text-zinc-500 flex-shrink-0">
          {metCount}/7
        </div>

        {/* E/S/T */}
        {alert.suggested_entry && (
          <div className="flex gap-2 text-[10px] text-muted flex-shrink-0 font-mono">
            <span>E <span className="text-text">{alert.suggested_entry.toFixed(0)}</span></span>
            {alert.suggested_stop && <span>S <span className="text-red-400">{alert.suggested_stop.toFixed(0)}</span></span>}
            {alert.suggested_target && <span>T <span className="text-green-400">{alert.suggested_target.toFixed(0)}</span></span>}
            {alert.rr != null && (
              <span className={alert.rr >= 2 ? 'text-green-400' : alert.rr >= 1.5 ? 'text-yellow-400' : 'text-zinc-400'}>
                {alert.rr.toFixed(1)}R
              </span>
            )}
          </div>
        )}

        {/* Timestamp */}
        <span className="text-[9px] text-zinc-600 flex-shrink-0 font-mono">
          {new Date(alert.timestamp).toLocaleTimeString()}
        </span>

        <span className={`text-zinc-500 text-xs transition-transform ${expanded ? 'rotate-90' : ''}`}>▸</span>
      </button>

      {expanded && (
        <div className="px-3 pb-3 space-y-2 bg-zinc-900/30">
          {/* Confirmation checklist */}
          <div className="flex flex-wrap gap-x-2 gap-y-1">
            {alert.confirmations.map(c => (
              <div key={c.name} className="flex items-center gap-1 text-[10px]">
                <span className={c.met ? 'text-green-400' : 'text-zinc-600'}>
                  {c.met ? '✓' : '✗'}
                </span>
                <span className={c.met ? 'text-zinc-300' : 'text-zinc-600'}>
                  {c.name.replace(/_/g, ' ')}
                </span>
              </div>
            ))}
          </div>

          {/* Confirmation details */}
          {alert.confirmations.filter(c => c.met && c.detail).map(c => (
            <div key={c.name} className="text-[9px] text-zinc-500 font-mono pl-2">
              {c.detail}
            </div>
          ))}

          {/* Fair value context */}
          <div className="text-[10px] text-zinc-500 font-mono">
            Fair value: {alert.fair_value_side} by {alert.fair_value_distance.toFixed(0)}pts
          </div>

          {/* Take trade (confirmed alerts only) */}
          {alert.state === 'confirmed' && (
            taking ? (
              <div className="flex items-center gap-2">
                <span className="text-[10px] text-zinc-500">Fill:</span>
                <input type="number" step="0.25" value={entryPrice}
                  onChange={e => setEntryPrice(e.target.value)}
                  className="bg-zinc-800 border border-zinc-700 rounded px-2 py-1 text-xs font-mono text-text w-24"
                  autoFocus />
                <button onClick={() => { onTakeTrade(alert, entryPrice); setTaking(false); }}
                  disabled={!entryPrice}
                  className="text-[10px] px-2.5 py-1 bg-tabTradingScanner/20 border border-tabTradingScanner text-tabTradingScanner rounded hover:bg-tabTradingScanner/30 disabled:opacity-40">
                  Confirm
                </button>
                <button onClick={() => setTaking(false)}
                  className="text-[10px] px-2 py-1 text-zinc-500 hover:text-zinc-300">Cancel</button>
              </div>
            ) : (
              <button onClick={() => setTaking(true)}
                className="text-[10px] px-3 py-1 bg-tabTradingScanner text-black rounded hover:bg-tabTradingScanner/80 font-medium">
                Take Trade
              </button>
            )
          )}

          {alert.state === 'monitoring' && (
            <div className="text-[10px] text-zinc-600 italic">
              Monitoring — waiting for orderflow confirmation
            </div>
          )}
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 2: Rewrite the main component layout to show alert feed**

Replace the right column in `TradingIntradayPage` (the `grid` div) with:

```tsx
// At top of component, add:
import { useLevelAlerts } from '@/hooks/useLevelAlerts';
import type { LevelAlert } from '@/types/market';

// Inside TradingIntradayPage function, add:
const { alerts: levelAlerts, connected: alertsConnected } = useLevelAlerts();
const [expandedAlert, setExpandedAlert] = useState<string | null>(null);

const handleTakeAlertTrade = async (alert: LevelAlert, priceStr: string) => {
  const price = parseFloat(priceStr);
  if (!price) return;
  try {
    await api.createTrade({
      instrument: session?.session?.symbol || 'NQ',
      direction: alert.direction,
      setup_type: alert.setup_type,
      entry_price: price,
      stop_price: alert.suggested_stop || 0,
      targets: alert.suggested_target ? [{ price: alert.suggested_target }] : [],
      contracts: 1,
      notes: `Level alert: ${alert.setup_name} at ${alert.level_label} (score: ${alert.score})`,
    });
  } catch (err) {
    console.error('Failed to create trade:', err);
  }
};
```

Replace the right column div (the `flex-1 min-w-0 grid ...`) with:

```tsx
        {/* RIGHT: Alert Feed */}
        <div className="flex-1 min-w-0 flex flex-col gap-2 overflow-hidden">

          {/* Alert header */}
          <div className="flex items-center justify-between px-2">
            <div className="flex items-center gap-2 text-[10px]">
              <span className="text-zinc-500">Levels: {ladderLevels.length}</span>
              <span className="text-zinc-700">│</span>
              <span className="text-zinc-500">Alerts: <span className="text-tabTradingScanner">{levelAlerts.length}</span></span>
              <span className={alertsConnected ? 'text-green-400' : 'text-red-400'}>●</span>
            </div>
          </div>

          {/* Alert feed */}
          <div className="flex-1 min-h-0 border border-zinc-800 rounded bg-zinc-900/30 flex flex-col overflow-hidden">
            {levelAlerts.length === 0 ? (
              <div className="flex-1 flex items-center justify-center text-zinc-600 text-xs">
                Monitoring {ladderLevels.length} levels. No alerts yet.
              </div>
            ) : (
              <div className="overflow-y-auto flex-1">
                {levelAlerts.map(alert => (
                  <LevelAlertRow
                    key={alert.id}
                    alert={alert}
                    expanded={expandedAlert === alert.id}
                    onToggle={() => setExpandedAlert(expandedAlert === alert.id ? null : alert.id)}
                    onTakeTrade={handleTakeAlertTrade}
                    connected={connected}
                    lastTick={lastTick}
                  />
                ))}
              </div>
            )}
          </div>

          {/* Scanner signals (existing, below alerts) */}
          {signals.length > 0 && (
            <div className="border border-zinc-800 rounded bg-zinc-900/30 max-h-[200px] flex flex-col overflow-hidden">
              <div className="px-3 py-1.5 border-b border-zinc-800 flex-shrink-0">
                <span className="text-[10px] text-zinc-500 uppercase">Scanner Signals</span>
                <span className="text-tabTradingScanner text-[10px] ml-1">{signals.length}</span>
              </div>
              <div className="overflow-y-auto">
                {signals.map(sig => (
                  <SignalRow key={sig.id} sig={sig}
                    expanded={expandedSignal === sig.id}
                    onToggle={() => setExpandedSignal(expandedSignal === sig.id ? null : sig.id)}
                    onTakeTrade={handleTakeTrade} connected={connected} lastTick={lastTick} />
                ))}
              </div>
            </div>
          )}

          {/* Context strip */}
          <div className="flex-shrink-0">
            <ContextStrip session={session} />
          </div>
        </div>
```

- [ ] **Step 3: Remove the Price Ladder left column**

The user watches TradingView for price action, so remove the left column Price Ladder panel. Change the two-column layout to single column:

Remove the `<div className="w-[200px] ...">` Price Strip div and the outer `<div className="flex gap-3 ...">` wrapper. The right column becomes the only column.

Keep `PriceLadder` component and `buildLadder` function in the file — they're still used for the level count display.

- [ ] **Step 4: Verify TypeScript compiles**

Run: `cd frontend && npx tsc --noEmit`
Expected: No errors

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/Terminal/pages/TradingIntradayPage.tsx
git commit -m "feat(trading): replace panels with level alert feed UI"
```

---

### Task 9: Visual verification

- [ ] **Step 1: Start backend and frontend**

Run: `cd backend && python -m src.app serve --port 8000 &`
Run: `cd frontend && npm run dev`

- [ ] **Step 2: Verify in browser**

Open `http://localhost:5173`, navigate to Intraday tab:
- Header shows level count and alert count
- Alert feed shows "Monitoring X levels. No alerts yet." when no ticks flowing
- Context strip shows at bottom
- Scanner signals appear below alerts if any exist
- If live stream is active, alerts appear when price touches levels

- [ ] **Step 3: Final commit**

```bash
git add -A
git commit -m "feat(trading): complete level monitor — real-time alert system

Adds LevelMonitor that watches the tick stream, detects when price
touches key levels, classifies setups (SFP, Spring, Poor Extreme,
IB Break, Exhaustion), scores orderflow confirmations, and pushes
alerts via SSE to the frontend alert feed."
```
