# TPO Profile Engine — Design Spec

**Date:** 2026-03-20
**Status:** Approved

## Overview

Extend the existing `backend/src/market_data/tpo.py` module with new fields (rotation factor, opening type, profile shape with B-shape, excess tick counts), add pre-computed storage for RL backtesting, API endpoints, backfill CLI, and frontend overlay rendering on the L1 CandleChart.

## Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Period length | 30 minutes (classic) | Standard Market Profile convention |
| Session anchor | Full Globex (18:00 ET → 17:00 ET) | Consistent with existing session boundary in `market_service.py` |
| Frontend placement | Overlay on L1 CandleChart + stats in BookSnapshot | Consistent with existing VP overlay pattern, no layout reflow |
| TPO accent color | Orange (#ff6b35) | Distinct from VP purples/pinks/yellows, matches tab accent |
| Storage | Pre-computed per session in `market_tpo_sessions` | RL backtesting needs bulk historical data without recomputing |
| API | Two endpoints: `/tpo` (batch historical) + `/tpo/live` (developing) | Separate concerns: RL training vs real-time display |
| Approach | Extend existing `tpo.py`, not create new file | Already has callers in `market_service.py`, `replay_engine.py`, `detector.py` |
| Session date convention | Date of the Globex open (ET), matching existing `market_session.date` | e.g., session opening 18:00 ET on March 19 → date="2026-03-19" |
| Rotation factor | Use signed RF from `metrics.py` (+1/-1 per high/low vs prev period) | Standard Market Profile definition, already implemented correctly |

## Existing Code to Extend

### `backend/src/market_data/tpo.py` (current state)

The module already provides:
- `TPOProfile` dataclass with: `letters`, `poc`, `vah`, `val`, `single_prints`, `ledges`, `poor_high`, `poor_low`, `ib_tpo_count`
- `compute_tpo_profile(bars_30m, tick_size)` — takes pre-aggregated 30m bars
- `classify_tpo_shape(profile)` — returns "p-shape", "b-shape", "d-shape", "balanced"
- `compute_rotation_factor(bars_30m)` — **NOTE: This is a different RF than `metrics.py`. It counts unsigned range extensions. We will NOT use this version.** The canonical RF is in `metrics.py` (signed ±1 per high/low comparison). The `tpo.py` version should be deprecated/removed.
- `detect_excess(profile)` — returns `(excess_high: bool, excess_low: bool)`

### `backend/src/market_data/metrics.py` (rotation factor)

`compute_rotation_factor(highs, lows) -> int` — the correct signed rotation factor. Per period: `high > prev_high → +1`, `high < prev_high → -1`, same for lows. Net sum across all periods.

### Callers to update

| File | Current usage | Migration |
|------|--------------|-----------|
| `services/market_service.py:22,278` | `compute_tpo_profile(bars_30m)` | Replace with `build_full_tpo_profile(bars_30m)` — returns enriched `TPOProfile` with all new fields populated |
| `services/market_service.py:742-750` | Constructs stub `TPOProfile` with empty letters | Add new fields with defaults. The stub will have `rotation_factor=0`, `profile_shape="D"`, `opening_type="OA"` — these are intentionally neutral defaults for the detector context when full TPO data is unavailable |
| `rl/data/replay_engine.py:31-36,505-526` | Calls 4 separate functions, builds dict manually | Replace entire block with single `build_full_tpo_profile(bars_30m)` call, then read fields from the returned `TPOProfile` dataclass. See migration detail below |
| `market_data/setups/detector.py:6` | Imports `TPOProfile` | Compatible — only accesses existing fields, new fields have defaults |

#### `replay_engine.py` migration detail

Current code (lines 505-526):
```python
profile = compute_tpo_profile(bars_30m, tick_size=TICK_SIZE)
shape = classify_tpo_shape(profile)
rotation_factor, rotation_count = compute_rotation_factor(bars_30m)
excess_high, excess_low = detect_excess(profile)
tpo_profile_dict = {
    "poc": profile.poc, "vah": profile.vah, "val": profile.val,
    "shape": shape,
    "rotation_factor": rotation_factor, "rotation_count": rotation_count,
    "excess_high": excess_high, "excess_low": excess_low,
    ...
}
```

Becomes:
```python
from ...market_data.tpo import build_full_tpo_profile

profile = build_full_tpo_profile(bars_30m, tick_size=TICK_SIZE)
tpo_profile_dict = {
    "poc": profile.poc, "vah": profile.vah, "val": profile.val,
    "shape": profile.profile_shape,
    "rotation_factor": profile.rotation_factor,
    "rotation_count": profile.rotation_factor,  # RF is now a single int (signed sum)
    "excess_high": profile.upper_excess > 0,    # bool compat for existing RL data
    "excess_low": profile.lower_excess > 0,     # bool compat for existing RL data
    "upper_excess_ticks": profile.upper_excess,  # NEW: tick count
    "lower_excess_ticks": profile.lower_excess,  # NEW: tick count
    "poor_high": profile.poor_high,
    "poor_low": profile.poor_low,
    "single_prints": profile.single_prints,
    "ledges": profile.ledges,
    "ib_tpo_count": profile.ib_tpo_count,
    "opening_type": profile.opening_type,        # NEW
    "opening_direction": profile.opening_direction,  # NEW
    "ib_high": profile.ib_high,                  # NEW
    "ib_low": profile.ib_low,                    # NEW
}
```

Key compat notes:
- `excess_high`/`excess_low` preserved as bools for backward compat with existing RL training data
- New `upper_excess_ticks`/`lower_excess_ticks` added as int fields
- `rotation_count` mapped to same value as `rotation_factor` (the old tuple return is removed)
- New TPO fields (`opening_type`, `ib_high`, etc.) added to the dict

## Backend Changes

### Extended `TPOProfile` dataclass

Add new fields to the existing dataclass. All new fields have defaults for backward compatibility.

```python
@dataclass
class TPOProfile:
    # --- Existing fields (unchanged) ---
    letters: dict[float, list[str]]      # price → [A, B, C, ...]
    poc: float
    vah: float
    val: float
    single_prints: list[float]
    ledges: list[float]
    poor_high: bool
    poor_low: bool
    ib_tpo_count: int

    # --- New fields ---
    tpo_counts: dict[float, int] = field(default_factory=dict)  # price → count (derived from letters)
    ib_high: float = 0.0                 # Initial balance high (periods A+B)
    ib_low: float = 0.0                  # Initial balance low
    rotation_factor: int = 0             # Signed RF from metrics.py
    profile_shape: str = "balanced"      # "p", "b", "D", "B" (renamed from classify_tpo_shape output)
    opening_type: str = "OA"             # "OD", "OTD", "ORR", "OA"
    opening_direction: str = "neutral"   # "up", "down", "neutral"
    upper_excess: int = 0                # Consecutive single-print levels at top
    lower_excess: int = 0                # Consecutive single-print levels at bottom
    session_high: float = 0.0
    session_low: float = 0.0
```

### Updated `compute_tpo_profile`

Keep the same signature (`bars_30m, tick_size`). Extend the function to also compute:

1. **`tpo_counts`**: Derived from `letters` — `{price: len(letters_at_price)}` for each price.
2. **`ib_high` / `ib_low`**: Max high / min low across the first 2 bars (periods A+B). If < 2 bars, use whatever is available.
3. **`session_high` / `session_low`**: Max high / min low across all bars.
4. **`upper_excess`**: Count consecutive single-print levels starting from the highest price downward. Stop at first price with > 1 letter.
5. **`lower_excess`**: Count consecutive single-print levels starting from the lowest price upward. Stop at first price with > 1 letter.

### New: `classify_opening_type(bars_30m) -> tuple[str, str]`

Returns `(opening_type, direction)`. Algorithm using periods A through D (first 4 bars):

```
Given: A = bar[0], B = bar[1], C = bar[2], D = bar[3] (if available)

1. Determine direction of period A:
   - up if close > open, down if close < open, neutral if equal

2. Open-Drive (OD):
   - A opens at or within 25% of session range from the extreme
   - B extends in same direction as A (no overlap with A in opposite direction)
   - C continues or holds (does not retrace > 50% of A+B range)
   → Strong conviction, no test

3. Open-Test-Drive (OTD):
   - A moves in one direction
   - B retraces partially into A's range (tests) but does not exceed A's extreme on the test side
   - C drives away from the test in A's original direction, exceeding A's extreme
   → Test then drive

4. Open-Rejection-Reverse (ORR):
   - A moves in one direction
   - B continues or extends A's direction
   - C or D reverses, breaking below A's low (if A was up) or above A's high (if A was down)
   → Failed continuation, reversal

5. Open-Auction (OA):
   - None of the above patterns match
   - Periods A-D rotate, overlapping significantly
   → Balanced, no directional conviction

If fewer than 4 periods exist, classify as OA (insufficient data).
```

### New: B-shape detection in `classify_tpo_shape`

Update the existing function to detect double distribution (B-shape):

```
1. Build TPO count histogram (sorted by price)
2. Find POC (global max)
3. Scan for a "valley" — a local minimum where TPO count drops below 40% of POC count
4. Check that both sides of the valley have a local maximum ≥ 60% of POC count
5. If valley found with peaks on both sides → "B" (double distribution)
6. Otherwise fall through to existing p/b/D/balanced logic

Shape name mapping (keep existing strings for backward compat, add B):
- "p-shape" → "p-shape" (unchanged)
- "b-shape" → "b-shape" (unchanged)
- "d-shape" → "d-shape" (unchanged)
- "balanced" → "balanced" (unchanged)
- NEW: "B-shape" for double distribution
The `profile_shape` field on `TPOProfile` stores these same strings.
```

### New: `build_full_tpo_profile(bars_30m, tick_size) -> TPOProfile`

Convenience function that calls `compute_tpo_profile`, then enriches with:
- `rotation_factor` via `metrics.compute_rotation_factor(highs, lows)`
- `profile_shape` via `classify_tpo_shape(profile)`
- `opening_type` + `opening_direction` via `classify_opening_type(bars_30m)`

This is the single entry point for both live computation and backfill.

### Update `detect_excess` to return tick counts

Change `detect_excess(profile) -> tuple[int, int]` (was `tuple[bool, bool]`). Now returns `(upper_excess_ticks, lower_excess_ticks)` — count of consecutive single-print levels at each extreme. Truthy values preserve backward compat for callers that check `if excess_high:`.

### Remove deprecated `compute_rotation_factor` from `tpo.py`

Delete the function from `tpo.py`. The canonical version is in `metrics.py`. Update any callers that imported it from `tpo.py` (currently only `replay_engine.py` and test files).

### Extract `_aggregate_bars_30m` as module-level function

Move `MarketService._aggregate_bars_30m()` to a module-level function in `tpo.py` (or `levels.py`) so the backfill CLI can use it without instantiating `MarketService`.

### Storage: `market_tpo_sessions` table

```sql
CREATE TABLE market_tpo_sessions (
    symbol        TEXT NOT NULL,
    date          TEXT NOT NULL,         -- YYYY-MM-DD (Globex open date, ET)
    poc           REAL NOT NULL,
    vah           REAL NOT NULL,
    val           REAL NOT NULL,
    ib_high       REAL,
    ib_low        REAL,
    rotation_factor INTEGER,
    profile_shape TEXT,                  -- "b", "p", "D", "B"
    opening_type  TEXT,                  -- "OD", "OTD", "ORR", "OA"
    opening_direction TEXT,              -- "up", "down", "neutral"
    upper_excess  INTEGER DEFAULT 0,
    lower_excess  INTEGER DEFAULT 0,
    session_high  REAL,
    session_low   REAL,
    session_json  TEXT NOT NULL,         -- Full TPOProfile as JSON (letters, tpo_counts, single_prints, ledges, etc.)
    created_at    TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (symbol, date)
);
```

Add SQLAlchemy model in `db/models.py` using the declarative base pattern matching existing models (e.g., `MarketSession`). The `session_json` column stores the full `TPOProfile` serialized via `dataclasses.asdict()` → `json.dumps()`.

**Why both indexed columns and `session_json`?** The indexed columns (`poc`, `vah`, `val`, `rotation_factor`, `profile_shape`, `opening_type`) enable fast SQL queries for RL feature selection (e.g., "all D-shape sessions"). The `session_json` blob preserves the full `letters` dict and `tpo_counts` needed to reconstruct the visual histogram or feed detailed RL features.

### API Endpoints

**GET `/api/trading/market/tpo`** — Historical batch (for RL)

| Param | Default | Description |
|-------|---------|-------------|
| symbol | NQ | Symbol |
| days | 30 | Number of historical days |

Returns:
```json
{
  "sessions": [
    {
      "date": "2026-03-19",
      "poc": 21775.0, "vah": 21812.5, "val": 21737.25,
      "rotation_factor": 3,
      "profile_shape": "D",
      "opening_type": "OTD",
      "opening_direction": "up",
      "ib_high": 21800.0, "ib_low": 21757.5,
      "upper_excess": 2, "lower_excess": 3,
      "session_high": 21850.0, "session_low": 21700.0,
      "tpo_counts": {"21700.0": 1, "21700.25": 1, "...": "..."},
      "letter_map": {"21775.0": ["D","E","F","G","H","I","J"], "...": "..."},
      "single_prints": [21850.0, 21700.0]
    }
  ],
  "symbol": "NQ",
  "count": 30
}
```

**GET `/api/trading/market/tpo/live`** — Today's developing profile

| Param | Default | Description |
|-------|---------|-------------|
| symbol | NQ | Symbol |

Returns same structure as a single session object. Computed on-the-fly: fetch today's 1m candles from `market_candles`, aggregate to 30m via `_aggregate_bars_30m()` (already exists in `market_service.py`), call `build_full_tpo_profile()`. Cache result for 60 seconds using the same `_cache` dict pattern used by `get_volume_profile_curve()`.

### Backfill CLI

```bash
python -m src.app backfill-tpo --days 90 --symbol NQ
```

For each historical session date:
1. Fetch 1m candles from `market_candles` for the Globex session window
2. Aggregate to 30m bars
3. Call `build_full_tpo_profile()`
4. Serialize and insert into `market_tpo_sessions`
5. Skip dates that already have a row

### Auto-Store

After the existing `compute_session()` in `market_service.py` finishes (which already computes TPO), also store the enriched profile to `market_tpo_sessions`. This piggybacks on the existing session computation flow — no new scheduler hook needed.

## Frontend

### Types (`frontend/src/types/market.ts`)

```typescript
interface TPOLiveProfile {
  poc: number;
  vah: number;
  val: number;
  ib_high: number;
  ib_low: number;
  rotation_factor: number;
  profile_shape: string;       // "b", "p", "D", "B"
  opening_type: string;        // "OD", "OTD", "ORR", "OA"
  opening_direction: string;   // "up", "down", "neutral"
  upper_excess: number;
  lower_excess: number;
  session_high: number;
  session_low: number;
  tpo_counts: Record<string, number>;  // price string → count
  single_prints: number[];
}
```

### API (`frontend/src/services/api.ts`)

```typescript
getTpoLive(symbol?: string): Promise<TPOLiveProfile>
getTpoHistory(symbol?: string, days?: number): Promise<{sessions: TPOLiveProfile[], count: number}>
```

### CandleChart Overlay

Orange TPO histogram on the right edge of the candle chart, same rendering approach as the existing VP overlay:
- Horizontal bars, width proportional to TPO count at each price level
- tPOC bar rendered brighter/wider, tVAH/tVAL marked with accent
- Semi-transparent (alpha ~0.3)
- Drawn via canvas overlay (same code path as VP, toggled independently)

TPO level lines on the candle chart:
- tPOC: solid orange line
- tVAH/tVAL: dashed orange lines
- Single prints: small dotted orange markers

All togglable via BookSnapshot eye-toggles. Hidden state persisted via existing `usePersistedState('l1-hidden-levels')`.

### BookSnapshot — TPO Section

New collapsible section below existing VP sections:

```
TPO Profile                          👁
├── tPOC     21,775.00              👁
├── tVAH     21,812.50              👁
├── tVAL     21,737.25              👁
├── Shape    D (normal)
├── Opening  OTD ↑
├── Rotation +3
├── IB Range 42.50
├── Single prints: 21850, 21700
├── Upper excess: 2 ticks
└── Lower excess: 3 ticks
```

Group eye-toggle hides the entire TPO overlay + all level lines. Individual toggles for tPOC/tVAH/tVAL lines.

### L1Page Data Flow

Fetch `/api/trading/market/tpo/live` on the same 60s interval as the expanded session. Pass TPO data to both CandleChart (for overlay) and BookSnapshot (for stats section).

## Files Changed

### Modified Files
- `backend/src/market_data/tpo.py` — Extend `TPOProfile` dataclass, add `classify_opening_type()`, add `build_full_tpo_profile()`, update `classify_tpo_shape()` with B-shape detection, remove deprecated `compute_rotation_factor`, update `detect_excess` to return tick counts
- `backend/src/db/models.py` — Add `MarketTPOSession` model
- `backend/src/services/market_service.py` — Add `get_tpo_live()`, `get_tpo_history()`, store TPO after session computation
- `backend/src/api/routes/market.py` — Add `/tpo` and `/tpo/live` endpoints
- `backend/src/app.py` — Add `backfill-tpo` CLI command
- `backend/src/rl/data/replay_engine.py` — Update imports (remove `compute_rotation_factor` from tpo, use new `TPOProfile` fields)
- `backend/src/market_data/setups/detector.py` — No changes needed (only uses `TPOProfile` type, new fields have defaults)
- `frontend/src/types/market.ts` — Add `TPOLiveProfile` type
- `frontend/src/services/api.ts` — Add `getTpoLive()`, `getTpoHistory()`
- `frontend/src/components/Terminal/pages/CandleChart.tsx` — Add TPO overlay rendering
- `frontend/src/components/Terminal/pages/BookSnapshot.tsx` — Add TPO stats section with toggles
- `frontend/src/components/Terminal/pages/L1Page.tsx` — Fetch TPO data, pass to children

### New Files
- `backend/tests/test_tpo_extended.py` — Tests for new TPO functions (opening type, B-shape, excess counts, build_full_tpo_profile)

### Handling Edge Cases
- **Weekends/holidays**: Sessions with no candle data are simply skipped (no row in `market_tpo_sessions`)
- **Partial sessions** (e.g., early close): Computed from available bars. `opening_type` defaults to "OA" if < 4 periods.
- **Beyond 26 periods**: Fix the existing bug in `compute_tpo_profile` where periods > 26 all get letter "Z". Replace with proper extension: AA, AB, AC, ..., AZ, BA, BB, ... (base-26 with A=0). A full Globex session = 46 periods, so we need up to "AT". Implement as a helper `_period_letter(index: int) -> str` in `tpo.py`.
- **Live profile early in session**: Returns whatever periods are available. Shape/opening type may be "OA"/"balanced" until enough data accumulates.
