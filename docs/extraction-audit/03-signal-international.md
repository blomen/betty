# Cluster 3: signal_international (cloudbet, marathon) + retired stake

> **Audit date:** 2026-04-26
> **Status:** cloudbet runs in its own dedicated `cloudbet:` tier (5-min cooldown).
> Marathon runs in `signal_international:` tier alongside smarkets (5-min cooldown,
> grouped: false). Stake is in `factory.py` but **not in any tier** — disabled in
> `active:` list ([providers.yaml:1015](../../backend/src/config/providers.yaml#L1015))
> with comment "Cloudflare blocks datacenter IPs — needs residential proxy".
> **Role:** signal-only sources (no bet placement). Provide odds for consensus,
> not for value-vs-Pinnacle. Cloudbet is also playable (unlimited stake), but
> we treat it as signal in this cluster's scope.

## 1. Inventory

| Provider | File | Lines | Transport | Endpoint | Auth | YAML tier |
|---|---|---|---|---|---|---|
| cloudbet | [providers/cloudbet.py](../../backend/src/providers/cloudbet.py) | 343 | `HttpTransport` (aiohttp) + proxy | `https://sports-api.cloudbet.com/pub/v2/odds` REST | `X-API-Key` from `CLOUDBET_API_KEY` env | `cloudbet:` (own tier, 5 min) |
| marathon | [providers/marathon.py](../../backend/src/providers/marathon.py) | 326 | `HttpTransport` (aiohttp) + proxy | `https://www.marathonbet.com/en/betting/{sport}/` HTML | none (public HTML) | `signal_international:` (5 min, grouped with smarkets) |
| stake | [providers/stake.py](../../backend/src/providers/stake.py) | 223 | `HttpTransport` (aiohttp) + proxy | `https://stake.com/_api/graphql` POST | none (public GraphQL) | **disabled** ([providers.yaml:1015](../../backend/src/config/providers.yaml#L1015)) |

### Live observation (last 3 h, 2026-04-26 06:20-09:31 UTC)
| Provider | Runs | Failures | Events | Avg duration |
|---|---|---|---|---|
| cloudbet | 10 | 0 | 1,731 | **155 s** |
| marathon | 6 | 0 | 2,392 | **7 s** |
| stake | 0 | n/a | n/a | n/a (not scheduled) |

Cloudbet is **22× slower than Marathon** despite returning 28 % fewer events.
That's the headline.

## 2. Extraction flows

### Cloudbet — two-step, serial competitions
```
extract(sport)
  └─ map sport → cloudbet sport_key (football → soccer, etc.)
  └─ GET /sports/{sport_key} → list of categories → competitions
  └─ filter eventCount > 0 (active competitions only)
  └─ FOR EACH competition (SERIAL):                    [cloudbet.py:313]
        GET /competitions/{key}?markets=A&markets=B&markets=C
        for each event in comp_data.events:
          parse_event() → StandardEvent
          break inner loop if limit hit
  └─ return all events
```
Soccer alone: ~100 active competitions × ~300 ms RTT = **30 s minimum, sequential**.

### Marathon — single page fetch, regex parse
```
extract(sport)
  └─ map sport → URL segment (football → "Football")
  └─ GET https://www.marathonbet.com/en/betting/{segment}/  (full HTML page)
  └─ parse_page(html, sport):
        _split_events(html) — regex finditer all coupon-row divs
        for each (event_id, event_name, is_live, block):
          if is_live: skip
          parse_event_html(block) → StandardEvent
            extract data-sel JSON blobs
            positional decode: [0:N]=match-winner, [N:N+2]=total, [N+2:N+4]=spread
            parse start_time from "score-and-time" div
            parse league from data-event-treeName
  └─ apply limit slice
```
Speed: 7 s for ~400 events → mostly the single GET cost; regex is fast.

### Stake — single GraphQL POST (currently disabled)
```
extract(sport)
  └─ map sport → stake slug
  └─ POST /graphql {query: SportFixtures, variables: {sport, limit}}
  └─ parse data.sport.fixtures: each fixture → parse_outcomes_to_market
        first non-draw outcome = home, second = away, "draw"/"x"/"tie" = draw
```

## 3. Resource model

### Cloudbet
| Resource | Notes |
|---|---|
| `HttpTransport` | inherited; uses `PROXY_URL` env for proxy. Closed properly via `extractor.close()` in orchestrator finally. |
| API key | `CLOUDBET_API_KEY` env or `config.api_key` — sent in `X-API-Key` header on every request |
| Concurrency | NONE within `extract()` — one competition at a time. Sport-level concurrency is provided by orchestrator's `Semaphore(concurrent_sports)`. |

### Marathon
| Resource | Notes |
|---|---|
| `HttpTransport` | inherited; uses `PROXY_URL` env for proxy. Closed properly. |
| Custom UA + Accept headers | hardcoded in `_HEADERS` dict at [marathon.py:20-28](../../backend/src/providers/marathon.py#L20). Mimics Chrome 131. |
| HTML parsing | regex via `_DATA_SEL_RE`, `_PREMATCH_TIME_RE`, `_TREE_NAME_RE` ([marathon.py:64-73](../../backend/src/providers/marathon.py#L64)). No DOM library. |

### Stake (retired)
| Resource | Notes |
|---|---|
| `HttpTransport` | inherited |
| GraphQL query | hardcoded in `_FIXTURES_QUERY` ([stake.py:48-70](../../backend/src/providers/stake.py#L48)). No persisted-query support. |
| Why disabled | Cloudflare 403s from German datacenter IPs. Would need residential proxy (we have one for tipwin via `RESIDENTIAL_PROXY_URL`, but stake.com remains gated). |

## 4. Lifecycle

All three providers reuse the inherited `HttpTransport`. Sessions are closed
via `extractor.close()` in the orchestrator's `finally` block. No leaks.

The retired Stake retriever is still in [factory.py:24, 152-159](../../backend/src/factory.py#L24)
but never instantiated because no tier references it.

## 5. Smells

### Cloudbet

| # | File:line | Smell | Impact |
|---|---|---|---|
| **A** | [cloudbet.py:313](../../backend/src/providers/cloudbet.py#L313) | **Serial competition fetch.** `for comp in competitions: await transport.get(comp_url)` — soccer's ~100 competitions take ~30 s sequentially when 5-10 s with `asyncio.gather + Semaphore(20)`. **This is the dominant cost** in the 155 s avg duration. | 5-10× speedup achievable with a textbook fan-out (mirror Pinnacle's `_fetch_league` pattern). |
| B | [cloudbet.py:322](../../backend/src/providers/cloudbet.py#L322) | If a competition fetch returns `None` (transient 5xx, network blip), it silently `continue`s. No retry, no backoff, no metrics. | Quiet partial extraction during API issues. |
| C | [cloudbet.py:71-72](../../backend/src/providers/cloudbet.py#L71) | `_HANDICAP_RE = re.compile(r"handicap=(-?\d+(?:\.\d+)?)")` parses the `params` string e.g. `"handicap=-2.5"`. Brittle to API shape change. Cloudbet returns structured JSON, but we treat handicap as opaque string. | Latent: any change in params encoding silently drops spread/total markets. |
| D | [cloudbet.py:332-336](../../backend/src/providers/cloudbet.py#L332) | `if limit and len(events) >= limit: break` exits both inner and outer loops — but caller passes `limit=0` (default) so this never triggers in practice. Dead code in production. | Code clarity. |
| E | [cloudbet.py:233](../../backend/src/providers/cloudbet.py#L233) | Skips events with no parseable markets but doesn't differentiate "Cloudbet returned no markets" vs "we didn't recognize the market keys". Both look the same in metrics. | Diagnostic gap. |

### Marathon

| # | File:line | Smell | Impact |
|---|---|---|---|
| **F** | [marathon.py:227-232](../../backend/src/providers/marathon.py#L227) | **`datetime.utcnow()` is deprecated in Python 3.12+** and the year-inference heuristic `parsed.month < now.month - 1` is **broken at year boundaries**. In December (now.month=12) we'd get `parsed.month < 11`, and a January game (parsed.month=1) yields True → year+1=2027 (correct!). But in **January** (now.month=1) `now.month - 1 = 0`, and any month ≤ 0 is impossible → year inference NEVER fires → December games scraped in January get year=2026 (today) instead of 2025. Net: marathon events from late December would be misdated by a year in early January. | Real bug. Affects 2-3 weeks per year (late Dec → early Jan). Affects fuzzy matching reliability for 1H of December. |
| G | [marathon.py:174-201](../../backend/src/providers/marathon.py#L174) | **Positional decoding** of selections: `[0:N]=match-winner, [N:N+2]=total, [N+2:N+4]=spread`. Assumes Marathonbet's HTML always emits bet groups in this fixed order. Any reordering or insertion of a new market type silently shifts every subsequent market type. | Latent breakage; high blast-radius if Marathon changes their layout. |
| H | [marathon.py:99](../../backend/src/providers/marathon.py#L99) | `pattern.finditer(html)` walks the full multi-MB HTML page **twice** (once in `_split_events`, once in `_FULL_BLOCK_RE` defined at line 57 but unused). The unused `_FULL_BLOCK_RE` ([line 57-61](../../backend/src/providers/marathon.py#L57)) appears to be vestigial. | Dead code + minor performance. |
| I | [marathon.py:308](../../backend/src/providers/marathon.py#L308) | Single `transport.get(url)` — no retry on 5xx, no specific UA rotation. Marathonbet rate-limits aggressively if they detect scrapers. | Cliff failure: one 503 = zero events for the cycle. |
| J | [marathon.py:48-110](../../backend/src/providers/marathon.py#L48) | Three regexes (`_EVENT_BLOCK_RE`, `_FULL_BLOCK_RE`, `_DATA_SEL_RE`) defined at module level but `_split_events` re-compiles the same pattern inline at [line 92](../../backend/src/providers/marathon.py#L92). Pre-compiled vs inline mismatch — the module-level ones are unused. | Cleanup opportunity. |
| K | [marathon.py:269](../../backend/src/providers/marathon.py#L269) | Live events are silently skipped — but the `_split_events` regex still parses them. Wasted CPU on every multi-MB page. | Minor perf. |

### Stake (retired)

| # | File:line | Smell | Impact |
|---|---|---|---|
| L | [stake.py:201-211](../../backend/src/providers/stake.py#L201) | If GraphQL returns `{errors: [...], data: null}`, `data.get("data", {}).get("sport", {}).get("fixtures") or []` silently returns 0 events. **Errors field is never inspected.** Production GraphQL convention is to log/raise on `errors`. | Silent failure mode if Stake re-enables and starts returning auth errors. |
| M | [stake.py:97-108](../../backend/src/providers/stake.py#L97) | "first non-draw outcome = home" — assumes Stake's GraphQL always orders outcomes home-first. Coin-flip reliable; Stake's API doesn't document outcome ordering. | Latent: 50 % chance of swapped home/away if Stake reorders. |
| N | [stake.py](../../backend/src/providers/stake.py) | Provider has no path to re-enablement (residential proxy needed but no env wiring). Code rots in factory while disabled. | Maintenance: dead code accumulating drift. |

## 6. Open-source comparable

| Project | What it does differently |
|---|---|
| [`scrapy-playwright`](https://github.com/scrapy-plugins/scrapy-playwright) parsing pattern | Separates fetch from parse. We could push Marathon's HTML into `selectolax.HTMLParser` (~10× faster than re module on multi-MB pages) and replace positional decoding with structural CSS selectors on `data-sel` divs. |
| [`gql` library](https://github.com/graphql-python/gql) | Inspects `errors` field automatically on GraphQL responses, supports persisted queries, retries on transient errors. Stake should use this if/when re-enabled. |
| [Cloudbet official docs](https://sports-api.cloudbet.com/) | Documents that competitions endpoint accepts `?live=false` to filter; we currently filter eventCount>0 client-side. |
| [`tenacity` async retry](https://tenacity.readthedocs.io/) | Marathon and Stake both make a single GET/POST with no retry. Wrap with `@retry(stop_after_attempt(3), wait_exponential())`. |

## 7. Verdict

- **Cloudbet:** keep, parallelize. The serial loop is the only meaningful issue; everything else is hygiene.
- **Marathon:** keep, fix the December/January year bug. Consider migrating regex to selectolax for resilience but not urgent (7 s avg is fine).
- **Stake:** **delete** the file from `factory.py` registration. It's been disabled for >6 months. If/when Cloudflare passthrough becomes feasible, we can resurrect from git history. Keeping unused providers in the factory creates drift (e.g., `BetService` import not present, datetime.utcnow deprecation creep).

## 8. Ranked fixes

| # | Fix | Provider | File:line | Impact | Effort |
|---|---|---|---|---|---|
| 1 | **Parallelize Cloudbet competitions** — `asyncio.gather(*[fetch_comp(c) for c in competitions])` under `Semaphore(20)`, mirror Pinnacle's `_fetch_league` shape. Preserve order or sort by `eventCount` desc. | cloudbet | [cloudbet.py:313](../../backend/src/providers/cloudbet.py#L313) | 155 s → ~20-30 s avg duration. Frees the slot for cloudbet to refresh closer to its 5-min cadence | 1.5 h |
| 2 | **Fix Marathon year-boundary bug** — replace `if parsed.month < now.month - 1` with explicit cross-year logic: if the parsed date is more than 6 months in the past relative to today, assume next year. Use `datetime.now(timezone.utc)` not deprecated `datetime.utcnow()`. | marathon | [marathon.py:227-232](../../backend/src/providers/marathon.py#L227) | Correctness for ~3 weeks per year (Dec 24 → Jan 14) | 30 min |
| 3 | **Add 5xx retry to Cloudbet competitions** — wrap `transport.get(comp_url)` in tenacity-style retry (3 attempts, exponential backoff). Pinnacle does this implicitly via `HttpTransport` 429 handling, but 5xx isn't covered. | cloudbet | [cloudbet.py:321-323](../../backend/src/providers/cloudbet.py#L321) | Eliminates silent partial extraction during transient API hiccups | 30 min |
| 4 | **Delete `stake.py` registration in factory.py** — remove the import and `elif retriever_type == "stake":` block. Keep the file (or move to `_retired/`) for future resurrection. | stake | [factory.py:24, 152-159](../../backend/src/factory.py#L24) | Reduces drift; signals "this is dead" clearly | 10 min |
| 5 | **Replace Marathon positional decoding with structural** — match each bet group by name (`MATCH WINNER`, `TOTAL`, `HANDICAP`) rather than positional `[0:N]`. Use `selectolax` to walk the DOM safely. | marathon | [marathon.py:174-201](../../backend/src/providers/marathon.py#L174) | Resilience to layout change. ~10× faster parse. | 3 h |
| 6 | **Remove dead `_FULL_BLOCK_RE` regex and inline duplicate** — keep one canonical pattern at module level | marathon | [marathon.py:57-61, 92-99](../../backend/src/providers/marathon.py#L57) | Cleanup | 15 min |
| 7 | **Skip live event regex** — split HTML before regex if possible, or filter `data-live="true"` matches before parsing | marathon | [marathon.py:266-274](../../backend/src/providers/marathon.py#L266) | Minor perf. Not urgent at 7 s. | 30 min |
| 8 | **If Stake re-enabled: switch to `gql` client + inspect errors field** | stake | [stake.py:201-211](../../backend/src/providers/stake.py#L201) | Future correctness | (deferred) |

**Minimum-viable bundle for active providers (1 + 2 + 3):** ~2.5 h.
**With cleanup (1 + 2 + 3 + 4):** ~2.7 h.
**Recommended (1-6):** ~6 h.

## 9. Re-introduction notes

**Deployed 2026-04-26 12:41 UTC** in commit `8cd07e9a` to `feat/slip-odds-architecture` on the Hetzner server:
- Fix #1: Cloudbet competitions fetched in parallel under `Semaphore(COMPETITION_CONCURRENCY=20)` (mirrors Pinnacle's `_fetch_league` pattern). Soccer's ~100 competitions × ~300 ms RTT was 30s sequential — should drop to 5-10s.
- Fix #2: Marathon year-boundary heuristic replaced with `(now - parsed) > 30 days` rollover check. Old `if parsed.month < now.month - 1` was broken in January (threshold = 0, never fires). New check verified for Dec→Jan and Jan→Dec scrapes.
- Fix #4: Stake retired from factory + YAML provider config + `signal_api` pool group. Source file kept for future resurrection.
- Fix #3 ("add 5xx retry to Cloudbet") deferred — `HttpTransport.get` already retries 429; adding 5xx is a follow-up.

Pre-deploy verification: ruff clean · py_compile clean · math sanity-checked for year-rollover edge cases.

**Post-deploy observations (cycle 1, 12:41–12:48 UTC):**
- ✅ **Cloudbet 6.2× faster**: 155s → 25s avg (target was 20-30s — hit). Parallel competition fan-out via `Semaphore(20)` confirmed working.
- ✅ Marathon: 7s → 2s (small sample but consistent with no regression).
- ✅ No `Unknown retriever type 'stake'` errors after stake retirement.

Post-deploy checks remaining:
- [ ] Marathon date-mismatch incidents during late-Dec / early-Jan window (won't verify until next year boundary; Smell F)
