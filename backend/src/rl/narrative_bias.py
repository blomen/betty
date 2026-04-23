"""Narrative-bias head — turns macro/regime context into actionable risk knobs.

The retired NarrativeGBT (H3) was solving the wrong problem (predicting day_type
from its own one-hot input). This module solves the RIGHT problem the user
articulated: take the multi-timeframe / macro / regime signals we already
extract and turn them into two actionable scalars that modulate live trading:

    bias_score      ∈ [-1, +1]  — directional bias of the regime
    risk_modulation ∈ [0.5, 1.5] — size scalar for the regime

These are NOT predictions of a label that's already in the input. They are
analytical aggregates of forward-looking regime features (Dow theory trend,
risk-on/off macro, session day type, opening type) that real traders use to
decide HOW MUCH to risk in the current market context.

Live integration:
- bias_score: multiplied into composite_confidence with the trade direction.
  Bias agrees with trade → confidence boost; bias opposes → confidence cut.
- risk_modulation: multiplied directly into the position size after SizeModel.

Rule-based for now (no training, no schema bump). If it produces measurable
lift we can replace with a trained head whose label is forward-window R.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

# Indices into the 18-dim narrative vector — see narrative_features.py
_NARR_REGIME_SCORE = 0
_NARR_HTF_TREND = 1
_NARR_VOLATILITY = 2
_NARR_DAY_TYPE = 3
_NARR_OPENING = 4
_NARR_TREND_ALIGNMENT = 13
_NARR_TREND_CONVICTION = 17

# Indices into the 11-dim macro slice (registry: macro segment at base_obs[178:189])
# We don't read base_obs directly here; live_inference passes the macro dict.


@dataclass(frozen=True)
class NarrativeBias:
    """Output of the narrative-bias head."""

    bias_score: float  # -1 strong bearish, +1 strong bullish, 0 neutral
    risk_modulation: float  # 0.5 defensive, 1.0 normal, 1.5 aggressive
    bias_agreement: float  # signed product of bias × trade_direction (-1..+1)
    components: dict  # individual sub-scores for debugging / observability


def compute_bias_score(narrative: np.ndarray) -> float:
    """Aggregate the multi-timeframe trend signals into a single directional bias.

    Weighted average of:
      - regime_score  (40%) — macro regime, wide window
      - htf_trend     (30%) — daily/weekly/monthly weighted trend, direct Dow
      - trend_alignment (20%) — agreement across horizons
      - trend_conviction (10%) — composite multi-narrative agreement check

    All inputs are already in [-1, +1]. Output clipped to [-1, +1].
    """
    if narrative is None or len(narrative) < 18:
        return 0.0
    regime = float(narrative[_NARR_REGIME_SCORE])
    htf = float(narrative[_NARR_HTF_TREND])
    align = float(narrative[_NARR_TREND_ALIGNMENT])
    conv = float(narrative[_NARR_TREND_CONVICTION])
    bias = 0.40 * regime + 0.30 * htf + 0.20 * align + 0.10 * conv
    return float(np.clip(bias, -1.0, 1.0))


def compute_risk_modulation(narrative: np.ndarray) -> float:
    """Compute regime-conditional size scalar in [0.5, 1.5].

    Friendly regime → modulate up: low VIX + strong trend + clear day type
    Hostile regime → modulate down: high VIX + non-trend + opening rejection (ORR)

    Inputs (all in narrative vector):
      - volatility_regime (narrative[2]) — VIX-normalised, +1 = high vol
      - day_type (narrative[3]) — +1 trend day, -1 non-trend
      - opening_type (narrative[4]) — OD/OTD/ORR/OA mapped to ordinals
      - trend_conviction (narrative[17]) — composite multi-tf agreement
    """
    if narrative is None or len(narrative) < 18:
        return 1.0

    vol_high = float(narrative[_NARR_VOLATILITY])  # -1 calm, +1 high vol
    day_type = float(narrative[_NARR_DAY_TYPE])  # -1 non-trend, +1 trend
    opening = float(narrative[_NARR_OPENING])  # OD=+1, OTD=+0.5, OA=0, ORR=-0.5
    conv = float(narrative[_NARR_TREND_CONVICTION])  # multi-tf alignment

    # Score components (each in [-1, +1] ish, then mapped to a multiplier)
    # +score → boost, -score → reduce
    vol_score = -vol_high * 0.3  # high vol = defensive
    trend_score = abs(day_type) * 0.25  # any clear day type (not balance) is good
    open_score = opening * 0.15  # trending opens (OD/OTD) are friendlier
    conv_score = abs(conv) * 0.30  # any strong conviction is good

    # Sum and map: 0 → 1.0, +1 → 1.5, -1 → 0.5
    raw = vol_score + trend_score + open_score + conv_score
    mod = 1.0 + raw * 0.5
    return float(np.clip(mod, 0.5, 1.5))


def compute_narrative_bias(
    narrative: np.ndarray,
    trade_direction: int,
) -> NarrativeBias:
    """Full narrative-bias output for a single inference call.

    Args:
        narrative: 18-dim feature vector from extract_narrative_features
        trade_direction: +1 long, -1 short, 0 skip

    Returns NarrativeBias with bias_score, risk_modulation, agreement, components.
    """
    bias = compute_bias_score(narrative)
    risk_mod = compute_risk_modulation(narrative)

    # Agreement: -1 (bias against trade) to +1 (bias with trade), 0 if skip
    if trade_direction == 0:
        agreement = 0.0
    else:
        agreement = float(np.clip(bias * trade_direction, -1.0, 1.0))

    components = {
        "regime_score": float(narrative[_NARR_REGIME_SCORE]) if narrative is not None and len(narrative) >= 1 else 0.0,
        "htf_trend": float(narrative[_NARR_HTF_TREND]) if narrative is not None and len(narrative) >= 2 else 0.0,
        "volatility_regime": float(narrative[_NARR_VOLATILITY])
        if narrative is not None and len(narrative) >= 3
        else 0.0,
        "day_type": float(narrative[_NARR_DAY_TYPE]) if narrative is not None and len(narrative) >= 4 else 0.0,
        "opening_type": float(narrative[_NARR_OPENING]) if narrative is not None and len(narrative) >= 5 else 0.0,
        "trend_alignment": float(narrative[_NARR_TREND_ALIGNMENT])
        if narrative is not None and len(narrative) >= 14
        else 0.0,
        "trend_conviction": float(narrative[_NARR_TREND_CONVICTION])
        if narrative is not None and len(narrative) >= 18
        else 0.0,
    }

    return NarrativeBias(
        bias_score=bias,
        risk_modulation=risk_mod,
        bias_agreement=agreement,
        components=components,
    )


def apply_bias_to_confidence(
    composite_confidence: float,
    bias_agreement: float,
    boost_strength: float = 0.15,
) -> float:
    """Adjust composite confidence by bias agreement.

    bias_agreement = +1 (bias strongly with trade) → confidence × (1 + boost)
    bias_agreement = -1 (bias strongly against trade) → confidence × (1 - boost)
    bias_agreement = 0 (neutral / skip) → no change

    Default boost_strength 0.15 is moderate — bias as a tiebreaker, not an
    overriding signal. Tuning higher gives narrative more authority.
    """
    factor = 1.0 + (bias_agreement * boost_strength)
    return float(np.clip(composite_confidence * factor, 0.0, 1.0))


def apply_risk_modulation_to_size(
    size_multiplier: float,
    risk_modulation: float,
) -> float:
    """Scale the SizeModel's predicted size by the regime risk modulation.

    risk_modulation 1.0 → no change
    risk_modulation 0.5 → halve size (defensive regime)
    risk_modulation 1.5 → 1.5× size (aggressive regime)

    Caller can clip the final size to whatever live cap they enforce.
    """
    return float(size_multiplier * risk_modulation)
