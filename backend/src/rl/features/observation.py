"""Observation vector assembler — dual-stream format.

Builds a packed float32 vector with layout:
  [tick_seq(50×4=200), candle_1m(10×3=30), candle_5m(6×3=18), context(119)]
  Total: 367 elements

The DQNetwork._split_and_forward() unpacks this into temporal sequences
and static context for the dual-stream architecture.

Context segment (119 features):
    level_type one-hot   25
    orderflow            15
    structure + session  23
    tpo                  13
    confluence            8
    macro                 7
    setup                13
    micro (hand-crafted) 15  (trimmed from 20 — raw ticks replace some)
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

from ..agent.network import (
    TICK_SEQ_LEN, TICK_FEATURES,
    CANDLE_1M_LEN, CANDLE_5M_LEN, CANDLE_FEATURES,
)


def _build_tick_sequence(recent_ticks: list[dict], touch_price: float) -> np.ndarray:
    """Build (TICK_SEQ_LEN, TICK_FEATURES) tensor from raw ticks.

    Per tick: [price_norm, size_norm, side(±1), dt_seconds_norm]
    price_norm = (price - touch_price) / touch_price * 1000 (basis points-ish)
    size_norm  = size / median_size (capped at 5)
    side       = +1 (buy/B) or -1 (sell/A)
    dt_norm    = seconds_since_first_tick / 60 (capped at 1)
    """
    out = np.zeros((TICK_SEQ_LEN, TICK_FEATURES), dtype=np.float32)
    if not recent_ticks:
        return out

    ticks = recent_ticks[-TICK_SEQ_LEN:]
    n = len(ticks)
    offset = TICK_SEQ_LEN - n  # right-align (pad zeros on left)

    sizes = [t["size"] for t in ticks]
    sorted_sizes = sorted(sizes)
    median_size = sorted_sizes[len(sorted_sizes) // 2] if sorted_sizes else 1
    median_size = max(median_size, 1)

    t0 = ticks[0]["ts"]

    for i, t in enumerate(ticks):
        row = offset + i
        out[row, 0] = np.clip((t["price"] - touch_price) / max(touch_price, 1) * 1000, -5.0, 5.0)
        out[row, 1] = np.clip(t["size"] / median_size, 0.0, 5.0)
        out[row, 2] = 1.0 if t.get("side") == "B" else -1.0
        dt = (t["ts"] - t0).total_seconds()
        out[row, 3] = np.clip(dt / 60.0, 0.0, 1.0)

    return out


def _build_candle_seq(candles: list, length: int, avg_vol: float) -> np.ndarray:
    """Build (length, CANDLE_FEATURES) tensor from CandleFlow objects.

    Per candle: [delta_norm, volume_norm, body_ratio]
    """
    out = np.zeros((length, CANDLE_FEATURES), dtype=np.float32)
    if not candles:
        return out

    window = candles[-length:]
    offset = length - len(window)

    for i, c in enumerate(window):
        row = offset + i
        out[row, 0] = float(np.clip(c.delta / max(avg_vol, 1.0), -1.0, 1.0))
        out[row, 1] = float(np.clip(c.volume / max(avg_vol, 1.0) / 5.0, 0.0, 1.0))
        out[row, 2] = float(c.body_ratio)

    return out


def build_observation(state: dict) -> np.ndarray:
    """Assemble the packed observation vector.

    Layout: [tick_seq(200), candle_1m(30), candle_5m(18), context(119)]
    Total: 367
    """
    level_type: LevelType = state.get("level_type", LevelType.VWAP)
    price: float = float(state.get("price", 0.0))
    candles: list = state.get("candles", [])         # 1m CandleFlows
    candles_5m: list = state.get("candles_5m", [])   # 5m CandleFlows
    vwap_bands = state.get("vwap_bands")
    volume_profile = state.get("volume_profile")
    tpo_profile = state.get("tpo_profile")
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

    # --- Temporal sequences (flattened for packing) ---
    tick_seq = _build_tick_sequence(recent_ticks, price)    # (50, 4)
    candle_1m = _build_candle_seq(candles, CANDLE_1M_LEN, avg_vol)   # (10, 3)
    candle_5m = _build_candle_seq(candles_5m, CANDLE_5M_LEN, avg_vol)  # (6, 3)

    # --- Static context features ---
    seg_level = np.array(encode_level_type(level_type), dtype=np.float32)  # 25

    seg_orderflow = extract_orderflow_features(candles, orderflow_signals)  # 15

    seg_structure = extract_structure_features(
        price, vwap_bands, volume_profile, session_levels, session_context
    )  # 23

    seg_tpo = extract_tpo_features(tpo_profile, price)  # 13

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
    ], dtype=np.float32)  # 8

    seg_macro = extract_macro_features(macro)  # 7

    seg_setup = extract_setup_features(state)  # 13

    # Approach direction as feature (from state)
    approach = state.get("approach_direction", "up")
    seg_approach = np.array([
        1.0 if approach == "up" else -1.0,
    ], dtype=np.float32)  # 1

    # Pack context
    context = np.concatenate([
        seg_level,        # 25
        seg_orderflow,    # 15
        seg_structure,    # 23
        seg_tpo,          # 13
        seg_confluence,   # 8
        seg_macro,        # 7
        seg_setup,        # 13
        seg_approach,     # 1
    ])  # total: 105

    # --- Pack everything flat: tick_seq + candle_1m + candle_5m + context ---
    obs = np.concatenate([
        tick_seq.flatten(),    # 200
        candle_1m.flatten(),   # 30
        candle_5m.flatten(),   # 18
        context,               # 105
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
# Context dim = everything after the temporal sequences
CONTEXT_DIM: int = OBSERVATION_DIM - (TICK_SEQ_LEN * TICK_FEATURES + CANDLE_1M_LEN * CANDLE_FEATURES + CANDLE_5M_LEN * CANDLE_FEATURES)
