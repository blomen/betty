"""Tests for MarketRecorder."""
import pytest
from unittest.mock import MagicMock, patch, call
from src.stocks.recorder import MarketRecorder


@pytest.fixture
def recorder():
    mock_factory = MagicMock()
    r = MarketRecorder(mock_factory)
    return r


def test_record_tick_buffers(recorder):
    recorder.record_tick(21450.25, 3, 1712678400.0)
    recorder.record_tick(21450.50, 1, 1712678401.0)
    assert len(recorder._tick_buffer) == 2


def test_record_depth_buffers(recorder):
    recorder.record_depth({"price": 21450.0, "volume": 100, "currentVolume": 50, "type": 0})
    assert len(recorder._depth_buffer) == 1
    assert recorder._depth_buffer[0]["side"] == "bid"


def test_record_depth_ask_side(recorder):
    recorder.record_depth({"price": 21451.0, "volume": 50, "currentVolume": 20, "type": 1})
    assert recorder._depth_buffer[0]["side"] == "ask"


def test_flush_clears_buffers(recorder):
    recorder.record_tick(21450.0, 1, 1712678400.0)
    recorder.record_depth({"price": 21450.0, "volume": 100, "type": 0})
    # Mock DB to avoid actual inserts
    mock_db = MagicMock()
    recorder._db_factory.return_value = mock_db
    recorder._flush_all()
    assert len(recorder._tick_buffer) == 0
    assert len(recorder._depth_buffer) == 0


def test_flush_empty_is_noop(recorder):
    recorder._flush_all()  # no crash, no DB call
    recorder._db_factory.assert_not_called()
