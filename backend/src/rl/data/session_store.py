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
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from .accumulators import IncrementalVolumeProfile

if TYPE_CHECKING:
    from src.market_data.levels import SwingStructure

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
    ib_high: float | None = None  # Initial balance high (09:30–10:30 ET)
    ib_low: float | None = None  # Initial balance low  (09:30–10:30 ET)


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


def vah_val_from_histogram(
    histogram: dict[float, int],
    value_area_pct: float = 0.70,
) -> tuple[float | None, float | None]:
    """Compute VAH and VAL from a volume histogram (70% value area).

    Returns (vah, val) or (None, None) if histogram is empty.
    """
    if not histogram:
        return None, None
    sorted_prices = sorted(histogram.keys())
    total_vol = sum(histogram.values())
    if total_vol <= 0:
        return None, None

    target_vol = total_vol * value_area_pct
    poc = max(histogram, key=histogram.__getitem__)
    poc_idx = sorted_prices.index(poc)

    cumulative = histogram[poc]
    lo_idx, hi_idx = poc_idx, poc_idx

    while cumulative < target_vol and (lo_idx > 0 or hi_idx < len(sorted_prices) - 1):
        expand_up = histogram[sorted_prices[hi_idx + 1]] if hi_idx < len(sorted_prices) - 1 else -1
        expand_dn = histogram[sorted_prices[lo_idx - 1]] if lo_idx > 0 else -1
        if expand_up >= expand_dn:
            hi_idx += 1
            cumulative += histogram[sorted_prices[hi_idx]]
        else:
            lo_idx -= 1
            cumulative += histogram[sorted_prices[lo_idx]]

    return sorted_prices[hi_idx], sorted_prices[lo_idx]


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


def _parse_ts(ts) -> datetime:
    """Convert a timestamp (str, datetime, or pandas Timestamp) to ET datetime."""
    if isinstance(ts, str):
        dt = datetime.fromisoformat(ts)
    elif hasattr(ts, "to_pydatetime"):
        # pandas Timestamp
        dt = ts.to_pydatetime()
    else:
        dt = ts
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ZoneInfo("UTC"))
    return dt.astimezone(_ET)


def build_session_summary(date_str: str, ticks: list[dict]) -> SessionSummary:
    """Build a SessionSummary from a list of tick dicts.

    Each tick must have keys: ts (ISO 8601 str), price (float), size (int).
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

    _IB_END = time(10, 30)  # Initial balance = 09:30–10:30 ET

    vp = IncrementalVolumeProfile(tick_size=0.25)
    rth_high: float | None = None
    rth_low: float | None = None
    eth_high: float | None = None
    eth_low: float | None = None
    ib_high: float | None = None
    ib_low: float | None = None

    for tick in ticks:
        price: float = float(tick["price"])
        size: int = int(tick["size"])
        dt = _parse_ts(tick["ts"])
        t = dt.time()

        is_rth = _RTH_START <= t < _RTH_END
        is_ib = _RTH_START <= t < _IB_END

        vp.update(price, size)

        if is_rth:
            rth_high = price if rth_high is None else max(rth_high, price)
            rth_low = price if rth_low is None else min(rth_low, price)
        else:
            eth_high = price if eth_high is None else max(eth_high, price)
            eth_low = price if eth_low is None else min(eth_low, price)

        if is_ib:
            ib_high = price if ib_high is None else max(ib_high, price)
            ib_low = price if ib_low is None else min(ib_low, price)

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
            ib_high=ib_high,
            ib_low=ib_low,
        )

    # Build canonical histogram from the internal accumulator state
    histogram: dict[str, int] = {f"{price:.2f}": vol for price, vol in vp._histogram.items()}

    # Filter single prints into zones
    single_print_zones = filter_single_print_zones(profile.single_prints, tick_size=0.25, min_consecutive=3)

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
        ib_high=ib_high,
        ib_low=ib_low,
    )


# ---------------------------------------------------------------------------
# Task 5: JSON I/O + compute_precomputed_levels
# ---------------------------------------------------------------------------


def save_summaries(summaries: dict[str, SessionSummary], path: Path) -> None:
    """Write dict[str, SessionSummary] to JSON file.

    single_print_zones tuples are serialized as lists.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    data: dict[str, dict] = {}
    for date, s in summaries.items():
        data[date] = {
            "date": s.date,
            "poc": s.poc,
            "vah": s.vah,
            "val": s.val,
            "histogram": s.histogram,
            "rth_high": s.rth_high,
            "rth_low": s.rth_low,
            "eth_high": s.eth_high,
            "eth_low": s.eth_low,
            "single_print_zones": [list(z) for z in s.single_print_zones],
        }

    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def load_summaries(path: Path) -> dict[str, SessionSummary]:
    """Load dict[str, SessionSummary] from JSON file.

    Returns empty dict if file not found.
    """
    path = Path(path)
    if not path.exists():
        return {}

    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)

    result: dict[str, SessionSummary] = {}
    for date, d in data.items():
        result[date] = SessionSummary(
            date=d["date"],
            poc=d["poc"],
            vah=d["vah"],
            val=d["val"],
            histogram=d["histogram"],
            rth_high=d.get("rth_high"),
            rth_low=d.get("rth_low"),
            eth_high=d.get("eth_high"),
            eth_low=d.get("eth_low"),
            single_print_zones=[tuple(z) for z in d.get("single_print_zones", [])],
        )
    return result


def _composite_poc(summaries: dict[str, SessionSummary], dates: list[str]) -> float | None:
    """Return POC from composite histogram of the given session dates."""
    selected = [summaries[d] for d in dates if d in summaries]
    if not selected:
        return None
    histo = composite_histogram(selected)
    return poc_from_histogram(histo)


def _composite_vah_val(summaries: dict[str, SessionSummary], dates: list[str]) -> tuple[float | None, float | None]:
    """Return VAH, VAL from composite histogram of the given session dates."""
    selected = [summaries[d] for d in dates if d in summaries]
    if not selected:
        return None, None
    histo = composite_histogram(selected)
    return vah_val_from_histogram(histo)


def _compute_swing_from_summaries(
    summaries: dict[str, SessionSummary],
    current_date: str,
) -> SwingStructure | None:
    """Build SwingStructure from session summaries for backtesting.

    Converts each prior session into a daily candle (using rth_high/rth_low/poc),
    then runs compute_multi_tf_swings. Weekly/monthly candles are aggregated from
    the daily candles by the same function.
    """
    from datetime import timezone
    from zoneinfo import ZoneInfo

    from src.market_data.levels import compute_multi_tf_swings

    CET = ZoneInfo("Europe/Stockholm")
    prior_dates = sorted(d for d in summaries if d < current_date)
    if not prior_dates:
        return None

    # Build synthetic 1m-like bars from session summaries.
    # Each session → one bar at 12:00 CET with OHLC from rth data.
    synth_bars: list[dict] = []
    for d in prior_dates:
        s = summaries[d]
        if s.rth_high is None or s.rth_low is None:
            continue
        dt = datetime.strptime(d, "%Y-%m-%d")
        ts = dt.replace(hour=12, tzinfo=CET).astimezone(timezone.utc)
        synth_bars.append(
            {
                "ts": ts,
                "open": s.poc,
                "high": s.rth_high,
                "low": s.rth_low,
                "close": s.poc,
            }
        )

    if len(synth_bars) < 7:  # need at least 2*lookback+1 for daily (lookback=3)
        return None

    # aggregate_to_timeframe expects 1m bars, but since we have exactly 1 bar
    # per day (at 12:00 CET), daily aggregation gives 1 candle per date.
    # For weekly/monthly, bars from multiple days merge correctly.
    return compute_multi_tf_swings(synth_bars)


def compute_precomputed_levels(
    summaries: dict[str, SessionSummary],
    current_date: str,
) -> dict:
    """Compute cross-session levels for injection into the replay engine.

    Returns
    -------
    Dict with keys:
      naked_pocs, poc_daily, daily_vah, daily_val,
      poc_weekly, weekly_vah, weekly_val,
      poc_monthly, monthly_vah, monthly_val, poc_macro,
      globex_high, globex_low, overnight_high, overnight_low, single_print_zones
    """
    # All prior sessions (before current_date), sorted ascending
    prior_dates = sorted(d for d in summaries if d < current_date)

    # --- poc_daily + VAH/VAL: previous session's POC/VAH/VAL ---
    poc_daily: float | None = None
    daily_vah: float | None = None
    daily_val: float | None = None
    if prior_dates:
        prev = summaries[prior_dates[-1]]
        poc_daily = prev.poc
        daily_vah = prev.vah
        daily_val = prev.val

    # --- poc_weekly + VAH/VAL: composite from last 5 prior sessions (require >= 3) ---
    poc_weekly: float | None = None
    weekly_vah: float | None = None
    weekly_val: float | None = None
    weekly_dates = prior_dates[-5:]
    if len(weekly_dates) >= 3:
        poc_weekly = _composite_poc(summaries, weekly_dates)
        weekly_vah, weekly_val = _composite_vah_val(summaries, weekly_dates)

    # --- poc_monthly + VAH/VAL: composite from last 20 prior sessions (require >= 10) ---
    poc_monthly: float | None = None
    monthly_vah: float | None = None
    monthly_val: float | None = None
    monthly_dates = prior_dates[-20:]
    if len(monthly_dates) >= 10:
        poc_monthly = _composite_poc(summaries, monthly_dates)
        monthly_vah, monthly_val = _composite_vah_val(summaries, monthly_dates)

    # --- poc_macro: composite from all prior (require >= 10) ---
    poc_macro: float | None = None
    if len(prior_dates) >= 10:
        poc_macro = _composite_poc(summaries, prior_dates)

    # --- Globex / overnight HL from current session's ETH range ---
    current = summaries.get(current_date)
    globex_high: float | None = current.eth_high if current else None
    globex_low: float | None = current.eth_low if current else None

    # --- Naked POCs ---
    naked_pocs = find_naked_pocs(summaries, current_date)

    # --- Single print zones: union from all prior sessions ---
    all_spz: list[tuple[float, float]] = []
    for d in prior_dates:
        all_spz.extend(summaries[d].single_print_zones)

    # --- Multi-timeframe swing structure ---
    swing_structure = _compute_swing_from_summaries(summaries, current_date)

    # --- IB range percentile: current session vs prior 30 sessions ---
    # Ranks how large/small today's IB is compared to recent history.
    # Requires ib_high/ib_low on SessionSummary (added 2026-04-13).
    # Falls back to 0.5 for old summaries that pre-date the field.
    ib_lookup_dates = prior_dates[-30:]
    prior_ib_ranges = [
        summaries[d].ib_high - summaries[d].ib_low
        for d in ib_lookup_dates
        if summaries[d].ib_high is not None and summaries[d].ib_low is not None
    ]
    if len(prior_ib_ranges) >= 5:
        # Will be filled in at replay time once today's IB forms; store the
        # sorted reference distribution so replay_engine can do the lookup.
        prior_ib_ranges_sorted = sorted(prior_ib_ranges)
    else:
        prior_ib_ranges_sorted = []

    # --- Prior VAs: last 3 sessions' VAH/VAL for composite_va_overlap ---
    prior_vas: list[tuple[float, float]] = []
    for d in prior_dates[-3:]:
        s = summaries[d]
        if s.vah and s.val and s.vah > s.val:
            prior_vas.append((s.vah, s.val))

    return {
        "naked_pocs": naked_pocs,
        "poc_daily": poc_daily,
        "daily_vah": daily_vah,
        "daily_val": daily_val,
        "poc_weekly": poc_weekly,
        "poc_monthly": poc_monthly,
        "poc_macro": poc_macro,
        "weekly_vah": weekly_vah,
        "weekly_val": weekly_val,
        "monthly_vah": monthly_vah,
        "monthly_val": monthly_val,
        "globex_high": globex_high,
        "globex_low": globex_low,
        "overnight_high": globex_high,  # alias for NQ
        "overnight_low": globex_low,
        "single_print_zones": all_spz,
        "swing_structure": swing_structure,
        "prior_ib_ranges_sorted": prior_ib_ranges_sorted,  # for ib_range_percentile
        "prior_vas": prior_vas,  # for composite_va_overlap
    }
