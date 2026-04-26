# Cluster 5: api_soft (unibet, betinia, betsson, bethard, spelklubben, vbet)

> **Audit date:** 2026-04-26
> **Status:** active in production, tier `api_soft` (2-min cooldown), grouped: false.
> 6 providers run as 6 independent ProviderSchedule loops.
> **Role:** soft-book odds extraction. These are the providers we compare
> against Pinnacle to find value bets and arb opportunities.
> **Why this cluster matters:** the **21:53 UTC tier-wide stall on 2026-04-25**
> happened here — 4 of 6 providers (bethard, spelklubben, betinia, betsson)
> stuck simultaneously, force-cancelled by watchdog ~30 times in 6 minutes.

## 1. Inventory

The 6 providers in this tier use **4 different retriever types**:

| Provider | Retriever class | File | Lines | Transport | Notes |
|---|---|---|---|---|---|
| unibet | `KambiRetriever` | [kambi.py](../../backend/src/providers/kambi.py) | 637 | `HttpTransport` (aiohttp) | Canonical only — represents 7 brands (leovegas, betmgm, speedybet, x3000, goldenbull, 1x2) but only unibet runs in extraction; siblings inherit via class-level cache |
| betinia | `AltenarRetriever` | [altenar.py](../../backend/src/providers/altenar.py) | 666 | `HttpTransport` (aiohttp) | Canonical only — represents 6 brands (campobet, lodur, quickcasino, swiper, dbet) but only betinia runs |
| betsson | `GeckoV2Retriever` | [gecko_v2.py](../../backend/src/providers/gecko_v2.py) | 742 | **`BrowserTransport`** (Playwright) | Each brand has its OWN `BrowserTransport` instance — separate Chromium |
| bethard | `GeckoV2Retriever` | same | same | own `BrowserTransport` | independent extractor instance |
| spelklubben | `GeckoV2Retriever` | same | same | own `BrowserTransport` | independent extractor instance |
| vbet | `VbetRetriever` | [vbet.py](../../backend/src/providers/vbet.py) | 589 | **WebSocket + raw socket SOCKS5** | Bypasses both `HttpTransport` and `BrowserTransport` |

### Live observation (last 3 h, 2026-04-26 06:20-09:31 UTC)

| Provider | Runs | Failures | Events | Avg duration |
|---|---|---|---|---|
| unibet | 2 | 0 | 2,152 | 46 s |
| betinia | 1 | 0 | 1,994 | 59 s |
| betsson | 2 | 0 | 3,629 | 147 s |
| bethard | 2 | 0 | 3,649 | 125 s |
| spelklubben | 1 | 0 | 1,823 | 112 s |
| vbet | 2 | 0 | 2,421 | 51 s |

**Note:** "1 run in 3h" for betinia and spelklubben means cycle time = ~10 min,
not the configured 2 min. Suggests scheduler revival back-off was active OR
post-restart scheduler hadn't completed a full warmup.

### Spelklubben anomaly
`/health/extraction` reports spelklubben **22117 minutes stale** (~15 days)
even though provider_run_metrics shows it ran successfully today.
The health endpoint reads `MAX(odds.updated_at)` ([health.py:222](../../backend/src/pipeline/health.py))
— so its odds rows aren't being updated. This is a real bug we'll need to
chase separately.

## 2. Extraction flows

### Kambi (unibet)
```
extract(sport)
  └─ check class-level _SHARED_GROUP_CACHE (TTL=3600s, key=base_url)  [kambi.py:53]
        └─ if miss: GET /{brand}/group.json → save in cache
  └─ recursively walk groups, find target_groups for sport
  └─ optional: filter by Pinnacle's target_leagues (cheat sheet)
  └─ asyncio.gather(_fetch_group_events for group in target_groups)
        Semaphore(5)                                                 [kambi.py:151]
        per group:
          GET /{brand}/betoffer/group/{group_id}.json
          parse betoffers → events with markets
  └─ dedupe events by ID (keep version with most markets)
```

Kambi is the cleanest of the 6 — uses `HttpTransport` properly, has class-level
cache that's actually shared across brands (the brand list in factory caches
just the canonical "unibet" instance and reuses).

### Altenar (betinia)
```
extract(sport)
  └─ map sport → Altenar sportId (1=football, 67=basketball, etc.)
  └─ GET /widget/GetUpcoming?sportId=X (single bulk fetch — events + competitors + markets + odds in one response)
  └─ build O(1) ID indexes (competitors, champs, markets, odds)
  └─ for event in sport_events: _parse_event() → StandardEvent
  └─ break inner loop if limit hit
  └─ Pass 2: _enrich_missing_spreads()                              [altenar.py:517-563]
        sem = asyncio.Semaphore(20)                                  [altenar.py:535]
        BATCH_SIZE = 50
        for events missing spread:
          GET /widget/GetEventDetails?... (one call per event)
        gather under Sem(20)
```

Altenar is fast because the bulk endpoint returns everything in one call.
Two-phase though — football has 73 % missing spread markets that need per-event enrichment.

### Gecko_v2 (betsson, bethard, spelklubben)
```
extract(sport)
  └─ if first call this run: _ensure_session()
        ┌─ async with self._session_init_lock:                      [gecko_v2.py:359]
        │    await transport._ensure_browser()  ← Playwright launch + context
        │    page.route("**/api/sb/**", capture_route)
        │    page.goto(site_url + init_path, timeout=60s)
        │    handle cookie consent
        │    wait up to 30s for capture_route to fire (capture x-sb-* headers + api_base)
        │    if no headers: fallback to page.goto(sport-page), wait 20s
        │    page.unroute("**/api/sb/**")
        │
        │    Retry up to 3 times within asyncio.wait_for(timeout=180):    [gecko_v2.py:371-396]
        │      on failure: await transport.close() + _ensure_browser()  ← FORCE-KILL PATH
        │
        └─ if all 3 retries fail: raise RetryableError, set _session_init_failed=True
                                  ↑ subsequent sport coroutines fail immediately
  └─ category_id from hardcoded map or _lookup_category_id (slug API)
  └─ GET /api/sb/v1/widgets/events-table/v2?... (page 1)
        on 400: re-init session, retry once
  └─ parallel-fetch remaining pages (up to limit/page-size)
  └─ _parse_page → events with markets
```

**Critical:** **3 brands × own BrowserTransport = 3 separate Chromium processes**
launched simultaneously. Each does its own 60 s page.goto + 30 s header capture
+ 30 s fallback navigation. **3× browser memory cost (~1-2 GB each)**.
Brands return identical events from the same OBG backend, but we don't cache
the captured headers across brands.

### VBet (BetConstruct WebSocket + raw SOCKS5)
```
extract(sport)
  └─ map sport → BetConstruct alias
  └─ for attempt in range(WS_MAX_RETRIES):
        try:
          if proxy:
            if SOCKS5:
              sock = socks.socksocket()
              sock.set_proxy(SOCKS5, proxy_host, proxy_port, user, pass)
              sock.connect((ws_host, 443))                          ← BLOCKING on event loop!
              ws_kwargs["sock"] = sock
            else:
              # Manual HTTP CONNECT tunnel
              tunnel = socket.socket(...)
              tunnel.connect(...)
              tunnel.sendall(b"CONNECT host:port HTTP/1.1...")
              resp = tunnel.recv(4096)
          async with websockets.connect(ws_url, ...) as ws:
            return await self._fetch_sport(ws, sport, alias, limit)
        except: retry with exponential backoff (delay = base * 2^attempt)
  └─ all retries exhausted: raise RetryableError
```

**Critical:** vbet uses **raw `socks.socksocket()` + blocking `sock.connect`**
([vbet.py:374-383](../../backend/src/providers/vbet.py#L374)) on the asyncio
event loop. Same for the HTTP CONNECT fallback ([line 387-401](../../backend/src/providers/vbet.py#L387)).
For the duration of `sock.connect()` (up to 15 s per `settimeout`),
the event loop stalls — health checks queue, other coroutines starve.

## 3. Resource model

| Resource | Kambi | Altenar | Gecko_v2 | VBet |
|---|---|---|---|---|
| Transport | `HttpTransport` (good) | `HttpTransport` (good) | `BrowserTransport` (heavy) | None — raw `socks` + `websockets` |
| Sessions per cycle | shared via class cache | 1 per provider | 1 per brand × 3 brands = 3 | 1 WS per attempt |
| Concurrency cap | `Semaphore(5)` per group | `Semaphore(20)` for enrichment | sport-level via orchestrator | none |
| Proxy | inherited HttpTransport | inherited HttpTransport | `use_proxy=True` Playwright proxy ([factory.py:150](../../backend/src/factory.py#L150)) | manual via `socks.socksocket` |
| Locks | class-level `_SHARED_GROUP_CACHE` | none | per-instance `_session_init_lock` | none |
| Memory cost | low (aiohttp) | low (aiohttp) | **high (~1-2 GB Chromium per brand × 3 brands)** | low (websocket) |

## 4. Lifecycle

### Per-extraction
- Kambi: lazy session init → group fetch → parallel betoffer fetch → dedupe → return
- Altenar: bulk fetch → parse → conditional enrichment
- Gecko: lock-protected session init (with 3 retries × 180s = up to 540s) → API calls via captured headers
- VBet: WS connect (with up to 3 attempts) → request session → fetch sport → close

### Cleanup
- Kambi/Altenar: orchestrator finally calls `extractor.close()` → `transport.close()`. Class-level caches survive across runs (intentional, TTL-bounded).
- **Gecko brands: each brand's `transport.close()` triggers `BrowserTransport._graceful_close` → 8 s timeout → if hung, `_kill_spawned_processes` force-kills Chrome PIDs ([transport.py:695-732](../../backend/src/core/transport.py#L695)). With 3 brands × per-cycle close, that's 3 force-kill paths per cycle.**
- Vbet: `async with websockets.connect` closes WS, but the SOCKS5 socket created at line 374 is wrapped into the connection and closed there.

## 5. The 21:53 UTC tier-wide stall — root cause analysis

Reconstructed from log timestamps + code:

| Time UTC | Event |
|---|---|
| 21:53:46 | `bethard STUCK — last completed 1370s ago, current run 1250s old` |
| 21:54:57 | `bethard` again STUCK + `spelklubben STUCK` |
| 21:55:58 | `betinia STUCK` + `bethard` again |
| 21:56-21:59 | Continuous force-cancel/restart across all 4 providers |

**4 providers stuck simultaneously, each force-cancelled by the watchdog every minute. This is not a provider bug — it's a tier-wide resource contention.**

Likely chain:
1. All 6 api_soft providers fire at the 2-min boundary at the same instant
2. Each provider runs in its own thread → its own asyncio loop → its own SQLAlchemy session
3. With 3 concurrent sports per provider × 6 providers = up to 18 sessions checked out
4. + cleanup loop (separate session)
5. + analyzer running under module-global `threading.Lock`
6. + ML/CLV/macro/training all using sessions
7. DB pool size 40 + 20 overflow = 60 connections — exhausted
8. PostgreSQL row locks: `cleanup_stale()` running `DELETE FROM odds WHERE ...` collides with provider commits doing `INSERT ... ON CONFLICT UPDATE` on overlapping rows
9. Providers block on either:
   - DB pool acquire timeout (pool exhausted) → SQLAlchemy raises after `pool_timeout` (default 30 s)
   - Row lock wait → PostgreSQL holds the request indefinitely
10. The watchdog's 1200 s api_soft floor fires → force-cancels the asyncio task → task state becomes inconsistent (mid-transaction, mid-API-fetch)
11. Restart loop starts — fresh task hits the same contention again
12. Recovery only when cleanup completes its DELETE batch and analyzer releases its lock

**Provider-level fixes won't solve this.** This is an orchestrator + DB pool issue.
The fix is to extract the analyzer + ML hooks out of the per-provider hot path
(see the post_extraction_worker design from earlier in this conversation),
NOT to make individual extractors faster.

## 6. Smells

### Kambi (unibet)

| # | File:line | Smell | Impact |
|---|---|---|---|
| A | [kambi.py:53, 60](../../backend/src/providers/kambi.py#L53) | Class-level `_SHARED_GROUP_CACHE` and `_SHARED_EVENT_CACHE` mutable. TTL works but **never bounded** — over hours of runtime cache grows monotonically. | Memory growth in long-lived process. |
| B | [kambi.py:151](../../backend/src/providers/kambi.py#L151) | `Semaphore(5)` per-call (per-extract). The 8 sibling Kambi brands could all create instances if any future code path wants them, multiplying total in-flight against the same Kambi backend. | Latent — currently only unibet runs, but the design doesn't enforce the singleton. |
| C | [kambi.py:139](../../backend/src/providers/kambi.py#L139) | Group filtering by `target_leagues` falls back to "use all" if filter matches 0 — no warning escalation if Pinnacle cheat sheet drifts dramatically. | Diagnostic gap. |

### Altenar (betinia)

| # | File:line | Smell | Impact |
|---|---|---|---|
| D | [altenar.py:535-536](../../backend/src/providers/altenar.py#L535) | `Semaphore(20)` for GetEventDetails enrichment + `BATCH_SIZE=50`. With ~200 events to enrich, 20 in flight against a single host. No per-host rate limit middleware. Altenar 429s on heavy load. | Latent rate-limit risk. |
| E | [altenar.py:158-194](../../backend/src/providers/altenar.py#L158) | `_standardize_outcome` does substring matching on team-name word overlap. Same false-positive class as polymarket / kalshi. | Wrong-side attribution. |
| F | [altenar.py:511-513](../../backend/src/providers/altenar.py#L511) | `try/except Exception ... return []` swallows ALL extraction errors as 0 events. Caller sees "0 events" not "extraction failed". | Diagnostic gap. |

### Gecko_v2 (betsson, bethard, spelklubben) — **the biggest cluster issue**

| # | File:line | Smell | Impact |
|---|---|---|---|
| **G** | [gecko_v2.py:186](../../backend/src/providers/gecko_v2.py#L186) | **`_session_init_lock` is per-instance.** Each gecko brand has its own retriever instance with its own lock — 3 separate Chromium browsers launching simultaneously, each doing 60 s page.goto + 30 s header capture. **All 3 brands share the same OBG backend** — they only differ in the brand path in the URL. We could share captured headers across brands. | 3× browser memory + 3× page-load latency for what should be 1 navigation. |
| **H** | [gecko_v2.py:371-396](../../backend/src/providers/gecko_v2.py#L371) | **3 retries × `asyncio.wait_for(timeout=180s)` = up to 540 s of session init alone before extraction starts.** Each failed attempt calls `transport.close() + _ensure_browser()` which is the force-kill source on the chrome leak path. **3 brands × 3 retries × 1 close-call = 9 force-kill paths per failed init cycle.** | This is the source of the 30+ overnight force-killed-chrome events. |
| I | [gecko_v2.py:217-218](../../backend/src/providers/gecko_v2.py#L217) | `page.route("**/api/sb/**", capture_route)` + `page.unroute(...)`: route handlers add latency to every matching request, then `unroute()` is best-effort. Under fail-paths the route may persist. | Edge-case leak; minor. |
| J | [gecko_v2.py:496-509](../../backend/src/providers/gecko_v2.py#L496) | `_fetch_page` swallows errors with DEBUG-only log + returns None. Pages dropped silently. | Quiet partial extraction. |
| K | [gecko_v2.py:330-355](../../backend/src/providers/gecko_v2.py#L330) | `run_id`-driven cache invalidation: a new run clears `_api_headers` and `_api_base`. **But `_session_init_lock` is acquired AFTER the run-check** — the 3 sport coroutines for a brand all see `_api_headers=None` simultaneously, race into the lock, and only one actually does the init. Plus the `transport.page is None` check at [line 350](../../backend/src/providers/gecko_v2.py#L350) is a valid guard but assumes the previous run's `close()` actually cleared the page. | Race-condition surface; current logic is correct but fragile. |

### VBet — the worst structural issues in this cluster

| # | File:line | Smell | Impact |
|---|---|---|---|
| **L** | [vbet.py:374-401](../../backend/src/providers/vbet.py#L374) | **Raw socket SOCKS5 + `sock.connect` + `tunnel.connect` are BLOCKING calls on the asyncio event loop.** Up to 15 s timeout per call. During this window, the entire event loop is frozen — health probes time out, other coroutines stall. | Cross-provider contention. Could explain transient health-check timeouts. |
| **M** | [vbet.py:387-401](../../backend/src/providers/vbet.py#L387) | Manual HTTP CONNECT tunnel implementation (parsing raw HTTP response). 100+ lines of network plumbing reinvented from scratch. Compare to `aiohttp`'s built-in `proxy=` kwarg or `aiohttp_socks.ProxyConnector`. | Maintenance burden + correctness risk (no test coverage). |
| N | [vbet.py:354-425](../../backend/src/providers/vbet.py#L354) | 3-retry loop with exponential backoff is fine, but each retry spawns a new socket — if proxy is sketchy, we leak FDs until GC runs (the `tunnel` socket has no explicit close on the failure path at [line 399](../../backend/src/providers/vbet.py#L399); only successful CONNECT closes it via the WS context manager). | FD leak under proxy failures. |
| O | [vbet.py:336-426](../../backend/src/providers/vbet.py#L336) | The whole extract function is 90 lines of imperative socket-wiring inline — should be a `connect_ws_with_proxy()` helper. | Readability. |

## 7. Open-source comparable

| Project | What it does differently |
|---|---|
| [Kambi public APIs (community wrappers)](https://github.com/topics/kambi) | Most use a shared `aiohttp.ClientSession` per region (eu1/na1) and key cache by region. We key by base_url which is brand-specific — could converge. |
| [`playwright-stealth`](https://github.com/AtuboDad/playwright_stealth) | Stealth plugins for fingerprint evasion. Gecko V2 doesn't use it — but Betsson's anti-bot may flag Playwright's bundled Chromium. |
| [`websockets` library](https://websockets.readthedocs.io/) + [`python-socks`](https://github.com/romis2012/python-socks) async helpers | `python-socks[asyncio]` provides async-compatible SOCKS proxy support — drop-in replacement for the blocking `socks.socksocket` in vbet. |
| [`scrapy-splash` / scrapy-playwright pattern](https://github.com/scrapy-plugins/scrapy-playwright) | Browser-pool reuse (one Chromium serves many requests). Eliminates the "3 brands × 3 Chromiums" structure for Gecko. |
| Shared CDP browser pool ([`playwright-cdp`](https://playwright.dev/python/docs/api/class-browsertype#browser-type-connect-over-cdp)) | One persistent Chromium with `--remote-debugging-port=9222`, all browser-based providers `connect_over_cdp`. Cuts memory from ~10 GB to ~2 GB total. |

## 8. Verdict

Per provider:

- **Kambi (unibet):** keep, it's the model. Bound the cache to be safe.
- **Altenar (betinia):** keep, fix the substring matching false-positive.
- **Gecko V2 (betsson/bethard/spelklubben):** **major refactor needed.** The 3-browser-per-tier pattern is the chrome-leak source AND the slowest path. Either share a browser+headers across brands or rewrite as `HttpTransport`-only with manual `x-sb-*` header acquisition (one navigation per pipeline cycle).
- **VBet:** **rewrite the proxy plumbing.** Use `python-socks[asyncio]` to eliminate blocking calls on the event loop.

Tier-level: the 21:53 stall is **not a provider issue.** It needs the
analyzer + ML side-effects extracted out of the per-provider hot path
(post_extraction_worker pattern), so concurrent api_soft cycles don't all
queue behind the analyzer's threading.Lock and don't all race the cleanup
loop on overlapping `odds` rows.

## 9. Ranked fixes

| # | Fix | Provider | File:line | Impact | Effort |
|---|---|---|---|---|---|
| 1 | **Move analyzer + ML out of per-provider hot path** (cluster-level / orchestrator) — root cause of 21:53 stall. Already designed in earlier conversation: `post_extraction_worker` with debounced queue. | (orchestrator) | [orchestrator.py:1111-1429](../../backend/src/pipeline/orchestrator.py#L1111) | Eliminates tier-wide stalls. **Highest leverage fix in the entire codebase.** | 2 days |
| 2 | **Share gecko session across betsson/bethard/spelklubben** — one navigation per pipeline cycle, one browser. Promote `_api_headers` cache to class-level keyed by `_init_path` (the path that triggers OBG API calls). All 3 brands use the same backend. | gecko_v2 | [gecko_v2.py:172-186](../../backend/src/providers/gecko_v2.py#L172) | -2 Chromium browsers, -67 % session-init cost, **-66 % force-kill events per cycle.** | 4 h |
| 3 | **Replace vbet's blocking socket setup with `python-socks[asyncio]`** — `from python_socks.async_.asyncio import Proxy` — eliminates blocking `sock.connect` on event loop. | vbet | [vbet.py:354-401](../../backend/src/providers/vbet.py#L354) | Removes cross-provider contention from event-loop blocking. | 3 h |
| 4 | **Fix gecko session-init death-spiral** — reduce 3-retry × 180 s to 2-retry × 90 s + cap total wait at 240 s instead of 540 s. Don't `transport.close()` between retries — that's the force-kill path. Reuse the existing browser. | gecko_v2 | [gecko_v2.py:371-396](../../backend/src/providers/gecko_v2.py#L371) | -6 Chrome force-kills per failed cycle. | 1 h |
| 5 | **Bound Kambi caches** — switch `_SHARED_GROUP_CACHE` and `_SHARED_EVENT_CACHE` to LRU (e.g. `cachetools.TTLCache(maxsize=64, ttl=3600)`). | kambi | [kambi.py:53-60](../../backend/src/providers/kambi.py#L53) | Caps memory growth in long-lived process. | 30 min |
| 6 | **Fix Altenar substring outcome match** | altenar | [altenar.py:158-194](../../backend/src/providers/altenar.py#L158) | Correctness — same fix pattern as polymarket / kalshi. | 1 h |
| 7 | **Add Altenar 429 handling** to `_enrich_missing_spreads` | altenar | [altenar.py:535](../../backend/src/providers/altenar.py#L535) | Closes silent rate-limit hole. | 1 h |
| 8 | **Investigate spelklubben odds-not-saving bug** — `/health/extraction` says 22117 min stale but provider_run_metrics shows successful runs. Either the run reports success but rolls back odds, or storage skips spelklubben somehow. | spelklubben | [storage.py + gecko_v2](../../backend/src/pipeline/storage.py) | Restores spelklubben odds freshness | 2-4 h investigation |
| 9 | **Remove vbet manual HTTP CONNECT tunnel** — `aiohttp` proxy support handles it; or `python-socks[asyncio]`. Delete 50 lines of socket plumbing. | vbet | [vbet.py:387-401](../../backend/src/providers/vbet.py#L387) | Maintenance + reliability. | (subset of #3) |
| 10 | **CDP-shared browser pool for gecko brands** — instead of `connect_over_cdp` to a persistent Chromium, all gecko brands attach to one host browser. (Bigger architectural change — discuss before doing.) | gecko_v2 + transport | [transport.py:451](../../backend/src/core/transport.py#L451) (CDP support exists) | Same goal as #2 but cluster-wide; possibly conflict with #2. | 2 days |

**Minimum-viable bundle (1 + 4 + 5 + 6 + 8):** ~6 days dev. Closes the tier-stall root cause + key correctness issue + memory bound + the spelklubben mystery.
**Recommended (1-7):** ~7-8 days. Adds the gecko share + vbet rewrite.

## 10. Re-introduction notes

> Filled in after fixes ship.

- [ ] post_extraction_worker deployed; tier stalls observed (target: 0 in 7-day window):
- [ ] Gecko shared headers: browser memory before vs after:
- [ ] Vbet event-loop block time before vs after python-socks switch:
- [ ] Spelklubben odds_updated_at delta over 24 h:
- [ ] Altenar 429 incidents in metrics:
