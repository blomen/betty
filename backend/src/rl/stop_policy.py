"""Stop-policy utilities: confidence + regime + structural-anchor adjustments.

Framework rule: the stop should reflect HOW CONFIDENT we are in the level and
the CURRENT MACRO REGIME — not just the model's raw prediction. Additionally,
a real trader anchors stops behind structural levels (order blocks, swing
highs/lows, PDH/PDL) so the stop only fires on a GENUINE invalidation, not
on noise.

Three multiplicative adjustments on top of the trained TriggerGBT stop:

  1. Confidence scale — high confidence tightens the stop (the level is
     clear, little breathing room needed); low confidence widens it.

  2. Regime scale — defensive regime (high VIX / non-trend / ORR opening)
     widens the stop so we don't get chopped out by noise; aggressive
     regime (calm trending day) tightens it.

  3. Structural anchor — if the zone contains a "structural" level type
     (swing hi/lo, PDH/PDL, naked_poc), the stop sits 2 ticks beyond that
     level in the invalidation direction. Trained stop acts as floor.

Final:  stop_ticks = max(base × conf_scale × regime_scale, structural_min)

All adjustments clipped to safe bounds (6–50 ticks) at the caller boundary.
"""

from __future__ import annotations

from collections.abc import Iterable

from .config import TICK_SIZE, LevelType

# --- Confidence scale ------------------------------------------------------
# conf=0.0 → 1.4× (wide breathing room, low conviction)
# conf=0.5 → 1.1×
# conf=1.0 → 0.8× (tight, we're sure of the level)
_CONF_MAX_SCALE: float = 1.4
_CONF_MIN_SCALE: float = 0.8


def compute_confidence_scale(composite_confidence: float) -> float:
    """Map composite_confidence ∈ [0, 1] → stop-width multiplier ∈ [0.8, 1.4].

    Linear: scale = 1.4 - 0.6 × confidence. Higher confidence tightens the
    stop; lower confidence gives it breathing room.
    """
    conf = max(0.0, min(1.0, float(composite_confidence)))
    return _CONF_MAX_SCALE - (_CONF_MAX_SCALE - _CONF_MIN_SCALE) * conf


# --- Regime scale ----------------------------------------------------------
# Narrative risk_modulation ∈ [0.5, 1.5]:
#   0.5 (defensive / hostile) → 1.2× stop (wider — market is choppy)
#   1.0 (neutral)             → 1.0×
#   1.5 (aggressive / friendly) → 0.9× stop (tighter — clean regime)
_REGIME_WIDE_SCALE: float = 1.2
_REGIME_TIGHT_SCALE: float = 0.9


def compute_regime_scale(risk_modulation: float) -> float:
    """Map risk_modulation ∈ [0.5, 1.5] → stop-width multiplier ∈ [0.9, 1.2].

    Counterintuitive but correct: defensive regime = wider stops (we expect
    chop; tight stops get eaten). Aggressive regime = tighter stops (clean
    trend, less noise).
    """
    rm = max(0.5, min(1.5, float(risk_modulation)))
    # Linear interpolation: rm=0.5 → 1.2, rm=1.5 → 0.9
    t = (rm - 0.5) / 1.0
    return _REGIME_WIDE_SCALE + (_REGIME_TIGHT_SCALE - _REGIME_WIDE_SCALE) * t


# --- Structural anchor -----------------------------------------------------
# Level types that constitute strong structural invalidation — a stop beyond
# these levels means the setup really did fail, not just noise.
# 2026-05-05: broadened from the swing/PDH/NYIB-only set. Empirical analysis
# of 247 trades since 05-01 showed only 47/247 got the structural-anchor
# widening (the +0.557R bucket); the rest sat at raw 27-32t GBT predictions
# (-0.30 avg R) because most live zones are composed of session highs/lows
# and TPO levels — not the narrow original set. Adding session/IB/TPO levels
# lets the anchor fire on the zones the model actually trades from.
_STRUCTURAL_LEVEL_TYPES: set[LevelType] = {
    # Swings — original structural set
    LevelType.DAILY_SWING_HIGH,
    LevelType.DAILY_SWING_LOW,
    LevelType.WEEKLY_SWING_HIGH,
    LevelType.WEEKLY_SWING_LOW,
    LevelType.MONTHLY_SWING_HIGH,
    LevelType.MONTHLY_SWING_LOW,
    # Prior-day + naked POC — original
    LevelType.PDH,
    LevelType.PDL,
    LevelType.NAKED_POC,
    # Initial balance — both NY and Tokyo session opens
    LevelType.NYIB_HIGH,
    LevelType.NYIB_LOW,
    LevelType.TIBH,
    LevelType.TIBL,
    # Session highs/lows — these are where real flow defends/attacks
    LevelType.TOKYO_HIGH,
    LevelType.TOKYO_LOW,
    # TPO POC + value-area edges — TPO POC is the day's volume mean,
    # VAH/VAL bound the 70% acceptance range
    LevelType.TPOC,
    LevelType.TVAH,
    LevelType.TVAL,
    # Volume-profile POCs across timeframes — when present in a zone they
    # mark structural acceptance, worth anchoring beyond
    LevelType.DAILY_POC,
    LevelType.WEEKLY_POC,
    LevelType.MONTHLY_POC,
    # SMC delivery — if a fresh OB/FVG anchors the zone, stop must sit
    # beyond the OB low (long) / OB high (short)
    LevelType.ORDER_BLOCK_BULL,
    LevelType.ORDER_BLOCK_BEAR,
    LevelType.FVG_BULL,
    LevelType.FVG_BEAR,
}


def compute_structural_anchor_ticks(
    zone_members: Iterable,
    trade_direction: int,
    entry_price: float,
    buffer_ticks: float = 2.0,
) -> float | None:
    """Stop distance (in ticks) anchored to the nearest structural level.

    For a LONG trade (trade_direction=+1), we look BELOW entry for support
    structures. For a SHORT, we look ABOVE. Stop sits `buffer_ticks` past
    the closest structural level in that direction — invalidates only if
    price genuinely breaks the structure.

    Args:
        zone_members: iterable of ZoneMember objects with `.level_type` and
            `.price` attributes.
        trade_direction: +1 long, -1 short.
        entry_price: trade entry price in price units.
        buffer_ticks: ticks to sit beyond the structural level (default 2).

    Returns None when no structural level is present in the relevant
    direction — caller falls back to the trained stop only.
    """
    if trade_direction == 0 or not zone_members:
        return None

    # Collect structural prices in the stop direction
    candidates: list[float] = []
    for member in zone_members:
        lt = getattr(member, "level_type", None)
        price = float(getattr(member, "price", 0.0))
        if lt not in _STRUCTURAL_LEVEL_TYPES:
            continue
        if trade_direction > 0:
            # Long: stop goes below — need structure BELOW entry
            if price < entry_price:
                candidates.append(price)
        else:
            # Short: stop goes above — need structure ABOVE entry
            if price > entry_price:
                candidates.append(price)

    if not candidates:
        return None

    # Closest structural level to entry
    if trade_direction > 0:
        nearest = max(candidates)  # closest-below = largest of the below-entry set
    else:
        nearest = min(candidates)  # closest-above = smallest of the above-entry set

    distance_price = abs(entry_price - nearest)
    distance_ticks = distance_price / TICK_SIZE + buffer_ticks
    return float(distance_ticks)


# --- Full pipeline ---------------------------------------------------------

_STOP_FLOOR_TICKS: float = 6.0
_STOP_CEIL_TICKS: float = 50.0


def apply_stop_adjustments(
    base_stop_ticks: float,
    composite_confidence: float,
    risk_modulation: float,
    zone_members: Iterable | None = None,
    trade_direction: int = 0,
    entry_price: float = 0.0,
    structural_buffer_ticks: float = 2.0,
) -> dict:
    """Produce the final stop_ticks + breakdown of each adjustment.

    Returns:
      {
        "base_ticks": float,          # trained TriggerGBT prediction
        "conf_scale": float,          # confidence multiplier
        "regime_scale": float,        # regime multiplier
        "scaled_ticks": float,        # base × conf × regime
        "structural_anchor_ticks": float | None,  # nearest OB-anchored stop
        "final_ticks": float,         # max(scaled, anchor) clipped to [6, 50]
      }
    """
    conf_scale = compute_confidence_scale(composite_confidence)
    regime_scale = compute_regime_scale(risk_modulation)
    scaled = float(base_stop_ticks) * conf_scale * regime_scale

    anchor = None
    if zone_members is not None and trade_direction != 0 and entry_price > 0:
        anchor = compute_structural_anchor_ticks(
            zone_members=zone_members,
            trade_direction=trade_direction,
            entry_price=entry_price,
            buffer_ticks=structural_buffer_ticks,
        )

    final = scaled if anchor is None else max(scaled, anchor)
    final = max(_STOP_FLOOR_TICKS, min(_STOP_CEIL_TICKS, final))

    return {
        "base_ticks": float(base_stop_ticks),
        "conf_scale": conf_scale,
        "regime_scale": regime_scale,
        "scaled_ticks": scaled,
        "structural_anchor_ticks": anchor,
        "final_ticks": float(final),
    }
