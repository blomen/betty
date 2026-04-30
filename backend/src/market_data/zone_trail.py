"""Zone-trail target computation for in-position trail-on-continuation.

Pure module: no I/O, no side effects, no asyncio. Caller owns the
broker.modify_stop call.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

TICK_SIZE = 0.25
PEAK_R_TRAIL_THRESHOLD = 2.0  # match BE-lock threshold


def _round_tick(price: float) -> float:
    return round(price / TICK_SIZE) * TICK_SIZE


def compute_zone_trail_target(
    tracker,
    touched_zone,
    all_zones: list,
    current_zone_R: float,
    of_score: float | None = None,
) -> tuple[float, float] | None:
    """Compute (target_stop, advance_zone_R) for a zone advance, or None.

    Conditions (must all hold):
      - tracker is in position (not flat)
      - tracker.peak_R >= 2.0 (BE-lock has fired)
      - touched_zone is in trade direction past entry (above for long, below for short)
      - touched_zone's R-multiple > current_zone_R (genuine new advance)
      - risk_unit > 0 (computable R)

    Trail target by orderflow strength (when ``of_score`` provided):
      - of_score >= 0.7 (strong continuation conviction):
          long  → touched_zone.lower_bound (TIGHT — lock aggressively)
          short → touched_zone.upper_bound
      - of_score in [0.3, 0.7): default behaviour (prior zone edge)
      - of_score < 0.3: caller is expected to skip the trail entirely;
        if it still calls us we use the prior-zone fallback.

    Default trail target (no of_score / mid orderflow):
      - prior zone exists between entry and touched_zone in trade direction:
          long → prior_zone.upper_bound
          short → prior_zone.lower_bound
      - no prior zone: fallback to entry + 1.0R (long) / entry - 1.0R (short)
    """
    if tracker.is_flat or tracker.entry_price <= 0:
        return None
    if tracker.peak_R < PEAK_R_TRAIL_THRESHOLD:
        return None
    risk_unit = abs(tracker.entry_price - tracker.stop_price)
    if risk_unit <= 0:
        return None

    side = tracker.side
    entry = tracker.entry_price

    # In-trade-direction check
    if side == "long":
        if touched_zone.center_price <= entry:
            return None
    elif side == "short":
        if touched_zone.center_price >= entry:
            return None
    else:
        return None

    # R-multiple of the touched zone
    if side == "long":
        advance_zone_R = (touched_zone.center_price - entry) / risk_unit
    else:
        advance_zone_R = (entry - touched_zone.center_price) / risk_unit

    # Idempotence: must be a NEW advance
    if advance_zone_R <= current_zone_R + 1e-6:
        return None

    # Find prior zone in trade direction (between entry and touched_zone)
    prior_zone = None
    if side == "long":
        candidates = [
            z
            for z in all_zones
            if z is not touched_zone and z.center_price > entry and z.center_price < touched_zone.center_price
        ]
        if candidates:
            prior_zone = max(candidates, key=lambda z: z.center_price)
    else:
        candidates = [
            z
            for z in all_zones
            if z is not touched_zone and z.center_price < entry and z.center_price > touched_zone.center_price
        ]
        if candidates:
            prior_zone = min(candidates, key=lambda z: z.center_price)

    # Strong-orderflow tighten: lock aggressively at the touched zone's
    # near edge. The continuation conviction is high enough that giving
    # the trade more room is more likely to give back than to extend.
    if of_score is not None and of_score >= 0.7:
        target_stop = _round_tick(touched_zone.lower_bound if side == "long" else touched_zone.upper_bound)
        return target_stop, advance_zone_R

    if prior_zone is not None:
        target_stop = _round_tick(prior_zone.upper_bound if side == "long" else prior_zone.lower_bound)
    else:
        # Fallback: entry + 1.0R (long) / entry - 1.0R (short)
        if side == "long":
            target_stop = _round_tick(entry + risk_unit)
        else:
            target_stop = _round_tick(entry - risk_unit)

    return target_stop, advance_zone_R
