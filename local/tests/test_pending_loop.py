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


# ---------------------------------------------------------------------------
# test_sync_provider_skips_event_page_for_dom_driven
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sync_provider_skips_event_page_for_dom_driven(monkeypatch):
    """A non-passive workflow whose tab is on /event/ must NOT have sync_history called."""
    from unittest.mock import AsyncMock
    import local.mirror.workflows as wfmod

    page = MagicMock()
    page.url = "https://altenar.example.com/event/123"

    workflow = MagicMock()
    workflow.sync_history_is_passive = False
    workflow.find_tab = AsyncMock(return_value=page)
    workflow.check_login = AsyncMock(return_value=True)
    workflow.sync_history = AsyncMock(return_value=[])

    monkeypatch.setattr(wfmod, "get_workflow", lambda pid: workflow)

    loop = PendingLoop(
        browser=_make_browser(running=True),
        broadcaster=_make_broadcaster(),
        proxy_url="http://localhost:8000",
    )
    await loop._sync_provider("altenar", [])

    workflow.sync_history.assert_not_called()


# ---------------------------------------------------------------------------
# test_sync_provider_proceeds_on_event_page_for_passive
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sync_provider_proceeds_on_event_page_for_passive(monkeypatch):
    """A passive workflow whose tab is on /event/ DOES get sync_history called."""
    from unittest.mock import AsyncMock
    import local.mirror.workflows as wfmod
    import local.mirror.pending_loop as pl

    page = MagicMock()
    page.url = "https://pinnacle.se/sports/123/event/456"

    workflow = MagicMock()
    workflow.sync_history_is_passive = True
    workflow.find_tab = AsyncMock(return_value=page)
    workflow.check_login = AsyncMock(return_value=True)
    workflow.sync_history = AsyncMock(return_value=[])

    monkeypatch.setattr(wfmod, "get_workflow", lambda pid: workflow)
    # reconcile_and_publish reaches into the tunnel client — stub it.
    monkeypatch.setattr(
        pl, "reconcile_and_publish", AsyncMock(return_value=0), raising=False
    )

    loop = PendingLoop(
        browser=_make_browser(running=True),
        broadcaster=_make_broadcaster(),
        proxy_url="http://localhost:8000",
    )
    # _record_unknown_open_bets posts to the tunnel; stub it so we don't need real HTTP.
    loop._record_unknown_open_bets = AsyncMock(return_value=None)

    await loop._sync_provider("pinnacle", [])

    workflow.sync_history.assert_called_once()


# ---------------------------------------------------------------------------
# test_refresh_balances_skips_event_page_for_dom_driven
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_refresh_balances_skips_event_page_for_dom_driven(monkeypatch):
    """_refresh_balances must NOT call sync_balance on a non-passive workflow on /event/."""
    from unittest.mock import AsyncMock
    import local.mirror.workflows as wfmod

    page = MagicMock()
    page.url = "https://altenar.example.com/event/123"

    workflow = MagicMock()
    workflow.sync_history_is_passive = False
    workflow.check_login = AsyncMock(return_value=True)
    workflow.sync_balance = AsyncMock(return_value=1234.0)

    monkeypatch.setattr(wfmod, "get_workflow", lambda pid: workflow)

    browser = _make_browser(running=True)
    browser.context.pages = [page]
    browser._detect_provider = MagicMock(return_value="altenar")

    loop = PendingLoop(
        browser=browser,
        broadcaster=_make_broadcaster(),
        proxy_url="http://localhost:8000",
    )
    loop._post_balance = AsyncMock(return_value=None)

    await loop._refresh_balances()

    workflow.sync_balance.assert_not_called()


# ---------------------------------------------------------------------------
# test_refresh_balances_proceeds_on_event_page_for_passive
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_refresh_balances_proceeds_on_event_page_for_passive(monkeypatch):
    """_refresh_balances DOES call sync_balance on a passive workflow on /event/."""
    from unittest.mock import AsyncMock
    import local.mirror.workflows as wfmod

    page = MagicMock()
    page.url = "https://pinnacle.se/sports/123/event/456"

    workflow = MagicMock()
    workflow.sync_history_is_passive = True
    workflow.check_login = AsyncMock(return_value=True)
    workflow.sync_balance = AsyncMock(return_value=2506.0)

    monkeypatch.setattr(wfmod, "get_workflow", lambda pid: workflow)

    browser = _make_browser(running=True)
    browser.context.pages = [page]
    browser._detect_provider = MagicMock(return_value="pinnacle")

    loop = PendingLoop(
        browser=browser,
        broadcaster=_make_broadcaster(),
        proxy_url="http://localhost:8000",
    )
    loop._post_balance = AsyncMock(return_value=None)

    await loop._refresh_balances()

    workflow.sync_balance.assert_called_once()
    loop._post_balance.assert_called_once_with("pinnacle", 2506.0)


# ---------------------------------------------------------------------------
# Fast positions-poll (polymarket near-instant recording)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fast_poll_syncs_polymarket_when_tab_open(monkeypatch):
    """_fast_poll runs the per-provider sync for polymarket when its tab is open."""
    from unittest.mock import AsyncMock
    import local.mirror.workflows as wfmod

    page = MagicMock()
    page.url = "https://polymarket.com/event/foo"
    workflow = MagicMock()
    workflow.find_tab = AsyncMock(return_value=page)
    monkeypatch.setattr(wfmod, "get_workflow", lambda pid: workflow)

    loop = PendingLoop(
        browser=_make_browser(running=True),
        broadcaster=_make_broadcaster(),
        proxy_url="http://localhost:8000",
    )
    loop._fetch_pending = AsyncMock(return_value={"polymarket": []})
    loop._sync_provider = AsyncMock(return_value=None)

    await loop._fast_poll()

    loop._sync_provider.assert_called_once()
    assert loop._sync_provider.call_args[0][0] == "polymarket"


@pytest.mark.asyncio
async def test_fast_poll_noop_when_no_tab(monkeypatch):
    """No polymarket tab → no DB fetch and no sync (lazy, cheap idle tick)."""
    from unittest.mock import AsyncMock
    import local.mirror.workflows as wfmod

    workflow = MagicMock()
    workflow.find_tab = AsyncMock(return_value=None)
    monkeypatch.setattr(wfmod, "get_workflow", lambda pid: workflow)

    loop = PendingLoop(
        browser=_make_browser(running=True),
        broadcaster=_make_broadcaster(),
        proxy_url="http://localhost:8000",
    )
    loop._fetch_pending = AsyncMock(return_value={})
    loop._sync_provider = AsyncMock(return_value=None)

    await loop._fast_poll()

    loop._fetch_pending.assert_not_called()
    loop._sync_provider.assert_not_called()


@pytest.mark.asyncio
async def test_sync_provider_skips_when_locked():
    """Per-pid lock: a second concurrent sync for the same provider is skipped."""
    import asyncio as _aio
    from unittest.mock import AsyncMock

    loop = PendingLoop(
        browser=_make_browser(running=True),
        broadcaster=_make_broadcaster(),
        proxy_url="http://localhost:8000",
    )
    loop._sync_provider_locked = AsyncMock(return_value=None)
    # Simulate an in-flight sync by pre-acquiring the provider's lock.
    lock = _aio.Lock()
    await lock.acquire()
    loop._sync_locks["polymarket"] = lock

    await loop._sync_provider("polymarket", [])

    loop._sync_provider_locked.assert_not_called()
    lock.release()
