"""Snapshot persistence round-trip via _set_pending_trade."""
from unittest.mock import MagicMock

from src.broker.position_tracker import PositionTracker


def test_pending_trade_carries_tracker_snapshot(monkeypatch):
    """On _set_pending_trade, the disk image includes a tracker_snapshot field."""
    from src.stocks import broker_adapter

    written = {}
    def fake_save(value):
        written["value"] = value
    monkeypatch.setattr(broker_adapter, "_save_pending_trade_to_disk", fake_save)

    # Build a minimal adapter shell
    adapter = MagicMock()
    adapter.tracker = PositionTracker()
    adapter.tracker.on_fill("long", 27226.0, 1, 27217.75)
    adapter._pending_trade = None

    # Bind the real method
    adapter._set_pending_trade = broker_adapter.TopstepXBrokerAdapter._set_pending_trade.__get__(adapter)

    pending = {"side": "long", "entry_price": 27226.0}
    adapter._set_pending_trade(pending)

    assert "tracker_snapshot" in written["value"]
    assert written["value"]["tracker_snapshot"]["side"] == "long"
    assert written["value"]["tracker_snapshot"]["entry_price"] == 27226.0


def test_set_pending_trade_with_none_clears_disk(monkeypatch):
    """_set_pending_trade(None) writes None to disk (no snapshot embedded)."""
    from src.stocks import broker_adapter

    written = {"called": False, "value": "sentinel"}
    def fake_save(value):
        written["called"] = True
        written["value"] = value
    monkeypatch.setattr(broker_adapter, "_save_pending_trade_to_disk", fake_save)

    adapter = MagicMock()
    adapter.tracker = PositionTracker()
    adapter._pending_trade = {"side": "long"}
    adapter._set_pending_trade = broker_adapter.TopstepXBrokerAdapter._set_pending_trade.__get__(adapter)

    adapter._set_pending_trade(None)

    assert written["called"]
    assert written["value"] is None
    assert adapter._pending_trade is None


def test_set_pending_trade_does_not_mutate_caller_dict(monkeypatch):
    """The caller's dict shouldn't grow a tracker_snapshot key (helper deep-copies)."""
    from src.stocks import broker_adapter

    monkeypatch.setattr(broker_adapter, "_save_pending_trade_to_disk", lambda v: None)

    adapter = MagicMock()
    adapter.tracker = PositionTracker()
    adapter.tracker.on_fill("long", 27226.0, 1, 27217.75)
    adapter._set_pending_trade = broker_adapter.TopstepXBrokerAdapter._set_pending_trade.__get__(adapter)

    caller_dict = {"side": "long", "entry_price": 27226.0}
    adapter._set_pending_trade(caller_dict)

    assert "tracker_snapshot" not in caller_dict
