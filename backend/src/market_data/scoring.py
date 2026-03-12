"""Scoring model: combines setup base score with confirmation adjustments."""
from .setups.detector import SetupCandidate, DetectorContext
from .orderflow import OrderflowSignals


def score_candidate(
    candidate: SetupCandidate,
    orderflow: OrderflowSignals,
    day_type_fits: bool,
    macro_aligned: bool,
    rf: int | None = None,
    aspr_percentile: float | None = None,
    timeframe_confluence: bool = False,
) -> float:
    """Apply adjustment factors to candidate's base score.

    Returns final score (0-100). Only surface to UI if >= 70.
    """
    score = candidate.base_score

    # Delta/CVD alignment
    if orderflow.delta_aligned:
        score += 10
    # Delta divergence (for reversal setups)
    if orderflow.delta_divergence and candidate.setup_type in ("spring", "sfp", "poor_extreme", "fakeout"):
        score += 10
    # VSA absorption
    if orderflow.vsa_absorption:
        score += 10
    # Tick volume
    if orderflow.tick_vol_accelerating:
        score += 8
    # Day type fit
    if day_type_fits:
        score += 10
    else:
        score -= 20
    # Macro alignment
    if macro_aligned:
        score += 5
    # Trapped traders
    if orderflow.trapped_traders:
        score += 8
    # Timeframe confluence (same setup visible on HTF)
    if timeframe_confluence:
        score += 10
    # RF/ASPR session context
    if rf is not None and aspr_percentile is not None:
        if aspr_percentile < 0.2:  # Compressed = breakout setups stronger
            if candidate.setup_type in ("ib_break", "break_from_balance"):
                score += 5
    # Passive/active ratio: high ratio at key level = absorption
    if orderflow.passive_active_ratio > 2.0:
        score += 5

    return max(0, min(100, score))


def day_type_fits_setup(day_type: str | None, setup_type: str) -> bool:
    """Check if setup type is valid for the day type."""
    if not day_type:
        return True  # Unknown = allow

    trend_setups = {"ib_break", "break_from_balance"}
    reversal_setups = {"spring", "sfp", "poor_extreme", "rule_80", "fakeout", "double_distribution"}

    if day_type == "trend":
        return setup_type in trend_setups or setup_type == "news_directional"
    elif day_type in ("normal", "normal_variation"):
        return setup_type in reversal_setups or setup_type == "news_directional"
    elif day_type == "neutral":
        return setup_type in ("break_from_balance", "news_directional")

    return True  # composite = anything goes


def filter_by_rr(candidates: list, min_rr: float = 1.5) -> list:
    """Filter candidates: only surface if TP1 R:R >= min_rr."""
    return [c for c in candidates if c.rr_tp1 and c.rr_tp1 >= min_rr]


def kelly_position_size(
    win_rate: float,
    avg_rr: float,
    account_balance: float,
    max_risk_pct: float = 0.02,
) -> float:
    """Kelly criterion position sizing, capped at max_risk_pct of account.

    Returns dollar risk amount (not contracts).
    """
    if win_rate <= 0 or avg_rr <= 0:
        return 0.0
    # Kelly fraction: f* = (bp - q) / b where b = avg_rr, p = win_rate, q = 1 - p
    b = avg_rr
    p = win_rate
    q = 1 - p
    kelly_f = (b * p - q) / b
    # Half-Kelly for safety
    half_kelly = kelly_f / 2
    # Cap at max_risk_pct
    risk_fraction = max(0, min(half_kelly, max_risk_pct))
    return round(account_balance * risk_fraction, 2)
