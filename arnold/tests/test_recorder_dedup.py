"""Kalshi recorder dedup — a settled position lingering in the provider feed
must NOT be re-inserted (the duplication death spiral, 2026-05-20 audit).

The Polymarket half of these tests moved to backend/tests/test_recorder_dedup.py
when the Polymarket recorder went server-side."""

from __future__ import annotations

import asyncio


class _Resp:
    status_code = 201
    text = ""


def _async(value):
    async def _coro():
        return value

    return _coro()


def test_kalshi_sync_skips_settled_position(monkeypatch):
    """Dedup against all recorded tickers — a settled ticker still in the
    portfolio feed is skipped, not re-inserted."""
    from arnold.mirror.recorders import kalshi_api

    ticker = "KXNQ-26-T1"
    pos = kalshi_api.RecoveredPosition(
        provider_id="kalshi",
        provider_bet_id=ticker,
        event_name="Team A vs Team B",
        outcome_name="Team A",
        odds=2.0,
        stake=10.0,
        currency="USD",
        raw={},
    )
    monkeypatch.setattr(kalshi_api, "fetch_open_positions", lambda: _async([pos]))
    posted: list = []

    async def api_post(payload):
        posted.append(payload)
        return _Resp()

    result = asyncio.run(
        kalshi_api.sync(
            api_post,
            fetch_events=lambda: _async([]),
            fetch_db_pending=lambda: _async([]),
            fetch_known_ids=lambda: _async([ticker]),
        )
    )
    assert result.inserted == 0
    assert result.skipped_dup == 1
    assert posted == []


def test_kalshi_sync_fails_closed(monkeypatch):
    """fetch_known_ids None → skip insert."""
    from arnold.mirror.recorders import kalshi_api

    pos = kalshi_api.RecoveredPosition(
        provider_id="kalshi",
        provider_bet_id="KXNQ-26-T2",
        event_name="A vs B",
        outcome_name="A",
        odds=2.0,
        stake=5.0,
        currency="USD",
        raw={},
    )
    monkeypatch.setattr(kalshi_api, "fetch_open_positions", lambda: _async([pos]))
    posted: list = []

    async def api_post(payload):
        posted.append(payload)
        return _Resp()

    result = asyncio.run(
        kalshi_api.sync(
            api_post,
            fetch_events=lambda: _async([]),
            fetch_db_pending=lambda: _async([]),
            fetch_known_ids=lambda: _async(None),
        )
    )
    assert result.inserted == 0
    assert posted == []
