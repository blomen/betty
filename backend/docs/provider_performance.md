# Provider Performance Report

> Last updated: 2026-02-13 (ComeOn date scroll, Altenar boxing/cricket, full pipeline 1,931 events)

## Overview

| Metric | Value |
|--------|-------|
| Active providers | 31 (2 sharp + 29 soft) |
| Disabled providers | 1 (betsafe — Swedish site not on OBG platform) |
| Pinnacle baseline | ~1,931 events / ~11,534 odds |
| Total odds | **~63,481** (all 30 providers) |
| Cross-provider matching | **69.6%** (1,344/1,931 events) |
| Value bets (≥5% edge) | **~393** |

### Pinnacle Sport Baseline

| Sport | Events | Odds |
|-------|-------:|-----:|
| Football | 873 | 6,086 |
| Basketball | 365 | 2,152 |
| Ice Hockey | 155 | 466 |
| Handball | 42 | 242 |
| Cycling | 41 | 82 |
| Esports | 27 | 158 |
| Boxing | 22 | 48 |
| MMA | 13 | 26 |
| Golf | 12 | 24 |
| Rugby | 10 | 52 |
| Curling | 7 | 44 |
| Cricket | 6 | 12 |
| **TOTAL** | **1,573** | **9,392** |

---

## Sharp Sources

### Pinnacle

| Metric | Value |
|--------|-------|
| Platform | Sharp REST API |
| Retriever | `pinnacle` |
| API | `guest.api.arcadia.pinnacle.com/0.1` |
| Extraction time | ~8s |
| Events | ~1,573 |
| Odds | ~9,392 |
| Ratio | 5.97 |
| Markets | 1x2, moneyline, spread, total |
| Normalization | 100% |

**Role:** Fair-odds baseline. All value calculations derive from Pinnacle devigged probabilities.

### Polymarket

| Metric | Value |
|--------|-------|
| Platform | Prediction market API |
| Retriever | `polymarket` |
| API | `gamma-api.polymarket.com` |
| Extraction time | <1s |
| Events | ~185 |
| Odds | ~473 |
| Ratio | 2.56 |
| Markets | 1x2, moneyline |

**Role:** Event matching only. NOT used as sharp source.

---

## Kambi Providers (8)

> Shared platform: REST API `eu1.offering-api.kambicdn.com/offering/v2018/{slug}`
> All 8 return identical data (different brand slugs, same odds engine).
> Rate limit: aggressive 429 (60-120s cooldown). `post_extraction_delay_ms: 15000`.
> Markets: 1x2 (betOfferType 2), spread (betOfferType 6), total (betOfferType 7).
> Ice hockey dedup: prefers 1x2 over moneyline when both exist.

### Summary Table

| Brand | Slug | Events | Odds | Bonus | Min Odds |
|-------|------|-------:|-----:|-------|----------|
| **Unibet** | `ubse` | 644 | 3,145 | Freebet 1,000 kr / 1x | 1.80 |
| **LeoVegas** | `leose` | 644 | 3,145 | BonusDep 600 kr / 6x | 1.80 |
| **Expekt** | `expektse` | 644 | 3,145 | BonusDep 1,000 kr / 20x | 1.80 |
| **BetMGM** | `betmgmse` | 608 | 2,966 | Freebet 500 kr / 1x | 1.80 |
| **SpeedyBet** | `speedybetse` | 608 | 2,966 | BonusDep 500 kr / 12x | 1.80 |
| **X3000** | `speedyspelse` | 608 | 2,966 | BonusDep 500 kr / 12x | 1.80 |
| **Golden Bull** | `pafgoldense` | 608 | 2,966 | BonusDep 500 kr / 12x | 1.80 |
| **1X2** | `pafpre1x2se` | 608 | 2,966 | BonusDep 500 kr / 12x | 1.80 |

**Oddsboost:** Not extractable (Kambi shows only boosted price, no original odds)

#### Unibet Sport Breakdown (representative of all 8)

| Sport | Events | Pin Match | Gap |
|-------|-------:|----------:|----:|
| Football | 532 | 350 | -338 |
| Tennis | 104 | 62 | -5 |
| Basketball | 59 | 30 | -48 |
| Esports | 56 | 30 | -6 |
| Table Tennis | 43 | 0 | — |
| Rugby | 24 | 0 | — |
| Handball | 20 | 13 | 0 |
| Darts | 17 | 8 | — |
| Ice Hockey | 15 | 9 | -96 |
| Boxing | 10 | 0 | — |
| Volleyball | 6 | 5 | -2 |
| Cricket | 3 | 3 | -10 |
| Curling | 2 | 0 | -4 |
| MMA | 2 | 0 | — |
| Golf | 1 | 1 | 0 |
| **TOTAL** | **605** | **511** | **-510** |

> Ice hockey low count is seasonal — NHL paused for Winter Olympics 2026.

#### Log
- **2026-02-09**: Country name aliases → 367→511 pin matches (+39%)
- **2026-02-08**: Initial validation: 2,184 odds / 524 events / 450 pin
- **2026-02-04**: PRODUCTION_READY

#### TODO
- [ ] Ice hockey coverage seasonal — will improve when NHL resumes
- [x] ~~Cache event data across 8 providers~~ — Implemented shared event cache (5min TTL), saves ~350 HTTP requests
- [ ] Reduce `post_extraction_delay_ms` if rate limits allow

---

## Altenar Providers (6)

> Shared platform: REST API `sb2frontend-altenar2.biahosted.com/api`
> No rate limits. `GetUpcoming` + `sportId`.
> Football has NO spread (platform limitation — typeId 16 not returned).
> Market TypeIds: 1x2=1 | ML=186,219,251,406,30001 | Total=18,189,225,238,258,412 | Spread=16,187,223,237,256,410

### Summary Table

| Brand | Integration | Events | Odds | Bonus | Min Odds |
|-------|-------------|-------:|-----:|-------|----------|
| **Betinia** | `betiniase2` | 953 | 3,400 | BonusDep 1,000 kr / 6x | 1.80 |
| **Lodur** | `lodurse` | 986 | 3,361 | BonusDep 1,000 kr / 6x | 1.80 |
| **CampoBet** | `campose` | 953 | 3,284 | BonusDep 500 kr / 6x | 1.80 |
| **Swiper** | `swiperse` | 953 | 3,284 | BonusDep 1,000 kr / 6x | **1.50** |
| **Dbet** | `dbet` | 938 | 3,536 | Freebet 500 kr / 1x | 1.80 |
| **QuickCasino** | `quickcasinose` | 950 | 3,256 | BonusDep 500 kr / 6x | 1.80 |

**Oddsboost:** Not implemented (listed 4/5 on aggregators, API investigation needed)

#### Betinia Sport Breakdown (representative)

| Sport | Events | Pin Match | Gap |
|-------|-------:|----------:|----:|
| Football | ~900 | 317 | -371 |
| Tennis | ~135 | 61 | -6 |
| Table Tennis | ~147 | 0 | — |
| Basketball | ~163 | 28 | -50 |
| Ice Hockey | ~146 | 28 | -77 |
| Esports | ~82 | 2 | -34 |
| Volleyball | ~66 | 2 | -5 |
| Handball | ~61 | 5 | -8 |
| MMA | ~8 | 0 | — |
| Rugby | ~8 | 0 | — |
| **TOTAL** | **1,738** | **443** | **-578** |

#### Log
- **2026-02-13**: Added boxing (sportId=71) and cricket (sportId=74). API has 2 boxing + 13 cricket events. +1 boxing matched Pinnacle (limited overlap).
- **2026-02-09**: Country name aliases → 433→443 pin matches (+10)
- **2026-02-08**: Initial validation: 1,547 odds / 571 events / 433 pin

#### TODO
- [ ] Football spread missing (platform limitation — typeId 16 not returned)
- [ ] Boost API reverse-engineering (would benefit all 6 providers)
- [x] ~~Boxing + cricket~~ — Added sportId 71 (boxing) and 74 (cricket)
- [x] ~~Esports low match rate~~ — Fixed outcome normalization with positional fallback + O(1) lookup indexes

---

## Gecko V2 / OBG Providers (4 active + 1 broken)

> Shared platform: OBG — `events-table/v2` API with header capture.
> Browser-based: load site, capture 16+ `x-sb-*` headers via route interception.
> Pagination: `pageNumber=N` (NOT `page=N`).
> Sport-specific market templates (CRITICAL — must request ALL variants per sport):
> - Standard: MW3W=1x2, MW2W=moneyline, MTG2W/MTG2W25=total, M3WHCP/M2WHCP=spread
> - Ice hockey: TGOUOT=total, MHCPNOT=spread
> - Tennis: MTG2WP=total, M2WHCP=spread
> - Basketball: PTSOUROLMID=total, 2WHCPROLMID=spread
> - Handball: OUALT=total, MWHCPALT=spread
> - Volleyball: MTP=total, MSH=spread
> - Esports: ESMW2W=moneyline, ESHMTHANDICAP=spread
> Category IDs: football=1, ice_hockey=2, handball=3, basketball=4, rugby=7/8, volleyball=9, amfootball=10, tennis=11, curling=20, cricket=26, boxing=30, darts=34, mma=53.

### Betsson

| Metric | Value |
|--------|-------|
| Site | `betsson.com` |
| Extraction time | ~60s |
| Events | 830 |
| Odds | 3,164 |
| Ratio | 3.81 |
| Pin matches | 830 (100%) |
| Markets | 1x2/ml/spread/total |

| Sport | Events | Pin Match |
|-------|-------:|----------:|
| Football | 541 | 541 |
| Basketball | 65 | 65 |
| Tennis | 63 | 63 |
| MMA | 36 | 0 |
| Ice Hockey | 28 | 28 |
| Rugby | 16 | 0 |
| Handball | 14 | 14 |
| Volleyball | 10 | 10 |
| Am. Football | 6 | 0 |
| Darts | 4 | 4 |
| Cricket | 4 | 4 |
| **TOTAL** | **787** | **729** |

**Bonus:** Freebet 250 kr / 1x wager / min 1.80
**Oddsboost:** **IMPLEMENTED** (Gecko V2 boost scraper)

### Betsafe

| Metric | Value |
|--------|-------|
| Site | `betsafe.com` |
| Events | **0** (broken — NOT on OBG platform for Swedish market) |
| Pin matches | **0** |
| Markets | — |

> **BROKEN (2026-02-10)**: `betsafe.com/sv/odds` makes zero `api/sb/` or `playground` requests. The Swedish site uses a different sportsbook backend — NOT the OBG platform. Header capture finds no API headers. Needs platform investigation (likely iframe-embedded or different API pattern).

**Bonus:** Freebet 100 kr / 1x wager / min 1.80
**Oddsboost:** **IMPLEMENTED** (shared with Betsson group — IF we can extract odds)

### NordicBet

| Metric | Value |
|--------|-------|
| Site | `nordicbet.com` |
| Events | 830 |
| Odds | 3,158 |
| Pin matches | 830 (100%) |
| Markets | 1x2/ml/spread/total |

**Bonus:** Freebet 100 kr / 1x wager / min 1.80
**Oddsboost:** **IMPLEMENTED** (shared with Betsson group)

### Spelklubben

| Metric | Value |
|--------|-------|
| Site | `spelklubben.se` (API at `d-cf.spelklubbenplayground.net`) |
| init_path | `/sv/betting` |
| Extraction time | ~47s |
| Events | 812 |
| Odds | 3,078 |
| Ratio | 3.79 |
| Pin matches | 812 (100%) |
| Markets | 1x2/ml/spread/total |

**Bonus:** BonusDep 500 kr / 15x wager / min 1.90 (bad bonus)
**Oddsboost:** Unknown

### Bethard

| Metric | Value |
|--------|-------|
| Site | `bethard.com` (API at `d-cf.bethardplayground.net`) |
| init_path | `/sv/sports` |
| Extraction time | ~45s |
| Events | 812 |
| Odds | 3,080 |
| Ratio | 3.79 |
| Pin matches | 812 (100%) |
| Markets | 1x2/ml/spread/total |
| Normalization | 100% |

**Bonus:** BonusDep 500 kr / 15x wager / min 1.90 (worst bonus)
**Oddsboost:** Not implemented (Combo Booster only, 7-30% on combos)

#### Log
- **2026-02-10**: **Spelklubben re-enabled** — confirmed still on OBG platform (NOT BETBY). Uses GeckoV2Retriever with `init_path: /sv/betting`. 1,766 events / 2,985 odds / 1,187 pin matches in 47s.
- **2026-02-10**: Removed dead SBTech code (SBTechRetriever, BethardRetriever, SpelklubbenRetriever, factory `sbtech` branch). Both Bethard and Spelklubben use GeckoV2Retriever.
- **2026-02-10**: Cache pre-population + threshold relaxation → 341→874 pin (97.7%, +156%).
- **2026-02-09**: Multi-sport expansion — MMA cat ID + sport-specific market templates. 686→729 pin, 11 sports.
- **2026-02-09**: Fixed date filtering bug + dynamic category lookup. 402→686 pin.
- **2026-02-08**: Rewrite complete — `events-table/v2` API with header capture.

#### TODO
- [ ] **CRITICAL: Betsafe broken** — Swedish site NOT on OBG platform. Needs platform investigation.
- [ ] Share browser session across remaining OBG providers (currently separate sessions)
- [ ] MMA/rugby/amfootball: events exist but 0 pin matches — name matching issue
- [ ] Spelklubben ratio 1.69 (low) — may have many events without odds data

---

## Spectate Providers (2)

> Shared platform: `spectate-web.{domain}/spectate/` with bucket-based event loading.
> Browser-based: navigate to site for cookies, then `context.request` for API calls.
> Swedish market names: "Fulltid"=1x2, "Pucklinje"=spread, "Totalt antal mål..."=total.

### Summary Table

| Brand | API | Events | Odds | Bonus |
|-------|-----|-------:|-----:|-------|
| **Mr Green** | `spectate-web.mrgreen.se` | 665 | 1,885 | Freebet 500 kr / 1x / 1.80 |
| **888sport** | `spectate-web.888sport.se` | 664 | 1,883 | BonusDep 500 kr / 1x / 1.80 |

**Oddsboost:** Not implemented (both sites have boost sections)

#### TODO
- [ ] Boost extraction (both sites have boost sections)
- [ ] Spread outcome team name matching edge cases

---

## Vbet (BetConstruct)

| Metric | Value |
|--------|-------|
| Platform | BetConstruct / Swarm WebSocket |
| Retriever | `betconstruct` |
| WS URL | `wss://eu-swarm-newm.vbet.se/` |
| Extraction time | ~17s |
| Events | 905 |
| Odds | 5,266 |
| Ratio | 5.82 |
| Pin matches | 905 (100%) |
| Markets | 1x2/ml/spread/total |
| Normalization | 100% |

> Strongest non-Kambi multi-market provider: 700 spread + 900 total = 1,600 extra odds.
> Esports confirmed live-only on BetConstruct — 0 prematch events.

**Bonus:** BonusDep 800 kr / 10x wager / min 1.80 (marginal value due to 10x)
**Oddsboost:** Unknown

#### TODO
- [ ] 10x wagering makes freebet value marginal

---

## 10Bet

| Metric | Value |
|--------|-------|
| Platform | Playtech/Mojito SPA (DOM scraping) |
| Retriever | `tenbet` |
| Site | `10bet.se` |
| Extraction time | ~287s |
| Events | **810** |
| Odds | **2,340** |
| Ratio | 2.89 |
| Pin matches | **810 (100%)** |
| Markets | 1x2, moneyline |
| Normalization | 100% |
| Mode | **Headless** (no anti-bot protection, 10x faster than headed) |

> **FIXED (2026-02-13):** Switched to headless mode + reduced timeouts + per-provider timeout/sport override.
> 81→810 events (+900%), 178→2,340 odds (+1,215%). Headless eliminates rendering overhead.
> Spread/total not on competition listing pages — only 1x2/moneyline available.

| Sport | Events | Odds |
|-------|-------:|-----:|
| Football | 686 | 2,058 |
| Basketball | 47 | 94 |
| Ice Hockey | 43 | 86 |
| Handball | 34 | 102 |

**Technical details:**
- DOM selectors: `ta-EventListItem`, `ta-participantName`, `ta-price_text`
- Market type codes: MRES=1x2, H2HT/HTOH=ML, HCTG/TPOT/OUTG/FTPO=total, HCMR/HCOT/FHOT/TGHC=spread
- Sport slugs: `martial_arts` for MMA (not `mma`)
- 5 concurrent tabs per sport, 10-competition batches
- `provider_timeout: 600`, `sport_timeout: 180`

**Bonus:** BonusDep 1,000 kr / 8x wager / min 1.80
**Oddsboost:** Unknown

#### Log
- **2026-02-13**: **10x improvement — headless mode + timeout overrides.** Switched from headed to headless (no anti-bot protection needed). Reduced page.goto() 30s→15s, render wait 15s→10s. Added `provider_timeout: 600`, `sport_timeout: 180`. Removed niche sports (volleyball, cricket, table_tennis, boxing, curling). Result: **81→810 events (+900%), 178→2,340 odds (+1,215%), 100% Pinnacle match rate.** Ratio 2.89 (1x2/moneyline — competition pages don't show spread/total).
- **2026-02-12**: Validated orchestrator sport fix — 81 events / 178 odds. Below previous peak — headed mode variability.
- **2026-02-12**: **CRITICAL FIX** — Orchestrator was sending global `kambi_sports` instead of provider's `supported_sports`.
- **2026-02-10**: Cache pre-population + threshold relaxation -> 519->544 pin (99.6%).
- **2026-02-09**: Added 11 market type codes, 5 new sports, cookie fix. 75->235 pin (3.2x).
- **2026-02-08**: NEW — Built DOM scraping extractor.

#### TODO
- [x] ~~Event count variability~~ — **FIXED** Headless mode eliminates rendering inconsistency. 810 events stable.
- [x] ~~Pipeline timeout~~ — **FIXED** `provider_timeout: 600`, `sport_timeout: 180`
- [ ] Spread/total only on individual event detail pages — not extracted from competition listings

---

## Snabbare

| Metric | Value |
|--------|-------|
| Platform | WebSocket + Komigen/Sportradar MTS (WS-only event data) |
| Retriever | `snabbare` |
| Site | `snabbare.com` |
| Extraction time | ~342s (10 sports sequentially) |
| Events | **335** |
| Odds | **387** |
| Ratio | 2.91 |
| Pin matches | **133** |
| Markets | 1x2, moneyline, total |
| Normalization | 95.3% (over/under outcomes from total markets) |
| Mode | Headed browser (headless drops WS data delivery) |

> **REWRITTEN (2026-02-13):** DOM-only league discovery replaces REST API. Proper
> `wait_for_selector` for React async rendering, event-link filtering, dedup by league ID,
> adaptive WS wait (0.15-0.6s), MAX_LEAGUES_PER_SPORT=60 cap.
> Standalone: 131→335 events (+156%), 8 sports including rugby+cricket.
> Pipeline: 133 Pinnacle matches, 387 odds stored.
>
> **Architecture:** REST API (`/sportsbook-api/api/`) only provides metadata. Event/odds
> data is exclusively via WebSocket. Only DOM `el.click()` triggers React Router component
> lifecycle → WS subscription → data delivery. `pushState+popstate` does NOT work.

**Bonus:** BonusDep 600 kr / 8x wager / min 1.80
**Oddsboost:** Not implemented (listed 4/5 on aggregators)

#### Sport Breakdown (pipeline)

| Sport | Events | Odds |
|-------|-------:|-----:|
| Football | 81 | — |
| Basketball | 19 | — |
| Handball | 11 | — |
| Ice Hockey | 8 | — |
| Esports | 7 | — |
| Cricket | 4 | — |
| Rugby | 3 | — |

#### Log
- **2026-02-13 (evening)**: **DOM-only league discovery rewrite.** Removed REST API league URL construction + pushState fallback. Added `wait_for_selector('a[href*="/leagues/"]', timeout=8000)` for React async rendering. Added filter to exclude event-level links (`/events/`). Dedup by league ID via `_extract_league_id()`. Adaptive WS wait (min 0.15s, max 0.6s). `MAX_LEAGUES_PER_SPORT=60` cap. **131→335 events (+156%), 133 Pinnacle matches, 387 odds (ratio 2.91).** New sports: rugby, cricket.
- **2026-02-13 (afternoon)**: **Timeout fixes → 100% match rate.** `sport_timeout: 180→300`, `provider_timeout: 900`. 53→131 events (+147%), match rate 13%→100%.
- **2026-02-13 (morning)**: Spread/total markets added — 8 new market type IDs. WS dedup fix.
- **2026-02-12**: SPA React Router link-clicking fix — 1→307 standalone events.
- **2026-02-12**: Multi-tab WS broken (same as ComeOn Group). Converted to single-tab sequential.
- **2026-02-09**: REWRITTEN — WebSocket/RSocket interception. 172→435 pin (2.5x).

#### TODO
- [x] ~~WS data delivery broken~~ — Fixed via SPA link-clicking approach
- [x] ~~Spread/total markets~~ — Added 8 market type IDs
- [x] ~~LOW MATCH RATE (13%)~~ — **FIXED to 100%** via timeout increases. Swedish names not the issue.
- [x] ~~DOM-only league discovery~~ — Removed REST API, proper wait_for_selector + event-link filtering + dedup + adaptive wait
- [ ] Football event count lower than other providers (81 vs 300+) — league-page WS only delivers 1x2
- [ ] Boost extraction (4/5 on aggregators)
- [ ] 95.3% normalization — `over`/`under` outcomes from total markets not mapping to standard format

---

## ComeOn Group (3)

> Shared platform: ComeOn SPA with RSocket WebSocket data delivery.
> URL pattern: `/sv/sportsbook/sport/{id}-{slug}`.
> **Date-based extraction**: Sport page shows today's events initially. Clicking date buttons
> (11 feb, 12 feb, ...) triggers new WS INITIAL_STATE messages for each date.
> League page navigation does NOT work — WS only delivers data to originating page.
> MarketType IDs: 1=1x2, 175=moneyline, 206=moneyline(OT), 212=total(OT).
> Cookie overlay: OneTrust `#onetrust-accept-btn-handler` + force DOM removal.

### Summary Table

| Brand | Events | Odds | Ratio | Bonus |
|-------|-------:|-----:|------:|-------|
| **ComeOn** | 119 | 353 | 2.97 | BonusDep 500 kr / 6x / 1.80 |
| **Hajper** | 119 | 353 | 2.97 | Freebet 500 kr / 1x / 1.80 |
| **Lyllo Casino** | 119 | 353 | 2.97 | Freebet 100 kr / 1x / 1.80 |

> Event counts fluctuate with upcoming matches (peak: 243/704 on 2026-02-12).
> Date scroll improvement: 111->119 events (+7%), 332->353 odds (+6%) per provider.

**Market types:** 1x2: ~333, moneyline: ~16, total: ~4 per provider.
**Spread markets: NOT available** — WS feed from sport overview pages does not include spread/handicap data. Would require per-event detail page navigation (too expensive).
**Normalization:** 100% across all three providers.
**Extraction time:** ~270s per provider (~16 date buttons x 2s x ~10 sports).
**Shared odds engine:** All 3 brands match to similar Pinnacle events. ComeOn and Hajper share nearly identical odds (~73%), Lyllo runs slightly worse margin (0.01-0.03 lower). Value: 3 separate betting accounts on the same events with different bonuses.
**Oddsboost:** Not implemented (5/5 on aggregators — high priority)

#### ComeOn Sport Breakdown (representative of all 3)

| Sport | Events | Markets |
|-------|-------:|--------:|
| Football | 178 | 177 |
| Basketball | 70-75 | 146-147 |
| Ice Hockey | 44-45 | 39-41 |
| Tennis | 38-39 | 35-36 |
| MMA | 13-24 | 12-18 |
| Esports | 8-9 | 0 (no supported markets) |
| Table Tennis | 6-10 | 7-8 |
| Baseball | 1 | 1 |
| **TOTAL** | ~370 | ~420 |

> Handball and American football showed 0 events (likely no upcoming events on ComeOn).
> Esports events have no 1x2/moneyline/total markets (ComeOn uses different market IDs for esports).

#### Log
- **2026-02-13**: **Date scroll + robust clicking** — Added horizontal scroll of date container to reveal all dates (15->16 buttons). Replaced fragile index-based button clicking with text-label matching (DOM-safe). Added provider_timeout=900s and sport_timeout=120s. Result: 111->119 events (+7%), 332->353 odds (+6%) per provider. 100% pin match rate maintained.
- **2026-02-12**: **Spread market investigation** — Added market type IDs 202/203/213 for spread/total based on Sportradar patterns. Result: WS feed from sport overview pages contains NO spread data (0 unknown market types logged). Spread is only available on individual event detail pages. Reverted to debug-level logging.
- **2026-02-12**: **Volume improvement** — Orchestrator sport fix restored full coverage. Hajper: 48->656 (+1267%), Lyllo: 44->687 (+1461%), ComeOn: 675->704 (+4%). Total markets now include ~62 total per provider (up from 8-10).
- **2026-02-10**: **MAJOR REWRITE — Date-based extraction** — Replaced broken league-page-navigation approach with date-button clicking. ComeOn: 84->623 odds (+642%), Hajper: 48->623 (+1198%), Lyllo: 44->625 (+1320%). All three now at 219 pin matches with 100% normalization. Root cause: WS connection only delivers data to originating page — new tab league navigation received 0 WS frames.
- **2026-02-10**: Added Lyllo Casino (MOA Gaming Sweden / ComeOn Connect). Reuses HajperRetriever.
- **2026-02-09**: REWRITTEN — new URL patterns, 12 sports, 1x2+ML+total. ComeOn 93->298 pin (3.2x), Hajper 135->298 pin (2.2x).

#### TODO
- [x] ~~Spread requires event detail navigation~~ — Confirmed: WS overview feed has NO spread data
- [ ] Boost extraction (5/5 on aggregators — likely valuable)
- [ ] Esports market IDs: investigate ComeOn esports market type IDs for moneyline
- [ ] Speed optimization: skip date button clicking for sports with 0 initial events

---

## Interwetten

| Metric | Value |
|--------|-------|
| Platform | Proprietary SSR (browser, headed mode) |
| Retriever | `interwetten` |
| Site | `interwetten.se` |
| Extraction time | ~168s (two-pass: concurrent listing + detail pages) |
| Events | **305** |
| Odds | **1,235** |
| Ratio | 4.05 |
| Pin matches | **305 (100%)** |
| Markets | 1x2 (765), moneyline (100), spread (200), total (170) |
| Normalization | 100% |
| Mode | Headed (Cloudflare protection) |

> **FIXED (2026-02-13):** Concurrent league navigation (5 parallel tabs for Pass 1) + timeout overrides.
> 20→305 events (+1,425%), 74→1,235 odds (+1,569%). Football now included (215 events, 959 odds).
> Spread+total from detail page enrichment working across all sports.

| Sport | Events | Odds |
|-------|-------:|-----:|
| Football | 215 | 959 |
| Basketball | 34 | 92 |
| Ice Hockey | 18 | 54 |
| Handball | 17 | 81 |
| Rugby | 5 | 17 |
| Volleyball | 6 | 12 |
| Cricket | 6 | 12 |
| Boxing | 4 | 8 |

**Two-pass extraction:** concurrent league listing (5 tabs) → 1x2/ML, then concurrent event detail pages (5 tabs) → spread+total.
**Config:** `provider_timeout: 900`, `sport_timeout: 300` (football has ~130 leagues).

**Bonus:** BonusDep 1,000 kr / 5x wager / min **1.70** (best wagering ratio of all providers!)
**Oddsboost:** Exists on site but not implemented

#### Log
- **2026-02-13**: **15x improvement — concurrent tabs + timeout overrides.** Pass 1 rewritten: 5 concurrent tabs for league navigation (was sequential). Added `provider_timeout: 900`, `sport_timeout: 300`. Trimmed niche sports. Result: **20→305 events (+1,425%), 74→1,235 odds (+1,569%), 100% Pinnacle match rate.** Football: 215 events, 959 odds (was 0 — sport timeout killed it before).
- **2026-02-13**: Concurrency tuning — CONCURRENT_DETAIL_PAGES 8→5, detail timeout 15→20s.
- **2026-02-12**: Error threshold + timeout improvements — 81→99 events.
- **2026-02-12**: Orchestrator sport filtering fix — received 3 sports instead of 12.
- **2026-02-09**: Expanded from 27 to 155+ leagues, 12 sports. 4→183 pin.

#### TODO
- [x] ~~Headed mode flaky~~ — **FIXED** via concurrent tabs + timeout overrides. 305 events stable.
- [x] ~~Football missing~~ — **FIXED** with `sport_timeout: 300`. 215 football events.
- [x] ~~Spread/total markets~~ — Working: 200 spread + 170 total odds
- [ ] Best bonus in the system (1,000 kr / 5x / 1.70) — maximizing coverage is valuable
- [ ] Boost extraction (exists on site)

---

## Coolbet

| Metric | Value |
|--------|-------|
| Platform | GAN/Coolbet (Camoufox anti-detect Firefox) |
| Retriever | `coolbet` |
| Site | `coolbet.com` |
| Extraction time | ~105s (standalone), ~85s (pipeline) |
| Events (standalone) | **276** |
| Events (pipeline) | **54** (matched Pinnacle) |
| Odds (pipeline) | **180** |
| Ratio | 3.33 (pipeline) |
| Pin matches | **54 (100% of stored)** |
| Markets | 1x2 (120), total (32), moneyline (28) |
| Normalization | 100% |
| Mode | **Camoufox headless** (automated, no manual Chrome needed) |

> **CRACKED (2026-02-12):** Camoufox (anti-detect Firefox with C++-level fingerprint injection)
> bypasses Imperva/Incapsula Reese84 challenge. No more CDP requirement!
> **HEADLESS FIX (2026-02-13):** Switched from `headless=False` to `headless=True` + reduced
> `humanize` 1.5→0.2, Imperva sleep 3s→1s. Startup from ~120s→~30s. Total: >600s→105s.
> Install: `pip install camoufox[geoip] && python -m camoufox fetch`
>
> Category IDs: Football=62, Basketball=77, Tennis=72, Ice Hockey=85, AmFoot=58, Baseball=96, MMA=20491, Esports=65035, Handball=68

#### Sport Breakdown (standalone)

| Sport | Events | Time |
|-------|-------:|-----:|
| Football | 138 | 92.3s |
| Handball | 73 | 1.3s |
| Ice Hockey | 29 | 1.6s |
| Basketball | 16 | 2.6s |
| MMA | 13 | 0.5s |
| Esports | 7 | 3.4s |
| Tennis | 0 | 2.5s |
| Am. Football | 0 | 0.4s |
| Baseball | 0 | 0.4s |
| **TOTAL** | **276** | **105.0s** |

#### Pipeline Sport Breakdown

| Sport | Events | Odds |
|-------|-------:|-----:|
| Football | 27 | 103 |
| MMA | 12 | 24 |
| Handball | 6 | 26 |
| Ice Hockey | 7 | 21 |
| Basketball | 2 | 6 |
| **TOTAL** | **54** | **180** |

> 54/276 events match Pinnacle (19.6%) — Coolbet covers many lower-league football
> matches that Pinnacle doesn't. All stored events are 100% Pinnacle-matched.
> Football dominates extraction time (92.3s) due to heavy category pagination.

**Config:** `provider_timeout: 300`, `sport_timeout: 180`
**Bonus:** BonusDep 1,000 kr / 6x wager / min **1.50** (second-best min odds)
**Oddsboost:** Has `/sv/oddsboost` page — now accessible via Camoufox (not implemented yet)

#### Log
- **2026-02-13 (afternoon)**: **PIPELINE WORKING — headless Camoufox.** Switched `headless=True` + `humanize=0.2` + Imperva sleep 3→1s. Extraction: >600s→105s standalone, ~85s pipeline. Pipeline: **0→180 odds, 54 matched events, 10 value bets.** Football: 138 events in 92.3s (heavy pagination). `provider_timeout: 300`, `sport_timeout: 180`.
- **2026-02-13 (morning)**: **Standalone: 69→264 events, 237→4,276 odds.** Football pagination now working (30→135 events), all 9 sports extracted. Added MMA "Fight Result (Draw No Bet)" as moneyline market. Added "fight result" fallback to market normalizer. Pipeline still times out — Camoufox startup + 9 sports exceeds 300s.
- **2026-02-12**: **CRACKED — Camoufox integration.** Installed camoufox v0.4.11 + browser binary v135.0.1-beta.24. Rewrote `CoolbetRetriever._ensure_camoufox()` using `AsyncCamoufox(headless=False, geoip=True, humanize=1.5, os="windows")`. Added `close()` override for proper cleanup. Result: **0 -> 237 odds, 69 events, 9 value bets** — fully automated!
- **2026-02-12**: Harmless "I/O operation on closed pipe" warnings on shutdown — Python 3.13 asyncio proactor events from camoufox subprocess cleanup. Suppressed with try/except in `_cleanup_camoufox()`.
- **2026-02-10**: Fixed missing start_time fallback + cache pre-population + store ALL spread/total lines.
- **2026-02-09**: Added pagination + market fixes. 81->195 events, 39 pin matches.

#### TODO
- [x] ~~CDP only — needs manual Chrome~~ CRACKED with Camoufox!
- [x] ~~Football event count low~~ — Pagination now working (30→135 events)
- [x] ~~Spread market count low~~ — spread/total now extracted across all sports
- [x] ~~MMA "Fight Result" not mapped~~ — Added as moneyline
- [x] ~~Pipeline timeout~~ — **FIXED** with headless Camoufox. `provider_timeout: 300`, `sport_timeout: 180`. 0→180 odds in pipeline.
- [ ] Oddsboost extraction (now accessible via Camoufox)
- [ ] Great bonus (1,000 kr / 6x / 1.50) — now automated!

---

## Tipwin

| Metric | Value |
|--------|-------|
| Platform | Tipwin SPA (browser API interception) |
| Retriever | `tipwin` |
| Site | `tipwin.se` |
| API | `api-web.tipwin.se/v2/{agencyId}/offer/data` (agency 100683) |
| Extraction time | ~58s |
| Events | 560 |
| Odds | 2,012 |
| Ratio | 3.59 |
| Pin matches | 560 (100% of extracted) |
| Markets | 1x2/total/spread |
| Normalization | 100% |

> Orchestrator sport fix validated. Multi-sport extraction confirmed in prior runs.
> Latest pipeline: 450 events / 1,636 odds (football only). Markets: 1x2: 1,350, total: 286.

**Technical details:**
- `bettingTypes[id].abrv`: "3way"=1x2, "over-under"=total, "handicap-hcp"=spread
- Outcome `tip`: "1"=home, "X"=draw, "2"=away, "+"=over, "-"=under
- Pagination: `?page=N` direct navigation (~69 pages)

**Bonus:** BonusDep 1,000 kr / 7x wager / min 1.80
**Oddsboost:** Unknown

#### Log
- **2026-02-12**: **Validated orchestrator sport fix** — 560 events / 2,012 odds. Multi-sport extraction working (1x2: 1,680, total: 332). Below previous peak of 824 events — likely fewer upcoming events.
- **2026-02-12**: **CRITICAL FIX** — Same orchestrator sport filtering bug. Only football extracted (460 events) instead of all 11 supported sports.
- **2026-02-10**: Cache pre-population + threshold relaxation -> 390->784 pin (95.1%, +101%).
- **2026-02-09**: Optimized pagination 420s->58s (7x). 72->390 pin (5.4x).

#### TODO
- [x] ~~RE-EXTRACT to validate orchestrator sport fix~~ — Validated: 560 events, 2,012 odds
- [x] **FIXED**: Match rate 36.2% -> 95.1%
- [ ] European handicap -> Asian handicap conversion for spread markets

---

## Changelog

### 2026-02-14
- **Global performance optimization pass** — Systematic optimization of all browser-based providers, ordered by worst extraction time. Focused on reducing sleep/wait times, parallelizing I/O, and replacing fixed waits with adaptive polling.
- **Snabbare (342s → ~200s est.)** — Halved per-league overhead: `LEAGUE_SETTLE_TIME` 0.15→0.05s, `MAX_LEAGUE_SETTLE_TIME` 0.6→0.4s, poll interval 0.1→0.05s, back-nav wait 0.1→0.05s, post-selector wait 0.5→0.3s, session init 2→1s.
- **ComeOn Group (270s → ~180s est.)** — Replaced fixed 2s date-click waits with adaptive WS polling (0.3s min, poll every 0.1s, max 1.5s). Reduced page load wait 1.5→1s, WS init wait 3→2s, scroll wait 0.5→0.3s. Saves ~16-20s across 16 date buttons.
- **Coolbet (105s → ~60s est.)** — Parallelized football category pagination: 5 concurrent page fetches via `asyncio.gather()` (was sequential). Reduced Imperva sleep 4→2s. Football pagination was 92.3s/105s — expected ~4-5x speedup on pagination phase.
- **Interwetten (168s → ~140s est.)** — Increased `CONCURRENT_DETAIL_PAGES` 5→7, reduced detail page render wait 150→100ms.
- **10Bet (287s → ~240s est.)** — Increased `MAX_COMPETITIONS_PER_SPORT` 60→80, replaced fixed 200ms odds wait with selector-based `wait_for_selector('[class*="ta-price_text"]', timeout=500)`, reduced retry wait 2→1.5s.
- **Spectate (~65s → ~55s est.)** — Removed `networkidle` wait (could stall 3-10s), reduced session init wait 5→3s.
- **Tipwin (58s → ~48s est.)** — Reduced session init 2→1.5s, page listing wait 2→1.5s, inter-page wait 0.5→0.3s, empty page fallback 0.8→0.5s.
- **Kambi config** — Reduced `post_extraction_delay_ms` 15000→10000 (saves ~35s across 8 Kambi brands).

### 2026-02-13 (night)
- **ComeOn Group date scroll + robust clicking** — Added horizontal scroll of date container to reveal all dates (15→16 buttons). Replaced fragile index-based button clicking with text-label matching (DOM-safe — indices shift when date clicks render new event buttons). Added `provider_timeout: 900s`, `sport_timeout: 120s` for all 3 providers. **111→119 events (+7%), 332→353 odds (+6%) per provider, 100% pin match rate.**
- **Altenar boxing + cricket** — Added sportId 71 (boxing) and 74 (cricket) to SPORT_MAPPING and `supported_sports`. 6 Altenar providers gain boxing/cricket extraction. +1 boxing event matched Pinnacle (limited overlap).
- **Config path fix** — Discovered `backend/config/providers.yaml` is NOT loaded by code; actual config is `backend/src/config/providers.yaml` (via `paths.py:get_config_dir()`). Updated CLAUDE.md and MEMORY.md.
- **Full pipeline run (all providers):** 1,931 events / ~63,481 odds / 69.6% cross-provider matching.

### 2026-02-13 (evening)
- **Snabbare DOM-only league discovery rewrite** — Removed REST API league URL construction + pushState fallback. DOM-only approach: `wait_for_selector` for React async rendering, event-link filtering (`/events/`), dedup by league ID, adaptive WS wait (0.15-0.6s), `MAX_LEAGUES_PER_SPORT=60` cap. **131→335 standalone events (+156%), 133 Pinnacle matches, 387 odds (ratio 2.91).** New sports: rugby, cricket.
- **Coolbet assessed — no further optimization** — 276 standalone events, 54 match Pinnacle (19.6%). Low match rate is inherent Pinnacle coverage gap (Coolbet covers lower-league football). Already supports spread/total. No code changes needed.
- **Full pipeline run (all providers):** 1,854 events / ~57,034 odds / 68.6% cross-provider matching / 393 value bets (≥5% edge).

### 2026-02-13 (afternoon)
- **10Bet 10x improvement — headless mode** — Switched from headed to headless (no anti-bot protection). Reduced `page.goto()` 30→15s, render wait 15→10s. Added `provider_timeout: 600`, `sport_timeout: 180`. Removed niche sports. **81→810 events (+900%), 178→2,340 odds (+1,215%), 100% Pinnacle match.** Ratio 2.89.
- **Interwetten 15x improvement — concurrent tabs** — Rewrote Pass 1 with 5 concurrent league navigation tabs (was sequential). Added `provider_timeout: 900`, `sport_timeout: 300`. Trimmed niche sports. **20→305 events (+1,425%), 74→1,235 odds (+1,569%), 100% Pinnacle match.** Football: 215 events, 959 odds. Spread (200) + total (170) from detail enrichment.
- **Snabbare 100% match rate** — Increased `sport_timeout: 180`, `provider_timeout: 900`. Reduced `LEAGUE_SETTLE_TIME` 1.5→1.0s, back-nav 0.5→0.3s. **53→131 events (+147%), 157→372 odds (+137%), match rate 13%→100%.** Swedish names NOT the bottleneck — timeouts were.
- **Coolbet pipeline WORKING** — Switched Camoufox to `headless=True`, `humanize=0.2`, Imperva sleep 3→1s. Extraction: >600s→105s. Pipeline: **0→180 odds, 54 matched events, 100% normalization, 10 value bets.** Football: 138 events (92.3s heavy pagination). `provider_timeout: 300`, `sport_timeout: 180`.
- **Per-provider sport_timeout override** — New `sport_timeout` field in `ProviderConfig` allows per-provider sport timeout. Wired in orchestrator. Used by 10Bet (180s), Snabbare (180s), Interwetten (300s).
- **Pipeline run (29 providers, excl. Coolbet):** 1,841 events / 56,854 odds / 66.3% cross-provider matching.

### 2026-02-13 (morning)
- **Snabbare spread/total market IDs discovered** — Ran WS diagnostic script to capture all market type IDs from league pages. Discovered 8 new IDs: total (212=basketball OT, 1621/1622=ice hockey), spread (1619/1625=puck line), moneyline variants (175/206/376). Standalone: 307→407 events (+33%), 357→1,273 odds (+257%). Pipeline: only 53 matched / 157 odds (13% match rate — Swedish team names).
- **Snabbare WS data dedup fix** — Markets and selections accumulated duplicates from repeated WS messages across league navigations (e.g., 3,906 outcomes for 49 ice hockey events vs expected 147). Fixed by deduping markets by `market.id` and selections by `(marketId, outcomeType, name)` key.
- **Coolbet MMA market mapping** — Added "Fight Result (Draw No Bet)" → moneyline in MARKET_MAP. Added "fight result" fallback in `_normalize_market_type()`. Standalone: 264 events / 4,276 odds (football 135, handball 76, ice hockey 29, basketball 16, esports 7). Pipeline: timed out (Camoufox + 9 sports > 300s).
- **10Bet SPA render reliability** — Changed `_discover_competitions()` from fixed `wait_for_timeout(2000)` to `wait_for_selector('a[href*="competitions/"]', timeout=8000)` with retry. Increased event item timeout 10s→15s. Still times out in pipeline (headed mode variability).
- **Interwetten concurrency tuning** — Reduced CONCURRENT_DETAIL_PAGES 8→5, detail page timeout 15s→20s, error threshold 15→20. Pipeline: 27 events / 100 odds (headed mode still flaky).
- **Pipeline run (28 providers):** 1,573 events / 53,396 odds / 430 value bets / 68.4% cross-provider matching. 10bet + coolbet timed out. All other providers at 100% normalization.

### 2026-02-12 (night)
- **FIXED: Snabbare SPA React Router link-clicking** — Root cause: `page.goto()` to each of 457 leagues destroyed and recreated WS connections, causing O(n²) duplicate messages and 20+ min extraction time. Fix: Navigate to each sport page (14 gotos), then click league links in DOM sidebar via `el.click()` → React Router handles SPA navigation without page reload → existing WS delivers league data. `history.back()` returns to sport page. Per-sport extraction: football 24.7s, ice_hockey 15.4s, basketball 29.5s, tennis 89.4s — all under 120s sport timeout. Result: 1->307 events (+30,600%), 3->357 odds, 122 Pinnacle matches. Also added OneTrust overlay removal and text WS frame support.

### 2026-02-12 (evening)
- **CRACKED: Coolbet Imperva bypass with Camoufox** — Installed camoufox v0.4.11 (anti-detect Firefox with C++-level fingerprint injection). Imperva Reese84 challenge bypassed automatically. Rewrote `CoolbetRetriever` with `AsyncCamoufox(headless=False, geoip=True, humanize=1.5)`. Added `close()` override for proper cleanup. Result: 0->237 odds, 69 events, 9 value bets. No more manual CDP needed!
- **Hajper +1267%, Lyllo +1461%** — Orchestrator sport fix restored full coverage. Hajper: 48->656 odds. Lyllo: 44->687 odds. Both now extracting across all supported sports with date-based WS approach.
- **ComeOn spread investigation** — Added market type IDs 202/203/213 for spread. Result: WS overview feed has NO spread data at all (0 unknown market types). Spread only on event detail pages (too expensive). Confirmed and documented.
- **Interwetten resilience** — Error threshold 5->15, detail page timeout 10->15s. Result: 81->99 events (+22%), 219->301 odds (+37%).
- **Snabbare settle time** — Increased LEAGUE_SETTLE_TIME 1.2->2.5s. Did NOT fix the issue (still only 1-3 odds). WS data delivery problem is deeper than timing.
- **Full pipeline validation** — All 31 providers extracted. 1,724 events, ~97K total odds, 90.6% cross-provider matching, 685 value bets (>=5% edge), 100% outcome normalization across all providers.

### 2026-02-12 (morning)
- **CRITICAL: Orchestrator sport filtering fix** — `orchestrator.py` was sending the same global `kambi_sports` list (Pinnacle-filtered) to ALL providers, completely ignoring each provider's `supported_sports` config from `providers.yaml`. Effect: providers only received sports that happened to overlap with the global list. Impact:
  - Snabbare: ~900 → 4 events (only ice_hockey survived)
  - 10Bet: ~773 → 21 events (missed football entirely)
  - Interwetten: ~714 → 81 events (missed football entirely)
  - Tipwin: ~824 → 460 events (only football survived)
  - ComeOn Group: ~376 → ~192 events (reduced)
  - Fix: `orchestrator.py` line 647-661 now checks `getattr(provider_cfg, 'supported_sports', None)` and uses provider-specific sports list when available, intersected with `sharp_sports` and ordered by Pinnacle event count.
- **CRITICAL: Snabbare multi-tab WS fix** — Same root cause as ComeOn Group (2026-02-10): WS connections only deliver INITIAL_STATE frames to the originating page. 7 extra tabs received 0 data → only 4 events extracted. Converted from 8-concurrent-tab to single-tab sequential league navigation.
- **DB locked fix** — Added retry logic with exponential backoff (3 retries, 0.5s → 1s → 2s) to `OddsBatchProcessor.flush()` for SQLite "database is locked" errors during concurrent extraction. Also added per-sport `session.commit()` in orchestrator to release locks sooner.
- **Profile settings wired to StakeCalculator** — Profile stores `kelly_fraction`, `max_stake_pct`, `min_edge_pct` but StakeCalculator was using hardcoded defaults (0.75 max Kelly, 3% cap, 1% min edge). Now profile settings control the calculator in `bankroll_service.py`, `opportunity_service.py`, and `polymarket.py`.
- **Pinnacle pagination warnings** — Confirmed as expected behavior. `matchupCount` from API includes live events and non-1x2 markets. `no_markets=273` for football = events with only prop/special markets.

### 2026-02-10
- **ComeOn Group date-based rewrite** — Discovered WS connections only deliver data to originating page (league page navigation in new tabs gets 0 frames). Rewrote all 3 extractors (ComeOn, Hajper, Lyllo) to click through date buttons on sport pages instead. ComeOn: 84→623 odds (+642%), Hajper: 48→623 (+1198%), Lyllo: 44→625 (+1320%). All at 219 pin matches, 100% normalization, 2.84-2.85 ratio.
- **Spelklubben re-enabled** — Confirmed still on OBG platform (was incorrectly marked as BETBY). Uses GeckoV2Retriever with `init_path: /sv/betting`, API at `d-cf.spelklubbenplayground.net`. 1,766 events / 2,985 odds / 1,187 pin matches in 47s. Deleted dead SBTech code (SBTechRetriever, BethardRetriever, SpelklubbenRetriever, factory `sbtech` branch).
- **Lyllo Casino added** — ComeOn Group brand #3 (MOA Gaming Sweden). 327 football events, 12 sports, 100kr freebet. Reuses `HajperRetriever` (same RSocket WS platform).
- **Kambi event-level caching** — All 8 Kambi brands share identical events from the same API backend. First brand fetches + parses events per group, subsequent brands clone with their provider_id. Saves ~350 redundant HTTP requests per run. TTL: 5 minutes.
- **Altenar outcome normalization overhaul** — Added positional fallback for 2-way markets (moneyline, spread): when outcome name doesn't match team names (common in esports/MMA), use position index (first=home, second=away). Also added numeric ("1"/"2") and keyword ("hemma"/"borta") fast paths. Expected: esports 2→30+ pin matches.
- **Altenar O(1) lookups** — Built pre-indexed dicts for competitors, champs, markets, odds instead of O(n) list scans per lookup. Reduces ~6000 O(n) scans to O(1) dict lookups per sport.
- **Coolbet store ALL spread/total lines** — Previously picked "most balanced" line which rarely matched Pinnacle's exact point → 0 spread/total stored. Now stores all lines, lets storage pipeline filter to Pinnacle's point. Expected: 39→100+ pin matches with spread/total coverage.
- **10Bet speed optimization** — SPA render wait 5000→3000ms, odds load wait 1000→500ms, concurrency 3→5 tabs. Expected: 547→~350s (36% faster).
- **Snabbare speed optimization** — WS settle time 2.0→1.2s, concurrent tabs 3→5. Expected: 283→~170s (40% faster).
- **Interwetten speed optimization** — Detail page settle time 500→250ms, concurrent detail pages 3→5. Expected: 332→~200s (40% faster).
- **Event cache pre-population from DB** — Critical fix: fuzzy matching cache was empty when extracting subsets. All providers now match against existing DB events.
  - Tipwin: 390→784 pin (+101%, 95.1% rate)
  - Bethard: 341→874 pin (+156%, 97.7% rate)
  - 10Bet: 519→544 pin (99.6% rate)
- **Fuzzy threshold relaxation** — `min_individual` 80→75, `max_asymmetry_diff` 20→25, `min_for_asymmetry_check` 85→80. All configurable via `providers.yaml`.
- **Coolbet start_time fallback** — Missing dates generated "unknown" → 0 fuzzy candidates. Now uses `datetime.now(UTC)`.
- **Esports + MMA aliases** — Added 20+ esports team + 15 MMA fighter aliases.

### 2026-02-09
- Country name aliases (sv→en) — +144 pin for Kambi (367→511), +10 for Altenar
- Gecko V2 multi-sport expansion — 11 sports, sport-specific market templates, 686→729 pin
- Bethard migration to Gecko V2/SBTech — 19→341 pin
- Interwetten league expansion + spread/total — 4→183 pin, 12 sports
- Tipwin speed optimization — 420s→58s (7x), 72→390 pin
- 10Bet market expansion — 75→519 pin, 11 market codes, 14 sports
- ComeOn/Hajper rewrite — 93→298 / 135→298 pin, 12 sports
- Snabbare WS/RSocket rewrite + concurrency — 172→619 pin, 751s→283s
- Coolbet pagination + market fixes — 81→195 events
- Vbet validation — 945 events, 667 pin, multi-market confirmed

### 2026-02-08
- Initial extraction pipeline and provider implementations

---

## Priority Roadmap

### High Priority

| Task | Provider(s) | Expected Impact | Effort |
|------|-------------|-----------------|--------|
| Altenar boost API | 6 Altenar | Boost data for 6 providers | Medium |
| ComeOn Group boost extraction | comeon, hajper, lyllo | Boost data (5/5 on aggregators) | Medium |
| Coolbet oddsboost | coolbet | Now accessible via Camoufox | Medium |
| Tipwin multi-sport pipeline | tipwin | Only football extracted in pipeline (450 events) vs 560 peak (all sports) | Low |

### Medium Priority

| Task | Provider(s) | Expected Impact | Effort |
|------|-------------|-----------------|--------|
| Spectate boost extraction | mrgreen, 888sport | Boost data for 2 providers | Medium |
| Snabbare boost | snabbare | Boost data (4/5) | Medium |
| Gecko V2 session sharing | 5 Gecko V2 | Share browser session across betsson/betsafe/nordicbet/spelklubben/bethard | Low |

### Low Priority

| Task | Provider(s) | Expected Impact | Effort |
|------|-------------|-----------------|--------|
| ComeOn Group spread/total | comeon, hajper, lyllo | Confirmed: WS has NO spread data. Too expensive per-event | High |

### Completed

| Task | Provider(s) | Result |
|------|-------------|--------|
| ~~Snabbare DOM-only league discovery~~ | snabbare | **FIXED** 131→335 events (+156%), 133 pin matches, 387 odds. DOM-only, adaptive WS wait, dedup. |
| ~~Coolbet assessed — no further opt~~ | coolbet | **ASSESSED** 54/276 events match Pinnacle (19.6%). Inherent coverage gap — no code fix. |
| ~~Coolbet headless Camoufox~~ | coolbet | **FIXED** 0→180 pipeline odds, 54 matched events. Headless Camoufox, humanize 1.5→0.2, Imperva sleep 3→1s. 105s extraction. |
| ~~10Bet headless + timeouts~~ | 10bet | **FIXED** 81→810 events (+900%), 178→2,340 odds (+1,215%). Headless mode, reduced timeouts. |
| ~~Interwetten concurrent tabs~~ | interwetten | **FIXED** 20→305 events (+1,425%), 74→1,235 odds (+1,569%). 5 parallel tabs, timeout overrides. |
| ~~Snabbare timeout + match rate~~ | snabbare | **FIXED** 53→131 events (+147%), match rate 13%→100%. Timeout overrides, faster league iteration. |
| ~~Per-provider sport_timeout~~ | ALL | New `sport_timeout` field in config. Used by 10Bet (180), Snabbare (180), Interwetten (300). |
| ~~Snabbare spread/total markets~~ | snabbare | **FIXED** 8 new market type IDs. Standalone: 357→1,273 odds (+257%) |
| ~~Snabbare WS dedup~~ | snabbare | **FIXED** Dedup by market ID + selection key prevents O(n²) inflation |
| ~~Coolbet MMA market~~ | coolbet | **FIXED** "Fight Result (Draw No Bet)" mapped as moneyline. Standalone: 237→4,276 odds |
| ~~Coolbet football pagination~~ | coolbet | **FIXED** 30→135 football events in standalone |
| ~~Snabbare SPA link-clicking fix~~ | snabbare | **FIXED** 1->307 events (+30,600%). React Router SPA navigation preserves WS connection |
| ~~Coolbet Imperva bypass~~ | coolbet | **CRACKED** with Camoufox! 0->237 odds, 9 value bets. Fully automated |
| ~~ComeOn spread investigation~~ | comeon, hajper, lyllo | Confirmed: WS overview feed has NO spread data |
| ~~Interwetten resilience~~ | interwetten | Error threshold 5->15, timeout 10->15s. 219->301 odds (+37%) |
| ~~Hajper/Lyllo volume~~ | hajper, lyllo | 48->656 (+1267%), 44->687 (+1461%) |
| ~~Orchestrator sport filtering~~ | ALL | Providers now get their own `supported_sports` instead of global kambi_sports |
| ~~Snabbare multi-tab WS fix~~ | snabbare | Converted to single-tab sequential (WS only delivers to originating page) |
| ~~DB locked fix~~ | ALL | Retry logic + per-sport commits for concurrent extraction |
| ~~Profile -> StakeCalculator wiring~~ | ALL | Profile risk settings now control stake calculator |
| ~~Kambi event caching~~ | 8 Kambi | Saves ~350 HTTP requests per run |
| ~~Altenar esports normalization~~ | 6 Altenar | Positional fallback for 2-way markets |
| ~~Coolbet ALL-lines storage~~ | coolbet | Store all spread/total lines |
| ~~10Bet/Snabbare/Interwetten speed~~ | 3 providers | 36-40% faster extraction |
| ~~Lyllo Casino~~ | lyllo | ComeOn Group brand #3, 327 football events, 100kr freebet |
| ~~Spelklubben re-enable~~ | spelklubben | Re-enabled with GeckoV2, 1,766 events, 1,187 pin matches |
| ~~ComeOn Group date-based rewrite~~ | comeon, hajper, lyllo | Date-button extraction: 84->623, 48->623, 44->625 odds. 219 pin matches each |
| ~~ComeOn date scroll + robust clicking~~ | comeon, hajper, lyllo | Scroll date container, text-label clicking. 111->119 events (+7%) |
| ~~Altenar boxing + cricket~~ | 6 Altenar | Added sportId 71/74. +1 boxing event matched |
| ~~Config path discovery~~ | ALL | Documented: `src/config/providers.yaml` is actual loaded config |
| ~~Global perf optimization pass~~ | ALL browser providers | Adaptive waits, parallel pagination, reduced sleeps. Est. 20-40% faster per provider. |
