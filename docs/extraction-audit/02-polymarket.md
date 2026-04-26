# Cluster 2: polymarket

> **Audit date:** 2026-04-26
> **Status:** active in production, tier `polymarket` (5-min cooldown), grouped: false
> **Role:** event-matching only (NOT a sharp source). Provides odds for events
> Pinnacle doesn't cover (esports, prop markets, weekend slates). De-vigging
> with Polymarket prices is explicitly disallowed in [analysis/value.py](../../backend/src/analysis/value.py).

## 1. Inventory

| Item | Value |
|---|---|
| Provider file | [backend/src/providers/polymarket.py](../../backend/src/providers/polymarket.py) |
| Line count | 1729 (the longest provider file in the repo) |
| Transport class | [`HttpTransport`](../../backend/src/core/transport.py#L93) for Gamma API + **ad-hoc `aiohttp.ClientSession`** for CLOB |
| Endpoints | `https://gamma-api.polymarket.com` (Gamma — events, markets) + `https://clob.polymarket.com` (CLOB — order books) |
| Proxy | None (no `proxy=` passed at [polymarket.py:130-137](../../backend/src/providers/polymarket.py#L130)) — Polymarket is unblocked from German datacenter IPs |
| YAML key | [`polymarket:`](../../backend/src/config/providers.yaml#L241) and tier [`polymarket:`](../../backend/src/config/providers.yaml#L896) |
| Cooldown | `interval_minutes: 5` (event matching doesn't need real-time refresh) |
| Watchdog floor | 1500 s |
| `min_depth_usd` | 10 USD (markets below this depth skipped) |
| `fill_size_usd` | 25 USD (target VWAP fill size walked through ask side) |
| `MIN_VOLUME` | 100 USD (filters untraded 50/50 markets — empirical: $0 vol = 92 % untraded, $100+ = 20 % untraded) |

### Live observation (last 3 h, 2026-04-26 06:20-09:31 UTC)
- 10 runs, 0 failures
- 2,321 events processed (~232 ev/run)
- Avg duration: **86 s** (within `polymarket` tier 1500s watchdog floor)
- 0 events_matched (correct — polymarket is treated as its own first-class events, matched against by other soft books)

## 2. Extraction flow

`extract(sport)` is a thin wrapper around `extract_all()` with caching. The
real work is in `extract_all()` which fetches everything in 3 phases:

```
extract(sport) → if first call, populate _cached_events via extract_all()
              → return _events_by_sport[sport]

extract_all() — Phase 1: paginated Gamma API fetch
  while True:
    GET /events?active=true&closed=false&tag_id=100639&limit=500&offset=N
    extend all_raw; break when len(data) < 500
  end Phase 1

  Phase 1b: catch-up for recently closed events (last 48h)
  while True:
    GET /events?closed=true&end_date_min=cutoff&limit=500&offset=N
    extend all_raw with new IDs only
    break when len(data) < 500   ← NO MAX_PAGES GUARD
  end Phase 1b

  Phase 2: CLOB order book fetch (if use_clob_prices=true)
  pre-filter markets by volume/price → collect needed token_ids
  _fetch_clob_books(token_ids):
    create OWN aiohttp.ClientSession + TCPConnector(limit=100)
    Semaphore(50): for each token_id: GET /book?token_id=X
    parse asks → walk ask side → VWAP via _calc_vwap_from_asks
    populate _clob_prices, _clob_depth, _clob_bids, _clob_asks
  end Phase 2

  Phase 3: parse events
  _parse_all() → for each item: _parse_event()
    parse title → home/away via _parse_teams (long prefix list)
    determine sport/league via SERIES_TO_SPORT or tag fallback
    if football/rugby: _combine_football_markets (3 binary → 1 1x2)
    else:
      _parse_market for moneyline (volume-rank, keep highest)
    _parse_map_winner_market for esports child_moneyline
    _parse_spread_market / _parse_map_handicap_market
    _parse_total_market
    dedupe spreads by abs(point), totals by point (keep highest volume)
    return StandardEvent
```

## 3. Resource model

| Resource | Where | Notes |
|---|---|---|
| Inherited `HttpTransport` | from `Retriever.__init__` ([polymarket.py:130-138](../../backend/src/providers/polymarket.py#L130)) | Used for ALL Gamma API calls. No proxy. |
| **Ad-hoc CLOB session** | [polymarket.py:323](../../backend/src/providers/polymarket.py#L323) | `aiohttp.ClientSession(connector=aiohttp.TCPConnector(limit=100, limit_per_host=100))` created PER `extract_all()` call. **Bypasses inherited HttpTransport entirely** — no proxy, no circuit breaker, no 429 handling, no consecutive-429 tracking. Properly closed via `async with`. |
| CLOB semaphore | `asyncio.Semaphore(50)` ([polymarket.py:277](../../backend/src/providers/polymarket.py#L277)) | Per-call. Limits 50 concurrent /book requests. Up from old 50-token chunks (which serialized ~5000 tokens for 1000+ s). |
| Per-request timeout | `aiohttp.ClientTimeout(total=8)` for /book ([polymarket.py:285](../../backend/src/providers/polymarket.py#L285)), `total=90` for Gamma via HttpTransport | OK |
| Cache (instance state) | `_cached_events: list = None`, `_events_by_sport: dict = None` ([polymarket.py:149-150](../../backend/src/providers/polymarket.py#L149)) | Populated on first `extract()` call, never invalidated within an extractor's lifetime. **OK in practice** because the orchestrator calls `engine.clear_extractor_cache()` at the start of every `run()` ([orchestrator.py:645](../../backend/src/pipeline/orchestrator.py#L645)) and `extractor.close()` in the `finally` ([orchestrator.py:1981](../../backend/src/pipeline/orchestrator.py#L1981)) — extractor instances live for one run only. |
| CLOB price caches | `_clob_prices`, `_clob_depth`, `_clob_bids`, `_clob_asks` dicts | Populated in Phase 2, read in Phase 3. Per-instance, fine within one run. |
| Cookie handling | None | Polymarket Gamma + CLOB are unauthenticated public APIs. |

## 4. Lifecycle

### Per-extraction
1. `extract(sport)` — first call populates cache, subsequent calls (per other sport) hit cache.
2. `extract_all()` runs the 3 phases in sequence. Phase 1 paginates Gamma. Phase 1b catches up closed events from last 48 h. Phase 2 fetches CLOB books (under its own ad-hoc session). Phase 3 parses + dedupes.

### Cleanup
- Same path as Pinnacle: orchestrator's `finally` calls `extractor.close()` → `transport.close()` (Gamma session). The CLOB session is already closed by `async with`.
- **Net:** no session leaks for polymarket — but the CLOB session escapes resilience features (proxy/breaker) by design, not by leak.

## 5. Smells

| # | File:line | Smell | Impact |
|---|---|---|---|
| **A** | [polymarket.py:320-327](../../backend/src/providers/polymarket.py#L320) | **CLOB ad-hoc session bypasses `HttpTransport`** — no circuit breaker, no 429 retry/backoff, no proxy plumbing, no consecutive-429 tracking. Polymarket CLOB rate-limits aggressively (1500 req/10s = 150 req/s); a 429 burst here doesn't trigger our circuit breaker, so the next pipeline cycle hammers it again. | Resilience hole. Recurrent 429 storms on CLOB would silently degrade pricing without breaker tripping. |
| **B** | [polymarket.py:238](../../backend/src/providers/polymarket.py#L238) | **VWAP gating condition is mathematically dead code.** `total_shares * (total_cost / total_shares if total_shares else price) < fill_size_usd` simplifies to `total_cost < fill_size_usd` (the multiply/divide cancel when `total_shares > 0`). Current behavior fills until the cumulative `total_cost` reaches `fill_size_usd` — which is what we want, but the code reads as if it's gating on price. | Confusing reader; works by accident. Latent bug if someone "fixes" the condition. |
| **C** | [polymarket.py:393-417](../../backend/src/providers/polymarket.py#L393) | Phase 1b catch-up loop has **no MAX_PAGES guard**. If Polymarket ever returns malformed pagination (e.g., a stuck offset cursor), this spins until the sport timeout fires. | Tail-risk; needs operator intervention. |
| D | [polymarket.py:15-104](../../backend/src/providers/polymarket.py#L15) | `SERIES_TO_SPORT` dict has **hardcoded year suffixes** (`nba-2026`, `mex-2025`, `ere-2025` etc.). Fallback at [line 1047](../../backend/src/providers/polymarket.py#L1047) strips `-20XX` suffix. Works through 2099, but maintenance burden grows yearly. | Maintenance. Periodic dict edits as new leagues appear. |
| E | [polymarket.py:271-277](../../backend/src/providers/polymarket.py#L271) | Comment claims "150 req/s" but `Semaphore(50)` doesn't actually rate-limit per second — it limits concurrent in-flight. Effective throughput depends on RTT. With 50 ms RTT, 50 concurrent → ~1000 req/s burst. Could exceed CLOB's 1500/10s = 150/s sustained. | Latent rate-limit risk. Need actual rate-limit middleware (token bucket, e.g. `aiolimiter`) not concurrency cap. |
| F | [polymarket.py:1212](../../backend/src/providers/polymarket.py#L1212) | `re.search(r"will\s+(.+?)\s+win", question_lower)` extracts team name from "Will X win" Yes/No markets. Greedy `.+?` plus substring match (`team_in_question in home_lower or home_lower in team_in_question`) can false-positive on prefix collisions. Example: "Will Real win" matches both "Real Madrid" and "Real Sociedad". | Wrong team attribution → wrong odds. Subtle correctness bug. |
| G | [polymarket.py:903-1030](../../backend/src/providers/polymarket.py#L903) | `_parse_teams` is a **128-line block of hardcoded prefix strip rules** for esports/tennis/cricket. Adding new tournaments requires code edit. | Maintenance. Slow to extend. |
| H | [polymarket.py:1006](../../backend/src/providers/polymarket.py#L1006) | `re.sub(r"\s*\([^)]+\)\s*", "", clean_title)` strips ALL parenthetical metadata, including legitimate parts of team names (e.g., "Manchester United (B)"). Risk is small — Polymarket doesn't typically use parenthetical team names — but it's an unbounded strip. | Tail-risk for edge teams. |
| I | [polymarket.py:341-465](../../backend/src/providers/polymarket.py#L341) | `extract_all()` is **227 lines** including all 3 phases inline. Refactor candidate. | Readability. Not behavior. |
| J | [polymarket.py:467-497](../../backend/src/providers/polymarket.py#L467) | `extract(sport)` first-call ignores `limit` parameter except for initial Gamma page size. If callers pass `limit=50`, they may expect 50 events per sport — but they get all-events-of-that-sport from cache. | Confusing API. Caller's `limit` is largely no-op once cache is populated. |
| K | [polymarket.py:751-901](../../backend/src/providers/polymarket.py#L751) | `fetch_resolved()` is 150 lines of resolution logic (winner extraction, spread/total winner from outcomePrices). Used by ML feedback loop and CLV resolution but completely separate from the main extract() path. | Code organization — could move to a `polymarket/resolved.py` submodule. |
| L | [polymarket.py:511](../../backend/src/providers/polymarket.py#L511) | `_parse_all` swallows per-event parse errors with DEBUG-only log. If Polymarket changes a field name, we silently drop events without raising. | Diagnostic gap during API changes. |

## 6. Open-source comparable

| Project | What it does differently |
|---|---|
| [`py-clob-client`](https://github.com/Polymarket/py-clob-client) (official) | Official Polymarket CLOB client. Handles auth, rate-limiting, retries. We don't use it because we want anonymous read-only access — but we could vendor its rate-limit logic. |
| [`gamma-api` Polymarket wrappers](https://github.com/topics/polymarket) | Most use one shared `aiohttp.ClientSession` for both Gamma + CLOB. We split them. |
| [`aiolimiter`](https://pypi.org/project/aiolimiter/) | Async token-bucket rate limiter. Decorator-based. Drop-in for Smell E (real per-second cap, not concurrency cap). |
| [`tenacity` async](https://tenacity.readthedocs.io/) | Retry decorator for transient errors. CLOB returns 429 + occasional 502 during Polymarket platform deploys — currently silently dropped. |

## 7. Verdict

**Keep the retriever. Fix the resilience hole and the dead-code condition.**

Polymarket is the only provider here that does pagination + CLOB-depth pricing
in a single coherent flow, and it's the most thoroughly battle-tested
(handles weird title formats across 7 sports, esports map markets, and binary
Yes/No markets with team-name extraction). The structure is fine. What's
broken is at the seams:

- CLOB ad-hoc session is a resilience hole (Smell A) — dual-mode HTTP
  (Gamma via `HttpTransport`, CLOB via raw `aiohttp.ClientSession`) means
  half our calls escape the safety net.
- VWAP condition (Smell B) works by accident.
- Catch-up loop unbounded (Smell C).
- Concurrency cap pretending to be rate limit (Smell E).
- Team-name substring matching (Smell F) is the only correctness bug.

## 8. Ranked fixes

| # | Fix | File:line | Impact | Effort |
|---|---|---|---|---|
| 1 | **Move CLOB book fetching through a `HttpTransport`** — either reuse the inherited one (set `clob_url` host explicitly) or instantiate a second `HttpTransport` for CLOB with its own circuit breaker | [polymarket.py:252-336](../../backend/src/providers/polymarket.py#L252) | Restores resilience parity (breaker + retry + 429 handling) for CLOB | 2 h |
| 2 | **Fix `_parse_teams` Yes/No team-name false-positive** — replace substring match with word-boundary check or tokenize home/away first; require ≥80 % token overlap | [polymarket.py:1212-1230](../../backend/src/providers/polymarket.py#L1212) | Correctness — eliminates wrong-team odds attribution | 1 h |
| 3 | **Add `MAX_PAGES` guard to Phase 1 + Phase 1b** — cap at 50 pages each (25k events is far above any realistic count) | [polymarket.py:361, 395](../../backend/src/providers/polymarket.py#L361) | Tail-risk: prevents infinite loop on malformed cursor | 10 min |
| 4 | **Replace `_calc_vwap_from_asks` dead-code condition** — simplify to clear `total_cost < fill_size_usd` so future readers don't "fix" it back to broken | [polymarket.py:238](../../backend/src/providers/polymarket.py#L238) | Clarity. No behavior change. | 5 min |
| 5 | **Use `aiolimiter` for CLOB token-bucket** at 100 req/s (60 % of stated 150/s ceiling) — replaces the `Semaphore(50)` concurrency cap with a real per-second cap | [polymarket.py:277](../../backend/src/providers/polymarket.py#L277) | Eliminates rate-limit-burst risk during fast Polymarket responses | 30 min |
| 6 | **Move `SERIES_TO_SPORT` to `config/polymarket-leagues.yaml`** — adding new leagues becomes a config change, not a code change | [polymarket.py:15-104](../../backend/src/providers/polymarket.py#L15) | Maintenance. Lower friction for ops. | 1 h |
| 7 | **Log Polymarket parse errors at WARNING level when error count > 5 % of events** — single-digit errors stay at DEBUG; surge surfaces visibly | [polymarket.py:508](../../backend/src/providers/polymarket.py#L508) | Observability during API changes | 15 min |
| 8 | **Split `polymarket.py` into 3-4 modules** (`__init__.py`, `parser.py`, `clob.py`, `resolved.py`) — makes the 1729-line file navigable. Pure refactor. | [polymarket.py](../../backend/src/providers/polymarket.py) | Readability. No behavior change. | 2 h |
| 9 | **Add unit tests for `_parse_teams`** with fixtures for all 30+ prefix patterns (ATP, ESPORTS, MMA, etc.) — currently no test guards against a regex change breaking team extraction | new `tests/providers/test_polymarket_parse_teams.py` | Regression safety for the most fragile code in this file | 2 h |

**Minimum-viable bundle (1 + 2 + 3 + 4):** ~3.5 h. Closes the resilience hole + fixes the correctness bug + tail-safety.
**Recommended bundle (1-7):** ~5.5 h. Adds rate-limit safety, observability, config-driven leagues.

## 9. Re-introduction notes

> Filled in after fixes ship.

- [ ] Fix bundle deployed (commit hash:)
- [ ] CLOB 429 / 5xx counts before vs after:
- [ ] Yes/No mismatched-team incidents (look for low-volume markets attributed to wrong team):
- [ ] Any regression in event count:
