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


def detect_squeeze(ctx: DetectorContext) -> list[SetupCandidate]:
    """Detect pre-breakout compression (squeeze).

    Conditions:
    - At least 10 candles available
    - Last 5 candles have declining range (each bar tighter than average)
    - Volume declining over last 5 candles
    - Current range < 60% of session average range
    """
    candles = ctx.candles
    if not candles or len(candles) < 10:
        return []

    recent = candles[-5:]
    older = candles[-10:-5]

    if not older:
        return []

    # Average range of older candles
    older_ranges = [float(c.high - c.low) for c in older]
    recent_ranges = [float(c.high - c.low) for c in recent]
    avg_older_range = sum(older_ranges) / len(older_ranges)
    avg_recent_range = sum(recent_ranges) / len(recent_ranges)

    if avg_older_range <= 0:
        return []

    # Range compression: recent bars < 60% of older bars
    range_ratio = avg_recent_range / avg_older_range
    if range_ratio > 0.60:
        return []

    # Volume declining
    older_vol = sum(c.volume for c in older) / len(older)
    recent_vol = sum(c.volume for c in recent) / len(recent)
    if older_vol <= 0:
        return []
    vol_ratio = recent_vol / older_vol
    if vol_ratio > 0.70:
        return []

    # Squeeze confirmed — direction neutral, trade the breakout
    confidence = min(1.0, (1.0 - range_ratio) + (1.0 - vol_ratio))
    return [SetupCandidate(
        setup_type="squeeze",
        setup_name="Pre-Breakout Squeeze",
        direction="neutral",
        level_touched="compression_zone",
        entry_price=ctx.last_price,
        stop_price=ctx.last_price,  # placeholder — breakout direction unknown
        target_1=ctx.vp.vah if ctx.vp else ctx.last_price,
        target_2=ctx.vp.val if ctx.vp else ctx.last_price,
        target_3=None,
        base_score=confidence * 100,
    )]
