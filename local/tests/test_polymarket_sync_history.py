"""Tests for the API-only polymarket._sync_history rewrite.

Validates that the data-api positions response is parsed into HistoryEntry
rows with the right status mapping (early-settle thresholds) and fee-adjusted
odds. Covers the wallet-not-found short-circuit, API errors, and the
empty-positions case.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from local.mirror.workflows.strategies.polymarket import (
    _poly_fee_adjusted_odds,
    _sync_history,
)


def _page_with_eval(side_effect):
    """Build a fake Playwright Page whose evaluate() returns successive values.

    side_effect is the list of awaitable return values for sequential
    page.evaluate calls (1st call → wallet lookup, 2nd call → fetch).
    """
    page = MagicMock()
    page.evaluate = AsyncMock(side_effect=side_effect)
    return page


# ---------------------------------------------------------------------------
# _poly_fee_adjusted_odds — pure math, mirrors backend formula
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "price,expected",
    [
        (0.50, 1.98),  # 1/0.5 = 2.0 → 1 + 1.0 * 0.98 = 1.98
        (0.25, 3.94),  # 1/0.25 = 4.0 → 1 + 3.0 * 0.98 = 3.94
        (0.80, 1.245),  # 1/0.8 = 1.25 → 1 + 0.25 * 0.98 = 1.245
    ],
)
def test_fee_adjusted_odds_mid_band(price, expected):
    """Fee math matches backend.recorders.polymarket_api._fee_adjusted_odds."""
    assert _poly_fee_adjusted_odds(price) == pytest.approx(expected, abs=0.001)


@pytest.mark.parametrize("price", [0.005, 0.01, 0.99, 0.995, 1.0, 0.0])
def test_fee_adjusted_odds_extremes_floor_to_1_01(price):
    """Extremes return 1.01 — matches the backend recorder floor."""
    assert _poly_fee_adjusted_odds(price) == 1.01


# ---------------------------------------------------------------------------
# _sync_history — wallet not resolvable → empty
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sync_history_returns_empty_when_no_wallet():
    """No wallet in localStorage → no fetch attempt, empty result."""
    page = _page_with_eval([None])  # wallet resolver returns null
    result = await _sync_history(page, intel=None)
    assert result == []
    # Only the wallet lookup happened; no fetch call.
    assert page.evaluate.await_count == 1


# ---------------------------------------------------------------------------
# _sync_history — pending + early-settle + skip-invalid
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sync_history_maps_positions_to_entries():
    """Open position → pending; curPrice ≥0.98 → won; curPrice ≤0.02 → lost."""
    wallet = "0x71fca29e6b31a93d262d2972c9b361af371d426d"
    positions = [
        # Mid-band open position → pending
        {
            "conditionId": "0x" + "a" * 64,
            "title": "Yankees vs Red Sox",
            "outcome": "Yankees",
            "avgPrice": 0.50,
            "size": 10.0,
            "curPrice": 0.55,
        },
        # curPrice well above WON threshold → won
        {
            "conditionId": "0x" + "b" * 64,
            "title": "Lakers vs Celtics",
            "outcome": "Lakers",
            "avgPrice": 0.40,
            "size": 25.0,
            "curPrice": 0.99,
        },
        # curPrice below LOST threshold → lost
        {
            "conditionId": "0x" + "c" * 64,
            "title": "Cubs vs Cardinals",
            "outcome": "Cubs",
            "avgPrice": 0.60,
            "size": 5.0,
            "curPrice": 0.01,
        },
        # avgPrice = 0 → skipped
        {
            "conditionId": "0x" + "d" * 64,
            "title": "Malformed",
            "outcome": "Yes",
            "avgPrice": 0,
            "size": 100.0,
            "curPrice": 0.5,
        },
        # size = 0 → skipped
        {
            "conditionId": "0x" + "e" * 64,
            "title": "Empty",
            "outcome": "No",
            "avgPrice": 0.5,
            "size": 0,
            "curPrice": 0.5,
        },
    ]
    page = _page_with_eval([wallet, positions])

    result = await _sync_history(page, intel=None)

    assert len(result) == 3, f"expected 3 entries (2 skipped), got {len(result)}"

    pending = result[0]
    assert pending.status == "pending"
    assert pending.provider_bet_id == "0x" + "a" * 64
    assert pending.event_name == "Yankees vs Red Sox"
    assert pending.outcome == "Yankees"
    assert pending.stake == pytest.approx(5.0)  # 0.5 * 10
    assert pending.payout == 0.0
    # Fee-adjusted odds = 1 + (2.0 - 1) * 0.98 = 1.98
    assert pending.odds == pytest.approx(1.98, abs=0.001)

    won = result[1]
    assert won.status == "won"
    assert won.payout == pytest.approx(25.0)  # size = 25 shares × $1

    lost = result[2]
    assert lost.status == "lost"
    assert lost.payout == 0.0


# ---------------------------------------------------------------------------
# _sync_history — API errors
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sync_history_handles_api_error_payload():
    """Fetch returns {__error: 503} → empty result, no exception."""
    page = _page_with_eval(["0x" + "f" * 40, {"__error": 503}])
    result = await _sync_history(page, intel=None)
    assert result == []


@pytest.mark.asyncio
async def test_sync_history_handles_non_list_payload():
    """Fetch returns a dict instead of a list → empty result, no crash."""
    page = _page_with_eval(["0x" + "1" * 40, {"unexpected": "shape"}])
    result = await _sync_history(page, intel=None)
    assert result == []


@pytest.mark.asyncio
async def test_sync_history_handles_empty_positions_list():
    """No open positions → empty result, no crash."""
    page = _page_with_eval(["0x" + "2" * 40, []])
    result = await _sync_history(page, intel=None)
    assert result == []
