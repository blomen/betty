"""Spring / Liquidity Trap: minor penetration below support, low volume, snap-back."""

from .detector import DetectorContext, SetupCandidate


def detect_spring(ctx: DetectorContext) -> list[SetupCandidate]:
    """Detect Wyckoff spring — brief dip below support on low volume, then reversal."""
    candidates = []

    # Check support levels: VAL, PDL, session low
    support_levels = [
        ("val", ctx.vp.val),
        ("pdl", ctx.session_levels.pdl),
    ]

    for level_name, level_price in support_levels:
        if not level_price or level_price <= 0:
            continue

        # Spring = price dipped below level but currently above (snap-back)
        penetration = level_price - ctx.last_price
        if -level_price * 0.003 < penetration < level_price * 0.001:
            # Price is near/just above level after dipping below
            if ctx.last_price >= level_price * 0.998:
                # Confirm: delta unwind (sellers exhausted) or low volume
                if ctx.orderflow.delta_unwind or not ctx.orderflow.tick_vol_accelerating:
                    if ctx.macro_bias != "bear":
                        candidates.append(
                            SetupCandidate(
                                setup_type="spring",
                                setup_name=f"Spring at {level_name.upper()}",
                                direction="long",
                                level_touched=level_name,
                                entry_price=ctx.last_price,
                                stop_price=level_price * 0.997,  # SL below spring low
                                target_1=ctx.vp.poc,
                                target_2=ctx.vp.vah,
                                target_3=ctx.session_levels.pdh,
                                base_score=72.0,
                            )
                        )

    return candidates
