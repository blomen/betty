# Cluster 7: browser_antibot (coolbet, comeon)

> **Audit date:** 2026-04-26
> **Status:** active in production, tier `browser_antibot` (15-min cooldown), grouped: false.
> 2 providers, both Camoufox-based (anti-detect Firefox).
> **Role:** soft-book extraction from sites with **aggressive bot protection**:
> Coolbet → **Imperva Reese84**, ComeOn → **Cloudflare**.
> **Cluster size:** 2 providers, 2130 lines of code (838 + 554 + 480 + 258).

## 1. Inventory

| Provider | Retriever | File(s) | Lines | Anti-bot | Strategy |
|---|---|---|---|---|---|
| coolbet | `CoolbetRetriever` | [coolbet.py](../../backend/src/providers/coolbet.py) | 838 | Imperva Reese84 | Camoufox (Firefox-based) + sport-aware page recycle + scroll/wait simulation |
| comeon | `ComeOnMultiLeagueRetriever` | [comeon_multileague.py](../../backend/src/providers/comeon_multileague.py) + helpers ([comeon_dom_parser.py](../../backend/src/providers/comeon_dom_parser.py), [comeon_dom_js.py](../../backend/src/providers/comeon_dom_js.py)) | 1292 (554 + 480 + 258) | Cloudflare | Camoufox + per-sport page recycle + sport priority queue + 0.2 humanize |

Both providers use [Camoufox](https://github.com/daijro/camoufox) — a patched
Firefox build that bypasses anti-bot fingerprinting at the C++ level
(WebGL canvas hashes, AudioContext, navigator.* spoofing, TLS fingerprints).
Camoufox runs as its **own subprocess** outside our `BrowserTransport` —
critical lifecycle implication.

`browser_antibot.max_concurrent_browsers: 1` ([providers.yaml:938](../../backend/src/config/providers.yaml#L938))
caps tier-level parallelism to 1 — comeon alone can take 20 min and uses
~1.5 GB. Two simultaneously was found to OOM the container.

### Live observation (last 3 h, 2026-04-26 06:20-09:31 UTC)

| Provider | Runs | Failures | Events | Avg duration |
|---|---|---|---|---|
| coolbet | 7 | 0 | 1,759 | 203 s |
| comeon | 3 | 0 | 2,432 | **1134 s** (~19 min) |

Coolbet is healthy. Comeon at 1134 s avg pushes against the 15-min cooldown
budget; cycle effective is ~30 min.

## 2. Extraction flows

### Coolbet
```
extract(sport)
  └─ _get_page():
      _ensure_camoufox():
        if page exists: try evaluate("() => true") — if alive, return        [coolbet.py:140-151]
        if dead: cleanup + relaunch (always full subprocess)                  [coolbet.py:155-202]
        ELSE: AsyncCamoufox.__aenter__() with:
          - geoip=True (ip-aware fingerprint)
          - humanize=True (mouse jitter, scroll simulation)
          - random os (windows or macos)
          - block_images=True
          - browserforge fingerprint (if installed) — fresh per launch
      Strategy 2 fallback: BrowserTransport.page (Playwright Chromium via CDP)
  └─ if not session_ready:
      page.goto(sport URL, wait_until=load, timeout=60s)
      asyncio.sleep(2) + window.scrollTo(0, 300) + sleep(1) +
        scrollTo(0, 600) + sleep(2) + scrollTo(0, 0)  ← Imperva mouse-track
      check body text for Imperva block phrases
        ("Incapsula", "security check", "Access denied", "Error 15")
      if blocked: raise RetryableError → next cycle relaunches Camoufox
  └─ proceed with API extraction via context.request.get
  └─ between sports: await self._recycle_page() — proactive close+reopen      [coolbet.py:108-127]
```

### ComeOn (multi-league)
```
extract(sport_or_list)
  └─ _get_page() = same Camoufox launch as coolbet but:
      humanize=0.2 (lighter — comeon's CF is less sensitive than Imperva)
      os="windows" (fixed, not random)
      no browserforge fingerprint
  └─ Warm-up: page.goto(/sv, timeout=30s) + sleep(3) + cookie dismiss        [comeon_multileague.py:194-211]
  └─ Sort sports by SPORT_PRIORITY (fast first, slow last)
  └─ FOR each sport:
      if not first sport: PROACTIVE PAGE RECYCLE                              [comeon_multileague.py:223-261]
        close old page → new_page() → page.goto(/sv) → sleep(2) → cookie dismiss
        if page recycle fails: full _cleanup_camoufox() + _ensure_camoufox()
      _extract_single_sport(sport):
        try API league discovery (faster path)
        fall back to DOM league discovery
        for each league:
          page.goto(league URL)
          DOM scrape via comeon_dom_parser
```

## 3. Resource model

| Resource | Coolbet | ComeOn |
|---|---|---|
| Camoufox subprocess | 1 instance, recycled per sport | 1 instance, page recycled per sport |
| Memory | ~1-1.5 GB Camoufox + Firefox renderer | ~1.5 GB |
| Page reuse | yes — single page recycled between sports | yes — single page, recycled between sports |
| Fallback | `BrowserTransport.page` (Chromium) — CDP only | `BrowserTransport.page` |
| Proxy | `get_proxy_dict()` → Bahnhof SOCKS5 | same |
| Fingerprint | `browserforge.FingerprintGenerator(browser="firefox", os=("windows","macos"))` per launch | none — uses Camoufox defaults |
| Cookie persistence | `~/.cache/camoufox-coolbet/` (config: `_PROFILE_DIR`) | none — fresh per launch |
| Humanize level | `True` (default 1.0) — mouse jitter + scroll | `0.2` (light) |

## 4. Lifecycle

### Per-extraction
- **Coolbet:** lazy init → page reuse with proactive `_recycle_page()` between sports → close on `extractor.close()`. The `_camoufox_unavailable` class flag prevents repeated import errors.
- **ComeOn:** lazy init → page recycle per sport → close on `extractor.close()`.

### Cleanup — the leak source
- `_cleanup_camoufox()` calls `await self._camoufox_browser.__aexit__(None, None, None)` ([coolbet.py:213](../../backend/src/providers/coolbet.py#L213), [comeon_multileague.py:154](../../backend/src/providers/comeon_multileague.py#L154))
- This signals Camoufox to terminate its Firefox subprocess and clean up the temp profile.
- **The exception handlers swallow `(Exception, OSError, ValueError)` silently** with comment "Camoufox subprocess may raise 'I/O operation on closed pipe' during shutdown — this is benign and expected."
- However, when Camoufox's subprocess is unresponsive (under memory pressure or after a hung navigation), `__aexit__` may return without actually killing the Firefox process. The Firefox process becomes orphaned — that's exactly what the watchdog logs as `[BrowserTransport] force-killed N/N hung browser processes` ([transport.py:735](../../backend/src/core/transport.py#L735)).

### Force-kill source confirmation
The 30+ overnight `force-killed hung browser processes` events between 18:58
and 22:33 UTC almost all came from this tier (camoufox subprocesses) plus
the gecko brands' Playwright Chrome (cluster 5). Both clusters share the
same root cause: closing the browser inside a retry loop (gecko) or under
exceptions during recycle (comeon) leaves orphaned processes that the
container watchdog has to clean up.

## 5. Smells

### Coolbet

| # | File:line | Smell | Impact |
|---|---|---|---|
| **A** | [coolbet.py:108-127](../../backend/src/providers/coolbet.py#L108) | **Per-sport `_recycle_page` close+reopen.** Each recycle is `page.close() + browser.new_page()` = ~2 s. Some pages hang on close (Imperva session not torn down cleanly) → `_cleanup_camoufox` triggers, full relaunch (~10 s). | -2 s × N sports per cycle minimum; cliff to 10 s on hang. |
| B | [coolbet.py:140-156](../../backend/src/providers/coolbet.py#L140) | `_ensure_camoufox` does `page.evaluate("() => true", timeout=5000)` to test page liveness — 5 s wait per call when page is dying. | Up to 5 s overhead on every sport, every cycle. |
| C | [coolbet.py:267-272](../../backend/src/providers/coolbet.py#L267) | Hardcoded scroll simulation: `sleep(2) + scrollTo(0,300) + sleep(1) + scrollTo(0,600) + sleep(2) + scrollTo(0,0)` = 5 s of fixed waits. Purpose is to satisfy Imperva's mouse-track. Once we've passed challenge once, subsequent navs in the same context don't need it. | -5 s per session init. |
| D | [coolbet.py:170](../../backend/src/providers/coolbet.py#L170) | `_camoufox_browser = await AsyncCamoufox(...).__aenter__()` — entering `__aenter__()` manually instead of `async with`. Pairs with manual `__aexit__` in cleanup. Ergonomically fragile but functional. | Code clarity. |
| E | [coolbet.py:178-184](../../backend/src/providers/coolbet.py#L178) | `browserforge.FingerprintGenerator` runs every launch; if browserforge is missing, falls through silently. Adds ~200 ms per launch. | Marginal. |

### ComeOn

| # | File:line | Smell | Impact |
|---|---|---|---|
| **F** | [comeon_multileague.py:228-261](../../backend/src/providers/comeon_multileague.py#L228) | **Per-sport recycle: close page → new page → goto(/sv) → sleep(2) → dismiss cookies.** With 7 sports per cycle, that's 7 × ~5-7 s = 35-50 s pure overhead per run. The recycle is needed to prevent SPA-state crashes (acknowledged in code comment) but **the warm-up after recycle is repeated work** — fresh page, same homepage. | 35-50 s/cycle wasted; the per-sport navigation cost compounds. |
| G | [comeon_multileague.py:240](../../backend/src/providers/comeon_multileague.py#L240) | After page recycle: `page.goto(/sv, timeout=30000)` — 30 s timeout for what should be a sub-1 s navigation in cached state. | If Cloudflare flags, full 30 s wasted before the recovery path triggers. |
| H | [comeon_multileague.py:271-275](../../backend/src/providers/comeon_multileague.py#L271) | `if not all_events: raise RetryableError(0 events)` — single failure mode. Doesn't distinguish "all sports failed" vs "0 events because comeon site was down". | Diagnostic gap. |
| I | [comeon_multileague.py:130-138](../../backend/src/providers/comeon_multileague.py#L130) | Camoufox params: `geoip=False, humanize=0.2, os="windows"` — coolbet uses `geoip=True, humanize=True, os=random`. Inconsistent fingerprint approach across the same tier. If we ever want shared Camoufox infrastructure, the divergent params block reuse. | Architectural drift. |
| J | (separate file) [comeon_dom_parser.py](../../backend/src/providers/comeon_dom_parser.py) + [comeon_dom_js.py](../../backend/src/providers/comeon_dom_js.py) | DOM scraping is split across 3 files (738 lines total just for parsing logic). Compare to other DOM-scrape providers that fit in 1 file. | Maintenance complexity. |

### Cross-cluster

| # | File:line | Smell | Impact |
|---|---|---|---|
| **K** | both `_cleanup_camoufox` | Exception swallow `(Exception, OSError, ValueError)` masks orphaned Firefox processes. We don't log when the subprocess didn't actually exit. | Hidden process leaks → eventual force-kill by container watchdog. |
| L | (architectural) | Both providers run their OWN Camoufox subprocess. With max_concurrent_browsers=1 in this tier, only 1 runs at a time — so there's NO sharing benefit from a class-level Camoufox manager. But across tiers, both browser_soft and browser_antibot run independent browsers. | Memory cost. CDP shared host browser doesn't help here (Camoufox is not Chromium). |
| M | (architectural) | `_camoufox_unavailable` class-flag is per-class but cooldown reset never happens. If install fixes a missing module mid-process, we'd never reload. | Operational corner case. |
| N | [factory.py:142-150](../../backend/src/factory.py#L142) | Coolbet uses `BrowserTransport(headless=True, use_proxy=True)` as its "transport" parameter, but Camoufox is launched separately via `AsyncCamoufox`. The transport is **only used as a fallback** (CDP path). Counter-intuitive — looking at factory.py would suggest both tiers run on Playwright. | Code clarity. |

## 6. Open-source comparable

| Project | What it does differently |
|---|---|
| [`camoufox`](https://github.com/daijro/camoufox) (the library we use) | Stable, but its Python wrapper requires manual `__aenter__/__aexit__` if not using `async with`. Forum reports indicate `__aexit__` race during shutdown is known and the maintainer recommends `os.kill` + `process.wait` after timeout. We just suppress the exception. |
| [`undetected-playwright-python`](https://github.com/AtuboDad/playwright_stealth) | Plays in the Chromium-stealth space — different anti-bot vector. Doesn't beat Imperva's Reese84 reliably. We chose Camoufox specifically for Firefox-based fingerprint diversity. |
| [`patchright`](https://github.com/Kaliiiiiiiiii-Vinyzu/patchright) (already imported in our `transport.py:11`!) | Patched Playwright — CDP-level fingerprint hiding. Unused in this tier. Could replace Camoufox for ComeOn (Cloudflare is less aggressive than Imperva). |
| [`browser_use`](https://github.com/browser-use/browser-use) | Browser pool manager — reuses one Camoufox subprocess across multiple "sessions" via context isolation. We don't pool Camoufox — each provider has its own instance. |
| [Cloudflare Turnstile bypass services](https://2captcha.com/api-docs/turnstile) | We don't use commercial services — keeping costs low. Camoufox handles ComeOn's CF challenge today, but if CF tightens, we'd need Turnstile API. |

## 7. Verdict

- **Coolbet:** keep. The Imperva mouse-track simulation is annoying but works. The 5-second `evaluate("() => true")` page liveness check should be reduced.
- **ComeOn:** **per-sport recycle is the dominant cost.** 35-50 s/cycle of pure overhead. Investigate whether the SPA-state crash that motivated recycling can be solved with `context.clear_cookies()` instead of full page recreation.

Cluster-level: **the camoufox subprocess shutdown swallow is the prime suspect for force-killed-hung-browser events.** Adding explicit process-tree kill after `__aexit__` would close the leak.

## 8. Ranked fixes

| # | Fix | Provider | File:line | Impact | Effort |
|---|---|---|---|---|---|
| 1 | **Investigate ComeOn page-recycle alternative** — try `context.clear_cookies()` + `page.goto("about:blank") + page.goto(home)` in place of close+new. If SPA-state survives, eliminate the warm-up. | comeon | [comeon_multileague.py:228-261](../../backend/src/providers/comeon_multileague.py#L228) | Up to 35-50 s/cycle savings. | 4 h investigation |
| 2 | **Add explicit subprocess kill after `__aexit__`** — track Camoufox PIDs at launch, after `__aexit__` returns send SIGTERM with timeout, then SIGKILL on remaining. Closes the orphan leak. | both | [coolbet.py:209-220](../../backend/src/providers/coolbet.py#L209), [comeon_multileague.py:150-159](../../backend/src/providers/comeon_multileague.py#L150) | Eliminates "force-killed hung browser" log spam from this tier. | 2 h |
| 3 | **Reduce coolbet `_ensure_camoufox` liveness probe timeout** from 5 s to 1 s — if the page can't respond in 1 s, it's dead anyway. | coolbet | [coolbet.py:142](../../backend/src/providers/coolbet.py#L142) | -4 s per sport on dying-page recovery path. | 5 min |
| 4 | **Skip mouse-track scroll on subsequent navigations within session** — track `_imperva_passed` flag per-session, only run the scroll dance once per launch. | coolbet | [coolbet.py:267-273](../../backend/src/providers/coolbet.py#L267) | -5 s per session init. | 30 min |
| 5 | **Log when subprocess didn't exit** — wrap `__aexit__` in `wait_for(timeout=10)`; on timeout, log warning + send SIGKILL to the camoufox PID tree. | both | both `_cleanup_camoufox` | Observability + closes the silent-leak path. | (subset of #2) |
| 6 | **Reuse single Camoufox subprocess across both providers via lazy class-level manager** — have a `CamoufoxManager.get(profile_key)` that returns a fresh page from a shared subprocess. **Caveat:** with `max_concurrent_browsers=1`, only one runs at a time anyway, so the gain is per-cycle launch cost (-1 launch on the second provider). | both | new `camoufox_manager.py` | Marginal — 1 fewer launch per 15 min. | 4 h |
| 7 | **Try `patchright` instead of Camoufox for ComeOn** — Cloudflare is less aggressive than Imperva; patchright (already imported in [transport.py:11](../../backend/src/core/transport.py#L11)) might suffice and would unify ComeOn into the existing `BrowserTransport` infrastructure. | comeon | new test path | Eliminates a whole subprocess class. Requires validation against Cloudflare. | 1-2 days |
| 8 | **Diagnose `0 events` failure mode in ComeOn** — distinguish "all sports failed" vs "site genuinely returned no events" vs "cf block". Per-sport granular logging + raise specific exceptions. | comeon | [comeon_multileague.py:271-275](../../backend/src/providers/comeon_multileague.py#L271) | Diagnostic. | 1 h |

**Minimum-viable bundle (1 + 2 + 3 + 4):** ~7 h. Closes the orphan-process leak + cuts ComeOn's per-sport overhead.
**Recommended (1-5 + 8):** ~8 h. Adds observability.
**Ambitious (with 7):** ~3 days, conditional on patchright validation.

## 9. Re-introduction notes

> Filled in after fixes ship.

- [ ] Force-killed-hung-browser events from this tier (currently ~30/night; target: 0):
- [ ] ComeOn duration before vs after recycle change (target: 1134 → ~700 s):
- [ ] Coolbet duration before vs after probe timeout fix:
- [ ] Camoufox launch count per cycle (target: 1, not 2 unless concurrent):
- [ ] Distinct error categories in metrics (currently 1 generic RetryableError):
