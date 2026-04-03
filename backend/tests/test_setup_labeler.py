"""Tests for rule-based setup labeling."""
import numpy as np
import pytest
from datetime import datetime, time
from zoneinfo import ZoneInfo

from src.rl.labeling.setup_types import SetupType
from src.rl.labeling.setup_labeler import label_episode

ET = ZoneInfo("US/Eastern")


def _make_episode(
    zone_types: list[str],
    approach_dir: str = "up",
    reward_cont: float = 0.5,
    reward_rev: float = -0.3,
    touch_time: time = time(11, 0),
    price_vs_value: float = 0.0,
    has_gap: bool = False,
    has_single_print: bool = False,
    ib_closed: bool = True,
    delta_ratio: float = 0.3,
    forward_reversal_speed: float = 0.0,
):
    return {
        "zone_types": zone_types,
        "approach_direction": approach_dir,
        "reward_cont": reward_cont,
        "reward_rev": reward_rev,
        "touch_time_et": datetime(2025, 1, 15, touch_time.hour, touch_time.minute, tzinfo=ET),
        "price_vs_value": price_vs_value,
        "has_gap": has_gap,
        "has_single_print": has_single_print,
        "ib_closed": ib_closed,
        "delta_ratio": delta_ratio,
        "forward_reversal_speed": forward_reversal_speed,
    }


def test_failed_auction_at_pdh():
    ep = _make_episode(
        zone_types=["pdh"],
        approach_dir="up",
        reward_cont=-0.5,
        reward_rev=1.2,
        forward_reversal_speed=8.0,
    )
    assert label_episode(ep) == SetupType.FAILED_AUCTION


def test_ib_extension():
    ep = _make_episode(
        zone_types=["nyib_high"],
        approach_dir="up",
        reward_cont=1.5,
        reward_rev=-0.3,
        touch_time=time(11, 30),
        ib_closed=True,
        delta_ratio=0.7,
    )
    assert label_episode(ep) == SetupType.IB_EXTENSION


def test_gap_fill():
    ep = _make_episode(
        zone_types=["daily_vah"],
        approach_dir="down",
        touch_time=time(10, 15),
        has_gap=True,
        price_vs_value=0.8,
    )
    assert label_episode(ep) == SetupType.GAP_FILL


def test_single_print_fill():
    ep = _make_episode(
        zone_types=["naked_poc"],
        has_single_print=True,
    )
    assert label_episode(ep) == SetupType.SINGLE_PRINT_FILL


def test_look_above_and_fail():
    ep = _make_episode(
        zone_types=["daily_vah"],
        approach_dir="up",
        reward_cont=-0.4,
        reward_rev=0.9,
        price_vs_value=1.0,
        forward_reversal_speed=6.0,
    )
    assert label_episode(ep) == SetupType.LOOK_ABOVE_BELOW_FAIL


def test_unknown_when_no_rule_matches():
    ep = _make_episode(
        zone_types=["vwap"],
        approach_dir="up",
        reward_cont=0.1,
        reward_rev=0.1,
    )
    assert label_episode(ep) == SetupType.UNKNOWN


def test_priority_failed_auction_over_look_above():
    ep = _make_episode(
        zone_types=["daily_vah", "pdh"],
        approach_dir="up",
        reward_cont=-0.5,
        reward_rev=1.5,
        price_vs_value=1.0,
        forward_reversal_speed=10.0,
    )
    assert label_episode(ep) == SetupType.FAILED_AUCTION
