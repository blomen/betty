"""Tests for broker position tracking."""

from src.broker.position_tracker import PositionTracker


def test_initial_state_is_flat():
    pt = PositionTracker()
    assert pt.is_flat
    assert pt.side is None
    assert pt.session_pnl == 0.0


def test_entry_long():
    pt = PositionTracker()
    pt.on_fill(side="long", price=25000.0, size=1, stop_price=24990.0)
    assert not pt.is_flat
    assert pt.side == "long"
    assert pt.entry_price == 25000.0
    assert pt.size == 1


def test_exit_pnl():
    pt = PositionTracker()
    pt.on_fill(side="long", price=25000.0, size=1, stop_price=24990.0)
    pnl = pt.on_exit(exit_price=25010.0)
    assert pnl == 10.0 * 20  # 10 pts * $20/pt for NQ
    assert pt.is_flat
    assert pt.session_pnl == 200.0


def test_peak_equity_tracking():
    pt = PositionTracker()
    pt.on_fill(side="long", price=25000.0, size=1, stop_price=24990.0)
    pt.on_exit(exit_price=25020.0)  # +$400
    assert pt.peak_equity == 400.0
    pt.on_fill(side="short", price=25020.0, size=1, stop_price=25030.0)
    pt.on_exit(exit_price=25025.0)  # -$100
    assert pt.session_pnl == 300.0
    assert pt.peak_equity == 400.0  # peak doesn't drop
    assert pt.trailing_dd == 100.0


def test_daily_loss_check():
    pt = PositionTracker()
    pt.on_fill(side="long", price=25000.0, size=1, stop_price=24990.0)
    pt.on_exit(exit_price=24950.0)  # -$1000
    assert pt.session_pnl == -1000.0
    assert pt.exceeds_daily_loss(1000.0)


def test_consecutive_stops():
    pt = PositionTracker()
    for _ in range(3):
        pt.on_fill(side="long", price=25000.0, size=1, stop_price=24990.0)
        pt.on_exit(exit_price=24990.0, was_stop=True)
    assert pt.consecutive_stops == 3


def test_trade_count():
    pt = PositionTracker()
    pt.on_fill(side="long", price=25000.0, size=1, stop_price=24990.0)
    pt.on_exit(exit_price=25010.0)
    pt.on_fill(side="short", price=25010.0, size=1, stop_price=25020.0)
    pt.on_exit(exit_price=25005.0)
    assert pt.trade_count == 2


def test_reset_session():
    pt = PositionTracker()
    pt.on_fill(side="long", price=25000.0, size=1, stop_price=24990.0)
    pt.on_exit(exit_price=25010.0)
    pt.reset_session()
    assert pt.is_flat
    assert pt.session_pnl == 0.0
    assert pt.trade_count == 0


def test_phase_property_reflects_locked_BE():
    """tracker.phase = 1 when locked_BE False, 2 when True."""
    t = PositionTracker()
    t.on_fill(side="long", price=25000.0, size=1, stop_price=24990.0)

    assert t.phase == 1, "fresh entry should be Phase 1"
    t.locked_BE = True
    assert t.phase == 2, "locked_BE should flip phase to 2"
    t.locked_BE = False
    assert t.phase == 1, "phase tracks locked_BE forward and back"


def test_phase_property_when_flat():
    """tracker.phase = 0 when flat (no position)."""
    t = PositionTracker()
    assert t.phase == 0
