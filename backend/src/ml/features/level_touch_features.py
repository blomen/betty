"""Extract feature vectors for the level touch ML classifier.

Features cover ~60 dimensions grouped into:
- Level metadata (7)
- Orderflow snapshot (18)
- Temporal derivatives (10)
- Session context (15)
- Macro context (5)
- Candle pattern (5)
"""

# ---------------------------------------------------------------------------
# Categorical encoding maps
# ---------------------------------------------------------------------------

LEVEL_TYPE_MAP: dict[str, int] = {
    "poc": 0,
    "vah": 1,
    "val": 2,
    "vwap": 3,
    "vwap_1sd": 4,
    "vwap_2sd": 5,
    "vwap_3sd": 6,
    "pdh": 7,
    "pdl": 8,
    "ib_high": 9,
    "ib_low": 10,
    "order_block": 11,
    "fvg": 12,
    "naked_poc": 13,
    "tokyo": 14,
    "london": 15,
    "weekly": 16,
    "monthly": 17,
}

LEVEL_CATEGORY_MAP: dict[str, int] = {
    "band": 0,
    "prior": 1,
    "overnight": 2,
    "structure": 3,
    "session": 4,
}

APPROACH_MAP: dict[str, int] = {
    "from_below": 0,
    "from_above": 1,
}

CVD_TREND_MAP: dict[str, int] = {
    "rising": 0,
    "falling": 1,
    "flat": 2,
}

STACKED_DIR_MAP: dict[str, int] = {
    "buy": 0,
    "sell": 1,
    "neutral": 2,
}

MARKET_TYPE_MAP: dict[str, int] = {
    "balanced": 0,
    "trending_up": 1,
    "trending_down": 2,
}

OPENING_TYPE_MAP: dict[str, int] = {
    "OD": 0,
    "OTD": 1,
    "ORR": 2,
    "OA": 3,
}

VALUE_MIGRATION_MAP: dict[str, int] = {
    "up": 0,
    "down": 1,
    "overlapping": 2,
}

DEV_POC_MAP: dict[str, int] = {
    "up": 0,
    "down": 1,
    "flat": 2,
}

REGIME_MAP: dict[str, int] = {
    "risk_on": 0,
    "risk_off": 1,
    "mixed": 2,
}

MACRO_BIAS_MAP: dict[str, int] = {
    "bull": 0,
    "bear": 1,
    "neutral": 2,
}

# Maps feature name → its encoding dict (for model preprocessing)
CATEGORICAL_MAPS: dict[str, dict[str, int]] = {
    "level_type": LEVEL_TYPE_MAP,
    "level_category": LEVEL_CATEGORY_MAP,
    "approach_direction": APPROACH_MAP,
    "cvd_trend": CVD_TREND_MAP,
    "stacked_imbalance_direction": STACKED_DIR_MAP,
    "market_type": MARKET_TYPE_MAP,
    "opening_type": OPENING_TYPE_MAP,
    "value_migration": VALUE_MIGRATION_MAP,
    "developing_poc_direction": DEV_POC_MAP,
    "regime": REGIME_MAP,
    "macro_bias": MACRO_BIAS_MAP,
}

# Feature names that carry boolean semantics
BOOLEAN_FEATURES: set[str] = {
    "delta_aligned",
    "delta_divergence",
    "delta_unwind",
    "vsa_absorption",
    "tick_vol_accelerating",
    "trapped_traders",
    "stop_run_detected",
    "price_in_value_area",
    "last_candle_is_doji",
}

# Ordered list of all feature names (used for model encoding / column ordering)
FEATURE_NAMES: list[str] = [
    # Level metadata (7)
    "level_type",
    "level_category",
    "level_strength",
    "level_confluence",
    "approach_direction",
    "distance_from_poc",
    "distance_from_vwap",
    # Orderflow snapshot (18)
    "delta",
    "delta_aligned",
    "delta_divergence",
    "delta_unwind",
    "cvd",
    "cvd_trend",
    "vsa_absorption",
    "tick_vol_accelerating",
    "trapped_traders",
    "passive_active_ratio",
    "big_trades_count",
    "big_trades_net_delta",
    "stop_run_detected",
    "imbalance_ratio_max",
    "stacked_imbalance_count",
    "stacked_imbalance_direction",
    "last_candle_delta",
    "last_candle_body_ratio",
    # Temporal derivatives (10)
    "delta_slope_5m",
    "delta_slope_10m",
    "cvd_acceleration",
    "volume_roc_5m",
    "tick_rate_roc",
    "spread_compression",
    "time_to_level_seconds",
    "price_velocity",
    "absorption_building",
    "imbalance_trend",
    # Session context (15)
    "market_type",
    "opening_type",
    "ib_range",
    "ib_range_vs_aspr",
    "aspr_percentile",
    "rotation_factor",
    "value_migration",
    "price_vs_vah",
    "price_vs_val",
    "price_vs_poc",
    "price_in_value_area",
    "session_elapsed_pct",
    "minutes_since_open",
    "developing_poc_direction",
    "prior_touch_count",
    # Macro context (5)
    "vix_level",
    "vix_change",
    "regime",
    "regime_score",
    "macro_bias",
    # Candle pattern (5)
    "last_3_candles_direction",
    "last_candle_is_doji",
    "consecutive_same_direction",
    "highest_volume_candle_position",
    "range_expansion",
]


def extract_level_touch_features(
    # Level metadata (7)
    level_type: str | None = None,
    level_category: str | None = None,
    level_strength: int | None = None,
    level_confluence: int | None = None,
    approach_direction: str | None = None,
    distance_from_poc: float | None = None,
    distance_from_vwap: float | None = None,
    # Orderflow snapshot (18)
    delta: int | None = None,
    delta_aligned: bool | None = None,
    delta_divergence: bool | None = None,
    delta_unwind: bool | None = None,
    cvd: int | None = None,
    cvd_trend: str | None = None,
    vsa_absorption: bool | None = None,
    tick_vol_accelerating: bool | None = None,
    trapped_traders: bool | None = None,
    passive_active_ratio: float | None = None,
    big_trades_count: int | None = None,
    big_trades_net_delta: int | None = None,
    stop_run_detected: bool | None = None,
    imbalance_ratio_max: float | None = None,
    stacked_imbalance_count: int | None = None,
    stacked_imbalance_direction: str | None = None,
    last_candle_delta: int | None = None,
    last_candle_body_ratio: float | None = None,
    # Temporal derivatives (10)
    delta_slope_5m: float | None = None,
    delta_slope_10m: float | None = None,
    cvd_acceleration: float | None = None,
    volume_roc_5m: float | None = None,
    tick_rate_roc: float | None = None,
    spread_compression: float | None = None,
    time_to_level_seconds: int | None = None,
    price_velocity: float | None = None,
    absorption_building: bool | None = None,
    imbalance_trend: float | None = None,
    # Session context (15)
    market_type: str | None = None,
    opening_type: str | None = None,
    ib_range: int | None = None,
    ib_range_vs_aspr: float | None = None,
    aspr_percentile: float | None = None,
    rotation_factor: float | None = None,
    value_migration: str | None = None,
    price_vs_vah: float | None = None,
    price_vs_val: float | None = None,
    price_vs_poc: float | None = None,
    price_in_value_area: bool | None = None,
    session_elapsed_pct: float | None = None,
    minutes_since_open: int | None = None,
    developing_poc_direction: str | None = None,
    prior_touch_count: int | None = None,
    # Macro context (5)
    vix_level: float | None = None,
    vix_change: float | None = None,
    regime: str | None = None,
    regime_score: float | None = None,
    macro_bias: str | None = None,
    # Approach volume (6)
    approach_vol_slope: float | None = None,
    approach_vol_ratio: float | None = None,
    approach_delta_slope: float | None = None,
    approach_buy_pct_trend: float | None = None,
    approach_vol_accel: float | None = None,
    approach_big_vol_count: int | None = None,
    # Candle pattern (5)
    last_3_candles_direction: int | None = None,
    last_candle_is_doji: bool | None = None,
    consecutive_same_direction: int | None = None,
    highest_volume_candle_position: int | None = None,
    range_expansion: float | None = None,
) -> dict:
    """Extract a flat feature dict for a single level touch observation."""
    return {
        # Level metadata
        "level_type": level_type,
        "level_category": level_category,
        "level_strength": level_strength,
        "level_confluence": level_confluence,
        "approach_direction": approach_direction,
        "distance_from_poc": distance_from_poc,
        "distance_from_vwap": distance_from_vwap,
        # Orderflow snapshot
        "delta": delta,
        "delta_aligned": delta_aligned,
        "delta_divergence": delta_divergence,
        "delta_unwind": delta_unwind,
        "cvd": cvd,
        "cvd_trend": cvd_trend,
        "vsa_absorption": vsa_absorption,
        "tick_vol_accelerating": tick_vol_accelerating,
        "trapped_traders": trapped_traders,
        "passive_active_ratio": passive_active_ratio,
        "big_trades_count": big_trades_count,
        "big_trades_net_delta": big_trades_net_delta,
        "stop_run_detected": stop_run_detected,
        "imbalance_ratio_max": imbalance_ratio_max,
        "stacked_imbalance_count": stacked_imbalance_count,
        "stacked_imbalance_direction": stacked_imbalance_direction,
        "last_candle_delta": last_candle_delta,
        "last_candle_body_ratio": last_candle_body_ratio,
        # Temporal derivatives
        "delta_slope_5m": delta_slope_5m,
        "delta_slope_10m": delta_slope_10m,
        "cvd_acceleration": cvd_acceleration,
        "volume_roc_5m": volume_roc_5m,
        "tick_rate_roc": tick_rate_roc,
        "spread_compression": spread_compression,
        "time_to_level_seconds": time_to_level_seconds,
        "price_velocity": price_velocity,
        "absorption_building": absorption_building,
        "imbalance_trend": imbalance_trend,
        # Session context
        "market_type": market_type,
        "opening_type": opening_type,
        "ib_range": ib_range,
        "ib_range_vs_aspr": ib_range_vs_aspr,
        "aspr_percentile": aspr_percentile,
        "rotation_factor": rotation_factor,
        "value_migration": value_migration,
        "price_vs_vah": price_vs_vah,
        "price_vs_val": price_vs_val,
        "price_vs_poc": price_vs_poc,
        "price_in_value_area": price_in_value_area,
        "session_elapsed_pct": session_elapsed_pct,
        "minutes_since_open": minutes_since_open,
        "developing_poc_direction": developing_poc_direction,
        "prior_touch_count": prior_touch_count,
        # Macro context
        "vix_level": vix_level,
        "vix_change": vix_change,
        "regime": regime,
        "regime_score": regime_score,
        "macro_bias": macro_bias,
        # Approach volume
        "approach_vol_slope": approach_vol_slope,
        "approach_vol_ratio": approach_vol_ratio,
        "approach_delta_slope": approach_delta_slope,
        "approach_buy_pct_trend": approach_buy_pct_trend,
        "approach_vol_accel": approach_vol_accel,
        "approach_big_vol_count": approach_big_vol_count,
        # Candle pattern
        "last_3_candles_direction": last_3_candles_direction,
        "last_candle_is_doji": last_candle_is_doji,
        "consecutive_same_direction": consecutive_same_direction,
        "highest_volume_candle_position": highest_volume_candle_position,
        "range_expansion": range_expansion,
    }
