"""Tests for KambiWorkflow and related bet namespace helpers."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from local.mirror.play_loop import _bet_ns


def test_bet_ns_kambi_event_id_does_not_collide_with_canonical():
    """kambi_event_id comes from provider_meta.event_id, not canonical event_id."""
    bet = {
        "event_id": "canonical-uuid-1234",
        "market": "1x2",
        "outcome": "home",
        "provider_meta": {
            "event_id": "99887766",  # Kambi eventId
            "outcome_id": "111222333",
            "betoffer_id": "555666",
        },
    }
    ns = _bet_ns(bet)
    assert ns.event_id == "canonical-uuid-1234"  # canonical preserved
    assert ns.kambi_event_id == "99887766"  # Kambi-specific
    assert ns.kambi_outcome_id == "111222333"  # Kambi-specific


def test_bet_ns_kambi_fields_empty_when_no_provider_meta():
    bet = {"event_id": "uuid", "market": "1x2", "outcome": "home"}
    ns = _bet_ns(bet)
    assert ns.kambi_event_id == ""
    assert ns.kambi_outcome_id == ""


from local.mirror.workflows.kambi import _parse_graphql_balance


def test_parse_graphql_balance_standard():
    data = {"data": {"viewer": {"user": {"balance": {"totalAmount": 1076.50, "currency": "SEK"}}}}}
    assert _parse_graphql_balance(data) == 1076.50


def test_parse_graphql_balance_array_wrapped():
    """LeoVegas sometimes returns a list with one item."""
    data = [{"data": {"viewer": {"user": {"balance": {"totalAmount": 250.0, "currency": "SEK"}}}}}]
    assert _parse_graphql_balance(data) == 250.0


def test_parse_graphql_balance_missing_returns_negative_one():
    assert _parse_graphql_balance(None) == -1
    assert _parse_graphql_balance({}) == -1
    assert _parse_graphql_balance({"data": {}}) == -1


def test_parse_graphql_balance_zero_balance():
    data = {"data": {"viewer": {"user": {"balance": {"totalAmount": 0.0, "currency": "SEK"}}}}}
    assert _parse_graphql_balance(data) == 0.0
