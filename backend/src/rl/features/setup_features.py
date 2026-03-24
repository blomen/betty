"""Setup detection feature extraction — encodes active AMT setups as binary features."""
from __future__ import annotations

import logging

import numpy as np

from ...market_data.setups.detector import DetectorContext, run_all_detectors

log = logging.getLogger(__name__)

# 13 setup types → 13 binary features
SETUP_TYPES = [
    "poor_extreme",
    "ib_break",
    "spring",
    "sfp",
    "rule_80",
    "fakeout",
    "break_from_balance",
    "double_distribution",
    "news_directional",
    "absorption",
    "vwap_sd2_reversal",
    "gap_logic",
    "pbd",
]

_N_FEATURES = len(SETUP_TYPES)  # 9


def extract_setup_features(
    state: dict,
) -> np.ndarray:
    """Extract 13 binary setup features from the current market state.

    Each feature is 1.0 if the corresponding setup was detected, 0.0 otherwise.
    Returns zeros(13) if the state lacks sufficient context to run detectors.
    """
    feats = np.zeros(_N_FEATURES, dtype=np.float32)

    vp = state.get("volume_profile")
    vwap = state.get("vwap_bands")
    session_levels = state.get("session_levels")
    tpo_profile_obj = state.get("tpo_profile_obj")  # TPOProfile object
    of_signals = state.get("orderflow_signals")

    # Need at minimum VP, session levels, TPO, and orderflow to run detectors
    if not all([vp, session_levels, tpo_profile_obj, of_signals]):
        return feats

    price = float(state.get("price", 0.0))
    if price <= 0:
        return feats

    try:
        ctx = DetectorContext(
            vp=vp,
            vwap=vwap,
            session_levels=session_levels,
            tpo=tpo_profile_obj,
            orderflow=of_signals,
            last_price=price,
            macro_bias=state.get("macro_bias"),
            structure=state.get("structure"),
            day_type=state.get("day_type"),
        )
        candidates = run_all_detectors(ctx)
    except Exception:
        return feats

    # Encode detected setups as binary vector
    for candidate in candidates:
        if candidate.setup_type in SETUP_TYPES:
            idx = SETUP_TYPES.index(candidate.setup_type)
            feats[idx] = 1.0

    return feats
