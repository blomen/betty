"""Incremental VWAP and Volume Profile accumulators for RL feature computation.

These maintain running state so each tick is O(1) rather than recomputing
from scratch over the full trade history.
"""

import math

from ...market_data.levels import VolumeProfile, VolumeProfileLevel, VWAPBands


class IncrementalVWAP:
    """Online VWAP with ±1/2/3 standard-deviation bands.

    Maintains three running sums:
      _cum_pv  - sum of price * size
      _cum_vol - sum of size
      _cum_pv2 - sum of price² * size

    VWAP = cum_pv / cum_vol
    variance = cum_pv2 / cum_vol - vwap²
    sd = sqrt(max(0, variance))
    """

    def __init__(self) -> None:
        self._cum_pv: float = 0.0
        self._cum_vol: int = 0
        self._cum_pv2: float = 0.0

    def update(self, price: float, size: int) -> None:
        """Incorporate one trade into the running accumulators."""
        self._cum_pv += price * size
        self._cum_vol += size
        self._cum_pv2 += price * price * size

    def get(self) -> VWAPBands | None:
        """Return current VWAP bands, or None if no trades have been added."""
        if self._cum_vol == 0:
            return None

        vwap = self._cum_pv / self._cum_vol
        variance = (self._cum_pv2 / self._cum_vol) - (vwap * vwap)
        sd = math.sqrt(max(0.0, variance))

        return VWAPBands(
            vwap=vwap,
            sd1_upper=vwap + sd,
            sd1_lower=vwap - sd,
            sd2_upper=vwap + 2 * sd,
            sd2_lower=vwap - 2 * sd,
            sd3_upper=vwap + 3 * sd,
            sd3_lower=vwap - 3 * sd,
        )

    def reset(self) -> None:
        """Clear all accumulated state."""
        self._cum_pv = 0.0
        self._cum_vol = 0
        self._cum_pv2 = 0.0


class IncrementalVolumeProfile:
    """Online volume profile with POC, value area (70 %), and single-print detection.

    Prices are snapped to the nearest tick_size grid before bucketing,
    matching the behaviour of levels.compute_volume_profile().

    Value-area expansion tie-breaking mirrors the batch function exactly:
    when the candidate volume above equals the candidate volume below,
    expand **up** first.
    """

    def __init__(self, tick_size: float = 0.25) -> None:
        self._tick_size = tick_size
        self._histogram: dict[float, int] = {}

    def update(self, price: float, size: int) -> None:
        """Snap price to tick grid and add size to that bucket."""
        snapped = round(price / self._tick_size) * self._tick_size
        self._histogram[snapped] = self._histogram.get(snapped, 0) + size

    def get(self) -> VolumeProfile | None:
        """Return current volume profile, or None if no trades have been added."""
        if not self._histogram:
            return None

        buckets = self._histogram
        poc = max(buckets, key=buckets.__getitem__)
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
        single_prints: list[tuple[float, float]] = []
        for i in range(1, len(sorted_prices)):
            if buckets[sorted_prices[i]] < poc_vol * 0.05:
                single_prints.append((sorted_prices[i], sorted_prices[i]))

        # HVN / LVN detection — local volume peaks and valleys inside VA.
        # Framework calls HVN "magnets" (price tends to pause/react) and LVN
        # "slips" (price moves through fast). These are critical AMT reference
        # points the model previously had no access to.
        mean_vol = total_volume / max(len(sorted_prices), 1)
        hvn_levels: list[float] = []
        lvn_levels: list[float] = []
        if len(sorted_prices) >= 3:
            for i in range(1, len(sorted_prices) - 1):
                p = sorted_prices[i]
                v = buckets[p]
                v_prev = buckets[sorted_prices[i - 1]]
                v_next = buckets[sorted_prices[i + 1]]
                # Only count within value area (HVN/LVN in tails are noisy)
                if not (val <= p <= vah):
                    continue
                if v > mean_vol * 1.5 and v > v_prev and v > v_next and p != poc:
                    hvn_levels.append(p)
                elif v < mean_vol * 0.5 and v < v_prev and v < v_next:
                    lvn_levels.append(p)

        levels = [VolumeProfileLevel(price=p, volume=v) for p, v in sorted(buckets.items())]

        return VolumeProfile(
            poc=poc,
            vah=vah,
            val=val,
            levels=levels,
            single_prints=single_prints,
            hvn_levels=hvn_levels,
            lvn_levels=lvn_levels,
        )

    def reset(self) -> None:
        """Clear all accumulated state."""
        self._histogram.clear()
