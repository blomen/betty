"""TPO / Market Profile computation: 30-min brackets, anomalies."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import time as _time
from zoneinfo import ZoneInfo

from .metrics import compute_rotation_factor as _metrics_rf

_CET = ZoneInfo("Europe/Stockholm")

# Non-overlapping session boundaries (CET)
_SESSION_BOUNDS = {
    "tokyo":  (_time(0, 0), _time(8, 0)),
    "london": (_time(8, 0), _time(15, 30)),
    "ny":     (_time(15, 30), _time(22, 0)),
}

# Minimum unique price levels in IB bars for IB to be considered valid
MIN_IB_TPO_COUNT = 4


def _period_letter(index: int) -> str:
    """Convert a 0-based period index to a TPO letter.

    0-25 → A-Z, 26 → AA, 27 → AB, 52 → BA, etc.
    """
    if index < 26:
        return chr(65 + index)
    return chr(65 + (index // 26) - 1) + chr(65 + (index % 26))


@dataclass
class TPOProfile:
    """Time Price Opportunity profile for a session."""
    letters: dict[float, list[str]]  # price → [A, B, C, ...]
    poc: float  # Price with most TPO letters
    vah: float
    val: float
    single_prints: list[float]  # Prices with only 1 letter
    ledges: list[float]  # Prices where profile cuts off abruptly
    poor_high: bool  # Thin tail at session high
    poor_low: bool   # Thin tail at session low
    ib_tpo_count: int  # Letters in first 2 brackets (A+B)
    # Extended fields
    tpo_counts: dict[float, int] = field(default_factory=dict)
    ib_high: float = 0.0
    ib_low: float = 0.0
    rotation_factor: int = 0
    profile_shape: str = "balanced"
    opening_type: str = "OA"
    opening_direction: str = "neutral"
    upper_excess: int = 0
    lower_excess: int = 0
    session_high: float = 0.0
    session_low: float = 0.0



def compute_tpo_profile(
    bars_30m: list[dict],
    tick_size: float = 0.25,
) -> TPOProfile:
    """Build TPO profile from 30-min OHLCV bars.

    Each 30-min period gets a letter (A, B, C...).
    Each price level touched in that period gets that letter.
    """
    if not bars_30m:
        return TPOProfile(
            letters={}, poc=0, vah=0, val=0,
            single_prints=[], ledges=[], poor_high=False, poor_low=False, ib_tpo_count=0,
        )

    letters: dict[float, list[str]] = {}

    for i, bar in enumerate(bars_30m):
        letter = _period_letter(i)
        low = round(bar["low"] / tick_size) * tick_size
        high = round(bar["high"] / tick_size) * tick_size

        price = low
        while price <= high + tick_size / 2:
            rounded = round(price / tick_size) * tick_size
            if rounded not in letters:
                letters[rounded] = []
            if letter not in letters[rounded]:
                letters[rounded].append(letter)
            price += tick_size

    if not letters:
        return TPOProfile(
            letters={}, poc=0, vah=0, val=0,
            single_prints=[], ledges=[], poor_high=False, poor_low=False, ib_tpo_count=0,
        )

    # POC = price with most letters
    poc = max(letters, key=lambda p: len(letters[p]))
    total_tpos = sum(len(v) for v in letters.values())

    # Value area = 70% of total TPOs
    sorted_prices = sorted(letters.keys())
    poc_idx = sorted_prices.index(poc)
    va_count = len(letters[poc])
    va_target = total_tpos * 0.70
    lo_idx = poc_idx
    hi_idx = poc_idx

    while va_count < va_target and (lo_idx > 0 or hi_idx < len(sorted_prices) - 1):
        up_count = len(letters.get(sorted_prices[min(hi_idx + 1, len(sorted_prices) - 1)], [])) if hi_idx < len(sorted_prices) - 1 else 0
        dn_count = len(letters.get(sorted_prices[max(lo_idx - 1, 0)], [])) if lo_idx > 0 else 0

        if up_count >= dn_count and hi_idx < len(sorted_prices) - 1:
            hi_idx += 1
            va_count += len(letters[sorted_prices[hi_idx]])
        elif lo_idx > 0:
            lo_idx -= 1
            va_count += len(letters[sorted_prices[lo_idx]])
        else:
            break

    vah = sorted_prices[hi_idx]
    val = sorted_prices[lo_idx]

    # Single prints: prices with exactly 1 letter
    single_prints = [p for p in sorted_prices if len(letters[p]) == 1]

    # Ledges: abrupt cutoff — price has 6+ fewer TPOs than its neighbor
    ledges = []
    for i in range(1, len(sorted_prices)):
        diff = abs(len(letters[sorted_prices[i]]) - len(letters[sorted_prices[i-1]]))
        if diff >= 6:
            ledges.append(sorted_prices[i])

    # Poor high/low: top/bottom 3 prices have ≤ 2 total letters
    top_3 = sorted_prices[-3:] if len(sorted_prices) >= 3 else sorted_prices
    bottom_3 = sorted_prices[:3] if len(sorted_prices) >= 3 else sorted_prices
    poor_high = sum(len(letters[p]) for p in top_3) <= 2
    poor_low = sum(len(letters[p]) for p in bottom_3) <= 2

    # IB TPO count: letters A and B
    ib_tpo_count = sum(1 for p in sorted_prices for l in letters[p] if l in ("A", "B"))

    # --- Extended fields ---
    tpo_counts = {p: len(v) for p, v in letters.items()}

    # IB high/low: max high / min low of first 2 bars
    ib_bars = bars_30m[:2]
    ib_high = max(b["high"] for b in ib_bars)
    ib_low = min(b["low"] for b in ib_bars)

    # Session high/low
    session_high = max(b["high"] for b in bars_30m)
    session_low = min(b["low"] for b in bars_30m)

    # Upper excess: consecutive single-print levels from top down
    upper_excess = 0
    for p in reversed(sorted_prices):
        if len(letters[p]) == 1:
            upper_excess += 1
        else:
            break

    # Lower excess: consecutive single-print levels from bottom up
    lower_excess = 0
    for p in sorted_prices:
        if len(letters[p]) == 1:
            lower_excess += 1
        else:
            break

    return TPOProfile(
        letters=letters, poc=poc, vah=vah, val=val,
        single_prints=single_prints, ledges=ledges,
        poor_high=poor_high, poor_low=poor_low,
        ib_tpo_count=ib_tpo_count,
        tpo_counts=tpo_counts,
        ib_high=ib_high, ib_low=ib_low,
        session_high=session_high, session_low=session_low,
        upper_excess=upper_excess, lower_excess=lower_excess,
    )


def classify_tpo_shape(profile: TPOProfile) -> str:
    """Classify the TPO profile shape based on distribution of letters.

    Returns one of: "B-shape", "p-shape", "b-shape", "d-shape", or "balanced".
    - B-shape: two distinct distribution clusters with a valley between them
    - p-shape: >65% of total TPO count above midpoint (concentration at top)
    - b-shape: >65% of total TPO count below midpoint (concentration at bottom)
    - d-shape: elongated range (>30 price levels) with roughly even distribution
    - balanced: everything else
    """
    if not profile.letters:
        return "balanced"

    sorted_prices = sorted(profile.letters.keys())
    n = len(sorted_prices)
    total_tpos = sum(len(v) for v in profile.letters.values())

    if total_tpos == 0:
        return "balanced"

    # B-shape: two peaks with valley between them
    counts = [len(profile.letters[p]) for p in sorted_prices]
    peak_count = max(counts)

    if peak_count >= 3 and n >= 10:
        valley_threshold = peak_count * 0.40
        peak_threshold = peak_count * 0.60
        for i in range(2, n - 2):
            if counts[i] <= valley_threshold:
                left_peak = max(counts[:i])
                right_peak = max(counts[i + 1:])
                if left_peak >= peak_threshold and right_peak >= peak_threshold:
                    return "B-shape"

    # Existing logic
    midpoint = (sorted_prices[0] + sorted_prices[-1]) / 2

    above_count = sum(len(profile.letters[p]) for p in sorted_prices if p > midpoint)
    below_count = sum(len(profile.letters[p]) for p in sorted_prices if p < midpoint)

    if above_count / total_tpos > 0.65:
        return "p-shape"
    if below_count / total_tpos > 0.65:
        return "b-shape"
    if n > 30:
        return "d-shape"
    return "balanced"



def detect_excess(profile: TPOProfile) -> tuple[int, int]:
    """Detect excess (sharp rejection) at session extremes.

    Counts consecutive single-print levels from each extreme inward.
    Returns (upper_count, lower_count). Empty profile → (0, 0).
    """
    if not profile.letters:
        return (0, 0)

    sorted_prices = sorted(profile.letters.keys())

    upper = 0
    for p in reversed(sorted_prices):
        if len(profile.letters[p]) == 1:
            upper += 1
        else:
            break

    lower = 0
    for p in sorted_prices:
        if len(profile.letters[p]) == 1:
            lower += 1
        else:
            break

    return (upper, lower)


def classify_opening_type(bars_30m: list[dict]) -> tuple[str, str]:
    """Classify session opening type from first 4 periods.

    Returns (opening_type, direction) where:
    - OD  = Open Drive: aggressive move from open, no retracement
    - OTD = Open Test Drive: initial move, retrace, then drive
    - ORR = Open Rejection Reverse: initial move then full reversal
    - OA  = Open Auction: balanced, no clear directional conviction
    """
    if len(bars_30m) < 4:
        return ("OA", "neutral")

    a, b, c, d = bars_30m[0], bars_30m[1], bars_30m[2], bars_30m[3]

    if a["close"] > a["open"]:
        a_dir = "up"
    elif a["close"] < a["open"]:
        a_dir = "down"
    else:
        return ("OA", "neutral")

    session_range = max(x["high"] for x in bars_30m[:4]) - min(x["low"] for x in bars_30m[:4])
    if session_range == 0:
        return ("OA", "neutral")

    ab_range = max(a["high"], b["high"]) - min(a["low"], b["low"])

    if a_dir == "up":
        a_opens_near_extreme = (a["open"] - min(x["low"] for x in bars_30m[:4])) / session_range <= 0.25
        b_extends = b["high"] > a["high"] and b["low"] >= a["low"]
        c_holds = c["low"] >= min(a["low"], b["low"]) + ab_range * 0.50 if ab_range > 0 else False
        if a_opens_near_extreme and b_extends and c_holds:
            return ("OD", "up")
        # ORR before OTD: full reversal is stronger signal
        b_continues = b["high"] >= a["high"]
        cd_reverses = (c["close"] < a["low"] or d["close"] < a["low"]) and (
            min(c["low"], d["low"]) < a["low"] - ab_range * 0.25
        )
        if b_continues and cd_reverses:
            return ("ORR", "down")
        b_retraces = b["low"] < a["high"] and b["low"] >= a["low"]
        c_drives = c["high"] > a["high"]
        if b_retraces and c_drives:
            return ("OTD", "up")
    else:  # down
        a_opens_near_extreme = (max(x["high"] for x in bars_30m[:4]) - a["open"]) / session_range <= 0.25
        b_extends = b["low"] < a["low"] and b["high"] <= a["high"]
        c_holds = c["high"] <= max(a["high"], b["high"]) - ab_range * 0.50 if ab_range > 0 else False
        if a_opens_near_extreme and b_extends and c_holds:
            return ("OD", "down")
        # ORR before OTD
        b_continues = b["low"] <= a["low"]
        cd_reverses = (c["close"] > a["high"] or d["close"] > a["high"]) and (
            max(c["high"], d["high"]) > a["high"] + ab_range * 0.25
        )
        if b_continues and cd_reverses:
            return ("ORR", "up")
        b_retraces = b["high"] > a["low"] and b["high"] <= a["high"]
        c_drives = c["low"] < a["low"]
        if b_retraces and c_drives:
            return ("OTD", "down")

    return ("OA", a_dir)


def aggregate_bars_30m(bars) -> list[dict]:
    """Aggregate 1-min BarData objects into 30-min OHLCV dicts."""
    result = []
    chunk = []
    for b in bars:
        chunk.append(b)
        if len(chunk) == 30:
            result.append({
                "high": max(c.high for c in chunk),
                "low": min(c.low for c in chunk),
                "open": chunk[0].open,
                "close": chunk[-1].close,
                "volume": sum(c.volume for c in chunk),
            })
            chunk = []
    return result


def build_full_tpo_profile(bars_30m: list[dict], tick_size: float = 0.25) -> TPOProfile:
    """Build fully enriched TPO profile. Single entry point for live, backfill, and RL."""
    profile = compute_tpo_profile(bars_30m, tick_size=tick_size)
    if not bars_30m:
        return profile
    highs = [b["high"] for b in bars_30m]
    lows = [b["low"] for b in bars_30m]
    profile.rotation_factor = _metrics_rf(highs, lows)
    profile.profile_shape = classify_tpo_shape(profile)
    profile.opening_type, profile.opening_direction = classify_opening_type(bars_30m)
    upper_ex, lower_ex = detect_excess(profile)
    profile.upper_excess = upper_ex
    profile.lower_excess = lower_ex
    return profile


# ---------------------------------------------------------------------------
# Per-session TPO profiles (Tokyo / London / NY)
# ---------------------------------------------------------------------------


@dataclass
class SessionTPO:
    """Per-session TPO profile with full letter data for visualization."""
    session: str       # "tokyo" | "london" | "ny"
    poc: float
    vah: float
    val: float
    shape: str         # "p-shape" | "b-shape" | "d-shape" | "balanced" | "B-shape"
    ib_high: float
    ib_low: float
    ib_valid: bool     # False if IB bars have < MIN_IB_TPO_COUNT price levels
    poor_high: bool
    poor_low: bool
    # Visualization fields:
    letters: dict[float, list[str]] = field(default_factory=dict)
    tpo_counts: dict[float, int] = field(default_factory=dict)
    upper_excess: int = 0
    lower_excess: int = 0
    session_high: float = 0.0
    session_low: float = 0.0
    opening_type: str = "OA"
    opening_direction: str = "neutral"


@dataclass
class SessionTPOSet:
    """Container for per-session TPO profiles + cross-session features."""
    tokyo: SessionTPO | None
    london: SessionTPO | None
    ny: SessionTPO | None
    poc_migration_tokyo_london: float  # (london.poc - tokyo.poc) / tick_size
    poc_migration_london_ny: float     # (ny.poc - london.poc) / tick_size


def _split_bars_by_session(
    bars_30m: list[dict],
) -> dict[str, list[dict]]:
    """Split 30m bars into session slices by CET time."""
    from datetime import timezone as _tz

    result: dict[str, list[dict]] = {"tokyo": [], "london": [], "ny": []}
    for bar in bars_30m:
        bar_ts = bar["ts"]
        if bar_ts.tzinfo is None:
            bar_ts = bar_ts.replace(tzinfo=_tz.utc)
        bar_cet = bar_ts.astimezone(_CET)
        bar_time = bar_cet.time()
        for session_name, (start, end) in _SESSION_BOUNDS.items():
            if start <= bar_time < end:
                result[session_name].append(bar)
                break
    return result


def _build_session_tpo(
    session_name: str,
    bars_30m: list[dict],
    tick_size: float,
) -> SessionTPO | None:
    """Build a SessionTPO from a slice of 30m bars for one session."""
    if not bars_30m:
        return None

    profile = compute_tpo_profile(bars_30m, tick_size=tick_size)
    shape = classify_tpo_shape(profile)
    opening_type, opening_direction = classify_opening_type(bars_30m)
    upper_excess, lower_excess = detect_excess(profile)

    # IB validity: check if first 2 bars touch enough price levels
    ib_bars = bars_30m[:2]
    ib_prices: set[float] = set()
    for bar in ib_bars:
        low_tick = round(bar["low"] / tick_size) * tick_size
        high_tick = round(bar["high"] / tick_size) * tick_size
        price = low_tick
        while price <= high_tick + tick_size / 2:
            ib_prices.add(round(price / tick_size) * tick_size)
            price += tick_size
    ib_valid = len(ib_prices) >= MIN_IB_TPO_COUNT

    return SessionTPO(
        session=session_name,
        poc=profile.poc,
        vah=profile.vah,
        val=profile.val,
        shape=shape,
        ib_high=profile.ib_high,
        ib_low=profile.ib_low,
        ib_valid=ib_valid,
        poor_high=profile.poor_high,
        poor_low=profile.poor_low,
        letters=profile.letters,
        tpo_counts={p: len(v) for p, v in profile.letters.items()},
        upper_excess=upper_excess,
        lower_excess=lower_excess,
        session_high=max(b["high"] for b in bars_30m),
        session_low=min(b["low"] for b in bars_30m),
        opening_type=opening_type,
        opening_direction=opening_direction,
    )


def compute_session_tpos(
    bars_30m: list[dict],
    tick_size: float = 0.25,
) -> SessionTPOSet:
    """Build per-session TPO profiles from 30m bars with timestamps.

    Each bar must have a "ts" key (datetime). Bars are split by CET time
    into Tokyo (00:00-08:00), London (08:00-15:30), NY (15:30-22:00).
    Letters restart at A for each session (slices are re-indexed from 0).
    """
    if not bars_30m:
        return SessionTPOSet(
            tokyo=None, london=None, ny=None,
            poc_migration_tokyo_london=0.0,
            poc_migration_london_ny=0.0,
        )

    slices = _split_bars_by_session(bars_30m)

    tokyo = _build_session_tpo("tokyo", slices["tokyo"], tick_size)
    london = _build_session_tpo("london", slices["london"], tick_size)
    ny = _build_session_tpo("ny", slices["ny"], tick_size)

    # POC migration deltas (in ticks)
    migration_tl = 0.0
    if tokyo and london and tokyo.poc and london.poc:
        migration_tl = (london.poc - tokyo.poc) / tick_size
    migration_ln = 0.0
    if london and ny and london.poc and ny.poc:
        migration_ln = (ny.poc - london.poc) / tick_size

    return SessionTPOSet(
        tokyo=tokyo, london=london, ny=ny,
        poc_migration_tokyo_london=migration_tl,
        poc_migration_london_ny=migration_ln,
    )
