"""Test that BatchBuilder produces three tiers: polymarket, pinnacle, soft."""
import pytest
from src.services.batch_builder import TIER_PRIORITY, BatchBet


def test_tier_priority_has_three_tiers():
    assert "polymarket" in TIER_PRIORITY
    assert "pinnacle" in TIER_PRIORITY
    assert "soft" in TIER_PRIORITY
    assert TIER_PRIORITY["polymarket"] > TIER_PRIORITY["pinnacle"]
    assert TIER_PRIORITY["pinnacle"] > TIER_PRIORITY["soft"]


def test_tier_priority_no_sharp():
    """The old 'sharp' tier must not exist."""
    assert "sharp" not in TIER_PRIORITY
