# TradingView API Audit — Arnold Overlay

> Probed live against the in-mirror Playwright TV instance (build that mounts `window.TradingViewApi.activeChart()`). All findings empirically verified, not from docs. Date: 2026-04-28.

## How to extend this audit

Run JS via `POST /mirror/browser/tv-eval` with `{"js": "(async()=>{ ... })()"}`. The endpoint targets the open TradingView tab in the local arnold mirror and returns the JSON-stringified result. Use this whenever you need a new probe.

## 1. Chart object

`window.TradingViewApi.activeChart()` is the entry point. Returns the chart instance with these usable methods (full list cached above; here are the ones we care about):

| Method | What it does | Notes |
|--------|--------------|-------|
| `createMultipointShape(points, opts)` | Create most drawings | Returns `Promise<entityId>` or `entityId`. Wrap in `_resolve()`. |
| `createShape(point, opts)` | Single-point drawings | Same return semantics. |
| `createAnchoredShape(point, opts)` | Anchored drawings (like anchored VWAP) | Restricted to specific anchored shape names. |
| `createStudy(name, forceOverlay, lock, inputs, overrides)` | Add an indicator | Display name as `name`. Most fail with `unexpected study id`; only certain names are accepted. |
| `createPositionLine()` | Async — returns clickable position widget | Has `onClose`/`onReverse`/`onModify` callbacks. |
| `createOrderLine()` | Async — clickable order widget | Same family. |
| `createExecutionShape()` | Async — execution marker | Available but untested. |
| `getShapeById(id)` | Returns live shape object | Has `getProperties()`, `setProperties()`, `setPoints()`, `getPoints()`. |
| `getStudyById(id)` | Returns live study object | Has `getInputsInfo()`, `getInputValues()`, `setInputValues()`, `applyOverrides()`. |
| `getAllShapes()` | Array of `{id, name}` | Used by cleanupStaleShapes. |
| `getAllStudies()` | Array of `{id, name}` | |
| `removeEntity(id)` | Remove shape OR study by id | Safe; throws → catchable. |
| `removeAllShapes()` / `removeAllStudies()` | Nuke. | Used by force_cleanup. |
| `getVisibleRange()` | `{from, to}` epoch sec | Useful for clipping zone time bounds. |
| `getVisiblePriceRange()` | `{from, to}` price | Useful for off-screen filtering. |
| `getPriceToBarRatio()` | px-to-price ratio | Needed for sizing brushes/highlighters by band height. |
| `symbol()` / `resolution()` | Current symbol & timeframe | |
| `setSymbol(s)` / `setResolution(r)` | Change them | |

## 2. Drawing shapes — required points + properties

Every `createMultipointShape` call receives `{shape, disableSave?, lock?, text?, overrides?}`. TV enforces the exact point count per shape. Confirmed point counts and override property keys:

### Single-point shapes (1 pt)
- `horizontal_line` — `linecolor, linewidth, linestyle, showPrice, textcolor, fontsize, bold, italic, horzLabelsAlign, vertLabelsAlign, visible, frozen, text`
- `horizontal_ray` — same as above + `showLabel`
- `vertical_line` — TBD (1 pt)
- `price_label` — `color, backgroundColor, borderColor, fontWeight, fontsize, transparency`
- `text` / `note` / `signpost` / `comment`
- `flag_mark` — `flagColor`
- `arrow_mark` — `flagColor`

### Two-point shapes (2 pt)
- `trend_line` / `arrow` — full label/stat suite: `linecolor, linewidth, linestyle, extendLeft, extendRight, showPriceRange, showPercentPriceRange, showPipsPriceRange, showBarsRange, showDateTimeRange, showDistance, showAngle, showMiddlePoint, alwaysShowStats, statsPosition, text` + style.
- `ray` — like trend_line minus 2nd point semantics
- `fib_retracement` — full level1..level24 + `extendLines, extendLinesLeft, fillBackground, transparency, reverse, levelsStyle`. Levels colored individually.
- `fib_circles` / `fib_speed_resistance_arcs` etc.
- `price_range` — `extendLeft, extendRight, showPriceRange, showPercentPriceRange, showPipsPriceRange, fillBackground, backgroundTransparency, customText`
- `date_range` — `extendTop, extendBottom, showBarsRange, showDateTimeRange`
- `date_and_price_range` — superset: `showBarsRange, showDateTimeRange, showPriceRange, showPercentPriceRange, showPipsPriceRange, drawBorder, borderWidth, fillBackground, backgroundTransparency, customText`. **Best alternative to plain rectangles for time-bounded zones.**
- `circle` / `curve` / `callout` — drawn between 2 anchors

### Three-point shapes (3 pt)
- `parallel_channel` — `level1..level7, labelTextColor, backgroundColor, fillBackground, transparency, labelText` + extends
- `rotated_rectangle` — `color, fillBackground, backgroundColor, transparency, linewidth`
- `triangle`
- `pitchfork` / `schiff_pitchfork`
- `arc`

### Variable-point freehand
- `highlighter` — `linecolor (rgba), transparency (0-100), smooth, width (px)`. **Currently used for zones.**
- `brush` — `linecolor, backgroundColor, transparency, smooth, fillBackground, linewidth, leftEnd, rightEnd`. Stroke + fill. More expressive than highlighter for "painted band" looks.
- `polyline` / `path` — multi-point line shapes

### Position shapes (1 OR 2 pt)
- `long_position` / `short_position` — point #1 = entry, point #2 (optional) = right edge bound. Properties: `stopLevel, profitLevel, stopBackground, profitBackground, stopBackgroundTransparency, profitBackgroundTransparency, riskDisplayMode, accountSize, lotSize, risk, leverage, alwaysShowStats, showPriceLabels, qtyPrecision, currency, infoBlocks, linecolor, textcolor, linewidth, fontsize`. **`infoBlocks`** is a deeply-nested object controlling each labeled stat (tpPriceOffset, tpPercentOffset, tpTickOffset, tpAmount, tpPL, openClosePL, qty, riskRewardRatio, slPriceOffset, etc.) — each with `{visible: bool}`. Used for trades.

  > **CRITICAL UNIT GOTCHA**: `stopLevel` and `profitLevel` are **TICK OFFSETS** from the entry point, **not absolute prices**. Pass the absolute number of ticks between entry and stop/target. NQ tick = 0.25, so `stopOffsetTicks = round(abs(stopPrice - entryPrice) / 0.25)`. The TV input panel labels the field "Ticks" — the "Price" field next to it is computed: for a SHORT, displayed_price = entry + ticks × tick_size; for a LONG, displayed_price = entry − ticks × tick_size. Passing 27349 (a price) to a SHORT at entry 27342.5 placed the stop at 34179.75 (= 27342.5 + 27349 × 0.25). Always convert prices → tick offsets before passing.

### Pattern shapes (rejected with required N points)
- `head_and_shoulders` (7 pt), `elliott_impulse_wave` (6), `elliott_triangle_wave` (6), `elliott_double_combo` (4), `elliott_triple_combo` (6), `cypher_pattern` (5), `abcd_pattern` (4), `xabcd_pattern` (5)

### Restricted (interactive-only)
- Anchored Volume Profile — see studies section.

## 3. Studies — what works programmatically

`createStudy(name, forceOverlay, lock, inputs, overrides)` looks up by **display name**. Most VP/VbP names are rejected with `unexpected study id`. What works on this build:

| Display name | Works? | Notes |
|--------------|--------|-------|
| `Anchored VWAP` | YES | Default anchor (no time input visible in inputsInfo); supports `Bands Calculation Mode`, multipliers, source. Could use this for zone confirmation. |
| `Anchored Volume Profile` | NO | Throws `Passed color string does not match any of the known color representations` from inside `Qr.getColor` regardless of inputs. Interactive-only. |
| `Volume Weighted Average Price` | NO | Pre-existing study on the chart had this name; createStudy rejects it. The Pine source is encrypted (visible as `bmI9Ks46_…` in `getInputValues`). |
| `Auto Anchored VWAP` | NO | Rejected. |
| `VbP *` (any volume profile variant) | NO | All rejected. |
| `Pivot Points Standard`, `Linear Regression`, `Bollinger Bands`, `Moving Average` | NO | All threw `undefined`. The lookup is name-sensitive and not by display name. |

Studies that already exist on the chart can still be inspected: `getAllStudies()` → `getStudyById(id)` → `getInputsInfo() / getInputValues() / setInputValues()`. So once a user has manually added a study, we can read & mutate it.

## 4. Position / Order widgets

`createPositionLine()` and `createOrderLine()` return Promises resolving to widget objects with rich APIs.

### PositionLine methods (high-value subset)
- `setPrice(p)`, `setQuantity(s)`, `setText(t)`, `setTooltip(t)`
- `setDirection('long'|'short')`, `setProfitState(true|false)`
- `setLineColor`, `setLineBuyColor`, `setLineSellColor`, `setLineStyle`, `setLineWidth`
- `setBodyBackgroundColor`, `setBodyBorderColor`, `setBodyTextColor`, `setBodyTextPositiveColor`, `setBodyTextNegativeColor`, `setBodyTextNeutralColor`, `setBodyFont`
- `setQuantityBackgroundColor`, `setQuantityTextColor`, `setQuantityFont`
- `setReverseButtonBorderColor`, `setReverseButtonBackgroundColor`, `setReverseButtonIconColor`, `onReverse(callback)`
- `setCloseButtonBorderColor`, `setCloseButtonBackgroundColor`, `setCloseButtonIconColor`, `onClose(callback)`, `setCloseEnabled(true|false)`, `isCloseEnabled()`
- `onModify(callback)`, `onContextMenu(callback)`, `setProtectTooltip`, `setCloseTooltip`, `setReverseTooltip`
- `block()` / `unblock()` (interaction-toggle)
- `remove()`

### OrderLine methods
- `setPrice`, `setQuantity`, `setText`, `setTooltip`, `setMode`, `setDirection`, `setActive`, `setEditable`, `setCancellable`
- `onMove(callback)` (drag-to-move with `setEditable(true)`)
- `onModify(callback)`, `onCancel(callback)`
- All the same body/quantity/style setters
- `remove()`

### Interaction wiring opportunities
- `positionLine.onClose(cb)` could wire to `POST /api/stocks/halt?flatten=true` — TV's "X" button on the position line becomes a flatten button.
- `positionLine.onReverse(cb)` could place an opposite trade.
- `orderLine.onMove(cb)` (if `setEditable(true)`) could let the user drag the stop in TV; we forward the new price to the broker.

## 5. Recommended Arnold-overlay usage

| Need | Best primitive | Why |
|------|---------------|-----|
| Zones (consolidated levels) | `highlighter` (current) or `brush` (richer) | Painted-stroke aesthetic, no label clipping, `linecolor` + `transparency` + `width` reflect strength. |
| Active position visual | `long_position`/`short_position` shape (1pt for open, 2pt for closed) | Native auto-Stop/Open P&L/Target labels + R:R box. |
| Trade history | Same `long_position`/`short_position` with `[entry_time, close_time]` 2-pt form | Time-bounded shape per trade; mutate-in-place handles stop trail updates. |
| Live position controls (close/reverse) | `createPositionLine()` widget with `onClose`/`onReverse` callbacks | Clickable directly on chart. |
| Modifiable stop/TP from chart | `createOrderLine()` with `setEditable(true)` + `onMove(cb)` | User drags stop, we forward to broker. |
| Anchored fair-value indicator | `createStudy('Anchored VWAP')` | Programmatic; default anchor. |
| Volume profile | **Manual** in TV UI (or a different VP study via Pine) | AVP createStudy is locked. |
| Ranges / time-bounded boxes | `date_and_price_range` | Built-in price/percent/pips/bars labels. |
| Confluence corridors (entry → target band) | `parallel_channel` (3pt) | Native level1..level7 with fill+labels. |

## 6. Open questions / future probes

- Can we set `Anchored VWAP`'s anchor time? (Inputs schema doesn't expose it directly — may need `setInputValues` with a hidden field, or the drawing-form `createAnchoredShape({shape:'anchored_vwap', ...})` was rejected; needs deeper probe.)
- Can `applyOverrides()` on a study object force input changes that `setInputValues` doesn't? (Tested briefly; deeper probe needed.)
- `createExecutionShape()` returned a Promise; full method signature not yet probed. Useful for individual fill markers.
- TV has a `_pineEditorTestApi` on the API root — possibly a path to compile custom Pine indicators with our zone data.

## 7. Tooling

To run a probe:

```bash
curl -sS --max-time 30 -X POST http://localhost:8000/mirror/browser/tv-eval \
  -H 'Content-Type: application/json' \
  -d '{"js":"(async()=>{ /* your code */ })()"}'
```

Returns `{url, result}` where `result` is the JSON-stringified return value of your async IIFE. Eval requires the local arnold + Playwright mirror running with the TV tab open.

For a one-off interactive probe that may take time, run with `--max-time 60` or longer; closing the TV tab during eval breaks the call (`Page.evaluate: Target page, context or browser has been closed`).
