"""Tests for PendingLoop — settlement sync and detection."""

from __future__ import annotations

import pytest
from unittest.mock import MagicMock

from local.mirror.pending_loop import PendingLoop, _detect_settlements


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_browser(running: bool = False):
    browser = MagicMock()
    browser.running = running
    browser.context = MagicMock() if running else None
    return browser


def _make_broadcaster():
    broadcaster = MagicMock()
    broadcaster.publish = MagicMock()
    return broadcaster


# ---------------------------------------------------------------------------
# test_initial_state
# ---------------------------------------------------------------------------


def test_initial_state():
    """PendingLoop starts with running=False and no task."""
    loop = PendingLoop(
        browser=_make_browser(),
        broadcaster=_make_broadcaster(),
        proxy_url="http://localhost:8000",
    )
    status = loop.get_status()
    assert status["running"] is False
    assert status["providers"] == {}


# ---------------------------------------------------------------------------
# test_detect_settlements_matches
# ---------------------------------------------------------------------------


def test_detect_settlements_matches():
    """_detect_settlements returns settlements when odds+stake match within tolerance."""
    db_pending = [
        {"bet_id": 1, "odds": 2.00, "stake": 100.0},
        {"bet_id": 2, "odds": 1.50, "stake": 50.0},
    ]
    history = [
        # Exact match for bet 1 — won
        {"odds": 2.00, "stake": 100.0, "status": "won", "payout": 200.0},
        # Within tolerance for bet 2 — lost (odds 5% off, stake 20% off)
        {"odds": 1.55, "stake": 42.0, "status": "lost", "payout": 0.0},
    ]
    result = _detect_settlements(db_pending, history)
    assert len(result) == 2
    assert result[0]["bet_id"] == 1
    assert result[0]["result"] == "won"
    assert result[0]["payout"] == 200.0
    assert result[1]["bet_id"] == 2
    assert result[1]["result"] == "lost"


# ---------------------------------------------------------------------------
# test_detect_settlements_no_match
# ---------------------------------------------------------------------------


def test_detect_settlements_no_match():
    """_detect_settlements returns empty list when no history entries match."""
    db_pending = [
        {"bet_id": 1, "odds": 2.00, "stake": 100.0},
    ]
    history = [
        # Odds too far off (>10%)
        {"odds": 2.50, "stake": 100.0, "status": "won", "payout": 250.0},
        # Stake too far off (>30%)
        {"odds": 2.00, "stake": 200.0, "status": "won", "payout": 400.0},
        # Pending entries should be ignored even on exact match
        {"odds": 2.00, "stake": 100.0, "status": "pending", "payout": None},
    ]
    result = _detect_settlements(db_pending, history)
    assert result == []


# ---------------------------------------------------------------------------
# test_sync_history_is_passive_defaults_false
# ---------------------------------------------------------------------------


def test_sync_history_is_passive_defaults_false(monkeypatch):
    """GenericWorkflow.__init__ leaves sync_history_is_passive False when the strategy doesn't set it."""
    from local.mirror.workflows.generic import GenericWorkflow
    import local.mirror.workflows.generic as gmod
    import local.mirror.workflows.strategies as smod
    from local.mirror.workflows.strategies import Strategy

    monkeypatch.setattr(gmod, "load_intel", lambda pid, intel_dir: None)
    monkeypatch.setattr(smod, "load_strategy", lambda pid: Strategy())

    wf = GenericWorkflow("test", "example.com")
    assert wf.sync_history_is_passive is False


# ---------------------------------------------------------------------------
# test_sync_history_is_passive_true_when_strategy_sets_flag
# ---------------------------------------------------------------------------


def test_sync_history_is_passive_true_when_strategy_sets_flag(monkeypatch):
    """GenericWorkflow.__init__ sets sync_history_is_passive=True when the strategy declares it."""
    from local.mirror.workflows.generic import GenericWorkflow
    import local.mirror.workflows.generic as gmod
    import local.mirror.workflows.strategies as smod
    from local.mirror.workflows.strategies import Strategy

    monkeypatch.setattr(gmod, "load_intel", lambda pid, intel_dir: None)
    monkeypatch.setattr(
        smod, "load_strategy", lambda pid: Strategy(sync_history_is_passive=True)
    )

    wf = GenericWorkflow("test", "example.com")
    assert wf.sync_history_is_passive is True


# ---------------------------------------------------------------------------
# test_passive_strategies_have_flag_set
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "provider_id", ["pinnacle", "kalshi", "polymarket", "cloudbet"]
)
def test_passive_strategies_have_flag_set(provider_id):
    """The four API-only strategies declare themselves passive."""
    from local.mirror.workflows.strategies import load_strategy

    strat = load_strategy(provider_id)
    assert strat is not None, f"strategy for {provider_id} did not load"
    assert strat.sync_history_is_passive is True, (
        f"{provider_id} strategy must set sync_history_is_passive=True — its "
        f"_sync_history is API-only and safe to poll on event pages"
    )


# ---------------------------------------------------------------------------
# test_dom_driven_strategy_is_not_passive
# ---------------------------------------------------------------------------


def test_dom_driven_strategy_is_not_passive():
    """Altenar strategy must NOT be flagged passive — its sync_history navigates."""
    from local.mirror.workflows.strategies import load_strategy

    strat = load_strategy("altenar")
    if strat is None:
        pytest.skip("altenar strategy not present in this checkout")
    assert strat.sync_history_is_passive is False, (
        "altenar _sync_history uses page.goto / DOM clicks; flagging it passive "
        "would let PendingLoop clobber open betslips"
    )
