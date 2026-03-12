"""IB Break setup: price exits first 60-min range with conviction."""
from .detector import DetectorContext, SetupCandidate


def detect_ib_break(ctx: DetectorContext) -> list[SetupCandidate]:
    """Detect Initial Balance breakout — price exits IB range with delta + tick vol."""
    candidates = []
    ib_h = ctx.session_levels.ib_high
    ib_l = ctx.session_levels.ib_low
    if not ib_h or not ib_l:
        return []

    ib_range = ib_h - ib_l
    if ib_range <= 0:
        return []

    # Break above IB → long
    if ctx.last_price > ib_h and ctx.macro_bias != "bear":
        if ctx.orderflow.delta_aligned and ctx.orderflow.tick_vol_accelerating:
            candidates.append(SetupCandidate(
                setup_type="ib_break",
                setup_name="IB Break Long",
                direction="long",
                level_touched="ib_high",
                entry_price=ctx.last_price,
                stop_price=ib_h - ib_range * 0.25,  # SL = 25% back inside IB
                target_1=ib_h + ib_range * 1.0,      # TP1 = 1x IB extension
                target_2=ib_h + ib_range * 1.5,      # TP2 = 1.5x
                target_3=ctx.session_levels.weekly_high,
                base_score=70.0,
            ))

    # Break below IB → short
    if ctx.last_price < ib_l and ctx.macro_bias != "bull":
        if ctx.orderflow.delta_aligned and ctx.orderflow.tick_vol_accelerating:
            candidates.append(SetupCandidate(
                setup_type="ib_break",
                setup_name="IB Break Short",
                direction="short",
                level_touched="ib_low",
                entry_price=ctx.last_price,
                stop_price=ib_l + ib_range * 0.25,
                target_1=ib_l - ib_range * 1.0,
                target_2=ib_l - ib_range * 1.5,
                target_3=ctx.session_levels.weekly_low,
                base_score=70.0,
            ))

    return candidates
