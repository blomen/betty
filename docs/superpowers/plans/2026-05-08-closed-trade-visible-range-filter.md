# Closed-trade Visible-range Filter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop closed-trade rectangles piling up at the chart's left edge by filtering them against TradingView's visible range, with auto-redraw on scroll/zoom.

**Architecture:** Pure userscript change in `arnold/tv_overlay/userscript/arnold-overlay.user.js`. Add a `closedTradePayloads` buffer that retains every closed trade received over the WS. Filter at draw time using `chart.getVisibleRange()`; subscribe to `chart.onVisibleRangeChanged()` (debounced 200 ms) to redraw/undraw as the user scrolls. Active trade rendering is untouched.

**Tech Stack:** JavaScript (Tampermonkey userscript), TradingView Charting Library widget API (`IChartWidgetApi.getVisibleRange`, `onVisibleRangeChanged`, `createMultipointShape`, `removeEntity`).

**Spec:** [docs/superpowers/specs/2026-05-08-closed-trade-visible-range-filter.md](../specs/2026-05-08-closed-trade-visible-range-filter.md)

**Note on testing:** Userscripts run inside Tampermonkey's sandbox attached to a live TradingView page — there is no headless test harness. Verification is manual on TradingView with the userscript installed; each task lists explicit observation steps. The local `arnold.bat` server hosts the userscript at `http://127.0.0.1:8000/stocks/api/tv-overlay/userscript`, so Tampermonkey auto-pulls fresh versions on `@version` bump.

---

## Task 1: Add closed-trade buffer + visible-range filter on receive

**Files:**
- Modify: `arnold/tv_overlay/userscript/arnold-overlay.user.js` (around lines 230-259, 397-405, 521)

- [ ] **Step 1: Add the buffer map next to `drawnPositions`**

In `arnold/tv_overlay/userscript/arnold-overlay.user.js`, find the block at lines 230-239 starting with `// Per-position registry.` and add a new map declaration immediately after `positionAnchors`:

```js
  // Per-position registry. Active trade gets TV's long_position widget
  // (live R:R bands, Open P&L); closed trades get a simple time-bounded
  // rectangle (no auto-drawn stop/tp bands — those stack into visual soup
  // when 8+ trades cluster in a tight window). Tracking shape kind so
  // mutate-in-place doesn't try to setProperties across primitive types.
  const drawnPositions = new Map(); // key → { shapeId, kind }
  // Active trade caches its entry-time anchor so stop/tp updates don't
  // scrub the entry handle each tick. Closed trades sync to broker
  // timestamps every render.
  const positionAnchors = new Map();
  // Every closed-trade payload received from the WS, keyed by payload.key.
  // Never evicted by drawPosition — entries persist across visible-range
  // changes so reconcileClosedTradeVisibility can redraw a previously-
  // undrawn trade when the user scrolls to its time window.
  const closedTradePayloads = new Map(); // key → payload
```

- [ ] **Step 2: Add the overlap-test helper above `drawPosition`**

Insert this helper between the registry block and the existing `function drawPosition(p)` declaration (currently line 241):

```js
  // True iff the closed trade's [entry_time, end_time] window overlaps
  // the chart's visible range. Both ranges are epoch seconds. Returns
  // true if the range is unavailable (fail-open during early boot before
  // chart.onChartReady fires).
  function _closedTradeOverlapsRange(payload, range) {
    if (!range || range.from == null || range.to == null) return true;
    const start = Number(payload.entry_time);
    const end = Number(payload.end_time);
    if (!Number.isFinite(start) || !Number.isFinite(end)) return false;
    return !(end < range.from || start > range.to);
  }

  function _currentVisibleRange() {
    if (!chart || typeof chart.getVisibleRange !== 'function') return null;
    try { return chart.getVisibleRange(); } catch (_) { return null; }
  }
```

- [ ] **Step 3: Modify `drawPosition` to buffer + filter closed trades**

Replace the existing function (lines 241-259) with:

```js
  function drawPosition(p) {
    if (!chart) return false;
    const isActive = (p.key === 'trade:active' || p.key === 'pos:current');

    const fillEpoch = (typeof p.entry_time === 'number') ? Math.floor(p.entry_time) : null;
    const now = Math.floor(Date.now() / 1000);
    let anchor;
    if (isActive) {
      if (!positionAnchors.has(p.key)) positionAnchors.set(p.key, fillEpoch != null ? fillEpoch : now);
      anchor = positionAnchors.get(p.key);
    } else {
      anchor = fillEpoch != null ? fillEpoch : now;
    }
    let endEpoch = (typeof p.end_time === 'number') ? Math.floor(p.end_time) : null;
    if (endEpoch == null || endEpoch <= anchor) endEpoch = anchor + 60;

    if (isActive) return _drawActiveShape(p, anchor, endEpoch);

    // Closed trade — always buffer, then draw only if it overlaps the
    // current visible range. Off-range trades stay in the buffer so the
    // range-change reconciler can draw them when scrolled into view.
    closedTradePayloads.set(p.key, p);
    const range = _currentVisibleRange();
    if (_closedTradeOverlapsRange(p, range)) {
      return _drawClosedRect(p, anchor, endEpoch);
    }
    // Out of range: ensure no stale shape lingers from a prior in-range emit.
    if (drawnPositions.has(p.key)) removePosition(p.key);
    return true; // accepted (buffered), so the WS sender still gets an ack.
  }
```

The `return true` for out-of-range payloads matters because the WS handler at line 521 only sends an ack when `drawPosition(msg)` returns truthy, and we want the broadcaster's diff layer to consider the message delivered.

- [ ] **Step 4: Modify `removePosition` to clear the buffer**

Replace the existing function (lines 397-405) with:

```js
  function removePosition(key) {
    const entry = drawnPositions.get(key);
    drawnPositions.delete(key);
    positionAnchors.delete(key);
    closedTradePayloads.delete(key);
    const shapeId = entry && entry.shapeId;
    if (shapeId != null && chart) {
      try { chart.removeEntity(shapeId); } catch (_) {}
    }
  }
```

Important: `removePosition` is called from two places — the `position_remove` WS handler (line 522, where we DO want the buffer cleared because the broadcaster has dropped this trade from its window) AND from `drawPosition`'s out-of-range branch above (where we DON'T want the buffer cleared). The fix: in the out-of-range branch, inline only the shape-removal logic instead of calling `removePosition`. Update Step 3's out-of-range branch to:

```js
    // Out of range: ensure no stale shape lingers from a prior in-range emit.
    const existing = drawnPositions.get(p.key);
    if (existing && existing.shapeId != null) {
      try { chart.removeEntity(existing.shapeId); } catch (_) {}
      drawnPositions.delete(p.key);
      positionAnchors.delete(p.key);
    }
    return true;
```

Apply this corrected branch — do not leave the `if (drawnPositions.has(p.key)) removePosition(p.key);` line from Step 3 in place.

- [ ] **Step 5: Smoke check syntax with a local lint**

Run: `node --check arnold/tv_overlay/userscript/arnold-overlay.user.js`
Expected: no output (file parses).

- [ ] **Step 6: Commit**

```bash
git add arnold/tv_overlay/userscript/arnold-overlay.user.js
git commit -m "feat(tv-overlay): buffer closed trades + filter against visible range on receive"
```

---

## Task 2: Subscribe to visible-range changes + reconcile

**Files:**
- Modify: `arnold/tv_overlay/userscript/arnold-overlay.user.js` (around lines 547-555)

- [ ] **Step 1: Add the reconcile function**

Below the helper block from Task 1 (right after `_currentVisibleRange`), add:

```js
  // Walk the closed-trade buffer and bring drawn shapes in sync with the
  // current visible range: draw newly-overlapping ones, remove newly-off-
  // range ones. Active trades are untouched (their shape lives in
  // drawnPositions under key 'trade:active' but isn't in
  // closedTradePayloads, so this loop never sees them).
  function reconcileClosedTradeVisibility() {
    if (!chart) return;
    const range = _currentVisibleRange();
    for (const [key, p] of closedTradePayloads) {
      const overlap = _closedTradeOverlapsRange(p, range);
      const isDrawn = drawnPositions.has(key);
      if (overlap && !isDrawn) {
        const fillEpoch = (typeof p.entry_time === 'number') ? Math.floor(p.entry_time) : null;
        const now = Math.floor(Date.now() / 1000);
        const anchor = fillEpoch != null ? fillEpoch : now;
        let endEpoch = (typeof p.end_time === 'number') ? Math.floor(p.end_time) : null;
        if (endEpoch == null || endEpoch <= anchor) endEpoch = anchor + 60;
        _drawClosedRect(p, anchor, endEpoch);
      } else if (!overlap && isDrawn) {
        const existing = drawnPositions.get(key);
        if (existing && existing.shapeId != null) {
          try { chart.removeEntity(existing.shapeId); } catch (_) {}
        }
        drawnPositions.delete(key);
        positionAnchors.delete(key);
      }
    }
  }

  // Trailing-edge debounce — coalesces drag/scroll/zoom storms into one
  // reconcile pass. 200 ms keeps the chart responsive without spamming
  // shape redraws.
  let _rangeChangeTimer = null;
  function _scheduleReconcile() {
    if (_rangeChangeTimer != null) clearTimeout(_rangeChangeTimer);
    _rangeChangeTimer = setTimeout(() => {
      _rangeChangeTimer = null;
      try { reconcileClosedTradeVisibility(); }
      catch (e) { sendError(`reconcileClosedTradeVisibility failed: ${e instanceof Error ? e.message : String(e)}`); }
    }, 200);
  }
```

- [ ] **Step 2: Subscribe inside the boot block**

Find the boot block at the end of the file (lines 548-555 starting with `attachPromise.then((c) => {`) and modify it:

```js
  // --- Boot ---
  attachPromise.then((c) => {
    if (!c) {
      console.warn('[arnold-overlay] could not find TradingView chart object — overlay disabled');
      return;
    }
    console.log('[arnold-overlay] attached to chart', c);
    // Reconcile closed-trade visibility on scroll/zoom. Wrapped in
    // try/catch in case onVisibleRangeChanged is unavailable on this TV
    // build — we still get correct first-paint behavior from the
    // drawPosition filter, just no auto-redraw on scroll.
    try {
      if (typeof c.onVisibleRangeChanged === 'function') {
        c.onVisibleRangeChanged().subscribe(null, _scheduleReconcile);
      } else {
        console.warn('[arnold-overlay] chart.onVisibleRangeChanged unavailable — closed-trade auto-redraw disabled');
      }
    } catch (e) {
      console.warn('[arnold-overlay] visible-range subscribe failed:', e);
    }
    connect();
  });
```

- [ ] **Step 3: Smoke check syntax**

Run: `node --check arnold/tv_overlay/userscript/arnold-overlay.user.js`
Expected: no output.

- [ ] **Step 4: Commit**

```bash
git add arnold/tv_overlay/userscript/arnold-overlay.user.js
git commit -m "feat(tv-overlay): redraw closed trades on visible-range change (debounced 200ms)"
```

---

## Task 3: Bump version + manual verification on TradingView

**Files:**
- Modify: `arnold/tv_overlay/userscript/arnold-overlay.user.js` (lines 4-5)

- [ ] **Step 1: Bump `@version` and update the description**

Replace lines 4-5 of the userscript header:

```js
// @version      0.5.0
// @description  Closed-trade rectangles are filtered against TV's visible range — off-screen trades no longer pile up at the chart's left edge. Auto-redraw on scroll/zoom (debounced 200ms). Active trade rendering unchanged.
```

The minor-version bump (0.4.4 → 0.5.0) reflects a behavior change visible to the user, not just a bug fix.

- [ ] **Step 2: Make sure the local server is serving the updated file**

If `arnold.bat` is already running, Tampermonkey will pick up the new file on next page load. If not, start it:

```bash
arnold.bat
```

Confirm with: `curl -s http://127.0.0.1:8000/stocks/api/tv-overlay/userscript | head -5`
Expected: first lines should include `@version      0.5.0`.

- [ ] **Step 3: Force Tampermonkey to refetch**

In Tampermonkey dashboard → Arnold TradingView Overlay → Last updated → "Check for updates". Confirm the version shows 0.5.0. Hard-reload the TradingView NQ tab (Ctrl+F5).

Open DevTools console on the TV tab. Look for:
- `[arnold-overlay] attached to chart` — script booted.
- No `chart.onVisibleRangeChanged unavailable` warning.

- [ ] **Step 4: Verify pile-up is gone (default view)**

Open NQ 1m chart, scroll to the right edge ("now"). Confirm:
- Closed trades that fall within the visible window draw at their real entry→exit timestamps.
- No vertical stack of rectangles at the chart's left edge.

- [ ] **Step 5: Verify scroll-back behavior**

Drag the chart left until you see bars from earlier today / yesterday. Confirm:
- Closed trades from that older window appear at their correct entry→exit positions.
- No left-edge pile in the new view either.
- Rectangles are styled correctly (red losses / green wins, P&L label intact).

- [ ] **Step 6: Verify scroll-forward cleanup**

Drag back to "now". Confirm:
- Yesterday's rectangles are no longer drawn (they were undrawn cleanly).
- No leftover ghost shapes on the chart.

- [ ] **Step 7: Verify active trade still works**

If a position is open (or via the next signal that fires), confirm:
- The active `long_position` / `short_position` shape still renders with R:R bands and live Open P&L.
- It draws regardless of whether you've scrolled away (active uses the right-edge anchor — entry should be near the live bar).

- [ ] **Step 8: Verify reload recovery**

Hard-reload the TV tab (Ctrl+F5). Within 30 s (next poll cycle), confirm:
- All in-range closed trades reappear.
- Buffer is rebuilt from the broadcaster's full re-emit.

- [ ] **Step 9: Commit**

```bash
git add arnold/tv_overlay/userscript/arnold-overlay.user.js
git commit -m "chore(tv-overlay): bump userscript to 0.5.0 (visible-range filter)"
```

---

## Self-Review

**Spec coverage:**
- Buffer (`closedTradePayloads`) — Task 1 Step 1. ✓
- Overlap helper (`_closedTradeOverlapsRange`) — Task 1 Step 2. ✓
- `position_upsert` filter on receive — Task 1 Step 3. ✓
- Buffer cleanup on `position_remove` — Task 1 Step 4. ✓
- Visible-range subscription — Task 2 Step 2. ✓
- Debounced reconcile — Task 2 Step 1. ✓
- Active trade unchanged — Task 1 Step 3 keeps the `isActive` early-return path identical. ✓
- Manual test cases (today, scroll back, scroll forward, active, reload) — Task 3 Steps 4-8. ✓
- Fail-open on missing range — Task 1 Step 2 helper returns true. ✓
- Defensive try/catch on `onVisibleRangeChanged` — Task 2 Step 2. ✓

**No placeholders:** every step has the actual code or exact command.

**Type / name consistency:**
- `closedTradePayloads` (Map) — used in Task 1 Steps 1, 3, 4 and Task 2 Step 1. Consistent.
- `_closedTradeOverlapsRange` — defined in Task 1 Step 2, called in Task 1 Step 3 and Task 2 Step 1. Consistent.
- `_currentVisibleRange` — defined in Task 1 Step 2, called in Task 1 Step 3 and Task 2 Step 1. Consistent.
- `reconcileClosedTradeVisibility` / `_scheduleReconcile` — defined in Task 2 Step 1, subscribed in Task 2 Step 2. Consistent.
- `_drawClosedRect(p, anchor, endEpoch)` — existing function, called with same signature in Task 2 Step 1.

**Open issue caught and fixed inline:** initial Step 4 of Task 1 had `removePosition` called from two paths, one of which would wrongly clear the buffer. Step 4 now inlines the shape-only cleanup in the out-of-range branch and reserves `removePosition` (which clears the buffer) for the `position_remove` WS path.
