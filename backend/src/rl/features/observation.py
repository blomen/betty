"""Observation vector assembler.

Builds the full ~107-dim float32 feature vector from a market state dict.

State dict keys:
    level_type      LevelType enum — the level being touched
    price           float — current price
    candles         list[CandleFlow] — recent 1-minute candle flows
    vwap_bands      VWAPBands | None
    volume_profile  VolumeProfile | None
    tpo_profile     dict | None — TPO profile (see tpo_features.py for schema)
    session_levels  SessionLevels | None
    all_levels      list[float] — all active level prices
    orderflow_signals  OrderflowSignals | None
    macro           dict | None — macro context (see macro_features.py)
    session_context dict | None — session context (see structure_features.py)
"""
from __future__ import annotations

import numpy as np

from ..config import LevelType, TICK_SIZE
from .level_features import encode_level_type, encode_confluence
from .orderflow_features import extract_orderflow_features
from .tpo_features import extract_tpo_features
from .structure_features import extract_structure_features
from .macro_features import extract_macro_features
from .setup_features import extract_setup_features
from .micro_features import extract_micro_features

# Candle window: last 5 candles × 3 features each
_CANDLE_WINDOW = 5
_CANDLE_FEATS_PER = 3  # delta_norm, volume_norm, body_ratio
_CANDLE_DIM = _CANDLE_WINDOW * _CANDLE_FEATS_PER  # 15


def _build_candle_window(candles: list, avg_vol: float) -> np.ndarray:
    """Last 5 candles → 15 features (delta_norm, volume_norm, body_ratio)."""
    out = np.zeros(_CANDLE_DIM, dtype=np.float32)
    if not candles:
        return out
    window = candles[-_CANDLE_WINDOW:] if len(candles) >= _CANDLE_WINDOW else candles
    # Pad on the left if fewer than 5 candles
    for i, c in enumerate(window):
        offset = i * _CANDLE_FEATS_PER
        out[offset + 0] = float(np.clip(c.delta / max(avg_vol, 1.0), -1.0, 1.0))
        out[offset + 1] = float(np.clip(c.volume / max(avg_vol, 1.0) / 5.0, 0.0, 1.0))
        out[offset + 2] = float(c.body_ratio)
    return out


def build_observation(state: dict) -> np.ndarray:
    """Assemble the full observation vector from a state dict.

    Segment sizes:
        level_type one-hot   25
        orderflow            15
        structure + session  23
        tpo                  13
        candle window        15
        confluence            8
        macro                 7
        setup                13
        micro (tick-level)   20
        ---
        total               139
    """
    level_type: LevelType = state.get("level_type", LevelType.VWAP)
    price: float = float(state.get("price", 0.0))
    candles: list = state.get("candles", [])
    vwap_bands = state.get("vwap_bands")
    volume_profile = state.get("volume_profile")
    tpo_profile = state.get("tpo_profile")
    session_levels = state.get("session_levels")
    all_levels: list[float] = state.get("all_levels", [])
    orderflow_signals = state.get("orderflow_signals")
    macro = state.get("macro")
    session_context = state.get("session_context")

    # Compute avg vol for normalisation (used by candle window and orderflow)
    if candles:
        avg_vol = sum(c.volume for c in candles[-20:]) / max(len(candles[-20:]), 1)
        avg_vol = max(avg_vol, 1.0)
    else:
        avg_vol = 1.0

    # 1. Level type one-hot (25 — FVG/SINGLE_PRINT removed, now confluence signals)
    seg_level = np.array(encode_level_type(level_type), dtype=np.float32)

    # 2. Orderflow (15)
    seg_orderflow = extract_orderflow_features(candles, orderflow_signals)

    # 3. Structure + session (23)
    seg_structure = extract_structure_features(
        price, vwap_bands, volume_profile, session_levels, session_context
    )

    # 4. TPO (13)
    seg_tpo = extract_tpo_features(tpo_profile, price)

    # 5. Candle window (15)
    seg_candles = _build_candle_window(candles, avg_vol)

    # 6. Confluence (8) — includes FVG/single_print overlap signals
    fvgs = state.get("fvgs", [])
    single_print_zones = state.get("single_print_zones", [])
    conf = encode_confluence(
        price, all_levels, tick_size=TICK_SIZE,
        fvgs=fvgs, single_print_zones=single_print_zones,
    )
    seg_confluence = np.array([
        conf["levels_within_5_ticks"] / 10.0,      # normalise: cap at 10
        conf["strongest_cluster_score"],
        conf["nearest_higher_level_dist"] / 50.0,  # already capped at 50
        conf["nearest_lower_level_dist"] / 50.0,
        conf["touched_level_hierarchy_rank"],
        conf["fvg_overlap"],                        # 1.0 if FVG overlaps level
        conf["fvg_width_ticks"],                    # width of overlapping FVG (0-1)
        conf["single_print_overlap"],               # 1.0 if single print overlaps
    ], dtype=np.float32)

    # 7. Macro (7)
    seg_macro = extract_macro_features(macro)

    # 8. Setup detection (9)
    seg_setup = extract_setup_features(state)

    # 9. Micro features — tick-level context at the touch point (20)
    recent_ticks = state.get("recent_ticks", [])
    seg_micro = extract_micro_features(recent_ticks, price)

    obs = np.concatenate([
        seg_level,        # 25
        seg_orderflow,    # 15
        seg_structure,    # 23
        seg_tpo,          # 13
        seg_candles,      # 15
        seg_confluence,   # 8
        seg_macro,        # 7
        seg_setup,        # 9
        seg_micro,        # 20
    ])

    # Sanitise: replace NaN / Inf with 0
    obs = np.where(np.isfinite(obs), obs, 0.0)

    return obs.astype(np.float32)


# Compute at import time by building a dummy observation
_dummy_state: dict = {
    "level_type": LevelType.VWAP,
    "price": 19000.0,
    "candles": [],
    "vwap_bands": None,
    "volume_profile": None,
    "tpo_profile": None,
    "tpo_profile_obj": None,
    "session_levels": None,
    "all_levels": [],
    "orderflow_signals": None,
    "macro": None,
    "session_context": None,
    "day_type": None,
}

OBSERVATION_DIM: int = int(build_observation(_dummy_state).shape[0])
