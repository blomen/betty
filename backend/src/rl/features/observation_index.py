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

SCHEMA_VERSION = 6  # 2026-05-18: OF stack bumped 25→27 (+ vsa_aligned + stop_run_aligned)
# now L1-quote-derived when LevelMonitor.l1_state holds a snapshot; falls
# back to candle-derived for backward compat. Dim count unchanged (313).
# Episodes recorded before this date have these dims candle-derived; new
# episodes recorded inside a container receiving TopstepX GatewayQuote
# events have them computed from true bestBid/bestAsk + Lee-Ready
# aggressor classification.

# Methodology group taxonomy (2026-05-17, per Fabio Valentini AMT + Ryan/
# blockroots OF + Cimitan + VSA fondamenti — 12 sources read).
# Each segment is tagged with a single primary category. The category
# determines which per-group diagnostic the segment contributes to.
# Categories:
#   OF             = tick-level orderflow (delta, CVD, footprint, big trades)
#   VSA            = bar-level effort vs result (candles, reactions, patterns)
#   PROFILE        = volume distribution (volume profile + TPO time-at-price)
#   AMT            = auction state (balance/imbalance, value migration, day type)
#   DOW_STRUCTURE  = multi-TF swing structure (HH/HL/LH/LL/BOS/CHoCH) + session levels
#   MICRO          = sub-candle tick patterns (approach velocity, acceleration)
#   ZONE_MEMORY    = prev-zone narrative + sweep detection
#   MACRO          = external context (VIX, DXY, news, COT, exchange stats)
#   EXECUTION      = trade execution state (approach direction, OF alignment)
MethodologyCategory = Literal[
    "OF",
    "VSA",
    "PROFILE",
    "AMT",
    "DOW_STRUCTURE",
    "MICRO",
    "ZONE_MEMORY",
    "MACRO",
    "EXECUTION",
]


class Segment(TypedDict):
    name: str
    title: str
    size: int
    labels: list[str]
    kind: Literal["scalar", "multi_hot", "one_hot"]
    category: MethodologyCategory


def _zone_composition_labels() -> list[str]:
    return [lt.value for lt in LevelType]


# Orderflow segment (21 dims). Dims marked [L1] are recomputed from L1
# quote state when LevelMonitor.l1_state has a current snapshot — else
# they fall back to candle-derived approximations. Dims marked [L2-needed]
# are placeholders requiring depth data we don't currently subscribe to;
# they read as the candle approximation. See SCHEMA_VERSION 4 note above.
#   [L1]        spread_ticks            (index 6) — true (ask-bid)/tick_size
#   [L1]        passive_active_ratio    (index 7) — Lee-Ready classification
#   [L2-needed] imbalance_density       (index 8)
#   [L2-needed] stacked_imbalance_count (index 9)
#   [L2-needed] stacked_direction       (index 10)
# All other dims (delta, cvd, big-trade, vsa_absorption, etc.) are
# tick-aggregation based and not improvable by L1 alone.
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
    # Tier C (2026-05-18 PROFILE follow-up) — methodology gap-fill dims
    "two_way_battle",
    "failed_auction_reabsorption",
    "close_position_in_range",
    "initiative_follow_through",
    # Approach-aligned (2026-05-18) — bakes OF×approach interaction
    "vsa_aligned",
    "stop_run_aligned",
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
            "category": "PROFILE",
        },
        {
            "name": "orderflow",
            "title": "Order flow",
            "size": 27,  # bumped 25→27 on 2026-05-18: + vsa_aligned + stop_run_aligned
            "labels": _ORDERFLOW_LABELS,
            "kind": "scalar",
            "category": "OF",
        },
        {
            "name": "structure",
            "title": "Structure / VWAP / VP / Dow",
            "size": 64,
            "labels": structure_labels,
            "kind": "scalar",
            "category": "DOW_STRUCTURE",
        },
        {
            "name": "tpo",
            "title": "TPO (per-session)",
            "size": 38,
            "labels": tpo_labels,
            "kind": "scalar",
            "category": "PROFILE",
        },
        {
            "name": "candles",
            "title": "Candle window",
            "size": 15,
            "labels": _CANDLE_LABELS,
            "kind": "scalar",
            "category": "VSA",
        },
        {
            "name": "zone_features",
            "title": "Zone features",
            "size": 4,
            "labels": _ZONE_FEATURE_LABELS,
            "kind": "scalar",
            "category": "PROFILE",
        },
        {
            "name": "zone_confluence",
            "title": "Zone confluence",
            "size": 5,
            "labels": _ZONE_CONFLUENCE_LABELS,
            "kind": "scalar",
            "category": "PROFILE",
        },
        {
            "name": "macro",
            "title": "Macro",
            "size": 11,
            "labels": _MACRO_LABELS,
            "kind": "scalar",
            "category": "MACRO",
        },
        {
            "name": "exchange_stats",
            "title": "Exchange stats",
            "size": 5,
            "labels": _EXCHANGE_STATS_LABELS,
            "kind": "scalar",
            "category": "MACRO",
        },
        {
            "name": "setup",
            "title": "Setup detectors",
            "size": 14,
            "labels": _SETUP_LABELS,
            "kind": "multi_hot",
            "category": "AMT",
        },
        {
            "name": "amt",
            "title": "AMT (Dalton)",
            "size": 20,
            "labels": _AMT_LABELS,
            "kind": "scalar",
            "category": "AMT",
        },
        {
            "name": "amt_dynamics",
            "title": "AMT dynamics",
            "size": 20,
            "labels": _AMT_DYNAMICS_LABELS,
            "kind": "scalar",
            "category": "AMT",
        },
        {
            "name": "micro",
            "title": "Micro (tick-level)",
            "size": 20,
            "labels": _MICRO_LABELS,
            "kind": "scalar",
            "category": "MICRO",
        },
        {
            "name": "approach",
            "title": "Approach direction",
            "size": 1,
            "labels": ["approach_direction"],
            "kind": "scalar",
            "category": "EXECUTION",
        },
        {
            "name": "execution",
            "title": "Execution context",
            "size": 7,
            "labels": _EXECUTION_LABELS,
            "kind": "scalar",
            "category": "EXECUTION",
        },
        {
            "name": "session_cvd",
            "title": "Session CVD",
            "size": 2,
            "labels": ["session_cvd_ratio", "session_cvd_sign"],
            "kind": "scalar",
            "category": "OF",
        },
        {
            "name": "hvn_lvn",
            "title": "HVN / LVN distance",
            "size": 2,
            "labels": ["hvn_dist", "lvn_dist"],
            "kind": "scalar",
            "category": "PROFILE",
        },
        {
            "name": "big_abs",
            "title": "Big-trade absolute",
            "size": 2,
            "labels": ["big_abs_count", "big_abs_net"],
            "kind": "scalar",
            "category": "OF",
        },
        {
            "name": "of_alignment",
            "title": "OF × zone alignment",
            "size": 3,
            "labels": ["of_score_rev", "zone_strength", "of_zone_alignment"],
            "kind": "scalar",
            "category": "EXECUTION",
        },
        {
            "name": "reaction",
            "title": "Post-touch reaction",
            "size": 8,
            "labels": _REACTION_LABELS,
            "kind": "scalar",
            "category": "VSA",
        },
        {
            "name": "pattern",
            "title": "Pattern detectors",
            "size": 5,
            "labels": _PATTERN_LABELS,
            "kind": "multi_hot",
            "category": "VSA",
        },
        {
            "name": "zone_quality",
            "title": "Zone quality",
            "size": 1,
            "labels": ["zone_quality"],
            "kind": "scalar",
            "category": "PROFILE",
        },
        {
            "name": "zone_memory",
            "title": "Zone touch memory",
            "size": 3,
            "labels": ["touch_count_norm", "last_result", "time_since_last"],
            "kind": "scalar",
            "category": "ZONE_MEMORY",
        },
        {
            "name": "prev_zone",
            "title": "Cross-zone narrative",
            "size": 5,
            "labels": [
                "prev_zone_dist_norm",
                "prev_zone_outcome",
                "prev_zone_age_norm",
                "prev_zone_valid",
                "stack_density",
            ],
            "kind": "scalar",
            "category": "ZONE_MEMORY",
        },
        {
            "name": "zone_sweep",
            "title": "Zone sweep detection",
            "size": 2,
            "labels": ["zone_sweep_recent_t", "last_wick_size_R"],
            "kind": "scalar",
            "category": "ZONE_MEMORY",
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
                "category": seg["category"],
            }
        )
        cursor += seg["size"]
    return {
        "version": SCHEMA_VERSION,
        "total_dim": OBSERVATION_DIM,
        "narrative_names": list(NARRATIVE_NAMES),
        "segments": out_segments,
    }


def _segment_offsets() -> dict[str, tuple[int, int]]:
    """Return name -> (start, end) for every segment. Derived (never hardcoded)."""
    offsets: dict[str, tuple[int, int]] = {}
    cursor = 0
    for seg in SEGMENTS:
        offsets[seg["name"]] = (cursor, cursor + seg["size"])
        cursor += seg["size"]
    return offsets


_SEGMENT_OFFSETS: dict[str, tuple[int, int]] = _segment_offsets()


def get_pretouch_mask() -> list[bool]:
    """Return a length-313 boolean mask where True = pre-touch (safe at decision
    time) and False = post-touch (leakage risk — outcome already partially known).

    Rationale: the observation vector is built at zone-touch time but some
    extractors include features derived from the bar/period AFTER the touch
    (reaction_velocity, pin_bar_rejection, zone_sweep_recent_t, etc.).
    Training a model on the full 313-d obs with these dims leaks the outcome
    into the features — the model learns "what happened after" rather than
    "what to do at touch time". Useless for live, where these dims are
    structurally not computable yet.

    Use this mask in training + backtest to zero post-touch dims before
    feeding to any model that's supposed to predict at decision time.

    Specific exclusions (17 dims of 313):
      - reaction: 8 dims (reaction_velocity, rejection_speed, vol_spike_ratio, ...)
      - pattern: 5 dims (pin_bar_rejection, absorption_wall, ...)
      - zone_sweep: 2 dims (sweep detection is post-touch by definition)
      - execution[0:2]: follow_through_confirmed + follow_through_strength
        (the other 5 execution dims — is_responsive_auction, is_initiative_auction,
        session_atr_norm, volume_anomaly, time_in_session — are pre-touch and stay)
    """
    mask = [True] * OBSERVATION_DIM
    _POST_TOUCH_SEGMENTS = {"reaction", "pattern", "zone_sweep"}
    for seg in SEGMENTS:
        if seg["name"] in _POST_TOUCH_SEGMENTS:
            start, end = _SEGMENT_OFFSETS[seg["name"]]
            for i in range(start, end):
                mask[i] = False
        elif seg["name"] == "execution":
            start, _ = _SEGMENT_OFFSETS[seg["name"]]
            # follow_through_confirmed (offset 0) + follow_through_strength (offset 1)
            mask[start] = False
            mask[start + 1] = False
    return mask


_PRETOUCH_MASK: list[bool] = get_pretouch_mask()


def get_trigger_equivalent_mask() -> list[bool]:
    """Return a length-313 boolean mask where True = segment is in the GBT's
    trigger_obs feature set, False = extra dim FT-T has but GBT doesn't.

    Use to test FT-T architecture quality ON THE SAME FEATURES the GBT uses,
    controlling for the feature-set advantage FT-T otherwise has from the
    full 313-d obs. If FT-T still beats GBT under this mask, architecture
    wins (per-group encoders + attention). If WR collapses to GBT-level,
    the gap was the extra features, not the architecture.

    Included segments (mirrors trigger_features.TRIGGER_SEGMENTS minus
    the GBT-self-forecast + exec_passthrough not in observation_index):
      micro (20) + orderflow (21) + candles (15) + zone_features (4) +
      zone_confluence (5) + zone_composition (35) + approach (1) = 101 dims
      212 dims masked.

    NOTE: GBT also includes 10 passthrough dims selected from structure/
    tpo/amt_dynamics — this mask is slightly MORE restrictive. If FT-T
    still wins under this stricter constraint, the architecture
    advantage is unambiguous.
    """
    keep_segments = {
        "micro",
        "orderflow",
        "candles",
        "zone_features",
        "zone_confluence",
        "zone_composition",
        "approach",
    }
    mask = [False] * OBSERVATION_DIM
    for seg in SEGMENTS:
        if seg["name"] in keep_segments:
            start, end = _SEGMENT_OFFSETS[seg["name"]]
            for i in range(start, end):
                mask[i] = True
    return mask


_TRIGGER_EQUIVALENT_MASK: list[bool] = get_trigger_equivalent_mask()


def get_segments_by_category() -> dict[MethodologyCategory, list[Segment]]:
    """Group segments by methodology category."""
    out: dict[MethodologyCategory, list[Segment]] = {}
    for seg in SEGMENTS:
        out.setdefault(seg["category"], []).append(seg)
    return out


_CATEGORY_SEGMENTS: dict[MethodologyCategory, list[Segment]] = get_segments_by_category()


def compute_per_group_diagnostic(obs) -> dict[str, dict[str, float]]:
    """Per-category diagnostic for a single observation vector.

    Returns:
        {category: {alive_pct, signal_strength, n_dims}}
        - alive_pct: fraction of dims in this category that are nonzero
        - signal_strength: mean absolute value across category dims
        - n_dims: total dims in this category

    Used for signal-dispatch logging ("VSA: 0.45 strength, OF: 0.72") and
    UI overlay. Trust the model's synaptic weights; this is observability,
    not a gate.
    """
    import numpy as np  # local import — keep module import cheap

    arr = np.asarray(obs, dtype=np.float32)
    out: dict[str, dict[str, float]] = {}
    for cat, segs in _CATEGORY_SEGMENTS.items():
        total_dims = 0
        alive_dims = 0
        abs_sum = 0.0
        for seg in segs:
            start, end = _SEGMENT_OFFSETS[seg["name"]]
            sub = arr[start:end]
            total_dims += sub.size
            alive_dims += int((sub != 0).sum())
            abs_sum += float(np.abs(sub).sum())
        out[cat] = {
            "alive_pct": (alive_dims / total_dims) if total_dims else 0.0,
            "signal_strength": (abs_sum / total_dims) if total_dims else 0.0,
            "n_dims": float(total_dims),
        }
    return out


def format_per_group_diagnostic(diag: dict[str, dict[str, float]]) -> str:
    """Compact one-line summary for logs: 'OF 0.42 | VSA 0.31 | PROFILE 0.55 ...'"""
    parts = [f"{cat} {d['signal_strength']:.2f}" for cat, d in sorted(diag.items())]
    return " | ".join(parts)
