"""PBD Profile setups (Tom Forvald framework).

Uses TPO profile shape + volume profile to classify the current auction
as P (uptrend), B (downtrend), or D (consolidation), then detects
4 sub-variants per shape based on price action at range boundaries.

P1/B1: Failed auction at range boundary → trade to other side (highest win rate)
P2/B2: Strong breakout with volume → continuation on retest
P3/B3: Breakout with exhaustion → fade back into range (counter-trend, lower WR)
P4/B4: Double breakout reversal → full trend reversal signal
"""
from .detector import DetectorContext, SetupCandidate


def detect_pbd(ctx: DetectorContext) -> list[SetupCandidate]:
    """Detect PBD sub-variant setups based on TPO shape and price location."""
    candidates: list[SetupCandidate] = []

    vah = ctx.vp.vah
    val = ctx.vp.val
    poc = ctx.vp.poc
    price = ctx.last_price
    of = ctx.orderflow
    sl = ctx.session_levels
    tpo = ctx.tpo

    if not vah or not val or not poc or vah <= val:
        return candidates

    va_range = vah - val
    prox = va_range * 0.05  # 5% of VA range as proximity threshold

    # Determine profile shape from TPO
    shape = getattr(tpo, "profile_shape", None)
    if not shape:
        return candidates

    # P-Profile: uptrend (fat at top, thin at bottom)
    if shape == "p":
        candidates.extend(_detect_p_setups(price, vah, val, poc, va_range, prox, of, sl))

    # B-Profile: downtrend (fat at bottom, thin at top)
    elif shape == "b":
        candidates.extend(_detect_b_setups(price, vah, val, poc, va_range, prox, of, sl))

    # D-Profile: consolidation (even distribution)
    elif shape in ("d", "balanced"):
        candidates.extend(_detect_d_setups(price, vah, val, poc, va_range, prox, of, sl))

    return candidates


def _detect_p_setups(
    price: float, vah: float, val: float, poc: float,
    va_range: float, prox: float, of, sl,
) -> list[SetupCandidate]:
    """P-profile (uptrend) sub-variants."""
    results: list[SetupCandidate] = []

    above_vah = price > vah + prox
    below_val = price < val - prox
    at_vah = abs(price - vah) <= prox
    at_val = abs(price - val) <= prox

    # P1: Failed auction BELOW range → long (buyers see cheap prices)
    # Price broke below VAL but is coming back inside with buying pressure
    if at_val and price > val:
        if of.cvd_trend in ("rising", "flat") or of.delta_pct > 0.15:
            results.append(SetupCandidate(
                setup_type="pbd",
                setup_name="P1: Failed Auction Below (Long)",
                direction="long",
                level_touched="val",
                entry_price=price,
                stop_price=val - va_range * 0.15,
                target_1=poc,
                target_2=vah,
                base_score=78.0,  # highest WR in P-profile
            ))

    # P2: Strong breakout ABOVE → continuation long
    if above_vah:
        if of.delta_aligned and of.stacked_imbalance_direction == "buy":
            results.append(SetupCandidate(
                setup_type="pbd",
                setup_name="P2: Breakout Continuation (Long)",
                direction="long",
                level_touched="vah",
                entry_price=price,
                stop_price=vah - va_range * 0.1,
                target_1=vah + va_range * 0.5,
                target_2=vah + va_range * 1.0,
                base_score=72.0,
            ))

    # P3: Exhaustion above → short fade (counter-trend, lower WR)
    if above_vah:
        volume_dying = of.passive_active_ratio > 1.5 or of.cvd_trend == "falling"
        if volume_dying and not of.delta_aligned:
            results.append(SetupCandidate(
                setup_type="pbd",
                setup_name="P3: Exhaustion Fade (Short)",
                direction="short",
                level_touched="vah",
                entry_price=price,
                stop_price=price + va_range * 0.2,
                target_1=vah,
                target_2=poc,
                base_score=62.0,  # lower WR — counter-trend
            ))

    # P4: Double breakout reversal — broke above, now broke BELOW entire range
    if below_val:
        if of.delta_aligned and of.stacked_imbalance_direction == "sell":
            results.append(SetupCandidate(
                setup_type="pbd",
                setup_name="P4: Reversal (Short)",
                direction="short",
                level_touched="val",
                entry_price=price,
                stop_price=val + va_range * 0.2,
                target_1=val - va_range * 0.5,
                target_2=val - va_range * 1.0,
                target_3=sl.pdl,
                base_score=70.0,
            ))

    return results


def _detect_b_setups(
    price: float, vah: float, val: float, poc: float,
    va_range: float, prox: float, of, sl,
) -> list[SetupCandidate]:
    """B-profile (downtrend) sub-variants — mirror of P."""
    results: list[SetupCandidate] = []

    above_vah = price > vah + prox
    below_val = price < val - prox
    at_vah = abs(price - vah) <= prox
    at_val = abs(price - val) <= prox

    # B1: Failed auction ABOVE range → short (sellers see expensive prices)
    if at_vah and price < vah:
        if of.cvd_trend in ("falling", "flat") or of.delta_pct < -0.15:
            results.append(SetupCandidate(
                setup_type="pbd",
                setup_name="B1: Failed Auction Above (Short)",
                direction="short",
                level_touched="vah",
                entry_price=price,
                stop_price=vah + va_range * 0.15,
                target_1=poc,
                target_2=val,
                base_score=78.0,
            ))

    # B2: Strong breakout BELOW → continuation short
    if below_val:
        if of.delta_aligned and of.stacked_imbalance_direction == "sell":
            results.append(SetupCandidate(
                setup_type="pbd",
                setup_name="B2: Breakout Continuation (Short)",
                direction="short",
                level_touched="val",
                entry_price=price,
                stop_price=val + va_range * 0.1,
                target_1=val - va_range * 0.5,
                target_2=val - va_range * 1.0,
                base_score=72.0,
            ))

    # B3: Exhaustion below → long fade (counter-trend)
    if below_val:
        volume_dying = of.passive_active_ratio > 1.5 or of.cvd_trend == "rising"
        if volume_dying and not of.delta_aligned:
            results.append(SetupCandidate(
                setup_type="pbd",
                setup_name="B3: Exhaustion Fade (Long)",
                direction="long",
                level_touched="val",
                entry_price=price,
                stop_price=price - va_range * 0.2,
                target_1=val,
                target_2=poc,
                base_score=62.0,
            ))

    # B4: Double breakout reversal — broke below, now broke ABOVE entire range
    if above_vah:
        if of.delta_aligned and of.stacked_imbalance_direction == "buy":
            results.append(SetupCandidate(
                setup_type="pbd",
                setup_name="B4: Reversal (Long)",
                direction="long",
                level_touched="vah",
                entry_price=price,
                stop_price=vah - va_range * 0.2,
                target_1=vah + va_range * 0.5,
                target_2=vah + va_range * 1.0,
                target_3=sl.pdh,
                base_score=70.0,
            ))

    return results


def _detect_d_setups(
    price: float, vah: float, val: float, poc: float,
    va_range: float, prox: float, of, sl,
) -> list[SetupCandidate]:
    """D-profile (consolidation) — ping-pong failed auctions at boundaries."""
    results: list[SetupCandidate] = []

    at_vah = abs(price - vah) <= prox
    at_val = abs(price - val) <= prox

    # D: Failed auction at VAH → short to other side
    if at_vah and price < vah:
        if of.cvd_trend in ("falling", "flat") or of.passive_active_ratio > 1.2:
            results.append(SetupCandidate(
                setup_type="pbd",
                setup_name="D: Range Short at VAH",
                direction="short",
                level_touched="vah",
                entry_price=price,
                stop_price=vah + va_range * 0.1,
                target_1=poc,
                target_2=val,
                base_score=70.0,
            ))

    # D: Failed auction at VAL → long to other side
    if at_val and price > val:
        if of.cvd_trend in ("rising", "flat") or of.passive_active_ratio > 1.2:
            results.append(SetupCandidate(
                setup_type="pbd",
                setup_name="D: Range Long at VAL",
                direction="long",
                level_touched="val",
                entry_price=price,
                stop_price=val - va_range * 0.1,
                target_1=poc,
                target_2=vah,
                base_score=70.0,
            ))

    return results
