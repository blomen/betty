# AMT Feature Expansion — Design Spec

## Problem

The AMT (Auction Market Theory) section displays hardcoded demo data (`trend_day (72%)`, `OD`, `p_shape`) because:

1. `build_expanded_session()` hardcodes `ml_day_type: None` — never runs the gate classifier
2. Frontend falls back to `DEMO_SESSION` when API returns no ML data
3. The current AMT feature vector (13 dims) is minimal — missing key Dalton concepts
4. Dynamic AMT state (IB extensions, acceptance/rejection, developing day type) isn't tracked

## Goal

Maximize AMT feature vectors for RL agent training by:
- Expanding static AMT features from 13 to 20 dimensions
- Adding a new dynamic AMT feature module with 20 dimensions that updates in real-time
- Fixing the broken data pipeline so real AMT data reaches both RL and UI
- Total AMT contribution: 13 → 40 features

## Architecture: Two Modules

### Module A: `amt_features.py` — Static Session Features (13 → 20 dims)

Set once per session after IB completes. Requires `session_levels`, `volume_profile`, `session_context`.

| Index | Feature | Source | Encoding |
|-------|---------|--------|----------|
| 0-5 | Dalton day type (6-way one-hot) | IB range vs daily range + extensions | existing |
| 6-9 | Opening type (4-way one-hot: OD/OTD/ORR/OA) | Open vs prior VA + IB directional ratio | existing |
| 10 | Range extension | `(daily_range - ib_range) / ib_range`, clipped 0-1 | existing |
| 11 | VA overlap with prior session | Overlap fraction of today's VA vs yesterday's | existing |
| 12 | Value migration | POC vs prior VA: -1 below, 0 inside, +1 above | existing |
| **13** | **IB range percentile** | Today's IB range vs 20-day rolling IB ranges, 0-1 | new |
| **14** | **Overnight gap vs IB** | `(open - prior_close) / ib_range`, clipped ±1. Measures gap context | new |
| **15** | **Open vs prior POC** | `(open - prev_poc) / TICK / 200`, clipped ±1 | new |
| **16** | **Composite VA overlap (5-day)** | Today's VA overlap with 5-day composite VA, 0-1 | new |
| **17** | **Prior session poor high** | Yesterday had unfinished auction at high: 0 or 1 | new |
| **18** | **Prior session poor low** | Yesterday had unfinished auction at low: 0 or 1 | new |
| **19** | **Prior session excess quality** | `(upper_excess - lower_excess) / 10`, clipped ±1 | new |

### Module B: `amt_dynamics_features.py` — Real-Time Evolving Features (20 dims, NEW)

Updates on every tick via `AMTDynamicsTracker`. Snapshots into state dict at DQN inference time.

| Index | Feature | What It Captures | Encoding |
|-------|---------|-----------------|----------|
| 0 | IB extension count up | Times price broke above IB high | count / 5, clipped 0-1 |
| 1 | IB extension count down | Times price broke below IB low | count / 5, clipped 0-1 |
| 2 | IB extension magnitude | Furthest extension as multiple of IB range | `max_ext / ib_range`, clipped 0-3 / 3 |
| 3 | IB extension net direction | One-sided vs two-sided extensions | +1 all up, -1 all down, 0 both |
| 4 | Developing day type (ordinal) | Current Dalton classification as session evolves | non_trend=0, normal=0.2, neutral=0.4, normal_var=0.6, trend=0.8, double=1.0 |
| 5 | Day type confidence | How clearly established (range_ratio distance from thresholds) | 0-1 continuous |
| 6 | Responsive activity ratio | Recent volume inside VA / total recent volume | 0-1 |
| 7 | Initiative activity ratio | Recent volume outside VA / total recent volume | 0-1 |
| 8 | VA edge acceptance (high) | Periods price spent above VAH (sustained = acceptance) | periods / 6, clipped 0-1 |
| 9 | VA edge rejection (high) | Price probed VAH and snapped back within 2 periods | 0 or 1 |
| 10 | VA edge acceptance (low) | Periods price spent below VAL | periods / 6, clipped 0-1 |
| 11 | VA edge rejection (low) | Price probed VAL and snapped back | 0 or 1 |
| 12 | Developing POC migration speed | Rate of POC movement (stable = balance, fast = initiative) | `poc_delta / ib_range` over last 5 periods, clipped 0-1 |
| 13 | VA width expansion rate | Change in VA width over last 5 periods | `delta_width / ib_range`, clipped ±1 |
| 14 | Balance area duration | Periods in current rotation bracket | periods / 12, clipped 0-1 |
| 15 | Balance area width | Width of current bracket in ticks | width / (2 * ib_range), clipped 0-1 |
| 16 | Single print proximity | Distance to nearest unfilled single print zone | signed ticks / 200, clipped ±1 |
| 17 | Excess quality at high | Consecutive single-print levels from session high | count / 10, clipped 0-1 |
| 18 | Excess quality at low | Consecutive single-print levels from session low | count / 10, clipped 0-1 |
| 19 | OTF activity signal | Large delta outside VA = institutional flow | `otf_delta / total_volume`, clipped 0-1 |

## AMTDynamicsTracker Class

Lightweight state tracker that lives on `LevelMonitor`. No DB queries, no heavy compute.

```python
class AMTDynamicsTracker:
    """Track evolving AMT state from tick-by-tick updates."""

    def __init__(self):
        # Set by initialize() from session data
        self.ib_high: float = 0
        self.ib_low: float = 0
        self.ib_range: float = 0
        self.vah: float = 0
        self.val: float = 0
        self.poc: float = 0
        self.single_prints: list[tuple[float, float]] = []

        # Tracked state
        self.ib_ext_up_count: int = 0
        self.ib_ext_down_count: int = 0
        self.ib_max_ext_up: float = 0
        self.ib_max_ext_down: float = 0
        self.session_high: float = 0
        self.session_low: float = 0
        self.vol_inside_va: int = 0
        self.vol_outside_va: int = 0
        self.va_probe_high_periods: int = 0
        self.va_probe_low_periods: int = 0
        self.va_rejection_high: bool = False
        self.va_rejection_low: bool = False
        self.poc_history: deque[float] = deque(maxlen=12)
        self.va_width_history: deque[float] = deque(maxlen=12)
        self.balance_bracket_high: float = 0
        self.balance_bracket_low: float = 0
        self.balance_start_period: int = 0
        self.current_period: int = 0
        self.delta_outside_va: int = 0
        self.total_volume: int = 0

    def initialize(self, session_data: dict) -> None:
        """Set IB/VA/POC from compute_session output."""

    def update(self, price: float, size: int, side: str) -> None:
        """Called on every tick. Updates all counters."""

    def on_period_close(self, period_high: float, period_low: float,
                        developing_poc: float, developing_vah: float,
                        developing_val: float) -> None:
        """Called every 30 min. Updates period-based metrics."""

    def snapshot(self) -> dict:
        """Return current state as dict for RL state assembly."""
```

### Update Flow

```
tick arrives → LevelMonitor.on_tick()
    → amt_tracker.update(price, size, side)
        - IB extension detection (new high/low beyond IB)
        - Volume inside/outside VA accumulation
        - Delta outside VA accumulation
        - Session high/low tracking

30-min period closes → amt_tracker.on_period_close()
    - Developing day type reclassification
    - VA edge acceptance/rejection check
    - POC migration speed update
    - VA width expansion tracking
    - Balance bracket detection

DQN inference fires → _build_rl_state()
    state["amt_dynamics"] = amt_tracker.snapshot()
    → extract_amt_dynamics_features(state["amt_dynamics"]) → 20-dim vector
```

## Data Pipeline Fixes

### 1. Session context enrichment (in `compute_session`)

Add to `session_context` dict passed to `LevelMonitor.set_session_context()`:

```python
session_context = {
    # existing keys...
    # NEW:
    "ib_range_percentile": ib_pct,           # from repo.get_historical_ib_ranges()
    "overnight_gap": overnight_gap,           # (open - prior_close) / ib_range
    "open_vs_prior_poc": open_vs_poc,         # (open - prev_poc) / TICK / 200
    "composite_va": {"vah": ..., "val": ...}, # 5-day composite from repo
    "prior_poor_high": prev_session.poor_high,
    "prior_poor_low": prev_session.poor_low,
    "prior_excess_quality": upper_ex - lower_ex,  # from prior TPO
}
```

### 2. MarketRepo additions

```python
def get_recent_sessions(self, symbol: str, days: int = 5) -> list[MarketSession]:
    """Return last N sessions for composite VA computation."""

def get_historical_ib_ranges(self, symbol: str, days: int = 20) -> list[float]:
    """Return IB ranges for percentile calculation."""
```

### 3. AMTDynamicsTracker initialization

`LevelMonitor.set_session_context()` calls `self._amt_tracker.initialize(ctx)` with IB/VA/POC from session data.

### 4. LevelMonitor tick integration

The existing tick handler in `LevelMonitor` already processes every tick for level proximity. Add `self._amt_tracker.update(price, size, side)` to the same path.

For 30-min period boundaries: track period count from session start. When `current_period` increments, call `on_period_close()` with developing profile values from the live volume profile.

## Observation Vector Changes

```
Before (zone mode):
  level(25) + orderflow(21) + structure(39) + tpo(38) + candles(15)
  + zone(4) + confluence(5) + macro(11) + exchange(5) + setup(14)
  + amt(13) + micro(20) + approach(1) + execution(7) = 218

After (zone mode):
  level(25) + orderflow(21) + structure(39) + tpo(38) + candles(15)
  + zone(4) + confluence(5) + macro(11) + exchange(5) + setup(14)
  + amt(20) + amt_dynamics(20) + micro(20) + approach(1) + execution(7) = 245
```

- `OBSERVATION_DIM`: 218 → 245
- `AUGMENTED_OBSERVATION_DIM`: 234 → 261
- Retrain required — existing model weights incompatible

## UI Fix (Secondary)

1. Remove `DEMO_SESSION` fallback in `TradingContainer.tsx` — show empty/loading state when no real data
2. Wire `build_expanded_session()` to run gate classifier prediction (code exists, just returns `None`)
3. BookSnapshot AMT section reads real `session.market_type`, `opening_type`, `distribution_type`
4. Add live AMT dynamics to SSE stream for real-time UI updates (rotation factor, day type evolution, acceptance/rejection indicators)

## Backfill Strategy

- **Static features [13-19]**: Fully backfillable from DB session history (IB ranges, poor high/low, excess counts all stored)
- **Dynamic features [0-19]**: Approximate from 1-min bars where available. IB extensions, volume inside/outside VA, period-based metrics can be reconstructed. Tick-level features (OTF delta, responsive vs initiative) zero-filled for historical data — agent learns these from live training only

## What NOT to Build

- No composite profile visualization (just the 5-day VA overlap feature)
- No market state machine service (developing day type ordinal captures this)
- No multi-day POC migration feature (naked POCs in structure_features already handle this)
- No separate balance area detector service (balance duration/width features are sufficient)
- No changes to GBT model — AMT features only feed the DQN observation vector
