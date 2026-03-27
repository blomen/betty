# RL Trading Agent for NQ Futures

**Date:** 2026-03-19
**Status:** Approved
**Inspiration:** Yoshi's Trackmania RL AI — same architecture, applied to trading

## Overview

A reinforcement learning agent that learns to trade NQ futures at structural level touches. The agent receives an observation vector (~105 floats) representing the full market state at the moment price touches a key level, and outputs one of three actions: LONG, SHORT, or SKIP. It trains via DQN on historical tick data replayed through the existing market data pipeline.

The core insight: Yoshi's AI receives speed/position/road-layout vectors every 0.1s and outputs steering/gas/brake. Our agent receives orderflow/structure/TPO/macro vectors at each level touch and outputs a trade direction. Same architecture, different domain.

## Motivation

- **Unbiased level discovery** — agent learns which level types actually produce tradeable reactions vs noise
- **Pattern recognition beyond human capacity** — ~105-dimensional observation space with subtle cross-feature interactions
- **Yoshi's progression model** — start simple (3 discrete actions, fixed risk), add complexity later (sizing, dynamic exits, session-level control)

## Architecture

### The Trackmania Analogy

| Trackmania (Yoshi) | Firev (Trading) |
|---|---|
| Speed, position, wheel contact | Delta, CVD, volume, spread, body ratio |
| Road centerline deviation | Price vs VWAP (SD), price position in VA |
| Next 3 corners (path encoding) | Top-N structural levels + distances |
| Steering + gas + brake | LONG / SHORT / SKIP |
| Track progress (speed) | R-multiple (P&L / risk) |
| Crashing into walls | Taking bad trades, learning to SKIP |

### Agent

- **Algorithm:** DQN (Deep Q-Network)
- **Network:** 128-128-64 fully connected, ReLU activations
- **Output:** 3 Q-values (LONG, SHORT, SKIP)
- **Action selection:** ε-greedy (ε: 1.0 → 0.05 over 5000 episodes)
- **Framework:** PyTorch (CPU only, ~25k parameters)
- **Future upgrade path:** PPO for continuous action space (Phase 3)

### Episode Structure

- **Trigger:** Price touches any structural level (detected by LevelMonitor during replay)
- **Observation:** ~105-dim vector snapshotted at touch moment
- **Action:** LONG / SHORT / SKIP
- **Outcome:** Forward price action determines reward
- **Reward:** R-multiple (target hit = +2.0, stop hit = -1.0, timeout = 0.0)

### Fixed Risk Parameters (Phase 1)

- **Stop:** 10 ticks ($50/contract)
- **Target:** 20 ticks ($100/contract) → 2:1 R ratio
- **Timeout:** 30 minutes

### Outcome Labeling

| Price action after touch | Optimal action | Reward |
|---|---|---|
| Hit +20 ticks before -10 ticks | LONG | +2.0 |
| Hit -20 ticks before +10 ticks | SHORT | +2.0 |
| Hit +10 stop first (went short scenario) | LONG loses | -1.0 |
| Hit -10 stop first (went long scenario) | SHORT loses | -1.0 |
| Timeout, no target or stop hit | SKIP | 0.0 |

## Data Pipeline

### Fetch

```
Databento Historical API (NQ, GLBX.MDP3)
    │  trades + MBP-1 (top-of-book)
    │  6+ months of tick data
    ▼
Parquet files in data/rl/ticks/
    └── NQ_YYYY-MM.parquet
```

### Replay Engine

Reads ticks chronologically, one session at a time, reconstructing everything the live pipeline computes:

```
Historical Ticks (Parquet)
    │
    ▼
┌──────────────────────────────────────┐
│  Session Replay Engine               │
│                                      │
│  For each tick:                      │
│    1. Update candle aggregator       │ → 1m/5m candles with OHLCV
│    2. Update orderflow engine        │ → delta, CVD, imbalances, footprint
│    3. Update VWAP accumulator        │ → incremental running VWAP + SDs
│    4. Update TPO profile             │ → 30-min letter buckets (on period close)
│    5. Update VP accumulator          │ → incremental histogram for POC/VAH/VAL
│    7. Check level touches            │ → price crosses a level?
│                                      │
│  On candle close (1m bar):           │
│    6. Recompute session levels       │ → IB, swing points, FVGs, order blocks
│                                      │
│  On level touch:                     │
│    → Snapshot full observation vector │
│    → Look forward in ticks to find   │
│      outcome (target/stop/timeout)   │
│    → Store episode to training DB    │
└──────────────────────────────────────┘
```

**Code reuse notes:**
- `orderflow.py` functions are reused but require side-field normalization: Databento historical API returns `"buy"`/`"sell"`, while `orderflow.py` expects `"A"`/`"B"`. The fetcher must normalize to `"A"`/`"B"` format at download time.
- `levels.py` batch functions (`compute_vwap_bands`, `compute_volume_profile`) are NOT incrementally updatable — they recompute from scratch. The replay engine builds **incremental accumulators** (running `cum_pv`, `cum_vol`, `cum_pv2` for VWAP; running histogram for VP) that produce equivalent results without reprocessing all ticks each time.
- `compute_session_levels()` requires 1-minute bars, not raw ticks. The replay engine aggregates ticks into bars first via the candle aggregator (step 1), then recomputes session levels on each bar close (step 6), not on each tick.
- Multi-TF VP profiles (weekly, monthly, macro) require data from prior periods. The fetcher downloads an extra month of tick data before the training window to bootstrap these profiles.

### Storage

```
data/rl/
├── ticks/              # Raw Parquet from Databento (side normalized to A/B)
│   └── NQ_YYYY-MM.parquet
├── episodes.db         # Training database (SQLite)
│   ├── raw_ticks       # Every tick (for future PPO phase)
│   ├── candles         # Aggregated candles + orderflow
│   ├── sessions        # Daily session metadata + levels
│   └── episodes        # Level touch snapshots (DQN training)
│       ├── observation # ~105-dim float vector
│       └── outcome     # R-multiple result
└── models/             # Saved checkpoints
    └── dqn_v1.pt
```

## Observation Vector (~105 floats)

All features normalized to [-1, 1] or [0, 1]. Features marked **(NEW)** require new implementation; unmarked features map to existing code.

### Level Identity (~25 dims, one-hot)

```
POC_session, POC_daily, POC_weekly, POC_monthly, POC_macro,
VAH, VAL,
VWAP, VWAP_SD1, VWAP_SD2, VWAP_SD3,
IB_high, IB_low,
PDH, PDL,
tokyo_HL, london_HL,
globex_HL (NEW), overnight_HL (NEW),
weekly_HL, monthly_HL,
naked_POC, single_print,
FVG, order_block, swing_point
```

### Orderflow Snapshot (~15 dims)

```
delta, delta_pct, cvd, cvd_trend,
volume_ratio (vs 20-candle avg), body_ratio, spread_ticks,
passive_active_ratio, imbalance_ratio_max,
stacked_imbalance_count, stacked_direction,
big_trades_count, big_trades_net_delta,
vsa_absorption, stop_run_detected
```

Note: `vsa_absorption` maps to existing `OrderflowSignals.vsa_absorption` field.

### Price Structure (~15 dims)

```
price_vs_vwap_sd, price_in_va (0-1),
distance_to_poc_ticks, distance_to_vah_ticks, distance_to_val_ticks,
distance_to_single_print_ticks,
single_prints_above (count), single_prints_below (count),
ib_range_ticks,
market_type (trend/normal/neutral one-hot),
poor_high, poor_low
```

### TPO Profile (~10 dims)

All TPO features are **(NEW)** — TPO module to be built.

```
tpo_poc, price_vs_tpo_poc_ticks,
tpo_value_area_width, price_in_tpo_va (0-1),
tpo_distribution_shape (p-shape/b-shape/d-shape/balanced one-hot),
time_at_current_price (TPO count),
excess_high, excess_low,
rotation_factor, rotation_count
```

Note: Existing `tpo.py` has a basic `compute_tpo_profile()`. The new TPO module extends it with
excess detection, distribution shape classification, rotation factor, and incremental updates.

### Recent Candle Window (~15 dims, last 5 candles summarized)

```
Per candle: delta_norm, volume_norm, body_ratio
Flattened: 5 × 3 = 15 dims
```

### Multi-TF Confluence (~5 dims)

```
levels_within_5_ticks (count), strongest_cluster_score,
nearest_higher_level_dist, nearest_lower_level_dist,
touched_level_hierarchy_rank
```

### Session Context (~8 dims)

```
minutes_since_rth_open (normalized),
session_volume_percentile, daily_range_percentile,
time_of_day_encoding (sin/cos pair),
session_type (trend/bracket/normal),
initial_balance_broken (above/below/neither)
```

### Macro (~10 dims)

```
vix_level_norm, vix_change_pct, regime_score,
dxy_change,
gex_level (NEW — requires data source integration),
us10y_change, us2y_change, yield_curve_spread,
news_event_active (NEW — binary), news_severity (NEW — 0-1)
```

Note: `gex_level` and news features have no existing data source. For Phase 1, these
will be set to 0.0 (neutral) during replay. Real-time integration is a Phase 2+ concern.
VIX, DXY, and bond yields can be fetched historically from free APIs (FRED, Yahoo Finance).

## Training

### Data Split (Chronological)

- **Train:** Months 1-4
- **Validation:** Month 5 (hyperparameter tuning)
- **Test:** Month 6 (final evaluation, never touched during training)

### Hyperparameters

```
batch_size:         64
learning_rate:      1e-4 (Adam)
replay_buffer:      100k episodes
epsilon_start:      1.0
epsilon_end:        0.05
epsilon_decay:      5000 episodes
target_net_update:  every 500 steps
episodes_per_epoch: 1000
gamma:              0.0 (deliberate: single-step episodes with no sequential
                         state, so no future discounting. Each level touch is
                         independent. Will change to γ>0 in Phase 3 when
                         moving to session-level sequential decisions.)
```

### Expected Data Volume

- ~50-200 level touches per session
- ~125 trading sessions in 6 months
- ~6,000-25,000 episodes total
- Full tick data stored for future PPO phase

## Evaluation

### Metrics

| Metric | What it tells us |
|---|---|
| Win rate | % of taken trades that hit target |
| Skip rate | % of level touches skipped (should be >60%) |
| Avg R-multiple | Mean reward per taken trade |
| Profit factor | Gross wins / gross losses (target: >1.5) |
| Per-level-type breakdown | Which levels the agent trades vs skips |
| Equity curve | Cumulative R over test sessions |
| Max drawdown (R) | Worst losing streak |

### "Good Enough" Criteria

- Profit factor > 1.5 on test set
- Skip rate > 60% (agent is selective)
- No single level type dominates
- Equity curve doesn't degrade in final test month (no overfitting)

### Level Discovery Analysis

Post-training analysis to determine which levels matter:

- **Skip rate per level type** — agent learns to skip worthless levels
- **Win rate per level type** — which levels produce winners
- **Average R per level type** — which levels produce the best winners
- **Level type × context combos** — conditional performance (e.g., Monthly POC only good when CVD confirms)

## Project Structure

```
backend/src/
├── rl/                            # NEW — all RL code
│   ├── __init__.py
│   ├── config.py                  # Hyperparameters, risk params, level types
│   │
│   ├── data/
│   │   ├── fetcher.py             # Databento historical tick download → Parquet
│   │   ├── replay_engine.py       # Tick-by-tick session reconstruction
│   │   ├── episode_builder.py     # Level touch → observation + outcome
│   │   └── normalization.py       # Running mean/std for feature scaling
│   │
│   ├── agent/
│   │   ├── network.py             # PyTorch DQN (128-128-64 → 3)
│   │   ├── replay_buffer.py       # Experience replay storage
│   │   ├── dqn.py                 # Training loop, ε-greedy, target net
│   │   └── evaluate.py            # Test set eval, metrics, level analysis
│   │
│   ├── features/
│   │   ├── observation.py         # Builds ~105-dim vector from replay state
│   │   ├── level_features.py      # Level identity one-hot + confluence
│   │   ├── orderflow_features.py  # Snapshot from orderflow engine
│   │   ├── tpo_features.py        # TPO profile features + rotation
│   │   ├── structure_features.py  # VWAP, VA, IB, session levels
│   │   └── macro_features.py      # VIX, bonds, news, DXY
│   │
│   └── cli.py                     # Typer commands: fetch, replay, train, eval
│
├── market_data/
│   ├── tpo.py                     # EXTEND — add excess, shape, rotation to existing
│   ├── levels.py                  # EXISTING — reused by replay engine
│   ├── orderflow.py               # EXISTING — reused (side field normalized at fetch)
│   └── ...
│
data/rl/
├── ticks/                         # Raw Parquet files from Databento
├── episodes.db                    # Training database
└── models/                        # Saved model checkpoints
```

### CLI Commands

```bash
python -m src.app rl fetch --months 6          # Download NQ ticks
python -m src.app rl replay --all              # Build episodes from ticks
python -m src.app rl train --epochs 100        # Train DQN agent
python -m src.app rl eval --checkpoint v1      # Evaluate on test set
```

### Dependencies

- `torch` (PyTorch CPU)
- `databento` (already installed)
- `pyarrow` (Parquet read/write)

## Phase Roadmap

### Phase 1: "Simple Track, No Brake" (THIS SPEC)

- Fetch historical NQ ticks from Databento
- Build TPO module (shared live + replay)
- Build replay engine (reconstructs full session from ticks)
- Build episode builder (level touch → observation + outcome)
- Build DQN agent (3 actions, fixed risk params)
- Train & evaluate on 6 months of data
- Level-type discovery analysis
- Frontend: training dashboard (equity curve, heatmaps)

### Phase 2: "Adding the Brake" (future)

- Dynamic stop/target distances (agent chooses 5/10/15/20 ticks)
- Position sizing (1x/2x/3x based on conviction)
- Shaped rewards (partial credit for direction-correct trades)
- Richer candle window (10-20 candles instead of 5)

### Phase 3: "Learning to Drift" (future)

- Upgrade to PPO for continuous action space
- Session-level agent (continuous decisions, not just at level touches)
- Dynamic exits (agent decides when to close, not fixed target)
- Multi-trade management
- Reward shaping for drawdown control

## Known Limitations (Phase 1)

- **Timeout asymmetry:** If price moves +15 ticks in the agent's direction but returns and times out, the reward is 0.0 (same as SKIP). The agent gets no signal that it was directionally correct. Shaped rewards in Phase 2 will address this.
- **SKIP reward of 0.0:** If expected trade reward hovers near 0, the agent may over-skip. Monitor skip rate — if it exceeds 90%, consider a small negative SKIP penalty (-0.01) to encourage exploration.
- **Data volume floor:** With ~6k-25k episodes and ~105 input dims, the lower bound is thin for training. If episode count is below 10k, consider: (a) fetching more months, (b) data augmentation by mirroring LONG/SHORT labels for symmetric levels, or (c) reducing observation vector dimensionality.
- **GEX and news features zeroed out:** No historical data source for Phase 1. These dims will be constant 0.0 during training — the agent will learn to ignore them. They become useful when live data sources are integrated.
- **No slippage or commission modeling:** Outcomes assume perfect fills at level-touch price. Real execution will have slippage. Phase 4 addresses this.

### Phase 4: "Endurance Map" (future)

- Live paper trading against real Databento stream
- Performance comparison vs manual trading
- Confidence calibration (Q-values vs actual outcomes)
- Live deployment with risk limits
