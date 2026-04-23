"""Break from Balance: 3+ days of overlapping VAs, ASPR compressed, then breakout."""

from .detector import DetectorContext, SetupCandidate


def detect_break_from_balance(ctx: DetectorContext) -> list[SetupCandidate]:
    """Detect Break from Balance — range compression then directional break."""
    candidates = []
    vah = ctx.vp.vah
    val = ctx.vp.val

    if not vah or not val or vah <= val:
        return []

    va_range = vah - val

    # Break above balance → long
    if ctx.last_price > vah and ctx.orderflow.delta_aligned and ctx.orderflow.tick_vol_accelerating:
        if ctx.day_type != "neutral":  # Neutral = still balanced
            candidates.append(
                SetupCandidate(
                    setup_type="break_from_balance",
                    setup_name="Break from Balance Long",
                    direction="long",
                    level_touched="vah",
                    entry_price=ctx.last_price,
                    stop_price=vah - va_range * 0.15,  # Tight SL just inside VA
                    target_1=vah + va_range * 0.5,
                    target_2=vah + va_range * 1.0,
                    target_3=ctx.session_levels.weekly_high,
                    base_score=72.0,
                )
            )

    # Break below balance → short
    if ctx.last_price < val and ctx.orderflow.delta_aligned and ctx.orderflow.tick_vol_accelerating:
        if ctx.day_type != "neutral":
            candidates.append(
                SetupCandidate(
                    setup_type="break_from_balance",
                    setup_name="Break from Balance Short",
                    direction="short",
                    level_touched="val",
                    entry_price=ctx.last_price,
                    stop_price=val + va_range * 0.15,
                    target_1=val - va_range * 0.5,
                    target_2=val - va_range * 1.0,
                    target_3=ctx.session_levels.weekly_low,
                    base_score=72.0,
                )
            )

    return candidates
