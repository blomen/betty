# ComeOn Extraction Speed + Scheduler Resilience

**Date:** 2026-03-20
**Status:** Implemented (Sections 1-2 complete; Section 3 abandoned — validation failed)
**Scope:** Scheduler auto-recovery, starvation prevention, concurrent ComeOn extraction

## Problem Statement

Three related issues affecting ComeOn and browser_soft extraction:

1. **ComeOn dead since March 16.** The watchdog permanently killed the schedule after consecutive failures (Cloudflare added that day). No auto-recovery path exists — only a server restart can revive it.

2. **Semaphore starvation.** All 6 browser_soft providers compete for a single `asyncio.Semaphore(1)`. No fairness mechanism — faster providers (888sport 30s, tipwin 70s) can starve slower ones (comeon 700s, 10bet 550s). The watchdog's stale threshold (3 hours) is too generous to catch this.

3. **ComeOn extraction takes 700-900s.** Sequential league page scraping (15 leagues × 5 sports × ~5s each) makes it the slowest browser provider and the biggest semaphore hog.

## Design

### 1. Scheduler Auto-Recovery

**Current behavior:** Watchdog marks a provider as `permanently_failed` after 3 consecutive failures, cancels the task, and never restarts it.

**New behavior:** After marking a provider as permanently failed, schedule revival attempts with exponential backoff.

```
consecutive_failures >= 3
  → mark permanently_failed, cancel task, set reviving=True
  → schedule revival after backoff (30min, 2hr, 6hr)
  → on revival attempt:
      if success → _provider_loop resets consecutive_failures on its own (line 273)
                  → clear reviving flag
      if failure → increment revival_attempts, schedule next revival
  → if revival_attempts >= MAX_REVIVAL_ATTEMPTS (default: 3) → truly dead until restart
```

**Implementation in `scheduler.py`:**

Add to `ProviderSchedule` dataclass:
```python
revival_attempts: int = 0
reviving: bool = False  # Prevents duplicate revival tasks
```

Revival cooldown with exponential backoff:
```python
REVIVAL_BACKOFFS = [1800, 7200, 21600]  # 30min, 2hr, 6hr
```

Add to `_watchdog_loop`, after permanently-failed block:
```python
# Schedule revival for permanently failed providers
if not schedule.running and schedule.consecutive_failures >= 3 and not schedule.reviving:
    if schedule.revival_attempts < MAX_REVIVAL_ATTEMPTS:
        schedule.reviving = True
        backoff = REVIVAL_BACKOFFS[min(schedule.revival_attempts, len(REVIVAL_BACKOFFS) - 1)]
        asyncio.create_task(self._attempt_revival(schedule, backoff))
```

New method `_attempt_revival`:
```python
async def _attempt_revival(self, schedule: ProviderSchedule, backoff: int):
    try:
        await asyncio.sleep(backoff)
        logger.info(
            f"[Watchdog] Attempting revival #{schedule.revival_attempts + 1} "
            f"for '{schedule.provider_id}' (backoff was {backoff}s)"
        )
        schedule.revival_attempts += 1
        # Do NOT reset consecutive_failures here — _provider_loop does it on success (line 273)
        schedule.running = True
        await self._restart_schedule(schedule)
        # reviving stays True until first successful extraction clears it
    except asyncio.CancelledError:
        logger.info(f"[Watchdog] Revival cancelled for '{schedule.provider_id}'")
        schedule.reviving = False
```

In `_provider_loop` success path (after line 273), add:
```python
if schedule.reviving:
    schedule.reviving = False
    schedule.revival_attempts = 0
    logger.info(f"[Scheduler:{schedule.provider_id}] Revival successful — back to normal")
```

**Cleanup in `stop_provider()` and `stop_all()`:** Cancel any pending revival sleep:
```python
schedule.reviving = False
```

### 2. Browser FIFO Lock

**Current behavior:** Bare `asyncio.Semaphore(1)` — no FIFO ordering guarantee among waiters.

**New behavior:** Replace with `asyncio.Lock()`, which guarantees FIFO ordering of waiters in CPython's asyncio implementation. This is a one-line fix that provides the same serialization with deterministic fairness.

**Implementation:**

```python
# Before (scheduler.py line 79):
self._browser_semaphore = asyncio.Semaphore(1)

# After:
self._browser_lock = asyncio.Lock()
```

Usage change in `_provider_loop` (line 265):
```python
# Before:
async with self._browser_semaphore:

# After:
async with self._browser_lock:
```

**Starvation detection** (add to `_watchdog_loop`):
```python
# Detect browser providers that haven't completed within 2x their interval
if schedule.category == "browser_soft" and schedule.last_completed:
    starvation_threshold = schedule.interval_seconds * 2
    elapsed = (now - schedule.last_completed).total_seconds()
    if elapsed > starvation_threshold:
        logger.critical(
            f"[Watchdog] Browser provider '{provider_id}' starving — "
            f"last completed {elapsed:.0f}s ago (threshold: {starvation_threshold:.0f}s)"
        )
```

### 3. Concurrent DOM Extraction for ComeOn

**Current behavior:** For each sport, leagues are scraped sequentially — one page at a time. A comment at line 351-352 of comeon_multileague.py says "ComeOn's SPA only renders fully on the active page — new tabs don't hydrate the React app reliably." This was written during the WS era and likely no longer applies to DOM scraping, where each `context.new_page()` + `page.goto()` creates a fresh, independent SPA instance.

**Validation result (2026-03-20): FAILED.** Tested with 2 concurrent Camoufox pages navigating to different league URLs. SPA does not render `[data-at="game-card"]` on concurrent pages even with 30s timeouts and staggered navigation. The existing sequential approach is confirmed necessary. **Section 3 abandoned.**

**New behavior (if validation passes):** After league discovery, open N concurrent pages (default 4) and scrape leagues in parallel batches.

**Flow change in `_extract_single_sport`:**

```
BEFORE (sequential):
  for league in leagues:
      page.goto(league.url)
      events += scrape_league_page(page)

AFTER (concurrent):
  for batch in chunk(leagues, concurrent_count):
      pages = [context.new_page() for _ in batch]
      tasks = [scrape_league_on_page(pages[i], batch[i]) for i in range(len(batch))]
      results = await asyncio.gather(*tasks)
      events += flatten(results)
      for p in pages: await p.close()
```

**New method `_scrape_league_concurrent`:**
```python
async def _scrape_league_concurrent(
    self, context, leagues: list[dict], sport: str, sport_timeout_remaining: float
) -> list[StandardEvent]:
    """Scrape multiple leagues concurrently using separate browser pages."""
    concurrent = self.config.get("concurrent_leagues", 4)
    all_events = []

    for batch in _chunk(leagues, concurrent):
        pages = []
        try:
            # Stagger page opens by 1s to avoid Cloudflare rate-limit spike
            for league in batch:
                page = await context.new_page()
                pages.append((page, league))
                await asyncio.sleep(1.0)

            # Scrape all pages concurrently
            tasks = [
                self._scrape_single_league(page, league, sport)
                for page, league in pages
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            for result in results:
                if isinstance(result, Exception):
                    logger.warning(f"[comeon] League scrape failed: {result}")
                else:
                    all_events.extend(result)
        finally:
            for page, _ in pages:
                await page.close()

    return all_events
```

**`_scrape_single_league`** — extracted from current sequential code:
```python
async def _scrape_single_league(self, page, league: dict, sport: str) -> list[StandardEvent]:
    """Scrape a single league on a dedicated page."""
    url = f"{self.site_url}/sv{league['href']}"
    await page.goto(url, wait_until="domcontentloaded", timeout=15000)
    await page.wait_for_selector('[data-at="game-card"]', timeout=10000)
    # Run existing JS_SCRAPE_ALL_MARKETS
    raw = await page.evaluate(JS_SCRAPE_ALL_MARKETS, {...})
    return self._parse_league_results(raw, sport, league)
```

**Key constraints:**
- Reuse existing Camoufox browser context — `context.new_page()` shares fingerprint and cookies
- Warm-up page stays alive for league discovery (not used for scraping)
- 1s stagger between page opens to avoid triggering Cloudflare rate limits
- `asyncio.gather` with `return_exceptions=True` — one league failure doesn't kill the batch
- Fall back to sequential if `context.new_page()` fails (e.g., browser resource limit) or if validation test shows SPA doesn't hydrate concurrently

**Config (providers.yaml):**
```yaml
comeon:
  concurrent_leagues: 4  # Already exists, now wired up
```

**Expected performance (conservative 2-2.5x estimate):**
- Current: ~700s (sequential, 5 sports with capped leagues)
- With 4 concurrent: ~280-350s (accounting for shared CPU/network contention)
- Net: **~2-2.5x speedup**, comeon drops from biggest semaphore hog to mid-tier

### 4. Error Handling & Fallbacks

**Concurrent page failure:**
- If `context.new_page()` raises (browser resource limit), fall back to sequential scraping for remaining leagues
- Log warning: `"[comeon] Concurrent page limit reached, falling back to sequential"`
- This ensures no regression — worst case is current performance

**SPA hydration failure (validation test fails):**
- If the 2-page concurrent test shows `[data-at="game-card"]` doesn't render on background pages, keep `concurrent_leagues: 1` (sequential) and skip Section 3 entirely
- The scheduler fixes (Sections 1-2) are still valuable independently

**Revival failure:**
- Each revival attempt gets one full extraction cycle
- If it fails, `consecutive_failures` increments again via `_provider_loop`, and the next watchdog tick schedules the next revival with longer backoff
- After 3 failed revivals, the provider stays dead until server restart
- Log CRITICAL: `"[Watchdog] Provider '{id}' exhausted all {MAX_REVIVAL_ATTEMPTS} revival attempts"`

**Starvation alert:**
- Watchdog logs CRITICAL when starvation detected
- No auto-corrective action (bumping priority could cause cascading issues)
- The FIFO lock prevents starvation by design — the alert is a safety net

## Files Modified

| File | Change |
|------|--------|
| `backend/src/pipeline/scheduler.py` | Auto-recovery with exponential backoff, `asyncio.Lock` FIFO, starvation detection |
| `backend/src/providers/comeon_multileague.py` | Concurrent league scraping (gated on validation test) |
| `backend/src/config/providers.yaml` | Wire up `concurrent_leagues: 4` |

## Testing Strategy

- **Scheduler auto-recovery:** Unit test — mock 3 consecutive failures → verify revival fires after backoff → verify `consecutive_failures` resets only on success → verify revival stops after MAX_REVIVAL_ATTEMPTS
- **FIFO lock:** Unit test — verify `asyncio.Lock` serves waiters in acquisition order
- **Concurrent scraping validation:** Manual test — open 2 Camoufox pages to different ComeOn league URLs → confirm `[data-at="game-card"]` renders on both
- **Concurrent scraping integration:** Integration test with 2 league URLs → verify both scraped and merged
- **Fallback:** Test that sequential mode kicks in when `new_page()` raises

## Non-Goals

- Changing the browser_soft interval (60min) — separate tuning concern
- Adding more browser_soft providers — the lock handles any count
- WS/RSocket interception — no REST API exists; DOM scraping is the proven approach
- Changing ComeOn's supported_sports list — already optimized
