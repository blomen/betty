"""Macro regime feature extraction."""
from __future__ import annotations

import numpy as np

_N_FEATURES = 10


def extract_macro_features(macro: dict | None) -> np.ndarray:
    """Extract 10 macro-regime features.

    Feature layout (indices 0-9):
      0  vix_norm            — vix / 50 (0→1 maps 0→50 VIX)
      1  vix_change_norm     — vix_change / 10
      2  regime_score        — pre-computed regime score (0-1), e.g. from HMM
      3  dxy_change          — DXY 1-day change / 1.0 (%), clipped ±3
      4  gex_placeholder     — 0.0 (gamma exposure, not yet wired up)
      5  us10y_change        — US 10Y yield change / 0.1 (bp), clipped ±3
      6  us2y_change         — US 2Y yield change / 0.1 (bp), clipped ±3
      7  yield_curve_spread  — (10Y - 2Y) / 2.0, clipped ±1
      8  news_placeholder    — 0.0 (news sentiment, not yet wired up)
      9  news_severity       — 0.0 (news severity, not yet wired up)

    Returns zeros(10) if macro is None.
    """
    if macro is None:
        return np.zeros(_N_FEATURES, dtype=np.float32)

    vix = float(macro.get("vix", 20.0))
    vix_change = float(macro.get("vix_change", 0.0))
    regime_score = float(macro.get("regime_score", 0.5))
    dxy_change = float(macro.get("dxy_change", 0.0))
    us10y_change = float(macro.get("us10y_change", 0.0))
    us2y_change = float(macro.get("us2y_change", 0.0))

    yield_curve = (
        float(macro.get("us10y", 0.0)) - float(macro.get("us2y", 0.0))
        if "us10y" in macro and "us2y" in macro
        else float(macro.get("yield_curve_spread", 0.0))
    )

    feats = np.array([
        np.clip(vix / 50.0, 0.0, 1.0),
        np.clip(vix_change / 10.0, -1.0, 1.0),
        np.clip(regime_score, 0.0, 1.0),
        np.clip(dxy_change, -3.0, 3.0),
        0.0,  # GEX placeholder
        np.clip(us10y_change / 0.1, -3.0, 3.0),
        np.clip(us2y_change / 0.1, -3.0, 3.0),
        np.clip(yield_curve / 2.0, -1.0, 1.0),
        0.0,  # news placeholder
        0.0,  # news severity placeholder
    ], dtype=np.float32)

    return feats
