"""Index → feature-name mapping for observation vectors.

The RL pipeline builds 302-dim base, 318-dim augmented, and 118-dim trigger
observations. Feature-importance outputs from LightGBM come back as raw
indices like `feature[285] = 356`, which say nothing about what feature that
actually is. This module translates an index back into
``(segment_name, dim_within_segment)``, and for well-known segments returns
the specific subfeature name (e.g. `orderflow[17] = stacked_imbalance_count`).

Keeps analysis tools, CLI reports, and the feature-correlation scans from
having to re-derive the segment layout each time.
"""

from __future__ import annotations

from dataclasses import dataclass

from .registry import AUGMENTED_SCHEMA, BASE_OBSERVATION_SCHEMA, TRIGGER_OBSERVATION_SCHEMA

# Per-segment subfeature names. Only populated where we know them. Unknown
# slots fall back to "<segment>[<idx>]" at print time.
_SUB_FEATURES: dict[str, list[str]] = {
    # orderflow_features.py (21 dims) — candle-level orderflow signals
    "orderflow": [
        "delta_ratio_signed",  # 0
        "delta_sign",  # 1
        "delta_magnitude_norm",  # 2
        "cvd_norm",  # 3
        "cvd_slope",  # 4
        "buy_vol_ratio",  # 5
        "sell_vol_ratio",  # 6
        "trade_count_norm",  # 7
        "avg_trade_size_norm",  # 8
        "big_trade_count_norm",  # 9
        "imbalance_max_buy",  # 10
        "imbalance_max_sell",  # 11
        "imbalance_mean",  # 12
        "stacked_imbalance_count_signed",  # 13
        "stacked_imbalance_magnitude",  # 14
        "stacked_imbalance_flip",  # 15
        "absorption_count",  # 16
        "absorption_strength",  # 17
        "absorption_wall_up",  # 18
        "absorption_wall_down",  # 19
        "of_composite_score",  # 20
    ],
    # micro_features.py (20 dims) — tick-level approach features
    "micro": [
        "tick_count_norm",  # 0
        "approach_accel",  # 1
        "approach_velocity",  # 2
        "aggression_ratio",  # 3
        "buy_pressure",  # 4
        "sell_pressure",  # 5
        "micro_delta_ratio",  # 6
        "micro_cvd_slope",  # 7
        "tape_compression",  # 8
        "reversal_count_norm",  # 9
        "big_trade_count_norm",  # 10
        "last5_velocity",  # 11
        "last5_delta_ratio",  # 12
        "mean_trade_size_norm",  # 13
        "inter_trade_time_norm",  # 14
        "price_range_norm",  # 15
        "direction_changes_norm",  # 16
        "trap_detector_score",  # 17
        "accel_sign_flip",  # 18
        "last5_acceleration",  # 19
    ],
    # reaction_features.py (8 dims) — post-touch market reaction
    "reaction": [
        "reaction_velocity",
        "reaction_aggression",
        "rejection_speed",
        "vol_spike_ratio",
        "tape_compression_post",
        "delta_alignment_with_dir",
        "opposing_momentum_build",
        "reaction_linearity",
    ],
    # pattern_features.py (5 dims)
    "patterns": [
        "pin_bar_rejection",
        "absorption_wall",
        "imbalance_cluster",
        "delta_divergence",
        "trapped_breakout",
    ],
    # level_features.zone_features (4 dims)
    "zone_features": [
        "hierarchy_score",
        "member_count_norm",
        "strength_norm",
        "width_ticks_norm",
    ],
    # level_features.zone_confluence (5 dims)
    "zone_confluence": [
        "overlap_fvg",
        "overlap_single_print",
        "overlap_hvn",
        "overlap_lvn",
        "overlap_count",
    ],
    # AugmentedSchema: gbt_forecast (8 dims — TriggerGBT output)
    "gbt_forecast": [
        "prob_cont",
        "prob_rev",
        "confidence",
        "expected_best_r",
        "expected_worst_r",
        "prob_breakeven",
        "predicted_levels",
        "predicted_stop",
    ],
    # AugmentedSchema: position_state (8 dims)
    "position_state": [
        "pos_side",
        "unrealized_r",
        "time_in_trade_norm",
        "session_pnl_norm",
        "consec_wins_norm",
        "consec_losses_norm",
        "session_progress",
        "reserved",
    ],
    # zone_memory (3 dims)
    "zone_memory": [
        "touch_count_norm",
        "last_result_signed",
        "time_since_last_touch_norm",
    ],
    # of_alignment (3 dims)
    "of_alignment": [
        "of_score_x_zone_strength",
        "and_gate_score",
        "direction_consistency",
    ],
    # session_cvd (2 dims)
    "session_cvd": ["rth_cvd_ratio_norm", "rth_cvd_sign"],
    # hvn_lvn (2 dims)
    "hvn_lvn": ["signed_dist_hvn_norm", "signed_dist_lvn_norm"],
    # big_trades_abs (2 dims)
    "big_trades_abs": ["big_trade_count_25plus_norm", "big_trade_size_mean_norm"],
    # approach_dir (1 dim)
    "approach_dir": ["direction_signed"],
    # zone_quality (1 dim)
    "zone_quality": ["unified_level_quality"],
}


def level_composition_names() -> list[str]:
    """Per-index names for the 31-dim level_composition multi-hot vector.

    Matches the ordering of LevelType enum in src/rl/config.py.
    """
    from ..config import LevelType

    return [lt.value for lt in LevelType]


@dataclass(frozen=True)
class FeatureLocation:
    """Where a given observation index falls in the segment layout."""

    obs_kind: str  # "base" | "augmented" | "trigger"
    index: int  # global index in the observation vector
    segment_name: str
    segment_start: int
    segment_dim: int
    sub_index: int  # index within the segment
    sub_name: str | None  # subfeature name if known

    @property
    def pretty(self) -> str:
        """Printable identifier for importance reports."""
        if self.sub_name:
            return f"{self.segment_name}[{self.sub_index}]={self.sub_name}"
        return f"{self.segment_name}[{self.sub_index}]"


def _locate(idx: int, schema, obs_kind: str) -> FeatureLocation | None:
    offset = 0
    for seg in schema:
        if idx < offset + seg.dim:
            sub = idx - offset
            sub_name = None
            names = _SUB_FEATURES.get(seg.name)
            if names and sub < len(names):
                sub_name = names[sub]
            return FeatureLocation(
                obs_kind=obs_kind,
                index=idx,
                segment_name=seg.name,
                segment_start=offset,
                segment_dim=seg.dim,
                sub_index=sub,
                sub_name=sub_name,
            )
        offset += seg.dim
    return None


def locate_base(idx: int) -> FeatureLocation | None:
    """Resolve an index in the 302-dim base observation."""
    return _locate(idx, BASE_OBSERVATION_SCHEMA, "base")


def locate_augmented(idx: int) -> FeatureLocation | None:
    """Resolve an index in the 318-dim augmented observation (base + GBT + position)."""
    total = sum(s.dim for s in BASE_OBSERVATION_SCHEMA)
    if idx < total:
        return locate_base(idx)
    return _locate(idx - total, AUGMENTED_SCHEMA, "augmented")


def locate_trigger(idx: int) -> FeatureLocation | None:
    """Resolve an index in the 118-dim trigger observation."""
    return _locate(idx, TRIGGER_OBSERVATION_SCHEMA, "trigger")


def pretty_base(idx: int) -> str:
    loc = locate_base(idx)
    return loc.pretty if loc else f"<OOB:{idx}>"


def pretty_augmented(idx: int) -> str:
    loc = locate_augmented(idx)
    return loc.pretty if loc else f"<OOB:{idx}>"


def pretty_trigger(idx: int) -> str:
    loc = locate_trigger(idx)
    return loc.pretty if loc else f"<OOB:{idx}>"


def print_layout(obs_kind: str = "augmented") -> None:
    """Dump the segment layout for debugging / documentation."""
    if obs_kind == "base":
        schema = BASE_OBSERVATION_SCHEMA
    elif obs_kind == "augmented":
        schema = list(BASE_OBSERVATION_SCHEMA) + list(AUGMENTED_SCHEMA)
    elif obs_kind == "trigger":
        schema = TRIGGER_OBSERVATION_SCHEMA
    else:
        raise ValueError(f"unknown obs_kind: {obs_kind!r}")

    offset = 0
    total = 0
    for seg in schema:
        end = offset + seg.dim
        print(f"  [{offset:3d}:{end:3d}] {seg.name:>24s}  dim={seg.dim:>2d}  {seg.description}")
        offset = end
        total += seg.dim
    print(f"  ─────────────────────────────── total = {total}")
