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

from .accumulators import IncrementalVolumeProfile

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


# ---------------------------------------------------------------------------
# Task 3: find_naked_pocs
# ---------------------------------------------------------------------------

def find_naked_pocs(
    summaries: dict[str, SessionSummary],
    current_date: str,
    max_lookback_sessions: int = 20,
) -> list[dict]:
    """Find POCs that have not been touched by any subsequent session's RTH range.

    Parameters
    ----------
    summaries:
        Dict keyed by date string (YYYY-MM-DD).
    current_date:
        The current session date (exclusive upper bound).
    max_lookback_sessions:
        How many prior sessions to consider.

    Returns
    -------
    List of {"date": str, "price": float} dicts for each naked POC.
    """
    if not summaries:
        return []

    # All prior sessions, sorted ascending
    prior_dates = sorted(d for d in summaries if d < current_date)
    # Respect max_lookback_sessions
    prior_dates = prior_dates[-max_lookback_sessions:]

    naked: list[dict] = []
    for date in prior_dates:
        poc = summaries[date].poc
        is_naked = True
        # Check all sessions after this one (but still before current_date)
        for later_date in prior_dates:
            if later_date <= date:
                continue
            later = summaries[later_date]
            if later.rth_high is None or later.rth_low is None:
                continue
            if later.rth_low <= poc <= later.rth_high:
                is_naked = False
                break
        if is_naked:
            naked.append({"date": date, "price": poc})

    return naked


# ---------------------------------------------------------------------------
# Task 4: build_session_summary
# ---------------------------------------------------------------------------

def _parse_ts(ts: str) -> datetime:
    """Parse an ISO 8601 timestamp to a timezone-aware datetime in ET."""
    dt = datetime.fromisoformat(ts)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ZoneInfo("UTC"))
    return dt.astimezone(_ET)


def build_session_summary(date_str: str, ticks: list[dict]) -> SessionSummary:
    """Build a SessionSummary from a list of tick dicts.

    Each tick must have keys: ts_event (ISO 8601 str), price (float), size (int).
    RTH = 09:30-16:00 ET; everything else is ETH.
    """
    if not ticks:
        return SessionSummary(
            date=date_str,
            poc=0.0,
            vah=0.0,
            val=0.0,
            histogram={},
        )

    vp = IncrementalVolumeProfile(tick_size=0.25)
    rth_high: float | None = None
    rth_low: float | None = None
    eth_high: float | None = None
    eth_low: float | None = None

    for tick in ticks:
        price: float = float(tick["price"])
        size: int = int(tick["size"])
        dt = _parse_ts(tick["ts_event"])
        t = dt.time()

        is_rth = _RTH_START <= t < _RTH_END

        vp.update(price, size)

        if is_rth:
            rth_high = price if rth_high is None else max(rth_high, price)
            rth_low = price if rth_low is None else min(rth_low, price)
        else:
            eth_high = price if eth_high is None else max(eth_high, price)
            eth_low = price if eth_low is None else min(eth_low, price)

    profile = vp.get()
    if profile is None:
        return SessionSummary(
            date=date_str,
            poc=0.0,
            vah=0.0,
            val=0.0,
            histogram={},
            rth_high=rth_high,
            rth_low=rth_low,
            eth_high=eth_high,
            eth_low=eth_low,
        )

    # Build canonical histogram from the internal accumulator state
    histogram: dict[str, int] = {
        f"{price:.2f}": vol
        for price, vol in vp._histogram.items()
    }

    # Filter single prints into zones
    single_print_zones = filter_single_print_zones(
        profile.single_prints, tick_size=0.25, min_consecutive=3
    )

    return SessionSummary(
        date=date_str,
        poc=profile.poc,
        vah=profile.vah,
        val=profile.val,
        histogram=histogram,
        rth_high=rth_high,
        rth_low=rth_low,
        eth_high=eth_high,
        eth_low=eth_low,
        single_print_zones=single_print_zones,
    )
