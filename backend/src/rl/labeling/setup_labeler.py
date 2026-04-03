"""Rule-based setup labeling for 5 mechanical setups.

Labels episodes based on structural context at the zone touch.
Each rule checks zone composition, approach direction, outcome,
and session context to classify the setup.
"""
from __future__ import annotations

from datetime import time

from .setup_types import SetupType

# Session extreme level types that qualify for failed auctions
_SESSION_EXTREMES = {
    "pdh", "pdl", "nyib_high", "nyib_low",
    "tokyo_high", "tokyo_low",
    "daily_swing_high", "daily_swing_low",
    "weekly_swing_high", "weekly_swing_low",
    "monthly_swing_high", "monthly_swing_low",
}

# Value area edge types for look-above/below-fail
_VA_EDGES = {"daily_vah", "daily_val", "tvah", "tval"}

_IB_CLOSE = time(10, 30)
_GAP_CUTOFF = time(12, 0)  # gap fills only in first 2 hours


def _is_failed_auction(ep: dict) -> bool:
    """Price probed beyond session extreme, failed to attract follow-through."""
    zone_types = set(ep["zone_types"])
    if not zone_types & _SESSION_EXTREMES:
        return False
    if ep["reward_rev"] <= ep["reward_cont"]:
        return False
    if ep.get("forward_reversal_speed", 0) < 5.0:
        return False
    return True


def _is_look_above_below_fail(ep: dict) -> bool:
    """Price pushed outside value area, rejected back inside."""
    zone_types = set(ep["zone_types"])
    if not zone_types & _VA_EDGES:
        return False
    pvv = ep.get("price_vs_value", 0)
    if abs(pvv) < 0.8:
        return False
    if ep["reward_rev"] <= ep["reward_cont"]:
        return False
    if ep.get("forward_reversal_speed", 0) < 4.0:
        return False
    return True


def _is_ib_extension(ep: dict) -> bool:
    """Breakout from initial balance with initiative activity."""
    zone_types = set(ep["zone_types"])
    if not zone_types & {"nyib_high", "nyib_low"}:
        return False
    if not ep.get("ib_closed", False):
        return False
    t = ep.get("touch_time_et")
    if t and t.time() < _IB_CLOSE:
        return False
    if ep["reward_cont"] <= ep["reward_rev"]:
        return False
    if abs(ep.get("delta_ratio", 0)) < 0.5:
        return False
    return True


def _is_gap_fill(ep: dict) -> bool:
    """Opening gap, price moving back to fill it."""
    if not ep.get("has_gap", False):
        return False
    t = ep.get("touch_time_et")
    if t and t.time() > _GAP_CUTOFF:
        return False
    return True


def _is_single_print_fill(ep: dict) -> bool:
    """Price returning to fill single-print zone or naked POC."""
    if ep.get("has_single_print", False):
        return True
    zone_types = set(ep["zone_types"])
    return "naked_poc" in zone_types


def label_episode(ep: dict) -> SetupType:
    """Label a single episode with its setup type.

    Applies rules in priority order. Returns SetupType.UNKNOWN
    if no rule matches (candidate for cluster labeling).
    """
    if _is_failed_auction(ep):
        return SetupType.FAILED_AUCTION
    if _is_look_above_below_fail(ep):
        return SetupType.LOOK_ABOVE_BELOW_FAIL
    if _is_ib_extension(ep):
        return SetupType.IB_EXTENSION
    if _is_gap_fill(ep):
        return SetupType.GAP_FILL
    if _is_single_print_fill(ep):
        return SetupType.SINGLE_PRINT_FILL
    return SetupType.UNKNOWN
