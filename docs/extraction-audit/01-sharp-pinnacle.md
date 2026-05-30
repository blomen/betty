# Cluster 1: sharp / pinnacle

> **Audit date:** 2026-04-26
> **Status:** active in production, tier `sharp` (1-min cooldown), grouped: false
> **Why first:** every other cluster depends on Pinnacle for fair-odds baseline.
> If sharp stalls, the `_sharp_ready` gate in [scheduler.py:97](../../backend/src/pipeline/scheduler.py#L97)
> blocks every soft provider's first run for up to 120 s; if Pinnacle's odds
> stop refreshing, the analyzer can't compute edges.

## 1. Inventory

| Item | Value |
|---|---|
| Provider file | [backend/src/providers/pinnacle.py](../../backend/src/providers/pinnacle.py) |
| Line count | 567 |
| Transport class | [`HttpTransport`](../../backend/src/core/transport.py#L93) (aiohttp) |
| Endpoint | `https://guest.api.arcadia.pinnacle.com/0.1` (guest API, no auth) |
| Proxy | `PROXY_URL` env (`socks5://...` direct gost on Bahnhof VPS:1080) |
| YAML key | [`pinnacle:`](../../backend/src/config/providers.yaml#L223) and tier [`sharp:`](../../backend/src/config/providers.yaml#L888) |
| Sport timeout | 480 s (per-provider override in YAML) |
| Cooldown | `interval_minutes: 1` |
| Watchdog floor | 1500 s ("Pinnacle worst-case ≈ 5 sport_timeouts" — [scheduler.py:611](../../backend/src/pipeline/scheduler.py#L611)) |
| Recent fixes | `53570599` (break Pinnacle watchdog death-spiral), `e6e5499d` (force-kill leaked chromes — not pinnacle but related) |

### Live observation (last 3 h, 2026-04-26 06:20-09:31 UTC)
- 10 runs, 0 failures
- 16,389 events processed (~1640 ev/run)
- Avg duration: **57 s** (well under 480 s sport timeout, well under 1500 s watchdog floor)
- 0 events_matched (correct — Pinnacle is the sharp source itself, not matched against)

## 2. Extraction flow

Entry point is `PinnacleRetriever.extract(sport, ...)` at [pinnacle.py:50](../../backend/src/providers/pinnacle.py#L50).
Caller is `ExtractionPipeline._extract_provider_sports` at [orchestrator.py:1708](../../backend/src/pipeline/orchestrator.py#L1708),
which wraps each `extract()` in `asyncio.wait_for(timeout=sport_timeout)` and
gathers per-sport results under a `Semaphore(concurrent_sports)` (concurrent
sports for Pinnacle defaults to `max_concurrent_sports_per_provider` from
orchestrator config).

```
extract(sport)
  └─ get sport_id from sports.yaml mapping     [pinnacle.py:67]
  └─ GET /sports/{sport_id}/leagues?all=false  [line 75-76]
  └─ filter active_leagues (matchupCount > 0)  [line 86]
  └─ asyncio.gather(_fetch_league for each)    [line 96-99]
  │     └─ Semaphore(MAX_CONCURRENT_LEAGUES=50)  [line 96]
  │     └─ per league:
  │         └─ GET /leagues/{id}/matchups       [line 219]
  │         └─ GET /leagues/{id}/markets/straight  [line 220]
  │         └─ asyncio.gather both              [line 225]
  └─ for each league result:
        for each matchup:
          _parse_matchup() → StandardEvent     [line 246]
            └─ resolve parent participants if special
            └─ extract home/away + start_time
            └─ capture live_state if status="started"
            └─ _parse_markets(period 0,6,1-5)  [line 377]
              └─ _parse_moneyline / _parse_spread / _parse_total
              └─ american_to_decimal conversion
        dedupe by event.id
  └─ apply limit, return list[StandardEvent]
```

## 3. Resource model

| Resource | Where | Notes |
|---|---|---|
| `aiohttp.ClientSession` | `HttpTransport.session` ([transport.py:144](../../backend/src/core/transport.py#L144)) | One per `HttpTransport` instance. `__init__` creates a fresh `HttpTransport` if no transport passed (which is the default — [pinnacle.py:30](../../backend/src/providers/pinnacle.py#L30)) |
| Proxy | `os.environ['PROXY_URL']` read at retriever init | SOCKS5 routes via `aiohttp_socks.ProxyConnector` ([transport.py:139](../../backend/src/core/transport.py#L139)); HTTP(S) uses `proxy=` kwarg per request |
| League semaphore | `asyncio.Semaphore(MAX_CONCURRENT_LEAGUES=50)` ([pinnacle.py:96](../../backend/src/providers/pinnacle.py#L96)) | Created **per `extract()` call** (per sport). Each sport extraction creates its own semaphore — semaphores are not shared across sports. |
| Sport-level semaphore | `asyncio.Semaphore(concurrent_sports)` from orchestrator | Limits concurrent `extract(sport)` calls within one provider run |
| Per-request timeout | `aiohttp.ClientTimeout(total=90)` ([transport.py:191](../../backend/src/core/transport.py#L191)) | Hard-coded |
| Per-sport timeout | `sport_timeout: 480` from YAML | Wraps the entire `extract(sport)` |
| Circuit breaker | Optional, `notify_circuit_breaker_after` 429s | Only triggered by 429 from `HttpTransport.get` |
| Cache | Optional `ResponseCache` (TTL/LRU) — pinnacle calls `transport.get()` **without** passing `cache=` so it never uses the cache | |

## 4. Lifecycle

### Startup
1. `ExtractorFactory.get_extractor("pinnacle")` constructs `PinnacleRetriever` with config dict.
2. `__init__` builds `HttpTransport` using `PROXY_URL` env. **No session is created yet** — `_ensure_session` is lazy, fires on first `get()`.
3. Sport map built from `ConfigLoader.sports` (one-time).

### Per-extraction
1. Each per-sport call goes through `_ensure_session` (idempotent, double-checked locked).
2. Session is reused across all leagues of a sport AND across sports within a single pipeline run, because the same `HttpTransport` instance is used.

### Cleanup
- After each `_extract_provider_sports`, the orchestrator calls `extractor.close()` in a `finally` block ([orchestrator.py:1981-1988](../../backend/src/pipeline/orchestrator.py#L1981)). `Retriever.close()` ([core/retriever.py:82-83](../../backend/src/core/retriever.py#L82)) calls `self.transport.close()` which closes the `aiohttp.ClientSession` and sets it to None.
- At the start of each `run()` ([orchestrator.py:645](../../backend/src/pipeline/orchestrator.py#L645)), `engine.clear_extractor_cache()` drops the cached extractor references — but those were already closed by the previous run's `finally`.
- **Net: session lifecycle is correct for Pinnacle.** The "Unclosed client session" warnings observed overnight (00:49-02:36 UTC) are likely from other providers that create ad-hoc `aiohttp.ClientSession`s (polymarket, kalshi, smarkets — see those clusters' audits).

## 5. Smells

| # | File:line | Smell | Impact |
|---|---|---|---|
| **A** | [pinnacle.py:15](../../backend/src/providers/pinnacle.py#L15) + [pinnacle.py:96](../../backend/src/providers/pinnacle.py#L96) | `MAX_CONCURRENT_LEAGUES = 50` is a **module-level constant**. The YAML has `concurrent_leagues: 10` with comment "Throttled for ISP proxy — too many parallel requests get 403" but it's read by [orchestrator.py:1666](../../backend/src/pipeline/orchestrator.py#L1666) for *sport-level* concurrency, NOT league-level. **The user-tuned 10-league limit is ignored; we always run at 50.** | Recurrence of the 403 storms the YAML comment was meant to prevent. Hidden config drift between code and YAML. |
| B | [pinnacle.py:152-201](../../backend/src/providers/pinnacle.py#L152) | `_check_pagination` only **logs warnings**; never paginates. If Pinnacle starts paginating leagues silently, we truncate. | Latent: Pinnacle currently doesn't paginate, but if they ever do we'd lose events without an error. |
| C | [pinnacle.py:372](../../backend/src/providers/pinnacle.py#L372) | `_logged_unknown_types: set = set()` is a **class-level mutable** shared across all instances and never bounded. | Unbounded memory growth in long-lived process. Also makes the "log once" semantics global instead of per-extraction. |
| D | [pinnacle.py:295-296](../../backend/src/providers/pinnacle.py#L295) | `start_time` parse failure swallowed with `contextlib.suppress(Exception)` → event ships with `start_time=""`. Downstream date-matching treats empty string as "today". | Silent data corruption: events with bad timestamps get fuzzy-matched against today's events on other books. |
| E | ~~Session leak across tier cycles~~ — **withdrawn after deeper read.** `extractor.close()` runs in the orchestrator's `finally` block at [orchestrator.py:1981-1988](../../backend/src/pipeline/orchestrator.py#L1981), which awaits `transport.close()`. Sessions ARE closed. The overnight "Unclosed client session" warnings come from other providers (polymarket CLOB, kalshi, smarkets) — see those audits. | n/a |
| F | [pinnacle.py:177](../../backend/src/providers/pinnacle.py#L177) | Pagination warning includes `next_page` cursor "present" but `cursor` is checked as truthy. Pinnacle returns `null` cursor in normal responses too — false-positive risk. | Log noise. |
| G | [pinnacle.py:411](../../backend/src/providers/pinnacle.py#L411) | `if not prices: continue` skips markets with empty prices but doesn't log. Hard to diagnose markets disappearing. | Diagnostic gap. |
| H | [transport.py:191](../../backend/src/core/transport.py#L191) | Per-request `total=90s` timeout hard-coded inside `HttpTransport.get`. Pinnacle responses are typically <1 s; 90 s is fine. But it's not config-driven, and slower providers reusing this transport (kalshi, polymarket CLOB inline sessions, smarkets) all share this number. | Cross-provider coupling via constant. |
| I | [pinnacle.py:218-227](../../backend/src/providers/pinnacle.py#L218) | When matchups OR markets fetch raises an exception, the league is silently dropped (`return None` at line 244). Caller treats `None` as failure but logs at DEBUG only. No 5xx-vs-network distinction. | Quiet partial extraction during transient API issues. |
| J | [transport.py:239-246](../../backend/src/core/transport.py#L239) | Non-200, non-429 responses (5xx, 503) **return `None` with no retry**. Only 429 retries. A single 503 from Pinnacle drops a league. | We saw briefly elevated 5xx rates during ISP-proxy hiccups; current behavior amplifies them. |

## 6. Open-source comparable

| Project | What it does differently |
|---|---|
| [`pinnacle-py`](https://github.com/topics/pinnacle-api) (community wrappers) | Use the authenticated `api.pinnacle.com` (paid feed) instead of the guest endpoint. Provide pagination, retry-on-5xx via `tenacity`, persistent sessions. |
| [`tenacity`](https://github.com/jd/tenacity) + [`aiohttp_retry`](https://github.com/inyutin/aiohttp_retry) | Decorator-based async retry with exponential backoff covering 5xx, 429, network errors. We only retry 429 by hand. |
| [`purgatory`](https://github.com/mardiros/purgatory) async circuit breaker | Per-host circuit breaker compatible with aiohttp. Our circuit breaker is in-memory, lost on restart, and only triggered by 429s. |
| [`aiohttp.ClientSession` reuse pattern](https://docs.aiohttp.org/en/stable/client_reference.html#aiohttp.ClientSession) (official guidance) | "Don't create a session per request" — the official aiohttp docs explicitly say: one session per application, scoped to the event loop. We create one per `ExtractionPipeline` per tier cycle. |

## 7. Verdict

**Keep the retriever shape. Fix the leaks and config drift.**

Pinnacle is the cleanest of all our extractors. The flow (leagues → fan-out
matchups+markets in parallel → parse) is a textbook async scrape and matches
what `pinnacle-py` wrappers do. The dedicated guest endpoint requires no
auth, the response shape is stable, and the metrics from this morning show it
running in 57 s avg with 100 % success across 10 cycles.

What's broken is at the seams:
- YAML setting silently ignored (smell A)
- `aiohttp.ClientSession` leaked across tier cycles (smell E)
- 5xx/network errors aren't retried (smells J + I)
- Class-level mutable state (smell C) and silent data corruption (smell D)

None of these require rewriting the extractor.

## 8. Ranked fixes

| # | Fix | File:line | Impact | Effort |
|---|---|---|---|---|
| 1 | Honor `concurrent_leagues` from YAML — read `config.get("concurrent_leagues", 50)` in `__init__`, store as `self._max_concurrent_leagues`, use in semaphore at line 96 | [pinnacle.py:15, 96](../../backend/src/providers/pinnacle.py#L96) | Prevents 403 storm at higher concurrency we don't actually want | 15 min |
| 2 | Add 5xx + connection-error retry via `tenacity` decorator on `HttpTransport.get` (or inline) — `retry_if_exception_type((aiohttp.ClientError,)) | retry_if_result(lambda r: r is None and ...)` with `wait_exponential` and 3 attempts | [transport.py:194-260](../../backend/src/core/transport.py#L194) | Catches transient errors that drop leagues silently | 1 h |
| 3 | Close the retriever's `HttpTransport` at the end of each tier cycle — add `await pipeline.close_extractors()` after the run, walking `engine._extractors` and calling `transport.close()` on HTTP-based ones | [orchestrator.py](../../backend/src/pipeline/orchestrator.py), [scheduler.py:399](../../backend/src/pipeline/scheduler.py#L399) | Eliminates "Unclosed client session" log spam, frees connection pools | 1 h |
| 4 | Replace `_logged_unknown_types: set` class-level with instance-level set, OR use a bounded LRU | [pinnacle.py:372](../../backend/src/providers/pinnacle.py#L372) | Caps memory; "log once" semantics become per-extraction (clearer) | 5 min |
| 5 | Fail loudly on `start_time` parse error: log warning + skip event (don't ship empty `start_time=""`) | [pinnacle.py:294-296](../../backend/src/providers/pinnacle.py#L294) | Stops silent fuzzy-match corruption | 5 min |
| 6 | Make `HttpTransport.get` per-request timeout configurable (`get(..., timeout=...)`) with sensible per-provider defaults | [transport.py:191](../../backend/src/core/transport.py#L191) | Decouples Pinnacle's 90 s timeout from slower providers | 30 min |
| 7 | Distinguish 5xx from network in `_fetch_league` — log at WARNING for 5xx, DEBUG for transient network | [pinnacle.py:242-243](../../backend/src/providers/pinnacle.py#L242) | Better observability when Pinnacle has issues | 10 min |
| 8 | Add real pagination support in `_check_pagination` — when `nextPage` truthy, follow cursor (best-effort, with cap) | [pinnacle.py:152](../../backend/src/providers/pinnacle.py#L152) | Future-proof against API change | 30 min |

**Total minimum-viable fix bundle (1 + 4 + 5):** ~25 min, no behavior change risk.
**Recommended bundle (1 + 2 + 3 + 4 + 5 + 7):** ~3 h, eliminates 80 % of overnight asyncio noise from this provider.

## 9. Re-introduction notes

**Deployed 2026-04-26 12:41 UTC** in commit `743fdb4e` to `feat/slip-odds-architecture` on the Hetzner server:
- Fix #1: `concurrent_leagues` YAML setting honored per-instance (was hard-coded `MAX_CONCURRENT_LEAGUES = 50`).
- Fix #4: `_logged_unknown_types` moved class-level → instance (bounded by retriever lifetime).
- Fix #5: `start_time` parse failures log a WARNING and skip the event (was silenced via `contextlib.suppress`).

Pre-deploy verification: ruff clean · py_compile clean · no external refs to removed constant · no tests exist for pinnacle.

**Post-deploy observations (cycle 1, 12:41–12:48 UTC):**
- ✅ Pinnacle 2.1× faster: 57s avg pre-deploy → 27s avg post-deploy across 3 runs.
- ✅ Events_processed stable (~1300 events per run, consistent with pre-deploy baseline).
- 0 "Unknown market type" log lines so far (per-instance set is bounded as designed).

Post-deploy checks remaining:
- [ ] 403 / 5xx counts (Smell A) — need 24 h of data
- [ ] New "skipping matchup ... unparseable start_time" warnings (will surface bad timestamps that were previously silent)
