"""Fakeout / Head Fake: convincing break that reverses, POC/VWAP holds."""

from .detector import DetectorContext, SetupCandidate


def detect_fakeout(ctx: DetectorContext) -> list[SetupCandidate]:
    """Detect fakeout — apparent breakout reverses with delta divergence."""
    candidates = []
    poc = ctx.vp.poc
    vwap = ctx.vwap.vwap if ctx.vwap else None

    if not poc:
        return []

    # Key confirmation: delta divergence (price says breakout, delta says no)
    if not ctx.orderflow.delta_divergence:
        return []

    # Fakeout above resistance → short
    resistance_levels = [
        ("vah", ctx.vp.vah),
        ("pdh", ctx.session_levels.pdh),
    ]

    for level_name, level_price in resistance_levels:
        if not level_price:
            continue
        # Price broke above then returned near level
        if ctx.last_price <= level_price * 1.001 and ctx.last_price >= level_price * 0.997:
            if ctx.macro_bias != "bull":
                # Confirm POC/VWAP is holding (price above both)
                anchor_holds = (vwap and ctx.last_price > vwap * 0.998) or ctx.last_price > poc * 0.998
                if anchor_holds or ctx.orderflow.vsa_absorption:
                    candidates.append(
                        SetupCandidate(
                            setup_type="fakeout",
                            setup_name=f"Fakeout Short at {level_name.upper()}",
                            direction="short",
                            level_touched=level_name,
                            entry_price=ctx.last_price,
                            stop_price=level_price * 1.003,
                            target_1=poc,
                            target_2=ctx.vp.val,
                            target_3=ctx.session_levels.pdl,
                            base_score=68.0,
                        )
                    )

    # Fakeout below support → long
    support_levels = [
        ("val", ctx.vp.val),
        ("pdl", ctx.session_levels.pdl),
    ]

    for level_name, level_price in support_levels:
        if not level_price:
            continue
        if ctx.last_price >= level_price * 0.999 and ctx.last_price <= level_price * 1.003:
            if ctx.macro_bias != "bear":
                anchor_holds = (vwap and ctx.last_price < vwap * 1.002) or ctx.last_price < poc * 1.002
                if anchor_holds or ctx.orderflow.vsa_absorption:
                    candidates.append(
                        SetupCandidate(
                            setup_type="fakeout",
                            setup_name=f"Fakeout Long at {level_name.upper()}",
                            direction="long",
                            level_touched=level_name,
                            entry_price=ctx.last_price,
                            stop_price=level_price * 0.997,
                            target_1=poc,
                            target_2=ctx.vp.vah,
                            target_3=ctx.session_levels.pdh,
                            base_score=68.0,
                        )
                    )

    return candidates
