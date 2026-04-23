"""Level type encoding and confluence feature extraction."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..config import TICK_SIZE, LevelType

if TYPE_CHECKING:
    from ..zone_builder import Zone


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

    levels_within = sum(1 for p in all_levels if p != touched_price and abs(p - touched_price) <= window)

    # Nearest higher / lower
    higher = [p for p in all_levels if p > touched_price + tick_size * 0.5]
    lower = [p for p in all_levels if p < touched_price - tick_size * 0.5]

    if higher:
        nearest_higher_dist = min(abs(p - touched_price) / tick_size for p in higher)
    else:
        nearest_higher_dist = 50.0  # far away default

    nearest_lower_dist = min(abs(p - touched_price) / tick_size for p in lower) if lower else 50.0

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
    for fvg in fvgs or []:
        lo = getattr(fvg, "price_low", 0.0)
        hi = getattr(fvg, "price_high", 0.0)
        if lo <= touched_price <= hi:
            fvg_overlap = 1.0
            fvg_width_ticks = max(fvg_width_ticks, (hi - lo) / tick_size)

    # Single print zone overlap
    sp_overlap = 0.0
    for sp in single_print_zones or []:
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


# ---------------------------------------------------------------------------
# Zone-based encodings (used in zone-consolidated observation mode)
# ---------------------------------------------------------------------------


def encode_zone_composition(zone: Zone) -> list[float]:
    """Return the multi-hot composition vector for a zone.

    Length equals ``len(LevelType)`` — one slot per level type.
    """
    return zone.composition


def _compute_session_relevance(level_type: LevelType, session_context: dict | None) -> float:
    """How relevant is this level type to the current session? 0=stale, 1=active."""
    if session_context is None:
        return 0.5  # unknown

    session_type = session_context.get("session_type", "rth")
    minutes_since_rth = session_context.get("minutes_since_rth", 60.0)

    # Tokyo levels: active during London (which includes Tokyo overlap), stale during RTH
    if level_type in (LevelType.TOKYO_HIGH, LevelType.TOKYO_LOW):
        if session_type == "london":
            return 1.0  # active session level
        elif session_type == "globex":
            return 0.8  # recent, still relevant
        else:
            return 0.3  # prior session level during RTH

    # NY IB levels: relevance depends on whether IB is still forming
    if level_type in (LevelType.NYIB_HIGH, LevelType.NYIB_LOW, LevelType.TIBH, LevelType.TIBL):
        if session_type != "rth":
            return 0.2  # IB not relevant outside RTH
        if minutes_since_rth < 30:
            return 0.5  # IB still forming — level is unstable
        else:
            return 1.0  # IB locked — meaningful level

    # Prior day levels: most relevant during RTH
    if level_type in (LevelType.PDH, LevelType.PDL):
        if session_type == "rth":
            return 0.9  # yesterday's key level in today's session
        else:
            return 0.5  # less relevant outside RTH

    # VWAP levels: only computed during RTH, always active
    if level_type in (LevelType.VWAP, LevelType.VWAP_SD1, LevelType.VWAP_SD2, LevelType.VWAP_SD3):
        if session_type == "rth":
            return 1.0  # active RTH VWAP
        else:
            return 0.3  # prior day's VWAP

    # TPO levels (TPOC, TVAH, TVAL): active during RTH, stale outside
    if level_type in (LevelType.TPOC, LevelType.TVAH, LevelType.TVAL):
        if session_type == "rth":
            return 1.0
        else:
            return 0.4

    # Volume profile (daily/weekly/monthly POC/VAH/VAL): always somewhat relevant
    # Naked POC: always relevant (historical)
    return 0.6  # default for POC/VAH/VAL levels


def encode_zone_features(zone: Zone, session_context: dict | None = None) -> list[float]:
    """Return 4 normalised scalar features for a zone.

    Features (all bounded 0-1):
        - width_norm:        ``min(zone.width_ticks / 50, 1)``
        - count_norm:        ``min(zone.member_count / 10, 1)``
        - hierarchy:         ``zone.hierarchy_score`` (already 0-1)
        - session_relevance: max relevance of member levels to current session
    """
    relevance = max(
        (_compute_session_relevance(m.level_type, session_context) for m in zone.members),
        default=0.5,
    )
    return [
        min(zone.width_ticks / 50.0, 1.0),
        min(zone.member_count / 10.0, 1.0),
        zone.hierarchy_score,
        relevance,
    ]


def encode_zone_confluence(
    zone: Zone,
    all_zones: list[Zone],
    fvgs: list | None = None,
    single_print_zones: list | None = None,
) -> list[float]:
    """Return 5 confluence features for a zone relative to its neighbourhood.

    Features:
        - nearest_higher_zone_dist: normalised ticks to nearest zone center above
        - nearest_lower_zone_dist:  normalised ticks to nearest zone center below
        - fvg_overlap:              1.0 if any FVG contains zone.center_price
        - fvg_width_ticks:          width of overlapping FVG (normalised, capped 1.0)
        - single_print_overlap:     1.0 if any single-print zone contains center_price
    """
    center = zone.center_price

    # Nearest higher / lower zone centers
    higher = [z.center_price for z in all_zones if z.center_price > center + TICK_SIZE * 0.5]
    lower = [z.center_price for z in all_zones if z.center_price < center - TICK_SIZE * 0.5]

    if higher:
        nearest_higher = min(abs(p - center) / TICK_SIZE / 50.0 for p in higher)
        nearest_higher = min(nearest_higher, 1.0)
    else:
        nearest_higher = 1.0

    if lower:
        nearest_lower = min(abs(p - center) / TICK_SIZE / 50.0 for p in lower)
        nearest_lower = min(nearest_lower, 1.0)
    else:
        nearest_lower = 1.0

    # FVG overlap
    fvg_overlap = 0.0
    fvg_width = 0.0
    for fvg in fvgs or []:
        lo = getattr(fvg, "price_low", 0.0)
        hi = getattr(fvg, "price_high", 0.0)
        if lo <= center <= hi:
            fvg_overlap = 1.0
            fvg_width = max(fvg_width, (hi - lo) / TICK_SIZE)

    fvg_width_norm = min(fvg_width / 20.0, 1.0)

    # Single print zone overlap
    sp_overlap = 0.0
    for sp in single_print_zones or []:
        if sp[0] <= center <= sp[1]:
            sp_overlap = 1.0
            break

    return [nearest_higher, nearest_lower, fvg_overlap, fvg_width_norm, sp_overlap]
