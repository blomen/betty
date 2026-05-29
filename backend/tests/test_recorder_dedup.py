"""Polymarket recorder dedup — a settled position lingering in the provider
feed must NOT be re-inserted (the duplication death spiral, 2026-05-20 audit).

The recorder runs server-side (src/recorders/server_poller.py); the Kalshi
half of these tests lives in arnold/tests/test_recorder_dedup.py."""

from __future__ import annotations

import asyncio

from src.recorders import polymarket_api


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


# ── Fix A: cid backfill instead of re-insert ──
# An arb_counter leg placed via Polymarket CLOB bypasses the placement intercept
# → recorded with provider_bet_id=None and (for un-extracted markets) blank
# event_id/outcome. Neither the cid dedup nor the (event_id, outcome) dedup can
# see it, so the recovery sync re-inserts it as a fresh row (live: poly bet 627
# → 810 re-record 9 days later). sync() must match the incoming position to the
# cidless pending row by title and BACKFILL the conditionId onto it, not insert.


def test_poly_sync_backfills_cid_onto_cidless_pending(monkeypatch):
    cid = "0x" + "d" * 64
    pos = polymarket_api.RecoveredPosition(
        provider_id="polymarket",
        provider_bet_id=cid,
        event_name="Maja Chwalinska vs Carole Monnet",
        outcome_name="Maja Chwalinska",
        odds=8.19,
        stake=4.03,
        currency="USDC",
        raw={},
    )
    monkeypatch.setattr(polymarket_api, "fetch_open_positions", lambda wallet: _async([pos]))
    posted: list = []
    patched: list = []

    async def api_post(payload):
        posted.append(payload)
        return _Resp()

    async def api_patch(bet_id, payload):
        patched.append((bet_id, payload))
        return _Resp()

    db_pending = [
        {
            "id": 627,
            "provider_bet_id": "",  # CLOB placement, no cid captured
            "event_name": "Maja Chwalinska vs Carole Monnet",
            "outcome": "",
            "odds": 8.33,
            "stake": 4.03,
        }
    ]
    result = asyncio.run(
        polymarket_api.sync(
            "0xwallet",
            api_post,
            fetch_events=lambda: _async([]),
            fetch_db_pending=lambda: _async(db_pending),
            api_patch=api_patch,
            fetch_known_ids=lambda: _async([]),
        )
    )
    assert result.inserted == 0
    assert posted == []
    assert len(patched) == 1
    assert patched[0][0] == 627
    assert patched[0][1]["provider_bet_id"] == cid
    assert result.skipped_dup == 1


def test_poly_sync_does_not_backfill_when_title_ambiguous(monkeypatch):
    """Two cidless pending bets share the title — can't tell which the position
    belongs to. Fall through to normal insert rather than mis-backfill."""
    cid = "0x" + "e" * 64
    pos = polymarket_api.RecoveredPosition(
        provider_id="polymarket",
        provider_bet_id=cid,
        event_name="Team A vs Team B",
        outcome_name="Team A",
        odds=2.0,
        stake=10.0,
        currency="USDC",
        raw={},
    )
    monkeypatch.setattr(polymarket_api, "fetch_open_positions", lambda wallet: _async([pos]))
    patched: list = []

    async def api_post(payload):
        return _Resp()

    async def api_patch(bet_id, payload):
        patched.append((bet_id, payload))
        return _Resp()

    db_pending = [
        {"id": 1, "provider_bet_id": "", "event_name": "Team A vs Team B", "outcome": "", "stake": 10.0, "odds": 2.0},
        {"id": 2, "provider_bet_id": "", "event_name": "Team A vs Team B", "outcome": "", "stake": 10.0, "odds": 2.0},
    ]
    result = asyncio.run(
        polymarket_api.sync(
            "0xwallet",
            api_post,
            fetch_events=lambda: _async([]),
            fetch_db_pending=lambda: _async(db_pending),
            api_patch=api_patch,
            fetch_known_ids=lambda: _async([]),
        )
    )
    # ambiguous → no backfill; normal (unmatched) insert proceeds
    assert patched == []
    assert result.inserted == 1


# ── Fix B: settle outcome guards (trade + resolved-position) ──
# Trades and positions carry the outcome side; the SELL-trade and resolved-
# position settle signals must not settle a bet off the OPPOSITE side's entry.
# Lenient: when the bet has no resolvable outcome (or the entry omits one),
# fall back to cid-only matching (no regression). The REDEEM signal carries no
# side data (outcomeIndex=999, outcome="") so it is intentionally NOT guarded.

_CID = "0x" + "f" * 64


def _settle_env(monkeypatch, *, trades=None, positions=None, redeems=None, market=None):
    monkeypatch.setattr(polymarket_api, "fetch_recent_trades", lambda wallet: _async(trades or []))
    monkeypatch.setattr(polymarket_api, "fetch_open_positions", lambda wallet: _async(positions or []))
    monkeypatch.setattr(polymarket_api, "fetch_redeem_activity", lambda wallet: _async(redeems or []))
    monkeypatch.setattr(polymarket_api, "fetch_market_resolution", lambda cid: _async(market))


def _away_bet():
    # outcome=away, away team is Monnet; a HOME-side entry must not settle it.
    return {
        "id": 99,
        "provider_bet_id": _CID,
        "outcome": "away",
        "home_team": "Maja Chwalinska",
        "away_team": "Carole Monnet",
        "stake": 4.03,
        "odds": 8.19,
    }


def test_settle_sell_trade_opposite_side_does_not_settle(monkeypatch):
    sell_home = {
        "side": "SELL",
        "price": 0.99,  # winning sell
        "size": 30.0,
        "conditionId": _CID,
        "outcome": "Maja Chwalinska",  # HOME side — not our away bet
    }
    _settle_env(monkeypatch, trades=[sell_home])
    settled: list = []

    async def api_settle(bet_id, res, payout):
        settled.append((bet_id, res, payout))
        return _Resp()

    out = asyncio.run(polymarket_api.settle("0xwallet", api_settle, lambda: _async([_away_bet()])))
    assert settled == []
    assert out["won"] == 0 and out["lost"] == 0


def test_settle_sell_trade_matching_side_settles(monkeypatch):
    sell_away = {
        "side": "SELL",
        "price": 0.99,
        "size": 30.0,
        "conditionId": _CID,
        "outcome": "Carole Monnet",  # AWAY side — our bet
    }
    _settle_env(monkeypatch, trades=[sell_away])
    settled: list = []

    async def api_settle(bet_id, res, payout):
        settled.append((bet_id, res, payout))
        return _Resp()

    out = asyncio.run(polymarket_api.settle("0xwallet", api_settle, lambda: _async([_away_bet()])))
    assert out["won"] == 1
    assert settled and settled[0][1] == "won"


def test_settle_resolved_position_opposite_side_does_not_settle(monkeypatch):
    """A resolved HOME position (lost price) must not mark our AWAY bet lost."""
    home_pos = polymarket_api.RecoveredPosition(
        provider_id="polymarket",
        provider_bet_id=_CID,
        event_name="Maja Chwalinska vs Carole Monnet",
        outcome_name="Maja Chwalinska",
        odds=2.0,
        stake=4.03,
        currency="USDC",
        raw={"redeemable": True, "curPrice": 0.0},
    )
    _settle_env(monkeypatch, positions=[home_pos])
    settled: list = []

    async def api_settle(bet_id, res, payout):
        settled.append((bet_id, res, payout))
        return _Resp()

    out = asyncio.run(polymarket_api.settle("0xwallet", api_settle, lambda: _async([_away_bet()])))
    assert settled == []
    assert out["lost"] == 0
