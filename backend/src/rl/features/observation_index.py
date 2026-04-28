"""Schema describing the layout of `build_observation()`'s output vector.

Maintained alongside `observation.py:build_observation` (zone mode). The Stocks
UI fetches this once on connect via `GET /stocks/api/observation-schema` and
uses it to slice + label the per-tick `inputs[]` array on `dqn_inference`
events. When the segment list changes here, the assertion at module import
fails loudly so we can't ship a stale schema.

Schema is a list of segments; each segment has:
    name     : machine identifier (e.g. "orderflow")
    title    : human display ("Order Flow")
    size     : number of dims contributed
    labels   : per-dim labels, length == size (None means "use generic dim_i")
    kind     : "scalar" | "multi_hot" | "one_hot" — for UI rendering hints

`SCHEMA_VERSION` bumps when the segment ordering or sizes change so the UI can
detect mismatches against its cached copy.
"""

from __future__ import annotations

from typing import Literal, TypedDict

from ..config import LevelType
from .narrative_features import NARRATIVE_NAMES
from .observation import OBSERVATION_DIM

SCHEMA_VERSION = 1


class Segment(TypedDict):
    name: str
    title: str
    size: int
    labels: list[str]
    kind: Literal["scalar", "multi_hot", "one_hot"]


def _zone_composition_labels() -> list[str]:
    return [lt.value for lt in LevelType]


_ORDERFLOW_LABELS: list[str] = [
    "delta_pct",
    "delta_norm",
    "cvd_norm",
    "cvd_trend",
    "volume_ratio",
    "body_ratio",
    "spread_ticks",
    "passive_active_ratio",
    "imbalance_density",
    "stacked_imbalance_count",
    "stacked_direction",
    "big_trades_count",
    "big_trades_net_delta",
    "vsa_absorption",
    "stop_run_detected",
    "delta_acceleration",
    "absorption_strength",
    "initiative_momentum",
    "volume_climax",
    "delta_divergence",
    "flow_shift",
]


def _structure_labels() -> list[str]:
    out: list[str] = ["price_vs_vwap_sd"]
    out += ["va_contains_price", "dist_to_poc", "dist_to_vah", "dist_to_val", "va_width"]
    out += ["ib_range", "above_ib_high", "below_ib_low"]
    out += [
        "minutes_since_rth",
        "session_volume_pct",
        "daily_range_pct",
        "minute_of_day_sin",
        "minute_of_day_cos",
        "session_rth",
        "session_globex",
        "session_overnight",
        "session_phase",
        "ib_break_up",
        "ib_break_down",
    ]
    for tf in ("d", "w", "m"):
        out.append(f"trend_{tf}")
    for tf in ("d", "w", "m"):
        out.append(f"dist_to_sh_{tf}")
    for tf in ("d", "w", "m"):
        out.append(f"dist_to_sl_{tf}")
    for tf in ("d", "w", "m"):
        out.append(f"above_sh_{tf}")
    for tf in ("d", "w", "m"):
        out.append(f"below_sl_{tf}")
    for tf in ("d", "w", "m"):
        out.append(f"position_{tf}")
    for tf in ("d", "w", "m"):
        out.append(f"hh_lh_{tf}")
    for tf in ("d", "w", "m"):
        out.append(f"hl_ll_{tf}")
    for tf in ("d", "w", "m"):
        out.append(f"swing_range_{tf}")
    for tf in ("d", "w", "m"):
        out.append(f"bos_active_{tf}")
    for tf in ("d", "w", "m"):
        out.append(f"choch_active_{tf}")
    for tf in ("d", "w", "m"):
        out.append(f"last_event_dir_{tf}")
    for tf in ("d", "w", "m"):
        out.append(f"swing_momentum_{tf}")
    out.append("trend_alignment")
    out += ["dist_to_pdh", "dist_to_pdl", "pdh_pdl_position", "pdh_pdl_range"]
    return out


def _tpo_labels() -> list[str]:
    per_session = [
        "price_vs_poc",
        "price_vs_vah",
        "price_vs_val",
        "shape",
        "ib_range",
        "price_vs_ib_mid",
        "poor_signal",
        "price_position_in_va",
        "rotation_factor",
        "opening_type",
        "opening_direction",
        "excess_signal",
    ]
    out: list[str] = []
    for sess in ("tokyo", "london", "ny"):
        out += [f"{sess}_{x}" for x in per_session]
    out += ["poc_migration_tokyo_london", "poc_migration_london_ny"]
    return out


_CANDLE_LABELS: list[str] = [f"c{i}_{f}" for i in range(5) for f in ("delta_norm", "volume_norm", "body_ratio")]

_ZONE_FEATURE_LABELS: list[str] = [
    "width_norm",
    "member_count_norm",
    "hierarchy_score",
    "session_relevance",
]

_ZONE_CONFLUENCE_LABELS: list[str] = [
    "nearest_higher_zone_dist",
    "nearest_lower_zone_dist",
    "fvg_overlap",
    "fvg_width_norm",
    "single_print_overlap",
]

_MACRO_LABELS: list[str] = [
    "vix_norm",
    "vix_change_norm",
    "regime_score",
    "dxy_change",
    "us10y_change",
    "us2y_change",
    "yield_curve_spread",
    "cot_net_norm",
    "cot_change_norm",
    "news_proximity",
    "news_importance",
]

_EXCHANGE_STATS_LABELS: list[str] = [
    "oi_norm",
    "oi_change_norm",
    "settlement_dist",
    "cleared_vol_norm",
    "block_vol_ratio",
]

_SETUP_LABELS: list[str] = [
    "poor_extreme",
    "ib_break",
    "spring",
    "sfp",
    "rule_80",
    "fakeout",
    "break_from_balance",
    "double_distribution",
    "news_directional",
    "absorption",
    "vwap_sd2_reversal",
    "gap_logic",
    "pbd",
    "squeeze",
]

_AMT_LABELS: list[str] = [
    "day_non_trend",
    "day_normal",
    "day_neutral",
    "day_normal_variation",
    "day_trend",
    "day_double_distribution",
    "open_OD",
    "open_OTD",
    "open_ORR",
    "open_OA",
    "range_extension",
    "va_overlap",
    "value_migration",
    "ib_percentile",
    "overnight_gap",
    "open_vs_prior_poc",
    "composite_va_overlap",
    "prior_poor_high",
    "prior_poor_low",
    "prior_excess_quality",
]

_AMT_DYNAMICS_LABELS: list[str] = [
    "ib_ext_up_count",
    "ib_ext_down_count",
    "ib_max_extension",
    "ib_ext_net_direction",
    "developing_day_type",
    "day_type_confidence",
    "responsive_ratio",
    "initiative_ratio",
    "va_acceptance_high",
    "va_rejection_high",
    "va_acceptance_low",
    "va_rejection_low",
    "poc_migration_speed",
    "va_width_expansion_rate",
    "balance_duration",
    "balance_width",
    "single_print_proximity",
    "excess_high",
    "excess_low",
    "otf_activity",
]

_MICRO_LABELS: list[str] = [
    "approach_velocity",
    "approach_accel",
    "net_delta_norm",
    "delta_trend",
    "max_trade_size_norm",
    "big_trade_ratio",
    "buy_volume_ratio",
    "tick_spread_norm",
    "consec_direction",
    "reversal_count_norm",
    "time_compression",
    "last5_avg_size",
    "last5_buy_ratio",
    "bid_side_aggression",
    "size_at_touch_norm",
    "approach_linearity",
    "vol_surge",
    "price_vs_midrange",
    "big_trade_skew",
    "last5_acceleration",
]

_EXECUTION_LABELS: list[str] = [
    "follow_through_confirmed",
    "follow_through_strength",
    "is_responsive_auction",
    "is_initiative_auction",
    "session_atr_norm",
    "volume_anomaly",
    "time_in_session",
]

_REACTION_LABELS: list[str] = [
    "reaction_velocity",
    "reaction_aggression",
    "rejection_speed",
    "vol_spike_ratio",
    "tape_compression",
    "delta_alignment",
    "opposing_momentum",
    "reaction_linearity",
]

_PATTERN_LABELS: list[str] = [
    "pin_bar_rejection",
    "absorption_wall",
    "imbalance_cluster",
    "delta_divergence",
    "trapped_breakout",
]


def _build_segments() -> list[Segment]:
    zone_labels = _zone_composition_labels()
    structure_labels = _structure_labels()
    tpo_labels = _tpo_labels()
    return [
        {
            "name": "zone_composition",
            "title": "Zone composition",
            "size": len(zone_labels),
            "labels": zone_labels,
            "kind": "multi_hot",
        },
        {
            "name": "orderflow",
            "title": "Order flow",
            "size": 21,
            "labels": _ORDERFLOW_LABELS,
            "kind": "scalar",
        },
        {
            "name": "structure",
            "title": "Structure / VWAP / VP / Dow",
            "size": 64,
            "labels": structure_labels,
            "kind": "scalar",
        },
        {
            "name": "tpo",
            "title": "TPO (per-session)",
            "size": 38,
            "labels": tpo_labels,
            "kind": "scalar",
        },
        {
            "name": "candles",
            "title": "Candle window",
            "size": 15,
            "labels": _CANDLE_LABELS,
            "kind": "scalar",
        },
        {
            "name": "zone_features",
            "title": "Zone features",
            "size": 4,
            "labels": _ZONE_FEATURE_LABELS,
            "kind": "scalar",
        },
        {
            "name": "zone_confluence",
            "title": "Zone confluence",
            "size": 5,
            "labels": _ZONE_CONFLUENCE_LABELS,
            "kind": "scalar",
        },
        {
            "name": "macro",
            "title": "Macro",
            "size": 11,
            "labels": _MACRO_LABELS,
            "kind": "scalar",
        },
        {
            "name": "exchange_stats",
            "title": "Exchange stats",
            "size": 5,
            "labels": _EXCHANGE_STATS_LABELS,
            "kind": "scalar",
        },
        {
            "name": "setup",
            "title": "Setup detectors",
            "size": 14,
            "labels": _SETUP_LABELS,
            "kind": "multi_hot",
        },
        {
            "name": "amt",
            "title": "AMT (Dalton)",
            "size": 20,
            "labels": _AMT_LABELS,
            "kind": "scalar",
        },
        {
            "name": "amt_dynamics",
            "title": "AMT dynamics",
            "size": 20,
            "labels": _AMT_DYNAMICS_LABELS,
            "kind": "scalar",
        },
        {
            "name": "micro",
            "title": "Micro (tick-level)",
            "size": 20,
            "labels": _MICRO_LABELS,
            "kind": "scalar",
        },
        {
            "name": "approach",
            "title": "Approach direction",
            "size": 1,
            "labels": ["approach_direction"],
            "kind": "scalar",
        },
        {
            "name": "execution",
            "title": "Execution context",
            "size": 7,
            "labels": _EXECUTION_LABELS,
            "kind": "scalar",
        },
        {
            "name": "session_cvd",
            "title": "Session CVD",
            "size": 2,
            "labels": ["session_cvd_ratio", "session_cvd_sign"],
            "kind": "scalar",
        },
        {
            "name": "hvn_lvn",
            "title": "HVN / LVN distance",
            "size": 2,
            "labels": ["hvn_dist", "lvn_dist"],
            "kind": "scalar",
        },
        {
            "name": "big_abs",
            "title": "Big-trade absolute",
            "size": 2,
            "labels": ["big_abs_count", "big_abs_net"],
            "kind": "scalar",
        },
        {
            "name": "of_alignment",
            "title": "OF × zone alignment",
            "size": 3,
            "labels": ["of_score_rev", "zone_strength", "of_zone_alignment"],
            "kind": "scalar",
        },
        {
            "name": "reaction",
            "title": "Post-touch reaction",
            "size": 8,
            "labels": _REACTION_LABELS,
            "kind": "scalar",
        },
        {
            "name": "pattern",
            "title": "Pattern detectors",
            "size": 5,
            "labels": _PATTERN_LABELS,
            "kind": "multi_hot",
        },
        {
            "name": "zone_quality",
            "title": "Zone quality",
            "size": 1,
            "labels": ["zone_quality"],
            "kind": "scalar",
        },
        {
            "name": "zone_memory",
            "title": "Zone touch memory",
            "size": 3,
            "labels": ["touch_count_norm", "last_result", "time_since_last"],
            "kind": "scalar",
        },
    ]


SEGMENTS: list[Segment] = _build_segments()


def _validate() -> None:
    total = sum(s["size"] for s in SEGMENTS)
    if total != OBSERVATION_DIM:
        raise RuntimeError(
            f"observation_index schema sums to {total} dims but build_observation() "
            f"produces {OBSERVATION_DIM}. Update SEGMENTS in observation_index.py to "
            f"match the actual layout in observation.py:build_observation()."
        )
    for seg in SEGMENTS:
        if len(seg["labels"]) != seg["size"]:
            raise RuntimeError(
                f"Segment '{seg['name']}' declares size={seg['size']} but has {len(seg['labels'])} labels."
            )


_validate()


def schema() -> dict:
    """Return the JSON-serializable schema for `/stocks/api/observation-schema`.

    Computed once at import time but returned via a function so future hot-
    reloads can refresh.
    """
    out_segments: list[dict] = []
    cursor = 0
    for seg in SEGMENTS:
        out_segments.append(
            {
                "name": seg["name"],
                "title": seg["title"],
                "size": seg["size"],
                "start": cursor,
                "end": cursor + seg["size"],
                "labels": list(seg["labels"]),
                "kind": seg["kind"],
            }
        )
        cursor += seg["size"]
    return {
        "version": SCHEMA_VERSION,
        "total_dim": OBSERVATION_DIM,
        "narrative_names": list(NARRATIVE_NAMES),
        "segments": out_segments,
    }
