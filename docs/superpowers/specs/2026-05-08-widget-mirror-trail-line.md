# Widget mirror — original stop band + trail line

**Date:** 2026-05-08
**Surfaces:** `arnold/tv_overlay/extension/page.js`, broadcaster, server runtime + persist + DB schema
**Status:** design approved, plan pending

## Problem

The TradingView active and closed `long_position` / `short_position` widgets do not reliably reflect the model's stop-management state.

- **Closed-trade widget** (`_drawClosedPositionWidget`,
  [page.js:1014](../../../arnold/tv_overlay/extension/page.js#L1014)) renders
  `stopLevel` from `broker_trades.stop_price`, which the persist callback at
  [broker_adapter.py:763-765](../../../backend/src/stocks/broker_adapter.py#L763-L765)
  intentionally recomputes from the entry-time `stop_ticks` ("set at signal
  time, immutable through trail walks"). The actually-placed stop after
  BE-lock + cont-trail walks is **not persisted anywhere**. So a 2R-winner
  whose stop was BE-locked to entry+2 ticks still renders a red 1R band
  below entry — visually indistinguishable from a trade where the stop was
  never moved.

- **Active-trade widget** (`_drawActivePositionShape`,
  [page.js:838](../../../arnold/tv_overlay/extension/page.js#L838)) does
  partial right thing: when the live `tracker.stop_price` crosses into
  profit it zeros out `stopLevel` and draws a horizontal line at the
  trailed stop via `_syncTrailLine`. But the original red band collapses
  and TV's auto R:R label disappears mid-trade — visually jarring, and
  loses the planned-1R-risk reference.

## Goal

Both widgets render the same way throughout the trade lifecycle:

1. The **original entry-time stop band** is always visible, so TV's
   built-in R:R label reads "2" the whole time (planned setup never
   changes).
2. A **trail line** is drawn at the actually-placed stop iff it differs
   from the original by ≥ 1 tick. Walks naturally as BE-lock fires and
   subsequent cont-trail signals move the stop further into profit.

Visual semantics match what the user already understands: "this is the
planned trade (red band + green target = R:R 2:1); this line is where the
stop actually sits right now / sat at exit."

## Non-goals

- **Userscript closed-rectangle path** (`_drawClosedRect` in
  `arnold-overlay.user.js`). Renders as a simple time-bounded rectangle,
  no stop bands at all — nothing to change.
- **TP advance visualization.** Already works: `pending["tp_price"]`
  mutates → `runtime-status` → widget's `profitLevel` redraws.
- **Multi-step trail history.** Only the final/current placed stop is
  rendered as one line. Intermediate trail walks aren't preserved in the
  closed-trade rendering.
- **R:R label customization.** Keep TV's built-in label.
- **Backwards-compat schema games.** Legacy `broker_trades` rows with
  NULL `final_stop_price` simply render no trail line (correct — we don't
  have the data for them).

## Architecture

### Layer 1 — Server-side persistence

**Schema add.** New column `broker_trades.final_stop_price FLOAT NULL`.
Migration via the existing `models._run_pg_migrations` ALTER TABLE pattern
(no Alembic). Forward-compatible: NULL on legacy rows.

**Persist callback.** Both code paths that call `_log_broker_trade(...)`
([broker_adapter.py:805](../../../backend/src/stocks/broker_adapter.py#L805)
and the normal-exit path elsewhere in the file) pass
`final_stop_price=self.tracker.stop_price`. The tracker's
`stop_price` already reflects BE-lock and cont-trail moves — `modify_stop`
mirrors every walk into `tracker.stop_price` and `_pending_trade["stop_price"]`.

### Layer 2 — Server endpoints

**`GET /api/stocks/runtime-status`** ([stocks.py:128-180](../../../backend/src/api/routes/stocks.py#L128-L180))
adds `position.original_stop_price`. Derived from
`pending["stop_ticks"] * 0.25` and the entry price:

```python
original_stop = None
stop_ticks = pending.get("stop_ticks")
if stop_ticks and entry > 0:
    direction = -1 if tracker.side == "long" else 1
    original_stop = _round_tick(entry + direction * stop_ticks * 0.25)
```

`position.stop_price` keeps its current meaning (= `tracker.stop_price`,
the live placed stop).

**`GET /api/stocks/broker-trades`** ([stocks.py:514](../../../backend/src/api/routes/stocks.py#L514))
includes the new `final_stop_price` field on each trade row.

### Layer 3 — Local pollers

**`_passive_position_poller`** ([stocks_runtime.py:155-228](../../../arnold/stocks_runtime.py#L155-L228))
reads `position.original_stop_price` and stashes it on the synthesized
position dict. The `_TrackerShim` does not need to carry it — the broadcaster
will read it directly from the position dict.

**`_passive_trades_poller`** ([stocks_runtime.py:236-270](../../../arnold/stocks_runtime.py#L236-L270))
needs no change. The JSON passthrough already forwards every column the
endpoint returns; once `final_stop_price` is in the API response, it lands
on `dash_state["trades"][i]["final_stop_price"]` automatically.

### Layer 4 — Broadcaster payload

Unified field naming so the widget logic is symmetric across active and closed:

- `original_stop_price` — entry-time, R:R basis (the band).
- `placed_stop_price` — currently-placed (active) or at-exit (closed).
  Optional / nullable.

**`reconcile_trades`** ([broadcaster.py:168](../../../arnold/tv_overlay/broadcaster.py#L168))
adds these fields to each emitted closed-trade `position_upsert`:

```python
"original_stop_price": t.get("stop_price"),
"placed_stop_price":   t.get("final_stop_price"),
```

**Active-trade synthesis** ([broadcaster.py:498-512](../../../arnold/tv_overlay/broadcaster.py#L498-L512))
adds:

```python
"original_stop_price": first.get("original_stop_price") or model_status.get("stop_price"),
"placed_stop_price":   model_status.get("stop_price"),
```

The fall-through (`first.get("original_stop_price") or model_status.get("stop_price")`)
keeps a fresh local client gracefully degrading to today's behavior if it
hits a server that hasn't deployed the new field yet — `original_stop_price`
== `placed_stop_price` means no trail line and the widget renders exactly
as today.

The existing `stop_price` field in the payload stays present for one
release cycle, set equal to `placed_stop_price`, then deleted in a
follow-up. This avoids breaking any other consumer that grew up reading
`p.stop_price`.

### Layer 5 — Widget rendering ([page.js](../../../arnold/tv_overlay/extension/page.js))

**Shared helper.** Extract the existing `_syncTrailLine`
([page.js:929-986](../../../arnold/tv_overlay/extension/page.js#L929-L986))
into a reusable `_drawTrailLineIfMoved(p, anchor, endEpoch, originalStop, placedStop)`:
- If `Math.abs(originalStop - placedStop) < NQ_TICK` → no line; remove any
  prior line for this position key.
- Otherwise draw a horizontal_line at `placedStop`, color green if the
  stop is in profit (`isLong ? placedStop > entry : placedStop < entry`),
  amber otherwise.
- Line is keyed in `drawnLevels` (or a similar registry) by
  `${p.key}:trail` so active and closed each get their own line and
  `removePosition` cleans up alongside the main shape.

**`_drawActivePositionShape`** ([page.js:838-925](../../../arnold/tv_overlay/extension/page.js#L838-L925)):
- **Drop the `stopInProfit ? 0 : …` branch** at line 856.
- `stopOffsetTicks` always = `Math.round(Math.abs(p.original_stop_price - p.entry) / NQ_TICK)`.
- Replace `_syncTrailLine(...)` calls (lines 892, 919) with
  `_drawTrailLineIfMoved(p, anchor, endEpoch, p.original_stop_price, p.placed_stop_price)`.

**`_drawClosedPositionWidget`** ([page.js:1014-1110](../../../arnold/tv_overlay/extension/page.js#L1014-L1110)):
- `stopLevel` continues to derive from `p.original_stop_price` (= the
  current `p.stop` for closed trades). No change to existing band geometry.
- After the shape is created/mutated, call
  `_drawTrailLineIfMoved(p, anchor, endEpoch, p.original_stop_price, p.placed_stop_price)`.
  Closed trades pass `endEpoch = exit_time` so the line is bounded to the
  trade's actual time window.

## Data flow

### Closed trade

```
broker_adapter._log_broker_trade
        │  final_stop_price = tracker.stop_price  (NEW)
        ▼
broker_trades row
        │
        ▼ /api/stocks/broker-trades  (server returns final_stop_price)
arnold/stocks_runtime._passive_trades_poller (passthrough)
        │
        ▼ dash_state["trades"][i].final_stop_price
arnold/tv_overlay/broadcaster.reconcile_trades
        │  emits  original_stop_price = stop_price
        │         placed_stop_price = final_stop_price
        ▼ position_upsert WS
extension/page.js _drawClosedPositionWidget
        │  stopLevel = abs(entry - original_stop_price) / NQ_TICK
        │  _drawTrailLineIfMoved(original, placed)
        ▼
TradingView chart: original red band + green target band + optional trail line
```

### Active trade

```
broker_adapter
        │  tracker.stop_price walks via modify_stop
        │  _pending_trade["stop_ticks"] is set at entry
        ▼
/api/stocks/runtime-status  (server exposes both)
        │  position.stop_price          = tracker.stop_price (live)
        │  position.original_stop_price = entry ± stop_ticks * 0.25  (NEW)
        ▼
arnold/stocks_runtime._passive_position_poller
        │  dash_state["positions"][0].{stop_price,original_stop_price}
        ▼
broadcaster active-trade synthesis
        │  emits  original_stop_price + placed_stop_price
        ▼ position_upsert WS
extension/page.js _drawActivePositionShape
        │  stopLevel = abs(entry - original_stop_price) / NQ_TICK
        │  _drawTrailLineIfMoved(original, placed)
        ▼
TradingView chart: original band visible throughout + trail line walks as stop walks
```

## Testing

Manual on TradingView with the extension installed (no automated harness):

1. **Active pre-2R.** Open a position. Confirm: original red 1R band, green
   2R target band, R:R label = "2", **no** trail line yet.
2. **Active at +2R cross.** When BE-lock fires server-side, confirm:
   original red band still visible, R:R still "2", **new horizontal line**
   at entry±2 ticks, colored green (in profit).
3. **Active at cont-signal next-zone trail.** Trail line walks up (long) /
   down (short) to the previously-broken zone's edge. Original band
   unchanged.
4. **Trade closes (TP winner).** Widget transitions to closed-shape rendering.
   Original red 1R band still visible, trail line still drawn at the
   BE-locked / final stop. R:R = "2".
5. **Closed -1R stop-out.** No trail line drawn (`placed_stop_price ==
   original_stop_price` because BE-lock never fired).
6. **Closed legacy row** (`final_stop_price = NULL` from before the
   migration). Renders exactly as today — no trail line. No regression.
7. **Active widget reload mid-trade** (Ctrl+F5 the TV tab while a position
   is open). Confirm trail line redraws on next poll cycle, no orphan
   shapes.

## Risks and rollback

- **Backend deploy required** for layers 1–3. Per `CLAUDE.md` deploy rules
  this is `bash /opt/arnold/scripts/server-deploy.sh rebuild backend` with
  the open-position gate. Use `ALLOW_OPEN_POSITION_DEPLOY=1` only if a
  position is open and the deploy is genuinely time-critical (it isn't —
  this is cosmetic).
- **Schema migration is non-destructive.** Adding a nullable column is
  forward-compatible; reverting the code without dropping the column is
  safe.
- **Extension change ships independently** of the backend deploy via
  Chrome extension reload. The widget gracefully renders no trail line
  when the new payload fields are missing, so a partial rollout (extension
  updated but backend not yet deployed) keeps today's behavior.
- **Rollback:** revert page.js + broadcaster + persist callback + endpoint
  changes; redeploy. The column can stay (or be dropped at leisure).

## Out of scope

- Userscript closed-rectangle rendering.
- TP advance line / chart-spanning TP visualization.
- Pyramid-add markers (separate feature).
- Multi-step trail history rendering.
- Database backfill of `final_stop_price` for historical rows.
