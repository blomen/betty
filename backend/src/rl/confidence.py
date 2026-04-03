"""Composite confidence scoring — combines all v5 signals into sizing decisions.

6 signals weighted into a 0-1 composite score:
  setup_confidence     (0.25) — max setup probability from narrative GBT
  narrative_alignment  (0.20) — regime/trend/initiative agreeing with trade direction
  trigger_confidence   (0.20) — trigger GBT directional conviction
  dqn_q_spread         (0.15) — DQN policy uncertainty
  zone_quality         (0.10) — structural importance of the zone
  micro_alignment      (0.10) — tick-level confirmation of trade direction
"""
from __future__ import annotations

import numpy as np

# Signal weights must sum to 1.0
_WEIGHTS = {
    "setup_confidence":    0.25,
    "narrative_alignment": 0.20,
    "trigger_confidence":  0.20,
    "dqn_q_spread":        0.15,
    "zone_quality":        0.10,
    "micro_alignment":     0.10,
}

assert abs(sum(_WEIGHTS.values()) - 1.0) < 1e-9, "Weights must sum to 1.0"

# Narrative indices for directional alignment check
_REGIME_IDX = 0           # regime_score: >0 bullish, <0 bearish
_HTF_TREND_IDX = 1        # htf_trend: >0 up, <0 down
_INITIATIVE_IDX = 8       # initiative_direction: >0 initiative buying, <0 selling
_DAY_TYPE_IDX = 3         # day_type: >0 trend day, <0 balanced/non-trend

# Micro feature indices
_MICRO_APPROACH_ACCEL_IDX = 1   # approach_accel: acceleration of approach
_MICRO_LAST5_VEL_IDX = 11       # last5_velocity: recent velocity
_MICRO_REVERSAL_IDX = 9         # reversal_count_norm: choppy=high, smooth=low
_MICRO_LAST5_ACCEL_IDX = 19     # last5_acceleration: final acceleration


def _compute_narrative_alignment(
    narrative: np.ndarray,
    trade_direction: int,
) -> float:
    """Count how many narrative signals agree with trade direction.

    Checks 4 signals:
      - regime_score (index 0): >0 for longs, <0 for shorts
      - htf_trend (index 1): >0 for longs, <0 for shorts
      - initiative_direction (index 8): >0 for longs, <0 for shorts
      - day_type (index 3): >0 for longs (trend up), <0 for shorts

    Returns fraction of signals in agreement [0, 1].
    Returns 0.5 (neutral) if trade_direction is 0 (skip).
    """
    if trade_direction == 0:
        return 0.5

    signals = [
        narrative[_REGIME_IDX],
        narrative[_HTF_TREND_IDX],
        narrative[_INITIATIVE_IDX],
        narrative[_DAY_TYPE_IDX],
    ]

    agreements = sum(
        1 for s in signals
        if (trade_direction > 0 and s > 0) or (trade_direction < 0 and s < 0)
    )
    return agreements / len(signals)


def _compute_micro_alignment(
    micro_features: np.ndarray,
    trade_direction: int,
) -> float:
    """Assess tick-level confirmation of trade direction.

    For REVERSAL setups (approaching a level): the approach should be
    decelerating — we look for deceleration + slowing + high reversal count.

    For CONTINUATION setups (breaking through): the approach should be
    accelerating — we look for acceleration + fast final move + low reversal count.

    Heuristic: use approach_accel sign to decide if this is a reversal or
    continuation approach, then evaluate 3 micro signals.

    Returns fraction of confirming signals [0, 1].
    Returns 0.5 (neutral) if trade_direction is 0.
    """
    if trade_direction == 0:
        return 0.5

    accel = float(micro_features[_MICRO_APPROACH_ACCEL_IDX])
    last5_vel = float(micro_features[_MICRO_LAST5_VEL_IDX])
    reversal_count = float(micro_features[_MICRO_REVERSAL_IDX])
    last5_accel = float(micro_features[_MICRO_LAST5_ACCEL_IDX])

    # Determine if we expect a reversal (approach decelerating = price losing momentum
    # into the level → reversal) or continuation (accelerating into level → continuation).
    is_reversal_approach = accel < 0  # approach is slowing down

    if is_reversal_approach:
        # For REVERSAL: want approach decelerating, approach slowing, high reversal_count
        signals = [
            accel < 0,           # index 1: approach decelerating
            last5_accel < 0,     # index 19: approach slowing in last 5 ticks
            reversal_count > 0.5,  # index 9: choppy (lots of direction changes)
        ]
    else:
        # For CONTINUATION: want approach accelerating, fast last5, low reversal_count
        signals = [
            accel > 0,           # index 1: approach accelerating
            last5_vel > 0,       # index 11: last 5 ticks moving fast
            reversal_count < 0.3,  # index 9: smooth approach (few reversals)
        ]

    confirming = sum(1 for s in signals if s)
    return confirming / len(signals)


def _compute_q_spread_score(q_spread: float) -> float:
    """Convert Q-value spread to a 0-1 confidence score.

    Larger spread = higher confidence (DQN is more decisive).
    q_spread is |max_q - min_q|, typically in [0, 2+].
    We map it with a soft clip: tanh(q_spread).
    """
    return float(np.tanh(max(q_spread, 0.0)))


def _compute_zone_quality(
    zone_confluence_weight: float,
    zone_member_count: int,
) -> float:
    """Combine zone hierarchy score and member count into 0-1 quality score.

    confluence_weight: Zone.hierarchy_score (0-1)
    member_count: number of level types that form the zone (typically 1-5)
    """
    # Normalize member count: 1 member → 0.2, 5+ members → 1.0
    member_score = min(zone_member_count / 5.0, 1.0)
    # Average with the hierarchy score
    return float(np.clip((zone_confluence_weight + member_score) / 2.0, 0.0, 1.0))


def compute_composite_confidence(
    setup_probs: np.ndarray,
    narrative: np.ndarray,
    trigger_forecast: np.ndarray,
    q_spread: float,
    zone_confluence_weight: float,
    zone_member_count: int,
    micro_features: np.ndarray,
    trade_direction: int,
) -> float:
    """Compute composite confidence score combining all v5 signals.

    Args:
        setup_probs: 8-dim setup probabilities from narrative GBT.
        narrative: 15-dim narrative signals (from extract_narrative_features).
        trigger_forecast: 8-dim trigger GBT forecast (direction probabilities).
        q_spread: DQN Q-value spread |max_q - min_q| (0 = uncertain, large = confident).
        zone_confluence_weight: Zone.hierarchy_score [0, 1].
        zone_member_count: Number of level types forming the zone.
        micro_features: 20-dim micro features (from extract_micro_features).
        trade_direction: +1 long, -1 short, 0 skip.

    Returns:
        Composite confidence score in [0, 1].
    """
    # 1. Setup confidence: max probability across setup types
    setup_conf = float(np.clip(np.max(setup_probs), 0.0, 1.0))

    # 2. Narrative alignment: fraction of regime signals agreeing with direction
    narrative_align = _compute_narrative_alignment(narrative, trade_direction)

    # 3. Trigger confidence: the max of the directional forecast probabilities
    trigger_conf = float(np.clip(np.max(trigger_forecast), 0.0, 1.0))

    # 4. DQN Q-spread: higher spread = more decisive policy
    q_conf = _compute_q_spread_score(q_spread)

    # 5. Zone quality: structural importance
    zone_qual = _compute_zone_quality(zone_confluence_weight, zone_member_count)

    # 6. Micro alignment: tick-level confirmation
    micro_align = _compute_micro_alignment(micro_features, trade_direction)

    composite = (
        _WEIGHTS["setup_confidence"]    * setup_conf
        + _WEIGHTS["narrative_alignment"] * narrative_align
        + _WEIGHTS["trigger_confidence"]  * trigger_conf
        + _WEIGHTS["dqn_q_spread"]        * q_conf
        + _WEIGHTS["zone_quality"]        * zone_qual
        + _WEIGHTS["micro_alignment"]     * micro_align
    )

    return float(np.clip(composite, 0.0, 1.0))


def size_multiplier(composite: float) -> float:
    """Map composite confidence to a position sizing multiplier.

    Tiers:
        0.85-1.00 → 1.5  (A+ setup, full conviction)
        0.70-0.85 → 1.0  (A setup, standard)
        0.50-0.70 → 0.6  (B setup, reduced)
        0.30-0.50 → 0.3  (C setup, minimum)
        <0.30     → 0.0  (skip — below confidence threshold)

    Args:
        composite: Composite confidence score in [0, 1].

    Returns:
        Size multiplier (0.0 = skip, 1.5 = max size).
    """
    if composite >= 0.85:
        return 1.5
    elif composite >= 0.70:
        return 1.0
    elif composite >= 0.50:
        return 0.6
    elif composite >= 0.30:
        return 0.3
    else:
        return 0.0
