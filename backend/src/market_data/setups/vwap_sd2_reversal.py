"""2nd SD VWAP Reversal: mean-reversion from extended VWAP bands.

Secondary setup (profit cushion required). Mid-to-late session:
1. Price reaches 2nd standard deviation of session VWAP
2. Absorption + initiative reversal on footprint
3. Enter toward POC (fair value)
4. Win rate ~40% at 1:2.5 to 1:3 R:R
"""
from .detector import DetectorContext, SetupCandidate


def detect_vwap_sd2_reversal(ctx: DetectorContext) -> list[SetupCandidate]:
    """Detect reversal at 2nd SD VWAP bands."""
    candidates: list[SetupCandidate] = []
    vwap = ctx.vwap
    of = ctx.orderflow
    vp = ctx.vp
    price = ctx.last_price

    if not vwap or not vwap.sd2_upper or not vwap.sd2_lower:
        return candidates
    if not vwap.vwap:
        return candidates

    # Proximity: within 0.15% of 2nd SD band
    prox = 0.0015

    # --- At upper 2SD → short toward POC ---
    at_upper = abs(price - vwap.sd2_upper) / max(price, 1) < prox
    if at_upper and price > vwap.vwap:
        # Need some sign of exhaustion: delta weakening, absorption, or volume drying
        exhaustion = (
            of.cvd_trend in ("flat", "falling")
            or of.passive_active_ratio > 1.5
            or of.delta_pct < -0.2
        )
        if exhaustion:
            # Stop at 3SD if available, else 0.3% above
            stop = vwap.sd3_upper if vwap.sd3_upper else price * 1.003
            candidates.append(SetupCandidate(
                setup_type="vwap_sd2_reversal",
                setup_name="2SD VWAP Short (mean reversion)",
                direction="short",
                level_touched="vwap_sd2",
                entry_price=price,
                stop_price=stop,
                target_1=vwap.vwap,  # POC/VWAP = highest prob target
                target_2=vwap.sd1_lower if vwap.sd1_lower else None,
                base_score=68.0,  # lower base — secondary setup
            ))

    # --- At lower 2SD → long toward POC ---
    at_lower = abs(price - vwap.sd2_lower) / max(price, 1) < prox
    if at_lower and price < vwap.vwap:
        exhaustion = (
            of.cvd_trend in ("flat", "rising")
            or of.passive_active_ratio > 1.5
            or of.delta_pct > 0.2
        )
        if exhaustion:
            stop = vwap.sd3_lower if vwap.sd3_lower else price * 0.997
            candidates.append(SetupCandidate(
                setup_type="vwap_sd2_reversal",
                setup_name="2SD VWAP Long (mean reversion)",
                direction="long",
                level_touched="vwap_sd2",
                entry_price=price,
                stop_price=stop,
                target_1=vwap.vwap,
                target_2=vwap.sd1_upper if vwap.sd1_upper else None,
                base_score=68.0,
            ))

    return candidates
