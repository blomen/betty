"""Tests for the zone-trail target computation."""

from dataclasses import dataclass

import pytest

from src.broker.position_tracker import PositionTracker
from src.market_data.zone_trail import compute_zone_trail_target


@dataclass
class FakeZone:
    """Minimal zone shape for tests — mirrors the runtime Zone dataclass fields used."""

    center_price: float
    upper_bound: float
    lower_bound: float
    member_count: int = 1


def _long_tracker(entry: float = 27226.0, stop: float = 27217.75) -> PositionTracker:
    tr = PositionTracker()
    tr.on_fill("long", price=entry, size=1, stop_price=stop)
    return tr


def _short_tracker(entry: float = 27300.0, stop: float = 27308.0) -> PositionTracker:
    tr = PositionTracker()
    tr.on_fill("short", price=entry, size=1, stop_price=stop)
    return tr


def test_long_first_advance_with_prior_zone_returns_prior_upper():
    """Long: touched zone B above entry, prior zone A exists → trail to A.upper_bound."""
    tr = _long_tracker()
    tr.update_mark(27258.0)  # peak_R ~ 3.88
    zone_b = FakeZone(center_price=27258.0, upper_bound=27260.0, lower_bound=27256.0)
    zone_a = FakeZone(center_price=27244.0, upper_bound=27246.0, lower_bound=27242.0)

    result = compute_zone_trail_target(tr, zone_b, all_zones=[zone_b, zone_a], current_zone_R=0.0)

    assert result is not None
    target_stop, advance_zone_R = result
    assert target_stop == 27246.0
    assert advance_zone_R == pytest.approx((27258.0 - 27226.0) / 8.25, rel=1e-3)


def test_short_first_advance_with_prior_zone_returns_prior_lower():
    """Short: touched zone below entry, prior zone above the touched one → trail to prior.lower_bound."""
    tr = _short_tracker()
    tr.update_mark(27270.0)  # peak_R ~ 3.75
    zone_b = FakeZone(center_price=27270.0, upper_bound=27272.0, lower_bound=27268.0)
    zone_a = FakeZone(center_price=27286.0, upper_bound=27288.0, lower_bound=27284.0)

    result = compute_zone_trail_target(tr, zone_b, all_zones=[zone_b, zone_a], current_zone_R=0.0)

    assert result is not None
    target_stop, _ = result
    assert target_stop == 27284.0  # prior zone (above) lower_bound


def test_no_prior_zone_falls_back_to_entry_plus_one_R():
    """Long: zone in open space (no prior zone between entry and current zone) → trail to entry + 1R."""
    tr = _long_tracker(entry=27226.0, stop=27217.75)  # 1R = 8.25
    tr.update_mark(27244.0)
    zone_b = FakeZone(center_price=27244.0, upper_bound=27246.0, lower_bound=27242.0)

    result = compute_zone_trail_target(tr, zone_b, all_zones=[zone_b], current_zone_R=0.0)

    assert result is not None
    target_stop, _ = result
    assert target_stop == pytest.approx(27226.0 + 8.25)  # entry + 1.0R


def test_same_zone_re_touched_returns_none():
    """If advance_zone_R <= current_zone_R, no trail (idempotent)."""
    tr = _long_tracker()
    tr.update_mark(27258.0)
    zone_b = FakeZone(center_price=27258.0, upper_bound=27260.0, lower_bound=27256.0)
    # current_zone_R already AT this zone's level
    advance_R_of_b = (27258.0 - 27226.0) / 8.25

    result = compute_zone_trail_target(tr, zone_b, all_zones=[zone_b], current_zone_R=advance_R_of_b)

    assert result is None


def test_zone_below_entry_for_long_returns_none():
    """Long: touched zone below entry — not a trade-direction advance, no trail."""
    tr = _long_tracker()
    tr.update_mark(27240.0)
    zone_below = FakeZone(center_price=27220.0, upper_bound=27222.0, lower_bound=27218.0)

    result = compute_zone_trail_target(tr, zone_below, all_zones=[zone_below], current_zone_R=0.0)

    assert result is None


def test_peak_R_below_2_returns_none():
    """Trail only fires when BE-lock has fired (peak_R >= 2.0)."""
    tr = _long_tracker()
    tr.update_mark(27240.0)  # peak_R ~ 1.7 — below 2.0
    zone = FakeZone(center_price=27240.0, upper_bound=27242.0, lower_bound=27238.0)

    result = compute_zone_trail_target(tr, zone, all_zones=[zone], current_zone_R=0.0)

    assert result is None


def test_zero_risk_unit_returns_none_safely():
    """Defensive: tracker with stop_price == entry_price yields zero risk_unit; helper returns None."""
    tr = PositionTracker()
    tr.on_fill("long", price=27226.0, size=1, stop_price=27226.0)
    tr.update_mark(27240.0)
    zone = FakeZone(center_price=27240.0, upper_bound=27242.0, lower_bound=27238.0)

    result = compute_zone_trail_target(tr, zone, all_zones=[zone], current_zone_R=0.0)

    assert result is None


def test_pending_trade_initializes_current_zone_R():
    """A fresh _pending_trade carries current_zone_R = 0.0."""
    # This is a smoke test — full entry path is exercised by integration tests.
    # Just verify the constant is present in the dict template.
    import inspect

    from src.stocks import broker_adapter
    src = inspect.getsource(broker_adapter.TopstepXBrokerAdapter._execute_entry)
    assert '"current_zone_R": 0.0' in src
