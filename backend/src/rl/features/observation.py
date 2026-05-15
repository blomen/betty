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
    zone touch memory             3
    ---
    total                       279

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

from ..config import TICK_SIZE, LevelType
from ..zone_builder import Zone, ZoneMember
from .amt_dynamics_features import extract_amt_dynamics_features
from .amt_features import extract_amt_features
from .exchange_stats_features import extract_exchange_stats_features
from .execution_features import extract_execution_features
from .level_features import (
    encode_confluence,
    encode_level_type,
    encode_zone_composition,
    encode_zone_confluence,
    encode_zone_features,
)
from .macro_features import extract_macro_features
from .micro_features import extract_micro_features
from .orderflow_features import extract_orderflow_features
from .setup_features import extract_setup_features
from .structure_features import extract_structure_features
from .tpo_features import extract_session_tpo_features

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
        price,
        vwap_bands,
        volume_profile,
        session_levels,
        session_context,
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
            price,
            all_levels,
            tick_size=TICK_SIZE,
            fvgs=fvgs,
            single_print_zones=single_print_zones,
        )
        seg_confluence = np.array(
            [
                conf["levels_within_5_ticks"] / 10.0,
                conf["strongest_cluster_score"],
                conf["nearest_higher_level_dist"] / 50.0,
                conf["nearest_lower_level_dist"] / 50.0,
                conf["touched_level_hierarchy_rank"],
                conf["fvg_overlap"],
                conf["fvg_width_ticks"],
                conf["single_print_overlap"],
            ],
            dtype=np.float32,
        )

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
    seg_approach = np.array(
        [
            1.0 if approach == "up" else -1.0,
        ],
        dtype=np.float32,
    )

    # 13. Execution context (7) — Fabio's timing/auction rules
    seg_execution = extract_execution_features(state, recent_ticks, candles, price)

    # 14. Session-anchored CVD (2) — framework: CVD divergence at day scale,
    # not a 20-candle rolling window. Normalized by session volume.
    session_cvd = float(state.get("session_cvd", 0.0))
    session_cvd_vol = float(state.get("session_cvd_total_vol", 0.0))
    session_cvd_ratio = float(np.clip(session_cvd / session_cvd_vol, -1.0, 1.0)) if session_cvd_vol > 0 else 0.0
    session_cvd_sign = 1.0 if session_cvd > 0 else (-1.0 if session_cvd < 0 else 0.0)
    seg_session_cvd = np.array([session_cvd_ratio, session_cvd_sign], dtype=np.float32)

    # 15. HVN / LVN distance (2) — framework calls these "magnets" and "slips"
    vp = volume_profile
    hvn_dist = 0.0
    lvn_dist = 0.0
    if vp is not None:
        hvns = getattr(vp, "hvn_levels", []) or []
        lvns = getattr(vp, "lvn_levels", []) or []
        if hvns:
            nearest_hvn = min(hvns, key=lambda p: abs(p - price))
            hvn_dist = float(np.clip((nearest_hvn - price) / (price * 0.002 + 1e-6), -1.0, 1.0))
        if lvns:
            nearest_lvn = min(lvns, key=lambda p: abs(p - price))
            lvn_dist = float(np.clip((nearest_lvn - price) / (price * 0.002 + 1e-6), -1.0, 1.0))
    seg_hvn_lvn = np.array([hvn_dist, lvn_dist], dtype=np.float32)

    # 15b. Absolute big-trade features (2) — Fabio's literal rule: institutional
    # activity filter at ≥25 contracts per trade (not relative threshold).
    # - count_25: fraction of last 50 ticks with size ≥25 (0-1)
    # - net_delta_25: signed net size of ≥25-contract trades / 100, clipped ±1
    big_abs_count = 0.0
    big_abs_net = 0.0
    if recent_ticks:
        window = recent_ticks[-50:] if len(recent_ticks) >= 50 else recent_ticks
        tot = len(window)
        if tot > 0:
            big_count = 0
            big_net = 0.0
            for t in window:
                sz = t.get("size", 0) if isinstance(t, dict) else getattr(t, "size", 0)
                if sz >= 25:
                    big_count += 1
                    side = t.get("side", "A") if isinstance(t, dict) else getattr(t, "side", "A")
                    # topstepx_stream convention (topstepx_stream.py:290-291):
                    #   type=0 (bid hit, SELL aggressor) -> side = "B"
                    #   type=1 (ask lift, BUY aggressor) -> side = "A"
                    # So "A" = buy aggressor (+), "B" = sell aggressor (-).
                    # Previous code inverted this and big_abs_net carried the
                    # WRONG SIGN for every autonomous-mode tick — model saw
                    # "buy flow" when it was sell flow and vice versa.
                    # Dead-dims audit fix 2026-05-15.
                    big_net += sz if side == "A" else -sz
            big_abs_count = float(big_count / tot)
            big_abs_net = float(np.clip(big_net / 100.0, -1.0, 1.0))
    seg_big_abs = np.array([big_abs_count, big_abs_net], dtype=np.float32)

    # 16. Orderflow-zone alignment (3) — framework cornerstone: zone quality
    # AND orderflow confluence both required for real trade signal.
    # - of_score_rev: orderflow score for REVERSAL trade at this touch [0,1]
    # - zone_strength: normalized zone hierarchy (members / 8)
    # - zone_of_alignment: of_score × zone_strength (AND gate)
    zone_strength = 0.0
    if zone is not None:
        zone_strength = min(getattr(zone, "member_count", 0) / 8.0, 1.0)

    # Compute orderflow score for the REV direction at this touch (model
    # always picks REV per FORCE_REV_ONLY live + training analysis).
    # approach=up → REV is SHORT (dir=-1); approach=down → REV is LONG (dir=+1).
    approach_str = state.get("approach_direction", "up")
    rev_dir = -1 if approach_str == "up" else 1

    # Feature components reuse signals/candles already extracted earlier.
    of_score = 0.0
    if candles:
        last_c = candles[-1]
        vol_c = max(getattr(last_c, "volume", 1), 1)
        delta_pct_c = getattr(last_c, "delta", 0) / vol_c
        dscore = max(-1.0, min(1.0, delta_pct_c * rev_dir / 0.15))
        if dscore > 0:
            of_score += 0.20 * dscore
    if orderflow_signals is not None:
        _cvd_trend = getattr(orderflow_signals, "cvd_trend", "flat")
        if (rev_dir == 1 and _cvd_trend == "rising") or (rev_dir == -1 and _cvd_trend == "falling"):
            of_score += 0.20
        elif _cvd_trend == "flat":
            of_score += 0.05
        _sic = getattr(orderflow_signals, "stacked_imbalance_count", 0) or 0
        _sdir = getattr(orderflow_signals, "stacked_direction", None)
        _wants_buy = rev_dir == 1
        _matches = (_wants_buy and _sdir == "buy") or (not _wants_buy and _sdir == "sell")
        if _matches:
            of_score += 0.25 * min(_sic / 3.0, 1.0)
        _vsa = float(getattr(orderflow_signals, "vsa_absorption", 0) or 0)
        _abs = float(getattr(orderflow_signals, "absorption_strength", 0) or 0)
        of_score += 0.20 * min(max(_vsa, _abs), 1.0)
        _big = float(getattr(orderflow_signals, "big_trades_net_delta", 0) or 0)
        if rev_dir == 1 and _big > 0:
            of_score += 0.15 * min(_big / 100.0, 1.0)
        elif rev_dir == -1 and _big < 0:
            of_score += 0.15 * min(-_big / 100.0, 1.0)
    of_score = float(max(0.0, min(1.0, of_score)))
    seg_of_alignment = np.array(
        [of_score, zone_strength, of_score * zone_strength],
        dtype=np.float32,
    )

    # 16b. Post-touch reaction features (Phase 3a — 8 dims)
    # Measures HOW THE MARKET REACTED to the level touch, not how it arrived.
    # In training: reaction_ticks = peek at norm_ticks[i:i+50] from replay_engine.
    # In live: session_manager waits N ticks after touch then rebuilds state.
    from .reaction_features import extract_reaction_features

    _touch_px = float(state.get("touch_price", price))
    seg_reaction = extract_reaction_features(
        state.get("reaction_ticks"),
        recent_ticks,
        _touch_px,
        approach_str,
        stop_ticks=20.0,
    )

    # 16c. Pattern detectors (Phase 3a — 5 dims)
    # Explicit Fabio-style patterns: pin_bar, absorption_wall, imbalance_cluster,
    # delta_divergence, trapped_breakout. Each 0-1 float.
    from .pattern_features import extract_pattern_features

    seg_pattern = extract_pattern_features(
        state.get("touch_bar_partial"),
        state.get("reaction_ticks"),
        recent_ticks,
        orderflow_signals,
        _touch_px,
        approach_str,
    )

    # 16d. Unified zone quality score (Phase 3a — 1 dim)
    # Combines hierarchy + member count + freshness into a single [0,1] signal.
    # Framework: zone "quality" is the PRECONDITION to trade. Model reads this
    # directly instead of inferring from 4 separate zone features.
    zq = 0.0
    if zone is not None:
        _mc = min(getattr(zone, "member_count", 0) / 8.0, 1.0)
        _hs = float(getattr(zone, "hierarchy_score", 0.0))
        # Time freshness: if touch_count is high, zone is being tested a lot
        # (could be good OR bad). We just weight by confluence primarily.
        zq = 0.5 * _mc + 0.5 * _hs
    seg_zone_quality = np.array([zq], dtype=np.float32)

    # 17. Zone touch memory (3) — session-level zone interaction history
    zone_memory = state.get("zone_memory", {})
    zone_key = None
    if zone is not None:
        zone_key = round(zone.center_price * 4) / 4  # snap to tick grid
    elif price > 0:
        zone_key = round(price * 4) / 4
    zm = zone_memory.get(zone_key, {}) if zone_key else {}
    seg_zone_memory = np.array(
        [
            min(zm.get("touch_count", 0), 10) / 10.0,  # 0-1, capped at 10
            zm.get("last_result", 0.0),  # +1 bounced, -1 broke through, 0 first
            min(zm.get("time_since_last", 3600), 3600) / 3600.0,  # 0-1, capped at 1h
        ],
        dtype=np.float32,
    )

    # 18. Cross-zone narrative (5) — connects stacked zones so the model
    # treats cascading breakdowns / climbs as a continuous trend instead
    # of independent zone-by-zone decisions.
    pz = state.get("prev_zone", {}) or {}
    # Normalise dist by 20pt (typical NQ stacked-zone spacing); clip ±1.0
    dist_norm = max(-1.0, min(1.0, float(pz.get("dist_pts", 0.0)) / 20.0))
    age_norm = min(float(pz.get("age_s", 0.0)) / 3600.0, 1.0)
    stack_density = int(state.get("zone_stack", 0))
    seg_prev_zone = np.array(
        [
            dist_norm,  # signed dist to prev zone (-1 to +1)
            float(pz.get("outcome", 0.0)),  # +1 prev rejected / -1 prev broke / 0 unknown
            age_norm,  # how long ago (0=now, 1=1h+)
            float(pz.get("valid", 0.0)),  # 1 if prev exists, else 0
            min(stack_density, 8) / 8.0,  # zones-within-5pt count, normed 0-1
        ],
        dtype=np.float32,
    )

    # Zone-sweep detection — teaches the model the stop-hunt pattern.
    # Audit 2026-05-15: 29 of 33 recent stop exits (87.9%) were wicks
    # piercing zones 5-10 ticks then reversing 5R+ in our intended
    # direction. The model was systematically the early bidder at zones
    # that were about to be swept. These two features expose:
    #   * zone_sweep_recent_t — exp(-Δt/600s), 0=no sweep on file,
    #     1=sweep just happened. Post-sweep entries are statistically
    #     the winners; the model should LIKE high values.
    #   * last_wick_size_R — magnitude of the most recent wick that
    #     pierced this zone, in R units (wick_ticks / stop_ticks).
    #     Big wick → market just paid the liquidity → next test is
    #     the real move.
    sweep_state = state.get("zone_sweep", {}) or {}
    sweep_recent_t = float(sweep_state.get("recent_t", 0.0))
    sweep_wick_R = float(sweep_state.get("last_wick_R", 0.0))
    # Clip to [0,1] / [0,5] respectively — bounded inputs train cleaner.
    sweep_recent_t = max(0.0, min(1.0, sweep_recent_t))
    sweep_wick_R = max(0.0, min(5.0, sweep_wick_R))
    seg_zone_sweep = np.array([sweep_recent_t, sweep_wick_R], dtype=np.float32)

    obs = np.concatenate(
        [
            seg_level,  # len(LevelType) — multi-hot (zone) or one-hot (legacy)
            seg_orderflow,  # 21
            seg_structure,  # 64
            seg_tpo,  # 38
            seg_candles,  # 15
            seg_zone_feats,  # 4 (zone) or 0 (legacy)
            seg_confluence,  # 5 (zone) or 8 (legacy)
            seg_macro,  # 11
            seg_exchange,  # 5
            seg_setup,  # 14
            seg_amt,  # 20
            seg_amt_dynamics,  # 20
            seg_micro,  # 20
            seg_approach,  # 1
            seg_execution,  # 7
            seg_session_cvd,  # 2 (RTH-session CVD ratio + sign)
            seg_hvn_lvn,  # 2 (signed distance to nearest HVN/LVN)
            seg_big_abs,  # 2 (absolute ≥25-contract activity)
            seg_of_alignment,  # 3 (of_score, zone_strength, their product)
            seg_reaction,  # 8 (Phase 3a — post-touch market reaction)
            seg_pattern,  # 5 (Phase 3a — explicit pattern detectors)
            seg_zone_quality,  # 1 (Phase 3a — unified level quality score)
            seg_zone_memory,  # 3
            seg_prev_zone,  # 5 (Phase 3d — cross-zone narrative for stacked zones)
            seg_zone_sweep,  # 2 (Phase 4 — sweep recency + wick magnitude in R)
        ]
    )

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
GBT_FORECAST_DIM: int = 8  # prob_cont, prob_rev, confidence, best_r, worst_r, breakeven, levels, stop
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

    return np.array(
        [
            pos_flat,
            pos_long,
            pos_short,
            unrealized_r,
            time_in_trade,
            session_pnl_norm,
            consec_norm,
            progress,
        ],
        dtype=np.float32,
    )


from .narrative_features import NARRATIVE_DIM, extract_narrative_features
from .trigger_features import TRIGGER_DIM, build_trigger_observation

# V5 dimensions (Phase 3b: trigger obs is 118-dim)
NARRATIVE_OBSERVATION_DIM = NARRATIVE_DIM  # 18
TRIGGER_OBSERVATION_DIM = TRIGGER_DIM  # 118


def build_narrative(state: dict) -> np.ndarray:
    """Build the narrative observation (slow-layer bias/risk)."""
    return extract_narrative_features(state)


def build_trigger(
    state: dict,
    trigger_gbt_forecast: np.ndarray | None = None,
) -> np.ndarray:
    """Build the trigger observation (Phase 3b: no narrative/setup_probs)."""
    base_obs = build_observation(state)
    return build_trigger_observation(state, base_obs, trigger_gbt_forecast)
