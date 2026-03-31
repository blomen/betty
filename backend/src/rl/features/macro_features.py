"""Macro regime feature extraction."""
from __future__ import annotations

import numpy as np

_N_FEATURES = 11


def extract_macro_features(macro: dict | None) -> np.ndarray:
    """Extract 11 macro-regime features.

    Feature layout (indices 0-10):
      0  vix_norm            — vix / 50 (0→1 maps 0→50 VIX)
      1  vix_change_norm     — vix_change / 10
      2  regime_score        — pre-computed regime score (0-1)
      3  dxy_change          — DXY 1-day change / 1.0 (%), clipped ±3
      4  us10y_change        — US 10Y yield change (bps) / 10, clipped ±1
      5  us2y_change         — US 2Y yield change (bps) / 10, clipped ±1
      6  yield_curve_spread  — (10Y - 2Y) / 2.0, clipped ±1
      7  cot_net_norm        — large spec net position / 200k, clipped ±1
      8  cot_change_norm     — week-over-week net change / 50k, clipped ±1
      9  news_proximity      — 1.0 - (minutes_to_event / 120), clipped 0-1
     10  news_importance     — event importance / 3.0, clipped 0-1

    Returns zeros(11) if macro is None.
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

    cot_net = float(macro.get("cot_net_position", 0.0))
    cot_change = float(macro.get("cot_net_change", macro.get("cot_change_1w", 0.0) or 0.0))
    news_prox = float(macro.get("news_proximity", 0.0))
    news_imp = float(macro.get("news_importance", 0.0))

    feats = np.array([
        np.clip(vix / 50.0, 0.0, 1.0),
        np.clip(vix_change / 10.0, -1.0, 1.0),
        np.clip(regime_score, 0.0, 1.0),
        np.clip(dxy_change, -3.0, 3.0),
        np.clip(us10y_change / 10.0, -1.0, 1.0),
        np.clip(us2y_change / 10.0, -1.0, 1.0),
        np.clip(yield_curve / 2.0, -1.0, 1.0),
        np.clip(cot_net / 200_000.0, -1.0, 1.0),
        np.clip(cot_change / 50_000.0, -1.0, 1.0),
        np.clip(news_prox, 0.0, 1.0),
        np.clip(news_imp / 3.0, 0.0, 1.0),
    ], dtype=np.float32)

    return feats
