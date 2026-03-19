"""Session summary store for RL trading system.

Precomputes per-session data (volume profile, RTH/ETH ranges, single prints)
so cross-session levels (naked POCs, composite multi-TF POCs, Globex HL) can
be injected into the replay engine.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

_ET = ZoneInfo("America/New_York")
_RTH_START = time(9, 30)
_RTH_END = time(16, 0)


# ---------------------------------------------------------------------------
# Dataclass
# ---------------------------------------------------------------------------

@dataclass
class SessionSummary:
    date: str
    poc: float
    vah: float
    val: float
    histogram: dict[str, int]
    rth_high: float | None = None
    rth_low: float | None = None
    eth_high: float | None = None
    eth_low: float | None = None
    single_print_zones: list[tuple[float, float]] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Task 1: filter_single_print_zones
# ---------------------------------------------------------------------------

def filter_single_print_zones(
    single_prints: list[tuple[float, float]],
    tick_size: float = 0.25,
    min_consecutive: int = 3,
) -> list[tuple[float, float]]:
    """Group consecutive single-print prices into zones.

    Parameters
    ----------
    single_prints:
        List of (price, price) tuples from VP single-print detection.
    tick_size:
        Minimum gap between consecutive ticks on the grid.
    min_consecutive:
        Minimum number of consecutive levels required to form a zone.

    Returns
    -------
    List of (zone_low, zone_high) tuples for zones with >= min_consecutive levels.
    """
    if not single_prints:
        return []

    # Extract unique prices (use the first element of each tuple as the price)
    prices = sorted({p[0] for p in single_prints})

    # Group into consecutive runs (adjacent within 1 tick)
    groups: list[list[float]] = []
    current_group: list[float] = [prices[0]]

    for price in prices[1:]:
        if price - current_group[-1] <= tick_size + 1e-9:
            current_group.append(price)
        else:
            groups.append(current_group)
            current_group = [price]
    groups.append(current_group)

    # Filter and return zones with enough consecutive levels
    zones: list[tuple[float, float]] = []
    for group in groups:
        if len(group) >= min_consecutive:
            zones.append((group[0], group[-1]))

    return zones


# ---------------------------------------------------------------------------
# Task 2: composite_histogram + poc_from_histogram
# ---------------------------------------------------------------------------

def composite_histogram(summaries: list[SessionSummary]) -> dict[float, int]:
    """Merge volume histograms from multiple SessionSummaries by adding bucket volumes.

    Histogram keys are strings like "100.00"; they are parsed to float and
    re-snapped to a 0.25 tick grid before merging.
    """
    merged: dict[float, int] = {}
    tick = 0.25
    for s in summaries:
        for key, vol in s.histogram.items():
            price = float(key)
            snapped = round(price / tick) * tick
            merged[snapped] = merged.get(snapped, 0) + vol
    return merged


def poc_from_histogram(histogram: dict[float, int]) -> float | None:
    """Return the price with the highest volume, or None if the histogram is empty."""
    if not histogram:
        return None
    return max(histogram, key=histogram.__getitem__)
