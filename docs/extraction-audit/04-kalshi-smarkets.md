# Cluster 4: kalshi + smarkets

> **Audit date:** 2026-04-26
> **Status:** kalshi runs in its own `kalshi:` tier (5-min cooldown).
> Smarkets runs in `signal_international:` tier (5-min cooldown, grouped with marathon).
> **Role:** prediction-market sources. Kalshi is **playable** (own bankroll path);
> Smarkets is **signal-only** (the user is IP-banned from their account, so no
> placement). Both feed the analyzer's consensus side, neither is a sharp source.

## 1. Inventory

| Provider | File | Lines | Transport | Endpoint | Auth | YAML tier |
|---|---|---|---|---|---|---|
| kalshi | [providers/kalshi.py](../../backend/src/providers/kalshi.py) | 539 | **ad-hoc `aiohttp.ClientSession`** (bypasses HttpTransport) | `https://api.elections.kalshi.com/trade-api/v2/events` | none | `kalshi:` (own tier) |
| smarkets | [providers/smarkets.py](../../backend/src/providers/smarkets.py) | 495 | **ad-hoc `aiohttp.ClientSession` + SOCKS5 ProxyConnector** | `https://api.smarkets.com/v3` | none (geoblocked) | `signal_international:` |

Both providers are **structurally similar**: both reimplement transport, both
take `circuit_breaker` and `rate_limit_config` parameters and ignore them, both
return None on any non-200 with no retry, both create their own session per
`extract()` call.

### Live observation (last 3 h, 2026-04-26 06:20-09:31 UTC)

| Provider | Runs | Failures | Events | Avg duration |
|---|---|---|---|---|
| kalshi | 10 | 0 | 390 | **111 s** (39 ev/run) |
| smarkets | 4 | 0 | 332 | **502 s** (83 ev/run, ~6 s/event) |

Smarkets at 502 s avg is the slowest non-browser provider. With a 5-min cooldown,
the cycle time (run + cooldown) is ~13 min, so smarkets effectively runs every
~13 minutes — not its target 5 min.

## 2. Extraction flows

### Kalshi
```
extract(sport)
  └─ Open ad-hoc aiohttp.ClientSession()                  [kalshi.py:502]
  └─ for _ in range(50):                                  [kalshi.py:503]   ← magic cap
        page_url = base + (cursor if any)
        GET (timeout=15)
        body = await resp.json()
        events += body["events"]
        parsed += parse({events}, sport) — filters by sport AFTER parsing
        if limit and len(parsed) >= limit: break
        cursor = body["cursor"] or None
        if not cursor: break
  return parsed[:limit]
```

**Key inefficiency:** The Kalshi `/events` endpoint is sport-agnostic (it
returns all sports mixed together). For 17 sports per cycle, we walk the same
~10k-event paginated stream up to 17 times per provider run — once per sport
in the orchestrator's per-sport loop. Despite the comment at [line 491-495](../../backend/src/providers/kalshi.py#L491)
saying "We now parse each page incrementally and stop once we have enough
sport-relevant events", the early-stop only fires once `limit` is reached
within the current `extract(sport)` call. Across 17 sports, we still re-walk
the page.

### Smarkets
```
extract(sport)
  └─ Build aiohttp_socks.ProxyConnector if SOCKS5 proxy   [smarkets.py:329-335]
  └─ Open aiohttp.ClientSession(connector)                [smarkets.py:341]
  └─ Page through /events?type_domain=<sport>&type=<sport>_match  [smarkets.py:342-362]
        max 20 pages, stop early if len >= limit
  └─ apply caller's limit cap to in_scope                 [smarkets.py:374-375]
  └─ Semaphore(CONCURRENT_MARKET_FETCHES=3)               [smarkets.py:377]
  └─ asyncio.gather(build_event(e) for e in in_scope)
        each build_event:
          GET /events/{eid}/markets/           ← 1 RTT
          for each kept market:
            asyncio.gather(
              GET /markets/{id}/contracts/,    ← 1 RTT
              GET /markets/{id}/last_executed_prices/,  ← 1 RTT
              GET /markets/{id}/quotes/        ← 1 RTT
            )
          parse_market_prices() on each
          build StandardEvent
```

**Cost model:** ~1 page list + 1 markets call + 3 inner calls per event ≈
4 RTTs per event. With ~80 events and Sem(3), total RTTs ≈ 320 / 3 = ~107
sequential RTT-equivalents at ~1.5 s each (proxy latency) = 160 s — but
observed 502 s implies real RTT closer to 5 s under proxy load.

## 3. Resource model

### Kalshi
| Resource | Notes |
|---|---|
| Inherited `Retriever.transport` | **None** — `super().__init__(config)` at [kalshi.py:476](../../backend/src/providers/kalshi.py#L476) doesn't pass a transport. So `self.transport = None`. |
| Ad-hoc session | `aiohttp.ClientSession()` per `extract()` ([kalshi.py:502](../../backend/src/providers/kalshi.py#L502)) — no headers, no proxy, no connector tuning |
| `extractor.close()` | Inherits `Retriever.close()` which `await self.transport.close()` — but `self.transport is None`. Raises `AttributeError`, caught by orchestrator's broad `except` and logged as "Extractor cleanup failed". **Silent design defect.** |
| Constructor params | `circuit_breaker`, `rate_limit_config` are accepted and ignored ([kalshi.py:475-482](../../backend/src/providers/kalshi.py#L475)) |
| Per-request timeout | 15 s hardcoded ([kalshi.py:506](../../backend/src/providers/kalshi.py#L506)) |
| Concurrency | None within extract — sequential pagination (~10 RTTs serial) |

### Smarkets
| Resource | Notes |
|---|---|
| Inherited `Retriever.transport` | **None** — same as Kalshi, `super().__init__(config)` skips transport ([smarkets.py:246](../../backend/src/providers/smarkets.py#L246)) |
| Ad-hoc session | `aiohttp.ClientSession(connector=ProxyConnector)` per `extract()` ([smarkets.py:341](../../backend/src/providers/smarkets.py#L341)) |
| Proxy | SOCKS5 via `aiohttp_socks.ProxyConnector.from_url(self.proxy_url)`. Falls back to direct if `aiohttp_socks` not installed (logs warning). Smarkets geoblocks Hetzner DE so direct = guaranteed 403. |
| `extractor.close()` | Same AttributeError as Kalshi |
| Constructor params | `circuit_breaker`, `rate_limit_config` accepted and ignored ([smarkets.py:240-246](../../backend/src/providers/smarkets.py#L240)) |
| Concurrency | `Semaphore(CONCURRENT_MARKET_FETCHES=3)` ([smarkets.py:238](../../backend/src/providers/smarkets.py#L238)) — was 8, dropped to 3 after rate-limit hits |
| Per-request timeout | 15 s hardcoded ([smarkets.py:302](../../backend/src/providers/smarkets.py#L302)) |

## 4. Lifecycle

### Per-extraction
- Kalshi: open session → paginate up to 50 pages → close session via `async with`
- Smarkets: open session+proxy → paginate up to 20 pages → fan out per-event with Sem(3) → close via `async with`

### Cleanup
- `extractor.close()` raises AttributeError (transport=None) but is swallowed by orchestrator's broad except.
- Sessions are closed via `async with` blocks. **No leak under normal completion.**
- **Risk under cancellation:** if the watchdog cancels mid-`gather`, in-flight tasks are destroyed but their sessions get GC'd later → emits `Unclosed client session` and `Task was destroyed but it is pending`. **This is the most likely source of the 20+ asyncio errors observed at 00:49-02:36 UTC overnight.**

## 5. Smells

### Cross-cluster

| # | Provider | File:line | Smell | Impact |
|---|---|---|---|---|
| **A** | both | [kalshi.py:475-482](../../backend/src/providers/kalshi.py#L475), [smarkets.py:240-262](../../backend/src/providers/smarkets.py#L240) | **Both providers bypass `HttpTransport` entirely.** `super().__init__(config)` is called without a transport. They reimplement session, retry, proxy, error handling. Bypasses circuit breaker (consecutive-429 detection, breaker tripping), shared `aiohttp.ClientSession` benefits, and the `Retry-After` honoring from `HttpTransport.get`. | Resilience hole. Both providers escape every safety net we have. |
| **B** | both | [kalshi.py:475](../../backend/src/providers/kalshi.py#L475), [smarkets.py:240](../../backend/src/providers/smarkets.py#L240) | **Constructor accepts `circuit_breaker` and `rate_limit_config` as parameters and stores `_circuit_breaker` but never uses it.** | Confusing API surface — looks like it integrates but doesn't. |
| **C** | both | [retriever.py:82-83](../../backend/src/core/retriever.py#L82) called on these | `extractor.close()` raises `AttributeError: 'NoneType' object has no attribute 'close'` because transport is None. Suppressed in orchestrator. | Latent: error log noise; if the orchestrator's `except` ever narrows, these break. |

### Kalshi-specific

| # | File:line | Smell | Impact |
|---|---|---|---|
| **D** | [kalshi.py:488-524](../../backend/src/providers/kalshi.py#L488) | **`/events` endpoint is sport-agnostic but `extract()` is called per-sport.** With 17 sports × 10 runs/3h, we walk the same ~10k events stream up to 170 times per cycle. Should be: extract once, cache, dispatch by sport — same pattern as polymarket's `_cached_events`. | Massive duplicated work. With 50-page cap × 200 events × 17 sports = ~170,000 redundant event-parse operations per pipeline cycle. |
| E | [kalshi.py:503](../../backend/src/providers/kalshi.py#L503) | `for _ in range(50)` — magic 50-page cap. No log when hitting the cap. | If Kalshi ever serves >10k events in a sport, we silently truncate. |
| F | [kalshi.py:506-511](../../backend/src/providers/kalshi.py#L506) | `except Exception as e: ... break` — single exception ends ALL pagination. No retry. | One transient 5xx ends the whole extraction. |
| G | [kalshi.py:298-314](../../backend/src/providers/kalshi.py#L298) | `_match_market_to_side` substring match: `sub in home_l or home_l in sub`. Same false-positive class as polymarket Smell F (e.g. "Real" matches "Real Madrid" and "Real Sociedad"). | Wrong-side odds attribution. Subtle correctness bug. |
| H | [kalshi.py:53-150](../../backend/src/providers/kalshi.py#L53) | NBA/NHL/MLB ticker→alias maps are 100+ lines of hardcoded data inside the provider file. NHL "ARI" still mapped to "coyotes" but they relocated to Utah Mammoth in 2024-25. | Data drift. Move to `config/kalshi-team-codes.yaml`. |
| I | [kalshi.py:209](../../backend/src/providers/kalshi.py#L209) | `_TITLE_PREFIX_RE = re.compile(r"^(game|match|leg|set)\s*\d+\s*:\s*", IGNORECASE)` — matches but doesn't include common prefixes like "Race", "Round", "Match Day". Tail risk for unusual series. | Latent miss. |

### Smarkets-specific

| # | File:line | Smell | Impact |
|---|---|---|---|
| **J** | [smarkets.py:411, 428](../../backend/src/providers/smarkets.py#L411) | **4 sequential HTTP calls per event under Sem(3) concurrency.** For 500 events: 500 × 4 / 3 ≈ 666 sequential RTT-equivalents at ~1.5-5 s through proxy = **15-55 minutes worst case**. The 502 s observed is mid-range. | Dominant cost. Could be ~3× faster with a smarter merged endpoint or batched fetch. |
| K | [smarkets.py:234, 238](../../backend/src/providers/smarkets.py#L234) | `MAX_PAGES = 20`, `CONCURRENT_MARKET_FETCHES = 3` are class-level constants — no per-call tuning. | Configuration drift. |
| L | [smarkets.py:130-140](../../backend/src/providers/smarkets.py#L130) | `_price_percent_string_to_odds` falls back to `(best_back + best_lay) // 2` integer arithmetic — loses 1-tick precision. | Marginal — Smarkets is signal-only so doesn't affect placement, but degrades odds quality fed to consensus. |
| M | [smarkets.py:153-157](../../backend/src/providers/smarkets.py#L153) | Module-level note acknowledges spread/total markets emit one contract per line and we silently skip them. Marked "follow-up I3" but no issue tracker reference. | Dropped market types — half of smarkets' surface area unused. |
| N | [smarkets.py:331-335](../../backend/src/providers/smarkets.py#L331) | If `aiohttp_socks` import fails, code logs warning and continues without proxy. Direct connection from Hetzner DE → 403 Security Check. The provider would fail silently with 0 events but no error. | Silent breakage on environment regression. |
| O | [smarkets.py:301-312](../../backend/src/providers/smarkets.py#L301) | `_fetch_json` returns None on any non-200 + on any exception. **No 429 backoff, no retry, no circuit breaker call.** Smarkets rate-limits aggressively (yaml comment says CONCURRENT_MARKET_FETCHES=8 hit 429s — dropped to 3). | Rate-limit hits silently degrade extractions; we have no visibility. |

## 6. Open-source comparable

| Project | What it does differently |
|---|---|
| [`kalshi-python`](https://github.com/Kalshi/kalshi-python) (official) | Has rate limiting, retries, and shared `requests.Session`. Synchronous-only — would need wrapping. |
| [`tenacity` / `aiohttp_retry`](https://github.com/inyutin/aiohttp_retry) | Decorator-based retry with backoff. Drop-in for `_fetch_json` in both. |
| [`purgatory` async circuit breaker](https://github.com/mardiros/purgatory) | Could wrap each provider's `_fetch_json` to give us breaker tripping for ad-hoc sessions. |
| [Smarkets Python wrapper](https://github.com/smarkets/smarkets-python) | Official client. Doesn't help us — we use unauthenticated public endpoints, but the rate-limit logic is reusable. |
| [`aiocache`](https://github.com/aio-libs/aiocache) | Per-call result cache. Solves the Kalshi sport-agnostic re-walk problem (Smell D) cleanly. |

## 7. Verdict

**Both retrievers are structurally redundant.** They reimplement transport
and bypass our resilience features. The right fix is to converge them onto
`HttpTransport` like Pinnacle / Polymarket / Cloudbet / Marathon do — not to
keep patching the ad-hoc sessions.

- **Kalshi:** medium-high effort to fix. The sport-agnostic endpoint is a
  caching opportunity (Smell D) — extract once per pipeline run, dispatch
  by sport. Combine with HttpTransport adoption.
- **Smarkets:** medium effort. SOCKS5 proxy is the only thing keeping it
  off `HttpTransport` — but `HttpTransport._ensure_session` already supports
  SOCKS5 via `aiohttp_socks.ProxyConnector` ([transport.py:139](../../backend/src/core/transport.py#L139)).
  We can pass `proxy=` to its constructor and remove all the ad-hoc connector
  building.

## 8. Ranked fixes

| # | Fix | Provider | File:line | Impact | Effort |
|---|---|---|---|---|---|
| 1 | **Migrate Kalshi to `HttpTransport`** — instantiate it in `__init__`, pass to `super().__init__`, replace ad-hoc session with `self.transport.get`. Add proper retry via HttpTransport's 429 path. | kalshi | [kalshi.py:475-524](../../backend/src/providers/kalshi.py#L475) | Restores breaker / 429 retry / shared session. Eliminates 'Unclosed client session' under cancellation. | 2 h |
| 2 | **Migrate Smarkets to `HttpTransport`** — same pattern. Pass `proxy=self.proxy_url` to HttpTransport (it handles SOCKS5 internally). Drop the bespoke ProxyConnector. | smarkets | [smarkets.py:240-341](../../backend/src/providers/smarkets.py#L240) | Same benefits + removes 100+ lines of duplicated transport code. | 2 h |
| 3 | **Cache Kalshi events at retriever level** — populate `_cached_events` on first `extract()` call, dispatch by sport on subsequent calls (mirror polymarket pattern at [polymarket.py:482-497](../../backend/src/providers/polymarket.py#L482)). | kalshi | [kalshi.py:488-524](../../backend/src/providers/kalshi.py#L488) | 17× reduction in redundant page-walks per pipeline cycle. | 1 h |
| 4 | **Fix Kalshi `_match_market_to_side` substring false-positive** — same fix pattern as polymarket Smell F (token-based matching with overlap threshold). | kalshi | [kalshi.py:298-324](../../backend/src/providers/kalshi.py#L298) | Correctness — prevents wrong-side odds when team names share substrings. | 1 h |
| 5 | **Move Kalshi NBA/NHL/MLB code maps to `config/kalshi-team-codes.yaml`** + add Utah Mammoth replacing Coyotes | kalshi | [kalshi.py:53-150](../../backend/src/providers/kalshi.py#L53) | Maintenance burden + immediate data fix (NHL ARI → utah mammoth). | 30 min |
| 6 | **Add 429/5xx retry to Smarkets `_fetch_json`** via `tenacity` decorator with exponential backoff. | smarkets | [smarkets.py:297-312](../../backend/src/providers/smarkets.py#L297) | Closes silent-failure on rate limits; surfaces issues to metrics. | 1 h |
| 7 | **Raise on missing `aiohttp_socks`** instead of falling back to direct (which guarantees 403). Make the import an explicit hard requirement at module level. | smarkets | [smarkets.py:331-335](../../backend/src/providers/smarkets.py#L331) | Surfaces environment regressions immediately instead of zero-event silently. | 10 min |
| 8 | **Add unit tests for `_extract_teams_from_title` (kalshi) + `extract_home_away_from_event_name` (smarkets)** with fixtures for "X at Y", "X vs Y", "Game N: X at Y", etc. | both | new tests | Regression safety for the two most fragile parsers. | 2 h |
| 9 | **Surface the spread/total skip in smarkets** — implement them properly (note I3 in [smarkets.py:148-152](../../backend/src/providers/smarkets.py#L148)). One-contract-per-line collapse needs careful handling. | smarkets | [smarkets.py:160-181](../../backend/src/providers/smarkets.py#L160) | Doubles smarkets' market coverage. Helps consensus. | 4 h |
| 10 | **Move ad-hoc constants to YAML** — `MAX_PAGES`, `CONCURRENT_MARKET_FETCHES`, `min_volume_usd`, `min_trades_24h` | both | various | Tunable without code change | 30 min |

**Minimum-viable bundle (1 + 2 + 3 + 4 + 6 + 7):** ~7.5 h. Eliminates the ad-hoc session / no-resilience pattern, fixes correctness bug, fixes wasteful re-walking, surfaces environment regressions.

**Recommended bundle (1-8):** ~10 h. Adds tests, data hygiene.

## 9. Re-introduction notes

> Filled in after fixes ship.

- [ ] Kalshi via HttpTransport: 'Unclosed client session' count over 24h before vs after:
- [ ] Kalshi total page-fetches per pipeline cycle before vs after caching:
- [ ] Smarkets duration before vs after:
- [ ] Smarkets 429 incidents visible in metrics:
- [ ] Kalshi false-side attribution incidents (look for low-confidence team matches):
