"""Snapshot/restore round-trip on PositionTracker."""

from src.broker.position_tracker import PositionTracker


def test_flat_snapshot_round_trip():
    tr = PositionTracker()
    snap = tr.to_snapshot()
    assert snap["side"] is None
    assert snap["size"] == 0
    assert snap["entry_price"] == 0.0
    assert snap["peak_R"] == 0.0
    assert snap["locked_BE"] is False

    fresh = PositionTracker()
    fresh.restore_from_snapshot(snap)
    assert fresh.is_flat


def test_open_position_snapshot_round_trip():
    tr = PositionTracker()
    tr.on_fill("long", price=27226.0, size=1, stop_price=27217.75)
    tr.update_mark(27250.0)  # peak_R should now be ~2.9
    tr.locked_BE = True

    snap = tr.to_snapshot()
    fresh = PositionTracker()
    fresh.restore_from_snapshot(snap)

    assert fresh.side == "long"
    assert fresh.entry_price == 27226.0
    assert fresh.stop_price == 27217.75
    assert fresh.size == 1
    assert abs(fresh.peak_R - tr.peak_R) < 1e-6
    assert fresh.locked_BE is True
    assert not fresh.is_flat


def test_restore_overwrites_existing_state():
    tr = PositionTracker()
    tr.on_fill("short", price=27300.0, size=2, stop_price=27308.0)

    fresh = PositionTracker()
    fresh.on_fill("long", price=27226.0, size=1, stop_price=27217.75)
    fresh.restore_from_snapshot(tr.to_snapshot())

    assert fresh.side == "short"
    assert fresh.size == 2
    assert fresh.entry_price == 27300.0
