"""Trigger feature assembler — 122-dim fast observation for the Trigger GBT.

Combines fast-moving features consumed by the Trigger GBT.  "Fast" here means
features that can change on every candle/tick.

**Phase 3b architecture** — the trigger layer is responsible for setup
identification (direction + stop + levels) from orderflow + level alignment.
Narrative signals and narrative-derived setup probabilities are NOT fed to
this layer. Narrative (bias + risk-on/off) influences the *risk heads*
(size, add, early-exit) in the decision layer, not direction identification.

Feature layout (122 dims):
  0:10    structural passthrough  10  (from passthrough_features.py)
  10:30   micro features          20  (from micro_features.py)
  30:55   orderflow               25  (from orderflow_features.py)
  55:70   candles                 15  (5 candles x 3 features)
  70:74   zone features            4  (from level_features.py)
  74:79   zone confluence          5  (from level_features.py)
  79:110  zone composition        31  (from level_features.py)
 110:111  approach direction       1
 111:119  trigger GBT forecast     8  (optional, zeros if not available)
 119:122  execution passthrough    3  (trades_today, time_to_close, session_pnl)

OF stack bumped 21→25 on 2026-05-18 (PROFILE follow-up) — added 4 new
pattern dims (two_way_battle, failed_auction_reabsorption,
close_position_in_range, initiative_follow_through) to capture
methodology primitives missing from the previous OF stack.
"""

from __future__ import annotations

import numpy as np

from .level_features import (
    encode_zone_composition,
    encode_zone_confluence,
    encode_zone_features,
)
from .micro_features import extract_micro_features
from .orderflow_features import extract_orderflow_features
from .passthrough_features import PASSTHROUGH_DIM, extract_passthrough

# ---------------------------------------------------------------------------
# Segment dimensions
# ---------------------------------------------------------------------------
TRIGGER_GBT_DIM: int = 8
EXEC_PASSTHROUGH_DIM: int = 3

_MICRO_DIM: int = 20
_ORDERFLOW_DIM: int = 25  # bumped 21→25 on 2026-05-18: 4 new Tier C pattern dims
_CANDLE_DIM: int = 15  # 5 candles x 3 features
_ZONE_FEATS_DIM: int = 4
_ZONE_CONF_DIM: int = 5
_ZONE_COMP_DIM: int = 31
_APPROACH_DIM: int = 1

TRIGGER_DIM: int = (
    PASSTHROUGH_DIM  # 10
    + _MICRO_DIM  # 20
    + _ORDERFLOW_DIM  # 25 (was 21)
    + _CANDLE_DIM  # 15
    + _ZONE_FEATS_DIM  #  4
    + _ZONE_CONF_DIM  #  5
    + _ZONE_COMP_DIM  # 31
    + _APPROACH_DIM  #  1
    + TRIGGER_GBT_DIM  #  8
    + EXEC_PASSTHROUGH_DIM  #  3
)  # 122

assert TRIGGER_DIM == 122, f"TRIGGER_DIM mismatch: {TRIGGER_DIM}"

# Ordered segment map — (name: dim) preserving layout order
TRIGGER_SEGMENTS: dict[str, int] = {
    "structural_passthrough": PASSTHROUGH_DIM,
    "micro": _MICRO_DIM,
    "orderflow": _ORDERFLOW_DIM,
    "candles": _CANDLE_DIM,
    "zone_features": _ZONE_FEATS_DIM,
    "zone_confluence": _ZONE_CONF_DIM,
    "zone_composition": _ZONE_COMP_DIM,
    "approach_direction": _APPROACH_DIM,
    "trigger_gbt_forecast": TRIGGER_GBT_DIM,
    "exec_passthrough": EXEC_PASSTHROUGH_DIM,
}

assert sum(TRIGGER_SEGMENTS.values()) == TRIGGER_DIM, "TRIGGER_SEGMENTS sum mismatch"

# ---------------------------------------------------------------------------
# Candle window builder (local copy — avoids circular import from observation.py)
# ---------------------------------------------------------------------------
_CANDLE_WINDOW = 5
_CANDLE_FEATS_PER = 3  # delta_norm, volume_norm, body_ratio


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


# ---------------------------------------------------------------------------
# Public assembler
# ---------------------------------------------------------------------------


def build_trigger_observation(
    state: dict,
    base_observation: np.ndarray,
    trigger_gbt_forecast: np.ndarray | None = None,
) -> np.ndarray:
    """Assemble the 122-dim trigger observation.

    Args:
        state: Full RL state dict (same as passed to ``build_observation()``).
               Expected keys: ``candles``, ``recent_ticks``, ``price``,
               ``orderflow_signals``, ``zone``, ``all_zones``, ``fvgs``,
               ``single_print_zones``, ``approach_direction``,
               ``session_context``, ``trades_today``, ``time_to_close``,
               ``session_pnl``.
        base_observation: float32 array from ``build_observation()``
                          — used by ``extract_passthrough()``.
        trigger_gbt_forecast: Optional ``(8,)`` float32 array of trigger GBT
                              multi-target predictions.  Zeros if not provided.

    Returns:
        np.ndarray of shape ``(122,)`` with dtype ``float32``.
    """
    price: float = float(state.get("price", 0.0))
    candles: list = state.get("candles", [])
    recent_ticks: list = state.get("recent_ticks", [])
    orderflow_signals = state.get("orderflow_signals")
    zone = state.get("zone")
    all_zones: list = state.get("all_zones", [])
    fvgs: list = state.get("fvgs", [])
    single_print_zones: list = state.get("single_print_zones", [])
    approach = state.get("approach_direction", "up")
    session_context = state.get("session_context")

    # Execution passthrough: trades_today, time_to_close, session_pnl
    trades_today: float = float(state.get("trades_today", 0))
    time_to_close: float = float(state.get("time_to_close", 0.0))
    session_pnl: float = float(state.get("session_pnl", 0.0))

    # Avg volume for candle normalisation
    if candles:
        avg_vol = sum(c.volume for c in candles[-20:]) / max(len(candles[-20:]), 1)
        avg_vol = max(avg_vol, 1.0)
    else:
        avg_vol = 1.0

    # 1. Structural passthrough (10)
    seg_passthrough = extract_passthrough(base_observation)

    # 4. Micro features (20)
    seg_micro = extract_micro_features(recent_ticks, price)

    # 5. Orderflow (21)
    seg_orderflow = extract_orderflow_features(candles, orderflow_signals)

    # 6. Candle window (15)
    seg_candles = _build_candle_window(candles, avg_vol)

    # 7. Zone features (4)
    if zone is not None:
        seg_zone_feats = np.array(
            encode_zone_features(zone, session_context=session_context),
            dtype=np.float32,
        )
    else:
        seg_zone_feats = np.zeros(_ZONE_FEATS_DIM, dtype=np.float32)

    # 8. Zone confluence (5)
    if zone is not None:
        seg_zone_conf = np.array(
            encode_zone_confluence(zone, all_zones, fvgs, single_print_zones),
            dtype=np.float32,
        )
    else:
        seg_zone_conf = np.zeros(_ZONE_CONF_DIM, dtype=np.float32)

    # 9. Zone composition (31)
    if zone is not None:
        seg_zone_comp = np.array(encode_zone_composition(zone), dtype=np.float32)
    else:
        seg_zone_comp = np.zeros(_ZONE_COMP_DIM, dtype=np.float32)

    # 10. Approach direction (1)
    seg_approach = np.array(
        [1.0 if approach == "up" else -1.0],
        dtype=np.float32,
    )

    # 11. Trigger GBT forecast (8) — zeros if not provided
    if trigger_gbt_forecast is not None:
        if trigger_gbt_forecast.shape != (TRIGGER_GBT_DIM,):
            raise ValueError(
                f"trigger_gbt_forecast must be shape ({TRIGGER_GBT_DIM},), got {trigger_gbt_forecast.shape}"
            )
        seg_gbt = trigger_gbt_forecast.astype(np.float32, copy=False)
    else:
        seg_gbt = np.zeros(TRIGGER_GBT_DIM, dtype=np.float32)

    # 12. Execution passthrough (3)
    seg_exec = np.array(
        [
            float(np.clip(trades_today / 10.0, 0.0, 1.0)),  # trades_today norm [0,10]→[0,1]
            float(np.clip(time_to_close / 390.0, 0.0, 1.0)),  # time_to_close (minutes) norm
            float(np.clip(session_pnl / 10.0, -1.0, 1.0)),  # session_pnl in R, clipped
        ],
        dtype=np.float32,
    )

    obs = np.concatenate(
        [
            seg_passthrough,  # 10
            seg_micro,  # 20
            seg_orderflow,  # 21
            seg_candles,  # 15
            seg_zone_feats,  #  4
            seg_zone_conf,  #  5
            seg_zone_comp,  # 31
            seg_approach,  #  1
            seg_gbt,  #  8
            seg_exec,  #  3
        ]
    )

    # Sanitise
    obs = np.where(np.isfinite(obs), obs, np.float32(0.0))
    return obs.astype(np.float32)
