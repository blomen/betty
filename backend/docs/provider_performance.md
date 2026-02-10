# Provider Performance Report

> Last updated: 2026-02-10

## Overview

| Metric | Value |
|--------|-------|
| Active providers | 31 (2 sharp + 29 soft) |
| Disabled providers | 1 (betsafe — Swedish site not on OBG platform) |
| Pinnacle baseline | ~1,288 events / ~7,673 odds |
| Cross-provider matching | **~95%+** (improved from 77.7% via cache pre-population + threshold tuning) |

### Pinnacle Sport Baseline

| Sport | Events | 1x2 | ML | Spread | Total |
|-------|-------:|----:|---:|-------:|------:|
| Football | 688 | 2,049 | — | ~600 | ~600 |
| Ice Hockey | 113 | — | ~113 | ~113 | ~113 |
| Basketball | 78 | — | ~78 | ~78 | ~78 |
| Tennis | 67 | — | ~67 | ~67 | ~67 |
| Esports | 36 | — | ~36 | ~36 | ~36 |
| Handball | 13 | ~13 | — | ~13 | ~13 |
| Cricket | 13 | — | ~13 | ~13 | ~13 |
| Volleyball | 7 | ~7 | — | ~7 | ~7 |
| Curling | 4 | ~4 | — | — | — |
| Am. Football | 1 | — | ~1 | ~1 | ~1 |
| Golf | 1 | — | ~1 | — | — |
| **TOTAL** | **~1,021** | **~2,049** | **~662** | **~1,790** | **~1,790** |

---

## Sharp Sources

### Pinnacle

| Metric | Value |
|--------|-------|
| Platform | Sharp REST API |
| Retriever | `pinnacle` |
| API | `guest.api.arcadia.pinnacle.com/0.1` |
| Extraction time | ~7s |
| Events | ~1,288 |
| Odds | ~7,673 |
| Ratio | 5.96 |
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
| Events | ~121 |
| Odds | ~291 |
| Ratio | 2.40 |
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

| Brand | Slug | Pin Match | Bonus | Min Odds |
|-------|------|----------:|-------|----------|
| **Unibet** | `ubse` | ~511 (84.5%) | Freebet 1,000 kr / 1x | 1.80 |
| **LeoVegas** | `leose` | ~511 | BonusDep 600 kr / 6x | 1.80 |
| **Expekt** | `expektse` | ~511 | BonusDep 1,000 kr / 20x | 1.80 |
| **BetMGM** | `betmgmse` | ~511 | Freebet 500 kr / 1x | 1.80 |
| **SpeedyBet** | `speedybetse` | ~511 | BonusDep 500 kr / 12x | 1.80 |
| **X3000** | `speedyspelse` | ~511 | BonusDep 500 kr / 12x | 1.80 |
| **Golden Bull** | `pafgoldense` | ~511 | BonusDep 500 kr / 12x | 1.80 |
| **1X2** | `pafpre1x2se` | ~511 | BonusDep 500 kr / 12x | 1.80 |

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

| Brand | Integration | Events | Pin Match | Bonus | Min Odds |
|-------|-------------|-------:|----------:|-------|----------|
| **Betinia** | `betiniase2` | 1,738 | 443 | BonusDep 1,000 kr / 6x | 1.80 |
| **Lodur** | `lodurse` | 1,896 | 462 | BonusDep 1,000 kr / 6x | 1.80 |
| **CampoBet** | `campose` | 578 | 448 (77.5%) | BonusDep 500 kr / 6x | 1.80 |
| **Swiper** | `swiperse` | 578 | 448 (77.5%) | BonusDep 1,000 kr / 6x | **1.50** |
| **Dbet** | `dbet` | 545 | 415 (76.1%) | Freebet 500 kr / 1x | 1.80 |
| **QuickCasino** | `quickcasinose` | 578 | 448 (77.5%) | BonusDep 500 kr / 6x | 1.80 |

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
- **2026-02-09**: Country name aliases → 433→443 pin matches (+10)
- **2026-02-08**: Initial validation: 1,547 odds / 571 events / 433 pin

#### TODO
- [ ] Football spread missing (platform limitation — typeId 16 not returned)
- [ ] Boost API reverse-engineering (would benefit all 6 providers)
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
| Events | 787 |
| Odds | 2,860 |
| Ratio | 3.63 |
| Pin matches | **729** (92.6%) |
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
| Events | 1,771 |
| Odds | 2,795 |
| Pin matches | **857** |
| Markets | 1x2/ml/spread/total |

**Bonus:** Freebet 100 kr / 1x wager / min 1.80
**Oddsboost:** **IMPLEMENTED** (shared with Betsson group)

### Spelklubben

| Metric | Value |
|--------|-------|
| Site | `spelklubben.se` (API at `d-cf.spelklubbenplayground.net`) |
| init_path | `/sv/betting` |
| Extraction time | ~47s |
| Events | 1,766 |
| Odds | 2,985 |
| Ratio | 1.69 |
| Pin matches | **1,187** (67.2%) |
| Markets | 1x2/ml/spread/total |

**Bonus:** BonusDep 500 kr / 15x wager / min 1.90 (bad bonus)
**Oddsboost:** Unknown

### Bethard

| Metric | Value |
|--------|-------|
| Site | `bethard.com` (API at `d-cf.bethardplayground.net`) |
| init_path | `/sv/sports` |
| Extraction time | ~45s |
| Events | ~895 |
| Odds | ~3,192 |
| Ratio | 3.57 |
| Pin matches | **874** (97.7%) |
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

| Brand | API | Events | Pin Match | Bonus |
|-------|-----|-------:|----------:|-------|
| **Mr Green** | `spectate-web.mrgreen.se` | ~789 | ~367 | Freebet 500 kr / 1x / 1.80 |
| **888sport** | `spectate-web.888sport.se` | ~789 | ~369 | BonusDep 500 kr / 1x / 1.80 |

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
| Events | 945 |
| Odds | 3,900 |
| Ratio | 4.13 |
| Pin matches | **667** (70.6%) |
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
| Extraction time | ~547s |
| Events | ~773 (varies by session) |
| Odds | ~1,602 |
| Ratio | 2.94 |
| Pin matches | **544** (99.6%) |
| Markets | 1x2/ml/spread/total |
| Normalization | 100% |
| Mode | Headed browser (SPA needs full rendering) |

**Technical details:**
- DOM selectors: `ta-EventListItem`, `ta-participantName`, `ta-price_text`
- Market type codes: MRES=1x2, H2HT/HTOH=ML, HCTG/TPOT/OUTG/FTPO=total, HCMR/HCOT/FHOT/TGHC=spread
- Sport slugs: `martial_arts` for MMA (not `mma`)
- 3 concurrent tabs per sport, 10-competition batches

**Bonus:** BonusDep 1,000 kr / 8x wager / min 1.80
**Oddsboost:** Unknown

#### Log
- **2026-02-10**: Cache pre-population + threshold relaxation → 519→544 pin (99.6%). Event count varies by session (773-1409).
- **2026-02-09**: Added 11 market type codes, 5 new sports, cookie fix. 75→235 pin (3.2x).
- **2026-02-08**: NEW — Built DOM scraping extractor.

#### TODO
- [x] **FIXED**: Match rate 36.8% → 99.6% (cache pre-population + threshold relaxation)
- [ ] Extraction time could be faster with more concurrent tabs

---

## Snabbare

| Metric | Value |
|--------|-------|
| Platform | WebSocket + REST API (RSocket interception) |
| Retriever | `snabbare` |
| Site | `snabbare.com` |
| Extraction time | ~283s (was 751s) |
| Events | ~900 |
| Odds | 1,729 |
| Ratio | 2.79 |
| Pin matches | **619** (100% of extracted) |
| Markets | 1x2/ml |
| Normalization | 100% |

> Concurrent tabs (3) with 2.0s settle time per league navigation. 8 sports active.

**Bonus:** BonusDep 600 kr / 8x wager / min 1.80
**Oddsboost:** Not implemented (listed 4/5 on aggregators)

#### Log
- **2026-02-09**: Concurrent tab optimization — 751s→283s (2.7x), 435→619 pin (+42%).
- **2026-02-09**: REWRITTEN — WebSocket/RSocket interception. 172→435 pin (2.5x).

#### TODO
- [ ] Event detail pages may have spread/total markets
- [ ] Boost extraction (4/5 on aggregators)

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

| Brand | Events | Odds | Pin Match | Ratio | Bonus |
|-------|-------:|-----:|----------:|------:|-------|
| **ComeOn** | 376 | 623 | **219** | 2.84 | BonusDep 500 kr / 6x / 1.80 |
| **Hajper** | 400 | 623 | **219** | 2.84 | Freebet 500 kr / 1x / 1.80 |
| **Lyllo Casino** | 394 | 625 | **219** | 2.85 | Freebet 100 kr / 1x / 1.80 |

**Market types:** 1x2: 531, moneyline: 84, total: 8-10 per provider.
**Normalization:** 100% across all three providers.
**Extraction time:** ~260s per provider (14 date buttons × 2s × ~10 sports).
**Shared odds engine:** All 3 brands match to the exact same 219 Pinnacle events. ComeOn and Hajper share nearly identical odds (~73%), Lyllo runs slightly worse margin (0.01-0.03 lower). Value: 3 separate betting accounts on the same events with different bonuses.
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
- **2026-02-10**: **MAJOR REWRITE — Date-based extraction** — Replaced broken league-page-navigation approach with date-button clicking. ComeOn: 84→623 odds (+642%), Hajper: 48→623 (+1198%), Lyllo: 44→625 (+1320%). All three now at 219 pin matches with 100% normalization. Root cause: WS connection only delivers data to originating page — new tab league navigation received 0 WS frames.
- **2026-02-10**: Added Lyllo Casino (MOA Gaming Sweden / ComeOn Connect). Reuses HajperRetriever.
- **2026-02-09**: REWRITTEN — new URL patterns, 12 sports, 1x2+ML+total. ComeOn 93→298 pin (3.2x), Hajper 135→298 pin (2.2x).

#### TODO
- [ ] Spread requires event detail navigation (mt.id=16,17) — too expensive per-event
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
| Extraction time | ~332s (two-pass: listing + detail pages) |
| Events | ~714 (12 sports) |
| Odds | 745 |
| Ratio | 3.23 |
| Pin matches | **183** |
| Markets | 1x2/ml/spread/total |
| Mode | Headed (Cloudflare protection) |

**Two-pass extraction:** listing pages → 1x2/ML (~100s), then event detail pages → spread+total (3 concurrent tabs, ~230s).

**Bonus:** BonusDep 1,000 kr / 5x wager / min **1.70** (best wagering ratio of all providers!)
**Oddsboost:** Exists on site but not implemented

#### Log
- **2026-02-09**: Expanded from 27 to 155+ leagues, 12 sports. 4→166→183 pin.

#### TODO
- [x] **FIXED**: Spread/total markets (88 spread + 52 total odds)
- [ ] Best bonus in the system (1,000 kr / 5x / 1.70) — maximizing coverage is valuable
- [ ] Further league expansion possible

---

## Coolbet

| Metric | Value |
|--------|-------|
| Platform | GAN/Coolbet (browser + CDP required) |
| Retriever | `coolbet` |
| Site | `coolbet.com` |
| Extraction time | ~30s |
| Events | 195 |
| Odds | 158 |
| Ratio | 4.05 |
| Pin matches | **39** (needs CDP re-test after fixes) |
| Markets | 1x2/ml/spread/total |
| Mode | CDP only (`chrome --remote-debugging-port=9222`) |

> Imperva/Incapsula blocks ALL Playwright-launched browsers. Only works via CDP.
> Category IDs: Football=62, Basketball=77, Tennis=72, Ice Hockey=85, AmFoot=58, Baseball=96, MMA=20491, Esports=65035, Handball=68

**Bonus:** BonusDep 1,000 kr / 6x wager / min **1.50** (second-best min odds)
**Oddsboost:** Has `/sv/oddsboost` page but blocked by Imperva

#### Log
- **2026-02-10**: Fixed missing start_time fallback + cache pre-population. Needs CDP re-test.
- **2026-02-09**: Added pagination + market fixes. 81→195 events, 39 pin matches.

#### TODO
- [ ] Re-test with CDP to validate match rate improvement (expected 39→~150+ pin)
- [ ] Great bonus (1,000 kr / 6x / 1.50) — worth the CDP hassle

---

## Tipwin

| Metric | Value |
|--------|-------|
| Platform | Tipwin SPA (browser API interception) |
| Retriever | `tipwin` |
| Site | `tipwin.se` |
| API | `api-web.tipwin.se/v2/{agencyId}/offer/data` (agency 100683) |
| Extraction time | ~58s |
| Events | ~824 |
| Odds | ~2,882 |
| Ratio | 3.50 |
| Pin matches | **784** (95.1%) |
| Markets | 1x2/total/spread |
| Normalization | 100% |

**Technical details:**
- `bettingTypes[id].abrv`: "3way"=1x2, "over-under"=total, "handicap-hcp"=spread
- Outcome `tip`: "1"=home, "X"=draw, "2"=away, "+"=over, "-"=under
- Pagination: `?page=N` direct navigation (~69 pages)

**Bonus:** BonusDep 1,000 kr / 7x wager / min 1.80
**Oddsboost:** Unknown

#### Log
- **2026-02-10**: Cache pre-population + threshold relaxation → 390→784 pin (95.1%, +101%).
- **2026-02-09**: Optimized pagination 420s→58s (7x). 72→390 pin (5.4x).

#### TODO
- [x] **FIXED**: Match rate 36.2% → 95.1%
- [ ] European handicap → Asian handicap conversion for spread markets

---

## Changelog

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
| Coolbet CDP re-test | coolbet | Validate match rate with ALL-lines fix (expected 39→100+ pin) | Low |
| Validate speed optimizations | 10bet, snabbare, interwetten | Verify reduced timeouts don't drop data quality | Low |
| Altenar boost API | 6 Altenar | Boost data for 6 providers | Medium |
| ComeOn Group boost extraction | comeon, hajper, lyllo | Boost data (5/5 on aggregators) | Medium |

### Medium Priority

| Task | Provider(s) | Expected Impact | Effort |
|------|-------------|-----------------|--------|
| Spectate boost extraction | mrgreen, 888sport | Boost data for 2 providers | Medium |
| Snabbare spread/total | snabbare | More market types (currently 1x2/ml only) | Medium |
| Snabbare boost | snabbare | Boost data (4/5) | Medium |
| Gecko V2 session sharing | 5 Gecko V2 | Share browser session across betsson/betsafe/nordicbet/spelklubben/bethard | Low |

### Low Priority

| Task | Provider(s) | Expected Impact | Effort |
|------|-------------|-----------------|--------|
| ComeOn Group spread/total | comeon, hajper, lyllo | Needs event detail nav (expensive) | High |

### Completed

| Task | Provider(s) | Result |
|------|-------------|--------|
| ~~Kambi event caching~~ | 8 Kambi | Saves ~350 HTTP requests per run |
| ~~Altenar esports normalization~~ | 6 Altenar | Positional fallback for 2-way markets |
| ~~Coolbet ALL-lines storage~~ | coolbet | Store all spread/total lines |
| ~~10Bet/Snabbare/Interwetten speed~~ | 3 providers | 36-40% faster extraction |
| ~~Lyllo Casino~~ | lyllo | ComeOn Group brand #3, 327 football events, 100kr freebet |
| ~~Spelklubben re-enable~~ | spelklubben | Re-enabled with GeckoV2, 1,766 events, 1,187 pin matches |
| ~~ComeOn Group date-based rewrite~~ | comeon, hajper, lyllo | Date-button extraction: 84→623, 48→623, 44→625 odds. 219 pin matches each |
