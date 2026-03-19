"""TPO / Market Profile computation: 30-min brackets, anomalies."""
from dataclasses import dataclass, field
from datetime import datetime
import string


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


TPO_LETTERS = list(string.ascii_uppercase)


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

    Returns one of: "p-shape", "b-shape", "d-shape", or "balanced".
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


def compute_rotation_factor(bars_30m: list[dict]) -> tuple[float, int]:
    """Compute the rotation factor from 30-min bars.

    Rotation = how many 30-min periods extend the session high or low range.
    Returns (factor, count) where factor = rotations / (total_periods - 1).
    Single bar → (0.0, 0).
    """
    if len(bars_30m) <= 1:
        return (0.0, 0)

    session_high = bars_30m[0]["high"]
    session_low = bars_30m[0]["low"]
    rotations = 0

    for bar in bars_30m[1:]:
        if bar["high"] > session_high:
            rotations += 1
            session_high = bar["high"]
        if bar["low"] < session_low:
            rotations += 1
            session_low = bar["low"]

    factor = rotations / (len(bars_30m) - 1)
    return (factor, rotations)


def detect_excess(profile: TPOProfile) -> tuple[bool, bool]:
    """Detect excess (sharp rejection) at session extremes.

    Excess = single TPO print at the extreme, indicating sharp rejection.
    - excess_high: top 2 prices each have only 1 letter
    - excess_low: bottom 2 prices each have only 1 letter
    Returns (excess_high, excess_low). Empty profile → (False, False).
    """
    if not profile.letters:
        return (False, False)

    sorted_prices = sorted(profile.letters.keys())

    if len(sorted_prices) < 2:
        top_2 = sorted_prices
        bottom_2 = sorted_prices
    else:
        top_2 = sorted_prices[-2:]
        bottom_2 = sorted_prices[:2]

    excess_high = all(len(profile.letters[p]) == 1 for p in top_2)
    excess_low = all(len(profile.letters[p]) == 1 for p in bottom_2)

    return (excess_high, excess_low)
