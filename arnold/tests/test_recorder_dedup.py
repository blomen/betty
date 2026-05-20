"""Recorder dedup — a settled position lingering in the provider feed must
NOT be re-inserted (the duplication death spiral, 2026-05-20 audit)."""

from __future__ import annotations

import asyncio

from arnold.mirror.recorders import polymarket_api


class _Resp:
    status_code = 201
    text = ""


def _async(value):
    async def _coro():
        return value

    return _coro()


def _position(cid: str):
    return polymarket_api.RecoveredPosition(
        provider_id="polymarket",
        provider_bet_id=cid,
        event_name="Team A vs Team B",
        outcome_name="Team A",
        odds=2.0,
        stake=10.0,
        currency="USDC",
        raw={},
    )


def test_poly_sync_skips_settled_position(monkeypatch):
    """conditionId is in fetch_known_ids (recorded) but NOT in db_pending
    (it settled) — must be skipped, not re-inserted."""
    cid = "0x" + "a" * 64
    monkeypatch.setattr(polymarket_api, "fetch_open_positions", lambda wallet: _async([_position(cid)]))
    posted: list = []

    async def api_post(payload):
        posted.append(payload)
        return _Resp()

    result = asyncio.run(
        polymarket_api.sync(
            "0xwallet",
            api_post,
            fetch_events=lambda: _async([]),
            fetch_db_pending=lambda: _async([]),
            fetch_known_ids=lambda: _async([cid]),
        )
    )
    assert result.inserted == 0
    assert result.skipped_dup == 1
    assert posted == []


def test_poly_sync_inserts_new_position(monkeypatch):
    """A conditionId not in fetch_known_ids IS inserted."""
    cid = "0x" + "b" * 64
    monkeypatch.setattr(polymarket_api, "fetch_open_positions", lambda wallet: _async([_position(cid)]))
    posted: list = []

    async def api_post(payload):
        posted.append(payload)
        return _Resp()

    result = asyncio.run(
        polymarket_api.sync(
            "0xwallet",
            api_post,
            fetch_events=lambda: _async([]),
            fetch_db_pending=lambda: _async([]),
            fetch_known_ids=lambda: _async([]),
        )
    )
    assert result.inserted == 1
    assert posted[0]["provider_bet_id"] == cid


def test_poly_sync_fails_closed_when_known_ids_unavailable(monkeypatch):
    """fetch_known_ids returning None = fetch failed → insert pass skipped
    entirely (never insert against an unknown dedup state)."""
    cid = "0x" + "c" * 64
    monkeypatch.setattr(polymarket_api, "fetch_open_positions", lambda wallet: _async([_position(cid)]))
    posted: list = []

    async def api_post(payload):
        posted.append(payload)
        return _Resp()

    result = asyncio.run(
        polymarket_api.sync(
            "0xwallet",
            api_post,
            fetch_events=lambda: _async([]),
            fetch_db_pending=lambda: _async([]),
            fetch_known_ids=lambda: _async(None),
        )
    )
    assert result.inserted == 0
    assert posted == []


def test_kalshi_sync_skips_settled_position(monkeypatch):
    """Same fix for the Kalshi recorder — dedup against all recorded tickers."""
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
