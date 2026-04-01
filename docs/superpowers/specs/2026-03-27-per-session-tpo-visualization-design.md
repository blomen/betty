# Per-Session TPO Letter Grid Visualization

## Problem

The chart currently renders a single composite TPO histogram (all sessions merged) on the right edge of the chart. This obscures session-specific structure — Tokyo may show a p-shape while London shows a b-shape, but the composite blends them into noise. The backend already computes per-session TPO profiles (`compute_session_tpos()`), but the data isn't exposed to the frontend or rendered visually.

## Solution

Replace the composite TPO histogram with 3 classic TPO letter grids — one anchored inside each session box (Tokyo, London, NY). Each grid shows actual TPO letters (A, B, C...) at each price level, right-aligned within the session box. POC/VAH/VAL lines extend rightward as dashed lines. Metadata (shape, IB range, opening type) displayed in a footer. All elements toggleable via the existing `hiddenLevels` system.

## Backend: New API Endpoint

### `GET /api/trading/market/tpo/sessions`

Returns per-session TPO profiles for today (developing) with full letter data.

**Response shape:**

```json
{
  "date": "2026-03-27",
  "sessions": {
    "tokyo": {
      "letters": {"23940.0": ["A","B","C","D","E"], "23935.0": ["B","C","D","E"], ...},
      "poc": 23940.0,
      "vah": 23950.0,
      "val": 23930.0,
      "ib_high": 23955.0,
      "ib_low": 23925.0,
      "ib_valid": true,
      "shape": "p-shape",
      "opening_type": "ORR",
      "opening_direction": "down",
      "poor_high": false,
      "poor_low": true,
      "upper_excess": 3,
      "lower_excess": 0,
      "session_high": 23960.0,
      "session_low": 23920.0
    },
    "london": { ... },
    "ny": { ... }
  },
  "poc_migration_tokyo_london": -8.0,
  "poc_migration_london_ny": 12.0
}
```

**Implementation:** New `MarketService.get_session_tpos()` method — same candle fetch pattern as `get_tpo_live()`, but calls `compute_session_tpos()` and returns the full `letters` dict from each session's `compute_tpo_profile()`. The existing `_build_session_tpo()` in `tpo.py` already calls `compute_tpo_profile()` but discards letters; modify it to preserve them.

**Important:** `_split_bars_by_session()` requires each bar to have a `ts` key (datetime). The existing `aggregate_bars_30m()` works on BarData objects without timestamps. The new service method must build 30m bars that preserve the timestamp of the first 1m bar in each chunk (same approach used in `replay_engine.py`).

**Caching:** Same 60-second cache as existing `get_tpo_live()`.

## Backend: `tpo.py` Changes

### `SessionTPO` dataclass — add fields

```python
@dataclass
class SessionTPO:
    session: str
    poc: float
    vah: float
    val: float
    shape: str
    ib_high: float
    ib_low: float
    ib_valid: bool
    poor_high: bool
    poor_low: bool
    # New fields:
    letters: dict[float, list[str]]   # price → [A, B, C, ...]
    tpo_counts: dict[float, int]      # price → count
    upper_excess: int
    lower_excess: int
    session_high: float
    session_low: float
    opening_type: str
    opening_direction: str
```

### `_build_session_tpo()` — return enriched data

Currently builds a `TPOProfile` via `compute_tpo_profile()` then discards it. Change to also call `classify_opening_type()`, `detect_excess()`, and copy `letters`, `tpo_counts`, `session_high`, `session_low` from the profile.

## Frontend: Types

### New `SessionTPOData` interface in `types/market.ts`

```typescript
export interface SessionTPOData {
  letters: Record<string, string[]>;  // price → letters
  poc: number;
  vah: number;
  val: number;
  ib_high: number;
  ib_low: number;
  ib_valid: boolean;
  shape: string;
  opening_type: string;
  opening_direction: string;
  poor_high: boolean;
  poor_low: boolean;
  upper_excess: number;
  lower_excess: number;
  session_high: number;
  session_low: number;
}

export interface SessionTPOResponse {
  date: string;
  sessions: {
    tokyo: SessionTPOData | null;
    london: SessionTPOData | null;
    ny: SessionTPOData | null;
  };
  poc_migration_tokyo_london: number;
  poc_migration_london_ny: number;
}
```

## Frontend: API

Add to `api.ts`:

```typescript
getSessionTPO(symbol?: string): Promise<SessionTPOResponse>
// GET /api/trading/market/tpo/sessions?symbol=NQ
```

## Frontend: Canvas Rendering in `CandleChart.tsx`

### Data flow

1. Fetch `SessionTPOResponse` on mount (same pattern as VP overlays)
2. Store in `sessionTPORef`
3. In `drawOverlays()`, after session boxes are drawn, render the letter grid inside each box

### Letter grid rendering algorithm

For each session (tokyo, london, ny):

1. **Get box bounds** — reuse the `SessionBox` already computed by `buildSessionBoxes()`
2. **Map price levels to Y coordinates** — use `pSeries.priceToCoordinate(price)` for each price in `letters`
3. **Compute right-edge X anchor** — `box.endEpoch` mapped to X coordinate via `timeScale.timeToCoordinate()`, then subtract 4px padding
4. **Render letters right-aligned** — for each price level, use `ctx.textAlign = 'right'` to draw the letter string (e.g., "A B C D E") from the anchor X. Use session color with varying opacity:
   - POC row: full opacity, subtle background highlight
   - Value area rows (between VAH and VAL): 0.7 opacity
   - Outside value area: 0.4 opacity
5. **POC marker** — draw "◄" marker after the POC row's letters
6. **Font**: `9px monospace`, letter-spacing 1px

### Level lines extending from session box

After the letter grid, draw dashed lines extending from the session box rightward to day end (22:00 CET), same pattern as existing TKY H/L lines:

| Line | Color | Style | Label |
|------|-------|-------|-------|
| POC | Session color, 0.6 alpha | dashed [4,3] | `TKY POC` / `LDN POC` / `NY POC` |
| VAH | Session color, 0.4 alpha | dashed [2,3] | `TKY VAH` / `LDN VAH` / `NY VAH` |
| VAL | Session color, 0.4 alpha | dashed [2,3] | `TKY VAL` / `LDN VAL` / `NY VAL` |

### Session metadata footer

At the bottom of each session box, render a small text line:

```
p-shape  IB:138  ORR↓  ex:3/0
```

- Shape classification
- IB range in ticks (`ib_high - ib_low` / tick_size)
- Opening type + direction arrow
- Excess counts (upper/lower)

Font: `8px monospace`, session color at 0.5 opacity.

## Frontend: Toggle Keys

### `BookSnapshot.tsx` LEVEL_GROUPS update

Replace existing `tpo` group and add per-session groups:

```typescript
const LEVEL_GROUPS: Record<string, string[]> = {
  // ... existing groups ...
  tpo_tokyo:  ['tpo_tky_letters', 'tpo_tky_poc', 'tpo_tky_vah', 'tpo_tky_val'],
  tpo_london: ['tpo_ldn_letters', 'tpo_ldn_poc', 'tpo_ldn_vah', 'tpo_ldn_val'],
  tpo_ny:     ['tpo_ny_letters',  'tpo_ny_poc',  'tpo_ny_vah',  'tpo_ny_val'],
};
```

This lets users toggle:
- Entire session TPO (letters + lines) via group toggle
- Individual POC/VAH/VAL lines per session

## Remove Composite TPO

- Remove the existing `tpo_counts` histogram rendering block in `drawOverlays()` (lines ~428-455 of CandleChart.tsx)
- Remove the `tpo` prop from `CandleChart` (the composite `TPOLiveProfile`)
- Remove the old `tpo` group from `LEVEL_GROUPS` (`['t_poc', 't_vah', 't_val', 'vp_tpo']`)
- Keep the `/api/trading/market/tpo/live` endpoint for now (used by RL and the right-panel stats display) but the chart no longer consumes it

## Rendering Edge Cases

- **Session not yet started**: Skip rendering (no data for that session)
- **Developing session**: Show letters accumulated so far; grid grows as new 30m periods complete
- **Weekend/no data**: No boxes rendered, no TPO grid
- **Zoom level**: At very low zoom, letter text becomes unreadable. If box width < 60px, fall back to histogram bars instead of letters (graceful degradation)
- **Scroll-back**: Only render for the most recent day's session boxes (same as current behavior)

## Data Dependencies

```
1m candles (market_candles DB)
  → aggregate_bars_30m()
  → _split_bars_by_session() by CET time
  → compute_tpo_profile() per session (letters, POC, VA, IB)
  → classify_tpo_shape(), classify_opening_type(), detect_excess()
  → SessionTPO with full letters dict
  → JSON response → canvas rendering
```

No new DB tables. No new dependencies. Reuses all existing TPO computation code.
