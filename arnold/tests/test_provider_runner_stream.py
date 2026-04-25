"""ProviderRunner consumes SlipOddsStream for live value-bet edge."""

from __future__ import annotations

from unittest.mock import MagicMock

from arnold.mirror.provider_runner import ProviderRunner


def _make_runner() -> ProviderRunner:
    return ProviderRunner(
        provider_id="pinnacle",
        browser=MagicMock(running=True, context=MagicMock(pages=[]), provider_data={}),
        broadcaster=MagicMock(),
        proxy_url="https://x.test",
        pop_bet=lambda: None,
        block_event_market=lambda b: None,
        is_blocked=lambda b: False,
        placed_today={},
    )


def test_provider_runner_initializes_slip_stream_attribute():
    runner = _make_runner()
    assert runner._slip_stream is None


def test_provider_runner_stop_handles_no_active_stream():
    """stop() must be safe to call when no stream has been started."""
    runner = _make_runner()
    runner.stop()  # should not raise


def test_provider_runner_stop_stops_active_stream():
    """stop() must stop any active SlipOddsStream."""
    runner = _make_runner()
    fake_stream = MagicMock()
    fake_stream.running = True
    runner._slip_stream = fake_stream
    runner.stop()
    fake_stream.stop.assert_called_once()
