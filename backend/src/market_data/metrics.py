"""Session metrics: Rotation Factor, ASPR, range baselines."""

from dataclasses import dataclass


@dataclass
class SessionMetrics:
    rotation_factor: int
    aspr: float
    aspr_percentile: float | None  # vs historical baseline


def compute_rotation_factor(highs: list[float], lows: list[float]) -> int:
    """Compute Rotation Factor from sequential 30-min period highs/lows.

    Per 30-min period:
      current high > prev high → +1
      current high < prev high → -1
      current low > prev low → +1
      current low < prev low → -1
    """
    if len(highs) < 2:
        return 0

    rf = 0
    for i in range(1, len(highs)):
        if highs[i] > highs[i - 1]:
            rf += 1
        elif highs[i] < highs[i - 1]:
            rf -= 1

        if lows[i] > lows[i - 1]:
            rf += 1
        elif lows[i] < lows[i - 1]:
            rf -= 1
    return rf


def compute_aspr(ranges: list[float]) -> float:
    """Average Sub-Period Range from 30-min candle ranges."""
    if not ranges:
        return 0.0
    return sum(ranges) / len(ranges)


def compute_aspr_percentile(current_aspr: float, historical_asprs: list[float]) -> float:
    """Where current ASPR falls in historical distribution (0.0 = lowest, 1.0 = highest)."""
    if not historical_asprs:
        return 0.5
    below = sum(1 for h in historical_asprs if h <= current_aspr)
    return below / len(historical_asprs)


def detect_value_migration(
    today_vah: float,
    today_val: float,
    yesterday_vah: float,
    yesterday_val: float,
) -> str:
    """Detect value area migration: up, down, or overlapping."""
    if today_val > yesterday_val and today_vah > yesterday_vah:
        return "up"
    elif today_val < yesterday_val and today_vah < yesterday_vah:
        return "down"
    else:
        return "overlapping"
