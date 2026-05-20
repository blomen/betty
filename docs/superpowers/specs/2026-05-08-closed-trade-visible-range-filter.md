# Closed-trade visible-range filter (TV overlay)

**Date:** 2026-05-08
**Surface:** `arnold/tv_overlay/userscript/arnold-overlay.user.js`
**Status:** design approved, plan pending

## Problem

Closed-trade rectangles pile up vertically at the chart's left edge (see user
screenshot, 2026-05-08). Root cause: `chart.createMultipointShape` clamps any
point whose timestamp predates TV's leftmost loaded bar to that bar, so every
out-of-range closed trade renders at the same X anchor.

The poller already restricts to `days=1`
([arnold/stocks_runtime.py:236-270](../../../arnold/stocks_runtime.py#L236-L270)),
and the broadcaster docstring at
[arnold/tv_overlay/broadcaster.py:195-199](../../../arnold/tv_overlay/broadcaster.py#L195-L199)
explicitly acknowledges this clamp. Tightening the window further is a guess
and breaks "scroll back to review yesterday" use cases.

## Goal

Filter closed-trade drawing against TV's visible range so off-screen trades
are not drawn (and therefore not clamped). Re-evaluate on scroll/zoom so
trades auto-appear when their time window comes into view, and auto-undraw
when scrolled away.

## Non-goals

- **Active trade.** `key === 'trade:active'` is always "now"-anchored on the
  right edge; clamping is not a real concern. Active rendering path stays
  exactly as today.
- **Widening the poller window.** `days=1` stays. Stats tab remains the
  authoritative surface for older trades.
- **Querying loaded range directly.** TV's widget API does not expose a clean
  "leftmost loaded bar" signal. Visible-range is a safe subset — anything
  visible is loaded — and is what `IChartWidgetApi` does expose.
- **Server-side changes.** Broadcaster keeps emitting every closed trade in
  the 24h window. The filter is purely in the userscript.

## Architecture

All changes in
[arnold/tv_overlay/userscript/arnold-overlay.user.js](../../../arnold/tv_overlay/userscript/arnold-overlay.user.js).

### New state

```js
// Every closed-trade payload received from the WS, keyed by payload.key.
// Never evicted — entries persist across visible-range changes so we can
// re-draw a previously-undrawn trade when the user scrolls to it.
const closedTradePayloads = new Map(); // key → payload
```

The existing `drawnPositions` map continues to track shape lifecycle for
trades that are currently rendered.

### New helper

```js
function _closedTradeOverlapsRange(payload, range) {
  // range = { from: epochSec, to: epochSec }
  // payload.entry_time and payload.end_time are both epoch seconds.
  if (!range || range.from == null || range.to == null) return true; // fail open
  const start = Number(payload.entry_time);
  const end = Number(payload.end_time);
  if (!Number.isFinite(start) || !Number.isFinite(end)) return false;
  // Overlap test: NOT (entirely before from) AND NOT (entirely after to).
  return !(end < range.from || start > range.to);
}
```

Fail-open behavior on missing range guards the brief window between WS
connect and `onChartReady`.

### Modified `position_upsert` handling

In the existing `drawPosition(p)` function (or its caller — wherever the
closed-vs-active branch lives):

1. If `p.key === 'trade:active'` (or `'pos:current'`) → unchanged path,
   `_drawActiveShape`. Return.
2. Else (closed):
   a. `closedTradePayloads.set(p.key, p)` — always, keeps buffer fresh.
   b. Compute `range = chart.getVisibleRange()`.
   c. If `_closedTradeOverlapsRange(p, range)` → `_drawClosedRect(p, …)`.
   d. Else → `removePosition(p.key)` (idempotent if not currently drawn).

### New visible-range subscription

Inside the `onChartReady` callback (after `chart` is established):

```js
let _rangeChangeTimer = null;
chart.onVisibleRangeChanged().subscribe(null, () => {
  if (_rangeChangeTimer != null) clearTimeout(_rangeChangeTimer);
  _rangeChangeTimer = setTimeout(_reconcileClosedTradeVisibility, 200);
});
```

`_reconcileClosedTradeVisibility` walks `closedTradePayloads`:
- For each payload: if overlap with current visible range AND not in
  `drawnPositions` → draw it. If no overlap AND in `drawnPositions` →
  `removePosition(key)`. Otherwise no-op.

200 ms trailing-edge debounce coalesces drag/scroll/zoom storms into one
reconcile pass.

### Removal of stale buffer entries

Closed trades never disappear from the broker's `days=1` window mid-session
in practice (the only churn is the active trade closing, which arrives via
the active→closed transition). To keep the buffer bounded long-term, on each
fresh poll cycle the broadcaster's existing diff layer would re-emit any
trade whose payload changed; the userscript simply overwrites the buffer
entry. We do **not** add explicit eviction — the buffer's worst-case size is
24 h × ~30 trades/day = ~30 entries, negligible.

If ever needed: `position_remove` already exists (broadcaster sends it for
trades that drop out of the window). Existing `removePosition` should also
delete from `closedTradePayloads`.

## Data flow (closed trades only)

```
broker_trades (server DB, 7d retained)
        │
        ▼  GET /api/stocks/broker-trades?days=1   (every 30s)
arnold/stocks_runtime.py _passive_trades_poller
        │
        ▼  dash_state["trades"]
arnold/tv_overlay/broadcaster.py reconcile_trades
        │  diff against self._trades (per-key)
        ▼  position_upsert WS message
userscript (ws handler)
        │
        ▼  closedTradePayloads.set(key, payload)        ← NEW
        ▼  if _closedTradeOverlapsRange(...) draw       ← NEW filter
                                  else removePosition()

chart.onVisibleRangeChanged (debounced 200ms)            ← NEW
        ▼  walk closedTradePayloads
        ▼  draw / undraw based on overlap
```

## Testing

Manual on TradingView with the local userscript installed:

1. **Today only:** open NQ 1m chart, scroll to "now". Confirm only trades
   whose time falls within the visible range render. No left-edge pile.
2. **Scroll back:** drag the chart left past yesterday's bars. Confirm
   yesterday's trades appear in their real `entry_time → end_time`
   positions, not stacked.
3. **Scroll forward:** drag back to "now". Confirm yesterday's rectangles
   are removed (no leftover shapes lingering off-screen).
4. **Active trade:** while a position is open, confirm it renders as today
   regardless of visible range (entry on right edge of chart).
5. **Fresh fill mid-session:** trigger a new closed trade and verify it
   draws immediately if visible, gets buffered if you've scrolled away.
6. **Reload chart:** F5 the TradingView tab. Confirm the buffer rebuilds
   from the next 30 s poll cycle and trades draw against the now-visible
   range correctly.

## Risks and rollback

- **Pure userscript change.** Ships via Tampermonkey auto-update from
  `http://127.0.0.1:8000/stocks/api/tv-overlay/userscript` (`@updateURL`).
- **Rollback:** revert the file, bump `@version` down, reload TV tab.
- **Failure modes:** if `chart.onVisibleRangeChanged` is unavailable on a
  given TV build, fall back to `setInterval(_reconcile…, 2000)` — slightly
  laggier but functionally equivalent. Defensive try/catch around the
  subscribe call.
- **Performance:** worst case 30 closed trades × 1 overlap test per range
  change. Negligible. Drawing is the cost — and we draw fewer shapes than
  before, not more.

## Out of scope for this spec

- Cosmetic changes to closed-trade rectangle styling (colors, labels).
- Splitting active-trade rendering between userscript and extension —
  current split (active in extension, closed in userscript) is fine.
- Server-side broker_trades retention or schema changes.
