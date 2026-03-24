"""Level type encoding and confluence feature extraction."""
from __future__ import annotations

from ..config import LevelType, TICK_SIZE


def encode_level_type(level_type: LevelType) -> list[float]:
    """One-hot encode a LevelType.

    Returns a list of len(LevelType) floats with exactly one 1.0.
    """
    members = list(LevelType)
    return [1.0 if m == level_type else 0.0 for m in members]


def encode_confluence(
    touched_price: float,
    all_levels: list[float],
    tick_size: float = TICK_SIZE,
    proximity_ticks: int = 5,
    fvgs: list | None = None,
    single_print_zones: list | None = None,
) -> dict:
    """Count and characterise nearby levels around a touched price.

    Args:
        touched_price: The price that was touched.
        all_levels: All active level prices in the market.
        tick_size: Minimum price increment.
        proximity_ticks: Number of ticks to use as the proximity window.
        fvgs: List of FVG objects with .price_low / .price_high attributes.
        single_print_zones: List of (low, high) tuples for single print zones.

    Returns dict with 8 features:
        levels_within_5_ticks:      count of levels within proximity window
        strongest_cluster_score:    proximity_ticks - min_distance (0-5 scale)
        nearest_higher_level_dist:  ticks to closest level above (capped 0-50)
        nearest_lower_level_dist:   ticks to closest level below (capped 0-50)
        touched_level_hierarchy_rank: rank (0-1) of touched price by proximity
        fvg_overlap:                1.0 if an FVG overlaps touched price, else 0.0
        fvg_width_ticks:            width of overlapping FVG in ticks (capped 0-20), normalised
        single_print_overlap:       1.0 if a single print zone overlaps, else 0.0
    """
    window = proximity_ticks * tick_size

    levels_within = sum(
        1 for p in all_levels
        if p != touched_price and abs(p - touched_price) <= window
    )

    # Nearest higher / lower
    higher = [p for p in all_levels if p > touched_price + tick_size * 0.5]
    lower  = [p for p in all_levels if p < touched_price - tick_size * 0.5]

    if higher:
        nearest_higher_dist = min(abs(p - touched_price) / tick_size for p in higher)
    else:
        nearest_higher_dist = 50.0  # far away default

    if lower:
        nearest_lower_dist = min(abs(p - touched_price) / tick_size for p in lower)
    else:
        nearest_lower_dist = 50.0

    # Cluster score: how close the nearest neighbour is within the window
    all_others = [p for p in all_levels if p != touched_price]
    if all_others:
        min_dist_ticks = min(abs(p - touched_price) / tick_size for p in all_others)
        strongest_cluster_score = max(0.0, proximity_ticks - min_dist_ticks) / proximity_ticks
    else:
        strongest_cluster_score = 0.0

    # Hierarchy rank: fraction of all_levels that are further from touched_price
    # than touched_price's nearest neighbours — i.e. how "central" it is.
    if len(all_levels) > 1:
        dists = sorted(abs(p - touched_price) for p in all_levels if p != touched_price)
        # rank is how close touched_price is to the median (0 = isolated, 1 = at cluster)
        median_dist = dists[len(dists) // 2]
        hierarchy_rank = 1.0 - min(1.0, dists[0] / max(median_dist, tick_size))
    else:
        hierarchy_rank = 0.5

    # FVG overlap: does an FVG zone contain the touched price?
    fvg_overlap = 0.0
    fvg_width_ticks = 0.0
    for fvg in (fvgs or []):
        lo = getattr(fvg, "price_low", 0.0)
        hi = getattr(fvg, "price_high", 0.0)
        if lo <= touched_price <= hi:
            fvg_overlap = 1.0
            fvg_width_ticks = max(fvg_width_ticks, (hi - lo) / tick_size)

    # Single print zone overlap
    sp_overlap = 0.0
    for sp in (single_print_zones or []):
        sp_lo, sp_hi = sp[0], sp[1]
        if sp_lo <= touched_price <= sp_hi:
            sp_overlap = 1.0
            break

    return {
        "levels_within_5_ticks": float(levels_within),
        "strongest_cluster_score": round(strongest_cluster_score, 4),
        "nearest_higher_level_dist": min(50.0, round(nearest_higher_dist, 2)),
        "nearest_lower_level_dist": min(50.0, round(nearest_lower_dist, 2)),
        "touched_level_hierarchy_rank": round(hierarchy_rank, 4),
        "fvg_overlap": fvg_overlap,
        "fvg_width_ticks": min(fvg_width_ticks / 20.0, 1.0),
        "single_print_overlap": sp_overlap,
    }
