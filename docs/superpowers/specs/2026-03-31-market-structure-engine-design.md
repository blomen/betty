# Market Structure Engine Design

**Date:** 2026-03-31
**Status:** Draft
**Scope:** Replace fractal pivot detection with proper Dow Theory / ICT market structure engine

## Problem

The current swing detection uses fractal pivots (bar high >= N neighbors) which:
1. Finds candle highs/lows, not structural reversal points
2. Has no concept of confirmation — a "pivot" can appear and disappear
3. Cannot detect BOS (Break of Structure) or CHoCH (Change of Character)
4. Uses only 48 days of 1m bars (live path), missing weekly/monthly structure
5. Structure classification is fragile (no tolerance, no intermediate states)

## Solution

Replace `detect_fractal_pivots` + `_classify_structure` with a `MarketStructureEngine` state machine that:
1. Confirms swings only when price closes beyond the prior swing level
2. Tracks BOS (trend continuation) and CHoCH (trend reversal) events
3. Uses session summaries (795 sessions back to 2011) for full D/W/M history
4. Provides 5-state trend classification (uptrend, downtrend, reversing_up, reversing_down, ranging)

## Algorithm: MarketStructureEngine

### State Machine

```
States: SEEKING_HIGH | SEEKING_LOW
Trend:  uptrend | downtrend | reversing_up | reversing_down | ranging

For each candle (processed chronologically):

  Track:
    - potential_high: highest high since last confirmed swing low
    - potential_low: lowest low since last confirmed swing high
    - last_confirmed_sh: most recent confirmed swing high
    - last_confirmed_sl: most recent confirmed swing low

  If SEEKING_HIGH (looking for next swing high after a confirmed low):
    - Update potential_high if candle.high > current potential_high
    - If candle.close > last_confirmed_sh.price:
      → CONFIRM potential_high as new swing high
      → This is a BOS (bullish) if in uptrend/reversing_up
      → This is a CHoCH (bullish) if in downtrend
      → Compare with prior SH: HH or LH?
      → Switch to SEEKING_LOW

  If SEEKING_LOW (looking for next swing low after a confirmed high):
    - Update potential_low if candle.low < current potential_low
    - If candle.close < last_confirmed_sl.price:
      → CONFIRM potential_low as new swing low
      → This is a BOS (bearish) if in downtrend/reversing_down
      → This is a CHoCH (bearish) if in uptrend
      → Compare with prior SL: HL or LL?
      → Switch to SEEKING_HIGH
```

### Close vs Wick Rule

Only candle **close** beyond a swing level confirms a break. A wick through is a liquidity sweep, not a structural break. This filters false breakouts and stop hunts.

### Swing Confirmation Logic

A swing is NOT confirmed just by having lower bars after it. It's confirmed when price closes beyond the opposing swing level:

- **Swing low confirmed** when price closes above the preceding swing high
- **Swing high confirmed** when price closes below the preceding swing low

This means swings are confirmed retroactively — the low existed at the time, but it becomes structurally significant only when price proves it was a reversal.

### BOS / CHoCH Detection

**BOS (Break of Structure)** — trend continuation:
- Uptrend: price closes above prior confirmed SH → BOS bullish (HH confirmed)
- Downtrend: price closes below prior confirmed SL → BOS bearish (LL confirmed)
- BOS and swing confirmation are the same event

**CHoCH (Change of Character)** — first reversal signal:
- Uptrend: price closes below most recent confirmed SL → CHoCH bearish
- Downtrend: price closes above most recent confirmed SH → CHoCH bullish
- After CHoCH, trend transitions to reversing state

### Trend State Transitions

```
uptrend + BOS above SH     → uptrend (HH confirmed)
uptrend + CHoCH below SL   → reversing_down

reversing_down + BOS below SL  → downtrend (LL confirmed)
reversing_down + BOS above SH  → uptrend (false break, trend resumes)

downtrend + BOS below SL       → downtrend (LL confirmed)
downtrend + CHoCH above SH     → reversing_up

reversing_up + BOS above SH    → uptrend (HH confirmed)
reversing_up + BOS below SL    → downtrend (false break, trend resumes)

Initial state: ranging → first BOS in either direction sets the trend
```

### Initialization

Process candles from oldest to newest. Start in `ranging` state with `SEEKING_HIGH`. The first two confirmed swings establish the initial structure. Need at least 4 confirmed swings (SH, SL, SH, SL) to classify a trend.

## Data Source

### Live Path

Load `backend/data/rl/session_summaries.json` at startup. Each session → 1 daily candle:
- `high = rth_high`
- `low = rth_low`
- `close = poc` (best proxy for session close — last traded price not always available)

795 sessions back to 2011 → plenty for daily, weekly, and monthly structure.

Cache the result — recompute only when a new session is added.

### Replay / Training Path

`compute_precomputed_levels` in `session_store.py` already has access to session summaries. Use the same `MarketStructureEngine` on sessions prior to `current_date`.

### Weekly / Monthly Candles

Aggregate the daily session candles:
- **Weekly**: Monday through Friday sessions → 1 weekly candle (high of highs, low of lows, close of Friday)
- **Monthly**: All sessions in calendar month → 1 monthly candle

Run `MarketStructureEngine` independently on each timeframe's candle series.

## Data Types

```python
@dataclass
class StructureEvent:
    """A structural event: swing confirmation, BOS, or CHoCH."""
    price: float
    timestamp: int          # epoch seconds
    event_type: str         # "bos_bullish", "bos_bearish", "choch_bullish", "choch_bearish"
    swing_type: str         # "swing_high" or "swing_low" that was confirmed
    swing_price: float      # price of the confirmed swing

@dataclass
class SwingLevel:
    price: float
    timestamp: int
    type: str               # "swing_high" or "swing_low"
    timeframe: str          # "daily", "weekly", "monthly"
    confirmed: bool = True  # always True in output (unconfirmed are internal)

@dataclass
class TimeframeSwings:
    timeframe: str
    structure: str          # "uptrend", "downtrend", "reversing_up", "reversing_down", "ranging"
    swing_highs: list[SwingLevel]   # confirmed, newest first, max 3
    swing_lows: list[SwingLevel]    # confirmed, newest first, max 3
    last_bos: StructureEvent | None
    last_choch: StructureEvent | None
    bos_active: bool        # True if last_bos within recency window
    choch_active: bool      # True if last_choch within recency window

@dataclass
class SwingStructure:
    daily: TimeframeSwings
    weekly: TimeframeSwings
    monthly: TimeframeSwings
    trend_alignment: float  # -1.0 to +1.0
```

### BOS/CHoCH Recency Window

A BOS or CHoCH flag is "active" if it occurred within the last N candles of that timeframe:
- Daily: N=5 (about 1 week)
- Weekly: N=3 (about 3 weeks)
- Monthly: N=2 (about 2 months)

After that, the event is historical — the trend state already reflects it.

## DQN Integration

### STRUCTURE Segment: 23 existing + 15 swing = 38 total

| Index | Feature | Range | Description |
|-------|---------|-------|-------------|
| 23 | swing_trend_d | [-1, 1] | Daily: -1 down, -0.5 reversing_down, 0 ranging, +0.5 reversing_up, +1 up |
| 24 | swing_trend_w | [-1, 1] | Weekly structure |
| 25 | swing_trend_m | [-1, 1] | Monthly structure |
| 26 | swing_dist_d | [-1, 1] | Signed distance to nearest confirmed daily swing (ticks/200) |
| 27 | swing_dist_w | [-1, 1] | Weekly |
| 28 | swing_dist_m | [-1, 1] | Monthly |
| 29 | swing_pos_d | [0, 1] | Price position in daily swing range |
| 30 | swing_pos_w | [0, 1] | Weekly |
| 31 | swing_pos_m | [0, 1] | Monthly |
| 32 | bos_d | 0/1 | Recent BOS on daily |
| 33 | bos_w | 0/1 | Recent BOS on weekly |
| 34 | bos_m | 0/1 | Recent BOS on monthly |
| 35 | choch_d | 0/1 | Recent CHoCH on daily |
| 36 | choch_w | 0/1 | Recent CHoCH on weekly |
| 37 | choch_m | 0/1 | Recent CHoCH on monthly |

### Trend Encoding

| State | Value |
|-------|-------|
| uptrend | +1.0 |
| reversing_up | +0.5 |
| ranging | 0.0 |
| reversing_down | -0.5 |
| downtrend | -1.0 |

### Observation Dimension Change

STRUCTURE grows from 32 → 38 (+6 for BOS/CHoCH flags).
Total OBSERVATION_DIM increases by 6.

### MonitoredLevels

Same 6 level types as before: `daily_swing_high/low`, `weekly_swing_high/low`, `monthly_swing_high/low`. But now these are **confirmed** structural levels, not fractal pivots. Same approach zone scaling (daily 15/5/20, weekly 25/10/35, monthly 40/15/50).

## Chart Rendering

Same as current implementation:
- D-SH / D-SL: white dashed lines
- W-SH / W-SL: blue dashed lines
- M-SH / M-SL: purple dashed lines

BOS/CHoCH markers on chart: deferred to future work. The DQN gets them as features regardless.

## Files Modified

| File | Change |
|------|--------|
| `backend/src/market_data/levels.py` | Replace `detect_fractal_pivots` + `_classify_structure` with `MarketStructureEngine` class. Keep `aggregate_to_timeframe`. Update `compute_multi_tf_swings` to use new engine. Add `StructureEvent` dataclass. |
| `backend/src/rl/features/structure_features.py` | Expand from 32 → 38 features. Add BOS/CHoCH extraction. Update trend_map for 5-state encoding. |
| `backend/src/rl/features/observation.py` | Update structure comment (32 → 38). |
| `backend/src/rl/data/session_store.py` | Update `_compute_swing_from_summaries` to use `MarketStructureEngine`. |
| `backend/src/services/market_service.py` | Live path: load session_summaries.json instead of _get_swing_bars. |
| `frontend/src/types/market.ts` | Add `StructureEvent` type, update `TimeframeSwings`. |
| `frontend/src/components/Terminal/pages/dqnConfig.ts` | Add 6 BOS/CHoCH feature names, update segment boundaries. |

## Testing

- Unit test: `MarketStructureEngine` with synthetic uptrend candles → confirms HH/HL, detects BOS
- Unit test: Uptrend → CHoCH → downtrend transition
- Unit test: CHoCH → false break → trend resumes
- Unit test: Close-only rule (wick through level doesn't confirm)
- Unit test: Insufficient candles → ranging with no swings
- Unit test: Weekly/monthly aggregation from daily sessions
- Unit test: Structure features return 38 elements with correct BOS/CHoCH flags
- Integration test: Full observation vector with new dimension
- Integration test: Replay engine passes swing structure correctly
