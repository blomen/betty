# International Signal Providers — Design Spec

**Date:** 2026-04-09
**Status:** Draft
**Scope:** Add 6 international signal-only providers to strengthen consensus model for Pinnacle execution

## Context

Firev currently extracts odds from ~40 Swedish-licensed sportsbooks, but most are white-labels sharing the same odds feed (Kambi: 10+ skins, Altenar: 7 skins). The actual number of **independent price signals** is low (~3-4: Pinnacle, Kambi, Altenar, plus a few browser providers).

Adding international books with **independent odds compilation** or **market-driven pricing** significantly improves consensus quality — more independent price points = higher confidence when identifying mispricings to bet on Pinnacle.

## Execution model

- **Pinnacle** — primary execution venue (unlimited, Swedish-licensed, tax-free)
- **Polymarket** — secondary execution (crypto, prediction markets)
- **All new providers** — signal-only (read odds, no bet placement)

## Providers to add

### Tier 1 — API-based (low difficulty, high value)

#### 1. Stake.com
- **Type:** Crypto sportsbook (Curaçao licensed, Sweden prohibited for play)
- **API:** Public GraphQL at `POST https://stake.com/_api/graphql`
- **Auth:** None for odds data
- **Rate limit:** ~1 req/s, Cloudflare active — needs proper headers (`x-language: en`, User-Agent)
- **Data format:** Decimal odds, fixture-based, standard markets
- **Coverage:** 50+ sports, massive global coverage including niche leagues and esports
- **Value:** Independent odds compilation, crypto book customer base prices differently than EU soft books
- **Sport mapping:** Uses slugs (`football`, `basketball`, `tennis`, etc.)

**GraphQL query structure:**
```graphql
query SportFixtures($sport: String!, $limit: Int) {
  sportFixtures(sportSlug: $sport, limit: $limit) {
    id
    name
    status
    tournament { name category { sport { name slug } } }
    startTime
    groups(groups: ["winner", "handicap", "totals"]) {
      templates(limit: 1) {
        markets(limit: 1) {
          outcomes { id name odds active }
        }
      }
    }
  }
}
```

**Implementation:** New `StakeRetriever` extending `Retriever` with `HttpTransport`. POST requests with GraphQL queries. Map outcomes to `StandardEvent` with 1x2/moneyline/spread/total markets.

#### 2. Cloudbet
- **Type:** Crypto sportsbook (Curaçao licensed)
- **API:** REST feed API with Swagger/OpenAPI docs
- **Auth:** None for feed API (read odds). API key for trading (not needed).
- **Rate limit:** TBD — documented in Swagger spec
- **Data format:** Decimal odds, event/market structure
- **Coverage:** 30+ sports, major global leagues
- **Value:** Independent crypto book, well-documented API, different customer base

**Implementation:** New `CloudbetRetriever` extending `Retriever` with `HttpTransport`. Standard REST GET requests. Map response to `StandardEvent`.

#### 3. Fairlay
- **Type:** P2P betting exchange (crypto, Bitcoin-focused)
- **API:** REST API, open-source client on GitHub (`Fairlay/Bitcoin-Betting`)
- **Auth:** None for market data (read odds)
- **Rate limit:** TBD
- **Data format:** Exchange back/lay prices — store best back price as provider odds
- **Coverage:** Sports + politics + crypto events
- **Value:** Market-driven prices from real traders — sharpest signal source after Pinnacle. P2P model means prices reflect actual supply/demand.

**Implementation:** New `FairlayRetriever` extending `Retriever` with `HttpTransport`. REST GET for market listings. Convert exchange prices to decimal odds (best back).

### Tier 2 — Browser/scrape-based (medium difficulty, high value)

#### 4. Marathonbet
- **Type:** EU sportsbook (independent odds compilation)
- **API:** No public API. Odds rendered in DOM with stable HTML structure.
- **Approach:** Playwright headless, DOM parsing. Odds visible in HTML tables without JS interception.
- **Coverage:** Football-heavy, strong EU coverage, independent odds team
- **Value:** Long-established independent compiler, not a white-label

**Implementation:** New `MarathonRetriever` extending `Retriever` with `BrowserTransport`. Navigate sport pages, parse odds from DOM selectors. Similar pattern to existing Spectate/Interwetten extractors.

#### 5. 1xBet
- **Type:** Large international sportsbook (Curaçao licensed)
- **API:** Internal JSON API endpoints accessible without auth. Known endpoint patterns from public analysis.
- **Approach:** REST API calls to internal endpoints — no browser needed if endpoints are stable.
- **Coverage:** 50+ sports, widest coverage of any single book. Niche leagues, esports, virtual sports.
- **Value:** Massive event coverage — catches events other books don't list. Independent odds.

**Implementation:** New `OnexbetRetriever` extending `Retriever` with `HttpTransport`. Direct API calls to internal endpoints. If endpoints are unstable, fall back to `BrowserTransport` with API interception (Gecko V2 pattern).

#### 6. Betway
- **Type:** UK/international sportsbook (independent odds compilation)
- **API:** Internal API endpoints may be accessible. Otherwise DOM scraping.
- **Approach:** Investigate internal API first. Fall back to Playwright DOM scraping.
- **Coverage:** Strong on football, basketball, esports. UK and EU markets.
- **Value:** Major independent compiler with UK customer base — different pricing than Nordic/EU books.

**Implementation:** New `BetwayRetriever` extending `Retriever`. Transport depends on investigation — `HttpTransport` if internal API works, `BrowserTransport` if DOM scraping needed.

## Architecture

### Provider registration

Each provider follows the existing pattern:

1. **Retriever class** in `backend/src/providers/{name}.py`
   - Extends `Retriever` (API) or uses `BrowserTransport` (DOM)
   - Implements `extract(sport) → List[StandardEvent]`
   - Normalizes team names via `normalize_team_name()`
   - Only extracts 1x2/moneyline, spread, total markets
   - Skips live events

2. **Factory registration** in `backend/src/factory.py`
   - Import retriever class
   - Add `elif retriever_type == "xxx":` block with transport setup

3. **Provider config** in `backend/src/config/providers.yaml`
   ```yaml
   stake:
     id: stake
     enabled: true
     retriever_type: stake
     api_base: https://stake.com/_api/graphql
   ```

4. **Sport mapping** in `backend/src/config/sports.yaml`
   - Add provider-specific sport IDs/slugs for each sport entry
   ```yaml
   football:
     stake_slug: football
     cloudbet_key: soccer
     onexbet_id: 1
   ```

### Extraction scheduling

New extraction tier in `providers.yaml`:

```yaml
signal_international:
  interval: 300  # 5 minutes
  providers:
    - stake
    - cloudbet
    - fairlay
    - marathon
    - onexbet
    - betway
```

These run independently from existing tiers. They don't share browser pools or rate limits with Swedish providers.

### Provider groups (orchestrator config)

```yaml
provider_groups:
  - name: signal_api
    retriever_types: [stake, cloudbet, fairlay]
    max_concurrent: 3
    shared_resource: none  # All independent APIs

  - name: signal_browser
    retriever_types: [marathon, betway]
    max_concurrent: 2
    shared_resource: browser

  - name: onexbet_api
    retriever_types: [onexbet]
    max_concurrent: 1  # Conservative — unknown rate limits
    shared_resource: api
```

### Data flow

```
International provider API/DOM → parse → List[StandardEvent]
    ↓
normalize_team_name() + normalize_market()
    ↓
_resolve_event_id() → match against existing Pinnacle events
    ↓
store_provider_event() → Event + Odds rows in PostgreSQL
    ↓
OpportunityScanner.scan_value() → uses all provider odds for consensus
```

No changes needed to the storage layer, matching layer, or scanner. These providers produce `StandardEvent` like all others — the pipeline handles them identically.

### Consensus model enhancement

Current scanner uses Pinnacle as the sole sharp source for fair odds. With international signals, future enhancement could:
- **Weighted consensus**: Blend Pinnacle + Fairlay (exchange) + Stake odds for better fair odds estimate
- **Confidence scoring**: More independent sources agreeing = higher confidence in the edge
- **Coverage gaps**: 1xBet and Stake cover events Pinnacle doesn't list — potential for cross-venue opportunities

These enhancements are out of scope for this spec — the immediate goal is getting the data flowing.

## Build order

| Phase | Provider | Effort | Dependencies |
|-------|----------|--------|-------------|
| 1a | Stake.com | ~2h | None — public GraphQL, straightforward |
| 1b | Cloudbet | ~2h | None — documented REST API |
| 1c | Fairlay | ~3h | None — REST API, exchange price conversion |
| 2a | 1xBet | ~4h | Investigate internal API endpoints first |
| 2b | Marathonbet | ~4h | Browser transport, DOM selector mapping |
| 2c | Betway | ~4h | Investigate API vs DOM approach first |

Phase 1 (API providers) can be built and deployed independently. Phase 2 (browser/investigation) follows.

## Success criteria

- All 6 providers extract pre-match 1x2/moneyline odds for football at minimum
- Events match against existing Pinnacle events (>50% match rate)
- Extraction completes within 5 minutes per provider
- No impact on existing extraction performance (separate tier/schedule)
- Provider odds visible in the frontend opportunity scanner

## Risks

- **Stake.com Cloudflare**: May block server IP. Mitigation: use proxy, proper headers, session reuse.
- **1xBet internal API instability**: Endpoints may change without notice. Mitigation: fall back to browser interception.
- **Marathonbet geo-blocking**: May restrict from German datacenter. Mitigation: Swedish proxy.
- **Rate limiting on all providers**: Conservative request rates, circuit breaker integration.
