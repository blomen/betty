"""Observation vector assembler — flat static features.

All features are hand-crafted from domain knowledge (AMT, orderflow, Fabio's
patterns). No raw tick sequences — the orderflow and micro features already
encode the temporal dynamics.

Zone mode (state["zone"] present):
    zone composition multi-hot  len(LevelType)  (31 currently)
    orderflow                   21
    dow + session + PDH          64
    tpo (per-session)            38
    candle window                15
    zone features                 4
    zone confluence               5
    macro                        11
    exchange stats                5
    setup                        14
    AMT features                 20
    AMT dynamics                 20
    micro (hand-crafted)         20
    approach direction            1
    execution context             7
    ---
    total                       276

Legacy mode (state["level_type"] present, no zone):
    level_type one-hot   31
    orderflow            21
    dow + session + PDH  64
    tpo (per-session)    38
    candle window        15
    confluence            8
    macro                11
    exchange stats        5
    setup                14
    AMT features         20
    AMT dynamics         20
    micro (hand-crafted) 20
    approach direction    1
    execution context     7
    ---
    total               275
"""
from __future__ import annotations

import numpy as np

from ..config import LevelType, TICK_SIZE
from .level_features import (
    encode_level_type,
    encode_confluence,
    encode_zone_composition,
    encode_zone_features,
    encode_zone_confluence,
)
from .orderflow_features import extract_orderflow_features
from .tpo_features import extract_session_tpo_features
from .structure_features import extract_structure_features
from .macro_features import extract_macro_features
from .setup_features import extract_setup_features
from .micro_features import extract_micro_features
from .execution_features import extract_execution_features
from .amt_features import extract_amt_features
from .amt_dynamics_features import extract_amt_dynamics_features
from .exchange_stats_features import extract_exchange_stats_features
from ..zone_builder import Zone, ZoneMember

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
    """Assemble the full observation vector from a state dict.

    Supports two modes:
    - **Zone mode** (``state["zone"]`` present): multi-hot composition,
      zone features after candle window, 5-dim zone confluence.
    - **Legacy mode** (``state["level_type"]`` present, no zone): one-hot
      level type, 8-dim old-style confluence.
    """
    zone: Zone | None = state.get("zone")
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

    # --- Zone mode vs Legacy mode ---
    if zone is not None:
        # 1. Zone composition multi-hot (len(LevelType))
        seg_level = np.array(encode_zone_composition(zone), dtype=np.float32)
    else:
        # 1. Level type one-hot (len(LevelType))
        level_type: LevelType = state.get("level_type", LevelType.VWAP)
        seg_level = np.array(encode_level_type(level_type), dtype=np.float32)

    # 2. Orderflow (21) — includes 6 new temporal dynamics features
    seg_orderflow = extract_orderflow_features(candles, orderflow_signals)

    # 3. Dow Theory + session + PDH/PDL (64)
    swing_structure = state.get("swing_structure")
    seg_structure = extract_structure_features(
        price, vwap_bands, volume_profile, session_levels, session_context,
        swing_structure=swing_structure,
    )

    # 4. TPO per-session (38)
    session_tpos = state.get("session_tpos")
    seg_tpo = extract_session_tpo_features(session_tpos, price)

    # 5. Candle window (15)
    seg_candles = _build_candle_window(candles, avg_vol)

    # 6. Zone features (4) — only in zone mode
    if zone is not None:
        seg_zone_feats = np.array(
            encode_zone_features(zone, session_context=state.get("session_context")),
            dtype=np.float32,
        )
    else:
        seg_zone_feats = np.array([], dtype=np.float32)

    # 7. Confluence — zone mode (5) vs legacy mode (8)
    fvgs = state.get("fvgs", [])
    single_print_zones = state.get("single_print_zones", [])
    if zone is not None:
        all_zones: list[Zone] = state.get("all_zones", [])
        seg_confluence = np.array(
            encode_zone_confluence(zone, all_zones, fvgs, single_print_zones),
            dtype=np.float32,
        )
    else:
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

    # 8. Macro (11) — VIX, DXY, yields, COT, news proximity
    seg_macro = extract_macro_features(macro)

    # 8.5. Exchange stats (5) — OI, settlement, cleared/block volume
    seg_exchange = extract_exchange_stats_features(macro, price=price)

    # 9. Setup detection (14)
    seg_setup = extract_setup_features(state)

    # 10. AMT features (20) — Dalton day type, opening type, VA migration
    seg_amt = extract_amt_features(session_levels, volume_profile, session_context, price)

    # 10b. AMT dynamics features (20) — real-time IB extensions, acceptance/rejection
    amt_dynamics = state.get("amt_dynamics")
    seg_amt_dynamics = extract_amt_dynamics_features(amt_dynamics)

    # 11. Micro features (20) — tick-level hand-crafted context
    seg_micro = extract_micro_features(recent_ticks, price)

    # 12. Approach direction (1)
    approach = state.get("approach_direction", "up")
    seg_approach = np.array([
        1.0 if approach == "up" else -1.0,
    ], dtype=np.float32)

    # 13. Execution context (7) — Fabio's timing/auction rules
    seg_execution = extract_execution_features(state, recent_ticks, candles, price)

    obs = np.concatenate([
        seg_level,        # len(LevelType) — multi-hot (zone) or one-hot (legacy)
        seg_orderflow,    # 21
        seg_structure,    # 64
        seg_tpo,          # 38
        seg_candles,      # 15
        seg_zone_feats,   # 4 (zone) or 0 (legacy)
        seg_confluence,   # 5 (zone) or 8 (legacy)
        seg_macro,        # 11
        seg_exchange,     # 5
        seg_setup,        # 14
        seg_amt,          # 20
        seg_amt_dynamics, # 20
        seg_micro,        # 20
        seg_approach,     # 1
        seg_execution,    # 7
    ])

    # Sanitise
    obs = np.where(np.isfinite(obs), obs, 0.0)
    return obs.astype(np.float32)


# Compute dimension at import time using zone mode
_dummy_member = ZoneMember(name="vwap", level_type=LevelType.VWAP, price=19000.0)
_dummy_zone = Zone(
    center_price=19000.0,
    upper_bound=19001.0,
    lower_bound=18999.0,
    members=[_dummy_member],
    composition=[1.0 if lt == LevelType.VWAP else 0.0 for lt in LevelType],
    width_ticks=8.0,
    member_count=1,
    hierarchy_score=0.5,
)
_dummy_state: dict = {
    "zone": _dummy_zone,
    "all_zones": [_dummy_zone],
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
    "amt_dynamics": None,
}

OBSERVATION_DIM: int = int(build_observation(_dummy_state).shape[0])
# No temporal/context split needed — everything is static context
CONTEXT_DIM: int | None = None

# --- Hybrid GBT+DQN augmentation ---
GBT_FORECAST_DIM: int = 8   # prob_cont, prob_rev, confidence, best_r, worst_r, breakeven, levels, stop
POSITION_STATE_DIM: int = 8  # pos_flat/long/short, unrealized_pnl, time_in_trade, session_pnl, consec_losses, progress
AUGMENTED_OBSERVATION_DIM: int = OBSERVATION_DIM + GBT_FORECAST_DIM + POSITION_STATE_DIM


def augment_observation(
    obs: np.ndarray,
    gbt_forecast: np.ndarray,
    position_state: np.ndarray,
) -> np.ndarray:
    """Augment base observation with GBT forecast (8) + position state (8).

    Args:
        obs: base observation from build_observation()
        gbt_forecast: GBT multi-target predictions (8-dim)
        position_state: position/session state (8-dim)

    Returns:
        Augmented observation (base + 16)
    """
    return np.concatenate([obs, gbt_forecast, position_state]).astype(np.float32)


def build_position_state(
    pos_side: str = "flat",
    unrealized_pnl_ticks: float = 0.0,
    entry_timestamp: float = 0.0,
    current_timestamp: float = 0.0,
    session_pnl_r: float = 0.0,
    consecutive_losses: int = 0,
    trade_count: int = 0,
    stop_ticks: float = 10.0,
) -> np.ndarray:
    """Build 8-dim position/session state vector.

    Returns ndarray of shape (8,) with normalized values.
    """
    # Position side one-hot
    pos_flat = 1.0 if pos_side == "flat" else 0.0
    pos_long = 1.0 if pos_side == "long" else 0.0
    pos_short = 1.0 if pos_side == "short" else 0.0

    # Unrealized P&L in R-multiples, clipped
    unrealized_r = np.clip(unrealized_pnl_ticks / max(stop_ticks, 1.0), -3.0, 3.0)

    # Time in trade (minutes, normalized to [0,1] over 60 min)
    if entry_timestamp > 0 and current_timestamp > entry_timestamp:
        time_in_trade = min((current_timestamp - entry_timestamp) / 3600.0, 1.0)
    else:
        time_in_trade = 0.0

    # Session P&L normalized
    session_pnl_norm = np.clip(session_pnl_r / 10.0, -1.0, 1.0)

    # Consecutive losses normalized
    consec_norm = min(consecutive_losses / 3.0, 1.0)

    # Session progress (trade count / 20)
    progress = min(trade_count / 20.0, 1.0)

    return np.array([
        pos_flat, pos_long, pos_short,
        unrealized_r, time_in_trade,
        session_pnl_norm, consec_norm, progress,
    ], dtype=np.float32)
