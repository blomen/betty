# Hierarchical Observation Architecture — Two-Stage Context→Trigger

**Date:** 2026-04-03
**Status:** Approved
**Motivation:** The current flat 276-dim observation treats "VIX level" and "approach velocity" with equal weight. The model gets 50.6% direction accuracy because it's drowning in slow-moving context features. Professional traders process information hierarchically: narrative first, then trigger. The model should too.

## Architecture Overview

Two-stage model that separates session context (slow, updates periodically) from trade execution (fast, fires at each zone touch).

```
Stage 1: Narrative Layer (slow)
  Macro + Structure + TPO + AMT → 15 named signals + 8 setup probabilities
  Updates: every 30min + on structural events (IB close, new swing, VA breach)

Stage 2: Trigger Layer (fast)
  Narrative signals + Structural passthrough + Micro + Orderflow + Candles + Zone → Action
  Fires: at each zone touch
```

## Stage 1: Narrative Layer

### Inputs (from existing features, regrouped)
- Macro (6 alive dims): VIX, DXY, 10Y, regime_score
- Structure (63 dims): Dow Theory swings daily/weekly/monthly, VWAP position, VP position
- TPO (38 dims): Value area, POC, excess, poor highs/lows, balance/imbalance
- AMT static (16 dims): Prior session state, opening type relative to value
- AMT dynamics (20 dims): Rotation count, initiative vs responsive, bracket evolution
- Execution context (7 dims): Time of day, session phase, trades taken today

### Outputs: 15 Named Narrative Signals

**Market Regime (3):**
| Signal | Range | Source |
|--------|-------|--------|
| `regime_score` | -1.0 (risk-off) to +1.0 (risk-on) | VIX, DXY, yields |
| `htf_trend` | -1.0 (bearish) to +1.0 (bullish) | Weekly/daily swing alignment |
| `volatility_regime` | 0 (low), 0.5 (normal), 1.0 (high) | ATR percentile vs 20-day |

**Session Context (7):**
| Signal | Values/Range | Source |
|--------|-------------|--------|
| `day_type` | 5-class: trend_up, trend_down, normal, normal_var, neutral | TPO + AMT dynamics |
| `opening_type` | 5-class: OTD_up, OTD_down, ORR, OD_up, OD_down | Open vs prior VA |
| `ib_type` | 3-class: wide, narrow, normal | IB range vs 20-day avg |
| `value_migration` | -1.0 to +1.0 | Today's VA vs yesterday's VA |
| `session_phase` | 5-class: pre_ib, ib_forming, post_ib_early, post_ib_late, close | Clock-based + IB status |
| `initiative_direction` | -1.0 (sellers) to +1.0 (buyers) | TPO initiative activity |
| `balance_width` | Normalized 0-1 | Developing balance range / ATR |

**Structural Position (5):**
| Signal | Range | Source |
|--------|-------|--------|
| `price_vs_value` | -1.0 (below VAL) to +1.0 (above VAH), 0 = inside | VP value area |
| `price_vs_poc` | Signed distance, normalized | Distance to session POC |
| `price_vs_ib` | -1.0 to +1.0, 0 = inside IB | IB range position |
| `trend_alignment` | -1.0 to +1.0 | Daily/weekly/monthly swing agreement |
| `excess_nearby` | 0 or 1 | Unfilled excess/single-print within 1 ATR |

### Setup Probabilities per Zone (8)
| Setup | Label Source |
|-------|-------------|
| `p_failed_auction` | Rule-based |
| `p_ib_extension` | Rule-based |
| `p_gap_fill` | Rule-based |
| `p_single_print_fill` | Rule-based |
| `p_look_above_below_fail` | Rule-based |
| `p_rotation_to_poc` | Cluster-derived |
| `p_excess_test` | Cluster-derived |
| `p_balance_break` | Cluster-derived |

### Update Triggers
- Every 30 minutes
- On IB close (10:30 ET)
- On new swing high/low detection
- On value area breach (price acceptance above VAH or below VAL)
- On single-print creation

### Model
LightGBM multi-output trained on narrative features only, predicting day_type + regime + setup probabilities. Trained on labeled episodes (see Setup Labeling section).

## Stage 2: Trigger Layer

### Inputs

| Component | Dims | Source |
|-----------|------|--------|
| Narrative signals | 15 | Stage 1 output |
| Setup probabilities | 8 | Stage 1 per-zone output |
| Structural passthrough | 10 | Top 10 importance structure/TPO features from current GBT |
| Micro features | 20 | Tick-level: approach velocity, acceleration, absorption, trade sizes |
| Orderflow | 21 | Delta, CVD, imbalance, exhaustion, momentum |
| Candles | 15 | Last 5 bars: delta, volume, body ratio |
| Zone features | 4 | Radius, weight, member count, confidence |
| Zone confluence | 5 | Count, has POC/VWAP/swing |
| Zone composition | 31 | Which level types make up this zone |
| Approach direction | 1 | Up or down approach |
| Trigger GBT forecast | 8 | Direction conf, expected R, breakeven prob, etc. |
| Execution passthrough | 3 | Trades today, time to close, session PnL |
| **Total** | **~141** | |

### Trigger GBT
Replaces current single GBT. Same multi-target outputs (direction, expected R, breakeven, levels, stop) but trained only on fast features + narrative signals. No longer needs to discover macro/structure context from raw data.

### Trigger DQN
Same Dueling Double DQN architecture. Input dim changes from 292 to ~141. Same outputs:
- Action: CONTINUATION / REVERSAL / SKIP
- Stop distance prediction (via stop head)
- Q-value confidence (from Q-value spread)

## Setup Labeling System

### Rule-Based Labels (5 mechanical setups)

**Failed Auction:**
- Zone contains session extreme (PDH/PDL, IB high/low)
- Price probed beyond the level (approach_dir going through)
- Reversal reward > continuation reward
- Forward scan: price rejected within 60 seconds

**IB Extension:**
- Touch after IB close (10:30 ET)
- Zone at or beyond IB high/low
- Continuation reward > reversal reward
- Initiative orderflow (delta_ratio > 0.6)

**Gap Fill:**
- Opening gap exists (open outside prior value area)
- Zone is within the gap region
- Price moving back toward prior value area
- Touch occurs in first 2 hours of session

**Single Print Fill:**
- Zone contains naked_poc or is in a single-print region from session store
- Price returning to fill it from outside

**Look-Above/Below-and-Fail:**
- Zone is at VAH or VAL
- Price pushed outside value area
- Reversal back inside within 5 minutes
- Essentially a failed auction specifically at value area edges

### Cluster-Derived Labels (3 softer setups)

**Rotation to POC:**
- Cluster episodes where zone contains POC/VPOC
- Price was at value edge, moved back toward center
- Clustering separates clean rotations from noise

**Excess Test:**
- Cluster episodes at zones with prior excess (poor high/low, single prints from prior sessions)
- Outcome varies: acceptance vs rejection
- Clustering separates the two patterns

**Balance Break:**
- Cluster episodes at zones where price has been rotating for 2+ sessions
- Breaking out vs fading back in
- Cluster by structural context + outcome

### Labeling Process
1. Run rule-based labeler on all episodes → labels ~60%
2. HDBSCAN clustering on narrative features for unlabeled remainder → propose labels
3. Manual review of 50-100 episodes per cluster to validate mapping
4. One label per episode, priority: Failed Auction > Look-Above/Below-Fail > IB Extension > Gap Fill > Single Print Fill > cluster labels

## Structural Passthrough Selection

Top 10 highest-importance raw features from current GBT that pass through to trigger layer (from the feature importance analysis):

1. `struct_3` — distance to VWAP/swing high (imp: 473)
2. `struct_4` — distance to swing low (imp: 374)
3. `struct_2` — VWAP position (imp: 341)
4. `struct_5` — IB distance (imp: 322)
5. `tpo_*` top 3 — (selected by importance from 38 TPO dims)
6. `amtdyn_*` top 3 — (selected by importance from 20 AMT dynamics dims)

Exact indices determined by running feature importance on the current trained GBT v3.

## Training Pipeline

```
Step 0:  Merge live episodes
Step 1:  Replay historical ticks → raw episodes (slow, tick-by-tick)
Step 2:  Run setup labeler on all episodes → adds setup_label
Step 3:  Train Narrative GBT on slow features → predicts day_type, regime, setup_probs
Step 4:  Augment episodes with narrative outputs (fast, numpy + GBT inference)
Step 5:  Train Trigger GBT on fast features + narrative → 8-dim forecast
Step 6:  Augment episodes with trigger GBT outputs (fast, numpy + GBT inference)
Step 7:  Train Trigger DQN on final hybrid episodes (~141 dims)
Step 8:  Evaluate
Step 9:  Deploy
```

Steps 1 is slow (tick replay). Steps 2-6 are fast (numpy operations + GBT inference). Step 7 is moderate (DQN training, ~2-3 hours with batch 4096).

## Live Inference Path

```
Every 30min + structural events:
  Current session state → Narrative GBT → updates 15 narrative signals + zone setup probs

On zone touch:
  [15 narrative + 8 setup_probs + 10 passthrough + micro + orderflow + candles + zone]
    → Trigger GBT → 8-dim forecast
    → Trigger DQN → action + stop distance + confidence
```

## File Structure

```
backend/src/rl/
├── features/
│   ├── narrative_features.py     # NEW — 15 named signals from slow features
│   ├── trigger_features.py       # NEW — assembles trigger observation
│   ├── passthrough_features.py   # NEW — top 10 structure/TPO passthrough
│   ├── observation.py            # REFACTORED — delegates to narrative/trigger
│   └── (existing files unchanged, used as building blocks)
├── labeling/
│   ├── setup_labeler.py          # NEW — rule-based labels for 5 mechanical setups
│   ├── setup_clusterer.py        # NEW — HDBSCAN clustering for 3 soft setups
│   └── setup_types.py            # NEW — SetupType enum + priority resolution
├── agent/
│   ├── narrative_gbt.py          # NEW — Narrative GBT
│   ├── trigger_gbt.py            # NEW — Trigger GBT (replaces gbt_model.py)
│   ├── dqn.py                    # MODIFIED — input dim 292 → ~141
│   └── (rest unchanged)
├── config.py                     # UPDATED — new dims, setup types, narrative triggers
├── session_manager.py            # UPDATED — calls narrative update on structural events
└── live_inference.py             # UPDATED — two-stage inference path
```

## Backwards Compatibility

- Old 276/292-dim models stay in `models/` as v4
- New models are v5 (narrative_gbt_v5, trigger_gbt_v5, dqn_v5)
- Live inference checks model version and uses the right observation builder
- Old episodes can be re-labeled and re-augmented without re-replaying ticks

## Expected Improvements

- DQN direction accuracy: 50.6% → target 58%+ (cleaner input, setup context)
- Skip quality: currently over-skipping profitable setups. Setup labels let the model learn "failed auctions at PDH are high-value, don't skip"
- Stop head: 14.1 tick MAE → target <8 ticks (narrative context helps — trend days need wider stops than rotation days)
- Interpretability: every trade decision has a readable narrative context attached
