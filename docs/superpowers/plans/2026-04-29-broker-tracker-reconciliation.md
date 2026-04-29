# Broker Tracker Reconciliation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Eliminate the "arrived while flat" data loss + lost-position-tracking bug after backend container restarts.

**Architecture:** Three-layer defense — primary recovery from TopstepX REST, secondary fallback from disk snapshot, continuous periodic size-mismatch detection.

**Tech Stack:** Python 3.10 + asyncio + httpx (TopstepX REST) + pytest + SQLAlchemy.

**Spec:** [docs/superpowers/specs/2026-04-29-broker-tracker-reconciliation.md](../specs/2026-04-29-broker-tracker-reconciliation.md)

---

## File Structure

| File | Role | New / Modified |
|---|---|---|
| `backend/src/stocks/tracker_reconciler.py` | The `reconcile_tracker_from_broker` helper, single responsibility | **Create** |
| `backend/src/stocks/broker_adapter.py` | Augment `_save_pending_trade_to_disk` / `_load_pending_trade_from_disk` to round-trip the new `tracker_snapshot` field | Modify |
| `backend/src/broker/position_tracker.py` | Add a `to_snapshot()` / `restore_from_snapshot(snap)` pair so callers don't poke private fields directly | Modify |
| `backend/src/stocks/server_bootstrap.py` | Wire reconciliation between adapter creation and SignalR stream start; add the periodic size-reconcile task | Modify |
| `backend/tests/test_tracker_reconciler.py` | Unit tests for the 5 spec cases | **Create** |
| `backend/tests/test_position_tracker_snapshot.py` | Round-trip tests for snapshot/restore | **Create** |

---

## Task 1: Snapshot/restore on PositionTracker

**Files:**
- Modify: `backend/src/broker/position_tracker.py`
- Test: `backend/tests/test_position_tracker_snapshot.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_position_tracker_snapshot.py
"""Snapshot/restore round-trip on PositionTracker."""
from src.broker.position_tracker import PositionTracker


def test_flat_snapshot_round_trip():
    tr = PositionTracker()
    snap = tr.to_snapshot()
    assert snap["side"] is None
    assert snap["size"] == 0
    assert snap["entry_price"] == 0.0
    assert snap["peak_R"] == 0.0
    assert snap["locked_BE"] is False

    fresh = PositionTracker()
    fresh.restore_from_snapshot(snap)
    assert fresh.is_flat


def test_open_position_snapshot_round_trip():
    tr = PositionTracker()
    tr.on_fill("long", price=27226.0, size=1, stop_price=27217.75)
    tr.update_mark(27250.0)  # peak_R should now be ~2.9
    tr.locked_BE = True

    snap = tr.to_snapshot()
    fresh = PositionTracker()
    fresh.restore_from_snapshot(snap)

    assert fresh.side == "long"
    assert fresh.entry_price == 27226.0
    assert fresh.stop_price == 27217.75
    assert fresh.size == 1
    assert abs(fresh.peak_R - tr.peak_R) < 1e-6
    assert fresh.locked_BE is True
    assert not fresh.is_flat


def test_restore_overwrites_existing_state():
    tr = PositionTracker()
    tr.on_fill("short", price=27300.0, size=2, stop_price=27308.0)

    fresh = PositionTracker()
    fresh.on_fill("long", price=27226.0, size=1, stop_price=27217.75)
    fresh.restore_from_snapshot(tr.to_snapshot())

    assert fresh.side == "short"
    assert fresh.size == 2
    assert fresh.entry_price == 27300.0
```

- [ ] **Step 2: Run test, verify failure**

Run: `cd backend && python -m pytest tests/test_position_tracker_snapshot.py -v`
Expected: FAIL — `AttributeError: 'PositionTracker' object has no attribute 'to_snapshot'`

- [ ] **Step 3: Add the methods to PositionTracker**

Add to `backend/src/broker/position_tracker.py` inside the `PositionTracker` class (alongside `update_mark`):

```python
    def to_snapshot(self) -> dict:
        """Serialize tracker state for disk persistence."""
        return {
            "side": self.side,
            "entry_price": self.entry_price,
            "stop_price": self.stop_price,
            "size": self.size,
            "entry_order_id": self.entry_order_id,
            "stop_order_id": self.stop_order_id,
            "peak_R": self.peak_R,
            "locked_half_R": self.locked_half_R,
            "locked_BE": self.locked_BE,
        }

    def restore_from_snapshot(self, snap: dict) -> None:
        """Restore tracker state from a to_snapshot() dict.

        Used by the bootstrap reconciliation path. Does NOT call on_fill
        (which would log a fresh "Position opened" line) — silently restores
        prior state without re-emitting events.
        """
        self.side = snap.get("side")
        self.entry_price = float(snap.get("entry_price") or 0.0)
        self.stop_price = float(snap.get("stop_price") or 0.0)
        self.size = int(snap.get("size") or 0)
        self.entry_order_id = snap.get("entry_order_id")
        self.stop_order_id = snap.get("stop_order_id")
        self.peak_R = float(snap.get("peak_R") or 0.0)
        self.locked_half_R = bool(snap.get("locked_half_R", False))
        self.locked_BE = bool(snap.get("locked_BE", False))
```

- [ ] **Step 4: Run tests, verify pass**

Run: `cd backend && python -m pytest tests/test_position_tracker_snapshot.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/src/broker/position_tracker.py backend/tests/test_position_tracker_snapshot.py
git commit -m "feat(tracker): add to_snapshot/restore_from_snapshot for disk persistence"
```

---

## Task 2: Reconciler module + tests

**Files:**
- Create: `backend/src/stocks/tracker_reconciler.py`
- Test: `backend/tests/test_tracker_reconciler.py`

- [ ] **Step 1: Discover the TopstepX position-search endpoint**

Read `backend/src/stocks/topstepx_client.py` to find the existing method for fetching open positions. If it exists (e.g., `client.search_open_positions()`), use it. If it doesn't, add it as part of this task.

Required shape (per CLAUDE.md memory `project_topstepx_trade_endpoints`): `POST /api/Position/searchOpen` with `{accountId}` body returns `{success, positions: [{contractId, type, size, averagePrice}]}` where `type` is `1=long, 2=short`. Verify this against the actual client.

- [ ] **Step 2: Write the failing test**

```python
# backend/tests/test_tracker_reconciler.py
"""Tests for reconcile_tracker_from_broker."""
from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.broker.position_tracker import PositionTracker
from src.stocks.tracker_reconciler import (
    ReconcileResult,
    reconcile_tracker_from_broker,
)


def _make_adapter(tracker: PositionTracker | None = None, pending: dict | None = None):
    adapter = MagicMock()
    adapter.tracker = tracker or PositionTracker()
    adapter._pending_trade = pending
    adapter._set_pending_trade = MagicMock()
    return adapter


@pytest.mark.asyncio
async def test_broker_only_populates_tracker():
    """Broker has open position; nothing on disk → tracker populated from broker."""
    adapter = _make_adapter()
    client = MagicMock()
    client.search_open_positions = AsyncMock(return_value=[
        {"contractId": "CON.F.US.ENQ.M26", "type": 1, "size": 1, "averagePrice": 27226.0}
    ])
    client.search_open_orders = AsyncMock(return_value=[
        {"orderId": 12345, "type": 4, "side": 1, "stopPrice": 27217.75}
    ])

    result = await reconcile_tracker_from_broker(adapter, client, "CON.F.US.ENQ.M26")

    assert adapter.tracker.side == "long"
    assert adapter.tracker.entry_price == 27226.0
    assert adapter.tracker.stop_price == 27217.75
    assert adapter.tracker.size == 1
    assert adapter.tracker.stop_order_id == 12345
    assert result.matched is True
    assert result.broker_only is True
    assert result.disk_only is False


@pytest.mark.asyncio
async def test_broker_and_disk_match():
    """Both sources agree → tracker populated, no warning."""
    adapter = _make_adapter(pending={"side": "long", "entry_price": 27226.0, "size": 1})
    client = MagicMock()
    client.search_open_positions = AsyncMock(return_value=[
        {"contractId": "CON.F.US.ENQ.M26", "type": 1, "size": 1, "averagePrice": 27226.0}
    ])
    client.search_open_orders = AsyncMock(return_value=[
        {"orderId": 12345, "type": 4, "side": 1, "stopPrice": 27217.75}
    ])

    result = await reconcile_tracker_from_broker(adapter, client, "CON.F.US.ENQ.M26")

    assert adapter.tracker.size == 1
    assert result.matched is True
    assert result.divergence_logged is False


@pytest.mark.asyncio
async def test_broker_and_disk_diverge_broker_wins():
    """Disk says size=2; broker says size=1 → broker wins, divergence logged."""
    adapter = _make_adapter(pending={"side": "long", "entry_price": 27200.0, "size": 2})
    client = MagicMock()
    client.search_open_positions = AsyncMock(return_value=[
        {"contractId": "CON.F.US.ENQ.M26", "type": 1, "size": 1, "averagePrice": 27226.0}
    ])
    client.search_open_orders = AsyncMock(return_value=[])

    result = await reconcile_tracker_from_broker(adapter, client, "CON.F.US.ENQ.M26")

    assert adapter.tracker.size == 1
    assert adapter.tracker.entry_price == 27226.0
    assert result.divergence_logged is True


@pytest.mark.asyncio
async def test_disk_only_means_position_closed_during_downtime():
    """No broker position; disk has stale data → clear disk, tracker stays flat."""
    adapter = _make_adapter(pending={"side": "long", "entry_price": 27226.0, "size": 1})
    client = MagicMock()
    client.search_open_positions = AsyncMock(return_value=[])
    client.search_open_orders = AsyncMock(return_value=[])

    result = await reconcile_tracker_from_broker(adapter, client, "CON.F.US.ENQ.M26")

    assert adapter.tracker.is_flat
    assert result.disk_only is True
    adapter._set_pending_trade.assert_called_once_with(None)


@pytest.mark.asyncio
async def test_broker_rest_failure_returns_degraded():
    """REST timeout → returns degraded result; caller falls back to Layer 2."""
    adapter = _make_adapter()
    client = MagicMock()
    client.search_open_positions = AsyncMock(side_effect=TimeoutError("REST timeout"))

    result = await reconcile_tracker_from_broker(adapter, client, "CON.F.US.ENQ.M26")

    assert result.degraded is True
    assert adapter.tracker.is_flat  # untouched
```

- [ ] **Step 3: Run test, verify failure**

Run: `cd backend && python -m pytest tests/test_tracker_reconciler.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.stocks.tracker_reconciler'`

- [ ] **Step 4: Implement the reconciler**

Create `backend/src/stocks/tracker_reconciler.py`:

```python
"""Reconcile broker_adapter.tracker state from TopstepX REST on bootstrap."""

from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class ReconcileResult:
    matched: bool = False
    broker_only: bool = False
    disk_only: bool = False
    divergence_logged: bool = False
    degraded: bool = False


async def reconcile_tracker_from_broker(
    adapter,
    client,
    contract_id: str,
) -> ReconcileResult:
    """Populate adapter.tracker from TopstepX REST. Return what happened.

    Order of operations:
      1. Query open positions on the contract.
      2. Look up matching stop order (if any) for stop_price + stop_order_id.
      3. If broker has position: tracker.on_fill(...), reconcile against disk.
      4. If broker has no position but disk does: clear disk.
      5. On REST failure: return degraded; caller may fall back to Layer 2.
    """
    result = ReconcileResult()

    try:
        positions = await client.search_open_positions()
    except Exception as e:
        logger.warning("reconcile: REST query failed (%s); returning degraded", e)
        result.degraded = True
        return result

    matching = [p for p in positions if p.get("contractId") == contract_id]

    pending = adapter._pending_trade

    if not matching:
        if pending:
            logger.info("reconcile: no broker position; clearing stale _pending_trade")
            adapter._set_pending_trade(None)
            result.disk_only = True
        return result

    # Broker has an open position
    pos = matching[0]
    pos_type = pos.get("type")
    side = "long" if pos_type == 1 else "short" if pos_type == 2 else None
    if side is None:
        logger.warning("reconcile: unknown position type=%s; skipping", pos_type)
        result.degraded = True
        return result

    avg_price = float(pos.get("averagePrice") or 0.0)
    size = int(pos.get("size") or 0)

    # Find the matching stop order
    stop_price = 0.0
    stop_order_id = None
    try:
        orders = await client.search_open_orders()
        # type=4 is stop order; side opposite to position
        opposite_side = 1 if side == "short" else 2
        for o in orders:
            if o.get("type") == 4 and o.get("side") == opposite_side:
                stop_price = float(o.get("stopPrice") or 0.0)
                stop_order_id = o.get("orderId")
                break
    except Exception as e:
        logger.warning("reconcile: stop-order lookup failed (%s); leaving stop=0", e)

    # Apply to tracker
    adapter.tracker.on_fill(side, avg_price, size, stop_price)
    if stop_order_id is not None:
        adapter.tracker.stop_order_id = stop_order_id

    # Reconcile against disk
    if pending:
        disk_size = int(pending.get("size") or 0)
        disk_entry = float(pending.get("entry_price") or 0.0)
        disk_side = pending.get("side")
        if (disk_size != size) or (disk_side != side) or abs(disk_entry - avg_price) > 0.5:
            logger.warning(
                "reconcile: broker/disk divergence — broker=(side=%s, size=%d, avg=%.2f) disk=(side=%s, size=%d, avg=%.2f); broker wins",
                side, size, avg_price, disk_side, disk_size, disk_entry,
            )
            result.divergence_logged = True
        result.matched = True
    else:
        result.broker_only = True

    logger.info(
        "reconcile: tracker populated from broker — side=%s entry=%.2f size=%d stop=%.2f stop_order_id=%s",
        side, avg_price, size, stop_price, stop_order_id,
    )
    return result
```

- [ ] **Step 5: Add `_set_pending_trade` helper to broker_adapter**

In `backend/src/stocks/broker_adapter.py`, find `_save_pending_trade_to_disk` and add a thin wrapper that updates the in-memory dict + persists in one call:

```python
    def _set_pending_trade(self, value: dict | None) -> None:
        """Single-step in-memory + disk update so callers don't drift."""
        self._pending_trade = value
        _save_pending_trade_to_disk(value)
```

(Add this as a method on `TopstepXBrokerAdapter` near `_save_pending_trade_to_disk`'s usage.)

- [ ] **Step 6: Run tests, verify pass**

Run: `cd backend && python -m pytest tests/test_tracker_reconciler.py -v`
Expected: 5 passed.

- [ ] **Step 7: Commit**

```bash
git add backend/src/stocks/tracker_reconciler.py backend/src/stocks/broker_adapter.py backend/tests/test_tracker_reconciler.py
git commit -m "feat(tracker): reconcile_tracker_from_broker — Layer 1 of restart fix"
```

---

## Task 3: Wire reconciliation into bootstrap

**Files:**
- Modify: `backend/src/stocks/server_bootstrap.py`

- [ ] **Step 1: Insert reconciliation call**

In `bootstrap_stocks_on_server` ([server_bootstrap.py:484-642](../../../backend/src/stocks/server_bootstrap.py#L484)), find the line `adapter = TopstepXBrokerAdapter(client, config)` (currently line 529). Add immediately after:

```python
    from .tracker_reconciler import reconcile_tracker_from_broker

    reconcile_result = await reconcile_tracker_from_broker(adapter, client, config.contract_id)
    if reconcile_result.degraded and adapter._pending_trade:
        # Layer 2 fallback: restore from disk snapshot if REST failed.
        snap = adapter._pending_trade.get("tracker_snapshot")
        if snap:
            log.warning("reconcile: REST failed, falling back to disk snapshot")
            adapter.tracker.restore_from_snapshot(snap)
        else:
            log.error("reconcile: REST failed AND no disk snapshot — broker_adapter is in unknown state; halting trading")
            adapter._halt("reconcile_failed")
```

- [ ] **Step 2: Verify no regression in import order**

Add a smoke test that imports the bootstrap module without running it:
```bash
cd backend && python -c "from src.stocks.server_bootstrap import bootstrap_stocks_on_server; print('import OK')"
```

- [ ] **Step 3: Commit**

```bash
git add backend/src/stocks/server_bootstrap.py
git commit -m "feat(bootstrap): wire tracker reconciliation before SignalR stream starts"
```

---

## Task 4: Persist tracker_snapshot alongside _pending_trade

**Files:**
- Modify: `backend/src/stocks/broker_adapter.py`

- [ ] **Step 1: Find every site that calls `_save_pending_trade_to_disk`**

Search:
```bash
grep -n "_save_pending_trade_to_disk\|_set_pending_trade" backend/src/stocks/broker_adapter.py
```

There are several call sites — each one has access to `self.tracker`.

- [ ] **Step 2: Augment `_set_pending_trade` to also store the snapshot**

Update the helper added in Task 2 Step 5:

```python
    def _set_pending_trade(self, value: dict | None) -> None:
        """In-memory + disk update with tracker snapshot for restart recovery."""
        if value is not None:
            value = dict(value)  # don't mutate caller's dict
            value["tracker_snapshot"] = self.tracker.to_snapshot()
        self._pending_trade = value
        _save_pending_trade_to_disk(value)
```

- [ ] **Step 3: Replace direct calls to `_save_pending_trade_to_disk(self._pending_trade)` with `self._set_pending_trade(self._pending_trade)`**

In each existing call site (likely `_execute_entry`, `on_stream_fill`, `flatten`, `modify_stop`), replace:
```python
_save_pending_trade_to_disk(self._pending_trade)
```
with:
```python
self._set_pending_trade(self._pending_trade)
```

This ensures the snapshot is refreshed on every state change, so the disk image stays in sync.

- [ ] **Step 4: Add a test that exercises the round trip**

In `backend/tests/test_broker_adapter_recovery.py` (new):

```python
import os
import tempfile
from unittest.mock import MagicMock

import pytest

from src.broker.position_tracker import PositionTracker


def test_pending_trade_carries_tracker_snapshot(monkeypatch):
    """On _set_pending_trade, the disk image includes a tracker_snapshot field."""
    from src.stocks import broker_adapter

    written = {}
    def fake_save(value):
        written["value"] = value
    monkeypatch.setattr(broker_adapter, "_save_pending_trade_to_disk", fake_save)

    # Build a minimal adapter shell
    adapter = MagicMock()
    adapter.tracker = PositionTracker()
    adapter.tracker.on_fill("long", 27226.0, 1, 27217.75)
    adapter._pending_trade = None

    # Bind the real method
    adapter._set_pending_trade = broker_adapter.TopstepXBrokerAdapter._set_pending_trade.__get__(adapter)

    pending = {"side": "long", "entry_price": 27226.0}
    adapter._set_pending_trade(pending)

    assert "tracker_snapshot" in written["value"]
    assert written["value"]["tracker_snapshot"]["side"] == "long"
    assert written["value"]["tracker_snapshot"]["entry_price"] == 27226.0
```

- [ ] **Step 5: Run test, then commit**

Run: `cd backend && python -m pytest tests/test_broker_adapter_recovery.py -v`
Expected: 1 passed.

```bash
git add backend/src/stocks/broker_adapter.py backend/tests/test_broker_adapter_recovery.py
git commit -m "feat(tracker): persist tracker_snapshot alongside _pending_trade — Layer 2"
```

---

## Task 5: Periodic position-size reconciliation (Layer 3)

**Files:**
- Modify: `backend/src/stocks/server_bootstrap.py`

- [ ] **Step 1: Add the reconcile loop function**

In `server_bootstrap.py`, before `bootstrap_stocks_on_server`:

```python
async def _reconcile_position_loop(adapter, client, contract_id: str) -> None:
    """Periodically (60s) verify tracker.size matches TopstepX position size.
    On mismatch, halt + flatten — better to take a wash trade than to
    operate with diverged state.
    """
    while True:
        try:
            await asyncio.sleep(60)
            if adapter.tracker.is_flat:
                continue
            try:
                positions = await client.search_open_positions()
            except Exception:
                logger.warning("reconcile loop: REST query failed; skipping cycle", exc_info=True)
                continue
            matching = [p for p in positions if p.get("contractId") == contract_id]
            broker_size = sum(int(p.get("size") or 0) for p in matching)
            local_size = int(adapter.tracker.size or 0)
            if broker_size != local_size:
                logger.error(
                    "reconcile loop: SIZE MISMATCH — broker=%d local=%d; halting + flattening",
                    broker_size, local_size,
                )
                adapter._halt("size_mismatch")
                try:
                    await adapter.flatten("size_mismatch_recovery")
                except Exception:
                    logger.exception("reconcile loop: flatten after mismatch failed")
        except asyncio.CancelledError:
            return
        except Exception:
            logger.exception("reconcile loop: unexpected error; continuing")
```

- [ ] **Step 2: Start the loop after stream.start()**

In `bootstrap_stocks_on_server`, after `await stream.start()` (currently line 626), add:

```python
    _reconcile_task = asyncio.create_task(
        _reconcile_position_loop(adapter, client, config.contract_id),
        name="server-position-reconciler",
    )
```

And include it in the runtime's task list:
```python
    runtime = ServerStocksRuntime(
        client=client,
        adapter=adapter,
        stream=stream,
        flatten_scheduler=flatten_scheduler,
        tasks={"zone_seed": _seed_task, "reconcile": _reconcile_task},
    )
```

- [ ] **Step 3: Smoke-test the import**

```bash
cd backend && python -c "from src.stocks.server_bootstrap import _reconcile_position_loop; print('import OK')"
```

- [ ] **Step 4: Commit**

```bash
git add backend/src/stocks/server_bootstrap.py
git commit -m "feat(bootstrap): periodic position-size reconciliation — Layer 3"
```

---

## Task 6: End-to-end verification + deploy

- [ ] **Step 1: Run all new tests**

```bash
cd backend && python -m pytest \
  tests/test_position_tracker_snapshot.py \
  tests/test_tracker_reconciler.py \
  tests/test_broker_adapter_recovery.py -v
```

Expected: 9 passed (3 + 5 + 1).

- [ ] **Step 2: Run the broader stocks test suite**

```bash
cd backend && python -m pytest tests/test_broker_adapter.py tests/services/ -v
```

Expected: no regression in pre-existing tests.

- [ ] **Step 3: Push to a feature branch + open PR**

(Don't push to main — this needs review.)

```bash
git push -u origin feat/tracker-reconciliation
gh pr create --base main --title "feat(stocks): broker tracker reconciliation on restart (3-layer fix)" --body-file docs/superpowers/specs/2026-04-29-broker-tracker-reconciliation.md
```

- [ ] **Step 4: After PR approved, deploy carefully**

- Confirm stocks position is flat: `curl /api/stocks/runtime-status`.
- Merge PR.
- Deploy: `ssh root@148.251.40.251 "bash /opt/arnold/scripts/server-deploy.sh rebuild backend"`.
- Watch logs for the `reconcile:` log lines on bootstrap.

- [ ] **Step 5: Manual chaos test in production**

After deploy:
1. Open a small NQ size=1 position via a test signal.
2. `ssh root@148.251.40.251 "docker compose restart backend"` (just restart, no rebuild — faster).
3. Watch logs: `reconcile: tracker populated from broker — side=long entry=X size=1` should appear within ~35s of restart (after the 30s startup grace).
4. `runtime-status` should show non-zero `peak_R` updating.
5. Move price (or wait for market move). Verify BE-lock fires at +2R.

- [ ] **Step 6: Layer 3 smoke test**

1. Open a small position.
2. Manually flatten it via TopstepX dashboard.
3. Within 60s, the `reconcile loop: SIZE MISMATCH` log should fire and the local adapter should halt.
4. Resume via `POST /api/stocks/resume`.

---

## Self-review notes

- Spec coverage: A→Task 2, B→Tasks 1+4, C→Task 5, D→Task 3, E→Tasks 1+2+4 unit tests + Task 6 integration. All covered.
- Type consistency: `reconcile_tracker_from_broker(adapter, client, contract_id)` signature is the same in Tasks 2, 3, 5. `to_snapshot()` / `restore_from_snapshot(snap)` names consistent.
- No placeholder steps. Each step has actual code.
- Out-of-scope items (per spec) explicitly NOT in any task: historical peak_R recovery, multi-symbol, GatewayUserAccount reconciliation, zone-aware trail-on-cont (separate plan).
- Known fragility: TopstepX REST endpoint paths assumed per memory. Task 2 Step 1 verifies against actual `topstepx_client.py` — if names differ, fix before continuing.
