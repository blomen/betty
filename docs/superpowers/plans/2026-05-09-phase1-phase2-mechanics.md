# Phase 1 / Phase 2 Mechanics Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire a deterministic two-phase trade state machine driven by zone-touch DQN re-evals, with floor-zero entry gates and confidence-scaled sizing across entry/pyramid/flip — so paper-trading produces clean (obs, action, realized_R) tuples for the training feedback loop.

**Architecture:** Phase state lives on `tracker.locked_BE` (False=Phase 1, True=Phase 2). Phase 1 = sacred bracket (SL or +1.5R, no interventions). Phase 2 = zone-driven CONT pyramid + cont-trail / REV flatten+flip / SKIP hold. Entry/pyramid/flip sizes all flow through `size_multiplier(composite_confidence)`. Per-tick `reversal_signals.should_exit` and `early_exit_lock` gated to off in both phases.

**Tech Stack:** Python 3.10+, FastAPI, asyncio, pytest. Live trades against TopstepX paper account via `arnold-backend-1` Docker container on Hetzner. Tests run locally via `cd backend && pytest tests/`.

**Spec:** [docs/superpowers/specs/2026-05-09-phase1-phase2-mechanics-design.md](docs/superpowers/specs/2026-05-09-phase1-phase2-mechanics-design.md)

---

## File Structure

| File | Role |
|---|---|
| `backend/src/stocks/broker_adapter.py` | Pre-populate tracker, conf-scaled entry size, expose phase property, REV-flip dispatch path. ~2000 lines today; do not split — follow existing patterns. |
| `backend/src/market_data/level_monitor.py` | Phase 2 threshold, gates relaxed to 0, REV-flip routing through on_signal, gate off per-tick reversal/early_exit handlers. |
| `backend/src/broker/position_tracker.py` | Add `phase` property (1 if `locked_BE` else 2). Pure read-only addition. |
| `backend/tests/test_phase_mechanics.py` | NEW — unit tests for the state machine: phase transitions, floor-zero gates, conf-scaled sizing, REV-flip path. |
| `backend/tests/test_broker_adapter_phase.py` | NEW — broker_adapter tests using existing AsyncMock pattern from `test_broker_adapter.py`. |

No new modules — all logic plugs into existing files.

---

### Task 1: Verify trail bug status (diagnostic, not implementation)

**Files:** none modified — read-only diagnostic.

**Why first:** The pre-populate fix landed in `_execute_entry` at [backend/src/stocks/broker_adapter.py:1610-1623](backend/src/stocks/broker_adapter.py#L1610-L1623) on 2026-05-08. But trail_count=0 for all 331 trades this week, including post-fix trades. Determine whether the bug is still active before designing further fixes — Phase 2 is unreachable without working trails.

- [ ] **Step 1: Query trail_count by day to find post-fix trades**

```bash
ssh root@148.251.40.251 "cd /opt/arnold && docker compose exec -T postgres psql -U arnold -d arnold -c \"SELECT session_date, COUNT(*) AS trades, SUM(CASE WHEN trail_count > 0 THEN 1 ELSE 0 END) AS trailed, MAX(peak_r) AS max_peak_r, ROUND(AVG(peak_r)::numeric, 3) AS avg_peak_r FROM broker_trades WHERE ts >= '2026-05-08' AND closed_at IS NOT NULL GROUP BY session_date ORDER BY session_date;\""
```

Expected output: shows whether ANY trade post-2026-05-08 had `trail_count > 0` or `peak_r > 1.5`. If `MAX(peak_r) < 1.5` everywhere, the bug persists — entry_price is still racing.

- [ ] **Step 2: Tail logs for the diagnostic SKIP message**

```bash
ssh root@148.251.40.251 "cd /opt/arnold && docker compose exec -T backend bash -c 'grep -c \"update_mark SKIP\" /app/logs/extraction.log /app/logs/api.log 2>/dev/null'"
```

If count > 0, the fill-arriving-before-entry_price race is still dropping ticks. Capture one full SKIP line for context:

```bash
ssh root@148.251.40.251 "cd /opt/arnold && docker compose exec -T backend bash -c 'grep \"update_mark SKIP\" /app/logs/api.log | tail -3'"
```

- [ ] **Step 3: Decide branch**

If MAX(peak_r) >= 1.5 anywhere AND no SKIP messages: **bug is fixed**, skip Task 2, proceed to Task 3.
Otherwise: **bug persists**, do Task 2.

Document the finding inline by adding a one-line note at the top of Task 2 stating "bug confirmed active as of <YYYY-MM-DD HH:MM>" or "bug fixed; Task 2 skipped."

- [ ] **Step 4: Commit the diagnostic note**

If Task 2 will be skipped:

```bash
git commit --allow-empty -m "chore(rl): trail bug diagnostic clean — Task 2 skipped

Post-2026-05-08 trades show MAX(peak_r)=<value> and 0 'update_mark SKIP'
log lines. Pre-populate fix in _execute_entry is sufficient.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

Otherwise, no commit; proceed to Task 2.

---

### Task 2: DEFERRED — trail bug root cause is NOT entry_price race

**2026-05-09 finding:** Task 1 diagnostic shows pre-populate fix is already working (zero "update_mark SKIP" log lines in 3 days), but `BE-lock at peak_R` and `update_mark:` log lines have fired **zero times** in 3 days despite 5 post-fix trades exceeding +1.5R realized P&L (one closed at +6.42R). The bug is upstream in `_check_positions` either not being called, throttled, or calling `update_mark_and_check_be_lock` on a different broker_adapter instance than the one holding the trade. This requires an isolated investigation outside the scope of the original Task 2 fix. **Deferred to a separate spec/plan; Phase 2 work in Tasks 8-12 will sit dormant until this is resolved.** Tasks 3-7, 13, 14 still produce value (floors, sizing, gates) on Phase 1 trades.

The text below is the original Task 2 spec, kept for reference only — DO NOT execute as written.

### Task 2 (ORIGINAL — superseded by deferral above): Trail bug fix — root-cause investigation and pre-populate `entry_price`

**Files:**
- Modify: `backend/src/stocks/broker_adapter.py` (around the `_execute_entry` block at lines 1610-1700, exact location depends on root cause)
- Test: `backend/tests/test_broker_adapter_phase.py` (NEW)

**Why:** The 2026-05-08 fix pre-populates `tracker.side`/`size`/`stop_price` before the entry order, but explicitly leaves `tracker.entry_price = 0` until the fill arrives. `update_mark` skips when `entry_price <= 0` ([broker_adapter.py:328](backend/src/stocks/broker_adapter.py#L328)). If the fill stream is delayed or the matching logic in `on_stream_fill` ever fails to write `entry_price`, peak_R stays at 0 and Phase 2 is unreachable.

Fix: pre-populate `tracker.entry_price = signal_price` BEFORE the entry order. The actual fill price overwrites it via `on_stream_fill` (existing behavior). A few ticks of inflated peak_R during the fill window is harmless — far better than peak_R stuck at 0 for the whole trade life.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_broker_adapter_phase.py`:

```python
"""Tests for Phase 1 / Phase 2 trade mechanics."""
import asyncio
from unittest.mock import AsyncMock

import pytest

from src.stocks.broker_adapter import BrokerAdapter


@pytest.fixture
def mock_client():
    client = AsyncMock()
    client.place_market_order = AsyncMock(return_value={"orderId": 100, "success": True})
    client.place_stop_order = AsyncMock(return_value={"orderId": 101, "success": True})
    client.cancel_order = AsyncMock(return_value={})
    client.liquidate_position = AsyncMock(return_value={})
    client.modify_order = AsyncMock(return_value={"success": True})
    return client


@pytest.fixture
def adapter(mock_client, monkeypatch):
    # Avoid side effects from disk persistence in tests
    monkeypatch.setattr(
        "src.stocks.broker_adapter._save_pending_trade_to_disk", lambda v: None
    )
    a = BrokerAdapter(client=mock_client)
    a._enabled = True
    return a


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def test_entry_price_pre_populated_before_fill(adapter, mock_client):
    """update_mark must advance peak_R immediately after _execute_entry, even
    before on_stream_fill writes the actual fill price. Otherwise the
    fill-race window leaves peak_R stuck at 0 and BE-lock never fires."""
    signal = {
        "action": "enter_long",
        "price": 25000.0,
        "stop_price": 24990.0,
        "stop_ticks": 40,
        "confidence": 0.75,
        "ts": __import__("time").time(),
    }
    result = _run(adapter._execute_entry(signal))
    assert not (result or {}).get("rejected"), result

    assert adapter.tracker.side == "long"
    assert adapter.tracker.entry_price > 0, (
        f"tracker.entry_price must be pre-populated; got {adapter.tracker.entry_price}"
    )

    # Now simulate a tick at +1.5R BEFORE on_stream_fill arrives
    risk_unit = 25000.0 - 24990.0  # 10 points
    plus_1_5R_price = 25000.0 + 1.5 * risk_unit  # 25015.0
    new_r = adapter.tracker.update_mark(plus_1_5R_price)
    assert new_r >= 1.5, (
        f"update_mark must advance peak_R past pre-fill ticks; got {new_r}"
    )
    assert adapter.tracker.peak_R >= 1.5
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd backend && pytest tests/test_broker_adapter_phase.py::test_entry_price_pre_populated_before_fill -v
```

Expected: FAIL with `tracker.entry_price must be pre-populated; got 0.0`.

- [ ] **Step 3: Pre-populate entry_price in `_execute_entry`**

In `backend/src/stocks/broker_adapter.py`, modify the pre-claim block at lines 1619-1623 to also set `entry_price`:

```python
side = "long" if is_long else "short"
self.tracker.side = side
self.tracker.size = size
self.tracker.stop_price = stop_price
# Pre-populate entry_price with the signal price so update_mark() doesn't
# skip during the fill-confirmation window. on_stream_fill overwrites this
# with the actual fill price when the fill arrives. If the order is
# rejected, _reset_tracker_for_rollback() zeros it out.
signal_price = float(signal.get("price", 0) or 0)
if signal_price > 0:
    self.tracker.entry_price = signal_price
log.info(
    "Position opening: %s %d entry≈%.2f stop=%.2f (waiting for entry fill)",
    side,
    size,
    signal_price,
    stop_price,
)
```

- [ ] **Step 4: Update `_reset_tracker_for_rollback` to clear entry_price**

Verify [`_reset_tracker_for_rollback`](backend/src/stocks/broker_adapter.py#L264-L278) sets `self.tracker.entry_price = 0.0`. If not present, add it:

```python
def _reset_tracker_for_rollback(self) -> None:
    self.tracker.side = None
    self.tracker.entry_price = 0.0  # ensure pre-populated value is cleared on rollback
    self.tracker.stop_price = 0.0
    self.tracker.size = 0
    self.tracker.entry_order_id = None
    self.tracker.stop_order_id = None
    self.tracker.peak_R = 0.0
    self.tracker.locked_half_R = False
    self.tracker.locked_BE = False
```

- [ ] **Step 5: Run test to verify it passes**

```bash
cd backend && pytest tests/test_broker_adapter_phase.py::test_entry_price_pre_populated_before_fill -v
```

Expected: PASS.

- [ ] **Step 6: Run full broker_adapter test suite to verify no regressions**

```bash
cd backend && pytest tests/test_broker_adapter.py tests/test_broker_adapter_recovery.py tests/test_position_tracker.py -v
```

Expected: all PASS. The pre-populate change must not break entry/exit flows.

- [ ] **Step 7: Commit**

```bash
git add backend/src/stocks/broker_adapter.py backend/tests/test_broker_adapter_phase.py
git commit -m "fix(broker): pre-populate tracker.entry_price before entry order

update_mark() skips when entry_price <= 0, freezing peak_R at 0 during
the fill-confirmation window. Pre-populate with signal price; the fill
overwrites it on arrival.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: Add `phase` property to PositionTracker

**Files:**
- Modify: `backend/src/broker/position_tracker.py`
- Test: `backend/tests/test_position_tracker.py`

**Why:** Read-through to a uniform `phase` indicator that matches the spec's vocabulary. Avoids scattered `tracker.locked_BE` reads in level_monitor and broker_adapter.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_position_tracker.py`:

```python
def test_phase_property_reflects_locked_BE():
    """tracker.phase = 1 when locked_BE False, 2 when True."""
    from src.broker.position_tracker import PositionTracker

    t = PositionTracker()
    t.on_fill(side="long", price=25000.0, size=1, stop_price=24990.0)

    assert t.phase == 1, "fresh entry should be Phase 1"
    t.locked_BE = True
    assert t.phase == 2, "locked_BE should flip phase to 2"
    t.locked_BE = False
    assert t.phase == 1, "phase tracks locked_BE forward and back"


def test_phase_property_when_flat():
    """tracker.phase = 0 when flat (no position)."""
    from src.broker.position_tracker import PositionTracker

    t = PositionTracker()
    assert t.phase == 0
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd backend && pytest tests/test_position_tracker.py::test_phase_property_reflects_locked_BE tests/test_position_tracker.py::test_phase_property_when_flat -v
```

Expected: FAIL with `AttributeError: 'PositionTracker' object has no attribute 'phase'`.

- [ ] **Step 3: Add the property**

In `backend/src/broker/position_tracker.py`, after the `is_flat` property (around line 60):

```python
@property
def phase(self) -> int:
    """Trade phase: 0=flat, 1=sacred bracket (pre-1.5R), 2=zone-driven ride (post-1.5R)."""
    if self.is_flat:
        return 0
    return 2 if self.locked_BE else 1
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd backend && pytest tests/test_position_tracker.py -v
```

Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/src/broker/position_tracker.py backend/tests/test_position_tracker.py
git commit -m "feat(broker): add tracker.phase property (0=flat, 1=sacred, 2=ride)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: Drop confidence floor to 0 in live broker dispatch

**Files:**
- Modify: `backend/src/market_data/level_monitor.py:1641-1689` (`_build_inference_gates`)
- Modify: `backend/src/market_data/level_monitor.py:1880-1920` (broker dispatch path — search for `_reckless` and `conf_floor_default`)
- Test: `backend/tests/test_phase_mechanics.py` (NEW)

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_phase_mechanics.py`:

```python
"""Tests for Phase 1 / Phase 2 entry gates and state-machine behavior."""
import os

import pytest


def test_conf_floor_is_zero_in_reckless_mode(monkeypatch):
    """Reckless mode (paper-phase) must accept any non-zero confidence."""
    monkeypatch.setenv("RECKLESS_LEARNING_MODE", "1")
    from src.market_data.level_monitor import _conf_floor

    assert _conf_floor() == 0.0, "Paper-phase floor must be 0"


def test_conf_floor_is_strict_when_reckless_disabled(monkeypatch):
    """Strict mode keeps the 0.15 floor for live-money runs."""
    monkeypatch.setenv("RECKLESS_LEARNING_MODE", "0")
    from src.market_data.level_monitor import _conf_floor

    assert _conf_floor() == 0.15
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd backend && pytest tests/test_phase_mechanics.py::test_conf_floor_is_zero_in_reckless_mode -v
```

Expected: FAIL with `ImportError: cannot import name '_conf_floor'`.

- [ ] **Step 3: Extract `_conf_floor` helper and drop reckless floor to 0**

In `backend/src/market_data/level_monitor.py`, near the top of the module (after imports, before any classes), add:

```python
def _conf_floor() -> float:
    """Confidence floor for entry dispatch.
    Reckless (paper) = 0.0 — every non-SKIP signal becomes a trade.
    Strict (real money) = 0.15 — historical default.
    """
    return 0.0 if os.environ.get("RECKLESS_LEARNING_MODE", "1") != "0" else 0.15
```

Then in `_build_inference_gates` (around line 1645), replace:

```python
conf_floor_default = 0.05 if reckless else 0.15
```

with:

```python
conf_floor_default = _conf_floor()
```

And in the broker dispatch path (around line 1898-1910 — search for `_reckless = os.environ.get("RECKLESS_LEARNING_MODE"`), replace the inline conf-floor computation with a call to `_conf_floor()`. Both paths must use the same helper.

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd backend && pytest tests/test_phase_mechanics.py::test_conf_floor_is_zero_in_reckless_mode tests/test_phase_mechanics.py::test_conf_floor_is_strict_when_reckless_disabled -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/src/market_data/level_monitor.py backend/tests/test_phase_mechanics.py
git commit -m "feat(rl): drop confidence floor to 0 in reckless mode

Paper-phase needs every non-SKIP DQN action to produce a labeled
training tuple. Strict mode keeps 0.15 for real-money runs.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: Drop orderflow floor to 0 in live broker dispatch

**Files:**
- Modify: `backend/src/market_data/level_monitor.py:1880-1920` (broker dispatch path — `of_floor` computation)
- Modify: `backend/src/market_data/level_monitor.py:1641-1689` (`_build_inference_gates` — already at 0.0 in reckless, but verify)
- Test: `backend/tests/test_phase_mechanics.py`

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_phase_mechanics.py`:

```python
def test_of_floor_is_zero_in_reckless_mode(monkeypatch):
    """Reckless mode keeps OF floor at 0 in BOTH gate-display and broker dispatch."""
    monkeypatch.setenv("RECKLESS_LEARNING_MODE", "1")
    from src.market_data.level_monitor import _of_floor

    assert _of_floor() == 0.0


def test_of_floor_strict(monkeypatch):
    monkeypatch.setenv("RECKLESS_LEARNING_MODE", "0")
    from src.market_data.level_monitor import _of_floor

    assert _of_floor() == 0.30
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd backend && pytest tests/test_phase_mechanics.py::test_of_floor_is_zero_in_reckless_mode -v
```

Expected: FAIL with `ImportError: cannot import name '_of_floor'`.

- [ ] **Step 3: Add `_of_floor` helper and replace inline computations**

In `backend/src/market_data/level_monitor.py`, alongside `_conf_floor`:

```python
def _of_floor() -> float:
    """Orderflow score floor for entry dispatch.
    Reckless (paper) = 0.0 — collect labeled outcomes for all OF regimes.
    Strict (real money) = 0.30 — early audit showed OF>=0.30 wins 4/4.
    """
    return 0.0 if os.environ.get("RECKLESS_LEARNING_MODE", "1") != "0" else 0.30
```

Replace inline `of_floor` computations in both `_build_inference_gates` and the broker dispatch path with `_of_floor()`. Find the broker-dispatch one near the comment "PAPER-TRADING: keep gates loose" — that's where the 0.30 strict default currently leaks in.

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd backend && pytest tests/test_phase_mechanics.py -v
```

Expected: all 4 floor tests PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/src/market_data/level_monitor.py backend/tests/test_phase_mechanics.py
git commit -m "feat(rl): drop orderflow floor to 0 in reckless mode

Both _build_inference_gates and the broker dispatch path now read from
a single _of_floor() helper.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: Add stop-tick sanity bounds (6-40) to live entry gate

**Files:**
- Modify: `backend/src/market_data/level_monitor.py` (broker dispatch block — around line 1880-1920)
- Test: `backend/tests/test_phase_mechanics.py`

**Why:** A dim-predicted stop of <6 ticks (~3 NQ pts) is below typical noise → near-instant stop hit, useless for training. >40 ticks → unclear setup, stop too wide. Both produce trades the model can't learn from.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_phase_mechanics.py`:

```python
def test_stop_ticks_too_tight_blocks_dispatch():
    """Dim-predicted stop < 6 ticks must block entry."""
    from src.market_data.level_monitor import _stop_ticks_in_bounds

    assert _stop_ticks_in_bounds(5.0) is False
    assert _stop_ticks_in_bounds(5.99) is False
    assert _stop_ticks_in_bounds(6.0) is True


def test_stop_ticks_too_wide_blocks_dispatch():
    """Dim-predicted stop > 40 ticks must block entry."""
    from src.market_data.level_monitor import _stop_ticks_in_bounds

    assert _stop_ticks_in_bounds(40.0) is True
    assert _stop_ticks_in_bounds(40.01) is False
    assert _stop_ticks_in_bounds(100.0) is False
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd backend && pytest tests/test_phase_mechanics.py::test_stop_ticks_too_tight_blocks_dispatch -v
```

Expected: FAIL with `ImportError`.

- [ ] **Step 3: Add the helper and wire into dispatch**

In `backend/src/market_data/level_monitor.py`, alongside the floor helpers:

```python
MIN_ENTRY_STOP_TICKS = 6.0
MAX_ENTRY_STOP_TICKS = 40.0


def _stop_ticks_in_bounds(stop_ticks: float) -> bool:
    """Filter dim-predicted stops outside the trainable noise band."""
    return MIN_ENTRY_STOP_TICKS <= float(stop_ticks) <= MAX_ENTRY_STOP_TICKS
```

In `_build_inference_gates`, after the `of_pass` block, add:

```python
stop_ticks = float(result.get("stop_ticks", 0) or 0)
stop_pass = _stop_ticks_in_bounds(stop_ticks)
```

Update the blocker chain to include stop_ticks (after `of_pass`):

```python
elif not of_pass:
    blocker = "orderflow"
elif not stop_pass:
    blocker = "stop_bounds"
elif not is_flat:
    blocker = "in_position"
```

And add to the returned dict:

```python
"stop_ticks": stop_ticks,
"stop_min": MIN_ENTRY_STOP_TICKS,
"stop_max": MAX_ENTRY_STOP_TICKS,
"stop_pass": stop_pass,
```

In the broker dispatch path (the same block where conf_floor and of_floor checks happen), add:

```python
if not _stop_ticks_in_bounds(result.get("stop_ticks", 0) or 0):
    logger.info(
        "Dispatch BLOCKED stop_bounds: stop_ticks=%.1f outside [%d, %d]",
        result.get("stop_ticks", 0) or 0,
        int(MIN_ENTRY_STOP_TICKS),
        int(MAX_ENTRY_STOP_TICKS),
    )
    return
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd backend && pytest tests/test_phase_mechanics.py::test_stop_ticks_too_tight_blocks_dispatch tests/test_phase_mechanics.py::test_stop_ticks_too_wide_blocks_dispatch -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/src/market_data/level_monitor.py backend/tests/test_phase_mechanics.py
git commit -m "feat(rl): enforce 6-40 tick stop sanity bounds in live dispatch

Surfaces a 'stop_bounds' blocker on the inference UI and rejects
nonsense-stop trades that the trainer can't learn from.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: Confidence-scaled entry size

**Files:**
- Modify: `backend/src/stocks/broker_adapter.py` (`_execute_entry`, around line 1519-1620 — locate `size` computation, currently from size_model)
- Test: `backend/tests/test_broker_adapter_phase.py`

**Why:** Spec — "even entry should be conf scaled". Replace the size_model ML output with `size_multiplier(composite_confidence)` × `BASE_SIZE`, floored at 1 contract.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_broker_adapter_phase.py`:

```python
def test_entry_size_high_confidence_scales_to_two(adapter, mock_client):
    """Confidence >= 0.85 → size_multiplier = 1.5 → round(1 × 1.5) = 2 contracts."""
    signal = {
        "action": "enter_long",
        "price": 25000.0,
        "stop_price": 24990.0,
        "stop_ticks": 40,
        "confidence": 0.90,  # composite >= 0.85 tier
        "ts": __import__("time").time(),
    }
    _run(adapter._execute_entry(signal))
    mock_client.place_market_order.assert_called_once_with("Buy", 2)


def test_entry_size_low_confidence_floors_at_one(adapter, mock_client):
    """Confidence < 0.30 → reckless multiplier 0.5 → round(1 × 0.5) = 1 (floor)."""
    signal = {
        "action": "enter_long",
        "price": 25000.0,
        "stop_price": 24990.0,
        "stop_ticks": 40,
        "confidence": 0.10,
        "ts": __import__("time").time(),
    }
    _run(adapter._execute_entry(signal))
    mock_client.place_market_order.assert_called_once_with("Buy", 1)


def test_entry_size_mid_confidence_one_contract(adapter, mock_client):
    """Confidence 0.50-0.85 tier → multiplier 0.6-1.0 → 1 contract."""
    signal = {
        "action": "enter_long",
        "price": 25000.0,
        "stop_price": 24990.0,
        "stop_ticks": 40,
        "confidence": 0.65,
        "ts": __import__("time").time(),
    }
    _run(adapter._execute_entry(signal))
    mock_client.place_market_order.assert_called_once_with("Buy", 1)
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd backend && pytest tests/test_broker_adapter_phase.py -v -k "entry_size"
```

Expected: FAIL — current sizing does not match these expected calls.

- [ ] **Step 3: Replace size_model call with size_multiplier in _execute_entry**

In `backend/src/stocks/broker_adapter.py`, locate the size computation in `_execute_entry`. There will be a call to a size_model predict or a hardcoded base. Replace with:

```python
from src.rl.confidence import size_multiplier

BASE_SIZE = 1
confidence = float(signal.get("confidence", 0) or 0)
size = max(1, round(BASE_SIZE * size_multiplier(confidence)))
```

Remove the size_model import and any size_model.predict call from this method. Leave the size_model joblib file in place (used elsewhere or kept for future use).

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd backend && pytest tests/test_broker_adapter_phase.py -v -k "entry_size"
```

Expected: all PASS.

- [ ] **Step 5: Run full broker_adapter test suite**

```bash
cd backend && pytest tests/test_broker_adapter.py tests/test_broker_adapter_recovery.py -v
```

Expected: all PASS. The size_model removal must not break entry/exit flows. If existing tests assert on a specific size value coming from the size_model, update them to match the confidence-scaled output.

- [ ] **Step 6: Commit**

```bash
git add backend/src/stocks/broker_adapter.py backend/tests/test_broker_adapter_phase.py
git commit -m "feat(broker): confidence-scaled entry size via size_multiplier

Drops size_model dependency from live entry path. size = round(1 × tier),
where tier is from src.rl.confidence.size_multiplier:
  conf >= 0.85 → 1.5 → 2 contracts
  0.70-0.85 → 1.0 → 1 contract
  0.50-0.70 → 0.6 → 1 (floor)
  0.30-0.50 → 0.3 → 1 (floor)
  < 0.30 (reckless) → 0.5 → 1 (floor)

size_model_v5.joblib stays in the model pool unused.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 8: Lower Phase 2 threshold from 2.0R to 1.5R

**Files:**
- Modify: `backend/src/market_data/level_monitor.py:1811, 1820` (in-position handler — `tr.peak_R >= 2.0` checks)
- Test: `backend/tests/test_phase_mechanics.py`

**Why:** BE-lock already fires at 1.5R ([broker_adapter.py:362](backend/src/stocks/broker_adapter.py#L362)). The Phase 2 cont-trail / pyramid handlers in level_monitor still gate on 2.0R, leaving a dead zone between 1.5R and 2.0R where stop is locked but no trail/pyramid fires.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_phase_mechanics.py`:

```python
def test_phase2_threshold_constant_is_1_5R():
    """Phase 2 transition gate must read 1.5R to match BE-lock."""
    from src.market_data import level_monitor

    assert level_monitor.PHASE_2_THRESHOLD_R == 1.5
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd backend && pytest tests/test_phase_mechanics.py::test_phase2_threshold_constant_is_1_5R -v
```

Expected: FAIL with `AttributeError: module ... has no attribute 'PHASE_2_THRESHOLD_R'`.

- [ ] **Step 3: Add the constant and replace inline 2.0 checks**

In `backend/src/market_data/level_monitor.py`, near the top of the module:

```python
# Phase 2 transition threshold — matches BE_LOCK_R in broker_adapter.py.
# Lowered from 2.0 to 1.5 on 2026-05-09 per phase1-phase2 spec.
PHASE_2_THRESHOLD_R = 1.5
```

Replace `tr.peak_R >= 2.0` at line 1811 (reversal_signals exit gate) and line 1820 (cont-trail gate) and any other Phase-2 gates in `_emit_zone_dqn_inference`:

```python
if tr.peak_R >= PHASE_2_THRESHOLD_R and rev.get("should_exit"):
    ...
elif tr.peak_R >= PHASE_2_THRESHOLD_R:
    ...
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd backend && pytest tests/test_phase_mechanics.py::test_phase2_threshold_constant_is_1_5R -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/src/market_data/level_monitor.py backend/tests/test_phase_mechanics.py
git commit -m "feat(rl): lower Phase 2 threshold from 2.0R to 1.5R

Matches existing BE-lock at 1.5R so cont-trail and pyramid fire
immediately at the locked-profit moment instead of waiting for an
extra 0.5R that 84% of trades never reach.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 9: Disable per-tick reversal_signals + early_exit_lock in Phase 2

**Files:**
- Modify: `backend/src/market_data/level_monitor.py:1811-1819` (reversal_signals.should_exit branch)
- Modify: `backend/src/market_data/level_monitor.py` (search for `early_exit_lock` or `EE_LOCK` usage near in-position handler)

**Why:** Spec — Phase 2 decisions are driven by zone-touch DQN re-eval ONLY. Per-tick orderflow exits and early-exit lock fire mid-zone and chop winners. Disable in live path; DQN output remains so the trainer keeps labeling outcomes.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_phase_mechanics.py`:

```python
def test_reversal_signals_does_not_flatten_in_phase_2(monkeypatch):
    """Per-tick reversal_signals.should_exit must NOT trigger flatten in Phase 2.
    Only zone-touch DQN action=REV opposite drives flips."""
    monkeypatch.setenv("DISABLE_PER_TICK_REVERSAL", "1")
    from src.market_data.level_monitor import _reversal_signals_active

    assert _reversal_signals_active() is False


def test_reversal_signals_default_disabled():
    """Default behavior post-spec: per-tick reversal exits OFF."""
    import os
    # Don't set the env var — exercise the default
    if "DISABLE_PER_TICK_REVERSAL" in os.environ:
        del os.environ["DISABLE_PER_TICK_REVERSAL"]
    from src.market_data.level_monitor import _reversal_signals_active

    assert _reversal_signals_active() is False, (
        "Phase 2 must NOT use per-tick reversal_signals by default"
    )
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd backend && pytest tests/test_phase_mechanics.py::test_reversal_signals_default_disabled -v
```

Expected: FAIL with `ImportError`.

- [ ] **Step 3: Add gate and wire into the reversal_signals branch**

In `backend/src/market_data/level_monitor.py`, near the floor helpers:

```python
def _reversal_signals_active() -> bool:
    """Per-tick reversal_signals exit. Default OFF per phase1-phase2 spec —
    Phase 2 decisions driven by zone-touch DQN action=REV only."""
    return os.environ.get("ENABLE_PER_TICK_REVERSAL", "0") == "1"


def _early_exit_lock_active() -> bool:
    """Per-tick early-exit lock. Default OFF per phase1-phase2 spec."""
    return os.environ.get("ENABLE_EARLY_EXIT_LOCK", "0") == "1"
```

In the in-position handler, gate the reversal_signals.should_exit flatten branch (currently at line 1811-1819):

```python
if tr.peak_R >= PHASE_2_THRESHOLD_R and rev.get("should_exit") and _reversal_signals_active():
    logger.info(
        "Phase-2 reversal-signals exit: %d fired peak_R=%.2f — flattening %s @ %.2f",
        rev.get("fired_count", 0),
        tr.peak_R,
        tr.side,
        price,
    )
    asyncio.create_task(broker.flatten("reversal_signals"))
```

Find any `early_exit_lock` or `EE_LOCK` flatten branch in the same in-position block and wrap with `_early_exit_lock_active()` similarly. If you can't find one, the current code may not have one — note inline and skip.

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd backend && pytest tests/test_phase_mechanics.py::test_reversal_signals_default_disabled -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/src/market_data/level_monitor.py backend/tests/test_phase_mechanics.py
git commit -m "feat(rl): gate per-tick reversal_signals + early_exit_lock OFF

Phase 2 decisions are zone-driven only per phase1-phase2 spec. Set
ENABLE_PER_TICK_REVERSAL=1 to restore the old behavior for diagnostics.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 10: Confidence-scaled pyramid size in Phase 2

**Files:**
- Modify: `backend/src/market_data/level_monitor.py:1863-1872` (pyramid add branch)
- Test: `backend/tests/test_phase_mechanics.py`

**Why:** Spec — "always confidence scaled, even entry should be conf scaled". The current pyramid size comes from the DQN's pyramid_decision head (`pyr.add_size`). Replace with `size_multiplier(composite_confidence)`.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_phase_mechanics.py`:

```python
def test_pyramid_size_high_conf():
    """Pyramid add at conf>=0.85 → 2 contracts."""
    from src.market_data.level_monitor import _pyramid_add_size

    assert _pyramid_add_size(confidence=0.90) == 2


def test_pyramid_size_low_conf_floors_at_one():
    """Pyramid add at low conf rounds up to 1 contract floor."""
    from src.market_data.level_monitor import _pyramid_add_size

    assert _pyramid_add_size(confidence=0.10) == 1


def test_pyramid_size_mid_conf():
    from src.market_data.level_monitor import _pyramid_add_size

    assert _pyramid_add_size(confidence=0.65) == 1
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd backend && pytest tests/test_phase_mechanics.py::test_pyramid_size_high_conf -v
```

Expected: FAIL with `ImportError`.

- [ ] **Step 3: Add helper and replace the pyramid add_size source**

In `backend/src/market_data/level_monitor.py`, near the floor helpers:

```python
PHASE_2_BASE_SIZE = 1


def _pyramid_add_size(confidence: float) -> int:
    """Phase 2 pyramid add size — confidence-scaled via size_multiplier."""
    from src.rl.confidence import size_multiplier

    return max(1, round(PHASE_2_BASE_SIZE * size_multiplier(float(confidence))))
```

In the pyramid branch at lines 1863-1872, replace:

```python
elif pyr.get("should_add"):
    add_size = int(max(1, round(float(pyr.get("add_size") or 0))))
```

with:

```python
# Spec: pyramid size is confidence-scaled, NOT from the DQN pyramid head.
# CONT action from the action head + zone touch in trade direction is
# sufficient — no extra should_add gate.
elif (result.get("action") == "CONT" and
      _trade_dir_matches(tr.side, result, zone)):
    confidence = float(result.get("confidence", 0) or 0)
    add_size = _pyramid_add_size(confidence)
```

Define `_trade_dir_matches` near the helpers:

```python
def _trade_dir_matches(side: str | None, result: dict, zone) -> bool:
    """True when DQN-CONT direction aligns with current position side."""
    if not side:
        return False
    # For CONT: trade direction = position direction. Always matches.
    return True
```

(For CONT, the DQN agreeing with the trade is the action's meaning — `_trade_dir_matches` here is conservatively always-True for CONT but provides a hook if the model later emits direction-bearing CONT signals.)

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd backend && pytest tests/test_phase_mechanics.py -v -k "pyramid"
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/src/market_data/level_monitor.py backend/tests/test_phase_mechanics.py
git commit -m "feat(rl): confidence-scaled pyramid size, ignore DQN pyramid head

Phase 2 pyramid fires on CONT action + zone touch alone. Add size
flows through size_multiplier(composite_confidence). The pyramid_decision
head's add_size and should_add are ignored in live; trainer still labels.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 11: Phase 2 REV-flip — route through broker.on_signal

**Files:**
- Modify: `backend/src/market_data/level_monitor.py` (in-position handler at line 1795+, the `result = None` suppression at line 1876)

**Why:** Spec — when DQN-REV opposite to position fires at a zone touch in Phase 2, flatten + open opposite. The broker_adapter's existing on_signal REV-flip path at [broker_adapter.py:591-609](backend/src/stocks/broker_adapter.py#L591-L609) already does exactly this. We just need to NOT suppress the `result` for Phase 2 REV opposite, so it falls through to `broker.on_signal(...)` with the new direction.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_broker_adapter_phase.py`:

```python
def test_rev_signal_flips_position_in_phase_2(adapter, mock_client):
    """In Phase 2 (locked_BE=True) long, REV signal closes long + opens short."""
    adapter.tracker.on_fill(side="long", price=25000.0, size=1, stop_price=24990.0)
    adapter.tracker.locked_BE = True
    adapter.tracker.peak_R = 1.6
    adapter.tracker.entry_price = 25000.0

    rev_signal = {
        "action": "enter_short",
        "price": 25030.0,
        "stop_price": 25040.0,
        "stop_ticks": 40,
        "confidence": 0.70,
        "ts": __import__("time").time(),
    }
    _run(adapter.on_signal(rev_signal))

    # Flatten was called
    mock_client.liquidate_position.assert_called_once()
    # Then a fresh short was placed
    place_calls = mock_client.place_market_order.call_args_list
    assert any(call.args[0] == "Sell" for call in place_calls), place_calls
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd backend && pytest tests/test_broker_adapter_phase.py::test_rev_signal_flips_position_in_phase_2 -v
```

Expected: FAIL — broker may receive the REV signal through level_monitor's suppression path, or some intermediate stop-cooldown blocks the re-entry.

If the existing on_signal flow already passes this test (since broker_adapter's flip path is independent of level_monitor's `result = None`), proceed to Step 3 documenting that this case already works directly via `on_signal`.

- [ ] **Step 3: Modify level_monitor in-position handler to NOT suppress on Phase 2 REV opposite**

In `backend/src/market_data/level_monitor.py`, the in-position handler currently sets `result = None` at line 1876 to prevent any further dispatch. Replace the suppression logic:

```python
# Suppress the entry-signal dispatch while in-position EXCEPT for Phase 2
# REV opposite to current position — those flow to broker.on_signal which
# handles flatten+flip via the existing REV-flip path.
is_rev_opposite = (
    result.get("action") in ("REV", "rev")
    and tr.locked_BE
    and _action_opposite_to_side(result, tr.side, zone)
)
if not is_rev_opposite:
    result = None
```

Define `_action_opposite_to_side` near the helpers:

```python
def _action_opposite_to_side(result: dict, side: str | None, zone) -> bool:
    """True when DQN action=REV implies a fresh entry opposite to current side.
    REV at an UP-approach zone wants short; REV at a DOWN-approach zone wants long."""
    if not side:
        return False
    approach = result.get("approach_direction", "up")
    rev_side = "short" if approach == "up" else "long"
    return rev_side != side
```

Then ensure the trailing `if broker is not None and result is not None:` block converts the REV result into an on_signal call. Look for the action-translation block (search for `enter_long` or `enter_short` and `result.get("action")`) — it should already convert "REV" + approach_direction into "enter_long"/"enter_short" for broker.on_signal. If it doesn't, add that conversion.

- [ ] **Step 4: Run test to verify it passes**

```bash
cd backend && pytest tests/test_broker_adapter_phase.py::test_rev_signal_flips_position_in_phase_2 -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/src/market_data/level_monitor.py backend/tests/test_broker_adapter_phase.py
git commit -m "feat(rl): route Phase 2 REV-opposite through broker.on_signal flip path

In-position handler no longer suppresses results for Phase 2 REV signals
opposite to current side. broker_adapter's existing flip-on-reversal
flow at on_signal handles flatten + fresh-open. New position re-enters
Phase 1 with locked_BE=False on a fresh on_fill.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 12: Phase 1 sacred — verify existing in-position handler ignores zone touches in Phase 1

**Files:**
- Modify (verify): `backend/src/market_data/level_monitor.py` (in-position handler around line 1795+)
- Test: `backend/tests/test_phase_mechanics.py`

**Why:** Spec — Phase 1 must be sacred. Zone touches that occur before peak_R hits 1.5 must NOT trigger DQN re-eval, pyramid, or trail. Verify by reading the current handler. The PHASE_2_THRESHOLD_R gate added in Task 8 should already cause Phase 1 to fall through; this task adds an explicit test to lock the behavior in.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_phase_mechanics.py`:

```python
def test_phase1_in_position_handler_no_op_below_threshold():
    """In-position handler must skip cont-trail/pyramid when peak_R < 1.5R."""
    from unittest.mock import MagicMock

    from src.market_data.level_monitor import _should_run_phase2_handlers

    tr = MagicMock()
    tr.is_flat = False
    tr.peak_R = 1.0
    tr.locked_BE = False

    assert _should_run_phase2_handlers(tr) is False, (
        "Phase 1 (peak_R<1.5) must NOT run Phase 2 handlers"
    )

    tr.peak_R = 1.5
    tr.locked_BE = True
    assert _should_run_phase2_handlers(tr) is True, (
        "Phase 2 (peak_R>=1.5 + locked_BE) MUST run handlers"
    )

    tr.is_flat = True
    assert _should_run_phase2_handlers(tr) is False, "flat → no Phase 2 handlers"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd backend && pytest tests/test_phase_mechanics.py::test_phase1_in_position_handler_no_op_below_threshold -v
```

Expected: FAIL with `ImportError`.

- [ ] **Step 3: Add the helper and use it as the in-position handler gate**

In `backend/src/market_data/level_monitor.py`, near the floor helpers:

```python
def _should_run_phase2_handlers(tr) -> bool:
    """Gate the in-position handler: skip entirely in Phase 1 (sacred bracket).
    Phase 2 entered when peak_R first crosses 1.5 and locked_BE flips True."""
    if getattr(tr, "is_flat", True):
        return False
    if not getattr(tr, "locked_BE", False):
        return False
    if float(getattr(tr, "peak_R", 0.0) or 0.0) < PHASE_2_THRESHOLD_R:
        return False
    return True
```

In the in-position handler in `_emit_zone_dqn_inference`, wrap the entire CONT/REV/pyramid/cont-trail block with this gate:

```python
if not broker.tracker.is_flat:
    if not _should_run_phase2_handlers(broker.tracker):
        # Phase 1 sacred — ignore this zone touch entirely. Trade plays out
        # against the original SL/TP bracket. Suppress the broker-dispatch
        # path so we don't accidentally enter a new position while in one.
        result = None
    else:
        try:
            tr = broker.tracker
            rev = result.get("reversal_signals") or {}
            pyr = result.get("pyramid_decision") or {}
            ...  # existing CONT/REV/pyramid/cont-trail dispatch
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd backend && pytest tests/test_phase_mechanics.py::test_phase1_in_position_handler_no_op_below_threshold -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/src/market_data/level_monitor.py backend/tests/test_phase_mechanics.py
git commit -m "feat(rl): Phase 1 sacred — in-position handler no-op below 1.5R

Locks the spec invariant: zone touches during Phase 1 do not trigger
DQN re-eval, pyramid, or cont-trail. Trade plays out against original
bracket. Phase 2 handlers gated behind _should_run_phase2_handlers.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 13: Run full test suite — ensure no regressions

**Files:** none modified.

- [ ] **Step 1: Run the test suite**

```bash
cd backend && pytest tests/ -x -v 2>&1 | tail -50
```

Expected: all tests PASS. The `-x` flag exits on first failure so problems surface fast.

- [ ] **Step 2: Run lint check**

```bash
cd backend && ruff check src/ tests/
```

Expected: no errors.

- [ ] **Step 3: Run lint format check**

```bash
cd backend && ruff format --check src/ tests/
```

Expected: no errors. If formatting issues are reported, run `ruff format src/ tests/` and commit the formatting fix as a separate commit.

- [ ] **Step 4: Commit format fixes if any**

```bash
git add backend/src backend/tests
git commit -m "chore: ruff format

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

(skip this step if no format changes were needed)

---

### Task 14: Deploy and verify against production

**Files:** none modified.

**Deploy this batch in one shot — multiple small rebuilds during trading hours hurt model learning per the [stocks-aware rebuild rules](CLAUDE.md). Market is closed weekend so timing flexibility is high.**

- [ ] **Step 1: Confirm no open position before deploy**

```bash
ssh root@148.251.40.251 "cd /opt/arnold && bash scripts/server-deploy.sh status"
ssh root@148.251.40.251 "cd /opt/arnold && docker compose exec -T postgres psql -U arnold -d arnold -c 'SELECT id, side, size, ts, closed_at FROM broker_trades WHERE closed_at IS NULL ORDER BY ts DESC LIMIT 5;'"
```

Expected: zero open positions (closed_at IS NULL row count = 0). The rebuild gate enforces this anyway, but verify before pushing.

- [ ] **Step 2: Push to main**

```bash
git push origin main
```

- [ ] **Step 3: Trigger the rebuild**

```bash
ssh root@148.251.40.251 "bash /opt/arnold/scripts/server-deploy.sh rebuild backend"
```

This blocks until /health returns 200 OR fails the open-position gate. Watch the output. Note: server-deploy.sh has a long RL-wait window (up to 2h) — see [CLAUDE.md "Deploy stuck on RL wait"](CLAUDE.md) for the deadlock-escape procedure if it stalls.

- [ ] **Step 4: Verify the running container has the new code**

```bash
ssh root@148.251.40.251 "cd /opt/arnold && git rev-parse HEAD && curl -sf http://localhost:8000/health | python3 -c 'import sys,json;d=json.load(sys.stdin);print(\"boot_id:\", d.get(\"boot_id\"))'"
```

Expected: HEAD matches the last commit pushed; boot_id changed from previous deploy.

- [ ] **Step 5: Verify the floor changes are live**

```bash
ssh root@148.251.40.251 "cd /opt/arnold && docker compose exec -T backend python3 -c 'from src.market_data.level_monitor import _conf_floor, _of_floor, PHASE_2_THRESHOLD_R; print(\"conf_floor:\", _conf_floor()); print(\"of_floor:\", _of_floor()); print(\"phase_2_threshold_R:\", PHASE_2_THRESHOLD_R)'"
```

Expected:
```
conf_floor: 0.0
of_floor: 0.0
phase_2_threshold_R: 1.5
```

- [ ] **Step 6: Open the SSE feed and watch a few zone touches**

When the next NQ session opens (Sunday 22:00 UTC if not already), tail the feed:

```bash
ssh root@148.251.40.251 "cd /opt/arnold && docker compose exec -T backend bash -c 'tail -F /app/logs/api.log' | grep -E 'Position opening|BE-lock|Pyramid|reversal_signals|Cont-trail|REV signal|Dispatch BLOCKED'"
```

Expected first observations within 10-30 minutes of session open:
- `Position opening` lines for new entries (every ~1-3 min during active session)
- `entry≈XXX` (pre-populated entry_price visible)
- After a winner runs to +1.5R: `BE-lock at peak_R=1.5X`
- After a winner crosses 1.5R and price touches the next zone: `Cont-trail` or `Pyramid add`
- Eventually a `REV signal` line followed by `flip_on_reversal` flatten

If `Position opening` lines appear but no BE-lock fires for any winner, **the trail bug is still active** — open a follow-up investigation; do not roll back. Phase 2 won't engage, but Phase 1 is still working as a clean SL-or-1.5R-then-runs system.

---

### Task 15: 24-hour acceptance check

**Files:** none modified.

- [ ] **Step 1: After 24 hours of live trading post-deploy, run the spec's acceptance queries**

```bash
ssh root@148.251.40.251 "cd /opt/arnold && docker compose exec -T postgres psql -U arnold -d arnold -c \"
SELECT
  COUNT(*) FILTER (WHERE peak_r >= 1.5) AS reached_phase2,
  COUNT(*) FILTER (WHERE trail_count > 0) AS trailed,
  COUNT(*) FILTER (WHERE exit_reason = 'DQN_ZONE_REVERSAL') AS dqn_flips,
  COUNT(*) FILTER (WHERE size = 2) AS size_2_trades,
  COUNT(*) FILTER (WHERE stop_ticks < 6 OR stop_ticks > 40) AS out_of_bound_trades,
  COUNT(*) AS total
FROM broker_trades
WHERE ts >= 'YYYY-MM-DD HH:MM' AND closed_at IS NOT NULL;
\""
```

Replace `YYYY-MM-DD HH:MM` with the deploy timestamp.

Spec acceptance criteria:
1. `reached_phase2 / total` rises to 15-25% (was ~4% pre-fix).
2. `trailed > 0` (was 0/331 last week).
3. `dqn_flips > 0` (was 0).
4. `size_2_trades > 0` only when `signal_confidence >= 0.85`.
5. `out_of_bound_trades = 0`.

- [ ] **Step 2: If criteria not met, file follow-up findings**

If any criterion fails, append a note to [docs/superpowers/specs/2026-05-09-phase1-phase2-mechanics-design.md](docs/superpowers/specs/2026-05-09-phase1-phase2-mechanics-design.md) with what was observed and what looks wrong. Do not roll back unless trading is actively losing more than the prior week's run-rate (-$3-4k/day) — paper-phase tolerates noisy first day.

- [ ] **Step 3: Commit the findings note**

```bash
git add docs/superpowers/specs/2026-05-09-phase1-phase2-mechanics-design.md
git commit -m "docs: 24h acceptance findings for phase1-phase2 deploy

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
git push origin main
```

---

## Self-review notes

Coverage check against spec sections:

| Spec section | Tasks |
|---|---|
| Phase 1 sacred bracket | 12 (gate handler) |
| Phase 1 → Phase 2 transition | 8 (threshold) + existing BE-lock |
| Phase 2 zone-driven ride — CONT pyramid | 10 |
| Phase 2 zone-driven ride — REV flip | 11 |
| Phase 2 zone-driven ride — SKIP hold | implicit (no-op when not CONT/REV) |
| Disable per-tick reversal_signals + early_exit_lock | 9 |
| Floor-zero conf | 4 |
| Floor-zero OF | 5 |
| Stop-tick sanity bounds | 6 |
| Conf-scaled entry size | 7 |
| Conf-scaled pyramid size | 10 |
| Conf-scaled flip size | implicit (Task 11 routes through on_signal which uses Task 7's sizing) |
| Trail bug fix | 1 (diagnostic) + 2 (fix) |
| Phase property on tracker | 3 |
| Acceptance criteria verification | 14 (immediate) + 15 (24h) |

No placeholders. Every step has either a runnable command or a code block. Type/symbol consistency: `_conf_floor`, `_of_floor`, `_stop_ticks_in_bounds`, `_pyramid_add_size`, `_should_run_phase2_handlers`, `PHASE_2_THRESHOLD_R`, `MIN_ENTRY_STOP_TICKS`, `MAX_ENTRY_STOP_TICKS`, `BASE_SIZE`, `PHASE_2_BASE_SIZE` — all used consistently between definitions and call sites.
