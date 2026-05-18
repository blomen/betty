"""Level engine: computes all structural levels from bar/tick data."""

import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from .structure import MarketStructureEngine, StructureEvent, SwingLevel  # noqa: F401 — re-exported

logger = logging.getLogger(__name__)

ET = ZoneInfo("US/Eastern")


@dataclass
class VolumeProfileLevel:
    price: float
    volume: int


@dataclass
class VolumeProfile:
    poc: float  # Price of control (highest volume)
    vah: float  # Value area high (70% volume boundary)
    val: float  # Value area low
    levels: list[VolumeProfileLevel] = field(default_factory=list)
    single_prints: list[tuple[float, float]] = field(default_factory=list)
    # High/Low Volume Nodes — framework calls these "magnets" (HVN) and
    # "slips" (LVN). HVN = local volume peak > 1.5× mean bucket volume and
    # higher than both neighbors. LVN = local volume valley < 0.5× mean.
    hvn_levels: list[float] = field(default_factory=list)
    lvn_levels: list[float] = field(default_factory=list)


@dataclass
class VWAPBands:
    vwap: float
    sd1_upper: float
    sd1_lower: float
    sd2_upper: float
    sd2_lower: float
    sd3_upper: float
    sd3_lower: float


@dataclass
class SessionLevels:
    pdh: float | None = None
    pdl: float | None = None
    pdh_time: int | None = None  # epoch when PDH was made
    pdl_time: int | None = None  # epoch when PDL was made
    tokyo_high: float | None = None
    tokyo_low: float | None = None
    london_high: float | None = None
    london_low: float | None = None
    ib_high: float | None = None
    ib_low: float | None = None
    ny_high: float | None = None
    ny_low: float | None = None
    weekly_high: float | None = None
    weekly_low: float | None = None
    monthly_high: float | None = None
    monthly_low: float | None = None


@dataclass
class TimeframeSwings:
    """Swing detection result for a single timeframe."""

    timeframe: str  # "daily", "weekly", "monthly"
    structure: str  # "uptrend", "downtrend", "reversing_up", "reversing_down", "ranging"
    swing_highs: list[SwingLevel] = field(default_factory=list)  # newest first
    swing_lows: list[SwingLevel] = field(default_factory=list)  # newest first
    prior_high: float | None = None  # previous period high (PDH / prior week H / prior month H)
    prior_low: float | None = None  # previous period low  (PDL / prior week L / prior month L)
    last_bos: StructureEvent | None = None
    last_choch: StructureEvent | None = None
    bos_active: bool = False
    choch_active: bool = False


@dataclass
class SwingStructure:
    """Multi-timeframe swing analysis result."""

    daily: TimeframeSwings
    weekly: TimeframeSwings
    monthly: TimeframeSwings
    trend_alignment: float  # -1.0 (all down) to +1.0 (all up)


def aggregate_to_timeframe(
    bars_1m: list[dict],
    timeframe: str,
) -> list[dict]:
    """Aggregate 1m bars into daily/weekly/monthly OHLC candles.

    Uses CET session boundaries:
    - Daily: 00:00-22:00 CET
    - Weekly: Monday 00:00 to Friday 22:00 CET
    - Monthly: 1st 00:00 to last trading day 22:00 CET

    Returns list of {"date": str, "open": float, "high": float, "low": float,
    "close": float, "ts": int} sorted chronologically.
    """
    if not bars_1m:
        return []

    from collections import OrderedDict

    _CET = ZoneInfo("Europe/Stockholm")
    buckets: OrderedDict[str, list[dict]] = OrderedDict()

    for bar in bars_1m:
        bar_ts = bar["ts"]
        if isinstance(bar_ts, str):
            bar_ts = datetime.fromisoformat(bar_ts)
        if bar_ts.tzinfo is None:
            bar_ts = bar_ts.replace(tzinfo=timezone.utc)
        bar_cet = bar_ts.astimezone(_CET)

        if bar_cet.hour >= 22:
            continue

        if timeframe == "daily":
            key = bar_cet.date().isoformat()
        elif timeframe == "weekly":
            week_start = bar_cet.date() - timedelta(days=bar_cet.weekday())
            key = week_start.isoformat()
        elif timeframe == "monthly":
            key = f"{bar_cet.year}-{bar_cet.month:02d}"
        else:
            raise ValueError(f"Unknown timeframe: {timeframe}")

        if key not in buckets:
            buckets[key] = []
        buckets[key].append(bar)

    result = []
    for key, group in buckets.items():
        highs = [b["high"] for b in group]
        lows = [b["low"] for b in group]
        first_ts = group[0]["ts"]
        if isinstance(first_ts, str):
            first_ts = datetime.fromisoformat(first_ts)
        if first_ts.tzinfo is None:
            first_ts = first_ts.replace(tzinfo=timezone.utc)

        result.append(
            {
                "date": key,
                "open": group[0].get("open", group[0].get("close", highs[0])),
                "high": max(highs),
                "low": min(lows),
                "close": group[-1].get("close", group[-1].get("open", lows[-1])),
                "ts": int(first_ts.timestamp()),
            }
        )

    return result


_TF_RECENCY = {"daily": 5, "weekly": 3, "monthly": 2}
# Max HIGHER-TF candles per timeframe (after aggregation), not 1m bars.
# The previous version sliced bars_1m to N — but N=120 1m bars = 2 hours
# = 1 daily candle, well below the engine's 5-candle minimum, which made
# every daily/weekly/monthly result come back empty. Cap by candles so
# the input semantics match the engine's needs while still bounding how
# far back we look (a year of dailies is plenty for swing structure).
_TF_MAX_CANDLES = {"daily": 90, "weekly": 52, "monthly": 24}


def compute_multi_tf_swings(bars_1m: list[dict]) -> SwingStructure:
    """Compute swing structure across daily, weekly, and monthly timeframes.

    Aggregates 1m bars into higher-timeframe candles and runs MarketStructureEngine
    (Dow Theory BOS/CHoCH state machine) on each timeframe.
    """

    def empty_tf(tf: str) -> TimeframeSwings:
        return TimeframeSwings(timeframe=tf, structure="ranging")

    if not bars_1m:
        return SwingStructure(
            daily=empty_tf("daily"),
            weekly=empty_tf("weekly"),
            monthly=empty_tf("monthly"),
            trend_alignment=0.0,
        )

    trend_scores = {
        "uptrend": 1.0,
        "reversing_up": 0.5,
        "ranging": 0.0,
        "reversing_down": -0.5,
        "downtrend": -1.0,
    }

    results: dict[str, TimeframeSwings] = {}
    for tf in ("daily", "weekly", "monthly"):
        # Aggregate first, then cap by candle count — order matters because
        # _TF_MAX_CANDLES is in the timeframe's natural unit, not 1m bars.
        all_candles = aggregate_to_timeframe(bars_1m, tf)
        max_n = _TF_MAX_CANDLES[tf]
        candles = all_candles[-max_n:] if len(all_candles) > max_n else all_candles
        logger.info("Swing %s: %d candles (from %d 1m bars)", tf, len(candles), len(bars_1m))

        # Prior period H/L computed first so it's available even when the
        # swing engine can't run (e.g. monthly with < 5 aggregated candles
        # on a backtest pool spanning ~3 months). Without this fallback the
        # MONTHLY_SWING_* dims stay structurally 0 across the whole pool
        # and GBT can never see the higher-TF pivot.
        prior_high = None
        prior_low = None
        if len(candles) >= 2:
            prior = candles[-2]
            prior_high = prior["high"]
            prior_low = prior["low"]

        if len(candles) < 5:
            tf_result = empty_tf(tf)
            tf_result.prior_high = prior_high
            tf_result.prior_low = prior_low
            results[tf] = tf_result
            continue

        engine = MarketStructureEngine(recency_bars=_TF_RECENCY[tf])
        sr = engine.process(candles)

        for s in sr.swing_highs:
            s.timeframe = tf
        for s in sr.swing_lows:
            s.timeframe = tf

        results[tf] = TimeframeSwings(
            timeframe=tf,
            structure=sr.structure,
            swing_highs=sr.swing_highs,
            swing_lows=sr.swing_lows,
            prior_high=prior_high,
            prior_low=prior_low,
            last_bos=sr.last_bos,
            last_choch=sr.last_choch,
            bos_active=sr.bos_active,
            choch_active=sr.choch_active,
        )

    alignment = sum(trend_scores.get(results[tf].structure, 0.0) for tf in ("daily", "weekly", "monthly")) / 3.0

    return SwingStructure(
        daily=results["daily"],
        weekly=results["weekly"],
        monthly=results["monthly"],
        trend_alignment=round(alignment, 2),
    )


def compute_volume_profile(
    trades: list[dict],
    tick_size: float = 0.25,
) -> VolumeProfile:
    """Build volume profile from trade ticks. Groups volume into price buckets."""
    if not trades:
        return VolumeProfile(poc=0, vah=0, val=0)

    # Bucket volume by price level
    buckets: dict[float, int] = {}
    for t in trades:
        price = round(t["price"] / tick_size) * tick_size
        buckets[price] = buckets.get(price, 0) + t["size"]

    if not buckets:
        return VolumeProfile(poc=0, vah=0, val=0)

    # POC = price with highest volume
    poc = max(buckets, key=buckets.get)
    total_volume = sum(buckets.values())

    # Value Area = 70% of total volume, expanding outward from POC
    sorted_prices = sorted(buckets.keys())
    poc_idx = sorted_prices.index(poc)
    va_volume = buckets[poc]
    va_target = total_volume * 0.70
    lo_idx = poc_idx
    hi_idx = poc_idx

    while va_volume < va_target and (lo_idx > 0 or hi_idx < len(sorted_prices) - 1):
        expand_up = (
            buckets.get(sorted_prices[min(hi_idx + 1, len(sorted_prices) - 1)], 0)
            if hi_idx < len(sorted_prices) - 1
            else 0
        )
        expand_down = buckets.get(sorted_prices[max(lo_idx - 1, 0)], 0) if lo_idx > 0 else 0

        if expand_up >= expand_down and hi_idx < len(sorted_prices) - 1:
            hi_idx += 1
            va_volume += buckets[sorted_prices[hi_idx]]
        elif lo_idx > 0:
            lo_idx -= 1
            va_volume += buckets[sorted_prices[lo_idx]]
        else:
            hi_idx = min(hi_idx + 1, len(sorted_prices) - 1)
            va_volume += buckets.get(sorted_prices[hi_idx], 0)

    vah = sorted_prices[hi_idx]
    val = sorted_prices[lo_idx]

    # Detect single prints: prices with volume < 5% of POC volume
    poc_vol = buckets[poc]
    single_prints = []
    for i in range(1, len(sorted_prices)):
        if buckets[sorted_prices[i]] < poc_vol * 0.05:
            single_prints.append((sorted_prices[i], sorted_prices[i]))

    levels = [VolumeProfileLevel(price=p, volume=v) for p, v in sorted(buckets.items()) if v > 0]

    return VolumeProfile(poc=poc, vah=vah, val=val, levels=levels, single_prints=single_prints)


def bars_to_trades(bars: list[dict], tick_size: float = 0.25) -> list[dict]:
    """Convert OHLCV bars into synthetic trades that distribute volume across each bar's range.

    Instead of placing all volume at the close price (which distorts volume profiles),
    this distributes each bar's volume evenly across all tick-grid prices from low to high.

    For a bar with low=20095, high=20105, volume=500, tick_size=0.25:
      → 41 price levels from 20095.00 to 20105.00
      → each gets ~12 units of volume

    This is the standard approximation used by TradingView, Sierra Chart, etc.
    when tick data is unavailable.
    """
    trades: list[dict] = []
    for bar in bars:
        high = bar.get("high", 0)
        low = bar.get("low", 0)
        close = bar.get("close", 0)
        volume = bar.get("volume", 1)

        # Snap to tick grid
        low_snapped = round(low / tick_size) * tick_size
        high_snapped = round(high / tick_size) * tick_size

        # Degenerate bar (no range or bad data) — fall back to close
        if high_snapped <= low_snapped or volume <= 0:
            trades.append({"price": close, "size": max(volume, 1)})
            continue

        # Build price list across bar range
        prices = []
        price = low_snapped
        while price <= high_snapped + tick_size * 0.1:  # epsilon for float
            prices.append(round(price, 10))
            price += tick_size

        n_levels = len(prices)

        if volume < n_levels:
            # Very low volume: place 1 unit at `volume` levels nearest to close
            sorted_by_dist = sorted(range(n_levels), key=lambda i: abs(prices[i] - close))
            for idx in sorted_by_dist[:volume]:
                trades.append({"price": prices[idx], "size": 1})
        else:
            # Normal case: distribute evenly with remainder at center
            vol_per_level = volume // n_levels
            remainder = volume - vol_per_level * n_levels

            sorted_by_dist = sorted(range(n_levels), key=lambda i: abs(prices[i] - close))
            extras = [0] * n_levels
            for idx in sorted_by_dist:
                if remainder <= 0:
                    break
                extras[idx] = 1
                remainder -= 1

            for i, p in enumerate(prices):
                trades.append({"price": p, "size": vol_per_level + extras[i]})

    return trades


def _accumulate_bars_into_buckets(bars: list[dict], tick_size: float = 0.25) -> dict[float, int]:
    """Distribute bar volume into tick-level price buckets directly (no intermediate list).

    Same logic as bars_to_trades → compute_volume_profile, but ~10x faster
    because it skips creating millions of trade dicts.
    """
    buckets: dict[float, int] = {}
    for bar in bars:
        high = bar.get("high", 0)
        low = bar.get("low", 0)
        close = bar.get("close", 0)
        volume = bar.get("volume", 1)

        low_snapped = round(low / tick_size) * tick_size
        high_snapped = round(high / tick_size) * tick_size

        if high_snapped <= low_snapped or volume <= 0:
            price = round(close / tick_size) * tick_size
            buckets[price] = buckets.get(price, 0) + max(volume, 1)
            continue

        n_levels = round((high_snapped - low_snapped) / tick_size) + 1
        vol_per_level = volume // n_levels

        if vol_per_level > 0:
            # Normal case: enough volume to spread across the range
            remainder = volume - vol_per_level * n_levels

            price = low_snapped
            for _ in range(n_levels):
                p = round(price, 10)
                buckets[p] = buckets.get(p, 0) + vol_per_level
                price += tick_size

            # Distribute remainder near close
            if remainder > 0:
                close_snapped = round(close / tick_size) * tick_size
                given = 0
                offset = 0
                while given < remainder:
                    for p in (
                        round(close_snapped + offset * tick_size, 10),
                        round(close_snapped - offset * tick_size, 10),
                    ):
                        if given >= remainder:
                            break
                        if low_snapped <= p <= high_snapped:
                            buckets[p] = buckets.get(p, 0) + 1
                            given += 1
                    offset += 1
                    if offset > n_levels:
                        break
        else:
            # TPO-style: volume < n_levels (e.g. volume=1, bar spans 40 ticks)
            # Place all volume units near the close price instead of creating
            # thousands of zero-volume entries that bloat the profile.
            close_snapped = round(close / tick_size) * tick_size
            given = 0
            offset = 0
            while given < volume:
                for p in (round(close_snapped + offset * tick_size, 10), round(close_snapped - offset * tick_size, 10)):
                    if given >= volume:
                        break
                    if low_snapped <= p <= high_snapped:
                        buckets[p] = buckets.get(p, 0) + 1
                        given += 1
                offset += 1
                if offset > n_levels:
                    break

    return buckets


def compute_volume_profile_from_bars(
    bars: list[dict],
    tick_size: float = 0.25,
) -> VolumeProfile:
    """Fast VP computation directly from bars — avoids creating intermediate trade list."""
    if not bars:
        return VolumeProfile(poc=0, vah=0, val=0)

    buckets = _accumulate_bars_into_buckets(bars, tick_size)

    if not buckets:
        return VolumeProfile(poc=0, vah=0, val=0)

    poc = max(buckets, key=buckets.get)
    total_volume = sum(buckets.values())

    sorted_prices = sorted(buckets.keys())
    poc_idx = sorted_prices.index(poc)
    va_volume = buckets[poc]
    va_target = total_volume * 0.70
    lo_idx = poc_idx
    hi_idx = poc_idx

    while va_volume < va_target and (lo_idx > 0 or hi_idx < len(sorted_prices) - 1):
        expand_up = (
            buckets.get(sorted_prices[min(hi_idx + 1, len(sorted_prices) - 1)], 0)
            if hi_idx < len(sorted_prices) - 1
            else 0
        )
        expand_down = buckets.get(sorted_prices[max(lo_idx - 1, 0)], 0) if lo_idx > 0 else 0

        if expand_up >= expand_down and hi_idx < len(sorted_prices) - 1:
            hi_idx += 1
            va_volume += buckets[sorted_prices[hi_idx]]
        elif lo_idx > 0:
            lo_idx -= 1
            va_volume += buckets[sorted_prices[lo_idx]]
        else:
            hi_idx = min(hi_idx + 1, len(sorted_prices) - 1)
            va_volume += buckets.get(sorted_prices[hi_idx], 0)

    vah = sorted_prices[hi_idx]
    val = sorted_prices[lo_idx]

    poc_vol = buckets[poc]
    single_prints = []
    for i in range(1, len(sorted_prices)):
        if buckets[sorted_prices[i]] < poc_vol * 0.05:
            single_prints.append((sorted_prices[i], sorted_prices[i]))

    levels = [VolumeProfileLevel(price=p, volume=v) for p, v in sorted(buckets.items()) if v > 0]

    return VolumeProfile(poc=poc, vah=vah, val=val, levels=levels, single_prints=single_prints)


def normalize_volume_per_day(bars: list[dict], target_vol_per_day: int = 100_000) -> list[dict]:
    """Equalize each bar's contribution for composite (multi-day) profiles.

    When live data capture has uneven volume across days (e.g., 2M today vs 75k
    yesterday due to incomplete capture), use time-at-price instead of volume-at-price.
    Each bar contributes 1 unit of volume — this is equivalent to a TPO profile,
    which is the correct approach when volume data quality varies across days.
    """
    if not bars:
        return bars
    return [{**b, "volume": 1} for b in bars]


def compute_vwap_bands(trades: list[dict]) -> VWAPBands | None:
    """Compute VWAP + 1/2/3 SD bands from trade ticks."""
    if not trades:
        return None

    cum_pv = 0.0
    cum_vol = 0
    cum_pv2 = 0.0

    for t in trades:
        p = t["price"]
        v = t["size"]
        cum_pv += p * v
        cum_vol += v
        cum_pv2 += p * p * v

    if cum_vol == 0:
        return None

    vwap = cum_pv / cum_vol
    variance = (cum_pv2 / cum_vol) - (vwap * vwap)
    sd = math.sqrt(max(0, variance))

    return VWAPBands(
        vwap=vwap,
        sd1_upper=vwap + sd,
        sd1_lower=vwap - sd,
        sd2_upper=vwap + 2 * sd,
        sd2_lower=vwap - 2 * sd,
        sd3_upper=vwap + 3 * sd,
        sd3_lower=vwap - 3 * sd,
    )


def compute_developing_vwap(
    ticks: list[dict],
    interval_seconds: int = 60,
    rth_only: bool = True,
) -> list[dict]:
    """Compute developing VWAP + SD bands from tick data, one point per interval.

    Returns a time series of VWAP snapshots that can be drawn as a curve.
    Each tick must have: ts (datetime), price (float), size (int).

    Args:
        ticks: Trade ticks sorted by time. Each has {ts, price, size}.
        interval_seconds: Emit one VWAP point per this many seconds (default 60 = 1m).
        rth_only: If True, only include RTH ticks (09:30-16:00 ET) and reset at RTH open.

    Returns:
        List of dicts: [{t: epoch, vwap, sd1_u, sd1_l, sd2_u, sd2_l, sd3_u, sd3_l}, ...]
    """
    if not ticks:
        return []

    cum_pv = 0.0
    cum_vol = 0
    cum_pv2 = 0.0
    result: list[dict] = []
    current_bucket: int | None = None
    current_date: str | None = None  # Track ET date for daily reset

    rth_start = time(9, 30)
    rth_end = time(16, 0)

    for t in ticks:
        ts = t["ts"]
        if not hasattr(ts, "astimezone"):
            continue

        # Convert to ET for RTH filtering and daily reset
        ts_et = ts.astimezone(ET)
        t_time = ts_et.time()
        t_date = ts_et.strftime("%Y-%m-%d")

        if rth_only and not (rth_start <= t_time < rth_end):
            continue

        # Reset at each new RTH session (new ET date)
        if t_date != current_date:
            cum_pv = 0.0
            cum_vol = 0
            cum_pv2 = 0.0
            current_bucket = None
            current_date = t_date

        price = t["price"]
        size = t["size"]

        cum_pv += price * size
        cum_vol += size
        cum_pv2 += price * price * size

        # Bucket by interval
        epoch = int(ts.timestamp())
        bucket = epoch // interval_seconds

        if bucket != current_bucket and cum_vol > 0:
            current_bucket = bucket
            vwap = cum_pv / cum_vol
            variance = max(0, (cum_pv2 / cum_vol) - vwap * vwap)
            sd = math.sqrt(variance)

            result.append(
                {
                    "t": bucket * interval_seconds,
                    "vwap": round(vwap, 2),
                    "sd1_u": round(vwap + sd, 2),
                    "sd1_l": round(vwap - sd, 2),
                    "sd2_u": round(vwap + 2 * sd, 2),
                    "sd2_l": round(vwap - 2 * sd, 2),
                    "sd3_u": round(vwap + 3 * sd, 2),
                    "sd3_l": round(vwap - 3 * sd, 2),
                }
            )

    return result


CET = ZoneInfo("Europe/Stockholm")

# Fixed CET session boundaries (match frontend SESSION_DEFS)
# True market hours — Tokyo/London overlap 08:00-09:00, London/NY overlap 15:30-16:30
_TOKYO_START = time(0, 0)
_TOKYO_END = time(9, 0)
_LONDON_START = time(8, 0)
_LONDON_END = time(16, 30)
_NY_START = time(15, 30)
_NY_END = time(22, 0)
_IB_END = time(16, 30)  # NY open + 60 min


def compute_session_levels(
    bars_1m: list[dict],
    session_date: datetime,
) -> SessionLevels:
    """Compute PDH/PDL, Tokyo/London H/L, IB from 1-minute bars.

    All session boundaries use fixed CET times (matching chart display):
    - Tokyo: 00:00 - 09:00 CET  (overlaps London 08:00-09:00)
    - London: 08:00 - 16:30 CET (overlaps Tokyo & NY)
    - NY / IB: 15:30 - 22:00 CET  (IB = first 60 min: 15:30-16:30)
    - PDH/PDL: prior trading day's full session (00:00-22:00 CET)
    """
    levels = SessionLevels()
    if not bars_1m:
        return levels

    today_cet = session_date.astimezone(CET).date()

    # Find the prior trading day's CET date from actual bar data (handles weekends)
    prior_cet_dates = set()
    for bar in bars_1m:
        bar_ts = bar["ts"]
        if isinstance(bar_ts, str):
            bar_ts = datetime.fromisoformat(bar_ts)
        if bar_ts.tzinfo is None:
            bar_ts = bar_ts.replace(tzinfo=timezone.utc)
        cet_date = bar_ts.astimezone(CET).date()
        if cet_date < today_cet:
            prior_cet_dates.add(cet_date)
    prev_cet = max(prior_cet_dates) if prior_cet_dates else today_cet - timedelta(days=1)

    for bar in bars_1m:
        bar_ts = bar["ts"]
        if isinstance(bar_ts, str):
            bar_ts = datetime.fromisoformat(bar_ts)
        if bar_ts.tzinfo is None:
            bar_ts = bar_ts.replace(tzinfo=timezone.utc)
        bar_cet = bar_ts.astimezone(CET)
        bar_date = bar_cet.date()
        bar_time = bar_cet.time()
        h, l = bar["high"], bar["low"]
        bar_epoch = int(bar_ts.timestamp())

        # PDH/PDL: prior trading day's full session (00:00-22:00 CET)
        if bar_date == prev_cet and bar_time < _NY_END:
            if levels.pdh is None or h > levels.pdh:
                levels.pdh = h
                levels.pdh_time = bar_epoch
            if levels.pdl is None or l < levels.pdl:
                levels.pdl = l
                levels.pdl_time = bar_epoch

        # Weekly H/L (current week, all sessions 00:00-22:00 CET).
        # MUST run before the `today only` filter below — otherwise prior days
        # in the current week never count and weekly_high collapses to today's
        # high. Same for monthly. Caller must pass at least 1 month of bars
        # for these to be meaningful.
        week_start = today_cet - timedelta(days=today_cet.weekday())
        if week_start <= bar_date <= today_cet and bar_time < _NY_END:
            levels.weekly_high = max(levels.weekly_high or h, h)
            levels.weekly_low = min(levels.weekly_low or l, l)

        # Monthly H/L (current month, all sessions 00:00-22:00 CET)
        if bar_date.year == today_cet.year and bar_date.month == today_cet.month and bar_time < _NY_END:
            levels.monthly_high = max(levels.monthly_high or h, h)
            levels.monthly_low = min(levels.monthly_low or l, l)

        # Today's sessions
        if bar_date != today_cet:
            continue

        # Tokyo: 00:00-09:00 CET
        if _TOKYO_START <= bar_time < _TOKYO_END:
            levels.tokyo_high = max(levels.tokyo_high or h, h)
            levels.tokyo_low = min(levels.tokyo_low or l, l)

        # London: 08:00-16:30 CET
        if _LONDON_START <= bar_time < _LONDON_END:
            levels.london_high = max(levels.london_high or h, h)
            levels.london_low = min(levels.london_low or l, l)

        # IB: 15:30-16:30 CET (first 60 min of NY)
        if _NY_START <= bar_time < _IB_END:
            levels.ib_high = max(levels.ib_high or h, h)
            levels.ib_low = min(levels.ib_low or l, l)

        # NY session: 15:30-22:00 CET
        if _NY_START <= bar_time < _NY_END:
            levels.ny_high = max(levels.ny_high or h, h)
            levels.ny_low = min(levels.ny_low or l, l)

    return levels


@dataclass
class OrderBlock:
    price_low: float
    price_high: float
    direction: str  # "bullish" or "bearish"
    volume: int


@dataclass
class FairValueGap:
    price_low: float
    price_high: float
    direction: str  # "bullish" or "bearish"


def detect_order_blocks(bars: list[dict], min_move_pct: float = 0.0002) -> list[OrderBlock]:
    """Detect order blocks: last candle before an impulsive move."""
    blocks = []
    if len(bars) < 3:
        return blocks

    for i in range(1, len(bars) - 1):
        move = bars[i + 1]["close"] - bars[i]["close"]
        move_pct = abs(move) / bars[i]["close"] if bars[i]["close"] > 0 else 0

        if move_pct >= min_move_pct:
            # Impulsive move detected — prior candle is the order block
            ob = bars[i]
            direction = "bullish" if move > 0 else "bearish"
            blocks.append(
                OrderBlock(
                    price_low=ob["low"],
                    price_high=ob["high"],
                    direction=direction,
                    volume=ob.get("volume", 0),
                )
            )

    return blocks


def detect_fvgs(bars: list[dict]) -> list[FairValueGap]:
    """Detect Fair Value Gaps: gap between candle N-1 and N+1 that candle N didn't fill."""
    gaps = []
    if len(bars) < 3:
        return gaps

    for i in range(1, len(bars) - 1):
        prev_bar = bars[i - 1]
        next_bar = bars[i + 1]

        # Bullish FVG: prev_bar high < next_bar low (gap up)
        if prev_bar["high"] < next_bar["low"]:
            gaps.append(
                FairValueGap(
                    price_low=prev_bar["high"],
                    price_high=next_bar["low"],
                    direction="bullish",
                )
            )

        # Bearish FVG: prev_bar low > next_bar high (gap down)
        if prev_bar["low"] > next_bar["high"]:
            gaps.append(
                FairValueGap(
                    price_low=next_bar["high"],
                    price_high=prev_bar["low"],
                    direction="bearish",
                )
            )

    return gaps


def detect_swing_points(bars: list[dict], lookback: int = 5) -> dict:
    """Detect HH/HL/LH/LL swing structure from bar data.

    A swing high = bar whose high > all bars within lookback on each side.
    A swing low = bar whose low < all bars within lookback on each side.

    Returns dict with structure classification and swing levels.
    """
    n = len(bars)
    if n < 2 * lookback + 1:
        return {
            "structure": "ranging",
            "last_hh": None,
            "last_hl": None,
            "last_lh": None,
            "last_ll": None,
            "swing_high": None,
            "swing_low": None,
        }

    # Find pivot highs and lows
    pivot_highs: list[tuple[int, float]] = []  # (index, price)
    pivot_lows: list[tuple[int, float]] = []

    for i in range(lookback, n - lookback):
        high = bars[i]["high"]
        low = bars[i]["low"]
        is_pivot_high = all(high >= bars[j]["high"] for j in range(i - lookback, i + lookback + 1) if j != i)
        is_pivot_low = all(low <= bars[j]["low"] for j in range(i - lookback, i + lookback + 1) if j != i)
        if is_pivot_high:
            pivot_highs.append((i, high))
        if is_pivot_low:
            pivot_lows.append((i, low))

    if len(pivot_highs) < 2 or len(pivot_lows) < 2:
        return {
            "structure": "ranging",
            "last_hh": pivot_highs[-1][1] if pivot_highs else None,
            "last_hl": None,
            "last_lh": None,
            "last_ll": None,
            "swing_high": pivot_highs[-1][1] if pivot_highs else None,
            "swing_low": pivot_lows[-1][1] if pivot_lows else None,
        }

    # Classify structure from last 2 pivot highs and lows
    ph1, ph2 = pivot_highs[-2][1], pivot_highs[-1][1]
    pl1, pl2 = pivot_lows[-2][1], pivot_lows[-1][1]

    hh = ph2 > ph1  # Higher high
    hl = pl2 > pl1  # Higher low
    lh = ph2 < ph1  # Lower high
    ll = pl2 < pl1  # Lower low

    if hh and hl:
        structure = "uptrend"
    elif lh and ll:
        structure = "downtrend"
    else:
        structure = "ranging"

    return {
        "structure": structure,
        "last_hh": ph2 if hh else None,
        "last_hl": pl2 if hl else None,
        "last_lh": ph2 if lh else None,
        "last_ll": pl2 if ll else None,
        "swing_high": pivot_highs[-1][1],
        "swing_low": pivot_lows[-1][1],
    }


def detect_naked_pocs(
    prior_sessions: list[dict],
    bars_since: list[dict],
) -> list[dict]:
    """Find POCs from prior sessions that price has never revisited.

    A POC is 'naked' if no bar's low-high range includes that price
    since the session it was computed from.

    Args:
        prior_sessions: [{date, poc}, ...] ordered oldest to newest
        bars_since: All bars from oldest session date to now

    Returns: [{date, price}, ...] for naked POCs only
    """
    if not prior_sessions:
        return []

    naked = []
    for session in prior_sessions:
        poc = session["poc"]
        touched = any(bar["low"] <= poc <= bar["high"] for bar in bars_since)
        if not touched:
            naked.append({"date": session["date"], "price": poc})

    return naked


def compute_vp_hierarchy(profiles: dict, current_price: float | None = None, cluster_radius: float = 5.0) -> list[dict]:
    """Score and rank all VP levels by timeframe weight and confluence.

    Each level gets:
      - base weight from timeframe (monthly > macro > weekly > leg > daily)
      - type weight (POC = 2x, VAH/VAL = 1x)
      - confluence bonus when multiple levels cluster within cluster_radius

    Returns sorted list of level clusters (strongest first):
      [{"price": float, "strength": float, "sources": [{"tf": str, "type": str, "price": float}], "zone_high": float, "zone_low": float}]
    """
    TF_WEIGHTS = {"monthly": 5, "macro": 4, "weekly": 3, "current": 2, "leg": 2, "session": 1}
    TYPE_WEIGHTS = {"poc": 2.0, "vah": 1.0, "val": 1.0}

    # Collect all individual levels
    raw_levels: list[dict] = []
    for tf, weight in TF_WEIGHTS.items():
        vp = profiles.get(tf)
        if not vp:
            continue
        for level_type, type_w in TYPE_WEIGHTS.items():
            price = vp.get(level_type) if isinstance(vp, dict) else getattr(vp, level_type, None)
            if price and price > 0:
                raw_levels.append(
                    {
                        "price": price,
                        "tf": tf,
                        "type": level_type,
                        "base_weight": weight * type_w,
                    }
                )

    if not raw_levels:
        return []

    # Sort by price for clustering
    raw_levels.sort(key=lambda x: x["price"])

    # Cluster nearby levels (within cluster_radius)
    clusters: list[dict] = []
    used = set()

    for i, lvl in enumerate(raw_levels):
        if i in used:
            continue
        cluster_sources = [lvl]
        used.add(i)

        for j in range(i + 1, len(raw_levels)):
            if j in used:
                continue
            if abs(raw_levels[j]["price"] - lvl["price"]) <= cluster_radius:
                cluster_sources.append(raw_levels[j])
                used.add(j)

        prices = [s["price"] for s in cluster_sources]
        avg_price = sum(prices) / len(prices)
        base_strength = sum(s["base_weight"] for s in cluster_sources)

        # Confluence multiplier: 2 levels = 1.5x, 3+ = 2x
        n = len(cluster_sources)
        confluence_mult = 1.0 if n == 1 else 1.5 if n == 2 else 2.0

        strength = base_strength * confluence_mult

        clusters.append(
            {
                "price": round(avg_price, 2),
                "strength": round(strength, 1),
                "zone_high": max(prices),
                "zone_low": min(prices),
                "sources": [{"tf": s["tf"], "type": s["type"], "price": s["price"]} for s in cluster_sources],
                "confluence": n,
                "distance": round(abs(avg_price - current_price), 2) if current_price else None,
            }
        )

    # Sort by strength descending
    clusters.sort(key=lambda x: x["strength"], reverse=True)
    return clusters


def compute_developing_poc(bars: list[dict], tick_size: float = 0.25) -> dict:
    """Track POC migration by comparing current POC vs POC from first half.

    Converts bars (OHLCV) to synthetic trades for compute_volume_profile.

    Returns:
        {
            "developing_poc": float | None,
            "prior_poc": float | None,
            "direction": "up" | "down" | "flat",
        }
    """
    if not bars:
        return {"developing_poc": None, "prior_poc": None, "direction": "flat"}

    current_vp = compute_volume_profile_from_bars(bars, tick_size)
    current_poc = current_vp.poc

    half = max(1, len(bars) // 2)
    first_half_vp = compute_volume_profile_from_bars(bars[:half], tick_size)
    prior_poc = first_half_vp.poc

    if current_poc is None or prior_poc is None or (current_poc == 0 and prior_poc == 0):
        return {"developing_poc": current_poc, "prior_poc": prior_poc, "direction": "flat"}

    diff = current_poc - prior_poc
    threshold = tick_size * 4  # 1 point for NQ

    if diff > threshold:
        direction = "up"
    elif diff < -threshold:
        direction = "down"
    else:
        direction = "flat"

    return {
        "developing_poc": current_poc,
        "prior_poc": prior_poc,
        "direction": direction,
    }
