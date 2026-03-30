# Multi-Timeframe Swing Detection Design

**Date:** 2026-03-30
**Status:** Draft
**Scope:** Backend swing detection, DQN integration, LevelMonitor, chart rendering

## Problem

The DQN agent lacks higher-timeframe market structure context. It has PDH/PDL (single prior day) and session H/L, but cannot determine whether price is in an uptrend (HH/HL) or downtrend (LL/LH) at the daily, weekly, or monthly level. This is fundamental context for AMT, Dow Theory, and auction market theory — the agent needs to know whether it's trading with or against the trend at each structural timeframe.

## Solution

Add multi-timeframe swing detection (daily/weekly/monthly) that:
1. Detects fractal pivot highs and lows on aggregated D/W/M candles
2. Classifies market structure per timeframe (uptrend/downtrend/ranging)
3. Feeds into the DQN as both passive STRUCTURE features and active MonitoredLevels
4. Renders on the CandleChart as horizontal price levels

## Architecture

### Data Flow

```
market_candles (120 days of 1m bars)
    ↓
aggregate_to_timeframe()  →  daily/weekly/monthly OHLC candles
    ↓
detect_fractal_pivots()   →  swing highs + swing lows per TF
    ↓
classify_structure()      →  HH/HL/LH/LL → uptrend/downtrend/ranging
    ↓
SwingStructure dataclass
    ↓
┌─────────────────────────────────────┐
│ build_expanded_session()            │
│  ├→ levels[] (6 new MonitoredLevels)│
│  ├→ swing_structure (context dict)  │
│  └→ session_context for DQN        │
└─────────────────────────────────────┘
    ↓
┌───────────────────┐  ┌─────────────────────────┐
│ LevelMonitor      │  │ DQN STRUCTURE segment   │
│ (approach/touch)  │  │ (9 passive features)    │
└───────────────────┘  └─────────────────────────┘
    ↓                          ↓
DQN inference with LEVEL TYPE one-hot (31 slots)
    + STRUCTURE context (32 features)
```

### Files Modified

| File | Change |
|------|--------|
| `backend/src/market_data/levels.py` | New `SwingLevel`, `TimeframeSwings`, `SwingStructure` dataclasses. New `aggregate_to_timeframe()`, `detect_fractal_pivots()`, `compute_multi_tf_swings()` functions. Refactor existing `detect_swing_points()` to use shared pivot logic. |
| `backend/src/rl/config.py` | Add 6 new `LevelType` enum entries for swing levels |
| `backend/src/rl/features/structure_features.py` | Expand from 23 → 32 features (9 new swing features) |
| `backend/src/rl/features/observation.py` | Update segment comment for new STRUCTURE size |
| `backend/src/rl/features/level_features.py` | One-hot encoding auto-expands (uses `len(LevelType)`) |
| `backend/src/services/market_service.py` | Call `compute_multi_tf_swings()` in `_enrich_with_bars()`, add swing levels to ExpandedSession |
| `backend/src/market_data/level_monitor.py` | Per-level approach zones, `_categorize()` for swing types |
| `frontend/src/types/market.ts` | New `SwingLevel`, `SwingStructure` types. Expand `SessionLevelDay` and `PriceStructure` |
| `frontend/src/components/Terminal/pages/CandleChart.tsx` | Render swing levels as colored dashed lines |
| `frontend/src/components/Terminal/pages/dqnConfig.ts` | Update LEVEL_TYPES (25→31), STRUCTURE (23→32), shift downstream segment boundaries |
| `frontend/src/components/Terminal/pages/NeuralNetworkSVG.tsx` | Updated total input count |

## Detailed Design

### 1. Bar Aggregation

New function `aggregate_to_timeframe(bars_1m, timeframe)` in `levels.py`:

- Groups 1m bars into daily/weekly/monthly OHLC candles using CET session boundaries
- Daily: 00:00-22:00 CET (matches existing session definition)
- Weekly: Monday 00:00 CET to Friday 22:00 CET
- Monthly: 1st of month 00:00 CET to last trading day 22:00 CET
- Returns list of `{"date": str, "open": float, "high": float, "low": float, "close": float, "ts": int}` sorted chronologically

### 2. Fractal Pivot Detection

New function `detect_fractal_pivots(candles, lookback, max_pivots)` in `levels.py`:

**Algorithm:** A swing high at index `i` requires `candles[i].high >= candles[j].high` for all `j` in `[i-lookback, i+lookback]` where `j != i`. Swing low is the mirror.

**Lookback per timeframe:**
- Daily: lookback=3 (confirmed after 3 subsequent days — catches real swings, not noise)
- Weekly: lookback=2 (confirmed after 2 subsequent weeks)
- Monthly: lookback=2 (confirmed after 2 subsequent months)

**Returns:** Last `max_pivots` (default 3) swing highs and swing lows as `SwingLevel` objects, newest first.

### 3. Structure Classification

From the last 2 swing highs (SH1, SH2) and last 2 swing lows (SL1, SL2):
- **Uptrend:** SH2 > SH1 (higher high) AND SL2 > SL1 (higher low)
- **Downtrend:** SH2 < SH1 (lower high) AND SL2 < SL1 (lower low)
- **Ranging:** Mixed (HH+LL, LH+HL, or insufficient pivots)

**Trend alignment score:** Sum of per-TF trend values (+1 up, 0 range, -1 down) divided by 3. Ranges from -1.0 (all downtrend) to +1.0 (all uptrend).

### 4. Backend Dataclasses

```python
@dataclass
class SwingLevel:
    price: float
    timestamp: int       # epoch seconds
    type: str            # "swing_high" or "swing_low"
    timeframe: str       # "daily", "weekly", "monthly"

@dataclass
class TimeframeSwings:
    timeframe: str       # "daily", "weekly", "monthly"
    structure: str       # "uptrend", "downtrend", "ranging"
    swing_highs: list[SwingLevel]  # last 3, newest first
    swing_lows: list[SwingLevel]   # last 3, newest first

@dataclass
class SwingStructure:
    daily: TimeframeSwings
    weekly: TimeframeSwings
    monthly: TimeframeSwings
    trend_alignment: float  # -1.0 to +1.0
```

### 5. DQN STRUCTURE Segment (9 New Features)

Added at indices 23-31 of the STRUCTURE segment (total grows 23 → 32):

| Index | Feature | Encoding | Range |
|-------|---------|----------|-------|
| 23 | `swing_trend_d` | Daily structure: -1/0/+1 | [-1, 1] |
| 24 | `swing_trend_w` | Weekly structure: -1/0/+1 | [-1, 1] |
| 25 | `swing_trend_m` | Monthly structure: -1/0/+1 | [-1, 1] |
| 26 | `swing_dist_d` | Signed distance to nearest daily swing (ticks/200) | [-1, 1] |
| 27 | `swing_dist_w` | Signed distance to nearest weekly swing (ticks/200) | [-1, 1] |
| 28 | `swing_dist_m` | Signed distance to nearest monthly swing (ticks/200) | [-1, 1] |
| 29 | `swing_pos_d` | Price position in daily swing range (0=low, 1=high) | [0, 1] |
| 30 | `swing_pos_w` | Price position in weekly swing range | [0, 1] |
| 31 | `swing_pos_m` | Price position in monthly swing range | [0, 1] |

**Distance calculation:** Find the closest swing level (either high or low) for that timeframe. Signed: positive if price is above the swing level, negative if below. Normalized by dividing by 200 ticks (~50 NQ points) and clipping to [-1, 1].

**Position calculation:** `(price - lowest_recent_swing_low) / (highest_recent_swing_high - lowest_recent_swing_low)`. Uses the full set of last 3 highs + 3 lows to define the range. Returns 0.5 if range is zero.

`extract_structure_features()` accepts a new `swing_structure: SwingStructure | None` parameter. Passed via `state["swing_structure"]` from `_build_rl_state()`.

### 6. LevelType Enum Expansion

6 new entries in `backend/src/rl/config.py`:

```python
DAILY_SWING_HIGH = "daily_swing_high"
DAILY_SWING_LOW = "daily_swing_low"
WEEKLY_SWING_HIGH = "weekly_swing_high"
WEEKLY_SWING_LOW = "weekly_swing_low"
MONTHLY_SWING_HIGH = "monthly_swing_high"
MONTHLY_SWING_LOW = "monthly_swing_low"
```

One-hot encoding grows from 25 → 31. `encode_level_type()` auto-expands since it uses `list(LevelType)`.

### 7. LevelMonitor Changes

**Per-level approach zones** — add `approach_ticks` and `at_level_ticks` to `MonitoredLevel`:

| Level Type | Approach (ticks) | At-Level (ticks) | Reject (ticks) |
|------------|-----------------|-------------------|-----------------|
| Daily swings | 15 | 5 | 20 |
| Weekly swings | 25 | 10 | 35 |
| Monthly swings | 40 | 15 | 50 |
| All other levels | 15 (default) | 5 (default) | 20 (default) |

`MonitoredLevel` gets two new fields with defaults matching current behavior. `on_tick()` uses `level.approach_ticks` / `level.at_level_ticks` instead of class constants.

**load_levels():** Swing levels arrive in the `levels` list of `ExpandedSession` with types like `"daily_swing_high"`. Categorized as `"structure"` by `_categorize()`.

### 8. ExpandedSession Integration

In `market_service.py._enrich_with_bars()`:

1. Fetch 120 days of 1m bars via new `_get_swing_bars(symbol)` method (separate from `_get_session_bars` which fetches ~5 days)
2. Call `compute_multi_tf_swings(bars_1m_120d)` → `SwingStructure`
3. Add most recent swing H/L per timeframe to the `levels` list (6 new entries)
4. Add full `SwingStructure` as `swing_structure` key in the response
5. Include in `session_context` dict so `_build_rl_state()` can access it

### 9. Chart Rendering

**SessionLevelDay expansion** — 6 new nullable fields:

```typescript
daily_swing_high: number | null;
daily_swing_low: number | null;
weekly_swing_high: number | null;
weekly_swing_low: number | null;
monthly_swing_high: number | null;
monthly_swing_low: number | null;
```

**Rendering** — same pattern as PDH/PDL dashed lines in CandleChart.tsx:
- Daily swings: white dashed lines, labels "D-SH" / "D-SL"
- Weekly swings: blue (`#3b82f6`) dashed lines, labels "W-SH" / "W-SL"
- Monthly swings: purple (`#a855f7`) dashed lines, labels "M-SH" / "M-SL"
- Full chart width (not time-scoped like session boxes)

### 10. Frontend dqnConfig.ts

LEVEL_TYPES array (31 entries):
```
existing 25 + 'daily_swing_high', 'daily_swing_low',
  'weekly_swing_high', 'weekly_swing_low',
  'monthly_swing_high', 'monthly_swing_low'
```

STRUCTURE array (32 entries):
```
existing 23 + 'swing_trend_d', 'swing_trend_w', 'swing_trend_m',
  'swing_dist_d', 'swing_dist_w', 'swing_dist_m',
  'swing_pos_d', 'swing_pos_w', 'swing_pos_m'
```

**Note:** The frontend dqnConfig.ts is currently stale — it shows 139 inputs (from an earlier model version) while the actual RL model has 167 inputs. This implementation should sync the frontend config to match the actual backend observation vector (182 after this change). All segment boundaries must be derived from the backend `observation.py` segment sizes.

Updated DQN_SEGMENTS (matching backend observation.py):

| Segment | Size | Start | End |
|---------|------|-------|-----|
| LEVEL TYPE | 31 | 0 | 31 |
| ORDERFLOW | 21 | 31 | 52 |
| STRUCTURE | 32 | 52 | 84 |
| TPO | 26 | 84 | 110 |
| CANDLES | 15 | 110 | 125 |
| CONFLUENCE | 8 | 125 | 133 |
| MACRO | 7 | 133 | 140 |
| SETUP | 14 | 140 | 154 |
| MICRO | 20 | 154 | 174 |
| APPROACH | 1 | 174 | 175 |
| EXECUTION | 7 | 175 | 182 |

### 11. Frontend Types

```typescript
interface SwingLevel {
  price: number;
  timestamp: number;
  type: 'swing_high' | 'swing_low';
  timeframe: 'daily' | 'weekly' | 'monthly';
}

interface TimeframeSwings {
  timeframe: string;
  structure: 'uptrend' | 'downtrend' | 'ranging';
  swing_highs: SwingLevel[];
  swing_lows: SwingLevel[];
}

interface SwingStructure {
  daily: TimeframeSwings;
  weekly: TimeframeSwings;
  monthly: TimeframeSwings;
  trend_alignment: number;
}
```

`ExpandedSession` gets `swing_structure?: SwingStructure` alongside existing `structure` field.

## Data Requirements

| Timeframe | Lookback | Trading Days Needed | Calendar Days of 1m Bars |
|-----------|----------|---------------------|--------------------------|
| Daily | 3 | ~20 | ~30 |
| Weekly | 2 | ~40 | ~60 |
| Monthly | 2 | ~80 | ~120 |

**Source:** `market_candles` table (SQLite). Single query for 120 days of 1m bars = ~158K rows. Fetched once per `build_expanded_session()` call (session startup + manual refresh).

**Graceful degradation:** If <20 days available, compute daily swings only. If <60 days, skip weekly. If <120 days, skip monthly. Return `None` for missing timeframes.

## Model Dimension Changes

| Segment | Before | After | Delta |
|---------|--------|-------|-------|
| LEVEL TYPE (one-hot) | 25 | 31 | +6 |
| STRUCTURE | 23 | 32 | +9 |
| **Total observation** | **167** | **182** | **+15** |

All other segments unchanged. Network input layer grows from 167 → 182. Existing model weights are invalidated (retraining required — expected since this adds new features).

## Testing

- Unit test: `aggregate_to_timeframe()` with known 1m bars → verify correct D/W/M candles
- Unit test: `detect_fractal_pivots()` with synthetic candle sequences (known HH/HL/LH/LL patterns)
- Unit test: Structure classification (uptrend, downtrend, ranging, insufficient data)
- Unit test: Graceful degradation with insufficient bar history
- Unit test: `extract_structure_features()` returns 32 features with swing data
- Integration test: `build_observation()` returns 182-element vector
- Integration test: `LevelMonitor` loads swing levels with correct approach zones
