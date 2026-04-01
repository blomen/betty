# Zone-Based Level Consolidation — Design Spec

**Date:** 2026-03-30
**Status:** Draft
**Scope:** Full pipeline — replay_engine, episode_builder, observation, session_manager, level_monitor, config

## Problem

The RL agent treats each of 27+ level types as independent signals. When levels cluster (VWAP_SD1 at 4514.25, DAILY_POC at 4514.50, TPOC at 4515.00), the system fires separate episodes for each — producing redundant training data with nearly identical market context but different `level_type` one-hot encodings. At inference, clustered levels produce rapid-fire, potentially conflicting signals.

**Specific failures:**

1. **Redundant episodes** — same price action generates N episodes (one per clustered level) with identical orderflow/structure but different one-hot labels. The model learns each level type independently instead of learning zone composition.
2. **No composition signal** — the 25-dim one-hot encodes "which single level was touched" but cannot express "POC + VWAP cluster". The 8-dim confluence features count neighbors but not *which types* neighbor.
3. **Inference noise** — live `level_monitor.py` fires per-level with no cooldown equivalent to replay's 30s gap. Clustered levels produce multiple signals within ticks of each other.

## Solution

Merge nearby levels into **zones** before episode generation and inference. One zone entry = one episode = one signal.

## Zone Builder

### Data Structures

```python
@dataclass
class Zone:
    center_price: float          # mean of member level prices
    upper_bound: float           # max member price + half-radius
    lower_bound: float           # min member price - half-radius
    members: list[ZoneMember]    # individual levels in this zone
    composition: list[float]     # 27-dim multi-hot (1.0 if LevelType present)
    width_ticks: float           # (upper - lower) / TICK_SIZE
    member_count: int
    hierarchy_score: float       # weighted importance of member types

@dataclass
class ZoneMember:
    name: str
    level_type: LevelType
    price: float
```

### Hierarchy Weights

Level types ranked by structural importance for `hierarchy_score`:

| Weight | Level Types |
|--------|-------------|
| 1.0 | DAILY_POC, WEEKLY_POC, MONTHLY_POC, NAKED_POC |
| 0.9 | VWAP, PDH, PDL |
| 0.8 | DAILY_VAH, DAILY_VAL, TPOC |
| 0.7 | WEEKLY_VAH, WEEKLY_VAL, MONTHLY_VAH, MONTHLY_VAL |
| 0.6 | NYIB_HIGH, NYIB_LOW, TVAH, TVAL |
| 0.5 | VWAP_SD1, TOKYO_HIGH, TOKYO_LOW, TIBH, TIBL |
| 0.4 | VWAP_SD2 |
| 0.3 | VWAP_SD3 |

`hierarchy_score = sum(weight for each member) / max_possible` normalized to [0, 1].

### Clustering Algorithm

**Greedy sequential merge with ATR-adaptive radius:**

```
Input:  levels = [(name, LevelType, price), ...], session_atr: float
Output: zones = [Zone, ...]

ATR_FRACTION = 0.05
radius = ATR_FRACTION * session_atr   # e.g. 0.05 × 40pts = 2.0 points

1. Sort levels by price ascending
2. Initialize first zone with levels[0]
3. For each subsequent level:
   a. If |level.price - current_zone.last_member.price| <= radius:
      → add to current zone
   b. Else:
      → finalize current zone, start new zone with this level
4. Finalize last zone
5. For each zone:
   - center_price = mean(member prices)
   - upper_bound = max(member prices) + radius/2
   - lower_bound = min(member prices) - radius/2
   - composition = multi-hot over 27 LevelTypes
   - width_ticks = (upper - lower) / TICK_SIZE
   - hierarchy_score = sum(weights) / sum(all_weights)
```

**Complexity:** O(N log N) sort + O(N) merge. Trivial for ~30 levels per session.

**Singleton zones:** A level with no neighbors within radius becomes a single-member zone. composition is effectively a one-hot — same information as today. No data loss.

### ATR Computation

Use the same ATR already available in replay_engine's candle data:
- 14-period ATR on 30-minute candles (from `candles_30m`)
- Fallback: `(session_high - session_low)` if insufficient candle history (first 30 mins of session)
- In level_monitor (live): use rolling ATR from CandleAggregator

### Rebuild Frequency

Zones are rebuilt whenever `_rebuild_active_levels()` runs (on new candle close). Level prices shift (VWAP drifts, VP updates) → zone membership can change. This is fine — zones represent the *current* structural landscape.

## Zone Touch Detection

### Replay Path (`replay_engine.py`)

Replace `_check_level_touch()` with `_check_zone_entry()`:

```python
def _check_zone_entry(self, price: float) -> list[Zone]:
    """Detect newly-entered zones with debouncing."""
    newly_entered: list[Zone] = []
    still_inside: set[str] = set()

    for zone in self._active_zones:
        inside = zone.lower_bound <= price <= zone.upper_bound
        key = f"zone_{round(zone.center_price / TICK_SIZE) * TICK_SIZE}"

        if inside:
            still_inside.add(key)
            if key not in self._zone_keys:
                self._zone_keys.add(key)
                newly_entered.append(zone)

    # Clear debounce for zones price has exited
    self._zone_keys -= (self._zone_keys - still_inside)
    return newly_entered
```

**Cooldown:** Keep existing 30s minimum between episodes. Now between *zone entries* — naturally more spaced since zones are wider than individual levels.

### Live Path (`level_monitor.py`)

Same state machine (WATCHING → APPROACHING → AT_LEVEL) but on zones instead of individual levels:
- APPROACHING: price within `APPROACHING_TICKS` of zone boundary
- AT_LEVEL: price inside zone bounds
- TRIGGERED: zone entry event fired
- REJECTED: price exits zone without triggering (moved away)

## Observation Vector Changes

### Current (167 dims)

| Segment | Dims | Description |
|---------|------|-------------|
| Level type one-hot | 25 | Single LevelType |
| Orderflow | 21 | Delta, absorption, initiative |
| Structure + session | 23 | Distance to bands/levels |
| TPO per-session | 26 | Time-at-price |
| Candle window | 15 | Last 5 candles |
| Confluence | 8 | Nearby level clustering |
| Macro | 7 | VIX, DXY, yields |
| Setup | 14 | Pattern detection |
| Micro | 20 | Tick-level context |
| Approach direction | 1 | Up/down |
| Execution context | 7 | Follow-through, ATR |

### Proposed (~170 dims)

| Segment | Dims | Change |
|---------|------|--------|
| **Zone composition (multi-hot)** | **27** | Replaces 25-dim one-hot. All 27 LevelTypes represented. Multiple bits active for clustered zones. |
| Orderflow | 21 | Unchanged |
| Structure + session | 23 | Unchanged |
| TPO per-session | 26 | Unchanged |
| Candle window | 15 | Unchanged |
| **Zone features** | **3** | NEW: zone_width_ticks, zone_member_count, zone_hierarchy_score |
| **Confluence (simplified)** | **5** | Reduced from 8. Remove redundant level-count features (now in zone features). Keep: nearest_higher_zone_dist, nearest_lower_zone_dist, fvg_overlap, fvg_width_ticks, single_print_overlap |
| Macro | 7 | Unchanged |
| Setup | 14 | Unchanged |
| Micro | 20 | Unchanged |
| Approach direction | 1 | Unchanged |
| Execution context | 7 | Unchanged |

**Net change:** 25 + 8 = 33 old → 27 + 3 + 5 = 35 new = **+2 dims** (167 → 169 total).

### Zone Feature Encoding

```python
def encode_zone(zone: Zone) -> list[float]:
    """3 features describing the zone itself."""
    return [
        min(zone.width_ticks / 50.0, 1.0),   # normalized width
        min(zone.member_count / 10.0, 1.0),   # normalized count
        zone.hierarchy_score,                   # already 0-1
    ]
```

### Simplified Confluence Encoding

```python
def encode_zone_confluence(
    zone: Zone,
    all_zones: list[Zone],
    fvgs: list,
    single_print_zones: list,
) -> list[float]:
    """5 features: inter-zone distances + structural overlap."""
    center = zone.center_price

    higher = [z for z in all_zones if z.center_price > center]
    lower = [z for z in all_zones if z.center_price < center]

    nearest_higher = min((z.center_price - center for z in higher), default=50 * TICK_SIZE)
    nearest_lower = min((center - z.center_price for z in lower), default=50 * TICK_SIZE)

    fvg_overlap, fvg_width = _check_fvg_overlap(center, fvgs)
    sp_overlap = _check_single_print_overlap(center, single_print_zones)

    return [
        min(nearest_higher / TICK_SIZE / 50.0, 1.0),
        min(nearest_lower / TICK_SIZE / 50.0, 1.0),
        fvg_overlap,
        min(fvg_width / 20.0, 1.0),
        sp_overlap,
    ]
```

## Episode Builder Changes

### Touch Price

Use `zone.center_price` as the episode's touch price. This is where the stop distance and velocity measurements originate.

### Trailing Through Zones

`_find_trail_levels()` currently finds individual levels ahead of entry for the trailing bonus. Change to find *zones* ahead:

```
trail_targets = [z.center_price for z in sorted_zones
                 if (z.center_price > entry_price) == is_long_direction]
```

Each zone captured = +0.5R bonus, same as before. Difference: clustered levels that were previously 3 separate trail targets become 1 zone target. This is correct — trailing through a 2-point cluster shouldn't count as 3 separate captures.

### Episode Count Impact

Current: 518,805 episodes (many from clustered level touches on same price action).
Expected: ~60-70% of current count (rough estimate). Clustered 2-3 level touches merge into single zone entries. Singleton zones produce same count as before.

This reduction is healthy — we're removing redundant episodes, not losing information.

## Session Manager Changes

### `on_zone_entry()` replaces `on_level_touch()`

```python
def on_zone_entry(self, state: dict, zone: Zone) -> dict:
    """Process zone entry event. Same logic as on_level_touch but zone-aware."""
    # Circuit breakers — unchanged
    # Build observation with zone composition instead of level one-hot
    # DQN inference — unchanged (just different input dims)
    # Direction, confidence, sizing — unchanged
    # Stop price: compute from zone boundary, not single level
    #   LONG entry: stop = zone.lower_bound - STOP_TICKS * TICK_SIZE
    #   SHORT entry: stop = zone.upper_bound + STOP_TICKS * TICK_SIZE
    # Return signal dict — unchanged format
```

### Stop Price from Zone Bounds

Current: stop = `level_price ∓ STOP_TICKS * TICK_SIZE`
Proposed: stop computed from zone boundary (the edge of the support/resistance area), not center. This gives the zone room to work — the model's stop_head prediction adjusts from the boundary.

## Level Monitor Changes (Live)

### Zone-Aware State Machine

```python
class MonitoredZone:
    zone: Zone
    status: LevelStatus  # WATCHING → APPROACHING → AT_LEVEL → TRIGGERED
    touched_at: float | None
    approach_price: float | None
```

Replace per-level tracking with per-zone tracking:
- `on_tick()` checks distance to zone boundaries (not individual levels)
- APPROACHING when price within 15 ticks of zone boundary
- AT_LEVEL when price inside zone bounds
- Fire `_on_zone_touched()` once per zone entry
- Debounce until price exits zone + REJECT_TICKS (20 ticks)

### `_build_rl_state()` Changes

Instead of mapping a single level name to LevelType, pass the full Zone object:
- `state["zone"] = zone` (Zone dataclass)
- `state["all_zones"] = all_zones` (for inter-zone confluence)
- Remove `state["level_type"]` (replaced by zone composition)

## Config Changes

### New Constants in `config.py`

```python
# Zone consolidation
ATR_FRACTION = 0.05          # zone radius as fraction of session ATR
ATR_PERIOD = 14              # ATR lookback (30m candles)
MIN_ZONE_RADIUS_TICKS = 4    # floor: never merge tighter than 1 point
MAX_ZONE_RADIUS_TICKS = 20   # cap: never merge wider than 5 points
```

Floor and cap prevent degenerate behavior:
- Very quiet sessions (ATR=8pts): radius = 0.4pts → floor at 1pt
- Extremely volatile sessions (ATR=200pts): radius = 10pts → cap at 5pts

### LevelType Enum — Unchanged

All 27 LevelTypes remain. They're used in the multi-hot composition vector. No additions or removals needed.

### Observation Dimensions

```python
# Update observation segment sizes
ZONE_COMPOSITION_DIM = 27    # was LEVEL_TYPE_DIM = 25
ZONE_FEATURES_DIM = 3        # new
CONFLUENCE_DIM = 5            # was 8
```

## New File Structure

```
backend/src/rl/
├── zone_builder.py          # NEW: Zone, ZoneMember, build_zones()
├── features/
│   ├── observation.py       # MODIFIED: zone composition + zone features
│   ├── level_features.py    # MODIFIED: encode_zone_confluence() replaces encode_confluence()
│   └── ...                  # other feature files unchanged
├── data/
│   ├── replay_engine.py     # MODIFIED: _check_zone_entry(), zone-aware state
│   ├── episode_builder.py   # MODIFIED: trail through zones
│   └── ...
├── session_manager.py       # MODIFIED: on_zone_entry()
├── config.py                # MODIFIED: new zone constants
└── ...

backend/src/market_data/
├── level_monitor.py         # MODIFIED: MonitoredZone, zone state machine
└── ...
```

## Migration & Backward Compatibility

1. **Full re-replay required** — `rl replay` regenerates all episodes with zone-based observations
2. **New observation dimensions** — old `observations.npy` (518K × 167) replaced with new (N × ~170)
3. **Old checkpoints incompatible** — `dqn_v1.pt` and `dqn_latest.pt` won't load (input dim changed)
4. **Full retrain required** — `rl train` on new episodes
5. **normalizer.json regenerated** — new feature dimensions need new running stats

No phased migration. This is a clean break — old model → new model.

## Verification

After implementation:
1. `rl replay` completes without errors, generates new episode arrays
2. Episode count is ~60-70% of previous (518K) — confirming cluster deduplication
3. Zone composition vectors have varying member counts (not all singletons)
4. `rl train` converges (loss decreasing, val accuracy improving)
5. `rl eval` per-zone metrics show comparable or better performance than per-level
6. `rl backtest` shows fewer trades (deduplication) with similar or better R/trade
