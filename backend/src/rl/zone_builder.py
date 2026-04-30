"""Zone builder — cluster nearby structural levels into zones.

Hierarchy weights come from two layers:
  1. Empirical weights derived from realized R on 524k+ episodes
     (`config/empirical_level_weights.yaml`, produced by
     `rl derive-hierarchy-weights`). Preferred when available.
  2. Hand-tuned fallback weights retained for safety when the YAML is
     missing or a newly-added level type isn't in it yet.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from pathlib import Path
from statistics import mean

from .config import (
    ATR_FRACTION,
    MAX_ZONE_RADIUS_TICKS,
    MIN_ZONE_RADIUS_TICKS,
    TICK_SIZE,
    LevelType,
)

log = logging.getLogger(__name__)

# --- Level families ------------------------------------------------------
# Levels inside the same family are sourced from the same signal stream
# and clustering them is partially redundant (e.g. VWAP + its σ bands all
# reference the same rolling volume anchor, so a zone that captures five
# of them is not five times stronger than a zone that captures one). The
# hierarchy_score uses the MAX weight within each family and SUMS across
# families, so cross-family confluence is what actually grows the score.
_LEVEL_FAMILY: dict[LevelType, str] = {
    # Daily volume profile
    LevelType.DAILY_POC: "daily_vp",
    LevelType.DAILY_VAH: "daily_vp",
    LevelType.DAILY_VAL: "daily_vp",
    # Weekly volume profile
    LevelType.WEEKLY_POC: "weekly_vp",
    LevelType.WEEKLY_VAH: "weekly_vp",
    LevelType.WEEKLY_VAL: "weekly_vp",
    # Monthly volume profile
    LevelType.MONTHLY_POC: "monthly_vp",
    LevelType.MONTHLY_VAH: "monthly_vp",
    LevelType.MONTHLY_VAL: "monthly_vp",
    # VWAP — one anchor, multiple σ bands
    LevelType.VWAP: "vwap",
    LevelType.VWAP_SD1: "vwap",
    LevelType.VWAP_SD2: "vwap",
    LevelType.VWAP_SD3: "vwap",
    # Prior session (PDH/PDL)
    LevelType.PDH: "prior_session",
    LevelType.PDL: "prior_session",
    # Asian/European session H-L. London H/L share the TOKYO_HIGH/LOW
    # LevelType (aliased in level_type_map) — both are session-extreme
    # references and contribute identically to the "sessions" family. Doing
    # it as an alias avoids growing LevelType, which would break the
    # observation vector shape and the trained DQN checkpoint.
    LevelType.TOKYO_HIGH: "sessions",
    LevelType.TOKYO_LOW: "sessions",
    # NY Initial Balance
    LevelType.NYIB_HIGH: "nyib",
    LevelType.NYIB_LOW: "nyib",
    # TPO profile — one builder, multiple anchors
    LevelType.TPOC: "tpo",
    LevelType.TVAH: "tpo",
    LevelType.TVAL: "tpo",
    LevelType.TIBH: "tpo",
    LevelType.TIBL: "tpo",
    # Swings per timeframe — each timeframe is its own structural read
    LevelType.DAILY_SWING_HIGH: "daily_swing",
    LevelType.DAILY_SWING_LOW: "daily_swing",
    LevelType.WEEKLY_SWING_HIGH: "weekly_swing",
    LevelType.WEEKLY_SWING_LOW: "weekly_swing",
    LevelType.MONTHLY_SWING_HIGH: "monthly_swing",
    LevelType.MONTHLY_SWING_LOW: "monthly_swing",
    # Structure
    LevelType.NAKED_POC: "naked_poc",
    # ICT / SMC price-delivery signals. Bull and bear of the same type
    # share a family — two stacked FVGs at the same price is still one
    # signal stream. But FVG and OB are separate families because they
    # detect different things (gap vs institutional candle).
    LevelType.FVG_BULL: "fvg",
    LevelType.FVG_BEAR: "fvg",
    LevelType.ORDER_BLOCK_BULL: "order_block",
    LevelType.ORDER_BLOCK_BEAR: "order_block",
}

# --- Synergy bonuses ----------------------------------------------------
# Extra strength when two families co-occur in a zone. Order matters only
# in that the key is the alphabetically-sorted pair, so we don't need to
# list both directions. Conservative defaults — the real weights should
# come from training outcomes once we have enough zone-annotated episodes.
# Examples of synergies worth rewarding:
#   daily_vp + daily_swing: volume node anchored at structural pivot
#   fvg + order_block: institutional footprint confirmed by gap
#   prior_session + vwap: PDH/PDL retested against developing VWAP band
_SYNERGY_BONUS: dict[tuple[str, str], float] = {
    ("daily_swing", "daily_vp"): 0.15,
    ("fvg", "order_block"): 0.20,
    ("prior_session", "vwap"): 0.10,
    ("daily_vp", "prior_session"): 0.10,
    ("daily_swing", "fvg"): 0.10,
    ("daily_swing", "order_block"): 0.10,
}

# Saturation constant — raw strength 1.5 maps to ~0.63 heat, 3.0 to ~0.86,
# 4.5 to ~0.95. Tuned so a single strong level (e.g. daily POC at 1.0)
# sits mid-ramp and a 3-family confluence lands firmly in the hot band.
_STRENGTH_TAU = 1.5

# Hand-tuned fallback — used only for level types missing from the empirical YAML
# or when the YAML itself can't be loaded.
_HIERARCHY_WEIGHTS: dict[LevelType, float] = {
    LevelType.DAILY_POC: 1.0,
    LevelType.WEEKLY_POC: 1.0,
    LevelType.MONTHLY_POC: 1.0,
    LevelType.NAKED_POC: 1.0,
    LevelType.DAILY_SWING_HIGH: 0.8,
    LevelType.DAILY_SWING_LOW: 0.8,
    LevelType.WEEKLY_SWING_HIGH: 0.9,
    LevelType.WEEKLY_SWING_LOW: 0.9,
    LevelType.MONTHLY_SWING_HIGH: 1.0,
    LevelType.MONTHLY_SWING_LOW: 1.0,
    LevelType.VWAP: 0.9,
    LevelType.PDH: 0.9,
    LevelType.PDL: 0.9,
    LevelType.DAILY_VAH: 0.8,
    LevelType.DAILY_VAL: 0.8,
    LevelType.TPOC: 0.8,
    LevelType.WEEKLY_VAH: 0.7,
    LevelType.WEEKLY_VAL: 0.7,
    LevelType.MONTHLY_VAH: 0.7,
    LevelType.MONTHLY_VAL: 0.7,
    LevelType.NYIB_HIGH: 0.6,
    LevelType.NYIB_LOW: 0.6,
    LevelType.TVAH: 0.6,
    LevelType.TVAL: 0.6,
    LevelType.VWAP_SD1: 0.5,
    LevelType.TOKYO_HIGH: 0.5,
    LevelType.TOKYO_LOW: 0.5,
    LevelType.TIBH: 0.5,
    LevelType.TIBL: 0.5,
    LevelType.VWAP_SD2: 0.4,
    LevelType.VWAP_SD3: 0.3,
    # ICT / SMC signals folded into the zone surface. Order blocks are the
    # institutional fingerprint before an impulsive move and get weighted
    # on par with daily structure; FVGs are weaker on their own but stack
    # nicely into confluence. These weights are intentionally conservative
    # pending an empirical update from training outcomes.
    LevelType.ORDER_BLOCK_BULL: 0.8,
    LevelType.ORDER_BLOCK_BEAR: 0.8,
    LevelType.FVG_BULL: 0.6,
    LevelType.FVG_BEAR: 0.6,
}

_DEFAULT_WEIGHT = 0.3


def _load_empirical_weights() -> dict[LevelType, float]:
    """Load empirical level weights from YAML. Returns {} if unavailable."""
    yaml_path = Path(__file__).parent / "config" / "empirical_level_weights.yaml"
    if not yaml_path.exists():
        log.info("zone_builder: no empirical weights YAML at %s, using hand-tuned fallback", yaml_path)
        return {}
    try:
        import yaml as _yaml

        data = _yaml.safe_load(yaml_path.read_text())
        raw_weights = data.get("weights", {}) if isinstance(data, dict) else {}
        out: dict[LevelType, float] = {}
        for name, w in raw_weights.items():
            try:
                out[LevelType(name)] = float(w)
            except ValueError:
                log.debug("zone_builder: unknown level type in YAML: %s", name)
        log.info(
            "zone_builder: loaded %d empirical level weights from %s (global_mean_R=%.3f, n_episodes=%s)",
            len(out),
            yaml_path,
            data.get("global_mean_R", float("nan")),
            data.get("n_episodes", "?"),
        )
        return out
    except Exception:
        log.exception("zone_builder: failed to load empirical weights; using hand-tuned fallback")
        return {}


# Empirical weights override hand-tuned at module import. The merged dict is the
# source of truth for _weight().
_EMPIRICAL_WEIGHTS: dict[LevelType, float] = _load_empirical_weights()
_MERGED_WEIGHTS: dict[LevelType, float] = {**_HIERARCHY_WEIGHTS, **_EMPIRICAL_WEIGHTS}


def _weight(lt: LevelType) -> float:
    return _MERGED_WEIGHTS.get(lt, _DEFAULT_WEIGHT)


@dataclass
class ZoneMember:
    name: str
    level_type: LevelType
    price: float


@dataclass
class Zone:
    center_price: float
    upper_bound: float
    lower_bound: float
    members: list[ZoneMember]
    composition: list[float]
    width_ticks: float
    member_count: int
    hierarchy_score: float


def _compute_radius(session_atr: float) -> float:
    """ATR-adaptive radius in price units, clamped to [min, max] ticks."""
    raw_ticks = (ATR_FRACTION * session_atr) / TICK_SIZE
    clamped_ticks = max(MIN_ZONE_RADIUS_TICKS, min(MAX_ZONE_RADIUS_TICKS, raw_ticks))
    return clamped_ticks * TICK_SIZE


def _build_composition(members: list[ZoneMember]) -> list[float]:
    """Multi-hot vector of length len(LevelType)."""
    level_types = list(LevelType)
    comp = [0.0] * len(level_types)
    type_to_idx = {lt: i for i, lt in enumerate(level_types)}
    for m in members:
        idx = type_to_idx.get(m.level_type)
        if idx is not None:
            comp[idx] = 1.0
    return comp


def _compute_strength(members: list[ZoneMember]) -> float:
    """Hierarchy score: per-family max weights summed, synergy-bonused, saturated.

    The math in three stages:
      1. Group members by family (VWAP bands are one family, daily VP is
         another, etc.). Within a family, take the single highest weight —
         this prevents redundancy (five VWAP bands at the same anchor
         shouldn't count five times).
      2. Sum the per-family weights. Raw total is monotonic in confluence.
      3. Apply pairwise synergy bonuses for co-occurring families that
         empirically reinforce each other (POC + swing, FVG + OB, …).
      4. Pass through 1 - exp(-x / tau) to saturate near 1. A single
         strong level sits around 0.5, three-family confluence nears 0.9.

    Monotonicity guarantee: adding a level never lowers the score, because
    adding a member to an existing family keeps that family's max the same
    or larger, and adding a new family strictly grows the raw sum.
    """
    if not members:
        return 0.0

    per_family_max: dict[str, float] = {}
    for m in members:
        fam = _LEVEL_FAMILY.get(m.level_type, m.level_type.value)
        w = _weight(m.level_type)
        if w > per_family_max.get(fam, 0.0):
            per_family_max[fam] = w

    raw = sum(per_family_max.values())

    # Synergy: add bonus per co-occurring family pair. Alphabetized key so
    # (a, b) and (b, a) hit the same entry.
    fams = sorted(per_family_max.keys())
    for i in range(len(fams)):
        for j in range(i + 1, len(fams)):
            raw += _SYNERGY_BONUS.get((fams[i], fams[j]), 0.0)

    return 1.0 - math.exp(-raw / _STRENGTH_TAU)


def _build_zone(members: list[ZoneMember], radius: float) -> Zone:
    prices = [m.price for m in members]
    center = mean(prices)
    lower = min(prices) - radius / 2
    upper = max(prices) + radius / 2
    width_ticks = (upper - lower) / TICK_SIZE
    composition = _build_composition(members)
    hierarchy_score = _compute_strength(members)

    return Zone(
        center_price=center,
        upper_bound=upper,
        lower_bound=lower,
        members=members,
        composition=composition,
        width_ticks=width_ticks,
        member_count=len(members),
        hierarchy_score=hierarchy_score,
    )


def build_zones(
    levels: list[tuple[str, LevelType, float]],
    session_atr: float,
) -> list[Zone]:
    """Cluster levels by greedy sequential merge with ATR-adaptive radius.

    Algorithm:
    1. Compute radius = clamp(ATR_FRACTION * session_atr, min_radius, max_radius)
    2. Sort levels by price ascending
    3. Walk sorted: if next level within radius of current zone's last member -> merge
    4. Else finalize current zone and start new one
    5. Return zones sorted by center_price ascending
    """
    if not levels:
        return []

    radius = _compute_radius(session_atr)

    sorted_levels = sorted(levels, key=lambda x: x[2])

    zones: list[Zone] = []
    current_members: list[ZoneMember] = []

    for name, level_type, price in sorted_levels:
        member = ZoneMember(name=name, level_type=level_type, price=price)
        if not current_members or abs(price - current_members[-1].price) <= radius:
            current_members.append(member)
        else:
            zones.append(_build_zone(current_members, radius))
            current_members = [member]

    if current_members:
        zones.append(_build_zone(current_members, radius))

    zones.sort(key=lambda z: z.center_price)
    return zones
