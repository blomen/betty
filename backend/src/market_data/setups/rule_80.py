"""80% Rule: opens outside prior VA, trades back inside for 2+ TPO periods."""
from .detector import DetectorContext, SetupCandidate


def detect_rule_80(ctx: DetectorContext) -> list[SetupCandidate]:
    """Detect 80% Rule — price opens outside VA, re-enters, targets opposite VA extreme."""
    candidates = []
    vah = ctx.vp.vah
    val = ctx.vp.val
    poc = ctx.vp.poc

    if not vah or not val or vah <= val:
        return []

    # Check TPO profile: need at least 2 letters inside VA after opening outside
    # Use ib_tpo_count as proxy for time spent inside VA
    if ctx.tpo.ib_tpo_count < 4:  # 2 TPO periods × ~2 price levels each
        return []

    # Opened above VA, now trading inside → target VAL (80% chance)
    if ctx.session_levels.ib_high and ctx.session_levels.ib_high > vah:
        if val < ctx.last_price < vah:
            candidates.append(SetupCandidate(
                setup_type="rule_80",
                setup_name="80% Rule Short (opened above VA)",
                direction="short",
                level_touched="vah",
                entry_price=ctx.last_price,
                stop_price=vah * 1.002,
                target_1=poc,
                target_2=val,
                target_3=ctx.session_levels.pdl,
                base_score=78.0,  # High base: 80% historical probability
            ))

    # Opened below VA, now trading inside → target VAH
    if ctx.session_levels.ib_low and ctx.session_levels.ib_low < val:
        if val < ctx.last_price < vah:
            candidates.append(SetupCandidate(
                setup_type="rule_80",
                setup_name="80% Rule Long (opened below VA)",
                direction="long",
                level_touched="val",
                entry_price=ctx.last_price,
                stop_price=val * 0.998,
                target_1=poc,
                target_2=vah,
                target_3=ctx.session_levels.pdh,
                base_score=78.0,
            ))

    return candidates
