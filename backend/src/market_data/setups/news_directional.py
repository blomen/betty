"""News Directional: post-release directional candle with VSA confirmation."""
from .detector import DetectorContext, SetupCandidate


def detect_news_directional(ctx: DetectorContext) -> list[SetupCandidate]:
    """Detect News Directional — directional M1 candle after scheduled release.

    Note: This detector relies on tick_vol_accelerating as proxy for news spike.
    Real news calendar integration is deferred to v2.
    """
    candidates = []

    # News spike proxy: very high tick volume + strong delta alignment
    if not ctx.orderflow.tick_vol_accelerating:
        return []
    if not ctx.orderflow.delta_aligned:
        return []

    # Must also have VSA confirmation (big candle, not absorption)
    if ctx.orderflow.vsa_absorption:
        return []  # Absorption = rejection, not directional

    # Bullish news candle → long
    if ctx.orderflow.delta > 0 and ctx.orderflow.cvd_trend == "rising":
        if ctx.macro_bias != "bear":
            candidates.append(SetupCandidate(
                setup_type="news_directional",
                setup_name="News Directional Long",
                direction="long",
                level_touched="news_spike",
                entry_price=ctx.last_price,
                stop_price=ctx.last_price * 0.997,  # Tight stop: 0.3%
                target_1=ctx.vp.vah if ctx.vp.vah and ctx.vp.vah > ctx.last_price else ctx.last_price * 1.005,
                target_2=ctx.session_levels.pdh,
                target_3=ctx.session_levels.weekly_high,
                base_score=65.0,  # Lower base: news is noisy
            ))

    # Bearish news candle → short
    if ctx.orderflow.delta < 0 and ctx.orderflow.cvd_trend == "falling":
        if ctx.macro_bias != "bull":
            candidates.append(SetupCandidate(
                setup_type="news_directional",
                setup_name="News Directional Short",
                direction="short",
                level_touched="news_spike",
                entry_price=ctx.last_price,
                stop_price=ctx.last_price * 1.003,
                target_1=ctx.vp.val if ctx.vp.val and ctx.vp.val < ctx.last_price else ctx.last_price * 0.995,
                target_2=ctx.session_levels.pdl,
                target_3=ctx.session_levels.weekly_low,
                base_score=65.0,
            ))

    return candidates
