# Broker Tracker Reconciliation on Restart — Design

**Date:** 2026-04-29
**Status:** Approved (writing plan next)
**Severity:** Safety — must be fixed before live trading

## Problem

When the backend container is recreated (deploy, OOM kill, watchdog restart) while a TopstepX position is open, the autonomous broker_adapter loses track of the position:

1. `_pending_trade` is persisted to disk and recovered on startup ([broker_adapter.py:188](../../../backend/src/stocks/broker_adapter.py#L188)) — carries side, entry, stop, signal_trigger, etc.
2. `tracker` (`PositionTracker`) is reset fresh on import ([position_tracker.py:31-57](../../../backend/src/broker/position_tracker.py#L31)) — `side=None`, `entry_price=0`, `size=0`, `stop_price=0`, `peak_R=0`, `locked_BE=False`.
3. SignalR reconnects to TopstepX, replays buffered fill events.
4. `on_stream_fill` checks `if tracker.is_flat: drop` ([broker_adapter.py:550](../../../backend/src/stocks/broker_adapter.py#L550)). Since the freshly-reset tracker has `side=None`, every replay fill is rejected with `arrived while flat — dropping`.
5. `level_monitor._check_positions` ([level_monitor.py:927-935](../../../backend/src/market_data/level_monitor.py#L927)) calls `update_mark_and_check_be_lock(price)` on every tick — but the broker check at line 934 short-circuits when `tracker.is_flat == True`. So `peak_R` never updates.
6. Eventually a NEW signal fires → `_execute_entry` → places a *second* TopstepX order. Now system thinks `size=1`; TopstepX may have `size=2`. PnL diverges.

**Observed live impact (2026-04-29 ~13:00 UTC):** an open NQ long at 27226 reached +3.0R (price hit 27250.75) but `peak_R` stayed at 0, BE-lock never fired, stop never moved. The system was completely silent on a winning trade. Required manual intervention to flatten and lock the profit.

The bug is triggered every time the container restarts while a position is open. It will recur on every deploy that touches `arnold/server.py`, `backend/src/stocks/*`, or anything else baked into the image. The deploy queue (currently in another agent's queue) doesn't help — even with stocks-flat gating before deploy, an entry can fire WHILE a deploy is mid-flight.

## Goal

After any container restart with an open TopstepX position, the broker_adapter's tracker state is fully reconciled before the SignalR stream begins replaying — so:

- Replayed entry fills are recognized as duplicates of an already-known entry and ignored gracefully (no data loss).
- New ticks correctly update `peak_R` and fire BE-lock at +2R.
- Subsequent zone-touch decisions (reversal exit, early exit, pyramid add) work normally.
- No risk of placing a duplicate entry order through `_execute_entry`.

## Non-goals

- Recovering `peak_R` historically (price history during the downtime window is not reconstructed). New ticks will rebuild peak_R from the current price forward — sufficient because BE-lock cares about *future* peaks ≥ 2.0, not past ones.
- Recovering signal_trigger / cont_p / rev_p of the original entry beyond what `_pending_trade` already persists.
- Defending against simultaneous double-entry from a race between bootstrap-time recovery and a same-second new signal (mitigated separately by the existing zone-cooldown).

## Architecture

Three layers of defense, in priority order:

### Layer 1 (primary) — Reconcile from TopstepX before SignalR starts

TopstepX's REST API is the source of truth for current positions. `bootstrap_stocks_on_server` already authenticates the client and creates the adapter ([server_bootstrap.py:521-529](../../../backend/src/stocks/server_bootstrap.py#L521)) before the stream starts at line 626. Insert a reconciliation step between adapter creation and stream start:

```
client = TopstepXClient(config)
await client.connect()
adapter = TopstepXBrokerAdapter(client, config)

# NEW: reconcile tracker from TopstepX REST before SignalR stream starts
await reconcile_tracker_from_broker(adapter, client, contract_id)

level_monitor.set_broker_adapter(adapter)
...
await stream.start()  # now replayed fills find tracker in correct state
```

`reconcile_tracker_from_broker` calls TopstepX's position endpoint (`/api/Position/searchOpen` or equivalent — implementation must verify the exact route in `topstepx_client.py`). For each open position matching `config.contract_id`:

- Read: `side` (from positionType), `size`, `averagePrice`, current `stopOrderId` if any.
- Lookup the stop order details via `/api/Order/search` to get `stopPrice`.
- Call `tracker.on_fill(side, average_price, size, stop_price, signal_price=0.0)` to populate the tracker.
- Set `tracker.entry_order_id` and `tracker.stop_order_id` so subsequent replay-fill matching works correctly.
- Cross-check against `_pending_trade` recovered from disk: if both exist and disagree, log a warning, prefer the broker view, mark `_pending_trade["recovered_from_broker"] = True`. If only `_pending_trade` exists but no open position at broker, it's stale — clear it via `_save_pending_trade_to_disk(None)`.

If TopstepX returns no open positions but `_pending_trade` exists from disk, the position was closed during the downtime — clear `_pending_trade` and proceed with a clean tracker.

### Layer 2 (defense-in-depth) — Persist tracker state alongside `_pending_trade`

Update `_save_pending_trade_to_disk` to also write a `tracker_snapshot` field:

```python
{
    "side": tracker.side,
    "entry_price": tracker.entry_price,
    "stop_price": tracker.stop_price,
    "size": tracker.size,
    "entry_order_id": tracker.entry_order_id,
    "stop_order_id": tracker.stop_order_id,
    "peak_R": tracker.peak_R,
    "locked_BE": tracker.locked_BE,
    "locked_half_R": tracker.locked_half_R,
}
```

On startup, if Layer 1 fails (TopstepX REST unreachable, auth in degraded mode, etc.), fall back to the disk snapshot. Apply via `tracker.on_fill(...)` plus direct field assignment for `peak_R` / `locked_BE` / `locked_half_R`.

Tradeoff: the disk snapshot can drift from the broker's actual state during the downtime window (e.g., if the position was stop-hit during the gap). Always log when falling back to Layer 2 so post-mortems are clear.

### Layer 3 (continuous safety) — Periodic position size reconciliation

Add a `_reconcile_position_loop` task started in `bootstrap_stocks_on_server` that runs every 60s:

1. Query TopstepX for the current open position size on `config.contract_id`.
2. Compare to `tracker.size`.
3. On mismatch:
   - Log loudly with both views.
   - Call `adapter._halt("size_mismatch")` to block new entries.
   - Call `adapter.flatten("size_mismatch_recovery")` to liquidate.
   - The next signal can re-enter from a known-clean state.

This catches the rare race where Layer 1 + Layer 2 both miss (e.g., a fill arrives during the bootstrap reconciliation window itself).

## Components

### A. `reconcile_tracker_from_broker` helper

**File:** `backend/src/stocks/tracker_reconciler.py` (new, single-purpose module)

Public surface:
```python
async def reconcile_tracker_from_broker(
    adapter: TopstepXBrokerAdapter,
    client: TopstepXClient,
    contract_id: str,
) -> ReconcileResult
```

Returns a small dataclass describing what happened: `{matched, broker_only, disk_only, divergence_logged}`. Caller logs the result for observability.

### B. Tracker snapshot persistence

**File:** `backend/src/stocks/broker_adapter.py`

Modify `_save_pending_trade_to_disk` and `_load_pending_trade_from_disk` to handle the augmented schema. Backward-compatibly: if loaded dict lacks `tracker_snapshot`, treat as legacy and skip Layer 2 fallback (Layer 1 + warning).

### C. Periodic reconciliation task

**File:** `backend/src/stocks/server_bootstrap.py`

New `_reconcile_position_loop(adapter, client, contract_id)` async task. Started after the SignalR stream is up. Runs `while True: await asyncio.sleep(60); _check_size()`.

### D. Bootstrap wiring

**File:** `backend/src/stocks/server_bootstrap.py`

Insert `await reconcile_tracker_from_broker(...)` between `adapter = TopstepXBrokerAdapter(...)` (line 529) and `level_monitor.set_broker_adapter(adapter)` (line 533).

### E. Tests

**File:** `backend/tests/test_tracker_reconciler.py` (new)

Cases:
1. **Broker has position, no disk state**: tracker populated from broker.
2. **Broker has position, disk state matches**: tracker populated, no warning.
3. **Broker has position, disk state diverges**: warning logged, broker wins.
4. **Broker has no position, disk state exists**: disk cleared, tracker stays flat.
5. **Broker REST timeout**: returns degraded result, caller can fall back to Layer 2.

Mock the TopstepX client. Use the existing `db_session` fixture pattern from `backend/tests/conftest.py`.

## Data flow

```
Container starts
  ↓
TopstepXClient.connect() (auth)
  ↓
TopstepXBrokerAdapter(client) created
  ├─ _pending_trade loaded from disk (existing)
  └─ tracker fresh (side=None)
  ↓
reconcile_tracker_from_broker(adapter, client, contract_id)
  ├─ GET /api/Position/searchOpen
  ├─ if open position: tracker.on_fill(side, avg_price, size, stop_price)
  ├─ if disk diverges: log warning, broker wins
  ├─ if disk-only: clear disk
  └─ if REST fails: fall back to disk snapshot (Layer 2) or stay flat + log
  ↓
level_monitor.set_broker_adapter(adapter)
  ↓
stream.start() ← replayed fills now match tracker correctly
  ↓
_reconcile_position_loop starts (60s interval)
```

## Error handling

- **TopstepX REST timeout** during reconciliation: log error, attempt Layer 2 (disk snapshot) fallback, log clearly which path was used. Do NOT block bootstrap on REST failure — the SignalR stream is more important than REST recovery.
- **Disk file corrupted / unparseable**: log error, treat as no-disk-state, proceed with Layer 1 only. Don't crash bootstrap.
- **Reconciliation finds size mismatch (Layer 3)**: halt + flatten, log loudly. Better to take a small wash trade than to operate with diverged state.
- **Race during reconciliation**: a fill arrives between TopstepX query and `tracker.on_fill`. The fill is dropped (tracker.is_flat). Acceptable — the position size is still reconciled because we set tracker from the broker REST response.

## Testing / verification

- **Unit:** test_tracker_reconciler.py covers the 5 cases above.
- **Integration manual:** open a small NQ position (size=1) in production, then `docker compose restart backend` (don't rebuild — just restart). On startup, logs should show `tracker reconciled from broker: side=long entry=X size=1`. `runtime-status` should show non-zero `peak_R` updating within seconds. Stop hit / TP path / reversal path should all work.
- **Layer 3 chaos test (manual):** open a position, then via TopstepX dashboard manually flatten it. Within 60s the periodic reconciler should detect size mismatch and halt + flatten the (already-zero) position locally.

## Out of scope

- Recovering historical peak_R. New ticks rebuild from current price; sufficient.
- Generalizing to multiple symbols. Current scope is single-contract NQ trading.
- Reconciling SignalR-side state (the GatewayUserAccount payload that fires periodically) — that's a separate channel and not part of this fix.
- The user-flagged trading-plan gap (zone-aware trail-on-cont) — covered in a separate spec.

## Open questions

None at design-approval time. The TopstepX REST endpoint exact paths must be verified during implementation against `topstepx_client.py` — but that's an implementation detail, not a design decision.
