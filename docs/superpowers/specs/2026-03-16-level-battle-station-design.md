# Level Battle Station — Design Spec

**Date:** 2026-03-16
**Status:** Draft
**Replaces:** Chart-centric TradingIntradayPage

## Problem

The current trading intraday page centers on a candlestick chart (lightweight-charts + Databento historical API). This is fragile (timestamp bugs, API lag), expensive to maintain, and doesn't match the actual workflow: levels and orderflow drive decisions, not visual candle reading. The sports betting side proves that a table-based UI with expand-for-detail is faster, more reliable, and already battle-tested.

## Solution

Replace the chart-centric page with a **Level Battle Station**: a table of structural levels that triggers a full-context "battle screen" when price reaches a level. Same pattern as the Soft/Value page — but for trading.

## Core Concept

1. **Level Monitor Table** — all structural levels as rows, sorted by proximity to current price
2. **Battle Screen** — gauge dashboard that fires when price touches a level, showing all orderflow/structure/ML confirmations in real-time
3. **Position Manager** — open positions table with re-evaluation at each target level

---

## Section 1: Level Monitor Table

### Data Source

Levels come from the existing `compute_session()` pipeline (AMT analysis). All level types:

| Category | Levels | Source |
|----------|--------|--------|
| VWAP | VWAP, ±1SD, ±2SD, ±3SD | `compute_vwap_bands()` |
| Profile | POC, VAH, VAL | `compute_volume_profile()` |
| IB | IB High, IB Low | `SessionLevels` |
| Prior Day | PDH, PDL | `SessionLevels` |
| Session | Tokyo H/L, London H/L | `SessionLevels` |
| Weekly/Monthly | Weekly H/L, Monthly H/L | `SessionLevels` |
| Structure | Swing H/L, Naked POCs | `detect_swing_points()`, `detect_naked_pocs()` |
| Overnight | ON High, ON Low | `SessionAnalysis.overnight_high/low` |

### Table Columns

| Column | Content |
|--------|---------|
| PRICE | Level price |
| LEVEL | Name (e.g. "VAH", "VWAP +2SD", "PDH") |
| TYPE | Category (session / band / prior / structure) |
| DIST | Distance from current price in ticks, signed |
| STATUS | Lifecycle state (see below) |

### Status Lifecycle

- **WATCHING** — default, level is on radar, row dimmed
- **APPROACHING** — price within configurable threshold (default 15 ticks), row brightens
- **AT LEVEL** — price touched (within 5 ticks / 1.25 NQ points), row highlights with accent color, sound plays, battle screen activates
- **TRIGGERED** — trade taken from this level, row marked
- **REJECTED** — price bounced away beyond threshold, row fades back to WATCHING

### Sorting & Filtering

- Default sort: absolute distance from current price (closest first)
- Levels beyond configurable range (e.g. 200 ticks) can be collapsed/hidden
- No manual filtering needed — proximity handles relevance

---

## Section 2: Battle Screen

### Trigger

When any level reaches AT LEVEL status, the battle screen expands below the compressed level table (split view — level table stays visible as 3-4 rows so you see the full battlefield).

### Layout

The battle screen shows the level that triggered + all confirmation data as gauge bars.

### Gauge Bar Design

Each gauge is a horizontal bar (0-100% fill) with:
- Bar fill (visual)
- Raw value (number)
- Label (text assessment: e.g. "STRONG", "HIGH", "NONE")
- Color coding: green = confirms long, red = confirms short, amber = neutral/mixed, dim = no data
- Direction inference: the level type implies a likely direction (support levels = long bias, resistance levels = short bias). Orderflow gauges compute both directions and show which side is confirming. `delta_aligned` and direction-dependent signals are computed for both long and short — the gauge shows whichever is stronger, with the label indicating direction (e.g. "BULLISH" vs "BEARISH")

```
DELTA        [████████░░░░░░░░] +340  BULLISH
CVD TREND    [██████████████░░] ↑↑    STRONG
ABSORPTION   [████████████████] HIGH  ●
IMBALANCE    [██████░░░░░░░░░░] 3x    STACKING
BIG TRADES   [████████░░░░░░░░] 5 buy ALIGNED
TRAPPED      [░░░░░░░░░░░░░░░░] --    NONE
STOP RUN     [░░░░░░░░░░░░░░░░] --    NONE
PA RATIO     [██████████░░░░░░] 2.1   PASSIVE
```

### Gauge Groups

**Row 1 — Orderflow (real-time, updates every 2-3s while at level):**

| Gauge | Source | Scale |
|-------|--------|-------|
| Delta | `OrderflowSignals.delta` | Normalized vs session average |
| CVD Trend | `OrderflowSignals.cvd_trend` | rising/falling/flat → bar position |
| Absorption | `OrderflowSignals.vsa_absorption` | volume × inverse body_ratio |
| Imbalance | `OrderflowSignals.stacked_imbalance_count` | count of consecutive stacks |
| Big Trades | `OrderflowSignals.big_trades_count` + net delta | count + direction |
| Trapped | `OrderflowSignals.trapped_traders` | boolean + magnitude |
| Stop Run | `OrderflowSignals.stop_run_detected` | boolean + magnitude |
| PA Ratio | `OrderflowSignals.passive_active_ratio` | ratio value |

**Row 2 — Structure (static per session, computed once):**

| Gauge | Source | Scale |
|-------|--------|-------|
| Market Type | `SessionAnalysis.market_type` | balanced / trending |
| Opening Type | `SessionAnalysis.opening_type` | OD / OTD / ORR / OA |
| Distribution | `SessionAnalysis.distribution_type` | normal / P / B / double |
| Poor H/L | `SessionAnalysis.poor_high/low` | boolean per side |
| Swing Structure | Swing point detection | HH/HL or LH/LL |
| Single Prints | `SessionAnalysis.single_prints` | count near level |

**Row 3 — ML & Context (refreshed on level trigger):**

| Gauge | Source | Scale |
|-------|--------|-------|
| Day Type | `GateClassifierModel` (M7) | class + confidence % |
| Pattern | `TemporalPatternModel` (M6) | direction + confidence |
| R-Multiple | `SetupScorerModel` (M5) | predicted R value |
| Macro Regime | VIX/regime score | risk-on / risk-off / neutral |
| VIX | Market data | level + direction |
| Confluence | Level proximity count | # of levels within 20 ticks |

### Trade Action Bar

At the bottom of the battle screen:

```
ENTRY: 19,850   STOP: 19,822   T1: 19,892 (POC)   T2: 19,920 (IB High)   T3: 19,960 (PDH)
SIZE: 2 NQ (calc from risk %)                                          [ TRADE LONG ] [ DISMISS ]
```

- Entry = current price at level
- Stop = nearest structure below (for long) or above (for short)
- T1/T2/T3 = next structural levels in trade direction
- Size = calculated from account risk % and stop distance (existing bankroll/Kelly logic)
- TRADE button creates position record
- DISMISS closes battle screen, level goes back to WATCHING

---

## Section 3: Position Manager

### Table (below battle screen area)

| Column | Content |
|--------|---------|
| ENTRY | Entry price |
| DIR | LONG / SHORT badge |
| SIZE | Current size (decreases on scale-outs) |
| CURRENT | Live price |
| P&L | Points + dollar value |
| STOP | Current stop level |
| NEXT LEVEL | Next target level name + price |
| DIST | Distance to next target |
| STATUS | RUNNING / AT TARGET / STOPPED / CLOSED |

### Target Re-evaluation

When price reaches T1/T2/T3 while a position is open, the battle screen fires again with fresh orderflow data. The context changes from "should I enter?" to "should I manage?"

**Action buttons at target:**
- **SCALE 50%** — reduce position by half, move stop to breakeven
- **CLOSE ALL** — exit entire position
- **HOLD** — dismiss alert, keep running to next target

### Scale-Out Tracking

The existing `Trade` model has a `contracts` field and `TradeEvent` model supports `event_type="partial_exit"`. On scale-out:
- A `TradeEvent` is created with `event_type="partial_exit"`, recording quantity and exit price
- The Trade's effective remaining size is computed as `contracts - sum(partial_exit quantities)`
- The original `contracts` value is preserved for P&L calculation

### Stop Management (automatic)

Stop movement is handled by the `LevelMonitor`'s position tracking. When a `position_at_target` event fires and the user takes an action:
- **SCALE**: `TradeEvent(partial_exit)` created, stop updated on Trade record to breakeven
- **HOLD**: no action, stop stays, next target becomes active
- **CLOSE**: Trade closed with final `TradeEvent`

Default stop rules (configurable):
- On entry: stop at suggested level
- After T1 scale: stop moves to breakeven
- After T2 scale: stop moves to T1

### Position Lifecycle

- **RUNNING** — between levels, no action needed, row shows live P&L
- **AT TARGET** — price hit T1/T2/T3, battle screen fires with position context
- **STOPPED** — hit stop loss, auto-recorded as closed
- **CLOSED** — manually closed via CLOSE ALL

---

## Section 4: Real-time Data Flow

### SSE Event Types (new)

Added to existing `DatabentoLiveStream` + SSE infrastructure:

| Event | Trigger | Payload |
|-------|---------|---------|
| `level_approaching` | Price within threshold of any level | `{level, price, distance, status}` |
| `level_touched` | Price at level (within 3 ticks) | `{level, price, orderflow_snapshot, structure, confluence, suggested_trade}` |
| `level_context` | ML/macro ready (200-500ms after touch) | `{level, ml, macro}` |
| `orderflow_update` | Every 2-3s while any level is AT LEVEL | `{orderflow_snapshot}` (gauge data) |
| `level_rejected` | Price moved away beyond threshold | `{level, status: "rejected"}` |
| `position_at_target` | Open position price hits T1/T2/T3 | `{position, target, orderflow_snapshot, suggested_action}` |

### Backend: Level Monitor

New `LevelMonitor` class, wired into `DatabentoLiveStream` as a subscriber callback (same pattern as `CandleFlow` and `TickWriter`). The stream's `_on_trade()` method calls `level_monitor.on_tick(price, size, ts)`.

**Architecture:** `LevelMonitor` is a separate class (not inline in stream loop) that:
- Receives tick callbacks from the stream
- Holds a cached list of session levels (refreshed on `compute_session()`)
- Maintains per-level state machine (WATCHING → APPROACHING → AT LEVEL → REJECTED)
- Publishes SSE events via the same `_publish()` mechanism used by candle/tick events

**Tick processing flow:**
1. On each tick, check price against all session levels (simple distance calc, O(n) where n ≈ 20-30 levels)
2. Update status per level based on distance thresholds
3. When status transitions to AT LEVEL:
   - Compute fresh orderflow from `TickBuffer` (last 60+ ticks, already in memory)
   - Emit `level_touched` SSE event with orderflow snapshot immediately
   - Schedule async tasks for heavier data (ML inference, macro fetch) — emit `level_context` follow-up event when ready (typically 200-500ms later)
4. While any level is AT LEVEL, emit `orderflow_update` every 2-3 seconds (timer-based, not per-tick)
5. When price moves away beyond threshold, emit `level_rejected`

**Progressive payload strategy:** The `level_touched` event contains only fast data (level info, orderflow snapshot, structure from cached session). ML and macro data arrive in a follow-up `level_context` event. Frontend shows gauges immediately with ML/macro gauges in "loading" state until the follow-up arrives.

**Multiple simultaneous levels:** When two or more levels are AT LEVEL simultaneously (confluence zone), the monitor emits `level_touched` for each but marks them as `{confluence: true, cluster: [level1, level2, ...]}`. The frontend battle screen shows the primary level (closest to price) with a confluence indicator listing the others.

**Re-trigger while active:** If a battle screen is already open for Level A and Level B triggers, the battle screen updates to show Level B (closer to current price) with Level A visible in the level table above. The user can click any AT LEVEL row to switch the battle screen focus.

Session levels are loaded once per session (from `compute_session()` result) and cached in the monitor. Refreshed when `compute_session()` is called (every 5 min via scheduler or manual refresh).

### Frontend: Event Handling

The existing `useMarketStream` hook registers `addEventListener` for `tick`, `book`, `candle`. Extend it with new event types. To avoid excessive re-renders, level events should be managed in a separate `useLevelMonitor` hook that subscribes to the same `EventSource` instance (shared via ref or context) but manages its own state:

```
useMarketStream hook (existing, kept)
    ├── tick → lastTick (used by useLevelMonitor for distance calc)
    └── book → book snapshot

useLevelMonitor hook (new)
    ├── level_approaching → update level status in state
    ├── level_touched → set activeBattle state, populate gauges
    ├── level_context → merge ML/macro data into active battle
    ├── orderflow_update → refresh gauge bar values (replace, not append)
    ├── level_rejected → clear activeBattle if matching level
    └── position_at_target → set activeBattle with position context
```

---

## Section 5: What Changes

### Removed

- `CandleChart.tsx` component
- `GaugeStrip.tsx` component (current bottom bar with `--` placeholders)
- `lightweight-charts` npm dependency
- `api.getCandles()` and candle-related state in TradingIntradayPage
- `GET /api/trading/market/candles` endpoint
- Databento historical bar fetching (`get_candles()` in market_service.py)
- Level ladder chart overlay

### Kept As-Is

- All backend computation: AMT analysis, orderflow, levels, ML models
- SSE stream infrastructure (`useMarketStream`, `DatabentoLiveStream`)
- Session/signals/indicators API endpoints
- Trade creation/management API and DB models
- Scanner + detectors (used for pattern identification in gauge data)

### New Backend

- **LevelMonitor** class in stream loop — proximity detection, status management
- **OrderflowSnapshot** packager — bundles all gauge data into one SSE payload
- **New SSE event types** — level_approaching, level_touched, orderflow_update, level_rejected, position_at_target
- **Position target tracker** — monitors open positions vs structural levels

### New Frontend

- **LevelMonitorTable** component — level rows with distance/status
- **BattleScreen** component — gauge dashboard with grouped confirmation bars
- **GaugeBar** component — reusable horizontal gauge with fill/value/label/color
- **PositionManager** component — open positions table with live P&L
- **TradeActionBar** component — entry/stop/targets + TRADE button
- **Sound system** — audio alerts on level triggers (Web Audio API with pre-loaded short tones; requires initial user interaction to unlock audio context per browser policy — first click on page enables sound)

---

## Section 6: UI Style

Retro terminal aesthetic consistent with existing app:
- Dark background, monospace text
- Gauge bars use accent colors (green/red/amber on dark)
- Battle screen transition: smooth expand, no page navigation
- Sound: short, distinct tones for approaching vs at-level
- Level status badges match existing pill/badge patterns

---

## Non-Goals (Explicit)

- No candlestick chart — levels and gauges replace visual price action
- No broker integration (phase 1) — TRADE button creates internal position record
- No external notifications — in-app only (highlight + sound)
- No auto-trading — system suggests, human decides
- No historical backtesting UI — this is a live trading cockpit
