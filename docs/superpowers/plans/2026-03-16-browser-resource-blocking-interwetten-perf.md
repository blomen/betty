# Browser Resource Blocking + Interwetten Performance

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Speed up all browser-based extraction by blocking unnecessary resources at the base class level, and add interwetten-specific time-budget guards + detail page optimizations.

**Architecture:** Resource blocking goes in `BrowserTransport._ensure_browser()` via `context.route()` so every page created from that context automatically blocks images/fonts/analytics. Interwetten gets time-budget guards (like 10Bet/ComeOn already have) and resource blocking on detail page tabs.

**Tech Stack:** Playwright `page.route()` / `route.abort()`, Python `time.time()`

---

## Chunk 1: Universal Resource Blocking in BrowserTransport

### Task 1: Add resource blocking to BrowserTransport

**Files:**
- Modify: `backend/src/core/transport.py:235-346` (BrowserTransport class)

Resource blocking is applied at the **context** level after context creation, so all pages (including `new_page()` tabs) inherit it. We block resource types that no extractor needs: images, fonts, media. We also block known tracking domains by URL pattern.

**Note:** Stylesheets are NOT blocked by default because Hajper uses `window.getComputedStyle()` for scroll container detection. Providers can opt into CSS blocking by setting `_BLOCK_STYLESHEETS = True`.

- [ ] **Step 1: Add the blocked resource types, tracking domains, and opt-in flag as class constants**

In `BrowserTransport`, add after line 262 (`self.page = None`):

```python
# Resource types always blocked during extraction (speeds up page loads)
_BLOCKED_RESOURCE_TYPES = {"image", "font", "media"}

# Opt-in: set True in subclass or instance to also block stylesheets
# Default False because Hajper uses getComputedStyle() for scroll detection
_BLOCK_STYLESHEETS = False

# Tracking/analytics domains to block (waste bandwidth, slow page loads)
_BLOCKED_URL_PATTERNS = [
    "google-analytics.com", "googletagmanager.com",
    "googlesyndication.com", "doubleclick.net",
    "bat.bing.com", "bat.bing.net",
    "facebook.net", "facebook.com/tr",
    "truendo.com", "braze.eu", "braze.com",
    "hotjar.com", "clarity.ms",
    "sportradar.com/widgets",  # BetAssist widgets, not odds data
]
```

- [ ] **Step 2: Add the route handler method**

Add a new method to BrowserTransport:

```python
async def _setup_resource_blocking(self):
    """Block images, fonts, and tracking scripts on all pages in this context."""
    if not self.context:
        return

    blocked_types = set(self._BLOCKED_RESOURCE_TYPES)
    if self._BLOCK_STYLESHEETS:
        blocked_types.add("stylesheet")

    async def _block_unnecessary(route):
        if route.request.resource_type in blocked_types:
            await route.abort()
            return
        url = route.request.url.lower()
        for pattern in self._BLOCKED_URL_PATTERNS:
            if pattern in url:
                await route.abort()
                return
        await route.continue_()

    await self.context.route("**/*", _block_unnecessary)
    logger.debug("Resource blocking enabled for browser context (block_css=%s)", self._BLOCK_STYLESHEETS)
```

- [ ] **Step 3: Wire it into `_ensure_browser()` after context creation**

In `_ensure_browser()`, add the call after each code path that creates a context. There are three paths:

**CDP path** (after line 298 `self.page = await self.context.new_page()`):
```python
await self._setup_resource_blocking()
```

**Persistent context path** (after line 331 `self.page = ...`):
```python
await self._setup_resource_blocking()
```

**Standard launch path** (after line 340 `self.page = await self.context.new_page()`):
```python
await self._setup_resource_blocking()
```

- [ ] **Step 4: Verify no extractor depends on images/CSS/fonts**

All browser extractors parse DOM `data-*` attributes, text content, or intercept API/WS responses. None rely on rendered visuals for images/fonts/media.

**CSS exception:** Hajper uses `window.getComputedStyle()` for scroll container detection, which needs stylesheets. CSS is NOT blocked by default — providers that don't need it can opt in via `_BLOCK_STYLESHEETS = True`.

- [ ] **Step 5: Run existing interwetten API parser tests to verify no regression**

Run: `cd backend && python -m pytest tests/providers/test_interwetten_api.py -v`
Expected: All tests PASS (these test JSON parsing, not browser behavior)

- [ ] **Step 6: Commit**

```bash
git add backend/src/core/transport.py
git commit -m "perf: add universal resource blocking to BrowserTransport

Block images, fonts, CSS, and tracking scripts for all browser-based
extractors. Applied at context level so all pages (including concurrent
tabs) inherit blocking automatically. Expected ~50-70% faster page loads."
```

---

## Chunk 2: Interwetten Time-Budget Guards

### Task 2: Add time-budget guards to interwetten extract()

**Files:**
- Modify: `backend/src/providers/interwetten.py:128-223` (extract method)

Follow the same pattern as 10Bet (`tenbet.py:160-213`): track elapsed time, exit league scraping at 70% of sport_timeout, skip detail enrichment if >80% elapsed.

- [ ] **Step 1: Add time tracking at the start of extract()**

At the top of `extract()` (line 128), after the docstring, add:

```python
import time as _time
extract_start = _time.time()
sport_timeout = self.config.get("sport_timeout", 300)
```

- [ ] **Step 2: Add time-budget check before league scraping gather()**

Before `tasks = [extract_league_concurrent(lg) for lg in leagues]` (line 193), add batched processing with time-budget checks instead of gathering all at once:

Replace lines 193-202 (the gather + dedup loop) with:

```python
        # Scrape leagues in batches with time-budget checks
        batch_size = 20
        for batch_start in range(0, len(leagues), batch_size):
            elapsed = _time.time() - extract_start
            if elapsed > sport_timeout * 0.70:
                logger.warning(
                    f"[{self.provider_id}] {sport}: time-budget exit at {elapsed:.0f}s "
                    f"({batch_start}/{len(leagues)} leagues, {len(all_events)} events)"
                )
                break

            batch = leagues[batch_start:batch_start + batch_size]
            tasks = [extract_league_concurrent(lg) for lg in batch]
            results = await asyncio.gather(*tasks)

            for league_events, league_hrefs in results:
                if league_events:
                    for event in league_events:
                        if event.id not in seen_event_ids:
                            seen_event_ids.add(event.id)
                            all_events.append(event)
                    event_hrefs.update(league_hrefs)
```

- [ ] **Step 3: Add time-budget gate before detail enrichment**

Replace the detail enrichment block (lines 214-221) with a budget-gated version:

```python
        # --- Pass 2: Event detail pages (spread + total) ---
        elapsed = _time.time() - extract_start
        if all_events and event_hrefs and sport in self.DETAIL_SPORTS:
            if elapsed < sport_timeout * 0.80:
                detail_count = await self._enrich_with_detail_markets(
                    page, all_events, event_hrefs, sport
                )
                logger.info(
                    f"[{self.provider_id}] {sport}: enriched {detail_count}/{len(all_events)} "
                    f"events with spread/total"
                )
            else:
                logger.warning(
                    f"[{self.provider_id}] {sport}: skipping detail enrichment — "
                    f"{elapsed:.0f}s already elapsed (budget: {sport_timeout}s)"
                )
```

- [ ] **Step 4: Commit**

```bash
git add backend/src/providers/interwetten.py
git commit -m "perf(interwetten): add time-budget guards for league + detail passes

Exit league scraping at 70% of sport_timeout, skip detail enrichment
at 80%. Prevents hard timeouts at 700s — delivers partial results
instead of timing out with nothing. Same pattern as 10Bet/ComeOn."
```

---

## Chunk 3: Interwetten Detail Page Optimizations

### Task 3: Reduce detail page navigation overhead

**Files:**
- Modify: `backend/src/providers/interwetten.py:375-459` (_enrich_with_detail_markets method)

Two optimizations:
1. Remove the unnecessary 50ms `wait_for_timeout` before `evaluate()` — the evaluate already waits for DOM readiness
2. Add time-budget check inside the detail enrichment loop so individual detail pages bail out at 90% of sport_timeout

- [ ] **Step 1: Pass extract_start and sport_timeout into enrichment**

Change the method signature (line 375) to accept timing info:

```python
    async def _enrich_with_detail_markets(
        self, page, events: List[StandardEvent],
        event_hrefs: Dict[str, str], sport: str,
        extract_start: float = 0, sport_timeout: float = 300,
    ) -> int:
```

Update the call site (in Task 2's budget-gated block) to pass them:

```python
                detail_count = await self._enrich_with_detail_markets(
                    page, all_events, event_hrefs, sport,
                    extract_start=extract_start, sport_timeout=sport_timeout,
                )
```

- [ ] **Step 2: Add time-budget check inside enrich_one()**

Inside `enrich_one()` (line 408), add at the top before the worker_page acquisition. Use `_time` from outer scope (already imported in extract()):

```python
        async def enrich_one(event: StandardEvent, href: str):
            nonlocal enriched, errors
            if errors > 20:
                return

            # Time-budget check: stop if approaching sport timeout
            if _time.time() - extract_start > sport_timeout * 0.90:
                return
```

- [ ] **Step 3: Remove the unnecessary 50ms wait**

Delete line 422:
```python
                    await worker_page.wait_for_timeout(50)
```

The `page.evaluate()` on the next line already waits for the DOM to be ready. Removing this saves 50ms × 120 detail pages = ~6s.

- [ ] **Step 4: Run tests**

Run: `cd backend && python -m pytest tests/providers/test_interwetten_api.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add backend/src/providers/interwetten.py
git commit -m "perf(interwetten): optimize detail page enrichment

Remove unnecessary 50ms wait before evaluate(), add 90% time-budget
guard inside detail enrichment loop."
```

---

## Chunk 4: Reduce MAX_DETAIL_EVENTS + Log Total Timing

### Task 4: Tune concurrency and add extraction timing log

**Files:**
- Modify: `backend/src/providers/interwetten.py`

- [ ] **Step 1: Opt interwetten into CSS blocking**

Interwetten doesn't use `getComputedStyle()` — it only reads `data-betting` attributes. Add to `InterwettenRetriever.__init__()` after `super().__init__()`:

```python
        self.transport._BLOCK_STYLESHEETS = True
```

- [ ] **Step 2: Reduce MAX_DETAIL_EVENTS from 200 to 120**

The detail pass is the main bottleneck. 200 detail pages at ~1-2s each = 200-400s. Capping at 120 keeps the detail pass under ~180s. Most value comes from the first 100-150 events (top leagues sorted first).

Change line 121:
```python
    MAX_DETAIL_EVENTS = 120
```

- [ ] **Step 3: Add total extraction timing log at end of extract()**

After the detail enrichment block, before the return statement, add:

```python
        total_elapsed = _time.time() - extract_start
        logger.info(
            f"[{self.provider_id}] {sport}: completed in {total_elapsed:.0f}s — "
            f"{len(all_events)} events"
        )
```

- [ ] **Step 4: Commit**

```bash
git add backend/src/providers/interwetten.py
git commit -m "perf(interwetten): reduce detail cap to 120, opt into CSS blocking, add timing log

Lower MAX_DETAIL_EVENTS from 200 to 120 to keep detail pass under ~180s.
Add total extraction timing log for monitoring."
```

---

## Verification

After all tasks, run the interwetten extractor manually and compare:

```bash
curl -X POST "http://localhost:8000/api/extraction/run?providers=interwetten"
```

Then query the DB:
```sql
SELECT provider_id, status, duration_seconds, events_processed, odds_processed, error_message
FROM provider_run_metrics
WHERE provider_id = 'interwetten'
ORDER BY start_time DESC LIMIT 3;
```

**Expected improvement:**
- Successful runs: 200-500s → 120-300s (resource blocking + faster detail pages)
- Timeout rate: 30% → <5% (time-budget guards deliver partial results)
- Event count: ~800+ on success (unchanged, we're not reducing league coverage)
