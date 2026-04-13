"""Tests for KambiWorkflow and related bet namespace helpers."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from firevsports.mirror.play_loop import _bet_ns


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
