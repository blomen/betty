# ComeOn Extraction Speed + Scheduler Resilience — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix scheduler auto-recovery so dead providers revive themselves, add FIFO fairness to the browser semaphore, and parallelize ComeOn's league scraping for ~2-2.5x speedup.

**Architecture:** Three independent changes to the extraction pipeline: (1) add revival logic to `ProviderSchedule` + watchdog in scheduler.py, (2) swap `asyncio.Semaphore(1)` for `asyncio.Lock()` in scheduler.py, (3) refactor ComeOn's sequential league loop into concurrent batches using multiple Camoufox pages.

**Tech Stack:** Python 3.10+ / asyncio / Playwright / Camoufox / pytest

**Spec:** `docs/superpowers/specs/2026-03-20-comeon-scheduler-resilience-design.md`

---

## File Structure

| File | Responsibility |
|------|---------------|
| `backend/src/pipeline/scheduler.py` | ProviderSchedule dataclass + auto-recovery + FIFO lock + starvation watchdog |
| `backend/src/providers/comeon_multileague.py` | Concurrent league scraping via multiple Camoufox pages |
| `backend/src/config/providers.yaml` | `concurrent_leagues: 4` wiring (already exists, no change needed) |
| `backend/tests/test_scheduler_recovery.py` | Unit tests for auto-recovery + FIFO lock |
| `backend/tests/test_comeon_concurrent.py` | Unit tests for concurrent league batching |

---

## Task 1: Add revival fields to ProviderSchedule

**Files:**
- Modify: `backend/src/pipeline/scheduler.py:22-35`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_scheduler_recovery.py`:

```python
"""Tests for scheduler auto-recovery and FIFO browser lock."""
import asyncio
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from src.pipeline.scheduler import ProviderSchedule


def test_provider_schedule_has_revival_fields():
    """ProviderSchedule should have revival_attempts and reviving fields."""
    schedule = ProviderSchedule(
        provider_id="test", category="browser_soft", interval_seconds=3600
    )
    assert schedule.revival_attempts == 0
    assert schedule.reviving is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_scheduler_recovery.py::test_provider_schedule_has_revival_fields -v`
Expected: FAIL — `ProviderSchedule` has no `reviving` field

- [ ] **Step 3: Add revival fields to ProviderSchedule**

In `backend/src/pipeline/scheduler.py`, add two fields to the `ProviderSchedule` dataclass (after line 35):

```python
@dataclass
class ProviderSchedule:
    """Schedule state for a single provider (or grouped sharp providers)."""
    provider_id: str              # Single provider or "sharp" for grouped
    category: str                 # "sharp", "api_soft", "browser_soft"
    interval_seconds: int         # Cooldown AFTER completion
    providers: list[str] | None = None  # Only for grouped (sharp): list of providers
    running: bool = False
    task: Optional[asyncio.Task] = field(default=None, repr=False)
    last_completed: Optional[datetime] = None
    run_count: int = 0
    last_error: Optional[str] = None
    last_duration: Optional[float] = None
    consecutive_failures: int = 0
    revival_attempts: int = 0
    reviving: bool = False
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_scheduler_recovery.py::test_provider_schedule_has_revival_fields -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/src/pipeline/scheduler.py backend/tests/test_scheduler_recovery.py
git commit -m "feat(scheduler): add revival fields to ProviderSchedule"
```

---

## Task 2: Replace browser semaphore with FIFO Lock

**Files:**
- Modify: `backend/src/pipeline/scheduler.py:79` (init), `backend/src/pipeline/scheduler.py:265` (usage)
- Test: `backend/tests/test_scheduler_recovery.py`

- [ ] **Step 1: Write the failing test**

Add to `backend/tests/test_scheduler_recovery.py`:

```python
@pytest.mark.asyncio
async def test_browser_lock_is_fifo():
    """asyncio.Lock guarantees FIFO ordering of waiters."""
    lock = asyncio.Lock()
    order = []

    async def acquire(label: str, delay: float = 0):
        await asyncio.sleep(delay)
        async with lock:
            order.append(label)
            await asyncio.sleep(0.01)  # Hold lock briefly

    # Lock is held first, then A and B enqueue in order
    async with lock:
        task_a = asyncio.create_task(acquire("A", 0.01))
        task_b = asyncio.create_task(acquire("B", 0.02))
        await asyncio.sleep(0.05)  # Let both enqueue

    await asyncio.gather(task_a, task_b)
    assert order == ["A", "B"], f"Expected FIFO order, got {order}"
```

- [ ] **Step 2: Run test to verify it passes (this validates asyncio.Lock FIFO behavior)**

Run: `cd backend && python -m pytest tests/test_scheduler_recovery.py::test_browser_lock_is_fifo -v`
Expected: PASS (this is a validation test, not TDD — we're confirming asyncio.Lock behavior)

- [ ] **Step 3: Replace Semaphore with Lock in scheduler.py**

In `backend/src/pipeline/scheduler.py`:

Line 79 — change:
```python
# Before:
self._browser_semaphore = asyncio.Semaphore(1)
# After:
self._browser_lock = asyncio.Lock()
```

Line 265 — change:
```python
# Before:
async with self._browser_semaphore:
# After:
async with self._browser_lock:
```

- [ ] **Step 4: Run existing tests to verify no regression**

Run: `cd backend && python -m pytest tests/ -v --timeout=30 -x`
Expected: PASS

**Note:** `backend/src/pipeline/pool_manager.py` has its own `_browser_semaphore` on the `PoolManager` class — this is a different semaphore on a different class and is NOT affected by this change. Only `scheduler.py` references `self._browser_semaphore`.

- [ ] **Step 5: Commit**

```bash
git add backend/src/pipeline/scheduler.py backend/tests/test_scheduler_recovery.py
git commit -m "fix(scheduler): replace Semaphore with FIFO Lock for browser providers"
```

---

## Task 3: Implement auto-recovery in watchdog

**Files:**
- Modify: `backend/src/pipeline/scheduler.py` — watchdog_loop (lines 449-520), new `_attempt_revival` method, `_provider_loop` success path (line 273), `stop_provider` (line 329)
- Test: `backend/tests/test_scheduler_recovery.py`

- [ ] **Step 1: Write the failing test for revival scheduling**

Add to `backend/tests/test_scheduler_recovery.py`:

```python
@pytest.mark.asyncio
async def test_revival_scheduled_after_permanent_failure():
    """Watchdog should schedule revival for permanently failed providers."""
    from src.pipeline.scheduler import ExtractionScheduler

    scheduler = ExtractionScheduler()
    schedule = ProviderSchedule(
        provider_id="test_provider",
        category="browser_soft",
        interval_seconds=3600,
        consecutive_failures=3,
        running=False,  # Watchdog already killed it
    )
    scheduler._schedules["test_provider"] = schedule

    # Verify revival gets triggered
    with patch.object(scheduler, '_attempt_revival', new_callable=AsyncMock) as mock_revival:
        # Simulate one watchdog tick
        await scheduler._check_schedules_once()

        assert schedule.reviving is True
        # _attempt_revival should have been scheduled (via create_task)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_scheduler_recovery.py::test_revival_scheduled_after_permanent_failure -v`
Expected: FAIL — `_check_schedules_once` and `_attempt_revival` don't exist yet

- [ ] **Step 3: Add constants and `_attempt_revival` method**

Add constants after line 66 of `scheduler.py`:

```python
    WATCHDOG_STALE_MULTIPLIER = 3
    MAX_REVIVAL_ATTEMPTS = 3
    REVIVAL_BACKOFFS = [1800, 7200, 21600]  # 30min, 2hr, 6hr
```

Add `_attempt_revival` method (after `_restart_schedule`, ~line 167):

```python
    async def _attempt_revival(self, schedule: ProviderSchedule, backoff: int):
        """Attempt to revive a permanently failed provider after cooldown."""
        try:
            await asyncio.sleep(backoff)
            logger.info(
                f"[Watchdog] Attempting revival #{schedule.revival_attempts + 1} "
                f"for '{schedule.provider_id}' (backoff was {backoff}s)"
            )
            schedule.revival_attempts += 1
            schedule.running = True
            await self._restart_schedule(schedule)
        except asyncio.CancelledError:
            logger.info(f"[Watchdog] Revival cancelled for '{schedule.provider_id}'")
            schedule.reviving = False
```

- [ ] **Step 4: Extract watchdog check into `_check_schedules_once` and add revival logic**

Refactor `_watchdog_loop` to extract the inner loop body into `_check_schedules_once()` for testability.

In the existing permanently-failed block (lines 468-484), add revival scheduling after the `continue`:

```python
    async def _check_schedules_once(self):
        """Single watchdog tick — check all schedules for issues.

        IMPORTANT: This is a refactoring of the existing _watchdog_loop inner body.
        All existing checks MUST be preserved. The full method body follows.
        """
        now = datetime.now(timezone.utc)
        for provider_id, schedule in self._schedules.items():
            # ── NEW: Revival scheduling for permanently failed providers ──
            if schedule.consecutive_failures >= 3 and not schedule.running:
                if schedule.reviving:
                    continue  # Revival already in progress
                if schedule.revival_attempts >= self.MAX_REVIVAL_ATTEMPTS:
                    continue  # Exhausted all attempts
                # Schedule revival
                schedule.reviving = True
                backoff = self.REVIVAL_BACKOFFS[
                    min(schedule.revival_attempts, len(self.REVIVAL_BACKOFFS) - 1)
                ]
                logger.info(
                    f"[Watchdog] Scheduling revival #{schedule.revival_attempts + 1} "
                    f"for '{provider_id}' in {backoff}s"
                )
                asyncio.create_task(self._attempt_revival(schedule, backoff))
                continue

            # ── EXISTING (modified): Mark permanently failed after 3+ consecutive failures ──
            # Added `and not schedule.reviving` guard to prevent re-killing a reviving provider
            # whose consecutive_failures hasn't reset yet (it resets on first success in _provider_loop)
            if schedule.consecutive_failures >= 3 and schedule.running and not schedule.reviving:
                logger.critical(
                    f"[Watchdog] Provider '{provider_id}' has {schedule.consecutive_failures} "
                    f"consecutive failures — marking as permanently failed"
                )
                schedule.running = False
                if schedule.task:
                    schedule.task.cancel()
                    schedule.task = None
                update_provider_state(provider_id, {
                    "running": False,
                    "permanently_failed": True,
                    "last_error": schedule.last_error,
                    "consecutive_failures": schedule.consecutive_failures,
                })
                continue

            # ── EXISTING: Check if the asyncio task is still alive ──
            if schedule.running and (schedule.task is None or schedule.task.done()):
                exc = schedule.task.exception() if schedule.task and not schedule.task.cancelled() else None
                logger.critical(
                    f"[Watchdog] Provider '{provider_id}' task is DEAD "
                    f"(running={schedule.running}, "
                    f"task_done={schedule.task.done() if schedule.task else 'None'}, "
                    f"exception={exc}). Forcing restart..."
                )
                await self._restart_schedule(schedule)
                continue

            # ── EXISTING: Check if the schedule is overdue (stale) ──
            if schedule.running and schedule.last_completed:
                stale_threshold = schedule.interval_seconds * self.WATCHDOG_STALE_MULTIPLIER
                elapsed = (now - schedule.last_completed).total_seconds()
                if elapsed > stale_threshold:
                    logger.critical(
                        f"[Watchdog] Provider '{provider_id}' is STALE — "
                        f"last completed {elapsed:.0f}s ago (threshold: {stale_threshold:.0f}s). "
                        f"run_count={schedule.run_count}"
                    )

            # ── EXISTING: Check if a schedule that should be running hasn't started yet ──
            if schedule.running and schedule.run_count == 0:
                if schedule.last_completed is None:
                    # Expected on first startup — only warn if it's been a while
                    pass  # Handled by stale check above once interval elapses
```

Update `_watchdog_loop` to call `_check_schedules_once`:

```python
    async def _watchdog_loop(self):
        while True:
            try:
                await self._check_schedules_once()
            except Exception as e:
                logger.error(f"[Watchdog] Error: {e}", exc_info=True)
            await asyncio.sleep(60)
```

- [ ] **Step 5: Add revival success reset to `_provider_loop`**

In `_provider_loop`, after line 273 (`schedule.consecutive_failures = 0`), add:

```python
                # Clear revival state on first successful extraction
                if schedule.reviving:
                    schedule.reviving = False
                    schedule.revival_attempts = 0
                    logger.info(f"[Scheduler:{schedule.provider_id}] Revival successful — back to normal")
```

- [ ] **Step 6: Add revival cleanup to `stop_provider` and `stop_all`**

In `stop_provider` (line 329), add after the task cancel:

```python
        schedule.reviving = False
```

In `stop_all` (line 522), add a loop BEFORE the existing provider stop loop to cancel any reviving providers (which have `running=False` and would be skipped by the existing loop):

```python
    def stop_all(self):
        """Stop all running schedules."""
        # Cancel any pending revival tasks first
        for provider_id, schedule in self._schedules.items():
            if schedule.reviving:
                schedule.reviving = False
        for provider_id in list(self._schedules.keys()):
            if self._schedules[provider_id].running:
                self.stop_provider(provider_id)
        # ... rest of existing stop_all (boosts, cleanup, etc.) ...
```

- [ ] **Step 7: Run tests**

Run: `cd backend && python -m pytest tests/test_scheduler_recovery.py -v`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add backend/src/pipeline/scheduler.py backend/tests/test_scheduler_recovery.py
git commit -m "feat(scheduler): add auto-recovery with exponential backoff for failed providers"
```

---

## Task 4: Add starvation detection to watchdog

**Files:**
- Modify: `backend/src/pipeline/scheduler.py` — `_check_schedules_once`
- Test: `backend/tests/test_scheduler_recovery.py`

- [ ] **Step 1: Write the failing test**

Add to `backend/tests/test_scheduler_recovery.py`:

```python
@pytest.mark.asyncio
async def test_starvation_detection_logs_critical(caplog):
    """Watchdog should log CRITICAL when a browser provider hasn't run in 2x its interval."""
    from src.pipeline.scheduler import ExtractionScheduler
    import logging

    scheduler = ExtractionScheduler()
    schedule = ProviderSchedule(
        provider_id="slow_provider",
        category="browser_soft",
        interval_seconds=3600,
        running=True,
        last_completed=datetime.now(timezone.utc) - timedelta(seconds=8000),  # > 2x interval
    )
    # Give it a mock task that looks alive
    schedule.task = MagicMock()
    schedule.task.done.return_value = False
    scheduler._schedules["slow_provider"] = schedule

    with caplog.at_level(logging.CRITICAL):
        await scheduler._check_schedules_once()

    assert any("starving" in r.message for r in caplog.records), \
        f"Expected CRITICAL starvation log, got: {[r.message for r in caplog.records]}"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_scheduler_recovery.py::test_starvation_detection_logs_critical -v`
Expected: FAIL — no "starving" log yet

- [ ] **Step 3: Add starvation detection to `_check_schedules_once`**

In the `_check_schedules_once` method, add after the existing stale check (around the section that checks `schedule.running and schedule.last_completed`):

```python
            # Starvation detection for browser providers
            if (schedule.category == "browser_soft" and schedule.running
                    and schedule.last_completed):
                starvation_threshold = schedule.interval_seconds * 2
                elapsed = (now - schedule.last_completed).total_seconds()
                if elapsed > starvation_threshold:
                    logger.critical(
                        f"[Watchdog] Browser provider '{provider_id}' starving — "
                        f"last completed {elapsed:.0f}s ago "
                        f"(threshold: {starvation_threshold:.0f}s)"
                    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_scheduler_recovery.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/src/pipeline/scheduler.py backend/tests/test_scheduler_recovery.py
git commit -m "feat(scheduler): add starvation detection for browser providers"
```

---

## Task 5: Concurrent DOM extraction — validation test

**Files:**
- Create: `backend/tests/test_comeon_concurrent.py`
- Modify: `backend/src/providers/comeon_multileague.py`

This task validates that ComeOn's SPA hydrates on concurrent Camoufox pages. If this fails, skip Task 6 and keep sequential scraping.

- [ ] **Step 1: Write the validation test**

Create `backend/tests/test_comeon_concurrent.py`:

```python
"""
Validate that ComeOn's SPA hydrates on concurrent Camoufox pages.

Run manually (requires Camoufox + network):
    cd backend && python -m pytest tests/test_comeon_concurrent.py -v -s --timeout=120

If this test FAILS: abandon concurrent approach, keep sequential.
If this test PASSES: proceed with Task 6.
"""
import asyncio
import pytest

# Mark as manual — not part of regular test suite
pytestmark = pytest.mark.skipif(
    True,  # Change to False to run manually
    reason="Manual validation test — requires Camoufox + network"
)


@pytest.mark.asyncio
async def test_concurrent_pages_hydrate():
    """Open 2 ComeOn league pages concurrently and verify both render game cards."""
    try:
        from camoufox.async_api import AsyncCamoufox
    except ImportError:
        pytest.skip("camoufox not installed")

    LEAGUE_URLS = [
        "https://www.comeon.com/sv/sportsbook/sport/1-fotboll/leagues/1-england-premier-league",
        "https://www.comeon.com/sv/sportsbook/sport/1-fotboll/leagues/3-england-championship",
    ]

    async with AsyncCamoufox(headless=True, geoip=True, humanize=0.2, os="windows") as browser:
        # Warm up — pass Cloudflare
        page0 = await browser.new_page()
        await page0.goto("https://www.comeon.com/sv", wait_until="domcontentloaded", timeout=30000)
        await asyncio.sleep(3)
        # Dismiss cookies
        try:
            btn = await page0.query_selector('#onetrust-accept-btn-handler')
            if btn:
                await btn.click()
                await asyncio.sleep(1)
        except Exception:
            pass
        await page0.close()

        # Open 2 pages with 1s stagger
        pages = []
        for url in LEAGUE_URLS:
            p = await browser.new_page()
            pages.append(p)
            await asyncio.sleep(1.0)

        # Navigate both
        async def navigate_and_check(page, url):
            await page.goto(url, wait_until="domcontentloaded", timeout=15000)
            await page.wait_for_selector('[data-at="game-card"]', timeout=15000)
            count = await page.evaluate(
                "() => document.querySelectorAll('[data-at=\"game-card\"]').length"
            )
            return count

        results = await asyncio.gather(
            navigate_and_check(pages[0], LEAGUE_URLS[0]),
            navigate_and_check(pages[1], LEAGUE_URLS[1]),
            return_exceptions=True,
        )

        for p in pages:
            await p.close()

    for i, result in enumerate(results):
        assert not isinstance(result, Exception), (
            f"Page {i} failed: {result}. "
            f"SPA does NOT hydrate on concurrent pages — abandon concurrent approach."
        )
        assert result > 0, f"Page {i} rendered 0 game cards"

    print(f"VALIDATION PASSED: Page 0 = {results[0]} cards, Page 1 = {results[1]} cards")
```

- [ ] **Step 2: Run the validation test manually**

Edit the `skipif` to `False`, then run:
```bash
cd backend && python -m pytest tests/test_comeon_concurrent.py -v -s --timeout=120
```

**If FAIL:** Stop here. Update the spec to note concurrent pages don't work. Skip Task 6. Revert `skipif` to `True`. Commit.
**If PASS:** Continue to Task 6. Revert `skipif` to `True`. Commit.

- [ ] **Step 3: Commit validation result**

```bash
git add backend/tests/test_comeon_concurrent.py
git commit -m "test(comeon): add concurrent page hydration validation test"
```

---

## Task 6: Implement concurrent league scraping (only if Task 5 passed)

**Files:**
- Modify: `backend/src/providers/comeon_multileague.py:255-389` (`_extract_single_sport`)
- Test: `backend/tests/test_comeon_concurrent.py`

- [ ] **Step 1: Write the unit test**

Add to `backend/tests/test_comeon_concurrent.py`:

```python
import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from src.providers.comeon_multileague import ComeOnMultiLeagueRetriever, _chunk


def test_chunk_splits_evenly():
    """_chunk should split a list into batches of size n."""
    items = [1, 2, 3, 4, 5, 6, 7]
    result = list(_chunk(items, 3))
    assert result == [[1, 2, 3], [4, 5, 6], [7]]


def test_chunk_single_batch():
    items = [1, 2]
    result = list(_chunk(items, 4))
    assert result == [[1, 2]]


@pytest.mark.asyncio
async def test_scrape_league_concurrent_merges_results():
    """Concurrent scraping should merge events from all pages."""
    config = {"site_url": "https://www.comeon.com", "id": "comeon",
              "domain": "comeon.com", "concurrent_leagues": 2}
    retriever = ComeOnMultiLeagueRetriever(config)

    mock_event_1 = MagicMock(id="ev1")
    mock_event_2 = MagicMock(id="ev2")
    mock_event_3 = MagicMock(id="ev3")

    # Mock _scrape_single_league to return different events per league
    call_count = 0
    async def mock_scrape(page, league, sport):
        nonlocal call_count
        call_count += 1
        if league["name"] == "League A":
            return [mock_event_1]
        elif league["name"] == "League B":
            return [mock_event_2, mock_event_3]
        return []

    retriever._scrape_single_league = mock_scrape

    # Mock context.new_page() and page.close()
    mock_context = AsyncMock()
    mock_pages = [AsyncMock(), AsyncMock()]
    mock_context.new_page.side_effect = mock_pages

    leagues = [
        {"name": "League A", "href": "/leagues/1-a", "id": 1},
        {"name": "League B", "href": "/leagues/2-b", "id": 2},
    ]

    result = await retriever._scrape_league_concurrent(
        context=mock_context, leagues=leagues, sport="football",
        sport_timeout_remaining=300,
    )

    assert len(result) == 3
    assert mock_context.new_page.call_count == 2
    # Both pages should be closed
    for p in mock_pages:
        p.close.assert_awaited_once()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_comeon_concurrent.py::test_chunk_splits_evenly tests/test_comeon_concurrent.py::test_scrape_league_concurrent_merges_results -v`
Expected: FAIL — `_chunk` and `_scrape_league_concurrent` don't exist

- [ ] **Step 3: Add `_chunk` helper and `_scrape_single_league` method**

At the top of `comeon_multileague.py` (after imports, before the class), add:

```python
from typing import Iterator

def _chunk(items: list, n: int) -> Iterator[list]:
    """Split a list into batches of size n."""
    for i in range(0, len(items), n):
        yield items[i:i + n]
```

Add `_scrape_single_league` as a new method on `ComeOnMultiLeagueRetriever` (extract from current loop body in `_extract_single_sport`, lines 369-382):

```python
    async def _scrape_single_league(
        self, page, league_info: dict, sport: str
    ) -> List[StandardEvent]:
        """Scrape a single league on a dedicated page."""
        try:
            events = await scrape_league_page(
                page=page,
                league_href=league_info["href"],
                site_url=self.site_url,
                sport=sport,
                league_name=league_info["name"],
                provider_id=self.provider_id,
            )
            return events
        except Exception as e:
            logger.debug(f"[{self.provider_id}] {league_info['name']}: scrape failed: {e}")
            return []
```

- [ ] **Step 4: Add `_scrape_league_concurrent` method**

Add to `ComeOnMultiLeagueRetriever`:

```python
    async def _scrape_league_concurrent(
        self, context, leagues: list[dict], sport: str, sport_timeout_remaining: float
    ) -> List[StandardEvent]:
        """Scrape multiple leagues concurrently using separate browser pages."""
        concurrent = self.config.get("concurrent_leagues", 4)
        all_events: List[StandardEvent] = []

        for batch in _chunk(leagues, concurrent):
            pages = []
            try:
                # Stagger page opens by 1s to avoid Cloudflare rate-limit spike
                for league in batch:
                    page = await context.new_page()
                    pages.append((page, league))
                    if len(pages) < len(batch):
                        await asyncio.sleep(1.0)

                # Scrape all pages in batch concurrently
                tasks = [
                    self._scrape_single_league(page, league, sport)
                    for page, league in pages
                ]
                results = await asyncio.gather(*tasks, return_exceptions=True)

                for i, result in enumerate(results):
                    if isinstance(result, Exception):
                        logger.warning(
                            f"[{self.provider_id}] {pages[i][1]['name']}: "
                            f"concurrent scrape failed: {result}"
                        )
                    else:
                        all_events.extend(result)
            except Exception as e:
                # new_page() failed — fall back to sequential for remaining
                logger.warning(
                    f"[{self.provider_id}] Concurrent page limit reached ({e}), "
                    f"falling back to sequential"
                )
                for league in batch[len(pages):]:
                    # Use the main page for remaining leagues
                    if self._page:
                        events = await self._scrape_single_league(
                            self._page, league, sport
                        )
                        all_events.extend(events)
                break
            finally:
                for page, _ in pages:
                    try:
                        await page.close()
                    except Exception:
                        pass

        return all_events
```

- [ ] **Step 5: Run tests**

Run: `cd backend && python -m pytest tests/test_comeon_concurrent.py -v -k "not hydrate"`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add backend/src/providers/comeon_multileague.py backend/tests/test_comeon_concurrent.py
git commit -m "feat(comeon): add concurrent league scraping method"
```

---

## Task 7: Wire concurrent scraping into `_extract_single_sport`

**Files:**
- Modify: `backend/src/providers/comeon_multileague.py:255-389`

- [ ] **Step 1: Update `_extract_single_sport` to use concurrent scraping**

Replace the sequential loop (lines 350-389) with the concurrent approach. The key change is in Step 6 of the method — replace the `for league_info in filtered_leagues` loop:

```python
        # Step 6: Scrape league pages
        concurrent_count = self.config.get("concurrent_leagues", 4)
        sport_timeout = self.config.get("sport_timeout", 360)
        sport_start = time.time()

        if concurrent_count > 1 and self._camoufox_browser:
            # Concurrent: use multiple pages from the Camoufox browser context
            all_events = await self._scrape_league_concurrent(
                context=self._camoufox_browser,
                leagues=filtered_leagues,
                sport=sport_normalized,
                sport_timeout_remaining=sport_timeout - (time.time() - sport_start),
            )
        else:
            # Sequential fallback (no Camoufox or concurrent_leagues=1)
            all_events = []
            leagues_scraped = 0
            for league_info in filtered_leagues:
                elapsed = time.time() - sport_start
                if elapsed > sport_timeout * 0.85:
                    logger.warning(
                        f"[{self.provider_id}] {sport_normalized}: time-budget exit at "
                        f"{elapsed:.0f}s ({leagues_scraped}/{len(filtered_leagues)} leagues, "
                        f"{len(all_events)} events)"
                    )
                    break

                events = await self._scrape_single_league(
                    self._page, league_info, sport_normalized
                )
                all_events.extend(events)
                leagues_scraped += 1

        logger.info(
            f"[{self.provider_id}] {sport_normalized}: "
            f"{len(all_events)} events from {len(filtered_leagues)} leagues "
            f"in {time.time() - sport_start:.0f}s"
        )
        return all_events
```

Also update the old comment at lines 350-352 to reflect the new approach:

```python
        # Step 6: Scrape league pages
        # Concurrent mode: open N pages in the Camoufox context, each scraping a different league.
        # Falls back to sequential if Camoufox unavailable or concurrent_leagues=1.
```

- [ ] **Step 2: Run all tests**

Run: `cd backend && python -m pytest tests/test_comeon_concurrent.py tests/test_scheduler_recovery.py -v`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add backend/src/providers/comeon_multileague.py
git commit -m "feat(comeon): wire concurrent scraping into extract_single_sport"
```

---

## Task 8: Final integration check

- [ ] **Step 1: Run the full test suite**

Run: `cd backend && python -m pytest tests/ -v --timeout=60 -x`
Expected: All PASS, no regressions

- [ ] **Step 2: Set concurrent_leagues to 4 for safe initial rollout**

Check `comeon.concurrent_leagues` in `backend/src/config/providers.yaml` (line ~631). It's currently set to `8`. Change to `4` for the initial rollout — we can increase later once validated in production. 8 concurrent Camoufox pages may hit memory or Cloudflare limits on the slow PC.

- [ ] **Step 3: Commit final state**

```bash
git add backend/src/pipeline/scheduler.py backend/src/providers/comeon_multileague.py backend/src/config/providers.yaml backend/tests/test_scheduler_recovery.py backend/tests/test_comeon_concurrent.py
git commit -m "feat: scheduler auto-recovery, FIFO browser lock, concurrent ComeOn extraction

- Add auto-recovery with exponential backoff (30m/2h/6h) for permanently failed providers
- Replace asyncio.Semaphore with asyncio.Lock for FIFO browser fairness
- Add starvation detection for browser providers
- Parallelize ComeOn league scraping via concurrent Camoufox pages
- Add fallback to sequential if concurrent pages fail"
```
