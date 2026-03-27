"""Squeeze / compression pre-breakout detector.

Detects narrowing range + declining volume before a potential breakout.
Fabio: "This can be a pre-explosion squeeze... these are the best sessions."

A squeeze is characterized by:
- IB range narrowing over consecutive bars (compression)
- Volume declining (participants waiting)
- Range bars getting smaller (tight consolidation)

This is distinct from break_from_balance — that detects the actual breakout.
Squeeze detects the PRE-breakout compression phase.
"""
from __future__ import annotations

from .detector import DetectorContext, SetupCandidate


def detect_squeeze(ctx: DetectorContext) -> SetupCandidate | None:
    """Detect pre-breakout compression (squeeze).

    Conditions:
    - At least 5 candles available
    - Last 5 candles have declining range (each bar tighter than average)
    - Volume declining over last 5 candles
    - Current range < 60% of session average range
    """
    candles = ctx.candles
    if not candles or len(candles) < 10:
        return None

    recent = candles[-5:]
    older = candles[-10:-5]

    if not older:
        return None

    # Average range of older candles
    older_ranges = [(c.high - c.low) for c in older]
    recent_ranges = [(c.high - c.low) for c in recent]
    avg_older_range = sum(older_ranges) / len(older_ranges)
    avg_recent_range = sum(recent_ranges) / len(recent_ranges)

    if avg_older_range <= 0:
        return None

    # Range compression: recent bars < 60% of older bars
    range_ratio = avg_recent_range / avg_older_range
    if range_ratio > 0.60:
        return None

    # Volume declining
    older_vol = sum(c.volume for c in older) / len(older)
    recent_vol = sum(c.volume for c in recent) / len(recent)
    if older_vol <= 0:
        return None
    vol_ratio = recent_vol / older_vol
    if vol_ratio > 0.70:
        return None

    # Squeeze confirmed
    return SetupCandidate(
        name="squeeze",
        direction="neutral",  # squeeze doesn't predict direction
        confidence=min(1.0, (1.0 - range_ratio) + (1.0 - vol_ratio)),
        entry_price=ctx.price,
        stop_distance=None,
        target_distance=None,
    )
