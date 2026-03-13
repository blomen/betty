"""Level engine: computes all structural levels from bar/tick data."""
import math
from dataclasses import dataclass, field
from datetime import datetime, time, timezone, timedelta
from zoneinfo import ZoneInfo

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
    tokyo_high: float | None = None
    tokyo_low: float | None = None
    london_high: float | None = None
    london_low: float | None = None
    ib_high: float | None = None
    ib_low: float | None = None
    weekly_high: float | None = None
    weekly_low: float | None = None
    monthly_high: float | None = None
    monthly_low: float | None = None


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
        expand_up = buckets.get(sorted_prices[min(hi_idx + 1, len(sorted_prices) - 1)], 0) if hi_idx < len(sorted_prices) - 1 else 0
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

    levels = [VolumeProfileLevel(price=p, volume=v) for p, v in sorted(buckets.items())]

    return VolumeProfile(poc=poc, vah=vah, val=val, levels=levels, single_prints=single_prints)


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


def compute_session_levels(
    bars_1m: list[dict],
    session_date: datetime,
) -> SessionLevels:
    """Compute PDH/PDL, Tokyo/London H/L, IB from 1-minute bars.

    All session boundaries in US/Eastern time:
    - Tokyo: 20:00 - 02:00 ET (prior evening into early morning)
    - London: 03:00 - 08:30 ET
    - IB: 09:30 - 10:30 ET (first 60 min of RTH)
    - PDH/PDL: prior calendar day's RTH range
    """
    levels = SessionLevels()
    if not bars_1m:
        return levels

    today_et = session_date.astimezone(ET).date() if isinstance(session_date, datetime) else session_date

    for bar in bars_1m:
        bar_ts = bar["ts"]
        if isinstance(bar_ts, str):
            bar_ts = datetime.fromisoformat(bar_ts)
        if bar_ts.tzinfo is None:
            bar_ts = bar_ts.replace(tzinfo=timezone.utc)
        bar_et = bar_ts.astimezone(ET)
        bar_date = bar_et.date()
        bar_time = bar_et.time()
        h, l = bar["high"], bar["low"]

        # PDH/PDL: yesterday's RTH (09:30-16:00)
        yesterday = today_et - timedelta(days=1)
        if bar_date == yesterday and time(9, 30) <= bar_time < time(16, 0):
            levels.pdh = max(levels.pdh or h, h)
            levels.pdl = min(levels.pdl or l, l)

        # Tokyo: 20:00 ET prior day to 02:00 ET current day
        if bar_date == yesterday and bar_time >= time(20, 0):
            levels.tokyo_high = max(levels.tokyo_high or h, h)
            levels.tokyo_low = min(levels.tokyo_low or l, l)
        elif bar_date == today_et and bar_time < time(2, 0):
            levels.tokyo_high = max(levels.tokyo_high or h, h)
            levels.tokyo_low = min(levels.tokyo_low or l, l)

        # London: 03:00-08:30 ET
        if bar_date == today_et and time(3, 0) <= bar_time < time(8, 30):
            levels.london_high = max(levels.london_high or h, h)
            levels.london_low = min(levels.london_low or l, l)

        # IB: 09:30-10:30 ET
        if bar_date == today_et and time(9, 30) <= bar_time < time(10, 30):
            levels.ib_high = max(levels.ib_high or h, h)
            levels.ib_low = min(levels.ib_low or l, l)

        # Weekly H/L (current week, Mon-Fri RTH)
        week_start = today_et - timedelta(days=today_et.weekday())
        if week_start <= bar_date <= today_et and time(9, 30) <= bar_time < time(16, 0):
            levels.weekly_high = max(levels.weekly_high or h, h)
            levels.weekly_low = min(levels.weekly_low or l, l)

        # Monthly H/L (current month RTH)
        if bar_date.year == today_et.year and bar_date.month == today_et.month and time(9, 30) <= bar_time < time(16, 0):
            levels.monthly_high = max(levels.monthly_high or h, h)
            levels.monthly_low = min(levels.monthly_low or l, l)

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


def detect_order_blocks(bars: list[dict], min_move_pct: float = 0.003) -> list[OrderBlock]:
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
            blocks.append(OrderBlock(
                price_low=ob["low"],
                price_high=ob["high"],
                direction=direction,
                volume=ob.get("volume", 0),
            ))

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
            gaps.append(FairValueGap(
                price_low=prev_bar["high"],
                price_high=next_bar["low"],
                direction="bullish",
            ))

        # Bearish FVG: prev_bar low > next_bar high (gap down)
        if prev_bar["low"] > next_bar["high"]:
            gaps.append(FairValueGap(
                price_low=next_bar["high"],
                price_high=prev_bar["low"],
                direction="bearish",
            ))

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
            "last_hh": None, "last_hl": None,
            "last_lh": None, "last_ll": None,
            "swing_high": None, "swing_low": None,
        }

    # Find pivot highs and lows
    pivot_highs: list[tuple[int, float]] = []  # (index, price)
    pivot_lows: list[tuple[int, float]] = []

    for i in range(lookback, n - lookback):
        high = bars[i]["high"]
        low = bars[i]["low"]
        is_pivot_high = all(
            high >= bars[j]["high"] for j in range(i - lookback, i + lookback + 1) if j != i
        )
        is_pivot_low = all(
            low <= bars[j]["low"] for j in range(i - lookback, i + lookback + 1) if j != i
        )
        if is_pivot_high:
            pivot_highs.append((i, high))
        if is_pivot_low:
            pivot_lows.append((i, low))

    if len(pivot_highs) < 2 or len(pivot_lows) < 2:
        return {
            "structure": "ranging",
            "last_hh": pivot_highs[-1][1] if pivot_highs else None,
            "last_hl": None, "last_lh": None, "last_ll": None,
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
