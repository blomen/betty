"""Tests for AllocationEngine envelope response (2026-04-19 redesign)."""
from unittest.mock import MagicMock, patch

import pytest

from backend.src.bankroll.allocator import AllocationEngine


class _Profile:
    id = 1
    liquid_balance = 0.0


@pytest.fixture
def engine():
    db = MagicMock()
    db.query.return_value.filter.return_value.all.return_value = []
    return AllocationEngine(db, _Profile())


def _stub_batch(builder_mock, capital_actions=None, balances=None):
    builder_mock.return_value.build.return_value = {
        "capital_plan": {"actions": capital_actions or []},
        "provider_balances": balances or {},
    }


def test_allocate_returns_envelope_keys(engine):
    with patch("backend.src.bankroll.allocator.BatchBuilder") as bb:
        _stub_batch(bb)
        result = engine.allocate(1000.0)
    assert set(result.keys()) == {
        "current_liquid",
        "deposit_input",
        "withdrawals",
        "effective_budget",
        "deposits",
        "keep_liquid",
        "recommended_total",
    }


def test_allocate_none_budget_is_unbounded(engine):
    with patch("backend.src.bankroll.allocator.BatchBuilder") as bb:
        _stub_batch(
            bb,
            capital_actions=[
                {"type": "deposit", "provider_id": "pinnacle", "amount": 2400,
                 "currency": "SEK", "unlocks": 14, "expected_ev": 980, "priority": 3},
            ],
        )
        result = engine.allocate(None)
    assert result["deposit_input"] is None
    assert result["effective_budget"] == float("inf")
    assert result["recommended_total"] == sum(d["amount_sek"] for d in result["deposits"])
    assert result["keep_liquid"] == 0


def test_allocate_withdrawals_expand_budget(engine):
    with patch("backend.src.bankroll.allocator.BatchBuilder") as bb:
        _stub_batch(
            bb,
            capital_actions=[
                {"type": "withdraw", "provider_id": "888sport", "amount": 0, "currency": "SEK"},
                {"type": "deposit", "provider_id": "pinnacle", "amount": 600,
                 "currency": "SEK", "unlocks": 3, "expected_ev": 90, "priority": 3},
            ],
            balances={"888sport": 450, "pinnacle": 0},
        )
        result = engine.allocate(200.0)
    assert result["withdrawals"][0]["provider_id"] == "888sport"
    assert result["withdrawals"][0]["amount_sek"] == 450
    assert result["effective_budget"] == 650  # 200 + 450
    assert sum(d["amount_sek"] for d in result["deposits"]) <= 650


def test_allocate_partial_tier_3_ranks_by_ev(engine):
    with patch("backend.src.bankroll.allocator.BatchBuilder") as bb:
        _stub_batch(
            bb,
            capital_actions=[
                {"type": "deposit", "provider_id": "polymarket", "amount": 500,
                 "currency": "USDC", "unlocks": 4, "expected_ev": 100, "priority": 3},
                {"type": "deposit", "provider_id": "pinnacle", "amount": 1000,
                 "currency": "SEK", "unlocks": 8, "expected_ev": 600, "priority": 3},
            ],
        )
        result = engine.allocate(1000.0)
    # Only pinnacle fits fully; polymarket should be absent or partial
    pids = [d["provider_id"] for d in result["deposits"]]
    assert "pinnacle" in pids
    assert pids[0] == "pinnacle"  # higher EV comes first


def test_allocate_surplus_becomes_keep_liquid(engine):
    with patch("backend.src.bankroll.allocator.BatchBuilder") as bb:
        _stub_batch(
            bb,
            capital_actions=[
                {"type": "deposit", "provider_id": "pinnacle", "amount": 200,
                 "currency": "SEK", "unlocks": 1, "expected_ev": 30, "priority": 3},
            ],
        )
        result = engine.allocate(5000.0)
    assert result["keep_liquid"] == 5000 - 200


def test_allocate_nothing_to_do(engine):
    with patch("backend.src.bankroll.allocator.BatchBuilder") as bb:
        _stub_batch(bb)
        result = engine.allocate(1000.0)
    assert result["deposits"] == []
    assert result["withdrawals"] == []
    assert result["recommended_total"] == 0
    assert result["keep_liquid"] == 1000


def test_withdrawal_threshold_ignores_dust(engine):
    with patch("backend.src.bankroll.allocator.BatchBuilder") as bb:
        _stub_batch(
            bb,
            capital_actions=[
                {"type": "withdraw", "provider_id": "888sport", "amount": 0, "currency": "SEK"},
            ],
            balances={"888sport": 25},  # below 50-kr threshold
        )
        result = engine.allocate(0.0)
    assert result["withdrawals"] == []
