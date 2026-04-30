"""Pinnacle DOM-scrape history parser — covers row → HistoryEntry mapping."""

from __future__ import annotations

import pytest


def _row(**kwargs):
    """Build a dict simulating a Pinnacle history row as produced by the JS scrape."""
    base = {
        "provider_bet_id": "W123456",
        "event_name": "Real Madrid vs Barcelona",
        "market": "Money Line",
        "outcome": "Real Madrid",
        "odds": "1.85",
        "stake": "100.00",
        "status": "WON",
        "payout": "185.00",
    }
    base.update(kwargs)
    return base


def test_parse_won_bet_maps_to_history_entry():
    from arnold.mirror.workflows.pinnacle import _parse_pinnacle_dom_row

    entry = _parse_pinnacle_dom_row(_row())

    assert entry is not None
    assert entry.provider_bet_id == "W123456"
    assert entry.status == "won"
    assert entry.odds == pytest.approx(1.85)
    assert entry.stake == pytest.approx(100.0)
    assert entry.payout == pytest.approx(185.0)


def test_parse_lost_bet():
    from arnold.mirror.workflows.pinnacle import _parse_pinnacle_dom_row

    entry = _parse_pinnacle_dom_row(_row(status="LOST", payout="0.00"))
    assert entry is not None
    assert entry.status == "lost"
    assert entry.payout == 0.0


def test_parse_void_bet():
    from arnold.mirror.workflows.pinnacle import _parse_pinnacle_dom_row

    entry = _parse_pinnacle_dom_row(_row(status="REFUNDED", payout="100.00"))
    assert entry is not None
    assert entry.status == "void"


def test_parse_unknown_status_returns_none():
    from arnold.mirror.workflows.pinnacle import _parse_pinnacle_dom_row

    entry = _parse_pinnacle_dom_row(_row(status="PENDING", payout="0.00"))
    assert entry is None


def test_parse_malformed_numbers_returns_none():
    from arnold.mirror.workflows.pinnacle import _parse_pinnacle_dom_row

    entry = _parse_pinnacle_dom_row(_row(odds="not-a-number"))
    assert entry is None


def test_parse_swedish_currency_suffix():
    """Stakes/payouts with 'kr' or 'SEK' suffix must parse cleanly."""
    from arnold.mirror.workflows.pinnacle import _parse_pinnacle_dom_row

    entry = _parse_pinnacle_dom_row(_row(stake="100,00 kr", payout="185,00 SEK", odds="1,85"))
    assert entry is not None
    assert entry.stake == pytest.approx(100.0)
    assert entry.payout == pytest.approx(185.0)
    assert entry.odds == pytest.approx(1.85)


def test_parse_drops_row_with_no_id_and_no_event():
    """Belt-and-suspenders: a row with neither bet id nor event name is noise."""
    from arnold.mirror.workflows.pinnacle import _parse_pinnacle_dom_row

    entry = _parse_pinnacle_dom_row(_row(provider_bet_id="", event_name=""))
    assert entry is None


def test_parse_keeps_row_with_event_but_no_id():
    """If bet id is missing but event name is populated, keep the row.

    Reconciler can fuzzy-match by event name even without a stable id.
    """
    from arnold.mirror.workflows.pinnacle import _parse_pinnacle_dom_row

    entry = _parse_pinnacle_dom_row(_row(provider_bet_id="", event_name="Real Madrid vs Barcelona"))
    assert entry is not None
    assert entry.provider_bet_id == ""
    assert entry.event_name == "Real Madrid vs Barcelona"
