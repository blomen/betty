"""Auction Market Theory analysis engine.

Computes volume profile, VWAP bands, initial balance, delta analysis,
market type classification, and opening type from bar/tick data.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from .base import BarData, TickData

_ET = ZoneInfo("US/Eastern")


def _to_et_time(ts: datetime) -> time:
    """Convert a datetime to US/Eastern time-of-day (handles DST correctly)."""
    if ts.tzinfo is not None:
        return ts.astimezone(_ET).time()
    return ts.replace(tzinfo=timezone.utc).astimezone(_ET).time()


logger = logging.getLogger(__name__)


# ============ Data Classes ============


@dataclass
class VolumeProfile:
    """Volume-at-price profile with value area."""

    poc: float  # Point of Control (highest volume price)
    vah: float  # Value Area High (top of 70% volume zone)
    val: float  # Value Area Low (bottom of 70% volume zone)
    profile: dict[float, int] = field(default_factory=dict)  # price → volume


@dataclass
class VWAPBands:
    """VWAP with standard deviation bands."""

    vwap: float
    upper_1sd: float
    lower_1sd: float
    upper_2sd: float
    lower_2sd: float
    upper_3sd: float
    lower_3sd: float


@dataclass
class InitialBalance:
    """Initial balance (first 60 min of RTH)."""

    ib_high: float
    ib_low: float
    ib_range: float


@dataclass
class DeltaAnalysis:
    """Delta (buy-sell volume) analysis."""

    bar_deltas: list[int] = field(default_factory=list)
    cumulative_delta: list[int] = field(default_factory=list)
    total_delta: int = 0
    delta_divergence: bool = False  # Price up + delta down or vice versa


@dataclass
class TPOProfile:
    """Time Price Opportunity (Market Profile) chart data.

    Each 30-min period gets a letter (A-Z). TPO count per price level
    gives time-at-price distribution (vs volume-at-price in VolumeProfile).
    """

    tpo_poc: float  # Price with most TPO prints
    tpo_vah: float  # TPO-based Value Area High
    tpo_val: float  # TPO-based Value Area Low
    tpo_count: dict[float, int] = field(default_factory=dict)  # price → TPO count
    period_letters: dict[float, set[str]] = field(default_factory=dict)  # price → set of letters
    distribution_type: str = "normal"  # "normal", "double", "p_shape", "b_shape"


@dataclass
class MacroSnapshot:
    """Macro market data for regime classification."""

    vix: float | None = None
    vix_change_pct: float | None = None  # Day-over-day change
    dxy: float | None = None
    dxy_change_pct: float | None = None
    us10y: float | None = None
    us10y_change_bps: float | None = None  # Basis points change
    us2y: float | None = None
    yield_curve_spread: float | None = None  # 10y - 2y
    regime: str = "unknown"  # "risk_on", "risk_off", "mixed"
    regime_score: float = 0.0  # -1.0 (max risk-off) to +1.0 (max risk-on)
    fetched_at: str | None = None
    # Phase 2 fields — None until external data sources added
    gex: float | None = None
    put_call_ratio: float | None = None
    es_nq_ratio_change: float | None = None


@dataclass
class SessionAnalysis:
    """Master analysis result for a trading session."""

    date: str
    symbol: str

    # Levels
    volume_profile: VolumeProfile | None = None
    tpo_profile: TPOProfile | None = None
    vwap_bands: VWAPBands | None = None
    initial_balance: InitialBalance | None = None

    # Macro
    macro: MacroSnapshot | None = None

    # Overnight
    overnight_high: float | None = None
    overnight_low: float | None = None

    # Previous day
    prev_poc: float | None = None
    prev_vah: float | None = None
    prev_val: float | None = None

    # Delta
    delta: DeltaAnalysis | None = None

    # Classifications
    market_type: str = "unknown"  # "balanced", "trending_up", "trending_down"
    opening_type: str = "unknown"  # "OD", "OTD", "ORR", "OA"
    poor_high: bool = False
    poor_low: bool = False
    single_prints: list[tuple[float, float]] = field(default_factory=list)

    # Current state
    last_price: float | None = None
    price_vs_va: str = "unknown"  # "above", "within", "below"
    price_vs_vwap: str = "unknown"  # "above_3sd", "above_2sd", "above_1sd", "at_vwap", "below_1sd", etc.
    price_vs_ib: str = "unknown"  # "above", "within", "below"

    def to_dict(self) -> dict:
        """Serialize for JSON storage and API response."""
        result = {
            "date": self.date,
            "symbol": self.symbol,
            "market_type": self.market_type,
            "opening_type": self.opening_type,
            "poor_high": self.poor_high,
            "poor_low": self.poor_low,
            "single_prints": self.single_prints,
            "last_price": self.last_price,
            "price_vs_va": self.price_vs_va,
            "price_vs_vwap": self.price_vs_vwap,
            "price_vs_ib": self.price_vs_ib,
            "overnight_high": self.overnight_high,
            "overnight_low": self.overnight_low,
            "prev_poc": self.prev_poc,
            "prev_vah": self.prev_vah,
            "prev_val": self.prev_val,
        }
        if self.volume_profile:
            result["poc"] = self.volume_profile.poc
            result["vah"] = self.volume_profile.vah
            result["val"] = self.volume_profile.val
        if self.tpo_profile:
            result["tpo_poc"] = self.tpo_profile.tpo_poc
            result["tpo_vah"] = self.tpo_profile.tpo_vah
            result["tpo_val"] = self.tpo_profile.tpo_val
            result["distribution_type"] = self.tpo_profile.distribution_type
        if self.vwap_bands:
            result["vwap"] = self.vwap_bands.vwap
            result["vwap_1sd_upper"] = self.vwap_bands.upper_1sd
            result["vwap_1sd_lower"] = self.vwap_bands.lower_1sd
            result["vwap_2sd_upper"] = self.vwap_bands.upper_2sd
            result["vwap_2sd_lower"] = self.vwap_bands.lower_2sd
            result["vwap_3sd_upper"] = self.vwap_bands.upper_3sd
            result["vwap_3sd_lower"] = self.vwap_bands.lower_3sd
        if self.initial_balance:
            result["ib_high"] = self.initial_balance.ib_high
            result["ib_low"] = self.initial_balance.ib_low
            result["ib_range"] = self.initial_balance.ib_range
        if self.delta:
            result["total_delta"] = self.delta.total_delta
            result["delta_divergence"] = self.delta.delta_divergence
            result["cumulative_delta_last"] = self.delta.cumulative_delta[-1] if self.delta.cumulative_delta else 0
        if self.macro:
            result["macro"] = {
                "vix": self.macro.vix,
                "vix_change_pct": self.macro.vix_change_pct,
                "dxy": self.macro.dxy,
                "dxy_change_pct": self.macro.dxy_change_pct,
                "us10y": self.macro.us10y,
                "us10y_change_bps": self.macro.us10y_change_bps,
                "yield_curve_spread": self.macro.yield_curve_spread,
                "regime": self.macro.regime,
                "regime_score": self.macro.regime_score,
            }
        return result


# ============ Core Computations ============


def compute_volume_profile(bars: list[BarData], tick_size: float = 0.25) -> VolumeProfile:
    """Build volume-at-price profile and compute POC/VAH/VAL.

    Uses the 70% value area rule: starting from POC, expand outward
    until 70% of total volume is captured.
    """
    if not bars:
        return VolumeProfile(poc=0, vah=0, val=0)

    # Build price→volume map using bar typical price bucketed to tick_size
    profile: dict[float, int] = {}
    for bar in bars:
        # Distribute bar volume across the bar's price range
        low_tick = round(bar.low / tick_size) * tick_size
        high_tick = round(bar.high / tick_size) * tick_size
        price_levels = np.arange(low_tick, high_tick + tick_size / 2, tick_size)

        if len(price_levels) == 0:
            tp = round(((bar.high + bar.low + bar.close) / 3) / tick_size) * tick_size
            profile[tp] = profile.get(tp, 0) + bar.volume
        else:
            vol_per_level = bar.volume // len(price_levels)
            remainder = bar.volume % len(price_levels)
            # Distribute remainder near close (matches levels.py behavior)
            close_snap = round(bar.close / tick_size) * tick_size
            sorted_by_close = sorted(
                range(len(price_levels)), key=lambda i: abs(round(price_levels[i], 2) - close_snap)
            )
            extras = set(sorted_by_close[:remainder])
            for i, price in enumerate(price_levels):
                p = round(price, 2)
                profile[p] = profile.get(p, 0) + vol_per_level + (1 if i in extras else 0)

    if not profile:
        return VolumeProfile(poc=0, vah=0, val=0)

    # POC = price with highest volume
    poc = max(profile, key=profile.get)
    total_vol = sum(profile.values())
    target_vol = total_vol * 0.70

    # Expand from POC outward
    sorted_prices = sorted(profile.keys())
    poc_idx = sorted_prices.index(poc)
    included_vol = profile[poc]
    lo_idx = poc_idx
    hi_idx = poc_idx

    while included_vol < target_vol and (lo_idx > 0 or hi_idx < len(sorted_prices) - 1):
        lo_vol = profile.get(sorted_prices[lo_idx - 1], 0) if lo_idx > 0 else 0
        hi_vol = profile.get(sorted_prices[hi_idx + 1], 0) if hi_idx < len(sorted_prices) - 1 else 0

        # Expand toward higher volume; on tie, expand UP first (industry standard)
        if hi_vol >= lo_vol and hi_idx < len(sorted_prices) - 1:
            hi_idx += 1
            included_vol += hi_vol
        elif lo_idx > 0:
            lo_idx -= 1
            included_vol += lo_vol
        else:
            hi_idx += 1
            included_vol += hi_vol

    val = sorted_prices[lo_idx]
    vah = sorted_prices[hi_idx]

    return VolumeProfile(poc=poc, vah=vah, val=val, profile=profile)


def compute_vwap_bands(bars: list[BarData]) -> VWAPBands:
    """Compute VWAP with 1/2/3 standard deviation bands.

    VWAP = cumsum(typical_price × volume) / cumsum(volume)
    SD bands use rolling variance of price around VWAP.
    """
    if not bars:
        return VWAPBands(vwap=0, upper_1sd=0, lower_1sd=0, upper_2sd=0, lower_2sd=0, upper_3sd=0, lower_3sd=0)

    tp_arr = np.array([(b.high + b.low + b.close) / 3 for b in bars])
    vol_arr = np.array([b.volume for b in bars], dtype=np.float64)

    cum_tp_vol = np.cumsum(tp_arr * vol_arr)
    cum_vol = np.cumsum(vol_arr)

    # Avoid division by zero
    cum_vol = np.where(cum_vol == 0, 1, cum_vol)
    vwap_arr = cum_tp_vol / cum_vol

    # Variance of price around VWAP
    cum_tp2_vol = np.cumsum(tp_arr**2 * vol_arr)
    variance = cum_tp2_vol / cum_vol - vwap_arr**2
    variance = np.maximum(variance, 0)  # Clamp numerical noise
    sd = np.sqrt(variance)

    # Use final values
    vwap = float(vwap_arr[-1])
    final_sd = float(sd[-1])

    return VWAPBands(
        vwap=round(vwap, 2),
        upper_1sd=round(vwap + final_sd, 2),
        lower_1sd=round(vwap - final_sd, 2),
        upper_2sd=round(vwap + 2 * final_sd, 2),
        lower_2sd=round(vwap - 2 * final_sd, 2),
        upper_3sd=round(vwap + 3 * final_sd, 2),
        lower_3sd=round(vwap - 3 * final_sd, 2),
    )


def compute_initial_balance(bars: list[BarData], rth_open: str = "09:30") -> InitialBalance:
    """Compute initial balance (first 60 minutes of RTH).

    Args:
        bars: 1-min bars for the session.
        rth_open: RTH open time as "HH:MM" (Eastern).
    """
    h, m = map(int, rth_open.split(":"))
    open_time = time(h, m)
    ib_end_time = time(h + 1, m)

    ib_bars = [b for b in bars if hasattr(b.timestamp, "time") and open_time <= _to_et_time(b.timestamp) < ib_end_time]

    if not ib_bars:
        return InitialBalance(ib_high=0, ib_low=0, ib_range=0)

    ib_high = max(b.high for b in ib_bars)
    ib_low = min(b.low for b in ib_bars)

    return InitialBalance(
        ib_high=round(ib_high, 2),
        ib_low=round(ib_low, 2),
        ib_range=round(ib_high - ib_low, 2),
    )


def compute_delta(ticks: list[TickData], bars: list[BarData]) -> DeltaAnalysis:
    """Compute delta analysis from tick data.

    Per-bar delta, cumulative delta, and divergence detection.
    """
    if not ticks and not bars:
        return DeltaAnalysis()

    # If we have ticks, compute accurate delta
    if ticks:
        # Group ticks by minute for per-bar delta
        tick_df = pd.DataFrame(
            [
                {
                    "timestamp": t.timestamp,
                    "size": t.size,
                    "side": t.side,
                }
                for t in ticks
            ]
        )

        if not tick_df.empty:
            tick_df["signed_vol"] = tick_df.apply(lambda r: r["size"] if r["side"] == "buy" else -r["size"], axis=1)

            tick_df["timestamp"] = pd.to_datetime(tick_df["timestamp"], utc=True)
            tick_df.set_index("timestamp", inplace=True)
            bar_deltas_series = tick_df["signed_vol"].resample("1min").sum().fillna(0)
            bar_deltas = bar_deltas_series.astype(int).tolist()
        else:
            bar_deltas = []
    else:
        # Fall back to bar-level delta if available
        bar_deltas = [b.delta for b in bars]

    cum_delta = []
    running = 0
    for d in bar_deltas:
        running += d
        cum_delta.append(running)

    total_delta = cum_delta[-1] if cum_delta else 0

    # Divergence: price trending up but delta trending down (or vice versa)
    divergence = False
    if bars and len(cum_delta) >= 20:
        price_change = bars[-1].close - bars[0].close
        delta_change = cum_delta[-1] - cum_delta[0]
        # Divergence if price and delta move in opposite directions significantly
        if (price_change > 0 and delta_change < -abs(total_delta) * 0.3) or (
            price_change < 0 and delta_change > abs(total_delta) * 0.3
        ):
            divergence = True

    return DeltaAnalysis(
        bar_deltas=bar_deltas,
        cumulative_delta=cum_delta,
        total_delta=total_delta,
        delta_divergence=divergence,
    )


def classify_market_type(profile: VolumeProfile, bars: list[BarData]) -> str:
    """Classify market as balanced or trending based on profile shape and price action.

    Balanced: narrow VA range relative to session range, P-shaped or b-shaped or D-shaped
    Trending: wide VA range, strong directional move
    """
    if not bars or not profile.poc:
        return "unknown"

    session_high = max(b.high for b in bars)
    session_low = min(b.low for b in bars)
    session_range = session_high - session_low

    if session_range == 0:
        return "balanced"

    va_range = profile.vah - profile.val
    va_ratio = va_range / session_range

    # Price direction
    price_change = bars[-1].close - bars[0].open
    pct_move = abs(price_change) / bars[0].open if bars[0].open else 0

    # Balanced: VA covers >60% of range and small directional move
    if va_ratio > 0.60 and pct_move < 0.005:
        return "balanced"

    # Trending: VA is narrow relative to range (one-sided move)
    if va_ratio < 0.50 or pct_move > 0.005:
        return "trending_up" if price_change > 0 else "trending_down"

    return "balanced"


def classify_opening_type(
    bars: list[BarData],
    prev_profile: VolumeProfile | None,
    ib: InitialBalance,
    rth_open: str = "09:30",
) -> str:
    """Classify opening type per AMT theory.

    OD  = Open Drive: Gap + strong IB in one direction
    OTD = Open Test Drive: Opens, tests one direction, reverses
    ORR = Open Rejection Reverse: Opens inside VA, rejects, reverses
    OA  = Open Auction: Opens inside VA, balanced two-sided trade
    """
    if not bars or ib.ib_range == 0:
        return "unknown"

    h, m = map(int, rth_open.split(":"))
    open_time = time(h, m)

    # Get first few bars of RTH
    rth_bars = [b for b in bars if hasattr(b.timestamp, "time") and _to_et_time(b.timestamp) >= open_time]
    if len(rth_bars) < 5:
        return "unknown"

    open_price = rth_bars[0].open

    # Where did we open relative to previous value area?
    if prev_profile and prev_profile.val and prev_profile.vah:
        opened_in_va = prev_profile.val <= open_price <= prev_profile.vah
    else:
        opened_in_va = True  # Default assumption

    # IB direction: which side of the open did IB establish?
    (ib.ib_high + ib.ib_low) / 2
    open_to_ib_high = ib.ib_high - open_price
    open_to_ib_low = open_price - ib.ib_low

    # Strong directional IB (>70% of range on one side of open)
    directional_ratio = max(open_to_ib_high, open_to_ib_low) / ib.ib_range if ib.ib_range else 0.5

    if not opened_in_va and directional_ratio > 0.70:
        return "OD"  # Open Drive — gap + strong directional IB
    elif directional_ratio > 0.65:
        # Check if early bars test one direction then reverse
        first_15 = rth_bars[:15]
        first_half = first_15[: len(first_15) // 2]
        second_half = first_15[len(first_15) // 2 :]
        if first_half and second_half:
            early_dir = first_half[-1].close - first_half[0].open
            late_dir = second_half[-1].close - second_half[0].open
            if (early_dir > 0 and late_dir < 0) or (early_dir < 0 and late_dir > 0):
                return "OTD"  # Open Test Drive
        return "OD"
    elif opened_in_va:
        if directional_ratio > 0.55:
            return "ORR"  # Open Rejection Reverse
        return "OA"  # Open Auction — balanced
    else:
        return "OTD"


def detect_poor_high_low(
    profile: VolumeProfile, session_high: float, session_low: float, tick_size: float = 0.25
) -> tuple[bool, bool]:
    """Detect poor highs and lows (excess at extremes).

    A poor high/low has volume at the extreme prices (no clean single-print
    tail), indicating unfinished auction.
    """
    if not profile.profile:
        return False, False

    sorted_prices = sorted(profile.profile.keys())
    if len(sorted_prices) < 10:
        return False, False

    # Check top 3 prices for high volume (poor high)
    top_prices = sorted_prices[-3:]
    avg_vol = sum(profile.profile.values()) / len(profile.profile)
    top_vol = sum(profile.profile.get(p, 0) for p in top_prices) / len(top_prices)
    poor_high = top_vol > avg_vol * 0.8  # High volume at extreme = poor auction

    # Check bottom 3 prices (poor low)
    bottom_prices = sorted_prices[:3]
    bottom_vol = sum(profile.profile.get(p, 0) for p in bottom_prices) / len(bottom_prices)
    poor_low = bottom_vol > avg_vol * 0.8

    return poor_high, poor_low


def detect_single_prints(profile: VolumeProfile, tick_size: float = 0.25) -> list[tuple[float, float]]:
    """Detect single print (low volume gap) areas in the profile.

    Single prints are areas where only 1 TPO printed — usually from
    initiative activity. We approximate as price levels with volume
    significantly below average.
    """
    if not profile.profile or len(profile.profile) < 10:
        return []

    sorted_prices = sorted(profile.profile.keys())
    avg_vol = sum(profile.profile.values()) / len(profile.profile)
    threshold = avg_vol * 0.15  # Below 15% of average = single print

    gaps = []
    gap_start = None

    for price in sorted_prices:
        vol = profile.profile.get(price, 0)
        if vol <= threshold:
            if gap_start is None:
                gap_start = price
        else:
            if gap_start is not None:
                gaps.append((gap_start, price - tick_size))
                gap_start = None

    return gaps


def compute_tpo_profile(bars: list[BarData], tick_size: float = 0.25, rth_open: str = "09:30") -> TPOProfile:
    """Build TPO (Market Profile) chart from 1-min bars.

    Assigns 30-min period letters (A=09:30-10:00, B=10:00-10:30, etc.).
    Each price level touched during a period gets one TPO print of that letter.
    TPO-based POC/VA uses the same 70% rule but on time-at-price instead of volume.
    """
    if not bars:
        return TPOProfile(tpo_poc=0, tpo_vah=0, tpo_val=0)

    h, m = map(int, rth_open.split(":"))
    open_time = time(h, m)

    # Filter to RTH bars only
    rth_bars = [b for b in bars if hasattr(b.timestamp, "time") and _to_et_time(b.timestamp) >= open_time]
    if not rth_bars:
        return TPOProfile(tpo_poc=0, tpo_vah=0, tpo_val=0)

    # Assign period letters: each 30-min block gets a letter A-Z
    tpo_count: dict[float, int] = {}
    period_letters: dict[float, set] = {}

    for bar in rth_bars:
        bar_time = _to_et_time(bar.timestamp)
        # Minutes since RTH open
        minutes = (bar_time.hour - h) * 60 + (bar_time.minute - m)
        if minutes < 0:
            continue
        period_idx = minutes // 30
        letter = chr(ord("A") + min(period_idx, 25))  # A-Z caps at Z

        # Mark each tick-level price in this bar's range
        low_tick = round(bar.low / tick_size) * tick_size
        high_tick = round(bar.high / tick_size) * tick_size
        price_levels = np.arange(low_tick, high_tick + tick_size / 2, tick_size)

        for price in price_levels:
            p = round(price, 2)
            if p not in period_letters:
                period_letters[p] = set()
            # Only count once per letter per price (TPO = unique period touches)
            if letter not in period_letters[p]:
                period_letters[p].add(letter)
                tpo_count[p] = tpo_count.get(p, 0) + 1

    if not tpo_count:
        return TPOProfile(tpo_poc=0, tpo_vah=0, tpo_val=0)

    # TPO POC = price with most TPO prints
    tpo_poc = max(tpo_count, key=tpo_count.get)
    total_tpo = sum(tpo_count.values())
    target_tpo = total_tpo * 0.70

    # 70% value area expansion from TPO POC
    sorted_prices = sorted(tpo_count.keys())
    poc_idx = sorted_prices.index(tpo_poc)
    included_tpo = tpo_count[tpo_poc]
    lo_idx = poc_idx
    hi_idx = poc_idx

    while included_tpo < target_tpo and (lo_idx > 0 or hi_idx < len(sorted_prices) - 1):
        lo_tpo = tpo_count.get(sorted_prices[lo_idx - 1], 0) if lo_idx > 0 else 0
        hi_tpo = tpo_count.get(sorted_prices[hi_idx + 1], 0) if hi_idx < len(sorted_prices) - 1 else 0

        if lo_tpo >= hi_tpo and lo_idx > 0:
            lo_idx -= 1
            included_tpo += lo_tpo
        elif hi_idx < len(sorted_prices) - 1:
            hi_idx += 1
            included_tpo += hi_tpo
        else:
            lo_idx -= 1
            included_tpo += lo_tpo

    tpo_val = sorted_prices[lo_idx]
    tpo_vah = sorted_prices[hi_idx]

    # Classify distribution shape
    dist_type = _classify_distribution(tpo_count, sorted_prices, tpo_poc)

    return TPOProfile(
        tpo_poc=tpo_poc,
        tpo_vah=tpo_vah,
        tpo_val=tpo_val,
        tpo_count=tpo_count,
        period_letters=period_letters,
        distribution_type=dist_type,
    )


def _classify_distribution(tpo_count: dict[float, int], sorted_prices: list[float], poc: float) -> str:
    """Classify TPO profile shape: normal, double, p_shape, b_shape.

    - Normal (D-shape): single mode, symmetric
    - P-shape: fat top, thin bottom (buying tail) — bullish
    - b-shape: thin top, fat bottom (selling tail) — bearish
    - Double distribution: two distinct modes separated by single prints
    """
    if len(sorted_prices) < 10:
        return "normal"

    n = len(sorted_prices)
    mid = n // 2
    top_half = sorted_prices[mid:]
    bottom_half = sorted_prices[:mid]

    top_tpo = sum(tpo_count.get(p, 0) for p in top_half)
    bottom_tpo = sum(tpo_count.get(p, 0) for p in bottom_half)
    total = top_tpo + bottom_tpo

    if total == 0:
        return "normal"

    top_ratio = top_tpo / total

    # Check for double distribution (two modes separated by thin area)
    counts = [tpo_count.get(p, 0) for p in sorted_prices]
    avg_count = sum(counts) / len(counts)
    thin_threshold = avg_count * 0.3

    # Find thin zones in the middle 60% of the range
    middle_start = n // 5
    middle_end = n - n // 5
    has_thin_zone = any(counts[i] <= thin_threshold for i in range(middle_start, middle_end))

    if has_thin_zone:
        # Verify there's meaningful volume on both sides of the thin zone
        for i in range(middle_start, middle_end):
            if counts[i] <= thin_threshold:
                below_mode = max(counts[:i]) if i > 0 else 0
                above_mode = max(counts[i + 1 :]) if i < n - 1 else 0
                if below_mode > avg_count * 1.2 and above_mode > avg_count * 1.2:
                    return "double"

    # P-shape vs b-shape
    if top_ratio > 0.62:
        return "p_shape"
    elif top_ratio < 0.38:
        return "b_shape"

    return "normal"


def compute_overnight_range(bars: list[BarData], rth_open: str = "09:30") -> tuple[float | None, float | None]:
    """Compute overnight (Globex) high and low before RTH open."""
    h, m = map(int, rth_open.split(":"))
    open_time = time(h, m)

    overnight_bars = [b for b in bars if hasattr(b.timestamp, "time") and _to_et_time(b.timestamp) < open_time]

    if not overnight_bars:
        return None, None

    return (
        max(b.high for b in overnight_bars),
        min(b.low for b in overnight_bars),
    )


def classify_price_position(
    price: float,
    profile: VolumeProfile | None,
    vwap: VWAPBands | None,
    ib: InitialBalance | None,
) -> tuple[str, str, str]:
    """Classify current price relative to VA, VWAP bands, and IB.

    Returns: (price_vs_va, price_vs_vwap, price_vs_ib)
    """
    # VS Value Area
    if profile and profile.vah and profile.val:
        if price > profile.vah:
            vs_va = "above"
        elif price < profile.val:
            vs_va = "below"
        else:
            vs_va = "within"
    else:
        vs_va = "unknown"

    # VS VWAP bands
    if vwap and vwap.vwap:
        if price >= vwap.upper_3sd:
            vs_vwap = "above_3sd"
        elif price >= vwap.upper_2sd:
            vs_vwap = "above_2sd"
        elif price >= vwap.upper_1sd:
            vs_vwap = "above_1sd"
        elif price <= vwap.lower_3sd:
            vs_vwap = "below_3sd"
        elif price <= vwap.lower_2sd:
            vs_vwap = "below_2sd"
        elif price <= vwap.lower_1sd:
            vs_vwap = "below_1sd"
        else:
            vs_vwap = "at_vwap"
    else:
        vs_vwap = "unknown"

    # VS Initial Balance
    if ib and ib.ib_high and ib.ib_low:
        if price > ib.ib_high:
            vs_ib = "above"
        elif price < ib.ib_low:
            vs_ib = "below"
        else:
            vs_ib = "within"
    else:
        vs_ib = "unknown"

    return vs_va, vs_vwap, vs_ib


def build_session_analysis(
    bars: list[BarData],
    ticks: list[TickData],
    prev_bars: list[BarData] | None,
    symbol: str,
    date_str: str,
    rth_open: str = "09:30",
    tick_size: float = 0.25,
) -> SessionAnalysis:
    """Build complete session analysis from bar and tick data.

    This is the main entry point for AMT analysis.
    """
    analysis = SessionAnalysis(date=date_str, symbol=symbol)

    if not bars:
        return analysis

    # Volume profile — use ALL bars (Globex+RTH) so historical VP matches live VP
    # (live VP anchors from 00:00 CET and includes all sessions)
    h, m = map(int, rth_open.split(":"))
    open_time = time(h, m)
    close_time = time(16, 0)

    rth_bars = [b for b in bars if hasattr(b.timestamp, "time") and open_time <= _to_et_time(b.timestamp) < close_time]

    if bars:
        analysis.volume_profile = compute_volume_profile(bars, tick_size)
        analysis.tpo_profile = compute_tpo_profile(bars, tick_size, rth_open)
        analysis.initial_balance = compute_initial_balance(bars, rth_open)

        # VWAP from actual tick prices (not bar typical price approximation).
        # Real VWAP = sum(price * size) / sum(size), matching TradingView.
        if ticks:
            rth_ticks = [
                t
                for t in ticks
                if hasattr(t.timestamp, "astimezone") and open_time <= t.timestamp.astimezone(_ET).time() < close_time
            ]
            if rth_ticks:
                import math

                cum_pv = sum(t.price * t.size for t in rth_ticks)
                cum_vol = sum(t.size for t in rth_ticks)
                cum_pv2 = sum(t.price * t.price * t.size for t in rth_ticks)
                if cum_vol > 0:
                    vwap = cum_pv / cum_vol
                    variance = max(0, (cum_pv2 / cum_vol) - vwap * vwap)
                    sd = math.sqrt(variance)
                    analysis.vwap_bands = VWAPBands(
                        vwap=round(vwap, 2),
                        upper_1sd=round(vwap + sd, 2),
                        lower_1sd=round(vwap - sd, 2),
                        upper_2sd=round(vwap + 2 * sd, 2),
                        lower_2sd=round(vwap - 2 * sd, 2),
                        upper_3sd=round(vwap + 3 * sd, 2),
                        lower_3sd=round(vwap - 3 * sd, 2),
                    )
                else:
                    analysis.vwap_bands = compute_vwap_bands(rth_bars)
            else:
                analysis.vwap_bands = compute_vwap_bands(rth_bars)
        else:
            analysis.vwap_bands = compute_vwap_bands(rth_bars)

        # Previous day profile
        prev_profile = None
        if prev_bars:
            prev_rth = [
                b
                for b in prev_bars
                if hasattr(b.timestamp, "time") and open_time <= _to_et_time(b.timestamp) < close_time
            ]
            if prev_rth:
                prev_profile = compute_volume_profile(prev_rth, tick_size)
                analysis.prev_poc = prev_profile.poc
                analysis.prev_vah = prev_profile.vah
                analysis.prev_val = prev_profile.val

        # Overnight range
        on_high, on_low = compute_overnight_range(bars, rth_open)
        analysis.overnight_high = on_high
        analysis.overnight_low = on_low

        # Delta
        analysis.delta = compute_delta(ticks, rth_bars)

        # Classifications
        analysis.market_type = classify_market_type(analysis.volume_profile, rth_bars)
        analysis.opening_type = classify_opening_type(bars, prev_profile, analysis.initial_balance, rth_open)

        # Poor high/low
        session_high = max(b.high for b in rth_bars)
        session_low = min(b.low for b in rth_bars)
        analysis.poor_high, analysis.poor_low = detect_poor_high_low(
            analysis.volume_profile, session_high, session_low, tick_size
        )

        # Single prints
        analysis.single_prints = detect_single_prints(analysis.volume_profile, tick_size)

    # Current price position
    analysis.last_price = bars[-1].close
    analysis.price_vs_va, analysis.price_vs_vwap, analysis.price_vs_ib = classify_price_position(
        analysis.last_price,
        analysis.volume_profile,
        analysis.vwap_bands,
        analysis.initial_balance,
    )

    # M7: ML day-type prediction (best-effort, supplements rule-based)
    try:
        from src.ml.serving.predictor import get_predictor

        predictor = get_predictor()
        if predictor.is_loaded("gate_classifier"):
            from src.ml.features.gate_features import extract_gate_features

            gate_features = extract_gate_features(
                ib_range=analysis.initial_balance.ib_range if analysis.initial_balance else None,
                opening_type=analysis.opening_type,
                vix_level=analysis.macro.vix if analysis.macro else None,
                gex=None,  # from options_flow if available
            )
            ml_type = predictor.predict("gate_classifier", gate_features)
            if ml_type and isinstance(ml_type, dict):
                from src.ml.models.gate_classifier import DAY_TYPE_LABELS

                predicted_class = ml_type.get("class", -1)
                probs = ml_type.get("probabilities", [])
                confidence = max(probs) if probs else 0
                if confidence > 0.6:
                    analysis.market_type = DAY_TYPE_LABELS.get(predicted_class, analysis.market_type)
    except Exception as e:
        logger.debug(f"M7 prediction skipped: {e}")

    return analysis
