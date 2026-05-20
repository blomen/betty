# Widget Mirror — Original Stop Band + Trail Line Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the TradingView active and closed position widgets always show the planned 1R stop band (so R:R label stays "2") plus a separate trail line at the actually-placed stop after BE-lock / cont-trail moves.

**Architecture:** Add `broker_trades.final_stop_price` to persist the trailed-stop at exit time. Capture `original_stop_price` on `_pending_trade` at entry time so it survives modify_stop walks. Expose both fields through the runtime-status and broker-trades endpoints, the local pollers, and the broadcaster's `position_upsert` payload using unified field names (`original_stop_price`, `placed_stop_price`). Update the extension's widget renderer to derive `stopLevel` from `original_stop_price` always and call a shared `_drawTrailLineIfMoved` helper for both active and closed shapes.

**Tech Stack:** Python (FastAPI, SQLAlchemy on Postgres), JavaScript (TradingView Charting Library widget API), Tampermonkey-style Chrome extension.

**Spec:** [docs/superpowers/specs/2026-05-08-widget-mirror-trail-line.md](../specs/2026-05-08-widget-mirror-trail-line.md)

**Note on testing:** the codebase has no automated harness for the chart extension or the FastAPI position pipeline; verification is a mix of `python -c` import smoke-checks, `node --check` syntax checks, and manual observation on the live TradingView page after the backend deploys. Plan steps reflect this.

---

## File structure

| File | Responsibility |
|---|---|
| [backend/src/db/models.py](../../../backend/src/db/models.py) | ORM definition + lightweight migration list |
| [backend/src/stocks/broker_adapter.py](../../../backend/src/stocks/broker_adapter.py) | `_pending_trade` capture of `original_stop_price`; both `_log_broker_trade` call sites pass `final_stop_price` |
| [backend/src/stocks/server_bootstrap.py](../../../backend/src/stocks/server_bootstrap.py) | Direct DB persist + `trade_closed` payload include `final_stop_price` |
| [backend/src/api/routes/stocks.py](../../../backend/src/api/routes/stocks.py) | `runtime-status` returns `position.original_stop_price`; `broker-trades` row_dict returns `final_stop_price` |
| [arnold/stocks_runtime.py](../../../arnold/stocks_runtime.py) | `_passive_position_poller` reads `original_stop_price` and stashes it on the position dict |
| [arnold/tv_overlay/broadcaster.py](../../../arnold/tv_overlay/broadcaster.py) | `reconcile_trades` and active-trade synthesis emit unified `original_stop_price` + `placed_stop_price` |
| [arnold/tv_overlay/extension/page.js](../../../arnold/tv_overlay/extension/page.js) | Drop `stopInProfit ? 0` branch; shared `_drawTrailLineIfMoved` helper; closed widget calls it |

The userscript (`arnold/tv_overlay/userscript/arnold-overlay.user.js`) renders closed trades as simple rectangles with no stop bands — out of scope per the spec.

---

## Task 1: Add `final_stop_price` column + migration

**Files:**
- Modify: [backend/src/db/models.py](../../../backend/src/db/models.py) (around line 2641 — `BrokerTrade` class; around line 2374 — `_run_pg_migrations` additions list)

- [ ] **Step 1: Add the column to the ORM model**

In `backend/src/db/models.py`, find the `class BrokerTrade(Base):` block (line 2602). Locate the existing line (2634) `stop_price = Column(Float, nullable=True)` and add the new column on the line below it:

```python
    entry_price = Column(Float, nullable=False)
    stop_price = Column(Float, nullable=True)
    # Actual placed stop at exit time — captures BE-lock + cont-trail walks.
    # NULL on rows pre-2026-05-08 migration; widget falls back to "no trail line".
    final_stop_price = Column(Float, nullable=True)
    tp_price = Column(Float, nullable=True)
```

- [ ] **Step 2: Add the migration entry**

In the same file, find `_run_pg_migrations` (line 2330). Locate the most recent broker_trades entry — currently the 2026-05-07 `entry_order_id` / `exit_order_id` block at lines 2380-2381 — and append a new entry at the END of the `additions` list, just before the closing `]`:

```python
        ("broker_trades", "exit_order_id", "BIGINT"),
        # 2026-05-08 — DQN raw action q-values, persisted at signal time so we
        # can analyze action margin and calibration without re-running inference.
        ("stock_signals", "q_values", "JSONB"),
        # 2026-05-08 — actual placed stop at exit time (BE-lock + cont-trail
        # walks). Used by the chart widget to draw a trail line at the
        # final stop while keeping the original stop_price band visible
        # for the R:R label. NULL on legacy rows.
        ("broker_trades", "final_stop_price", "DOUBLE PRECISION"),
    ]
```

- [ ] **Step 3: Smoke-check the import**

Run from the repo root:

```
cd backend && python -c "from src.db.models import BrokerTrade; print('final_stop_price' in BrokerTrade.__table__.columns)"
```

Expected: `True`

- [ ] **Step 4: Commit**

```bash
git add backend/src/db/models.py
git commit -m "feat(stocks): add broker_trades.final_stop_price column for trail-line rendering"
```

---

## Task 2: Capture `original_stop_price` and persist `final_stop_price`

**Files:**
- Modify: [backend/src/stocks/broker_adapter.py](../../../backend/src/stocks/broker_adapter.py) (around line 1799-1827 — `_pending_trade` init in `_execute_entry`; around line 822 and line 1402 — `_log_broker_trade` calls; line 763-769 — recovery-path stop derivation)
- Modify: [backend/src/stocks/server_bootstrap.py](../../../backend/src/stocks/server_bootstrap.py) (around line 230-260 — direct DB persist; around line 295-309 — `_trade_payload_to_dict`)

- [ ] **Step 1: Capture `original_stop_price` on `_pending_trade` at entry time**

In `backend/src/stocks/broker_adapter.py`, find the `self._pending_trade = {` block (line 1799). Locate the existing `"stop_price": stop_price,` line (currently line 1805) and add `original_stop_price` directly after it:

```python
            "stop_price": stop_price,
            # Captured once at entry, never mutated by modify_stop.
            # The widget reads this to draw the planned-1R band so R:R
            # stays correct after BE-lock / cont-trail walks shift stop_price.
            "original_stop_price": stop_price,
            "tp_price": tp_price,
```

- [ ] **Step 2: Pass `final_stop_price` to the normal-exit `_log_broker_trade` call**

Find the `_log_broker_trade(` call at line 1402 in `backend/src/stocks/broker_adapter.py`. Locate `stop_price=stop_price,` (line 1410) and add `final_stop_price` directly after it. Also tighten `stop_price` to read from `pt["original_stop_price"]` when available, falling back to the existing `stop_price` local for orphan-recovery cases:

```python
                entry_price=entry_px,
                stop_price=pt.get("original_stop_price") or stop_price,
                final_stop_price=self.tracker.stop_price or pt.get("stop_price") or stop_price,
                tp_price=pt.get("tp_price"),
```

`self.tracker.stop_price` reflects every BE-lock + cont-trail walk (verified via [broker_adapter.py:344, 959, 977](../../../backend/src/stocks/broker_adapter.py#L344)). The fallback chain handles trackers that went flat before the persist — falling through to `pt["stop_price"]` (last placed stop in pending) and finally `stop_price` (entry-time recompute).

- [ ] **Step 3: Pass `final_stop_price` to the recovery-path `_log_broker_trade` call**

Find the `_log_broker_trade(` call at line 822 in the same file. Same edit pattern — locate `stop_price=stop_price,` (line 817) and add the new param. Recovery path's `self.tracker` may already be flat (`tracker.stop_price` reset to 0), so prefer `pt["stop_price"]` first:

```python
            entry_price=entry_px,
            stop_price=pt.get("original_stop_price") or stop_price,
            final_stop_price=pt.get("stop_price") or self.tracker.stop_price or stop_price,
            tp_price=pt.get("tp_price"),
```

- [ ] **Step 4: Persist `final_stop_price` to broker_trades via direct write**

In `backend/src/stocks/server_bootstrap.py`, find the `BrokerTrade(` constructor call at line 230. Locate `stop_price=p.get("stop_price"),` (line 238) and add `final_stop_price` directly after it:

```python
                row = BrokerTrade(
                    ts=ts_open,
                    profile_id=profile_row.id if profile_row else None,
                    session_date=p.get("session_date") or ts_open.strftime("%Y-%m-%d"),
                    symbol=p.get("symbol", "NQ"),
                    side=p.get("side"),
                    size=p.get("size"),
                    entry_price=p.get("entry_price"),
                    stop_price=p.get("stop_price"),
                    final_stop_price=p.get("final_stop_price"),
                    tp_price=p.get("tp_price"),
```

- [ ] **Step 5: Forward `final_stop_price` on the `trade_closed` broadcast payload**

In the same file, find `_trade_payload_to_dict` (line 279). Locate `"stop_price": p.get("stop_price"),` (line 300) and add `final_stop_price` directly after it:

```python
        "entry_price": p.get("entry_price"),
        "stop_price": p.get("stop_price"),
        "final_stop_price": p.get("final_stop_price"),
        "tp_price": p.get("tp_price"),
```

- [ ] **Step 6: Smoke-check imports**

Run from the repo root:

```
cd backend && python -c "from src.stocks.broker_adapter import _log_broker_trade; from src.stocks.server_bootstrap import _persist_broker_trade_direct, _trade_payload_to_dict; print('OK')"
```

Expected: `OK`

- [ ] **Step 7: Commit**

```bash
git add backend/src/stocks/broker_adapter.py backend/src/stocks/server_bootstrap.py
git commit -m "feat(stocks): capture original_stop_price + persist final_stop_price at trade close"
```

---

## Task 3: Expose new fields on the API endpoints

**Files:**
- Modify: [backend/src/api/routes/stocks.py](../../../backend/src/api/routes/stocks.py) (lines 128-180 — `runtime_status`; line 504-527 — `_row_dict` in `broker_trades`)

- [ ] **Step 1: Add `original_stop_price` to the `runtime_status` response**

In `backend/src/api/routes/stocks.py`, find the `def runtime_status(request: Request):` endpoint (line 128). Locate the existing block that derives `entry`, `stop`, `tp` (lines 150-153). Replace it with:

```python
    pending = getattr(adapter, "_pending_trade", None) or {}
    entry = tracker.entry_price or float(pending.get("entry_price") or 0.0) or float(pending.get("signal_price") or 0.0)
    stop = tracker.stop_price or pending.get("stop_price")
    tp = pending.get("tp_price")
    # Original entry-time stop, captured once on _pending_trade and never
    # mutated by modify_stop. Used by the chart widget to render the
    # planned-1R band (so R:R = 2 stays correct after trail walks).
    original_stop = pending.get("original_stop_price")
```

Then locate the `"position": {` dict (line 160) and add the new field next to `stop_price`:

```python
        "position": {
            "flat": tracker.is_flat,
            "side": tracker.side,
            "size": tracker.size,
            "entry_price": entry,
            "stop_price": float(stop) if stop is not None else 0.0,
            "original_stop_price": float(original_stop) if original_stop is not None else None,
            "tp_price": float(tp) if tp is not None else None,
            "peak_R": tracker.peak_R,
            "locked_half_R": tracker.locked_half_R,
        },
```

- [ ] **Step 2: Add `final_stop_price` to the `broker-trades` row_dict**

In the same file, find `_row_dict` (line 504). Locate `"stop_price": r.stop_price,` (line 513) and add `final_stop_price` directly after it:

```python
    def _row_dict(r: BrokerTrade) -> dict:
        return {
            "id": r.id,
            "ts": r.ts.isoformat() if r.ts else None,
            "session_date": r.session_date,
            "symbol": r.symbol,
            "side": r.side,
            "size": r.size,
            "entry_price": r.entry_price,
            "stop_price": r.stop_price,
            "final_stop_price": r.final_stop_price,
            "tp_price": r.tp_price,
```

- [ ] **Step 3: Smoke-check imports**

```
cd backend && python -c "from src.api.routes.stocks import runtime_status, _row_dict if False else None; from src.api.routes import stocks as _; print('OK')"
```

(The `if False` is to defang the import-time symbol check in case `_row_dict` is locally scoped — the goal is just to confirm the module loads.) Actually use:

```
cd backend && python -c "import importlib; importlib.import_module('src.api.routes.stocks'); print('OK')"
```

Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add backend/src/api/routes/stocks.py
git commit -m "feat(stocks): expose original_stop_price + final_stop_price on runtime-status / broker-trades"
```

---

## Task 4: Local poller forwards `original_stop_price`

**Files:**
- Modify: [arnold/stocks_runtime.py](../../../arnold/stocks_runtime.py) (around line 175-215 — `_passive_position_poller`)

- [ ] **Step 1: Read `original_stop_price` from the runtime-status response and put it on the position dict**

In `arnold/stocks_runtime.py`, find the `if pos and not pos.get("flat"):` block in `_passive_position_poller` (around line 176). Locate the existing assignment block:

```python
                stop = pos.get("stop_price")
                tp = pos.get("tp_price")
```

(currently around lines 181-182). Add a new line immediately after for `original_stop`:

```python
                stop = pos.get("stop_price")
                original_stop = pos.get("original_stop_price")
                tp = pos.get("tp_price")
```

Then locate the `update_positions([{...}])` call (around line 205-215) and add the new field to the dict:

```python
                    update_positions(
                        [
                            {
                                "price": effective_entry,
                                "size": size,
                                "side": side,
                                "entry_time": entry_time,
                                "stop_price": float(stop) if stop else None,
                                "original_stop_price": float(original_stop) if original_stop else None,
                                "tp_price": float(tp) if tp else None,
                            }
                        ]
                    )
```

If the existing `update_positions` call doesn't already include `stop_price` (some earlier versions left it implicit), add it explicitly per the snippet above. The `_passive_position_poller` is the only writer to this list.

- [ ] **Step 2: `_passive_trades_poller` requires no edit** — it does pure JSON passthrough of the `/api/stocks/broker-trades` response, so once Task 3 is in place the new `final_stop_price` field appears on every trade dict in `dash_state["trades"]` automatically. Verify by reading the poller code:

```
cd /c/Users/rasmu/arnold && python -c "
import re
with open('arnold/stocks_runtime.py') as f:
    src = f.read()
m = re.search(r'_passive_trades_poller.*?await asyncio.sleep', src, re.S)
print('passes through JSON' if m and 'r.json()' in m.group(0) else 'NEEDS EDIT')
"
```

Expected: `passes through JSON`

- [ ] **Step 3: Commit**

```bash
git add arnold/stocks_runtime.py
git commit -m "feat(stocks): poll runtime-status original_stop_price into local position state"
```

---

## Task 5: Broadcaster emits unified field names

**Files:**
- Modify: [arnold/tv_overlay/broadcaster.py](../../../arnold/tv_overlay/broadcaster.py) (around line 200-260 — `reconcile_trades`; around line 498-512 — active-trade synthesis)

- [ ] **Step 1: Add fields to the closed-trade `position_upsert` payload in `reconcile_trades`**

Find the payload-construction block in `reconcile_trades` (broadcaster.py around line 234-258). Locate the line that currently emits `"stop"` (the existing field name the widget reads) and the surrounding context. The exact payload assembly differs by version, but the goal is to add two new keys to whatever dict is being upserted per closed trade. Add:

```python
                "stop": float(t["stop_price"]) if t.get("stop_price") is not None else None,
                # Unified field names — widget uses these; legacy `stop` field
                # kept for back-compat for one release cycle.
                "original_stop_price": float(t["stop_price"]) if t.get("stop_price") is not None else None,
                "placed_stop_price": float(t["final_stop_price"]) if t.get("final_stop_price") is not None else None,
                "tp": float(t["tp_price"]) if t.get("tp_price") is not None else None,
```

(Place adjacent to the existing `stop` / `tp` keys. If those exact keys don't appear under those names, locate whichever fields the userscript reads as `p.stop` and `p.tp` and add the new keys alongside.)

- [ ] **Step 2: Add fields to the active-trade synthesis dict**

Find the active-trade synthesis at around line 498-512 in the same file. Locate the dict that `trades.insert(0, {...})` adds. The current shape includes `"stop_price": model_status.get("stop_price")` and `"tp_price": ...`. Add the two unified fields:

```python
                            trades.insert(
                                0,
                                {
                                    "id": "active",
                                    "ts": _epoch_to_iso(first.get("entry_time")),
                                    "side": first.get("side"),
                                    "size": first.get("size", 1),
                                    "entry_price": entry,
                                    "stop_price": model_status.get("stop_price"),
                                    "tp_price": first.get("tp_price") or model_status.get("tp_price"),
                                    # Unified fields used by the widget.
                                    "original_stop_price": first.get("original_stop_price") or model_status.get("stop_price"),
                                    "final_stop_price": model_status.get("stop_price"),
                                    "exit_price": None,
                                    "closed_at": None,
                                    "pnl_dollars": None,
                                    "halted": bool(first.get("halted", False)),
                                },
                            )
```

The fall-through `first.get("original_stop_price") or model_status.get("stop_price")` means: if the local poller hasn't been updated yet (older arnold.bat), we fall back to the live placed stop, which makes `original_stop_price == placed_stop_price`. The widget then draws no trail line and renders identically to today — graceful degradation.

- [ ] **Step 3: Smoke-check Python syntax**

```
cd /c/Users/rasmu/arnold && python -c "import ast; ast.parse(open('arnold/tv_overlay/broadcaster.py').read()); print('OK')"
```

Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add arnold/tv_overlay/broadcaster.py
git commit -m "feat(tv-overlay): emit original_stop_price + placed_stop_price in position_upsert payload"
```

---

## Task 6: Extension widget — shared trail-line helper + drop `stopInProfit ? 0` branch

**Files:**
- Modify: [arnold/tv_overlay/extension/page.js](../../../arnold/tv_overlay/extension/page.js) (lines 838-925 — `_drawActivePositionShape`; lines 929-986 — `_syncTrailLine`; lines 1014-1110 — `_drawClosedPositionWidget`)

This is the load-bearing change — read each step carefully.

- [ ] **Step 1: Add a shared `_drawTrailLineIfMoved` helper**

Just above the existing `_syncTrailLine` function (line 929 in `arnold/tv_overlay/extension/page.js`), insert the new helper. The helper unifies what `_syncTrailLine` does today for the active widget so both active and closed call the same code:

```js
  // Shared trail-line drawer. Called by both active and closed widgets.
  // Draws a horizontal_line at `placedStop` when it differs from
  // `originalStop` by ≥ 1 NQ tick. Removes any prior trail line for the
  // position key when the stops match (so legacy / unmoved trades show
  // no extra line). Color: green when stop is in profit (long: above
  // entry; short: below), amber when walked but still on the loss side
  // (rare — defensive trail tightening on an underwater trade).
  async function _drawTrailLineIfMoved(p, anchor, endEpoch, originalStop, placedStop) {
    const trailKey = `${p.key}:trail`;
    const NQ_TICK = 0.25;
    const orig = Number(originalStop);
    const placed = Number(placedStop);
    const entry = Number(p.entry);
    if (!Number.isFinite(orig) || !Number.isFinite(placed) || !Number.isFinite(entry)) {
      _safeRemoveTrail(trailKey);
      return;
    }
    if (Math.abs(orig - placed) < NQ_TICK) {
      _safeRemoveTrail(trailKey);
      return;
    }
    const isLong = p.side === 'long';
    const inProfit = isLong ? placed > entry : placed < entry;
    const color = inProfit ? '#10b981' : '#f59e0b';
    const points = [
      { time: anchor, price: placed },
      { time: endEpoch, price: placed },
    ];
    const overrides = {
      linecolor: color,
      linestyle: 2,        // dashed
      linewidth: 1,
      showLabel: false,
    };
    const existing = drawnLevels.get(trailKey);
    if (existing && existing.shapeId != null && typeof chart.getShapeById === 'function') {
      try {
        const obj = chart.getShapeById(existing.shapeId);
        if (obj) {
          if (typeof obj.setPoints === 'function') obj.setPoints(points);
          if (typeof obj.setProperties === 'function') obj.setProperties(overrides);
          return;
        }
      } catch (_) {}
    }
    try {
      const shapeId = await _resolve(chart.createMultipointShape(points, {
        shape: 'horizontal_line',
        disableSave: true,
        overrides,
      }));
      if (shapeId == null) return;
      if (existing && existing.shapeId != null && existing.shapeId !== shapeId) {
        try { chart.removeEntity(existing.shapeId); } catch (_) {}
      }
      drawnLevels.set(trailKey, { shapeId, kind: 'horizontal_line' });
    } catch (e) {
      sendError(`_drawTrailLineIfMoved failed: ${e instanceof Error ? e.message : String(e)}`);
    }
  }

  function _safeRemoveTrail(trailKey) {
    const existing = drawnLevels.get(trailKey);
    if (existing && existing.shapeId != null && chart) {
      try { chart.removeEntity(existing.shapeId); } catch (_) {}
    }
    drawnLevels.delete(trailKey);
  }
```

This uses the existing `drawnLevels` map (already declared in the file for level rendering). If your build of page.js uses a different name for the levels registry, adjust accordingly — search for `const drawnLevels` to confirm.

- [ ] **Step 2: Drop `stopInProfit ? 0` in `_drawActivePositionShape`**

In `_drawActivePositionShape` (line 838), find lines 855-856:

```js
    const stopInProfit = isLong ? stopPrice > p.entry : stopPrice < p.entry;
    const stopOffsetTicks = stopInProfit ? 0 : Math.round(Math.abs(stopPrice - p.entry) / NQ_TICK);
```

Replace the entire two lines with:

```js
    // stopOffsetTicks is always derived from the ORIGINAL entry-time stop,
    // so TV's auto R:R label stays "2" through the whole trade lifecycle.
    // The trail line drawn by _drawTrailLineIfMoved shows the actually-
    // placed stop after BE-lock / cont-trail walks.
    const originalStop = (typeof p.original_stop_price === 'number' && p.original_stop_price > 0)
      ? Number(p.original_stop_price)
      : stopPrice;
    const stopOffsetTicks = Math.max(1, Math.round(Math.abs(originalStop - p.entry) / NQ_TICK));
```

The `Math.max(1, ...)` floor matches the existing `_drawClosedPositionWidget` convention (line 1031) — `stopLevel = 0` makes TV hide the band entirely.

- [ ] **Step 3: Replace `_syncTrailLine` calls with `_drawTrailLineIfMoved`**

In `_drawActivePositionShape`, find both `_syncTrailLine(...)` invocations:

- Line 892: `_syncTrailLine(p, anchor, endEpoch, stopPrice, stopInProfit);`
- Line 919: `_syncTrailLine(p, anchor, endEpoch, stopPrice, stopInProfit);`

Replace both with (note: the call is now async-friendly via promise chaining; existing call sites don't await, which is fine):

```js
      _drawTrailLineIfMoved(p, anchor, endEpoch, originalStop, stopPrice);
```

(`stopPrice` here is the live placed stop the function already computed at line 841 — `placed_stop_price` from the payload via the existing `p.stop` mapping.)

Also update the `stopInProfit` references that may remain elsewhere in the function — search the function body for `stopInProfit` and remove any uses (the variable no longer exists). The only remaining concern is decorative — TV's `stopLineColor` override in some configs depends on whether stop is in profit; if your file has any `linecolor: stopInProfit ? ... : ...` ternaries, replace them with the unconditional default-band color (let TV's defaults paint the original stop band red on the loss side).

- [ ] **Step 4: Add the trail-line call to `_drawClosedPositionWidget`**

In `_drawClosedPositionWidget` (line 1014), find the `try { … }` block that creates or mutates the long_position shape. After the try-block (i.e., AFTER the existing return-success or fall-through paths but BEFORE the function returns), add a call to draw the trail line:

```js
      // Trail line for closed trades — same helper as active. Draws iff
      // the placed stop at exit differs from the entry-time stop. Bounded
      // to the trade's exit time (anchor → endEpoch).
      _drawTrailLineIfMoved(p, anchor, endEpoch, p.original_stop_price ?? stopPx, p.placed_stop_price);
```

Insert this immediately after `drawnPositions.set(p.key, { shapeId, kind: shapeName });` (line 1103) AND in the mutate-in-place success branch (line 1077, just before `return true`). Both paths must call it.

For closed trades, `p.original_stop_price` is sent by the broadcaster (Task 5) as the same value as the legacy `p.stop` — so the `??` fallback to `stopPx` keeps legacy rows (NULL `final_stop_price`) rendering with no trail line.

- [ ] **Step 5: Hook trail-line removal into `removePosition`**

Find the `removePosition` function in page.js. After it removes the main shape, also remove the trail line via `_safeRemoveTrail`:

```js
  function removePosition(key) {
    const entry = drawnPositions.get(key);
    drawnPositions.delete(key);
    positionAnchors.delete(key);
    const shapeId = entry && entry.shapeId;
    if (shapeId != null && chart) {
      try { chart.removeEntity(shapeId); } catch (_) {}
    }
    _safeRemoveTrail(`${key}:trail`);
  }
```

(Match the existing function shape; only the `_safeRemoveTrail` call is new.)

- [ ] **Step 6: Smoke-check JS syntax**

```
node --check arnold/tv_overlay/extension/page.js
```

Expected: no output.

- [ ] **Step 7: Commit**

```bash
git add arnold/tv_overlay/extension/page.js
git commit -m "feat(tv-overlay): unify widget rendering — original stop band + trail line for active and closed"
```

---

## Task 7: Manual verification handoff (user action required)

**Files:** none modified. This task documents the post-deploy verification the user must perform.

This task does NOT execute. It captures the test plan that lands after the backend deploy + extension reload.

- [ ] **Step 1: Backend deploy**

The user (NOT the agent) must run from a shell with SSH access:

```bash
ssh root@148.251.40.251 "bash /opt/arnold/scripts/server-deploy.sh rebuild backend"
```

If a position is open and you accept the flatten cost, set `ALLOW_OPEN_POSITION_DEPLOY=1` per [CLAUDE.md](../../../CLAUDE.md). Otherwise wait for flat.

- [ ] **Step 2: Reload the Chrome extension**

`chrome://extensions` → Arnold TV Overlay → reload. Then hard-reload the TradingView NQ tab (Ctrl+F5).

- [ ] **Step 3: Verify the migration**

```bash
ssh root@148.251.40.251 "cd /opt/arnold && docker compose exec -T postgres psql -U arnold -d arnold -c \"SELECT column_name FROM information_schema.columns WHERE table_name='broker_trades' AND column_name='final_stop_price';\""
```

Expected: one row, `final_stop_price`.

- [ ] **Step 4: Live observation checklist**

Monitor the next live trade through its lifecycle:

1. **Pre-2R:** original red 1R band, green 2R band, R:R label = "2". No trail line.
2. **At +2R cross (BE-lock fires):** original red band still visible, R:R = "2", new GREEN dashed horizontal line at entry+2t (long) / entry-2t (short).
3. **At cont-signal next-zone trail (peak_R ≥ 2):** trail line walks up (long) to the previously-broken zone's edge. Original red band unchanged.
4. **At trade close:** widget transitions to closed-shape rendering. Original red 1R band still visible, trail line drawn at the BE-locked / final stop.
5. **A -1R stop-out:** no trail line drawn (placed_stop == original_stop).
6. **Closed legacy row** (any trade closed before the migration): renders exactly as today — no trail line. No regression.

If any step misbehaves, capture: trade id, time, screenshot, DevTools console output → file as bug.

---

## Self-Review

**Spec coverage:**
- Schema add `broker_trades.final_stop_price` — Task 1. ✓
- `original_stop_price` captured on `_pending_trade` — Task 2 Step 1. ✓
- Both `_log_broker_trade` call sites pass `final_stop_price` — Task 2 Steps 2, 3. ✓
- Direct DB persist + trade_closed broadcast forward `final_stop_price` — Task 2 Steps 4, 5. ✓
- `runtime-status` exposes `original_stop_price` — Task 3 Step 1. ✓
- `broker-trades` exposes `final_stop_price` — Task 3 Step 2. ✓
- Local position poller forwards `original_stop_price` — Task 4 Step 1. ✓
- Local trades poller forwards `final_stop_price` (passthrough) — Task 4 Step 2 (verify-only). ✓
- Broadcaster emits unified `original_stop_price` + `placed_stop_price` for both closed and active — Task 5 Steps 1, 2. ✓
- Extension widget drops `stopInProfit ? 0` branch — Task 6 Step 2. ✓
- Shared `_drawTrailLineIfMoved` helper for active and closed — Task 6 Steps 1, 3, 4. ✓
- `removePosition` cleans up trail line — Task 6 Step 5. ✓
- Manual verification on TradingView — Task 7. ✓

**No placeholders:** every step lists exact edits, exact commands, expected output. No "TBD" / "implement later".

**Type / name consistency:**
- DB column: `final_stop_price` (everywhere it's referenced). ✓
- Pending dict key: `original_stop_price` — Task 2 Step 1, Task 3 Step 1. ✓
- Runtime-status JSON: `position.original_stop_price` — Task 3 Step 1, Task 4 Step 1. ✓
- Position dict key (local): `original_stop_price` — Task 4 Step 1, Task 5 Step 2. ✓
- Trades dict key (local): `final_stop_price` — Task 4 Step 2, Task 5 Step 1. ✓
- Broadcaster WS payload: `original_stop_price` + `placed_stop_price` — Task 5 Steps 1, 2; Task 6 Steps 1, 4. ✓
- Widget reads: `p.original_stop_price`, `p.placed_stop_price` — Task 6 Steps 2, 4. ✓
- Helper name: `_drawTrailLineIfMoved` — Task 6 Steps 1, 3, 4, 5. ✓
- Trail-line registry key: `${p.key}:trail` — Task 6 Steps 1, 5. ✓

**Inline fix during review:** Task 3 Step 3 originally had a misleading `if False else None` smoke-check; replaced with `importlib.import_module(...)`.
