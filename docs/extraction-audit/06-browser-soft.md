# Cluster 6: browser_soft (888sport, interwetten, 10bet, tipwin)

> **Audit date:** 2026-04-26
> **Status:** active in production, tier `browser_soft` (10-min cooldown), grouped: false
> **Role:** soft-book odds extraction from sites without exposed JSON APIs.
> Each provider runs a Playwright/Chromium browser to render the SPA and
> scrape DOM (or intercept XHR network calls).
> **Cluster size:** 4 providers, 2895 lines of code.

## 1. Inventory

| Provider | Retriever | File | Lines | Strategy | Concurrent tabs |
|---|---|---|---|---|---|
| 888sport | `SpectateRetriever` | [spectate.py](../../backend/src/providers/spectate.py) | 525 | XHR interception via `_fetch_api`; bucket caching | sport-level only |
| interwetten | `InterwettenRetriever` | [interwetten.py](../../backend/src/providers/interwetten.py) | 731 | DOM scrape across leagues + per-event detail pages | **16 league + 8 detail tabs in single context** |
| 10bet | `TenBetRetriever` | [tenbet.py](../../backend/src/providers/tenbet.py) | 1043 | DOM scrape per competition + per-event detail | **2-4 competition tabs + 4 detail tabs** |
| tipwin | `TipwinRetriever` | [tipwin.py](../../backend/src/providers/tipwin.py) | 596 | XHR interception via `page.route` on a single page navigated through paginated list | sequential (120 navigations on 1 page) |

All four use `BrowserTransport` ([factory.py](../../backend/src/factory.py)) with
`headless=True` (10bet, interwetten, spectate) or `use_proxy=True` (spectate).

`browser_soft.max_concurrent_browsers: 2` ([providers.yaml:927](../../backend/src/config/providers.yaml#L927))
caps how many browser-tier providers run in parallel — but each running provider
has its own Chromium process AND can open many tabs internally.

### Live observation (last 3 h, 2026-04-26 06:20-09:31 UTC)

| Provider | Runs | Failures | Events | Avg duration |
|---|---|---|---|---|
| 888sport | 5 | 0 | 5,539 | 85 s |
| interwetten | 2 | 0 | 1,249 | **1185 s** (~20 min) |
| 10bet | **0** | n/a (last completed 06:34 UTC, 30+ min stale at audit time) | — | n/a |
| tipwin | 3 | 0 | 2,391 | 307 s (~5 min) |

**Three concerns:**
1. **Interwetten cycle exceeds its 10-min cooldown** (1185 s avg = 19.7 min). Effective cycle ≈ 30 min.
2. **10bet stuck since 06:34 UTC** — last successful run took 2168 s (timed out twice, watchdog logged "STARVING / STALE" warnings continuously).
3. **888sport is healthy at 85 s** — proves the cluster is workable when the design fits.

## 2. Extraction flows

### 888sport (Spectate) — fast, well-behaved
```
extract(sport)
  └─ ensure_sport_init: page.goto(www_url, wait_until=load, timeout=20s)
        └─ wait_for_timeout(3000) ← fixed 3s sleep on init             [spectate.py:100]
  └─ check digest cache (TTL=120s)
        └─ if miss: _fetch_api(/eventsrequest/getEventsDigest/{sport_slug})
  └─ collect buckets (today/tomorrow/starting_soon + dates with count>0)
  └─ asyncio.gather(fetch_bucket for each)                             [spectate.py:192-193]
        per bucket: check cache (TTL=120s) → _fetch_api(POST /sportsbook-req/getUpcomingEvents/...)
  └─ merge + dedupe by event.id
  └─ apply limit
```

Spectate uses **XHR interception via `_fetch_api`** (not DOM scrape) — fast,
predictable. Bucket cache (120s TTL) and digest cache. Notable: `_bucket_cache_ttl=120`
shorter than the 600s scheduler interval, so cache only helps within a single
sport-iteration burst, never across cycles.

### Interwetten — heavy multi-tab DOM scrape
```
extract(sport)
  └─ navigate sport overview, discover leagues
  └─ Pass 1: scrape league pages
        Open up to CONCURRENT_LEAGUE_PAGES=16 tabs in single context  [interwetten.py:185-194]
        For each league: page.goto + DOM scrape of .s-event elements
  └─ For each event missing spread/total: open event detail page
        CONCURRENT_DETAIL_PAGES=8                                      [interwetten.py:439]
        Per detail: page.goto + DOM scrape of point + odds
  └─ aggregate, dedupe by event.id
```

**Critical:** **24 tabs open simultaneously in one browser context** during a
soccer extraction (16 league + 8 detail). Each tab redoes Truendo cookie
banner dismissal because Truendo stores consent in **localStorage** which is
not shared across tabs by default ([interwetten.py:349-350](../../backend/src/providers/interwetten.py#L349) —
"Truendo uses localStorage — not shared across tabs").

### 10bet (TenBet) — slowest provider in the cluster
```
extract(sport)
  └─ ensure_init + cookie consent
  └─ _discover_competitions: page.goto(competitions URL, timeout=15s)
        wait_for_selector('a[href*="competitions/"]', timeout=10s)
        page.evaluate to extract competition list
  └─ For each competition (Sem(2 or 4)):
        page = await transport.new_page()  ← NEW PAGE PER COMPETITION  [tenbet.py:399]
        page.on("response", _on_response) — debug API capture
        page.goto(comp matches URL, timeout=35s)
        wait_for_selector('[class*="ta-EventListItem"]', timeout=12s)
        wait_for_selector('[class*="ta-price_text"]', timeout=2s)
        page.evaluate scrape DOM for events + markets
  └─ Pass 2: enrich detail (spread + total) for events
        Open 3 extra pages → 4-page pool
        for each event (Sem(4)):
          worker_page.goto(detail URL, timeout=15s)
          wait_for_function('prices have data', timeout=12s)
          fallback: wait_for_timeout(8000)                              [tenbet.py:994]
          page.evaluate JS_EXTRACT_DETAIL_MARKETS
        Close extra pages
```

**The 2168 s timeout is structural:** football alone has 40 competitions ×
~50 s/comp (page open + nav + 12 s wait + 2 s wait + DOM scrape + parse +
close) at `concurrency=2` = **~1000 s minimum**. Then detail enrichment
(up to 300 events × ~10 s each at concurrency 4) adds another ~750 s.

**The 8-second `wait_for_timeout` fallback at [tenbet.py:994](../../backend/src/providers/tenbet.py#L994)
fires after `wait_for_function` already consumed up to 12 s** — that's 20 s
per event in the worst case, on a cold page.

### Tipwin — sequential pagination on single page
```
extract(...)
  └─ page.route("**/offer/data*", intercept_offer_api)                  [tipwin.py:210]
  └─ page.goto(home, timeout=30s) + cookie consent (one-time)
  └─ page.goto(/sv/sports/full/, timeout=30s)
  └─ Calculate total_pages from API response (capped at 120)
  └─ FOR EACH PAGE 2..max_pages SEQUENTIALLY:                          [tipwin.py:253-260]
        page.goto(?page={pg}, timeout=10s)
        await asyncio.sleep(0.5)
  └─ page.unroute(...)
  └─ Process all captured api_responses
```

**The 120 sequential page navigations** at ~2-3 s each = ~240 s minimum.
The `await asyncio.sleep(0.5)` at line 256 is mandatory because the route
handler captures inline; without it, fast `page.goto` overruns capture.
**`context.request.get` would work and could fan out 8-16 wide** —
the API URL is captured during the first navigation.

## 3. Resource model

| Resource | 888sport | interwetten | 10bet | tipwin |
|---|---|---|---|---|
| BrowserTransport | 1 (headless, with proxy) | 1 (headless) | 1 (headless) | 1 (headless) |
| Tabs per cycle | 1 (single page reuse) | **up to 24** | up to **40 + 4** (1 per competition + 4 for details) | 1 (single page, 120 navs) |
| Auth/cookie | none | Truendo (localStorage, per-tab) | site cookie consent (per-context) | Truendo (one-time) |
| Page reuse | yes — single page across sports | yes — main + 15 league pages reused | **NO — new page per competition** | yes — single page reused |
| Max nav timeouts | 20 s init | 30 s league + 30 s detail | 35 s comp + 15 s detail | 30 s init + 10 s page |
| Selector waits | 0 (XHR-based) | 5 s (.s-event) | 12 s (event list) + 2 s (prices) + 12 s (function) + 8 s (fallback) | 0 (XHR-based) |
| Caches | digest (60 s) + bucket (120 s) | none | competition list (per-call) | merged lookups in-memory per call |

## 4. Lifecycle

### Per-extraction
- 888sport: page reused; sport-level XHR fetches via `_fetch_api`
- interwetten: tabs opened in main context, closed after pass 1; detail pages opened in pass 2, closed after
- 10bet: new page per competition (closed at end), 4-page pool for details (closed after enrichment)
- tipwin: single page navigated 120 times sequentially

### Cleanup
- All four use `BrowserTransport.close()` from orchestrator's `finally`. Same as gecko: graceful close → 8 s timeout → force-kill via `_kill_spawned_processes`.
- **10bet and interwetten can leak Chrome processes if shutdown fires mid-tab-creation** — pages opened in pass 2 (interwetten detail / 10bet detail) without proper try/finally lose references, and graceful close may hang on those tabs.

## 5. Smells

### 888sport (Spectate) — least bad

| # | File:line | Smell | Impact |
|---|---|---|---|
| A | [spectate.py:96](../../backend/src/providers/spectate.py#L96) | `wait_for_timeout(3000)` fixed 3 s sleep on session init. Cargo-culted from older script — current Spectate API responds within 1 s. | -2 s per cycle. Minor. |
| B | [spectate.py:84](../../backend/src/providers/spectate.py#L84) | `_bucket_cache_ttl = 120` (2 min) but scheduler runs every 600 s (10 min). **Cache rarely hits across cycles** — only within a single sport-iteration burst. Should be longer (e.g. 540 s, just under interval) OR shared across cycles via class cache. | Wasted CPU re-fetching same buckets. |
| C | [spectate.py:135-150](../../backend/src/providers/spectate.py#L135) | Bucket selection logic is OK but doesn't filter "starting_soon" against time-of-day. Mid-week midnight extracts buckets that are hours stale. | Edge-case. |

### Interwetten — multi-tab cookie-banner storm

| # | File:line | Smell | Impact |
|---|---|---|---|
| **D** | [interwetten.py:185-197](../../backend/src/providers/interwetten.py#L185) | **24 tabs in one context (16 league + 8 detail), each redoing Truendo cookie banner dismissal because consent stored in localStorage doesn't propagate across tabs.** Each tab takes ~500 ms extra for `_dismiss_cookie_banner`. With 24 tabs, that's ~12 s of redundant cookie dismissal per cycle. | Significant cost compounding. The localStorage issue is documented in the code comment ([interwetten.py:349-350](../../backend/src/providers/interwetten.py#L349)) but not solved. |
| **E** | [interwetten.py:138](../../backend/src/providers/interwetten.py#L138) | `CONCURRENT_DETAIL_PAGES = 8` — but comment says "Reduced from 20 — only 5 browser slots, 20 tabs caused 19 s/event". So we know the system tops out at 8; we just don't know if 8 is right. | Tunable not tuned. |
| F | [interwetten.py:217](../../backend/src/providers/interwetten.py#L217) | `batch_size = 40` for league extraction with `Sem(16)`. Only matters if leagues > 40 (rare). Fine. | n/a |
| G | [interwetten.py:202-204](../../backend/src/providers/interwetten.py#L202) | `if errors > 30: return [], {}` — early-bail but caller swallows the error. No metrics increment. | Diagnostic gap. |
| H | [interwetten.py:339](../../backend/src/providers/interwetten.py#L339) | `page.goto(url, wait_until="domcontentloaded", timeout=30s)` per league. With 16-tab concurrency, all 16 nav requests fire simultaneously → can rate-limit at the CDN. Comment in [providers.yaml:927](../../backend/src/config/providers.yaml#L927) shows this was tuned. | Load on origin. |

### 10bet (TenBet) — biggest cost driver in the cluster

| # | File:line | Smell | Impact |
|---|---|---|---|
| **I** | [tenbet.py:399](../../backend/src/providers/tenbet.py#L399) | **`page = await self.transport.new_page()` per competition** in `_scrape_competition`. With 40 competitions × ~50 s/comp × 2 concurrency = ~1000 s minimum. **Page creation alone is ~300 ms × 40 = 12 s wasted.** | Should reuse pool of 2-4 pages (mirror the detail-enrichment pattern at [tenbet.py:952-960](../../backend/src/providers/tenbet.py#L952)). |
| **J** | [tenbet.py:994](../../backend/src/providers/tenbet.py#L994) | **`wait_for_timeout(8000)` literal 8-second sleep AFTER `wait_for_function` already consumed 12 s.** Worst case: 20 s of wait on a single event before any DOM extraction. | 200-event enrichment × 20 s worst-case = 4000 s = 67 min. |
| K | [tenbet.py:982-991](../../backend/src/providers/tenbet.py#L982) | `wait_for_function` polls JS expression with 12 s timeout. Polling on price text appearance. The JS evaluates every animation frame — costs the renderer cycles. A simpler `wait_for_selector('[class*="ta-price_text"]:visible:not(:empty)')` does the same with native DOM events. | Renderer load. |
| L | [tenbet.py:233](../../backend/src/providers/tenbet.py#L233) | `batch_size = 15` for competitions but Sem(2 or 4) — the batch grouping is dead weight; just gather all under semaphore. | Code clarity. |
| M | [tenbet.py:411](../../backend/src/providers/tenbet.py#L411) | `page.on("response", _on_response)` for API discovery logging. Useful during development but production overhead — every response fires the handler. | Minor perf. |
| N | [tenbet.py:177](../../backend/src/providers/tenbet.py#L177) | Comment claims "Each comp ~4-5 s with Semaphore(4). 40 × 5 s / 4 = 50 s" — but observed is 30+ s/comp with Sem(2). Comment is outdated by 5-10×. | Documentation drift. |

### Tipwin — sequential pagination

| # | File:line | Smell | Impact |
|---|---|---|---|
| **O** | [tipwin.py:253-260](../../backend/src/providers/tipwin.py#L253) | **120 sequential `page.goto` calls** with `wait_for_timeout(0.5)` between each. ~2-3 s/page = 240-360 s minimum. We've already captured the API URL via `page.route` — could use `context.request.get(api_url, params={page: N})` with `Semaphore(8)` and parallelize. | 4-8× speedup possible. |
| P | [tipwin.py:246](../../backend/src/providers/tipwin.py#L246) | `max_pages = min(total_pages or 100, 120)` — magic 120 hard cap. If tipwin grows beyond that, we silently truncate. | Latent. |
| Q | [tipwin.py:225-226](../../backend/src/providers/tipwin.py#L225) | After initial page load, if no API responses captured, sleeps 3 s and retries. After 6 s no response, raises RetryableError. Single failure mode. | Cliff. |
| R | [tipwin.py:195-205](../../backend/src/providers/tipwin.py#L195) | Route handler intercepts and re-fulfills response with original body. `await route.fulfill(response=response, body=body)` adds latency vs `await route.continue_()` + parsing on response event. | Minor perf — necessary for this pattern. |

### Cluster-level

| # | File:line | Smell | Impact |
|---|---|---|---|
| S | [providers.yaml:927](../../backend/src/config/providers.yaml#L927) | `max_concurrent_browsers: 2` cap is at the tier scope — but each provider opens 1-24 internal tabs. Two providers running concurrently = up to 48 tabs across 2 contexts → 4-6 GB RAM peak. | Architectural: the cap doesn't bound the actual concurrency footprint. |
| T | (across all 4) | **No shared browser pool.** Each provider has its own `BrowserTransport`. 4 providers × ~1-2 GB each = 4-8 GB just for browser_soft tier. | Memory cost compounds with browser_antibot tier (coolbet, comeon). Total can exceed 12 GB. |

## 6. Open-source comparable

| Project | What it does differently |
|---|---|
| [`scrapy-playwright`](https://github.com/scrapy-plugins/scrapy-playwright) | Single page, single context, swap URL via `page.goto`. Avoids per-request page creation. Tipwin's pattern done right. |
| [`playwright connect_over_cdp`](https://playwright.dev/python/docs/api/class-browsertype#browser-type-connect-over-cdp) | Persistent Chromium with `--remote-debugging-port=9222`, all browser providers attach to it. Cuts memory from 4-8 GB total to ~2 GB. Already supported in [transport.py:451](../../backend/src/core/transport.py#L451). |
| [`selectolax`](https://github.com/rushter/selectolax) | ~10× faster HTML parsing than `page.evaluate()` DOM walks. Our 10bet `page.evaluate` JS scripts could be replaced with `await page.content() → selectolax`. |
| [`undetected-playwright-python`](https://github.com/AtuboDad/playwright_stealth) | Stealth flags. None of these 4 needs anti-bot evasion (interwetten and tipwin already work headless), so we're using BrowserTransport more for "API not exposed" than for fingerprinting. |
| [`aiolimiter`](https://pypi.org/project/aiolimiter/) | Token-bucket rate limiter for the multi-tab nav pattern (interwetten 16 concurrent goto's against one origin). |

## 7. Verdict

- **888sport (Spectate):** keep, only Smell A and B are worth fixing.
- **interwetten:** keep, but the 24-tab cookie storm needs to be solved either by setting Truendo's localStorage value at context level (one-time) or by setting the cookie consent cookie directly at context-init.
- **10bet:** **biggest payoff** — fix `_scrape_competition` page churn (Smell I) and the `wait_for_timeout(8000)` fallback (Smell J). Realistic target: 2168 s → ~600 s.
- **tipwin:** **second biggest payoff** — replace 120 sequential `page.goto` with parallel `context.request.get`. Realistic target: 307 s → ~60 s.

Cluster-level: **shared CDP browser** would be the single highest-impact
architectural change for this tier + the antibot tier. -50 % memory, -15-30 s
per-cycle browser-launch cost.

## 8. Ranked fixes

| # | Fix | Provider | File:line | Impact | Effort |
|---|---|---|---|---|---|
| 1 | **Reuse page pool in 10bet `_scrape_competition`** — instead of `await transport.new_page()` per competition, mirror the detail-enrichment pool pattern. Open 3 extra pages once, gather under Sem(2-4), close pool at end. | 10bet | [tenbet.py:399](../../backend/src/providers/tenbet.py#L399) | 40 × 50 s ÷ 4 concurrency = ~500 s. Total ~600 s including detail. | 2 h |
| 2 | **Drop 8-second `wait_for_timeout` fallback in 10bet** — `wait_for_function` already covers up to 12 s. If it times out, the page genuinely doesn't have prices yet — skip the event rather than waiting another 8 s. | 10bet | [tenbet.py:992-994](../../backend/src/providers/tenbet.py#L992) | -8 s × 200 events = -1600 s on detail enrichment. | 30 min |
| 3 | **Tipwin parallel pagination** — capture the API URL during page 1 navigation, then `context.request.get` for pages 2-N under `Semaphore(8)`. Drop the 120 sequential page.gotos. | tipwin | [tipwin.py:253-260](../../backend/src/providers/tipwin.py#L253) | 307 s → ~60 s. | 3 h |
| 4 | **Set Truendo consent at context level** — `context.add_init_script(`localStorage.setItem(...)`)` so all child tabs inherit consent without re-running the dismissal handler. | interwetten | [interwetten.py:258-274](../../backend/src/providers/interwetten.py#L258) | -12 s per cycle (24 tabs × 0.5 s redundant cookie work). | 1 h |
| 5 | **Lengthen Spectate bucket cache TTL** to ~540 s (or move to class-level + share across cycles). | 888sport | [spectate.py:84](../../backend/src/providers/spectate.py#L84) | Eliminates wasted re-fetch within the 600 s scheduler interval. | 30 min |
| 6 | **Drop spectate fixed 3-second init sleep** | 888sport | [spectate.py:100](../../backend/src/providers/spectate.py#L100) | -3 s per cycle. | 5 min |
| 7 | **Reduce 10bet `wait_for_function` polling** — switch to `wait_for_selector('[class*="ta-price_text"]:visible')`. | 10bet | [tenbet.py:982-991](../../backend/src/providers/tenbet.py#L982) | Frees renderer cycles; minor speed gain per event. | 30 min |
| 8 | **Tipwin observability** — log when `max_pages` cap fires; surface partial-pagination count to metrics. | tipwin | [tipwin.py:246](../../backend/src/providers/tipwin.py#L246) | Prevents silent truncation. | 15 min |
| 9 | **Cluster-wide: shared CDP Chromium for browser-tier providers** (combines with browser_antibot cluster) — launch one persistent Chromium with `--remote-debugging-port`, all 4 browser_soft + 2 browser_antibot providers `connect_over_cdp`. Need a small new module to manage the host browser process. | (architectural) | new `browser_pool.py` + edits in [factory.py:138-173](../../backend/src/factory.py#L138) and [transport.py:435-521](../../backend/src/core/transport.py#L435) | -50 % browser memory across both tiers (~5-8 GB savings). Eliminates per-launch fingerprint. | 2-3 days |

**Minimum-viable bundle (1 + 2 + 3 + 4):** ~6.5 h. Eliminates the slowest three providers' biggest costs.
**Recommended (1-7):** ~7.5 h. Adds tuning + cleanup.
**Architectural (with #9):** ~3 days. Cuts memory cluster-wide.

## 9. Re-introduction notes

**Deployed 2026-04-26 12:41 UTC** to `feat/slip-odds-architecture` on the Hetzner server (post-deploy audit recorded here):

`99fcc9c7` — 10bet (TenBet):
- Fix #1: Page-pool reuse in `extract()`. Pool size = concurrency, opened once at start of extract, closed in `finally`. `_scrape_competition` gained a `page=` kwarg (default `None` for back-compat). Switched batch-then-wait to `asyncio.as_completed` for early-exit when limit hit. Removed dead `batch_size` and the captured-but-unused `sport_timeout` line.
- Fix #2: Dropped the 8s `wait_for_timeout(8000)` fallback in `_enrich_events_with_details`. Worst case was 20s/event × 200 events = 4000s on the slow path; now we increment the error counter and return after the 12s `wait_for_function`.
- Fix #7 (`wait_for_function` → `wait_for_selector`) — **not shipped.** Smaller win, deferred.

`ae056ede` — tipwin:
- Fix #3: Parallel pagination via `context.request.get`. The route handler captures the API URL + headers from page 1's nav; pages 2..N then fan out under `Semaphore(8)` instead of 120 sequential `page.goto(?page=N)`. Legacy sequential fallback kept for the path where API URL capture fails.

`522d8e86` — interwetten:
- Fix #4: Truendo consent seeded via `context.add_init_script` BEFORE any page nav. Init scripts apply to every page in the context including ones opened later, so all 24 worker tabs (16 league + 8 detail) skip `_dismiss_cookie_banner`. Idempotent via `_truendo_seed_installed` flag. Defensive `_dismiss_cookie_banner` retained as fallback.

Fixes #5 (Spectate bucket cache TTL), #6 (Spectate fixed init sleep drop), #8 (tipwin observability), #9 (CDP-shared browser pool) — **deferred.** Lower-impact or architectural.

Pre-deploy verification: ruff clean · py_compile clean · 6/6 tenbet tests pass · 15/15 interwetten tests pass.

**Post-deploy observations (cycle 1, 12:41–12:48 UTC):**
- ✅ post_extraction_worker started cleanly, processed 7 tier completions, analyzer running each time (345-453 value bets / 449-522 arb opps).
- ✅ Browser_soft providers eventually completed (betsson 64s for 567 events; later cycles will compare 10bet directly).
- ⚠️ **One-off mass failure at 12:44:27**: 888sport, interwetten, 10bet all hit "Connection closed while reading from the driver" simultaneously — Playwright/CDP startup race when 3 browser-based providers launch in parallel right after container restart. Pre-existing (not from this batch). Recovers on next cycle.
- ⚠️ **Tipwin regression detected**: 40 events vs expected ~800 (5 % of normal). Root cause: my parallel-pagination fix (`ae056ede`) captured the homepage's small `/offer/data` URL (40 highlights) as the pagination template. Fix committed: `0d20ff52` — capture URL only from `items`-shaped responses, prefer the response with highest `totalNumberOfItems`. **Redeployed 13:06 UTC; first post-fix run at 13:11 captured 74 API responses across 100 pages and returned 944 events across 9 sports — fix validated.**

Post-deploy checks (will fill in after next deploy + 24 h):
- [ ] 10bet avg duration (target: ~600s) — first post-deploy run failed in the 12:44:27 race; need cycle 2 data
- [ ] Tipwin avg duration AFTER `0d20ff52` lands (was 14s but 40 events; target: ~60-80s with 800+ events)
- [ ] Interwetten cookie-banner overhead (was: 12s/cycle on 24-tab redundant work; target: <1s) — first run failed in race; need cycle 2 data
- [ ] Force-kill events from this tier (was: 30+ overnight; observed in 7m post-deploy: 5, all clustered at 12:44:27)
