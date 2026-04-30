"""Pinnacle strategy — placement-XHR response parser tests.

Ported from test_pinnacle_slip.py. The dedicated PinnacleMirrorWorkflow class
has been replaced by strategies/pinnacle.py + GenericWorkflow routing.
"""

from __future__ import annotations

from arnold.mirror.workflows.strategies.pinnacle import (
    parse_placement_response,
    parse_placement_status,
)

# ---- parse_placement_status ----


def test_parse_placement_status_success_via_wagerNumber():
    body = {"wagerNumber": 12345678}
    result = parse_placement_status(body)
    assert result["success"] is True
    assert result["error"] is None


def test_parse_placement_status_success_via_betId():
    body = {"betId": "abc123"}
    result = parse_placement_status(body)
    assert result["success"] is True


def test_parse_placement_status_failure():
    body = {"error": "STAKE_LIMIT_EXCEEDED"}
    result = parse_placement_status(body)
    assert result["success"] is False
    assert result["error"] == "STAKE_LIMIT_EXCEEDED"


def test_parse_placement_status_failure_unknown():
    body = {}
    result = parse_placement_status(body)
    assert result["success"] is False
    assert result["error"] == "unknown"


def test_parse_placement_status_failure_extracts_max_stake():
    body = {"error": "STAKE_LIMIT_EXCEEDED", "maxStake": 50.0}
    result = parse_placement_status(body)
    assert result["success"] is False
    assert result["max_stake"] == 50.0


def test_parse_placement_status_failure_extracts_max_stake_from_limits():
    body = {
        "error": "STAKE_LIMIT_EXCEEDED",
        "limits": [
            {"amount": 3.71, "type": "minRiskStake"},
            {"amount": 100.0, "type": "maxRiskStake"},
        ],
    }
    result = parse_placement_status(body)
    assert result["success"] is False
    assert result["max_stake"] == 100.0


# ---- parse_placement_response ----


def test_parse_placement_response_extracts_wagerNumber():
    assert parse_placement_response({"wagerNumber": 12345}) == "12345"


def test_parse_placement_response_extracts_betId():
    assert parse_placement_response({"betId": "abc"}) == "abc"


def test_parse_placement_response_returns_none_on_missing():
    assert parse_placement_response({}) is None
