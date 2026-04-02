"""Real-time AMT (Auction Market Theory) dynamics tracker.

Lightweight tick-by-tick state tracker for IB extensions, responsive/initiative
volume, VA acceptance/rejection, developing day type, and balance areas.

Fed by LevelMonitor (live) or ReplayEngine (backtest) on every tick.
Produces a 20-feature snapshot for the RL observation vector.
"""
from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field

# Dalton day-type ordinal mapping
_DAY_TYPE_ORDINAL: dict[str, float] = {
    "non_trend": 0.0,
    "normal": 0.2,
    "neutral": 0.4,
    "normal_variation": 0.6,
    "trend": 0.8,
    "double_distribution": 1.0,
}

# Thresholds for range_ratio classification
_THRESHOLDS = (1.15, 1.5, 2.0)

_SNAPSHOT_KEYS = (
    "ib_ext_up_count",
    "ib_ext_down_count",
    "ib_max_extension",
    "ib_ext_net_direction",
    "developing_day_type",
    "day_type_confidence",
    "responsive_ratio",
    "initiative_ratio",
    "va_acceptance_high",
    "va_rejection_high",
    "va_acceptance_low",
    "va_rejection_low",
    "poc_migration_speed",
    "va_width_expansion_rate",
    "balance_duration",
    "balance_width",
    "single_print_proximity",
    "excess_high",
    "excess_low",
    "otf_activity",
)


class AMTDynamicsTracker:
    """Maintains running AMT state from tick-by-tick updates.

    Usage::

        tracker = AMTDynamicsTracker()
        tracker.initialize(session_data)
        for tick in ticks:
            tracker.update(tick.price, tick.size, tick.side)
        features = tracker.snapshot()
    """

    __slots__ = (
        # Session reference levels
        "ib_high", "ib_low", "ib_range",
        "vah", "val", "poc",
        "single_prints",
        # Running session extremes
        "session_high", "session_low",
        # IB extension state
        "_ib_ext_up_count", "_ib_ext_down_count",
        "_ib_max_ext_up", "_ib_max_ext_down",
        "_was_above_ib", "_was_below_ib",
        # Volume buckets
        "_vol_responsive", "_vol_initiative",
        "_otf_delta",
        # Excess tracking (large volume at session extremes)
        "_excess_high_vol", "_excess_low_vol", "_total_vol",
        # Period-close state
        "_poc_history", "_va_widths",
        "_initial_va_width",
        "_periods_above_vah", "_periods_below_val",
        "_rejection_high_pending", "_rejection_low_pending",
        "_rejection_high_countdown", "_rejection_low_countdown",
        "_va_acceptance_high", "_va_rejection_high",
        "_va_acceptance_low", "_va_rejection_low",
        # Balance area
        "_balance_periods", "_balance_width",
        # Developing VA (updated on period close)
        "_dev_vah", "_dev_val",
        # Initialized flag
        "_initialized",
    )

    def __init__(self) -> None:
        self.ib_high: float = 0.0
        self.ib_low: float = 0.0
        self.ib_range: float = 0.0
        self.vah: float = 0.0
        self.val: float = 0.0
        self.poc: float = 0.0
        self.single_prints: list[tuple[float, float]] = []

        self.session_high: float = 0.0
        self.session_low: float = float("inf")

        self._ib_ext_up_count: int = 0
        self._ib_ext_down_count: int = 0
        self._ib_max_ext_up: float = 0.0
        self._ib_max_ext_down: float = 0.0
        self._was_above_ib: bool = False
        self._was_below_ib: bool = False

        self._vol_responsive: int = 0
        self._vol_initiative: int = 0
        self._otf_delta: int = 0

        self._excess_high_vol: int = 0
        self._excess_low_vol: int = 0
        self._total_vol: int = 0

        self._poc_history: deque[float] = deque(maxlen=12)
        self._va_widths: deque[float] = deque(maxlen=12)
        self._initial_va_width: float = 0.0

        self._periods_above_vah: int = 0
        self._periods_below_val: int = 0
        self._rejection_high_pending: bool = False
        self._rejection_low_pending: bool = False
        self._rejection_high_countdown: int = 0
        self._rejection_low_countdown: int = 0
        self._va_acceptance_high: int = 0
        self._va_rejection_high: int = 0
        self._va_acceptance_low: int = 0
        self._va_rejection_low: int = 0

        self._balance_periods: int = 0
        self._balance_width: float = 0.0

        self._dev_vah: float = 0.0
        self._dev_val: float = 0.0

        self._initialized: bool = False

    # ------------------------------------------------------------------
    # Initialization
    # ------------------------------------------------------------------

    def initialize(self, session_data: dict) -> None:
        """Set session reference levels from pre-computed session data.

        Expected keys: ib_high, ib_low, vah, val, poc, single_prints (optional).
        """
        self.ib_high = float(session_data.get("ib_high", 0.0))
        self.ib_low = float(session_data.get("ib_low", 0.0))
        self.ib_range = self.ib_high - self.ib_low
        self.vah = float(session_data.get("vah", self.ib_high))
        self.val = float(session_data.get("val", self.ib_low))
        self.poc = float(session_data.get("poc", (self.ib_high + self.ib_low) / 2.0))
        self.single_prints = list(session_data.get("single_prints", []))

        self.session_high = self.ib_high
        self.session_low = self.ib_low

        self._dev_vah = self.vah
        self._dev_val = self.val
        self._initial_va_width = max(self.vah - self.val, 1e-9)

        self._initialized = True

    # ------------------------------------------------------------------
    # Tick-level update
    # ------------------------------------------------------------------

    def update(self, price: float, size: int, side: str) -> None:
        """Process a single tick. Called on every trade.

        Args:
            price: Trade price.
            size: Trade size (contracts).
            side: 'buy' or 'sell' (aggressor side).
        """
        if not self._initialized:
            return

        # Update session extremes
        if price > self.session_high:
            self.session_high = price
        if price < self.session_low:
            self.session_low = price

        # --- IB extension tracking ---
        if self.ib_range > 0:
            above_ib = price > self.ib_high
            below_ib = price < self.ib_low

            # Count new extension: was inside/below, now above
            if above_ib and not self._was_above_ib:
                self._ib_ext_up_count += 1
            # Count new extension: was inside/above, now below
            if below_ib and not self._was_below_ib:
                self._ib_ext_down_count += 1

            self._was_above_ib = above_ib
            self._was_below_ib = below_ib

            # Track max extension magnitude
            ext_up = max(0.0, price - self.ib_high)
            ext_down = max(0.0, self.ib_low - price)
            if ext_up > self._ib_max_ext_up:
                self._ib_max_ext_up = ext_up
            if ext_down > self._ib_max_ext_down:
                self._ib_max_ext_down = ext_down

        # --- Responsive vs initiative volume ---
        va_high = self._dev_vah
        va_low = self._dev_val
        if va_low <= price <= va_high:
            self._vol_responsive += size
        else:
            self._vol_initiative += size
            # OTF delta: directional delta outside VA
            if side == "buy":
                self._otf_delta += size
            else:
                self._otf_delta -= size

        # --- Excess tracking (volume at session extremes) ---
        self._total_vol += size
        threshold = self.ib_range * 0.05 if self.ib_range > 0 else 1.0
        if abs(price - self.session_high) <= threshold:
            self._excess_high_vol += size
        if abs(price - self.session_low) <= threshold:
            self._excess_low_vol += size

    # ------------------------------------------------------------------
    # Period-close update (every 30 min)
    # ------------------------------------------------------------------

    def on_period_close(
        self,
        period_high: float,
        period_low: float,
        developing_poc: float,
        developing_vah: float,
        developing_val: float,
    ) -> None:
        """Called at the close of each 30-min period.

        Updates POC migration, VA width tracking, acceptance/rejection,
        and balance area detection.
        """
        if not self._initialized:
            return

        # Update developing VA for responsive/initiative split
        self._dev_vah = developing_vah
        self._dev_val = developing_val

        # --- POC migration ---
        self._poc_history.append(developing_poc)

        # --- VA width tracking ---
        va_width = max(developing_vah - developing_val, 1e-9)
        self._va_widths.append(va_width)

        # --- VA acceptance/rejection ---
        # High side
        if period_high > self.vah:
            self._periods_above_vah += 1
            if not self._rejection_high_pending:
                self._rejection_high_pending = True
                self._rejection_high_countdown = 2
        else:
            if self._rejection_high_pending:
                # Price probed above VAH but snapped back within 2 periods
                self._rejection_high_countdown -= 1
                if self._rejection_high_countdown <= 0:
                    self._va_rejection_high += 1
                    self._rejection_high_pending = False

        # If sustained above VAH for 3+ periods, count as acceptance
        if self._periods_above_vah >= 3 and period_high > self.vah:
            self._va_acceptance_high += 1
            self._periods_above_vah = 0  # reset after counting

        # Low side
        if period_low < self.val:
            self._periods_below_val += 1
            if not self._rejection_low_pending:
                self._rejection_low_pending = True
                self._rejection_low_countdown = 2
        else:
            if self._rejection_low_pending:
                self._rejection_low_countdown -= 1
                if self._rejection_low_countdown <= 0:
                    self._va_rejection_low += 1
                    self._rejection_low_pending = False

        if self._periods_below_val >= 3 and period_low < self.val:
            self._va_acceptance_low += 1
            self._periods_below_val = 0

        # --- Balance area detection ---
        if self.ib_range > 0:
            session_range = self.session_high - self.session_low
            if session_range <= 1.5 * self.ib_range:
                self._balance_periods += 1
                self._balance_width = session_range
            else:
                # Once broken, reset balance counter
                self._balance_periods = 0
                self._balance_width = 0.0

    # ------------------------------------------------------------------
    # Snapshot
    # ------------------------------------------------------------------

    def snapshot(self) -> dict[str, float]:
        """Return all 20 AMT dynamics features as a dict.

        All values are finite floats (no inf/nan).
        """
        ib_range = self.ib_range if self.ib_range > 0 else 1e-9

        # Day type classification
        daily_range = self.session_high - self.session_low
        day_type_name, day_type_confidence = self._classify_day_type(
            ib_range, daily_range, self._ib_max_ext_up, self._ib_max_ext_down,
        )

        # IB extension net direction: positive = more up extensions
        ext_total = max(self._ib_ext_up_count + self._ib_ext_down_count, 1)
        ib_ext_net = (self._ib_ext_up_count - self._ib_ext_down_count) / ext_total

        # Max extension normalized by IB range
        ib_max_ext = max(self._ib_max_ext_up, self._ib_max_ext_down) / ib_range

        # Volume ratios
        total_vol = max(self._vol_responsive + self._vol_initiative, 1)
        responsive_ratio = self._vol_responsive / total_vol
        initiative_ratio = self._vol_initiative / total_vol

        # POC migration speed: average absolute move per period
        poc_migration = 0.0
        if len(self._poc_history) >= 2:
            moves = [
                abs(self._poc_history[i] - self._poc_history[i - 1])
                for i in range(1, len(self._poc_history))
            ]
            poc_migration = (sum(moves) / len(moves)) / ib_range

        # VA width expansion rate
        va_expansion = 0.0
        if len(self._va_widths) >= 2:
            latest = self._va_widths[-1]
            va_expansion = (latest - self._initial_va_width) / max(self._initial_va_width, 1e-9)

        # Balance area
        balance_duration = min(self._balance_periods / 12.0, 1.0)  # normalize to 0-1
        balance_width = self._balance_width / ib_range if self._balance_periods >= 3 else 0.0

        # Single print proximity: min distance from current session mid to any single print zone
        single_print_prox = 0.0
        if self.single_prints:
            session_mid = (self.session_high + self.session_low) / 2.0
            min_dist = min(
                min(abs(session_mid - sp[0]), abs(session_mid - sp[1]))
                for sp in self.single_prints
            )
            single_print_prox = max(0.0, 1.0 - min_dist / ib_range)

        # Excess: high volume at extremes normalized
        total_vol_safe = max(self._total_vol, 1)
        excess_high = min(self._excess_high_vol / total_vol_safe, 1.0)
        excess_low = min(self._excess_low_vol / total_vol_safe, 1.0)

        # OTF activity: absolute delta outside VA, normalized
        otf_activity = min(abs(self._otf_delta) / max(total_vol_safe, 1), 1.0)

        result = {
            "ib_ext_up_count": float(self._ib_ext_up_count),
            "ib_ext_down_count": float(self._ib_ext_down_count),
            "ib_max_extension": _finite(ib_max_ext),
            "ib_ext_net_direction": _finite(ib_ext_net),
            "developing_day_type": _DAY_TYPE_ORDINAL.get(day_type_name, 0.0),
            "day_type_confidence": _finite(day_type_confidence),
            "responsive_ratio": _finite(responsive_ratio),
            "initiative_ratio": _finite(initiative_ratio),
            "va_acceptance_high": float(self._va_acceptance_high),
            "va_rejection_high": float(self._va_rejection_high),
            "va_acceptance_low": float(self._va_acceptance_low),
            "va_rejection_low": float(self._va_rejection_low),
            "poc_migration_speed": _finite(poc_migration),
            "va_width_expansion_rate": _finite(va_expansion),
            "balance_duration": _finite(balance_duration),
            "balance_width": _finite(balance_width),
            "single_print_proximity": _finite(single_print_prox),
            "excess_high": _finite(excess_high),
            "excess_low": _finite(excess_low),
            "otf_activity": _finite(otf_activity),
        }
        return result

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _classify_day_type(
        ib_range: float,
        daily_range: float,
        ext_up: float,
        ext_down: float,
    ) -> tuple[str, float]:
        """Classify Dalton day type and return (name, confidence).

        Confidence is based on distance from the nearest threshold,
        normalized to 0-1.
        """
        if ib_range <= 0:
            return "normal", 0.0

        ratio = daily_range / ib_range

        # Determine day type
        if ratio <= 1.15:
            day_type = "non_trend"
        elif ratio <= 1.5:
            day_type = "normal"
        elif ratio <= 2.0:
            max_ext = max(ext_up, ext_down, 1e-9)
            imbalance = abs(ext_up - ext_down) / max_ext
            day_type = "neutral" if imbalance < 0.2 else "normal_variation"
        else:
            if ext_up > 3.0 * max(ext_down, 1e-9) or ext_down > 3.0 * max(ext_up, 1e-9):
                day_type = "trend"
            else:
                day_type = "double_distribution"

        # Confidence: distance from nearest threshold boundary
        distances = [abs(ratio - t) for t in _THRESHOLDS]
        min_dist = min(distances)
        # Normalize: at threshold = 0 confidence, 0.5+ away = full confidence
        confidence = min(min_dist / 0.5, 1.0)

        return day_type, confidence


def _finite(v: float) -> float:
    """Clamp to finite float (no inf/nan)."""
    if math.isfinite(v):
        return v
    return 0.0
