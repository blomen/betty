"""Add / pyramid policy — compound into winning positions when aligned.

Framework rule the user articulated: "if we already have a position and were
confident that it will break through next level (cont) depending on how
confident we are we can increase the size once more and snowball with the
earlier profits."

This module answers one question at each new level touch:

    Should we ADD to the existing position, given current conditions?

Four conditions must all hold for a pyramid add to fire:

  1. We're already in a position (`pos_side != flat`).
  2. The model's new action direction MATCHES the position direction.
     CONT + long position, REV + short position, etc. Opposing direction
     means FLIP, not pyramid — handled elsewhere.
  3. Current composite confidence ≥ `MIN_CONFIDENCE` — we're confident in
     the new entry, not just taking any aligned touch.
  4. The position is in profit (`unrealized_R > 0`). Framework: "snowball
     with earlier profits" — never pyramid into a losing trade.

When all four hold, we compute:

    add_size = base_size_mult × ADD_FRACTION

where ADD_FRACTION < 1.0 (half-Kelly style — additions are smaller than
initial entries to bound compounded risk). The total position size is
capped at MAX_POSITION_MULT to prevent runaway accumulation on long
winning streaks.

Rule-based MVP. If this produces measurable lift we can replace with a
trained add_model head whose label is forward-window R improvement from
adding vs holding.
"""

from __future__ import annotations

from dataclasses import dataclass

# --- Thresholds ---------------------------------------------------------------
# Minimum composite confidence to consider pyramiding. Below this, just trail
# the stop — no new size.
MIN_CONFIDENCE: float = 0.6

# Fraction of the base size tier to add on each pyramid. 0.5 = half-Kelly
# compounding. Lower is more conservative.
ADD_FRACTION: float = 0.5

# Hard cap on total position size. 3.0 lets us stack base (1.0) + 2 adds
# (0.5 each) OR base × 1.5 regime + 1 add — plenty of compounding room
# without runaway risk.
MAX_POSITION_MULT: float = 3.0

# Minimum unrealized R before considering an add. Framework says "earlier
# profits" — require the position to be materially in profit, not just
# barely positive. 0.3R = clear profit, not just noise.
MIN_UNREALIZED_R: float = 0.3


@dataclass(frozen=True)
class PyramidDecision:
    """Output of the pyramid policy check."""

    should_add: bool
    add_size: float  # size to add on top of existing position (0.0 if no add)
    reason: str  # short tag explaining the decision
    detail: str  # human-readable detail


def check_pyramid(
    pos_side: str,  # "flat", "long", "short"
    pos_size: float,  # current total size (multiplier, where 1.0 = base Kelly)
    unrealized_R: float,  # current position's unrealized R
    action_direction: int,  # +1 long-direction, -1 short-direction, 0 skip
    base_size_mult: float,  # SizeModel×narrative output for THIS new touch
    composite_confidence: float,
    min_confidence: float = MIN_CONFIDENCE,
    add_fraction: float = ADD_FRACTION,
    max_position_mult: float = MAX_POSITION_MULT,
    min_unrealized_r: float = MIN_UNREALIZED_R,
) -> PyramidDecision:
    """Decide whether to pyramid (add size) vs just trail the stop.

    Returns PyramidDecision with should_add, add_size, and a reason tag.
    """
    # Position state check
    pos_dir = 1 if pos_side == "long" else -1 if pos_side == "short" else 0
    if pos_dir == 0:
        return PyramidDecision(
            should_add=False,
            add_size=0.0,
            reason="no_position",
            detail="pos_side is flat — open normally, not pyramid",
        )

    # Alignment check — opposite direction is flip, not pyramid
    if pos_dir != action_direction:
        return PyramidDecision(
            should_add=False,
            add_size=0.0,
            reason="opposite_direction",
            detail=f"pos_side={pos_side} vs action_direction={action_direction:+d} — flip, not pyramid",
        )

    # Confidence gate
    if composite_confidence < min_confidence:
        return PyramidDecision(
            should_add=False,
            add_size=0.0,
            reason="low_confidence",
            detail=f"confidence {composite_confidence:.3f} < {min_confidence:.2f}",
        )

    # Profit-cushion requirement
    if unrealized_R < min_unrealized_r:
        return PyramidDecision(
            should_add=False,
            add_size=0.0,
            reason="no_profit_cushion",
            detail=f"unrealized_R {unrealized_R:+.2f} < {min_unrealized_r:.2f}",
        )

    # Position-size cap — don't stack past the hard ceiling
    if pos_size >= max_position_mult:
        return PyramidDecision(
            should_add=False,
            add_size=0.0,
            reason="size_cap",
            detail=f"pos_size {pos_size:.2f} already at cap {max_position_mult:.2f}",
        )

    # Compute add size, clip so total doesn't exceed the cap
    raw_add = base_size_mult * add_fraction
    headroom = max_position_mult - pos_size
    add_size = min(raw_add, headroom)

    if add_size <= 0.0:
        return PyramidDecision(
            should_add=False,
            add_size=0.0,
            reason="no_headroom",
            detail=f"base_size={base_size_mult:.2f} but headroom={headroom:.2f}",
        )

    return PyramidDecision(
        should_add=True,
        add_size=float(add_size),
        reason="pyramid_add",
        detail=(
            f"add {add_size:.2f} (frac {add_fraction:.2f} × base {base_size_mult:.2f}) on "
            f"{pos_side} at {unrealized_R:+.2f}R profit, conf {composite_confidence:.3f}"
        ),
    )
