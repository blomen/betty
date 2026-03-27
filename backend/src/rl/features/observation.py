"""Observation vector assembler — flat static features.

All features are hand-crafted from domain knowledge (AMT, orderflow, Fabio's
patterns). No raw tick sequences — the orderflow and micro features already
encode the temporal dynamics.

Segment sizes:
    level_type one-hot   25
    orderflow            21  (was 15, added 6 temporal dynamics)
    structure + session  23
    tpo (per-session)    26
    candle window        15
    confluence            8
    macro                 7
    setup                14  (13 + squeeze detector)
    micro (hand-crafted) 20
    approach direction    1
    execution context     7  (follow-through, responsive/initiative, ATR, vol anomaly, time)
    ---
    total               167
"""
from __future__ import annotations

import numpy as np

from ..config import LevelType, TICK_SIZE
from .level_features import encode_level_type, encode_confluence
from .orderflow_features import extract_orderflow_features
from .tpo_features import extract_session_tpo_features
from .structure_features import extract_structure_features
from .macro_features import extract_macro_features
from .setup_features import extract_setup_features
from .micro_features import extract_micro_features
from .execution_features import extract_execution_features

# Candle window: last 5 candles x 3 features each
_CANDLE_WINDOW = 5
_CANDLE_FEATS_PER = 3  # delta_norm, volume_norm, body_ratio
_CANDLE_DIM = _CANDLE_WINDOW * _CANDLE_FEATS_PER  # 15


def _build_candle_window(candles: list, avg_vol: float) -> np.ndarray:
    """Last 5 candles -> 15 features (delta_norm, volume_norm, body_ratio)."""
    out = np.zeros(_CANDLE_DIM, dtype=np.float32)
    if not candles:
        return out
    window = candles[-_CANDLE_WINDOW:] if len(candles) >= _CANDLE_WINDOW else candles
    for i, c in enumerate(window):
        offset = i * _CANDLE_FEATS_PER
        out[offset + 0] = float(np.clip(c.delta / max(avg_vol, 1.0), -1.0, 1.0))
        out[offset + 1] = float(np.clip(c.volume / max(avg_vol, 1.0) / 5.0, 0.0, 1.0))
        out[offset + 2] = float(c.body_ratio)
    return out


def build_observation(state: dict) -> np.ndarray:
    """Assemble the full observation vector from a state dict."""
    level_type: LevelType = state.get("level_type", LevelType.VWAP)
    price: float = float(state.get("price", 0.0))
    candles: list = state.get("candles", [])
    vwap_bands = state.get("vwap_bands")
    volume_profile = state.get("volume_profile")
    session_levels = state.get("session_levels")
    all_levels: list[float] = state.get("all_levels", [])
    orderflow_signals = state.get("orderflow_signals")
    macro = state.get("macro")
    session_context = state.get("session_context")
    recent_ticks: list[dict] = state.get("recent_ticks", [])

    # Avg vol for normalisation
    if candles:
        avg_vol = sum(c.volume for c in candles[-20:]) / max(len(candles[-20:]), 1)
        avg_vol = max(avg_vol, 1.0)
    else:
        avg_vol = 1.0

    # 1. Level type one-hot (25)
    seg_level = np.array(encode_level_type(level_type), dtype=np.float32)

    # 2. Orderflow (21) — includes 6 new temporal dynamics features
    seg_orderflow = extract_orderflow_features(candles, orderflow_signals)

    # 3. Structure + session (23)
    seg_structure = extract_structure_features(
        price, vwap_bands, volume_profile, session_levels, session_context
    )

    # 4. TPO per-session (26)
    session_tpos = state.get("session_tpos")
    seg_tpo = extract_session_tpo_features(session_tpos, price)

    # 5. Candle window (15)
    seg_candles = _build_candle_window(candles, avg_vol)

    # 6. Confluence (8)
    fvgs = state.get("fvgs", [])
    single_print_zones = state.get("single_print_zones", [])
    conf = encode_confluence(
        price, all_levels, tick_size=TICK_SIZE,
        fvgs=fvgs, single_print_zones=single_print_zones,
    )
    seg_confluence = np.array([
        conf["levels_within_5_ticks"] / 10.0,
        conf["strongest_cluster_score"],
        conf["nearest_higher_level_dist"] / 50.0,
        conf["nearest_lower_level_dist"] / 50.0,
        conf["touched_level_hierarchy_rank"],
        conf["fvg_overlap"],
        conf["fvg_width_ticks"],
        conf["single_print_overlap"],
    ], dtype=np.float32)

    # 7. Macro (7)
    seg_macro = extract_macro_features(macro)

    # 8. Setup detection (13)
    seg_setup = extract_setup_features(state)

    # 9. Micro features (20) — tick-level hand-crafted context
    seg_micro = extract_micro_features(recent_ticks, price)

    # 10. Approach direction (1)
    approach = state.get("approach_direction", "up")
    seg_approach = np.array([
        1.0 if approach == "up" else -1.0,
    ], dtype=np.float32)

    # 11. Execution context (7) — Fabio's timing/auction rules
    seg_execution = extract_execution_features(state, recent_ticks, candles, price)

    obs = np.concatenate([
        seg_level,        # 25
        seg_orderflow,    # 21
        seg_structure,    # 23
        seg_tpo,          # 26
        seg_candles,      # 15
        seg_confluence,   # 8
        seg_macro,        # 7
        seg_setup,        # 14 (was 13, added squeeze)
        seg_micro,        # 20
        seg_approach,     # 1
        seg_execution,    # 7
    ])

    # Sanitise
    obs = np.where(np.isfinite(obs), obs, 0.0)
    return obs.astype(np.float32)


# Compute dimension at import time
_dummy_state: dict = {
    "level_type": LevelType.VWAP,
    "price": 19000.0,
    "candles": [],
    "candles_5m": [],
    "vwap_bands": None,
    "volume_profile": None,
    "session_tpos": None,
    "tpo_profile": None,
    "tpo_profile_obj": None,
    "session_levels": None,
    "all_levels": [],
    "orderflow_signals": None,
    "macro": None,
    "session_context": None,
    "day_type": None,
    "recent_ticks": [],
}

OBSERVATION_DIM: int = int(build_observation(_dummy_state).shape[0])
# No temporal/context split needed — everything is static context
CONTEXT_DIM: int | None = None
