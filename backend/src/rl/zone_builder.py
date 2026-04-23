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


def _build_zone(members: list[ZoneMember], radius: float) -> Zone:
    prices = [m.price for m in members]
    center = mean(prices)
    lower = min(prices) - radius / 2
    upper = max(prices) + radius / 2
    width_ticks = (upper - lower) / TICK_SIZE
    composition = _build_composition(members)

    # Mean weight across the members — bounded to [0, ~max_empirical_weight].
    # Empirical weights can exceed 1.0 (the strongest level type today is
    # weekly_swing_low ≈ 1.14) so we clip to a generous ceiling to keep the
    # feature on roughly [0, 1] for downstream normalizers/models.
    mean_weight = sum(_weight(m.level_type) for m in members) / len(members) if members else 0.0
    hierarchy_score = min(max(mean_weight / 1.2, 0.0), 1.0)

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
