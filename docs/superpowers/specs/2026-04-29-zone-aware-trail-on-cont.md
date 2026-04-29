# Zone-Aware Trail-on-Continuation — Design

**Date:** 2026-04-29
**Status:** Approved (writing plan next)
**Depends on:** [2026-04-29-broker-tracker-reconciliation.md](2026-04-29-broker-tracker-reconciliation.md) — must ship first so the tracker is in a known-good state before zone-touch trail logic relies on it.

## Problem

The current trading plan implementation only matches the user's stated plan partially:

| User's plan | Implementation today | Match? |
|---|---|---|
| At +2R: move stop to small profit (cover fees + spread) | BE-lock fires at `peak_R >= 2.0` → stop to `entry + 2 ticks` (≈ +$10 on NQ) | ✅ Correct |
| TP target = next S/R zone (long: above, short: below) | Hardcoded `tp_price = entry + 2*offset`, never placed as broker order — pure metadata | ❌ Major gap |
| At each zone touched while in-position: orderflow + S/R drives cont vs reversal decision | `_emit_zone_dqn_inference` fires for ALL zone touches; the in-position branch ([level_monitor.py:1540-1586](../../../backend/src/market_data/level_monitor.py#L1540)) only routes to (reversal_exit / early_exit / pyramid_add). No "continuation → trail stop up" branch. | ❌ Major gap |
| Reversal at zone → exit | `rev.get("should_exit")` flatten ([level_monitor.py:1552](../../../backend/src/market_data/level_monitor.py#L1552)) | ✅ Correct |

**Observed live impact:** A long entered at 27226 on 2026-04-29 reached +3R (price 27250+) without ever moving the stop above `entry + 2 ticks`. There was no logic to trail the stop up to a previously-broken zone as the trade advanced. The trade either rides until stop hit (locks $10), opposite signal, or end-of-day. Even when the trade moves through 2-3 zones, no progressive lock-in happens.

## Goal

When a position is open AND `peak_R >= 2.0` (BE-lock has fired) AND price reaches a new resistance/support zone (above entry for long, below for short), make a deliberate cont-vs-reversal decision driven by orderflow + DQN probabilities, with two outcomes:

- **Continuation:** trail stop UP to the previously-broken zone (the zone where the trade most recently advanced past). Trade keeps riding toward the next zone.
- **Reversal:** flatten at the current zone — let the existing reversal-exit branch handle it.

Effect: a winning trade that runs through 3 zones locks in profit at each step. A 4R trade that reverses can give back at most one zone before the trail stop hits, instead of all the way to entry+$10.

The 2R BE-lock stays exactly as-is — it covers the "stop to small profit after fees" requirement and triggers BEFORE any zone-trail decision is meaningful.

## Non-goals

- Place TP as an actual broker order. The dynamic-TP behavior is implemented via the cont-vs-reversal decision at each zone — when reversal fires, `flatten` runs. No need for a TopstepX TP order.
- Multi-tier scaling out (close 50% at TP1, leave runner). Architecturally distinct; deferrable. A single full-size position with progressive trail-stop captures most of the same value with less complexity.
- Cross-instrument generalization. Single-contract NQ for now.
- Backfilling historical trades with what-would-have-been zone trail outcomes. Forward-looking only.

## Architecture

### Concept: "current zone"

The position has a notion of `current_zone_R`, set at entry to 0 (still at the entry zone) and incremented when price advances past a new zone. Stored in `_pending_trade["current_zone_R"]` so it survives restarts via the Layer-2 disk snapshot from the dependency spec.

When price reaches a zone above `current_zone_R` (for a long) and `peak_R >= 2.0`, the system fires a "zone advance" event:

1. Compute the cont vs rev decision (use existing DQN cont_p / rev_p + orderflow_score logic).
2. If continuation:
   - Trail stop to the previously-broken zone's edge — for a long, the upper bound of the zone we just *broke through* (i.e., the boundary above the previous current_zone). Use `_round_tick`. Defense-in-depth via existing `modify_stop` only-tighten guards ([broker_adapter.py:469-528](../../../backend/src/stocks/broker_adapter.py#L469)).
   - Update `_pending_trade["current_zone_R"]` to the new zone.
   - Persist via `_set_pending_trade` (so a restart in the middle of a 3-zone run remembers progress).
3. If reversal: do nothing here (let the existing `rev.should_exit` path handle the flatten). Or fire it explicitly if rev signals are absent but the cont decision is "weak" — a tunable knob.

### Where the new branch sits

In [`level_monitor.py:1540-1586`](../../../backend/src/market_data/level_monitor.py#L1540), the in-position handler has three existing branches (mutually exclusive, in priority order):

```python
if rev.get("should_exit"):           # 1. Reversal exit
    ...
elif not tr.locked_half_R and tr.peak_R >= 0.5 and ee_prob >= ee_thresh:   # 2. Early exit
    ...
elif pyr.get("should_add"):          # 3. Pyramid add
    ...
```

Add a fourth branch BEFORE pyramid_add but AFTER early_exit:

```python
elif tr.peak_R >= 2.0 and _is_new_zone_advance(broker, zone, price):   # 4. Cont trail (NEW)
    ...
```

The new branch is mutually exclusive with the existing three — only fires when the others don't.

### Computing trail target

For a long that just advanced from zone A (current_zone) to zone B (newly-touched zone above):

- The "zone we just broke" is the band BETWEEN A and B. Specifically, the upper edge of zone A is the level that was the resistance — once broken, it becomes support.
- Trail stop target = `zone_A.upper_bound` (rounded down to tick).
- Apply only-tighten via `modify_stop` (which already enforces "stops never relax" — long stops only move up).

For a short, symmetric: trail to `zone_A.lower_bound`.

If no zone A is identifiable (e.g. trade entered in open space, this is the first zone advance past +2R), fall back to `entry + 1.0R` (locks half the unrealized gain at +2R). This handles the "no nearby zone in trade direction below" edge case.

### Zone identification at advance

When the in-position handler fires for a zone-touch event, it has access to:

- `zone` (the touched zone, with `center_price`, `upper_bound`, `lower_bound`, `member_count`)
- `broker.tracker.entry_price`, `broker.tracker.side`, `broker.tracker.peak_R`
- `_pending_trade["current_zone_R"]` (last advance level)

A zone "advance" condition for a long:
- `zone.center_price > entry_price` (above entry)
- `zone.center_price > zone_at_current_R_level` (above where we already trailed to)
- `peak_R >= 2.0` (BE-lock has fired — the trade is in profit territory)

Implementation detail: tracking "current_zone_R" as an R-level is more robust than tracking the zone object itself (zones can be rebuilt every 5 min). Compute `current_zone_R = (zone.center_price - entry_price) / risk_unit` (long; sign-flipped for short).

When advancing past a new zone, set `current_zone_R` to the R-multiple of the newly-touched zone's center.

To find the "previous zone" for trail target: search the zone list for the highest zone (long) below current price, then use that zone's upper_bound as the stop target.

## Components

### A. Helper: `_compute_zone_trail_target`

**File:** `backend/src/market_data/level_monitor.py` (or a new helper module if cleaner)

Pure function. Inputs: `tracker`, `current_zone` (the zone just touched), `all_zones`. Output: `(target_stop_price: float, advance_zone_R: float)`. Returns `None` if no advance is warranted.

### B. New in-position branch in level_monitor

**File:** `backend/src/market_data/level_monitor.py:~1571` (between early-exit and pyramid_add)

```python
elif tr.peak_R >= 2.0:
    # 4. Cont-trail: at a new zone above (long) or below (short) entry,
    #    trail stop to the previously-broken zone's edge.
    trail = _compute_zone_trail_target(broker, zone, self._zones)
    if trail is not None:
        target_stop, advance_zone_R = trail
        logger.info(
            "Cont-trail: peak_R=%.2f advance_zone=%.2f → trail stop to %.2f",
            tr.peak_R, advance_zone_R, target_stop,
        )
        asyncio.create_task(broker.modify_stop(target_stop))
        # Record the advance so we don't re-trail at the same zone
        if broker._pending_trade:
            broker._pending_trade["current_zone_R"] = advance_zone_R
            broker._set_pending_trade(broker._pending_trade)
```

### C. Adjacency: persist `current_zone_R` in `_pending_trade`

**File:** `backend/src/stocks/broker_adapter.py:_execute_entry`

When creating `_pending_trade` ([broker_adapter.py:976-1000](../../../backend/src/stocks/broker_adapter.py#L976)), initialize `"current_zone_R": 0.0`. The Layer-2 snapshot from the dependency spec already round-trips this through `_set_pending_trade`.

### D. Tests

**File:** `backend/tests/test_zone_trail.py` (new)

Cases:

1. **First zone advance past +2R, prior zone exists**: trail target = prior zone's upper_bound (long) / lower_bound (short).
2. **First zone advance, no prior zone in trade direction**: trail target = entry + 1.0R fallback.
3. **Same zone touched twice**: second touch produces no trail (idempotent).
4. **Zone advance below entry**: ignored (this is for trades-in-loss, not trail-up).
5. **peak_R < 2.0**: no trail target, branch doesn't fire.
6. **Reversal signal also fires**: priority — reversal wins, trail branch skipped.
7. **Pyramid signal also fires**: priority — trail wins (we want to lock gains before adding more risk).

Mock `broker`, `_zones`, and `tracker`. Pure function tests for `_compute_zone_trail_target`; integration-ish test for the branch logic via a helper that exercises the in-position dispatcher.

## Data flow

```
Position entered at 27226 (long, +R risk_unit = 8.25, stop = 27217.75, peak_R = 0)
  ↓
Price advances to 27242 (+1.94R) — peak_R updates to 1.94
  ↓
Price advances to 27243 (peak_R = 2.06) — BE-lock fires
  ├─ stop → 27226.5 (entry + 2 ticks)
  ├─ tracker.locked_BE = True
  └─ current_zone_R = 0.0 (still at entry zone)
  ↓
Price advances to 27258 — touches zone Z1 at price ~27258 (peak_R = 3.88)
  ├─ in-position handler fires
  ├─ _compute_zone_trail_target(tracker, Z1, all_zones)
  │     finds prior zone Z0 below at center ~27244 (the 2R level)
  │     returns (Z0.upper_bound = 27246.5, advance_zone_R = 3.88)
  ├─ broker.modify_stop(27246.5) — stops can only go up; 27246.5 > 27226.5 ✓
  └─ _pending_trade["current_zone_R"] = 3.88
  ↓
Price advances to 27272 — touches zone Z2 (peak_R = 5.58)
  ├─ in-position handler fires
  ├─ _compute_zone_trail_target finds Z1 below at 27258
  ├─ broker.modify_stop(27258 upper_bound)
  └─ current_zone_R = 5.58
  ↓
Price reverses to 27258 → stop at Z1.upper hit
  └─ Realized exit ≈ +3.88R (locked at Z1 trail) instead of +0.06R (BE-lock alone)
```

## Error handling

- **`_compute_zone_trail_target` returns None** (no prior zone found): branch fires fallback `entry + 1.0R`. If even that fails (zero-risk_unit edge case): log warning, no trail, position unchanged.
- **`modify_stop` only-tighten guard rejects** (race where stop has already moved): logged via existing `modify_stop call:` diagnostic, no harm done.
- **Multiple zones touched in same tick**: only the BEST zone (highest member_count) fires inference, per existing `_emit_zone_dqn_inference` logic ([level_monitor.py:815-826](../../../backend/src/market_data/level_monitor.py#L815)). Trail uses that zone.
- **Restart mid-trade**: `current_zone_R` survives via Layer-2 snapshot from dependency spec. After reconciliation, the position resumes with the correct trail level.
- **Same zone re-touched**: `advance_zone_R > current_zone_R` guard prevents re-trailing at the same level. Idempotent.

## Testing / verification

- **Unit:** `test_zone_trail.py` covers the 7 cases above.
- **Integration manual:** open a small NQ size=1 long, wait for +2R advance, then watch logs for `Cont-trail: peak_R=X.XX advance_zone=Y.YY → trail stop to Z.ZZ`. Verify TopstepX shows the new stop price.
- **Backtest sanity:** can't fully backtest without rebuilding zone history per tick, but a spot check on the last 30 days of broker_trades closed at small profit should show how many would have hit a Z1 trail stop instead. (Optional, cheap to do.)

## Out of scope

- DQN retraining with cont-trail in the obs vector. The trail decision uses existing cont_p / rev_p / orderflow_score; the model's output drives this branch but doesn't need to be retrained on it (it's a downstream decision rule, not a feature).
- Multi-zone confluence weighting (e.g., trail stop further when multiple zones cluster). Stick to "trail to previous broken zone" first; layer in confluence later if data supports it.
- Per-trade trail aggressiveness tuning (some setups deserve looser trails). Punt to a v2 if needed.
- Tracker reconciliation on restart — that's the dependency spec.

## Open questions

None at design-approval time. Implementation will need to verify:
- The exact `_zones` list shape on `level_monitor` (does it include zones below entry too, or only those near current price? — answer: all active zones).
- Whether `zone.upper_bound` / `zone.lower_bound` round to tick — they should, since they come from the zone builder which respects TICK_SIZE.
