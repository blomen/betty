"""Tests for TopstepXStream handler methods.

We test handler logic directly without establishing real SignalR connections.
"""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch

from src.stocks.topstepx_stream import TopstepXStream, _parse_ts


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_stream() -> TopstepXStream:
    """Return a stream instance with no real connections."""
    return TopstepXStream(
        token="test-token",
        contract_id="CON.F.US.ENQ.M26",
        account_id=99999,
    )


TICK_DATA = {
    "price": 21500.25,
    "volume": 3,
    "timestamp": "2026-04-09T14:30:00Z",
}

EXPECTED_TS = 1775745000.0  # 2026-04-09T14:30:00Z as epoch


# ---------------------------------------------------------------------------
# _parse_ts helper
# ---------------------------------------------------------------------------

def test_parse_ts_iso_z():
    ts = _parse_ts("2026-04-09T14:30:00Z")
    assert abs(ts - EXPECTED_TS) < 1.0


def test_parse_ts_bad_string():
    # Should return 0.0 and not raise
    ts = _parse_ts("not-a-date")
    assert ts == 0.0


# ---------------------------------------------------------------------------
# _handle_trade
# ---------------------------------------------------------------------------

def test_handle_trade_calls_on_tick():
    stream = _make_stream()
    received = []
    stream.on_tick = lambda price, size, ts, side: received.append((price, size, ts, side))

    stream._handle_trades(["CON.F.US.ENQ.M26", [TICK_DATA]])

    assert len(received) == 1
    price, size, ts, side = received[0]
    assert price == 21500.25
    assert size == 3
    assert abs(ts - EXPECTED_TS) < 1.0


def test_handle_trade_ignores_empty():
    stream = _make_stream()
    called = []
    stream.on_tick = lambda *a: called.append(a)

    # Should not raise and should not call on_tick
    stream._handle_trades(["CON.F.US.ENQ.M26", []])
    assert called == []


def test_handle_trade_bad_data():
    stream = _make_stream()
    called = []
    stream.on_tick = lambda *a: called.append(a)

    # Missing required keys — should not raise
    stream._handle_trades(["CON.F.US.ENQ.M26", [{"foo": "bar"}]])
    assert called == []


def test_handle_trade_non_dict_arg():
    stream = _make_stream()
    called = []
    stream.on_tick = lambda *a: called.append(a)

    # First arg is not a dict — trades list contains non-dict
    stream._handle_trades(["CON.F.US.ENQ.M26", ["not-a-dict"]])
    assert called == []


# ---------------------------------------------------------------------------
# _handle_user_trade
# ---------------------------------------------------------------------------

FILL_DATA = {
    "orderId": 42,
    "contractId": "CON.F.US.ENQ.M26",
    "price": 21498.75,
    "size": 1,
    "side": 0,
}


def test_handle_fill_calls_on_fill():
    stream = _make_stream()
    fills = []
    stream.on_fill = lambda f: fills.append(f)

    stream._handle_user_trade([FILL_DATA])

    assert len(fills) == 1
    assert fills[0]["orderId"] == 42
    assert fills[0]["price"] == 21498.75


def test_handle_fill_ignores_empty():
    stream = _make_stream()
    fills = []
    stream.on_fill = lambda f: fills.append(f)

    stream._handle_user_trade([])
    assert fills == []


# ---------------------------------------------------------------------------
# No callback — no crash
# ---------------------------------------------------------------------------

def test_no_callback_no_crash_tick():
    stream = _make_stream()
    assert stream.on_tick is None
    # Should not raise
    stream._handle_trades(["CON.F.US.ENQ.M26", [TICK_DATA]])


def test_no_callback_no_crash_fill():
    stream = _make_stream()
    assert stream.on_fill is None
    # Should not raise
    stream._handle_user_trade([FILL_DATA])


# ---------------------------------------------------------------------------
# _handle_position and _handle_order (log only)
# ---------------------------------------------------------------------------

def test_handle_position_no_crash():
    stream = _make_stream()
    stream._handle_position([{"contractId": "CON.F.US.ENQ.M26", "netSize": 1}])


def test_handle_position_empty_no_crash():
    stream = _make_stream()
    stream._handle_position([])


def test_handle_order_no_crash():
    stream = _make_stream()
    stream._handle_order([{"orderId": 7, "status": "Working"}])


def test_handle_order_empty_no_crash():
    stream = _make_stream()
    stream._handle_order([])


# ---------------------------------------------------------------------------
# _handle_depth
# ---------------------------------------------------------------------------

@pytest.fixture
def stream():
    return _make_stream()


def test_handle_depth_calls_on_depth(stream):
    depths = []
    stream.on_depth = lambda d: depths.append(d)
    stream._handle_depth(["CON.F.US.ENQ.M26", [{"price": 21450.0, "volume": 100, "currentVolume": 50, "type": 0, "timestamp": "2026-04-09T14:30:00Z"}]])
    assert len(depths) == 1
    assert depths[0]["price"] == 21450.0


def test_handle_depth_ignores_empty(stream):
    stream.on_depth = lambda d: None
    stream._handle_depth(["CON.F.US.ENQ.M26", []])  # no crash


def test_handle_depth_no_callback_no_crash(stream):
    stream.on_depth = None
    stream._handle_depth(["CON.F.US.ENQ.M26", [{"price": 21450.0, "volume": 100, "type": 0}]])
