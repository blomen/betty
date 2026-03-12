"""TPO / Market Profile computation: 30-min brackets, anomalies."""
from dataclasses import dataclass, field
from datetime import datetime
import string


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
        letter = TPO_LETTERS[i] if i < len(TPO_LETTERS) else TPO_LETTERS[-1]
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

    return TPOProfile(
        letters=letters, poc=poc, vah=vah, val=val,
        single_prints=single_prints, ledges=ledges,
        poor_high=poor_high, poor_low=poor_low,
        ib_tpo_count=ib_tpo_count,
    )
