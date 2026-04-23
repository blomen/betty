"""Poor Extreme setup detector.

Trigger: Session makes new high/low on volume significantly below average;
thin tail in TPO profile.
"""

from .detector import DetectorContext, SetupCandidate


def detect_poor_extreme(ctx: DetectorContext) -> list[SetupCandidate]:
    """Detect poor extreme setups from TPO profile."""
    candidates = []

    # Check if day type is appropriate (not trend days)
    if ctx.day_type == "trend":
        return []

    # Poor high → short setup
    if ctx.tpo.poor_high and ctx.macro_bias != "bull":
        stop = ctx.session_levels.ib_high or ctx.vp.vah
        if stop and ctx.last_price > 0:
            candidates.append(
                SetupCandidate(
                    setup_type="poor_extreme",
                    setup_name="Poor High Reversal",
                    direction="short",
                    level_touched="session_high",
                    entry_price=ctx.last_price,
                    stop_price=stop * 1.001,  # Slightly above
                    target_1=ctx.vp.poc,
                    target_2=ctx.vp.val,
                    target_3=ctx.session_levels.pdl,
                    base_score=75.0,
                )
            )

    # Poor low → long setup
    if ctx.tpo.poor_low and ctx.macro_bias != "bear":
        stop = ctx.session_levels.ib_low or ctx.vp.val
        if stop and ctx.last_price > 0:
            candidates.append(
                SetupCandidate(
                    setup_type="poor_extreme",
                    setup_name="Poor Low Reversal",
                    direction="long",
                    level_touched="session_low",
                    entry_price=ctx.last_price,
                    stop_price=stop * 0.999,
                    target_1=ctx.vp.poc,
                    target_2=ctx.vp.vah,
                    target_3=ctx.session_levels.pdh,
                    base_score=75.0,
                )
            )

    return candidates
