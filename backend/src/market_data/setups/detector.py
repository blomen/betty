"""Setup detector orchestrator: runs all individual detectors and returns scored results."""
from dataclasses import dataclass
from datetime import datetime

from ..levels import VolumeProfile, VWAPBands, SessionLevels
from ..tpo import TPOProfile
from ..orderflow import OrderflowSignals


@dataclass
class SetupCandidate:
    """A detected setup opportunity."""
    setup_type: str        # "spring", "sfp", "poor_extreme", etc.
    setup_name: str        # Human readable: "Poor High Reversal"
    direction: str         # "long" or "short"
    level_touched: str     # Which level triggered: "vah", "pdh", "ib_high", etc.
    entry_price: float
    stop_price: float
    target_1: float
    target_2: float | None = None
    target_3: float | None = None
    base_score: float = 65.0  # Default until historical win rate available
    detected_at: datetime | None = None


@dataclass
class DetectorContext:
    """All data a setup detector needs."""
    vp: VolumeProfile
    vwap: VWAPBands | None
    session_levels: SessionLevels
    tpo: TPOProfile
    orderflow: OrderflowSignals
    last_price: float
    # Context gates
    macro_bias: str | None  # "bull", "bear", "neutral"
    structure: str | None   # "uptrend", "downtrend", "ranging"
    day_type: str | None    # "trend", "normal", etc.


def run_all_detectors(ctx: DetectorContext) -> list[SetupCandidate]:
    """Run all setup detectors and return candidates."""
    from .poor_extreme import detect_poor_extreme
    from .ib_break import detect_ib_break
    from .spring import detect_spring
    from .sfp import detect_sfp
    from .rule_80 import detect_rule_80
    from .fakeout import detect_fakeout
    from .break_from_balance import detect_break_from_balance
    from .double_distribution import detect_double_distribution
    from .news_directional import detect_news_directional

    detectors = [
        detect_poor_extreme,
        detect_ib_break,
        detect_spring,
        detect_sfp,
        detect_rule_80,
        detect_fakeout,
        detect_break_from_balance,
        detect_double_distribution,
        detect_news_directional,
    ]

    candidates = []
    for detector in detectors:
        try:
            result = detector(ctx)
            if result:
                candidates.extend(result if isinstance(result, list) else [result])
        except Exception:
            pass  # Individual detector failure shouldn't block others

    return candidates
