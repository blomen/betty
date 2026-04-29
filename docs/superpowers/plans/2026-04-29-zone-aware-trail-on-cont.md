# Zone-Aware Trail-on-Continuation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** When a position is up +2R and price reaches a new S/R zone in trade direction, trail the stop up to the previously-broken zone's edge. Lock progressive gains as the trade advances through multiple zones.

**Architecture:** A new in-position branch in [`level_monitor.py`](../../../backend/src/market_data/level_monitor.py)'s zone-touch handler. Pure-function helper `_compute_zone_trail_target` does the math; existing `broker.modify_stop` does the order modification with its only-tighten guard.

**Tech Stack:** Python 3.10 + asyncio + pytest.

**Spec:** [docs/superpowers/specs/2026-04-29-zone-aware-trail-on-cont.md](../specs/2026-04-29-zone-aware-trail-on-cont.md)

**Depends on:** [broker tracker reconciliation](2026-04-29-broker-tracker-reconciliation.md) — must ship first.

---

## File Structure

| File | Role | New / Modified |
|---|---|---|
| `backend/src/market_data/zone_trail.py` | Pure helper `_compute_zone_trail_target(tracker, zone, all_zones)` — single responsibility, easy to unit-test | **Create** |
| `backend/src/market_data/level_monitor.py` | Add the 4th in-position branch that calls the helper + invokes `modify_stop` | Modify |
| `backend/src/stocks/broker_adapter.py` | `_execute_entry` initializes `_pending_trade["current_zone_R"] = 0.0` | Modify |
| `backend/tests/test_zone_trail.py` | Unit tests for the 7 spec cases | **Create** |

---

## Task 1: Pure helper for trail target computation

**Files:**
- Create: `backend/src/market_data/zone_trail.py`
- Test: `backend/tests/test_zone_trail.py`

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_zone_trail.py
"""Tests for the zone-trail target computation."""
from dataclasses import dataclass

import pytest

from src.broker.position_tracker import PositionTracker
from src.market_data.zone_trail import compute_zone_trail_target


@dataclass
class FakeZone:
    """Minimal zone shape for tests — mirrors the runtime Zone dataclass fields used."""
    center_price: float
    upper_bound: float
    lower_bound: float
    member_count: int = 1


def _long_tracker(entry: float = 27226.0, stop: float = 27217.75) -> PositionTracker:
    tr = PositionTracker()
    tr.on_fill("long", price=entry, size=1, stop_price=stop)
    return tr


def _short_tracker(entry: float = 27300.0, stop: float = 27308.0) -> PositionTracker:
    tr = PositionTracker()
    tr.on_fill("short", price=entry, size=1, stop_price=stop)
    return tr


def test_long_first_advance_with_prior_zone_returns_prior_upper():
    """Long: touched zone B above entry, prior zone A exists → trail to A.upper_bound."""
    tr = _long_tracker()
    tr.update_mark(27258.0)  # peak_R ~ 3.88
    zone_b = FakeZone(center_price=27258.0, upper_bound=27260.0, lower_bound=27256.0)
    zone_a = FakeZone(center_price=27244.0, upper_bound=27246.0, lower_bound=27242.0)

    result = compute_zone_trail_target(tr, zone_b, all_zones=[zone_b, zone_a], current_zone_R=0.0)

    assert result is not None
    target_stop, advance_zone_R = result
    assert target_stop == 27246.0
    assert advance_zone_R == pytest.approx((27258.0 - 27226.0) / 8.25, rel=1e-3)


def test_short_first_advance_with_prior_zone_returns_prior_lower():
    """Short: touched zone below entry, prior zone above the touched one → trail to prior.lower_bound."""
    tr = _short_tracker()
    tr.update_mark(27270.0)  # peak_R ~ 3.75
    zone_b = FakeZone(center_price=27270.0, upper_bound=27272.0, lower_bound=27268.0)
    zone_a = FakeZone(center_price=27286.0, upper_bound=27288.0, lower_bound=27284.0)

    result = compute_zone_trail_target(tr, zone_b, all_zones=[zone_b, zone_a], current_zone_R=0.0)

    assert result is not None
    target_stop, _ = result
    assert target_stop == 27284.0  # prior zone (above) lower_bound


def test_no_prior_zone_falls_back_to_entry_plus_one_R():
    """Long: zone in open space (no prior zone between entry and current zone) → trail to entry + 1R."""
    tr = _long_tracker(entry=27226.0, stop=27217.75)  # 1R = 8.25
    tr.update_mark(27244.0)
    zone_b = FakeZone(center_price=27244.0, upper_bound=27246.0, lower_bound=27242.0)

    result = compute_zone_trail_target(tr, zone_b, all_zones=[zone_b], current_zone_R=0.0)

    assert result is not None
    target_stop, _ = result
    assert target_stop == pytest.approx(27226.0 + 8.25)  # entry + 1.0R


def test_same_zone_re_touched_returns_none():
    """If advance_zone_R <= current_zone_R, no trail (idempotent)."""
    tr = _long_tracker()
    tr.update_mark(27258.0)
    zone_b = FakeZone(center_price=27258.0, upper_bound=27260.0, lower_bound=27256.0)
    # current_zone_R already AT this zone's level
    advance_R_of_b = (27258.0 - 27226.0) / 8.25

    result = compute_zone_trail_target(tr, zone_b, all_zones=[zone_b], current_zone_R=advance_R_of_b)

    assert result is None


def test_zone_below_entry_for_long_returns_none():
    """Long: touched zone below entry — not a trade-direction advance, no trail."""
    tr = _long_tracker()
    tr.update_mark(27240.0)
    zone_below = FakeZone(center_price=27220.0, upper_bound=27222.0, lower_bound=27218.0)

    result = compute_zone_trail_target(tr, zone_below, all_zones=[zone_below], current_zone_R=0.0)

    assert result is None


def test_peak_R_below_2_returns_none():
    """Trail only fires when BE-lock has fired (peak_R >= 2.0)."""
    tr = _long_tracker()
    tr.update_mark(27240.0)  # peak_R ~ 1.7 — below 2.0
    zone = FakeZone(center_price=27240.0, upper_bound=27242.0, lower_bound=27238.0)

    result = compute_zone_trail_target(tr, zone, all_zones=[zone], current_zone_R=0.0)

    assert result is None


def test_zero_risk_unit_returns_none_safely():
    """Defensive: tracker with stop_price == entry_price yields zero risk_unit; helper returns None."""
    tr = PositionTracker()
    tr.on_fill("long", price=27226.0, size=1, stop_price=27226.0)
    tr.update_mark(27240.0)
    zone = FakeZone(center_price=27240.0, upper_bound=27242.0, lower_bound=27238.0)

    result = compute_zone_trail_target(tr, zone, all_zones=[zone], current_zone_R=0.0)

    assert result is None
```

- [ ] **Step 2: Run tests, verify failure**

Run: `cd backend && python -m pytest tests/test_zone_trail.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.market_data.zone_trail'`

- [ ] **Step 3: Implement the helper**

Create `backend/src/market_data/zone_trail.py`:

```python
"""Zone-trail target computation for in-position trail-on-continuation.

Pure module: no I/O, no side effects, no asyncio. Caller owns the
broker.modify_stop call.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

TICK_SIZE = 0.25
PEAK_R_TRAIL_THRESHOLD = 2.0  # match BE-lock threshold


def _round_tick(price: float) -> float:
    return round(price / TICK_SIZE) * TICK_SIZE


def compute_zone_trail_target(
    tracker,
    touched_zone,
    all_zones: list,
    current_zone_R: float,
) -> tuple[float, float] | None:
    """Compute (target_stop, advance_zone_R) for a zone advance, or None.

    Conditions (must all hold):
      - tracker is in position (not flat)
      - tracker.peak_R >= 2.0 (BE-lock has fired)
      - touched_zone is in trade direction past entry (above for long, below for short)
      - touched_zone's R-multiple > current_zone_R (genuine new advance)
      - risk_unit > 0 (computable R)

    Trail target:
      - prior zone exists between entry and touched_zone in trade direction:
          long → prior_zone.upper_bound
          short → prior_zone.lower_bound
      - no prior zone: fallback to entry + 1.0R (long) / entry - 1.0R (short)
    """
    if tracker.is_flat or tracker.entry_price <= 0:
        return None
    if tracker.peak_R < PEAK_R_TRAIL_THRESHOLD:
        return None
    risk_unit = abs(tracker.entry_price - tracker.stop_price)
    if risk_unit <= 0:
        return None

    side = tracker.side
    entry = tracker.entry_price

    # In-trade-direction check
    if side == "long":
        if touched_zone.center_price <= entry:
            return None
    elif side == "short":
        if touched_zone.center_price >= entry:
            return None
    else:
        return None

    # R-multiple of the touched zone
    if side == "long":
        advance_zone_R = (touched_zone.center_price - entry) / risk_unit
    else:
        advance_zone_R = (entry - touched_zone.center_price) / risk_unit

    # Idempotence: must be a NEW advance
    if advance_zone_R <= current_zone_R + 1e-6:
        return None

    # Find prior zone in trade direction (between entry and touched_zone)
    prior_zone = None
    if side == "long":
        candidates = [
            z for z in all_zones
            if z is not touched_zone
            and z.center_price > entry
            and z.center_price < touched_zone.center_price
        ]
        if candidates:
            prior_zone = max(candidates, key=lambda z: z.center_price)
    else:
        candidates = [
            z for z in all_zones
            if z is not touched_zone
            and z.center_price < entry
            and z.center_price > touched_zone.center_price
        ]
        if candidates:
            prior_zone = min(candidates, key=lambda z: z.center_price)

    if prior_zone is not None:
        target_stop = _round_tick(prior_zone.upper_bound if side == "long" else prior_zone.lower_bound)
    else:
        # Fallback: entry + 1.0R (long) / entry - 1.0R (short)
        if side == "long":
            target_stop = _round_tick(entry + risk_unit)
        else:
            target_stop = _round_tick(entry - risk_unit)

    return target_stop, advance_zone_R
```

- [ ] **Step 4: Run tests, verify pass**

Run: `cd backend && python -m pytest tests/test_zone_trail.py -v`
Expected: 7 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/src/market_data/zone_trail.py backend/tests/test_zone_trail.py
git commit -m "feat(zone-trail): pure helper compute_zone_trail_target"
```

---

## Task 2: Initialize current_zone_R in `_pending_trade`

**Files:**
- Modify: `backend/src/stocks/broker_adapter.py`

- [ ] **Step 1: Find the `_pending_trade` dict construction in `_execute_entry`**

Currently around [broker_adapter.py:976-1000](../../../backend/src/stocks/broker_adapter.py#L976). It builds the dict with side, size, stop_price, tp_price, etc.

- [ ] **Step 2: Add the field**

Inside the existing dict construction, add `"current_zone_R": 0.0,` alongside the other entry fields. Position is irrelevant — pick anywhere in the dict literal:

```python
        self._pending_trade = {
            "ts": now,
            "session_date": now.strftime("%Y-%m-%d"),
            "symbol": "NQ",
            "side": side,
            "size": size,
            "stop_price": stop_price,
            "tp_price": tp_price,
            "current_zone_R": 0.0,   # NEW — last zone advance level (in R units)
            "stop_ticks": stop_dist_ticks,
            ...
```

- [ ] **Step 3: Add a quick test that fresh entries carry the field**

Append to `backend/tests/test_zone_trail.py`:

```python
def test_pending_trade_initializes_current_zone_R():
    """A fresh _pending_trade carries current_zone_R = 0.0."""
    # This is a smoke test — full entry path is exercised by integration tests.
    # Just verify the constant is present in the dict template.
    import inspect

    from src.stocks import broker_adapter
    src = inspect.getsource(broker_adapter.TopstepXBrokerAdapter._execute_entry)
    assert '"current_zone_R": 0.0' in src
```

(Source inspection is fragile but cheap; replaces a heavy integration test for this one-line addition.)

- [ ] **Step 4: Commit**

```bash
git add backend/src/stocks/broker_adapter.py backend/tests/test_zone_trail.py
git commit -m "feat(broker): initialize current_zone_R=0 in _pending_trade"
```

---

## Task 3: Wire the in-position branch into level_monitor

**Files:**
- Modify: `backend/src/market_data/level_monitor.py`

- [ ] **Step 1: Import the helper**

Near the top of `level_monitor.py`:

```python
from .zone_trail import compute_zone_trail_target
```

- [ ] **Step 2: Insert the new branch**

In the in-position handler section ([level_monitor.py:~1540-1586](../../../backend/src/market_data/level_monitor.py#L1540)), find the existing branch chain:

```python
if rev.get("should_exit"):
    ...
elif not tr.locked_half_R and tr.peak_R >= 0.5 and ee_prob >= ee_thresh:
    ...
elif pyr.get("should_add"):
    ...
```

Insert a new branch BEFORE `pyramid_add` (so trail wins over pyramid — lock gains before adding more risk):

```python
elif tr.peak_R >= 2.0:
    # 4. Cont-trail: at a new zone in trade direction past entry,
    # trail stop to the previously-broken zone's edge. Idempotent
    # via current_zone_R (refuses to re-trail at the same level).
    pending = broker._pending_trade or {}
    current_zone_R = float(pending.get("current_zone_R") or 0.0)
    trail = compute_zone_trail_target(tr, zone, self._zones, current_zone_R)
    if trail is not None:
        target_stop, advance_zone_R = trail
        logger.info(
            "Cont-trail: peak_R=%.2f advance_zone_R=%.2f → trail stop to %.2f",
            tr.peak_R, advance_zone_R, target_stop,
        )
        asyncio.create_task(broker.modify_stop(target_stop))
        if pending:
            pending["current_zone_R"] = advance_zone_R
            broker._set_pending_trade(pending)
```

- [ ] **Step 3: Verify the branch order is correct**

Read the chain. Expected order (top-down priority):

1. `rev.should_exit` → flatten (highest priority, kills the trade)
2. `tr.locked_half_R` early-exit lock → flatten (second-highest, kills the trade at +0.5R weakness)
3. `tr.peak_R >= 2.0` cont-trail → trail stop (NEW — locks gains progressively)
4. `pyr.should_add` → add to position (lowest, only if no other branch fires)

The `elif` chain enforces this. Confirm each branch is mutually exclusive.

- [ ] **Step 4: Smoke-test the import**

```bash
cd backend && python -c "from src.market_data.level_monitor import LevelMonitor; print('import OK')"
```

- [ ] **Step 5: Commit**

```bash
git add backend/src/market_data/level_monitor.py
git commit -m "feat(level-monitor): cont-trail branch — trail stop to previously-broken zone"
```

---

## Task 4: End-to-end verification

- [ ] **Step 1: Run all new tests**

```bash
cd backend && python -m pytest tests/test_zone_trail.py -v
```

Expected: 8 passed (7 helper + 1 source-inspection).

- [ ] **Step 2: Run the broader market_data test suite for regression**

```bash
cd backend && python -m pytest tests/ -k "level_monitor or zone or broker_adapter" -v
```

Expected: no regressions in pre-existing tests.

- [ ] **Step 3: Push to feature branch + open PR**

```bash
git push -u origin feat/zone-aware-trail-on-cont
gh pr create --base main --title "feat(stocks): zone-aware trail-on-continuation" --body-file docs/superpowers/specs/2026-04-29-zone-aware-trail-on-cont.md
```

- [ ] **Step 4: Block deploy on dependency PR**

This PR's verification depends on broker-tracker-reconciliation being live first. Before deploying, check:

```bash
gh pr view <broker-tracker-reconciliation-PR-num> --json state
```

If state != MERGED, hold this PR's deploy until it is.

- [ ] **Step 5: Deploy carefully (after dep merged)**

- Confirm stocks position flat: `curl /api/stocks/runtime-status`.
- Merge this PR.
- Deploy: `ssh root@148.251.40.251 "bash /opt/arnold/scripts/server-deploy.sh rebuild backend"`.

- [ ] **Step 6: Live verification**

After deploy, the next time a long enters and runs to +2R+:

1. Watch for `Cont-trail: peak_R=X.XX advance_zone_R=Y.YY → trail stop to Z.ZZ` in logs.
2. Verify `runtime-status` shows the updated stop_price.
3. Verify the TopstepX dashboard shows the modified stop order at the new price.
4. If the trade reverses, verify it exits at the trail stop (not at BE+2 ticks).

Negative case (reversal at zone): if the trade hits a reversal signal at the new zone first, verify reversal_exit fires (priority 1) and trail does NOT fire.

---

## Self-review notes

- **Spec coverage:**
  - Component A → Task 1 (helper module)
  - Component B → Task 3 (level_monitor branch)
  - Component C → Task 2 (current_zone_R init)
  - Component D → Task 1 (tests) + Task 2 (smoke)
- **No placeholder steps.** Each step has actual code or actual command.
- **Type consistency:** `compute_zone_trail_target(tracker, zone, all_zones, current_zone_R)` signature matches between Task 1 (definition) and Task 3 (caller). Returns `tuple[float, float] | None` consistently.
- **Out of scope (per spec):** DQN retraining, multi-tier scaling out, multi-zone confluence weighting, per-trade trail tuning. None of those appear in any task.
- **Known fragility:** Task 2 Step 3's source-inspection test is fragile — if the dict construction is ever refactored, the test will need updating. Acceptable tradeoff for avoiding a heavy integration test for a one-line change.
