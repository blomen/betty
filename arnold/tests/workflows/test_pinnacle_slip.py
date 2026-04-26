"""Pinnacle mirror workflow — slip read/write + placement parsing + odds conversion."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from arnold.mirror.workflows.pinnacle import PinnacleMirrorWorkflow, american_to_decimal

# ---- odds conversion ----


class TestAmericanToDecimal:
    def test_minus_100(self):
        assert american_to_decimal(-100) == pytest.approx(2.0)

    def test_plus_100(self):
        assert american_to_decimal(100) == pytest.approx(2.0)

    def test_minus_133(self):
        assert american_to_decimal(-133) == pytest.approx(1.752, abs=0.001)

    def test_plus_200(self):
        assert american_to_decimal(200) == pytest.approx(3.0)

    def test_minus_250(self):
        assert american_to_decimal(-250) == pytest.approx(1.4, abs=0.001)


# ---- read_slip_odds ----


@pytest.mark.asyncio
async def test_read_slip_odds_returns_none_when_storage_empty():
    wf = PinnacleMirrorWorkflow()
    page = MagicMock()
    page.evaluate = AsyncMock(return_value=None)
    assert await wf.read_slip_odds(page) is None


@pytest.mark.asyncio
async def test_read_slip_odds_returns_decimal_from_american():
    wf = PinnacleMirrorWorkflow()
    page = MagicMock()
    # The JS evaluator returns the American price directly (after JSON parsing in JS)
    page.evaluate = AsyncMock(return_value=-133)
    odds = await wf.read_slip_odds(page)
    assert odds == pytest.approx(1.752, abs=0.001)


@pytest.mark.asyncio
async def test_read_slip_odds_returns_none_on_evaluator_exception():
    wf = PinnacleMirrorWorkflow()
    page = MagicMock()
    page.evaluate = AsyncMock(side_effect=RuntimeError("page closed"))
    assert await wf.read_slip_odds(page) is None


# ---- update_slip_stake ----


@pytest.mark.asyncio
async def test_update_slip_stake_returns_true_on_success():
    wf = PinnacleMirrorWorkflow()
    page = MagicMock()
    page.evaluate = AsyncMock(return_value=True)
    assert await wf.update_slip_stake(page, 25.0) is True


@pytest.mark.asyncio
async def test_update_slip_stake_returns_false_on_no_input():
    wf = PinnacleMirrorWorkflow()
    page = MagicMock()
    page.evaluate = AsyncMock(return_value=False)
    assert await wf.update_slip_stake(page, 25.0) is False


@pytest.mark.asyncio
async def test_update_slip_stake_returns_false_on_exception():
    wf = PinnacleMirrorWorkflow()
    page = MagicMock()
    page.evaluate = AsyncMock(side_effect=RuntimeError("page closed"))
    assert await wf.update_slip_stake(page, 25.0) is False


# ---- parse_placement_status ----


def test_parse_placement_status_success_via_wagerNumber():
    body = {"wagerNumber": 12345678}
    result = PinnacleMirrorWorkflow.parse_placement_status(body)
    assert result["success"] is True
    assert result["error"] is None


def test_parse_placement_status_success_via_betId():
    body = {"betId": "abc123"}
    result = PinnacleMirrorWorkflow.parse_placement_status(body)
    assert result["success"] is True


def test_parse_placement_status_failure():
    body = {"error": "STAKE_LIMIT_EXCEEDED"}
    result = PinnacleMirrorWorkflow.parse_placement_status(body)
    assert result["success"] is False
    assert result["error"] == "STAKE_LIMIT_EXCEEDED"


def test_parse_placement_status_failure_unknown():
    body = {}
    result = PinnacleMirrorWorkflow.parse_placement_status(body)
    assert result["success"] is False
    assert result["error"] == "unknown"


# ---- parse_placement_response ----


def test_parse_placement_response_extracts_wagerNumber():
    assert PinnacleMirrorWorkflow.parse_placement_response({"wagerNumber": 12345}) == "12345"


def test_parse_placement_response_extracts_betId():
    assert PinnacleMirrorWorkflow.parse_placement_response({"betId": "abc"}) == "abc"


def test_parse_placement_response_returns_none_on_missing():
    assert PinnacleMirrorWorkflow.parse_placement_response({}) is None
