"""Absorption → Initiative: passive orders absorb aggressive flow, then initiative takes over.

Core Fabio pattern. At VA boundary during balance:
1. Aggressive sellers/buyers get absorbed by passive counterpart
2. CVD divergence: delta vs price diverge (absorption signal)
3. Initiative candle fires in opposite direction with high volume + imbalance
4. Enter in initiative direction, stop beyond absorption zone, target opposite VA.
"""
from .detector import DetectorContext, SetupCandidate


def detect_absorption(ctx: DetectorContext) -> list[SetupCandidate]:
    """Detect Absorption → Initiative at VA boundaries."""
    candidates: list[SetupCandidate] = []
    of = ctx.orderflow
    vp = ctx.vp
    sl = ctx.session_levels
    price = ctx.last_price

    if not vp.vah or not vp.val or not vp.poc:
        return candidates

    va_range = vp.vah - vp.val
    if va_range <= 0:
        return candidates

    # Proximity threshold: within 0.3% of VA boundary
    prox = 0.003

    # --- Absorption at VAH → short (buyers absorbed, sellers take initiative) ---
    at_vah = vp.vah and abs(price - vp.vah) / max(price, 1) < prox
    if at_vah:
        # CVD divergence: price near highs but delta weakening
        cvd_divergence = (
            of.cvd_trend in ("flat", "falling")
            and of.passive_active_ratio > 1.2  # more passive than aggressive
        )
        # Initiative selling: strong negative delta candle
        initiative_sell = of.delta_pct < -0.3 and of.stacked_imbalance_direction == "sell"

        if cvd_divergence or initiative_sell:
            candidates.append(SetupCandidate(
                setup_type="absorption",
                setup_name="Absorption Short at VAH",
                direction="short",
                level_touched="vah",
                entry_price=price,
                stop_price=vp.vah + va_range * 0.1,  # just above VAH
                target_1=vp.poc,
                target_2=vp.val,
                target_3=sl.pdl,
                base_score=72.0,
            ))

    # --- Absorption at VAL → long (sellers absorbed, buyers take initiative) ---
    at_val = vp.val and abs(price - vp.val) / max(price, 1) < prox
    if at_val:
        cvd_divergence = (
            of.cvd_trend in ("flat", "rising")
            and of.passive_active_ratio > 1.2
        )
        initiative_buy = of.delta_pct > 0.3 and of.stacked_imbalance_direction == "buy"

        if cvd_divergence or initiative_buy:
            candidates.append(SetupCandidate(
                setup_type="absorption",
                setup_name="Absorption Long at VAL",
                direction="long",
                level_touched="val",
                entry_price=price,
                stop_price=vp.val - va_range * 0.1,
                target_1=vp.poc,
                target_2=vp.vah,
                target_3=sl.pdh,
                base_score=72.0,
            ))

    return candidates
