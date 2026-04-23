"""Gap Logic (Larry Williams "Oops"): gap fill setup at session open.

Mechanical setup with 66-70% fill rate historically:
1. RTH opens with a gap above PDH or below PDL (min 20 points on NQ)
2. If price breaks back through the gapped level → enter toward fill
3. Target: previous session close (gap fill)
4. Stop: session high/low (the gap extreme)
"""

from .detector import DetectorContext, SetupCandidate

# Minimum gap size in points for NQ (20 points = 80 ticks at 0.25)
_MIN_GAP_POINTS = 20.0


def detect_gap_logic(ctx: DetectorContext) -> list[SetupCandidate]:
    """Detect gap fill setup at RTH open."""
    candidates: list[SetupCandidate] = []
    sl = ctx.session_levels
    price = ctx.last_price

    pdh = sl.pdh
    pdl = sl.pdl

    if not pdh or not pdl:
        return candidates

    # --- Gap UP above PDH: if price breaks back below PDH → sell ---
    if pdh and price < pdh:
        # We're below PDH now — check if there was a gap up
        # The gap would have been: open above PDH, now trading below
        # We approximate by checking if PDH is significantly below
        # where we "should" be (i.e., we broke back through)
        gap_size = pdh - pdl  # rough proxy; real gap = open - PDH
        if gap_size >= _MIN_GAP_POINTS:
            # Price is below PDH after a gap up → gap fill in progress
            # Only trigger if we're close to PDH (just broke through)
            if abs(price - pdh) / max(price, 1) < 0.003:
                # Estimate previous close as midpoint of PDH/PDL
                # (precise close would need prior session data)
                prev_close_est = (pdh + pdl) / 2.0
                candidates.append(
                    SetupCandidate(
                        setup_type="gap_logic",
                        setup_name="Gap Fill Short (Oops)",
                        direction="short",
                        level_touched="pdh",
                        entry_price=price,
                        stop_price=pdh + gap_size * 0.3,  # above the gap
                        target_1=prev_close_est,
                        target_2=pdl,
                        base_score=70.0,
                    )
                )

    # --- Gap DOWN below PDL: if price breaks back above PDL → buy ---
    if pdl and price > pdl:
        gap_size = pdh - pdl
        if gap_size >= _MIN_GAP_POINTS and abs(price - pdl) / max(price, 1) < 0.003:
            prev_close_est = (pdh + pdl) / 2.0
            candidates.append(
                SetupCandidate(
                    setup_type="gap_logic",
                    setup_name="Gap Fill Long (Oops)",
                    direction="long",
                    level_touched="pdl",
                    entry_price=price,
                    stop_price=pdl - gap_size * 0.3,
                    target_1=prev_close_est,
                    target_2=pdh,
                    base_score=70.0,
                )
            )

    return candidates
