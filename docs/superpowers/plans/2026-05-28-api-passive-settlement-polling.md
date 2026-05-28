# API-Passive Settlement Polling Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Pinnacle / Kalshi / Polymarket / Cloudbet pending bets get auto-reconciled against provider APIs within ~60s, without the user manually navigating to history pages, while DOM-driven providers (Altenar/Gecko/Kambi) keep their current event-page DOM-clobber protection.

**Architecture:** Add `sync_history_is_passive: bool = False` to the `Strategy` dataclass and `ProviderWorkflow` base class (parallel to the existing `autonomous_placement` field). Mark the four API-only strategies as passive. Relax the two event-page guards in `PendingLoop` so they only fire for non-passive workflows. Wire `pending_loop.start()` into router setup. Reuses the existing reconcile + record-unknowns pipeline.

**Tech Stack:** Python 3.12, FastAPI, Playwright (async), pytest, unittest.mock. SSE for UI updates.

**Spec:** [docs/superpowers/specs/2026-05-28-api-passive-settlement-polling-design.md](../specs/2026-05-28-api-passive-settlement-polling-design.md)

---

## File Structure

```
local/mirror/workflows/base.py
  + class attr sync_history_is_passive: bool = False (parallel to autonomous_placement)

local/mirror/workflows/strategies/__init__.py
  + Strategy dataclass field sync_history_is_passive: bool = False

local/mirror/workflows/generic.py
  + GenericWorkflow.__init__ sets self.sync_history_is_passive from self.strategy

local/mirror/workflows/strategies/pinnacle.py
  ~ Strategy(...) constructor: sync_history_is_passive=True
  ~ module docstring (stale DOM-scrape claim)

local/mirror/workflows/strategies/kalshi.py
  ~ Strategy(...) constructor: sync_history_is_passive=True

local/mirror/workflows/strategies/polymarket.py
  ~ Strategy(...) constructor: sync_history_is_passive=True

local/mirror/workflows/strategies/cloudbet.py
  ~ Strategy(...) constructor: sync_history_is_passive=True

local/mirror/pending_loop.py
  ~ _sync_provider: has_event guard gated on workflow.sync_history_is_passive
  ~ _refresh_balances: event-page guard gated on workflow.sync_history_is_passive

local/mirror/router.py
  ~ replace "intentionally NOT started" comment block; call pending_loop.start()

local/tests/test_pending_loop.py
  + 4 unit tests for the gating behavior
  + 1 smoke test asserting the flag plumbs through GenericWorkflow
```

Strategy dataclass actually lives in `local/mirror/workflows/strategies/__init__.py:18-59`, not `base.py` (the design spec wording was loose). The base ProviderWorkflow class lives in `local/mirror/workflows/base.py:59`.

---

## Task 1: Flag plumbing — Strategy → ProviderWorkflow → GenericWorkflow

**Files:**
- Modify: `local/mirror/workflows/strategies/__init__.py:59` (add field to Strategy dataclass)
- Modify: `local/mirror/workflows/base.py:67` (add class attr to ProviderWorkflow)
- Modify: `local/mirror/workflows/generic.py:100-104` (set instance attr from strategy)
- Test: `local/tests/test_pending_loop.py` (add test for flag plumbing)

This is pure scaffolding — no behavior change yet. Just makes `workflow.sync_history_is_passive` callable.

- [ ] **Step 1: Write the failing test**

Append to `local/tests/test_pending_loop.py`:

```python
# ---------------------------------------------------------------------------
# test_sync_history_is_passive_defaults_false
# ---------------------------------------------------------------------------

def test_sync_history_is_passive_defaults_false():
    """A GenericWorkflow whose strategy doesn't set the flag exposes False."""
    from local.mirror.workflows.generic import GenericWorkflow
    from local.mirror.workflows.strategies import Strategy

    wf = GenericWorkflow.__new__(GenericWorkflow)
    wf.strategy = Strategy()  # all defaults
    # Mirror what __init__ would have done — we skip the real __init__ to
    # avoid touching intel JSON / disk in this unit test.
    wf.sync_history_is_passive = bool(
        wf.strategy and wf.strategy.sync_history_is_passive
    )
    assert wf.sync_history_is_passive is False


# ---------------------------------------------------------------------------
# test_sync_history_is_passive_true_when_strategy_sets_flag
# ---------------------------------------------------------------------------

def test_sync_history_is_passive_true_when_strategy_sets_flag():
    """A GenericWorkflow whose strategy sets the flag exposes True."""
    from local.mirror.workflows.generic import GenericWorkflow
    from local.mirror.workflows.strategies import Strategy

    wf = GenericWorkflow.__new__(GenericWorkflow)
    wf.strategy = Strategy(sync_history_is_passive=True)
    wf.sync_history_is_passive = bool(
        wf.strategy and wf.strategy.sync_history_is_passive
    )
    assert wf.sync_history_is_passive is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest local/tests/test_pending_loop.py::test_sync_history_is_passive_true_when_strategy_sets_flag -v`
Expected: FAIL with `TypeError: __init__() got an unexpected keyword argument 'sync_history_is_passive'`

- [ ] **Step 3: Add field to Strategy dataclass**

Edit `local/mirror/workflows/strategies/__init__.py`. After the existing `parse_placement_status` line (`:59`), add:

```python
    parse_placement_status: Callable | None = None  # sync (body) -> dict
    # True for strategies whose sync_history is purely page.evaluate(fetch(...))
    # — no page.goto, no DOM clicks. Safe to background-poll even while the
    # user is on an event page; the call cannot clobber an open betslip.
    # Consumed by PendingLoop to bypass its event-page skip guard.
    sync_history_is_passive: bool = False
```

- [ ] **Step 4: Add class attr to ProviderWorkflow base**

Edit `local/mirror/workflows/base.py:67`. Below the existing `autonomous_placement` line, add:

```python
    autonomous_placement: bool = False  # True for API-based providers (Pinnacle) — place_bet() called on user confirm
    sync_history_is_passive: bool = False  # True when sync_history is pure-API — safe to poll on event pages
```

- [ ] **Step 5: Set instance attr in GenericWorkflow**

Edit `local/mirror/workflows/generic.py`. Find the block at lines 100-104 that sets `self.autonomous_placement`:

```python
        # Intel JSON may declare this provider as autonomous (API-based place_bet
        # called on user confirm instead of waiting for a placement interception).
        self.autonomous_placement = bool(
            (self.intel or {}).get("autonomous_placement", False)
        )
```

After it, before the `fetch_balance` block at line 105, insert:

```python
        # Strategy-declared: True iff this provider's sync_history is purely
        # page.evaluate(fetch(...)) with no DOM mutation. Read by PendingLoop
        # to bypass the event-page skip guard. Source is the Strategy dataclass
        # (not intel JSON) because passive-ness is an implementation property
        # of _sync_history, not a config knob.
        self.sync_history_is_passive = bool(
            self.strategy and self.strategy.sync_history_is_passive
        )
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest local/tests/test_pending_loop.py::test_sync_history_is_passive_defaults_false local/tests/test_pending_loop.py::test_sync_history_is_passive_true_when_strategy_sets_flag -v`
Expected: both PASS.

- [ ] **Step 7: Commit**

```bash
git add local/mirror/workflows/strategies/__init__.py local/mirror/workflows/base.py local/mirror/workflows/generic.py local/tests/test_pending_loop.py
git commit -m "feat(mirror): add sync_history_is_passive flag plumbing"
```

---

## Task 2: Mark four strategies as passive

**Files:**
- Modify: `local/mirror/workflows/strategies/pinnacle.py:1434` (Strategy ctor) + `:1-9` (stale docstring)
- Modify: `local/mirror/workflows/strategies/kalshi.py:658` (Strategy ctor)
- Modify: `local/mirror/workflows/strategies/polymarket.py:1626` (Strategy ctor)
- Modify: `local/mirror/workflows/strategies/cloudbet.py:439` (Strategy ctor)
- Test: `local/tests/test_pending_loop.py` (parametrized loader test)

- [ ] **Step 1: Write the failing parametrized test**

Append to `local/tests/test_pending_loop.py`:

```python
# ---------------------------------------------------------------------------
# test_passive_strategies_have_flag_set
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("provider_id", ["pinnacle", "kalshi", "polymarket", "cloudbet"])
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest local/tests/test_pending_loop.py -k "test_passive_strategies_have_flag_set or test_dom_driven_strategy_is_not_passive" -v`
Expected: 4 FAIL (passive providers — flag still False) + 1 PASS (altenar).

- [ ] **Step 3: Mark Pinnacle passive + fix stale docstring**

Edit `local/mirror/workflows/strategies/pinnacle.py:1-9` — replace the module docstring:

```python
"""Pinnacle strategy — API-based balance, history, settlement, and live odds.

Overrides GenericWorkflow methods with Pinnacle-specific REST API logic:
  - scan(): read-only preview of account state (balance, pending, settled, DB diff)
  - settle_all(): API fetch of pending/settled bets → record missing → auto-settle → sync balance
  - sync_history(): API-only pull of /bets?status=unsettled + /bets?status=settled
  - check_live_price(): fetch markets → compute edge vs fair odds

Placement is intentionally NOT autonomous — the user reviews every stake
and clicks Place on the Pinnacle tab; the placement XHR is intercepted by
parse_placement_status / parse_placement_response.
"""
```

Then at `local/mirror/workflows/strategies/pinnacle.py:1434`, edit the Strategy ctor — add the flag after `parse_placement_status`:

```python
strategy = Strategy(
    check_login=_check_login,
    sync_balance=_sync_balance,
    fetch_balance=_fetch_balance,
    sync_history=_sync_history,
    navigate_to_event=_navigate_to_event,
    prep_betslip=_prep_betslip,
    check_live_price=_check_live_price,
    scan=_scan,
    settle_all=_settle_all,
    read_slip_odds=_read_slip_odds,
    read_outcome_odds_dom=_read_outcome_odds_dom,
    update_slip_stake=_update_slip_stake,
    parse_placement_response=parse_placement_response,
    parse_placement_status=parse_placement_status,
    sync_history_is_passive=True,
)
```

- [ ] **Step 4: Mark Kalshi passive**

Edit `local/mirror/workflows/strategies/kalshi.py:658`. Add flag after `parse_placement_status`:

```python
strategy = Strategy(
    check_login=_check_login,
    sync_balance=_sync_balance,
    fetch_balance=_fetch_balance,
    sync_history=_sync_history,
    navigate_to_event=_navigate_to_event,
    prep_betslip=_prep_betslip,
    check_live_price=_check_live_price,
    place_bet=_place_bet,
    parse_placement_response=_parse_placement_response,
    parse_placement_status=_parse_placement_status,
    sync_history_is_passive=True,
)
```

- [ ] **Step 5: Mark Polymarket passive**

Edit `local/mirror/workflows/strategies/polymarket.py:1626`. Add flag after `redeem_all`:

```python
strategy = Strategy(
    check_login=_check_login,
    sync_balance=_sync_balance,
    sync_history=_sync_history,
    navigate_to_event=_navigate_to_event,
    prep_betslip=_prep_betslip,
    check_live_price=_check_live_price,
    scrape_portfolio=_scrape_portfolio,
    claim_banner=_claim_banner,
    redeem_all=_redeem_all,
    sync_history_is_passive=True,
)
```

- [ ] **Step 6: Mark Cloudbet passive**

Edit `local/mirror/workflows/strategies/cloudbet.py:439`. Add flag after `navigate_to_event`:

```python
strategy = Strategy(
    check_login=_check_login,
    sync_balance=_sync_balance,
    fetch_balance=_fetch_balance,
    sync_history=_sync_history,
    navigate_to_event=_navigate_to_event,
    sync_history_is_passive=True,
)
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `pytest local/tests/test_pending_loop.py -k "test_passive_strategies_have_flag_set or test_dom_driven_strategy_is_not_passive" -v`
Expected: 5 PASS.

- [ ] **Step 8: Commit**

```bash
git add local/mirror/workflows/strategies/pinnacle.py local/mirror/workflows/strategies/kalshi.py local/mirror/workflows/strategies/polymarket.py local/mirror/workflows/strategies/cloudbet.py local/tests/test_pending_loop.py
git commit -m "feat(mirror): mark API-only strategies sync_history_is_passive"
```

---

## Task 3: PendingLoop._sync_provider respects the flag

**Files:**
- Modify: `local/mirror/pending_loop.py:352-359` (event-page skip)
- Test: `local/tests/test_pending_loop.py`

- [ ] **Step 1: Write failing tests**

Append to `local/tests/test_pending_loop.py`:

```python
# ---------------------------------------------------------------------------
# test_sync_provider_skips_event_page_for_dom_driven
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_sync_provider_skips_event_page_for_dom_driven(monkeypatch):
    """A non-passive workflow whose tab is on /event/ must NOT have sync_history called."""
    from unittest.mock import AsyncMock
    import local.mirror.pending_loop as pl

    page = MagicMock()
    page.url = "https://altenar.example.com/event/123"

    workflow = MagicMock()
    workflow.sync_history_is_passive = False
    workflow.find_tab = AsyncMock(return_value=page)
    workflow.check_login = AsyncMock(return_value=True)
    workflow.sync_history = AsyncMock(return_value=[])

    monkeypatch.setattr(pl, "get_workflow", lambda pid: workflow, raising=False)

    # _sync_provider imports get_workflow inside the function from .workflows
    import local.mirror.workflows as wfmod
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

    # _record_unknown_open_bets reaches into the tunnel client; stub it.
    async def _noop(*a, **kw): return None
    monkeypatch.setattr(pl, "reconcile_and_publish", AsyncMock(return_value=0), raising=False)

    loop = PendingLoop(
        browser=_make_browser(running=True),
        broadcaster=_make_broadcaster(),
        proxy_url="http://localhost:8000",
    )
    # Patch the instance method so we don't have to mock the HTTP client.
    loop._record_unknown_open_bets = AsyncMock(return_value=None)

    await loop._sync_provider("pinnacle", [])

    workflow.sync_history.assert_called_once()
```

- [ ] **Step 2: Install pytest-asyncio if missing**

Run: `python -c "import pytest_asyncio; print(pytest_asyncio.__version__)"`
If ImportError: `pip install pytest-asyncio` and add to `requirements.txt` / `pyproject.toml` test deps.

Also check `pyproject.toml` / `pytest.ini` for asyncio config. If none, append to `pyproject.toml` `[tool.pytest.ini_options]`:

```toml
asyncio_mode = "auto"
```

(Or mark each test `@pytest.mark.asyncio` as already done in the test code above — that works without auto mode.)

- [ ] **Step 3: Run tests to verify they fail**

Run: `pytest local/tests/test_pending_loop.py -k "test_sync_provider" -v`
Expected: `test_sync_provider_proceeds_on_event_page_for_passive` FAILS because today's code returns at line 354 regardless of the flag. The dom_driven test PASSES because that's already the current behavior.

- [ ] **Step 4: Relax the event-page guard for passive workflows**

Edit `local/mirror/pending_loop.py:352-359`. Replace:

```python
        current_url = (page.url or "").lower()
        has_event = "/event/" in current_url or "#/event/" in current_url
        if has_event:
            logger.debug(
                f"[PendingLoop] {pid} tab is on an event page ({current_url[:60]}); "
                f"skipping sync to avoid clobbering an active betslip"
            )
            return
```

With:

```python
        current_url = (page.url or "").lower()
        has_event = "/event/" in current_url or "#/event/" in current_url
        if has_event and not getattr(workflow, "sync_history_is_passive", False):
            logger.debug(
                f"[PendingLoop] {pid} tab is on an event page ({current_url[:60]}); "
                f"skipping sync to avoid clobbering an active betslip"
            )
            return
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest local/tests/test_pending_loop.py -k "test_sync_provider" -v`
Expected: both PASS.

- [ ] **Step 6: Commit**

```bash
git add local/mirror/pending_loop.py local/tests/test_pending_loop.py
git commit -m "feat(mirror): bypass _sync_provider event-page guard for passive workflows"
```

---

## Task 4: PendingLoop._refresh_balances respects the flag

**Files:**
- Modify: `local/mirror/pending_loop.py:304-305` (event-page skip)
- Test: `local/tests/test_pending_loop.py`

- [ ] **Step 1: Write failing tests**

Append to `local/tests/test_pending_loop.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest local/tests/test_pending_loop.py -k "test_refresh_balances" -v`
Expected: `test_refresh_balances_proceeds_on_event_page_for_passive` FAILS (current code `continue`s at line 290 unconditionally). The dom_driven test PASSES.

- [ ] **Step 3: Relax the event-page guard**

Edit `local/mirror/pending_loop.py`. Replace the body of `_refresh_balances` from line 289 through the end of the loop body (~line 314) — the full inner-for-loop block — with this version. The change: load `workflow` BEFORE the event-page check (so we can read its `sync_history_is_passive` attr), then gate the skip on the flag.

Find this existing block (currently at lines 289-314):

```python
        seen: set[str] = set()
        for page in list(self._browser.context.pages):
            try:
                url = page.url or ""
            except Exception:
                continue
            pid = self._browser._detect_provider(url)
            if not pid or pid in seen:
                continue
            seen.add(pid)
            url_lower = url.lower()
            # Same gate as _sync_provider — never refresh while user is on an
            # event page; a play runner may have a betslip prepped + pending
            # confirmation, and we don't want to surface a network error
            # banner mid-bet.
            if "/event/" in url_lower or "#/event/" in url_lower:
                continue
            try:
                workflow = get_workflow(pid)
                if not await workflow.check_login(page):
                    continue
                balance = await workflow.sync_balance(page)
                if balance >= 0:
                    await self._post_balance(pid, balance)
            except Exception:
                logger.debug(f"[PendingLoop] balance refresh failed for {pid}")
```

Replace it with:

```python
        seen: set[str] = set()
        for page in list(self._browser.context.pages):
            try:
                url = page.url or ""
            except Exception:
                continue
            pid = self._browser._detect_provider(url)
            if not pid or pid in seen:
                continue
            seen.add(pid)
            url_lower = url.lower()
            try:
                workflow = get_workflow(pid)
            except Exception:
                continue
            on_event = "/event/" in url_lower or "#/event/" in url_lower
            # Same gate as _sync_provider — never refresh while user is on an
            # event page UNLESS the workflow is API-passive (sync_balance is
            # pure-API for those — no risk of clobbering an open betslip).
            if on_event and not getattr(workflow, "sync_history_is_passive", False):
                continue
            try:
                if not await workflow.check_login(page):
                    continue
                balance = await workflow.sync_balance(page)
                if balance >= 0:
                    await self._post_balance(pid, balance)
            except Exception:
                logger.debug(f"[PendingLoop] balance refresh failed for {pid}")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest local/tests/test_pending_loop.py -k "test_refresh_balances" -v`
Expected: both PASS.

- [ ] **Step 5: Run the full test file to catch regressions**

Run: `pytest local/tests/test_pending_loop.py -v`
Expected: every previously-passing test still passes.

- [ ] **Step 6: Commit**

```bash
git add local/mirror/pending_loop.py local/tests/test_pending_loop.py
git commit -m "feat(mirror): bypass _refresh_balances event-page guard for passive workflows"
```

---

## Task 5: Wire pending_loop.start() into router setup

**Files:**
- Modify: `local/mirror/router.py:243-250`

No new tests — the start is a single call to existing tested code. We verify by running the mirror and watching the log.

- [ ] **Step 1: Replace the "intentionally NOT started" comment block and call start()**

Edit `local/mirror/router.py:243-250`. Replace:

```python
    play_loop = PlayLoop(browser, broadcaster, proxy_url)
    # PendingLoop is intentionally NOT started. Per the auto-nav invariant
    # the mirror is hands-off on everything except arb event-clicks — the
    # user manually navigates to provider history pages and the browser
    # interceptor catches the response. The interceptor → history_synced
    # SSE → reactive_sync helper below records any unknown pending bets +
    # reconciles settlements. Kept the instance so we can still reuse its
    # helpers (_record_unknown_open_bets / reconcile) from the reactive path.
    pending_loop = PendingLoop(browser, broadcaster, proxy_url)
```

With:

```python
    play_loop = PlayLoop(browser, broadcaster, proxy_url)
    # PendingLoop runs in the background. Each per-provider tick is gated on
    # workflow.sync_history_is_passive — DOM-driven providers (Altenar/Gecko/
    # Kambi) still skip while the tab is on an event page so their
    # sync_history's page.goto / DOM clicks can't clobber an open betslip
    # (this is the auto-nav invariant). API-passive providers (Pinnacle/
    # Kalshi/Polymarket/Cloudbet) settle every 60s regardless of where the
    # tab is parked — their _sync_history is pure page.evaluate(fetch(...))
    # and cannot disturb a betslip. Reactive sync via history_intercepted
    # still works on top of the poll for DOM-driven providers.
    pending_loop = PendingLoop(browser, broadcaster, proxy_url)
    pending_loop.start()
```

- [ ] **Step 2: Run the full pytest suite to catch any router-import regression**

Run: `pytest local/tests/ -v`
Expected: all green.

- [ ] **Step 3: Commit**

```bash
git add local/mirror/router.py
git commit -m "feat(mirror): start PendingLoop on router setup"
```

---

## Task 6: Manual verification on live system

No code changes — verifying the change actually closes the loop end-to-end.

- [ ] **Step 1: Start betty**

Run from repo root: `betty.bat`

Watch the log for `[PendingLoop] started`. If absent, check that Task 5's commit is on the running branch and `betty.bat` launched the local FastAPI from this checkout.

- [ ] **Step 2: Log in to Pinnacle in the Playwright browser**

Click the Pinnacle chip in Betty UI. Provider tab opens. Log in. Wait for the green "Logged in" pill and balance to populate.

- [ ] **Step 3: Place a small Pinnacle bet on a match starting in <30 min**

Pick an arb leg or value bet whose Pinnacle event starts soon. Place a 10-15 kr stake via the normal manual flow (click outcome on Pinnacle tab → Place → Confirm). The bet should appear in Betty's PENDING row.

- [ ] **Step 4: Navigate Pinnacle tab AWAY from history**

Click any non-history page in the Pinnacle tab (e.g. a different sport). The point is to prove settlement doesn't depend on the history tab being visible.

- [ ] **Step 5: Wait for the match to finish + Pinnacle's API to settle the bet**

(Typically a few minutes after final result.) Verify on Pinnacle's site that the bet shows as settled.

- [ ] **Step 6: Confirm Betty's UI flips PENDING → W/L within ~60s**

Watch Betty UI without clicking anywhere. Within ~60s of Pinnacle settling, the PENDING row should disappear and the bet should appear in the settled section with the correct W/L badge.

- [ ] **Step 7: Cross-check mirror log**

In betty's console, confirm there's a `[PendingLoop] syncing pinnacle` line per minute and a `[pinnacle] Settled bet #N` line for the bet that just settled. If `[PendingLoop] syncing pinnacle` does NOT appear, the Pinnacle tab may have closed or login state expired — re-check.

- [ ] **Step 8: Confirm no regressions on a DOM-driven provider**

Open an Altenar-family tab (e.g. Betinia) and navigate to an event page (`/event/...`). In the mirror log, `[PendingLoop] altenar tab is on an event page... skipping sync` should appear when its tick runs. This proves the dom-driven guard is still in effect.

---

## Self-Review Notes

Spec coverage:
- Goal 1 (auto-reconcile Pinnacle pending bets) — Tasks 1-5 deliver this. Task 6 verifies end-to-end.
- Goal 2 (DOM-driven providers unchanged) — Task 3 keeps the guard for non-passive; Task 2 explicitly tests Altenar is NOT flagged passive; Task 6 step 8 verifies live.
- Goal 3 (minimal new surface area) — only the 4 strategy ctors + 1 dataclass field + 1 class attr + 1 GenericWorkflow init line + 2 guard edits + 1 router edit. No new files, no new classes.

Type / name consistency:
- `sync_history_is_passive` (snake_case bool) used identically in Strategy dataclass, ProviderWorkflow class attr, GenericWorkflow instance attr, PendingLoop reads. ✓
- `_record_unknown_open_bets` is referenced once (in the task 3 test) as an instance method patched with AsyncMock — matches the real signature in pending_loop.py. ✓
- `_post_balance` patched in Task 4 tests — matches the real method.

No placeholders, no "TBD", no "similar to" references — every step has full code.
