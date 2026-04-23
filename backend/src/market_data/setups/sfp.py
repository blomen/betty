"""Swing Failure Pattern: price breaks swing H/L then CLOSES back inside."""

from .detector import DetectorContext, SetupCandidate


def detect_sfp(ctx: DetectorContext) -> list[SetupCandidate]:
    """Detect SFP — price pierces a level but closes back inside (requires close confirmation)."""
    candidates = []

    # SFP at highs → short (price broke above resistance but closed back below)
    resistance_levels = [
        ("vah", ctx.vp.vah),
        ("pdh", ctx.session_levels.pdh),
        ("ib_high", ctx.session_levels.ib_high),
    ]

    for level_name, level_price in resistance_levels:
        if not level_price or level_price <= 0:
            continue
        # Price is now below level (closed back) and delta shows unwind
        if ctx.last_price < level_price and ctx.last_price > level_price * 0.997:
            if ctx.orderflow.delta_unwind and ctx.orderflow.trapped_traders and ctx.macro_bias != "bull":
                candidates.append(
                    SetupCandidate(
                        setup_type="sfp",
                        setup_name=f"SFP Short at {level_name.upper()}",
                        direction="short",
                        level_touched=level_name,
                        entry_price=ctx.last_price,
                        stop_price=level_price * 1.002,  # SL above the swing high
                        target_1=ctx.vp.poc,
                        target_2=ctx.vp.val,
                        target_3=ctx.session_levels.pdl,
                        base_score=75.0,
                    )
                )

    # SFP at lows → long
    support_levels = [
        ("val", ctx.vp.val),
        ("pdl", ctx.session_levels.pdl),
        ("ib_low", ctx.session_levels.ib_low),
    ]

    for level_name, level_price in support_levels:
        if not level_price or level_price <= 0:
            continue
        if ctx.last_price > level_price and ctx.last_price < level_price * 1.003:
            if ctx.orderflow.delta_unwind and ctx.orderflow.trapped_traders and ctx.macro_bias != "bear":
                candidates.append(
                    SetupCandidate(
                        setup_type="sfp",
                        setup_name=f"SFP Long at {level_name.upper()}",
                        direction="long",
                        level_touched=level_name,
                        entry_price=ctx.last_price,
                        stop_price=level_price * 0.998,
                        target_1=ctx.vp.poc,
                        target_2=ctx.vp.vah,
                        target_3=ctx.session_levels.pdh,
                        base_score=75.0,
                    )
                )

    return candidates
