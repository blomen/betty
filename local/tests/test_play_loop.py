"""Tests for PlayLoop — automated betting state machine."""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock

from local.mirror.play_loop import PlayLoop, STATE_IDLE


def _make_browser():
    browser = MagicMock()
    browser.running = False
    browser.context = None
    return browser


def _make_broadcaster():
    broadcaster = MagicMock()
    broadcaster.publish = MagicMock()
    return broadcaster


def _make_loop() -> PlayLoop:
    return PlayLoop(
        browser=_make_browser(),
        broadcaster=_make_broadcaster(),
        proxy_url="https://148.251.40.251",
    )


def _make_bet(provider_id: str, edge_pct: float = 5.0) -> dict:
    return {
        "bet_id": 1,
        "provider_id": provider_id,
        "event_name": "Arsenal vs Chelsea",
        "market": "1x2",
        "outcome": "home",
        "odds": 2.10,
        "fair_odds": 2.00,
        "edge_pct": edge_pct,
        "stake": 50.0,
    }


# ---------------------------------------------------------------------------
# test_initial_state
# ---------------------------------------------------------------------------

def test_initial_state():
    """PlayLoop starts in idle state with no current bet."""
    loop = _make_loop()
    assert loop.state == STATE_IDLE
    assert loop.current_bet is None


# ---------------------------------------------------------------------------
# test_load_batch_filters_funded
# ---------------------------------------------------------------------------

def test_load_batch_filters_funded():
    """load_batch keeps only bets whose provider has balance > 0."""
    loop = _make_loop()
    batch = [
        _make_bet("pinnacle", edge_pct=8.0),
        _make_bet("altenar", edge_pct=5.0),
        _make_bet("kambi", edge_pct=3.0),
    ]
    balances = {
        "pinnacle": 500.0,
        "altenar": 100.0,
        "kambi": 0.0,         # unfunded — should be excluded
    }
    loop.load_batch(batch, balances)
    status = loop.get_status()

    assert status["queue_total"] == 2
    assert status["queue_remaining"] == 2

    # Bets should be sorted by edge desc (pinnacle first)
    provider_ids = [b["provider_id"] for b in loop._queue]
    assert provider_ids == ["pinnacle", "altenar"]


# ---------------------------------------------------------------------------
# test_unfunded_excluded
# ---------------------------------------------------------------------------

def test_unfunded_excluded():
    """Providers with zero or missing balance are excluded from the queue."""
    loop = _make_loop()
    batch = [
        _make_bet("betconstruct", edge_pct=10.0),
        _make_bet("tipwin", edge_pct=7.0),
        _make_bet("coolbet", edge_pct=4.0),
    ]
    balances = {
        "betconstruct": 0.0,   # zero — excluded
        # tipwin missing entirely — excluded
        "coolbet": 250.0,      # funded
    }
    loop.load_batch(batch, balances)
    status = loop.get_status()

    assert status["queue_total"] == 1
    assert status["queue_remaining"] == 1
    assert loop._queue[0]["provider_id"] == "coolbet"
