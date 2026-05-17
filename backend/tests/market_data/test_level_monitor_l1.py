from src.market_data.l1_quote_state import L1Snapshot
from src.market_data.level_monitor import LevelMonitor


def test_level_monitor_has_l1_state():
    lm = LevelMonitor(publish_fn=lambda *_a, **_kw: None)
    assert hasattr(lm, "l1_state")
    assert lm.l1_state.snapshot() is None


def test_level_monitor_l1_state_update_and_snapshot():
    lm = LevelMonitor(publish_fn=lambda *_a, **_kw: None)
    lm.l1_state.update(bid=25000.0, ask=25000.25, bid_size=10, ask_size=8, ts=1.0)
    snap = lm.l1_state.snapshot()
    assert isinstance(snap, L1Snapshot)
    assert snap.bid == 25000.0
