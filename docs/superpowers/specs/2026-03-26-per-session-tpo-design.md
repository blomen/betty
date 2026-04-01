# Per-Session TPO Profiles

**Date:** 2026-03-26
**Status:** Approved
**Scope:** Replace composite TPO features with per-session (Tokyo/London/NY) TPO profiles in the RL observation vector

## Problem

The RL agent currently receives 13 TPO features from a single composite profile spanning the full trading day (00:00–22:00 CET). This flattens session-level structure — the agent can't distinguish whether value was built by Asian participants, European institutions, or US flow. POC migration across sessions (a strong directional bias signal) is lost entirely.

## Decision

Per-session TPO profiles with non-overlapping CET boundaries. 26 features replace the 13 composite features. Composite profile preserved for frontend charting and setup detectors.

## Session Boundaries (CET, non-overlapping)

| Session | Start | End | Max 30m bars |
|---------|-------|-----|-------------|
| Tokyo | 00:00 | 08:00 | 16 |
| London | 08:00 | 15:30 | 15 |
| NY | 15:30 | 22:00 | 13 |

These differ from `levels.py` session boundaries (which overlap for H/L computation). TPO requires non-overlapping windows so letters don't double-count across sessions.

## Data Model

### SessionTPO

Lightweight per-session profile — only the features that matter for RL:

```python
@dataclass
class SessionTPO:
    session: str          # "tokyo" | "london" | "ny"
    poc: float
    vah: float
    val: float
    shape: str            # "p" | "b" | "d" | "balanced"
    ib_high: float
    ib_low: float
    ib_valid: bool        # False if IB bars have < min TPO count
    poor_high: bool
    poor_low: bool
```

### SessionTPOSet

Container for the three sessions plus cross-session features:

```python
@dataclass
class SessionTPOSet:
    tokyo: SessionTPO | None    # None if session hasn't started
    london: SessionTPO | None
    ny: SessionTPO | None
    poc_migration_tokyo_london: float  # ticks (london.poc - tokyo.poc) / tick_size
    poc_migration_london_ny: float     # ticks (ny.poc - london.poc) / tick_size
```

## Computation

### compute_session_tpos(bars_30m, tick_size, bar_timestamps) → SessionTPOSet

1. Each 30m bar's timestamp is converted to CET via `bar_ts.astimezone(CET)` — never raw UTC hour comparison (DST safety).
2. Bars are split into 3 slices by CET time boundaries.
3. Each slice is re-indexed starting at 0 so `_period_letter(i)` assigns A, B, C... from the start of that session.
4. `compute_tpo_profile(slice, tick_size)` runs on each slice → POC, VAH, VAL, poor_high, poor_low.
5. `classify_tpo_shape(profile)` determines shape per session.
6. IB = first 2 bars of each slice (60 min). IB validity check: if the IB bars have fewer than `MIN_IB_TPO_COUNT` unique price levels touched, `ib_valid = False`.
7. POC migration deltas computed between consecutive sessions (in ticks).
8. Sessions that haven't started yet → None.

## Feature Vector (26 features)

### Per-session block (8 features × 3 sessions = 24)

| # | Feature | Formula | Range |
|---|---------|---------|-------|
| 0 | price_vs_poc | `(price - poc) / tick / 200` | ~[-1, 1] |
| 1 | price_vs_vah | `(price - vah) / tick / 200` | ~[-1, 1] |
| 2 | price_vs_val | `(price - val) / tick / 200` | ~[-1, 1] |
| 3 | shape | p=+1, d=-1, balanced=0 | [-1, 1] |
| 4 | ib_range | `(ib_high - ib_low) / tick / 200`, zeroed if `not ib_valid` | [0, 1] |
| 5 | price_vs_ib_mid | `(price - ib_mid) / tick / 200`, zeroed if `not ib_valid` | ~[-1, 1] |
| 6 | poor_signal | +1 poor_high, -1 poor_low, 0 neither | [-1, 1] |
| 7 | price_position_in_va | continuous, see formula below | unbounded outside VA |

**price_position_in_va formula:**
- `price > vah`: `(price - vah) / (vah - val)` → positive, above VA
- `price < val`: `(price - val) / (vah - val)` → negative, below VA
- `val ≤ price ≤ vah`: `(price - val) / (vah - val) - 0.5` → [-0.5, +0.5] within VA

### Cross-session features (2)

| # | Feature | Formula | Range |
|---|---------|---------|-------|
| 24 | poc_migration_tokyo_london | `(london.poc - tokyo.poc) / tick / 200` | ~[-1, 1] |
| 25 | poc_migration_london_ny | `(ny.poc - london.poc) / tick / 200` | ~[-1, 1] |

### Observation vector layout

Total observation: 146 - 13 + 26 = **159 features**. TPO segment shifts from indices 65-77 (13) to 65-90 (26). All downstream indices shift +13.

## Storage

No schema migration. The existing `market_tpo_sessions.session_json` (JSON column) is extended:

```json
{
  "letters": {"19800.0": ["A", "B"]},
  "poc": 19850.0,
  "session_tpos": {
    "tokyo":  {"poc": 19820, "vah": 19840, "val": 19800, "shape": "balanced", "ib_high": 19830, "ib_low": 19805, "ib_valid": false, "poor_high": false, "poor_low": true},
    "london": {"poc": 19860, "vah": 19880, "val": 19840, "shape": "p", "ib_high": 19870, "ib_low": 19845, "ib_valid": true, "poor_high": true, "poor_low": false},
    "ny":     {"poc": 19890, "vah": 19910, "val": 19870, "shape": "p", "ib_high": 19900, "ib_low": 19875, "ib_valid": true, "poor_high": false, "poor_low": false},
    "poc_migration_tokyo_london": 160.0,
    "poc_migration_london_ny": 120.0
  }
}
```

Composite fields preserved for frontend charting. Backfill recomputes from existing 1-min bars in DB.

## Integration Points

### replay_engine.py (_build_state)

- 30m bars split by CET-converted timestamps (not raw UTC hours)
- `compute_session_tpos()` builds `SessionTPOSet`
- Feature extractor produces 26-dim array
- State dict key: `"session_tpos"` (SessionTPOSet or dict)

### market_service.py (compute_session + get_tpo_live)

- Calls `compute_session_tpos()` alongside existing composite profile
- Embeds `session_tpos` in `session_json` for DB persistence
- Live path: developing profiles update as bars arrive, empty sessions are None

### network.py

- Input dimension: 146 → 159
- First layer: Linear(159, 256) — no other architectural changes

### observation.py

- TPO segment: 13 → 26 features
- Calls `extract_session_tpo_features(session_tpo_set, price)` instead of `extract_tpo_features`
- All downstream feature indices shift +13

### rl/config.py

- Update `OBS_DIM` if constant exists

## What Stays Unchanged

- **Composite TPO profile**: preserved for frontend chart rendering and setup detectors
- **Setup detectors**: continue consuming composite `TPOProfile` object (poor_extreme, ib_break use composite IB which is NY RTH — correct for those patterns)
- **`market_tpo_sessions` table schema**: no migration, JSON-only extension
- **`levels.py` session boundaries**: overlapping H/L boundaries are correct for level computation
- **`amt.py` RTH TPO**: used by `SessionAnalysis`, independent concern

## Migration Notes

- **OBSERVATION_DIM** is computed dynamically from `build_observation()` via a dummy state — no hardcoded constant to update. It will auto-adjust to 159 when the feature vector changes.
- **Existing model checkpoints** (trained on 146-dim input) will be incompatible. A full retrain is required after this change. No checkpoint migration — just retrain.
- **Backfill**: `backfill_tpo_sessions` must be re-run to populate `session_tpos` in existing `session_json` rows. Existing composite fields are preserved.

## Key Invariants

1. Session bar splits use `bar_ts.astimezone(CET)`, never raw UTC hour comparison (DST-safe)
2. Tokyo IB flagged `ib_valid=False` when IB bars have insufficient TPO count (low overnight volume)
3. Empty sessions (not yet started) → None → zeros in feature vector
4. Letters restart at A per session (slice re-indexed from 0)
5. Shape ordinal encoding: p=+1, d=-1, balanced=0
6. `price_position_in_va` is continuous [-0.5, +0.5] within VA, unbounded outside
7. POC migration deltas normalized by tick_size/200 to match other feature scales

## Files Touched

| File | Change |
|------|--------|
| `backend/src/market_data/tpo.py` | Add `SessionTPO`, `SessionTPOSet`, `compute_session_tpos()` |
| `backend/src/rl/features/tpo_features.py` | Replace `extract_tpo_features` → `extract_session_tpo_features` (13→26) |
| `backend/src/rl/features/observation.py` | Update TPO segment 13→26, total 146→159 |
| `backend/src/rl/agent/network.py` | Input dim 146→159 |
| `backend/src/rl/data/replay_engine.py` | Split bars by session (CET), build `SessionTPOSet` |
| `backend/src/services/market_service.py` | Call `compute_session_tpos`, embed in session_json |
| `backend/tests/test_rl_tpo_extensions.py` | Update tests for new feature shape |
