# Composite Confidence Scoring + Dynamic Position Sizing

**Date:** 2026-04-03
**Status:** Approved
**Depends on:** Hierarchical Observation Architecture (v5 pipeline must complete first)

## Problem

Current sizing uses only Q-value spread as confidence — a single number from one model. The v5 architecture produces multiple confidence signals across layers that should all feed into sizing. A failed auction at PDH with decelerating approach and macro alignment should get 150% base size. A weak signal at an ambiguous zone with conflicting narrative should get 30%.

## Composite Confidence Score

Combines 6 signals into a single 0-1 score:

### Signal Sources

| Signal | Range | Source | Weight | Why |
|--------|-------|--------|--------|-----|
| `setup_confidence` | 0-1 | Narrative GBT: max setup probability | 0.25 | Known setup = higher confidence |
| `narrative_alignment` | 0-1 | Agreement between regime, trend, day_type, initiative_direction | 0.20 | Everything pointing same direction |
| `trigger_gbt_confidence` | 0-1 | Trigger GBT: abs(p_cont - p_rev) | 0.20 | Model's directional conviction |
| `dqn_q_spread` | 0-1 | DQN: normalized Q-value spread | 0.15 | Policy model's uncertainty |
| `zone_quality` | 0-1 | Zone confluence weight * member count, normalized | 0.10 | Structural importance of the level |
| `micro_alignment` | 0-1 | Approach velocity + orderflow agreeing with trade direction | 0.10 | Execution timing quality |

### Computation

```python
composite = (
    0.25 * setup_confidence +
    0.20 * narrative_alignment +
    0.20 * trigger_gbt_confidence +
    0.15 * dqn_q_spread +
    0.10 * zone_quality +
    0.10 * micro_alignment
)
```

### Narrative Alignment Score

Measures whether the slow context signals agree with the trade direction:

```
For a LONG trade:
  regime_score > 0        → +1 (risk-on)
  htf_trend > 0           → +1 (bullish)
  initiative_direction > 0 → +1 (buyers in control)
  day_type trending up     → +1

  alignment = count_agreeing / 4  → 0 to 1
```

For SHORT, invert the signs. For SKIP, alignment is 0.

### Micro Alignment Score

Measures whether the tick-level action confirms the trade:

```
For REVERSAL at a zone:
  approach decelerating (micro_19 < 0)  → +1
  absorption detected (orderflow[16])    → +1
  volume climax (orderflow[18])          → +1

  micro_alignment = count_confirming / 3
```

## Sizing Tiers

| Composite Score | Tier | Size Multiplier | Description |
|----------------|------|-----------------|-------------|
| 0.85 - 1.00 | **A+ setup** | 1.5x base | Everything aligns — full conviction |
| 0.70 - 0.85 | **A setup** | 1.0x base | Strong setup, standard size |
| 0.50 - 0.70 | **B setup** | 0.6x base | Decent but mixed signals |
| 0.30 - 0.50 | **C setup** | 0.3x base | Low confidence, minimum size |
| 0.00 - 0.30 | **Skip** | 0x | Not enough alignment |

These multiply with the existing intraday compounding and consecutive-loss reduction.

## Implementation

### New file: `backend/src/rl/confidence.py`

```python
class CompositeConfidence:
    def score(narrative, setup_probs, trigger_forecast, q_spread, zone, micro_obs, trade_direction) -> float
    def size_multiplier(composite_score) -> float
```

### Modify: `backend/src/rl/session_manager.py`

Replace current `_compute_size(confidence)` with:
```python
def _compute_size(self, composite_confidence: float) -> float:
    multiplier = CompositeConfidence.size_multiplier(composite_confidence)
    size = self.BASE_SIZE * multiplier
    # ... existing compounding/loss-reduction logic stays
```

### Training the confidence calibrator (future)

After accumulating enough live episodes with composite scores and outcomes, train a small calibration model:
- Input: composite confidence score + setup type
- Target: actual win rate at that confidence level
- This calibrates the sizing weights to real market performance

## File Structure

| Action | File | What |
|--------|------|------|
| CREATE | `backend/src/rl/confidence.py` | Composite confidence scorer + size multiplier |
| MODIFY | `backend/src/rl/session_manager.py` | Wire composite confidence into sizing |
| MODIFY | `backend/src/rl/live_inference.py` | LiveInferenceV5 returns composite_confidence in signal |
| CREATE | `backend/tests/test_confidence.py` | Tests for scoring and sizing tiers |
