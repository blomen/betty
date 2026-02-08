# Provider Performance Report

> Last updated: 2026-02-08

## Summary

| Metric | Value |
|--------|-------|
| Active providers | 26 (2 sharp + 24 soft) |
| Disabled providers | 5 (4 Gecko V2 + 10bet) |
| Total soft book odds (last run) | 28,208 |
| Total soft book events (last run) | 10,604 |
| Pinnacle baseline | 1,456 events / 8,782 odds |

---

## Provider Performance Table

### API-Based Providers (always available)

| Provider | Platform | Odds | Events | Ratio | Norm% | Pin Match | Pin% | Markets |
|----------|----------|------|--------|-------|-------|-----------|------|---------|
| pinnacle | Sharp | 8,782 | 1,456 | 6.03 | 100% | - | - | 1x2/ml/spread/total |
| polymarket | Sharp | 396 | 162 | 2.44 | 100% | - | - | 1x2/ml (some spread/total) |
| betinia | Altenar | 5,449 | 2,036 | 2.68 | 100% | 598 | 29.4% | 1x2/ml/spread/total |
| dbet | Altenar | 5,891 | 1,996 | 2.95 | 100% | 593 | 29.7% | 1x2/ml/spread/total |
| lodur | Altenar | 5,492 | 2,047 | 2.68 | 100% | 578 | 28.2% | 1x2/ml/spread/total |
| quickcasino | Altenar | 5,460 | 2,042 | 2.67 | 100% | 598 | 29.3% | 1x2/ml/spread/total |
| campobet | Altenar | - | - | - | - | - | - | 1x2/ml/spread/total |
| swiper | Altenar | - | - | - | - | - | - | 1x2/ml/spread/total |
| unibet | Kambi | 2,758 | 827 | 3.33 | 100% | 355 | 42.9% | 1x2/ml/spread/total |
| leovegas | Kambi | 2,762 | 828 | 3.34 | 100% | 355 | 42.9% | 1x2/ml/spread/total |
| expekt | Kambi | - | - | - | - | - | - | 1x2/ml/spread/total |
| betmgm | Kambi | - | - | - | - | - | - | 1x2/ml/spread/total |
| speedybet | Kambi | - | - | - | - | - | - | 1x2/ml/spread/total |
| x3000 | Kambi | - | - | - | - | - | - | 1x2/ml/spread/total |
| goldenbull | Kambi | - | - | - | - | - | - | 1x2/ml/spread/total |
| 1x2 | Kambi | - | - | - | - | - | - | 1x2/ml/spread/total |

> `-` = not extracted in this run (rate-limited or browser-based)

### Browser-Based Providers (require Playwright)

| Provider | Platform | Odds | Events | Ratio | Norm% | Pin Match | Markets | Notes |
|----------|----------|------|--------|-------|-------|-----------|---------|-------|
| mrgreen | Spectate | ~2,182 | ~789 | 2.77 | 100% | 367 | 1x2/ml/spread/total | Browser session init |
| 888sport | Spectate | ~2,186 | ~789 | 2.77 | 100% | 369 | 1x2/ml/spread/total | Browser session init |
| bethard | SBTech | ~570 | ~259 | 2.20 | 100% | 19 | 1x2/ml only | Listing page only |
| snabbare | Sportradar MTS | ~1,440 | ~583 | 2.47 | 100% | 172 | 1x2/ml only | DOM scraping |
| comeon | ComeOn WS | ~400 | ~174 | 2.30 | 100% | 14 | 1x2 only | 8% event coverage |
| hajper | ComeOn WS | ~924 | ~309 | 2.99 | 100% | 135 | 1x2 only | Better than comeon |
| vbet | BetConstruct | - | - | - | - | - | 1x2/ml/spread/total | WebSocket |
| interwetten | Proprietary | - | - | - | - | - | 1x2/ml | SSR DOM scraping |
| coolbet | GAN/Coolbet | ~187 | ~81 | 2.31 | 100% | - | 1x2/ml/spread/total | CDP required |
| tipwin | Tipwin SPA | ~45 | ~15 | 3.00 | 100% | - | 1x2/ml/spread/total | API interception |

### Disabled Providers

| Provider | Platform | Reason | Fix Needed |
|----------|----------|--------|------------|
| betsson | Gecko V2 | event-market widget returns props only | Rework to EventsTable widget |
| betsafe | Gecko V2 | Same as betsson | Same |
| nordicbet | Gecko V2 | Same as betsson | Same |
| spelklubben | Gecko V2 | Same as betsson | Same |
| 10bet | SBTech WS | Uses WebSocket (framegas3.com), not REST | Custom WS extractor |

---

## Per-Provider Analysis

### Kambi Providers (8)

**Shared characteristics:**
- REST API, no browser needed
- All share same Kambi offering API (`eu1.offering-api.kambicdn.com`)
- Rate limit is aggressive (429 after few requests, 60-120s cooldown)
- `post_extraction_delay_ms: 15000` between providers to avoid bans
- Spread/total fully supported via `betOfferType` 6 (spread) and 7 (total)
- Ice hockey dedup: prefers 1x2 over moneyline when both exist

**unibet / leovegas** (validated)
- Ratio: 3.33-3.34 (good — includes spread/total)
- 828 events, 355 Pinnacle matches (42.9%)
- Higher Pin% than Altenar because Kambi has fewer niche leagues

**expekt / betmgm / speedybet / x3000 / goldenbull / 1x2** (not in last run)
- Expected: Same stats as unibet/leovegas (identical API, different slugs)
- All use same `offering/v2018/{slug}` endpoint

**Optimization opportunities:**
- [ ] Run all 8 Kambi providers in a single extraction (currently rate-limited)
- [ ] Cache Kambi group data across providers (same events, different odds unlikely)
- [ ] Reduce `post_extraction_delay_ms` if rate limits allow

---

### Altenar Providers (6)

**Shared characteristics:**
- REST API, no browser needed, no rate limit issues
- Uses `GetUpcoming` endpoint with `sportId` filter
- Market types via `typeId` (see MEMORY.md for full map)
- Point values in market's `sv` field

**betinia / lodur / quickcasino / dbet** (validated)
- Ratio: 2.67-2.95 (good)
- ~2,000 events each, ~580-598 Pinnacle matches (~29%)
- Lower Pin% than Kambi because Altenar covers more niche sports (table tennis, volleyball)
- dbet has slightly higher ratio (2.95) — more spread/total markets

**campobet / swiper** (active but not in last run)
- Expected: Similar to betinia (same platform, different integration ID)

**Optimization opportunities:**
- [ ] Altenar supports pagination — verify we're getting ALL events (not capped)
- [ ] Add handball category ID if missing from sport mapping
- [ ] Test concurrent extraction of all 6 Altenar providers (no rate limit observed)

---

### Spectate Providers (2): mrgreen, 888sport

**Performance:**
- ~789 events, ratio 2.77, 100% normalization
- 367-369 Pinnacle matches (46.5%)
- Football dominates (498 events)
- Spread/total working after Swedish market name mapping fix

**Optimization opportunities:**
- [ ] Some date buckets return 400 (normal, but investigate if events are missed)
- [ ] Spread outcome team name matching could fail for abbreviated names
- [ ] Add more Swedish market name variants if new ones appear
- [ ] Parallel bucket fetching already implemented

---

### SBTech Provider (1): bethard

**Performance:**
- ~259 events, ratio 2.20, 100% normalization
- Only 19 Pinnacle matches (7.3% — very low)
- Listing pages only return 1x2/moneyline (no spread/total without event detail nav)
- 53.7% of events have market selections

**Optimization opportunities:**
- [ ] **HIGH PRIORITY**: Navigate to event detail pages for spread/total markets
- [ ] Improve team name matching (Swedish names → canonical)
- [ ] Add `?tab=upcoming` to all sport URLs (3x more events)
- [ ] Scroll loop covers infinite-scroll, but may miss some events
- [ ] Name reversal for individual sports already implemented

---

### Snabbare (1)

**Performance:**
- ~583 events, ratio 2.47, 100% normalization
- 172 Pinnacle matches (29.5%)
- DOM scraping — only captures 1x2/moneyline from listing page

**Optimization opportunities:**
- [ ] **MEDIUM**: Event detail pages may have spread/total
- [ ] Concurrent league processing (sem=10) already optimized
- [ ] Some leagues may not load due to empty state detection
- [ ] Time parsing could be more robust for edge cases

---

### ComeOn Group (2): comeon, hajper

**Performance:**
- ComeOn: 174 events with odds, 14 Pinnacle matches (8% event coverage is terrible)
- Hajper: 309 events, ratio 2.99, 135 Pinnacle matches (much better)
- WebSocket/RSocket — only captures markets for events rendered on page
- 1x2 only from listing page

**Optimization opportunities:**
- [ ] **HIGH PRIORITY**: Navigate to event detail pages for more market data
- [ ] ComeOn has 2,238 events but only 174 get odds — need to scroll/paginate
- [ ] Investigate why Hajper captures 2x more events than ComeOn
- [ ] RSocket market subscriptions could capture spread/total

---

### Vbet (BetConstruct WebSocket)

**Performance:**
- Not in last extraction run
- WebSocket Swarm API — should support 1x2/ml/spread/total

**Optimization opportunities:**
- [ ] Validate extraction works and check event counts
- [ ] WebSocket reconnection handling
- [ ] Market type mapping completeness

---

### Interwetten (Proprietary SSR)

**Performance:**
- Not in last extraction run
- Browser-based, headed mode required
- SSR DOM scraping — limited to 1x2/ml

**Optimization opportunities:**
- [ ] Add spread/total extraction (may need event detail pages)
- [ ] Headed mode is resource-heavy — investigate headless workaround
- [ ] Validate extraction works in current pipeline

---

### Coolbet (GAN Platform)

**Performance:**
- ~81 events, ratio 2.31, 100% normalization
- CDP connection required (Imperva blocks Playwright)
- Supports 1x2/ml/spread/total via two-step API

**Optimization opportunities:**
- [ ] **MEDIUM**: Only fetching a subset of categories — add more sport category IDs
- [ ] Milliodds conversion (>100 = divide by 1000) already implemented
- [ ] Could increase event count by adding more league categories
- [ ] CDP dependency makes automated extraction fragile

---

### Tipwin (Proprietary SPA)

**Performance:**
- ~15 events, ratio 3.0, 100% normalization
- Browser-based API interception
- Supports 1x2/spread/total via `bettingTypes` lookup

**Optimization opportunities:**
- [ ] **HIGH PRIORITY**: Very low event count — investigate missing sports/leagues
- [ ] Cookie consent click required on first load
- [ ] API response has more events than we're capturing — check filter params
- [ ] `agencyId` 100683 is SE-specific — verify it's correct

---

### Gecko V2 (Betsson Group — 4 providers, DISABLED)

**Status:** BROKEN — event-market widget (`/api/sb/v1/widgets/event-market/v1`) returns ONLY prop/exotic markets

**Fix needed:**
- [ ] Rework to intercept EventsTable widget or use direct API
- [ ] EventsTable uses lazy loading with `widgetRequest` objects
- [ ] Alternative: Intercept initial page render SSR data
- [ ] This would unlock 4 providers: betsson, betsafe, nordicbet, spelklubben

**Impact if fixed:** Betsson Group has significant market share in Sweden. All 4 providers have freebet/depositbonus bonuses.

---

### 10Bet (SBTech WebSocket — DISABLED)

**Status:** Uses WebSocket (`wss://openapi.framegas3.com`), not REST

**Fix needed:**
- [ ] Custom WebSocket extractor (different from SBTech REST)
- [ ] Analyze WS message format
- [ ] Map to StandardEvent

---

## Sport Coverage Matrix

| Sport | Pinnacle | Kambi | Altenar | Spectate | Snabbare | ComeOn |
|-------|----------|-------|---------|----------|----------|--------|
| Football | 778 | 500 | 1,137 | ~498 | ~300 | ~100 |
| Basketball | 320 | 103 | 317 | ~80 | ~50 | ~30 |
| Ice Hockey | 121 | 13 | 122 | ~40 | ~30 | ~10 |
| Tennis | 68 | 52 | 173 | ~30 | ~50 | ~10 |
| Handball | 42 | 38 | 74* | - | - | - |
| Volleyball | 51 | 1 | 85 | - | - | - |
| Esports | 25 | 29 | 84 | ~20 | ~20 | ~5 |
| MMA | 7 | 18 | 15 | ~5 | ~5 | - |
| Rugby | - | 29 | 6 | - | - | - |
| Table Tennis | - | 16 | 95 | - | - | - |
| Cricket | 5 | 6 | - | - | - | - |
| American Football | 1 | 1 | 1 | - | - | - |

> `*` = dbet only (other Altenar providers don't have handball mapped)
> `~` = approximate from previous runs

---

## Bonus & Oddsboost Status

### Bonus Summary

| Provider | Type | Amount (SEK) | Wagering | Min Odds |
|----------|------|-------------|----------|----------|
| unibet | Freebet | 1,000 | 1x | 1.80 |
| leovegas | Bonus Deposit | 600 | 6x | 1.80 |
| expekt | Bonus Deposit | 1,000 | 20x | 1.80 |
| betmgm | Freebet | 500 | 1x | 1.80 |
| speedybet | Bonus Deposit | 500 | 12x | 1.80 |
| x3000 | Bonus Deposit | 500 | 12x | 1.80 |
| goldenbull | Bonus Deposit | 500 | 12x | 1.80 |
| 1x2 | Bonus Deposit | 500 | 12x | 1.80 |
| betinia | Bonus Deposit | 1,000 | 6x | 1.80 |
| campobet | Bonus Deposit | 500 | 6x | 1.80 |
| swiper | Bonus Deposit | 1,000 | 6x | 1.50 |
| lodur | Bonus Deposit | 1,000 | 6x | 1.80 |
| dbet | Freebet | 500 | 1x | 1.80 |
| quickcasino | Bonus Deposit | 500 | 6x | 1.80 |
| mrgreen | Freebet | 500 | 1x | 1.80 |
| 888sport | Bonus Deposit | 500 | 1x | 1.80 |
| bethard | Bonus Deposit | 500 | 15x | 1.90 |
| snabbare | Bonus Deposit | 600 | 8x | 1.80 |
| comeon | Bonus Deposit | 500 | 6x | 1.80 |
| hajper | Freebet | 500 | 1x | 1.80 |
| vbet | Freebet | 800 | 10x | 1.80 |
| interwetten | Bonus Deposit | 1,000 | 5x | 1.70 |
| coolbet | Deposit Bonus | 1,000 | 6x | 1.50 |
| tipwin | Bonus Deposit | 1,000 | 7x | 1.80 |
| betsson | Freebet | 250 | 1x | 1.80 |
| betsafe | Freebet | 100 | 1x | 1.80 |
| nordicbet | Freebet | 100 | 1x | 1.80 |
| spelklubben | Bonus Deposit | 500 | 15x | 1.90 |

### Best Value Bonuses (sorted by effective value)

**Freebets (best — 1x wagering):**
1. Unibet: 1,000 SEK freebet (1x @ 1.80)
2. Vbet: 800 SEK freebet (10x @ 1.80) — high wagering for freebet
3. BetMGM: 500 SEK freebet (1x @ 1.80)
4. Dbet: 500 SEK freebet (1x @ 1.80)
5. MrGreen: 500 SEK freebet (1x @ 1.80)
6. Hajper: 500 SEK freebet (1x @ 1.80)

**Low-wagering deposit bonuses:**
1. Interwetten: 1,000 SEK (5x @ 1.70)
2. Betinia: 1,000 SEK (6x @ 1.80)
3. Lodur: 1,000 SEK (6x @ 1.80)
4. Swiper: 1,000 SEK (6x @ 1.50 — lowest min odds!)
5. Coolbet: 1,000 SEK (6x @ 1.50)
6. ComeOn: 500 SEK (6x @ 1.80)

---

### Oddsboost Per Provider

#### Kambi Group (unibet, leovegas, expekt, betmgm, speedybet, x3000, goldenbull, 1x2)
- **Status:** NOT EXTRACTABLE
- **Reason:** Kambi SPECIAL_BETS only exposes boosted price, NOT original odds. Without original odds, we can't calculate true edge on boosts.
- **Workaround:** Could cross-reference boosted events against Pinnacle fair odds, but boost identification requires knowing which odds are boosted vs normal.
- **Priority:** LOW — fundamental API limitation

#### Altenar Group (betinia, campobet, swiper, lodur, dbet, quickcasino)
- **Status:** NOT IMPLEMENTED
- **Boost availability:** Listed on aggregator sites (4-5/5 rating)
- **Investigation needed:** Reverse-engineer Altenar API for boost endpoints
- **Likely shared:** All Altenar brands probably share same boost mechanism
- **Priority:** MEDIUM — 6 providers would benefit

#### Spectate Group (mrgreen, 888sport)
- **Status:** NOT IMPLEMENTED
- **Boost availability:** Both sites have boost sections
- **Investigation needed:** Browser-based extraction of boost page, need to find boosted events and original odds
- **Priority:** MEDIUM

#### Betsson Group (betsson, betsafe, nordicbet, spelklubben)
- **Status:** IMPLEMENTED but DISABLED
- **Reason:** Gecko V2 extractor is broken (props only). Boost extraction code exists but can't run.
- **Boost URL:** `https://www.betsson.com/sv/odds/odds-boost`
- **Shared across:** All 4 Betsson brands
- **Priority:** HIGH — unlocking Gecko V2 enables boost extraction too

#### Bethard (SBTech)
- **Status:** NOT IMPLEMENTED
- **Boost type:** "Combo Booster" (7-30% extra on parlays)
- **Single-bet boosts:** Unknown — needs investigation
- **Priority:** LOW — combo boosts less useful for value detection

#### Snabbare
- **Status:** NOT IMPLEMENTED
- **Boost availability:** Listed (4/5 on aggregators)
- **Investigation needed:** Check if boost section exists on site
- **Priority:** LOW

#### ComeOn Group (comeon, hajper)
- **Status:** NOT IMPLEMENTED
- **Boost availability:** Listed (5/5 on aggregators for comeon)
- **Investigation needed:** Browser-based boost page scraping
- **Priority:** LOW — event coverage too low to be useful for boosts

#### Vbet / Interwetten / Coolbet / Tipwin
- **Status:** NOT IMPLEMENTED
- **Boost availability:** Unknown
- **Priority:** LOW — investigate boost pages first

---

## Priority Optimization Roadmap

### High Priority
1. **Fix Gecko V2** — Unlocks 4 Betsson Group providers + boost extraction
2. **Bethard event detail navigation** — Add spread/total markets (currently 1x2/ml only)
3. **Tipwin event coverage** — Only 15 events, should be 100+
4. **ComeOn event coverage** — Only 8% of events get market data

### Medium Priority
5. **Altenar pagination audit** — Verify we're not missing events
6. **Coolbet category expansion** — Add more sport category IDs
7. **Altenar boost investigation** — Reverse-engineer boost API for 6 providers
8. **Spectate boost investigation** — Browser-based boost page for 2 providers

### Low Priority
9. **Snabbare spread/total** — Event detail pages may have more markets
10. **10Bet WebSocket extractor** — New platform, significant effort
11. **Kambi provider caching** — Share group data across 8 providers
12. **Interwetten spread/total** — Need event detail navigation
