"""Per-session TPO feature extraction for RL observation vector.

Replaces the old 13-feature composite TPO extractor with 26 features:
8 per session (Tokyo/London/NY) + 2 POC migration deltas.
"""
from __future__ import annotations

import numpy as np

from ..config import TICK_SIZE
from ...market_data.tpo import SessionTPO, SessionTPOSet

_FEATURES_PER_SESSION = 8
_N_SESSIONS = 3
_N_MIGRATION = 2
_N_FEATURES = _FEATURES_PER_SESSION * _N_SESSIONS + _N_MIGRATION  # 26

# Shape ordinal: p-shape = bullish (+1), b-shape = bearish (-1), d-shape = neutral (0)
_SHAPE_ORDINAL = {"p-shape": 1.0, "b-shape": -1.0, "d-shape": 0.0}


def _extract_single_session(
    session: SessionTPO | None,
    current_price: float,
) -> np.ndarray:
    """Extract 8 features from a single session TPO profile."""
    out = np.zeros(_FEATURES_PER_SESSION, dtype=np.float32)
    if session is None:
        return out

    poc, vah, val = session.poc, session.vah, session.val
    va_width = vah - val

    # 0: price_vs_poc (ticks, normalised to ~[-1, 1])
    out[0] = np.clip((current_price - poc) / TICK_SIZE / 200.0, -1.0, 1.0)
    # 1: price_vs_vah
    out[1] = np.clip((current_price - vah) / TICK_SIZE / 200.0, -1.0, 1.0)
    # 2: price_vs_val
    out[2] = np.clip((current_price - val) / TICK_SIZE / 200.0, -1.0, 1.0)
    # 3: shape ordinal
    out[3] = _SHAPE_ORDINAL.get(session.shape, 0.0)
    # 4: ib_range (zeroed if not valid)
    if session.ib_valid:
        out[4] = np.clip((session.ib_high - session.ib_low) / TICK_SIZE / 200.0, 0.0, 1.0)
        # 5: price_vs_ib_mid
        ib_mid = (session.ib_high + session.ib_low) / 2.0
        out[5] = np.clip((current_price - ib_mid) / TICK_SIZE / 200.0, -1.0, 1.0)
    # 6: poor_signal
    out[6] = float(session.poor_high) - float(session.poor_low)
    # 7: price_position_in_va (continuous)
    if va_width > 0:
        if current_price > vah:
            out[7] = np.clip((current_price - vah) / va_width, 0.0, 2.0)
        elif current_price < val:
            out[7] = np.clip((current_price - val) / va_width, -2.0, 0.0)
        else:
            out[7] = (current_price - val) / va_width - 0.5

    return out


def extract_session_tpo_features(
    session_tpos: SessionTPOSet | None,
    current_price: float,
) -> np.ndarray:
    """Extract 26 features from per-session TPO profiles.

    Feature layout:
      0-7:   Tokyo  (price_vs_poc, price_vs_vah, price_vs_val, shape,
                      ib_range, price_vs_ib_mid, poor_signal, price_position_in_va)
      8-15:  London (same 8)
      16-23: NY     (same 8)
      24:    poc_migration_tokyo_london (ticks / 200)
      25:    poc_migration_london_ny    (ticks / 200)

    Returns zeros(26) if session_tpos is None.
    """
    if session_tpos is None:
        return np.zeros(_N_FEATURES, dtype=np.float32)

    tokyo_feats = _extract_single_session(session_tpos.tokyo, current_price)
    london_feats = _extract_single_session(session_tpos.london, current_price)
    ny_feats = _extract_single_session(session_tpos.ny, current_price)

    migrations = np.array([
        np.clip(session_tpos.poc_migration_tokyo_london / 200.0, -1.0, 1.0),
        np.clip(session_tpos.poc_migration_london_ny / 200.0, -1.0, 1.0),
    ], dtype=np.float32)

    return np.concatenate([tokyo_feats, london_feats, ny_feats, migrations])


# Keep backward-compatible alias so any remaining callers don't break at import
def extract_tpo_features(
    tpo_profile: dict | None,
    current_price: float,
    bars_30m: list[dict] | None = None,
) -> np.ndarray:
    """Deprecated: returns zeros(26). Use extract_session_tpo_features instead."""
    return np.zeros(_N_FEATURES, dtype=np.float32)
