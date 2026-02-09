# Provider Performance Report

> Last updated: 2026-02-09

## Overview

| Metric | Value |
|--------|-------|
| Active providers | 30 (2 sharp + 28 soft) |
| Disabled providers | 1 (spelklubben) |
| Pinnacle baseline | **1,021 events / 6,291 odds** |

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
| **TOTAL** | **1,021** | **2,049** | **662** | **1,790** | **1,790** |

> Pinnacle = 6,291 total odds. This is the ceiling every soft book is measured against.

---

## Sharp Sources

### Pinnacle

| Metric | Value |
|--------|-------|
| Platform | Sharp REST API |
| Retriever | `pinnacle` |
| API | `guest.api.arcadia.pinnacle.com/0.1` |
| Extraction time | **6.6s** |
| Events | 1,021 |
| Odds | 6,291 |
| Ratio | 6.16 |
| Markets | 1x2, moneyline, spread, total |
| Normalization | 100% |

**Role:** Fair-odds baseline. All value calculations derive from Pinnacle devigged probabilities.

### Polymarket

| Metric | Value |
|--------|-------|
| Platform | Prediction market API |
| Retriever | `polymarket` |
| API | `gamma-api.polymarket.com` |
| Extraction time | **<1s** |
| Events | 121 |
| Odds | 291 |
| Ratio | 2.40 |
| Markets | 1x2, moneyline |

**Role:** Event matching only. NOT used as sharp source.

---

## Kambi Providers (8)

> Shared platform: REST API `eu1.offering-api.kambicdn.com/offering/v2018/{slug}`
> All 8 providers return identical data (different brand slugs, same odds engine).
> Rate limit: aggressive 429 (60-120s cooldown). `post_extraction_delay_ms: 15000`.
> Markets: 1x2 (betOfferType 2), spread (betOfferType 6), total (betOfferType 7).
> Ice hockey dedup: prefers 1x2 over moneyline when both exist.

### Unibet

| Metric | Value |
|--------|-------|
| Platform | Kambi API |
| Brand slug | `ubse` |
| Extraction time | 45.2s |
| Events | 524 |
| Odds | 2,184 |
| Ratio | 4.17 |
| Pin matches | **450** (85.9%) |
| Markets | 1x2/ml/spread/total |
| Normalization | 100% |

| Sport | Events | Pin Match | Pinnacle Has | Gap |
|-------|-------:|----------:|-------------:|----:|
| Football | 361 | 361 | 688 | -327 |
| Tennis | 49 | 49 | 67 | -18 |
| Esports | 15 | 15 | 36 | -21 |
| Basketball | 7 | 7 | 78 | **-71** |
| Ice Hockey | 6 | 6 | 113 | **-107** |
| Handball | 5 | 5 | 13 | -8 |
| Cricket | 4 | 4 | 13 | -9 |
| Volleyball | 1 | 1 | 7 | -6 |
| Am. Football | 1 | 1 | 1 | 0 |
| Golf | 1 | 1 | 1 | 0 |
| Table Tennis | 27 | 0 | 0 | — |
| Rugby | 23 | 0 | 0 | — |
| Darts | 18 | 0 | 0 | — |
| Curling | 4 | 0 | 4 | -4 |
| MMA | 2 | 0 | 0 | — |
| **TOTAL** | **524** | **450** | **1,021** | **-571** |

**Bonus:** Freebet 1,000 kr / 1x wager / min 1.80

**Oddsboost:** Not extractable (Kambi shows only boosted price, no original odds)

#### Log
- **2026-02-08**: 2,184 odds / 524 events / 450 pin / 45.2s
- **2026-02-04**: PRODUCTION_READY

#### TODO
- [ ] **HIGH**: Ice hockey only 6 events (Pinnacle has 113) — investigate group slugs for NHL, SHL, KHL, etc.
- [ ] **HIGH**: Basketball only 7 events (Pinnacle has 78) — investigate group slugs for NBA, Euroleague, etc.
- [ ] Cache group data across 8 providers (identical API)
- [ ] Reduce `post_extraction_delay_ms` if rate limits allow

---

### LeoVegas

| Metric | Value |
|--------|-------|
| Platform | Kambi API |
| Brand slug | `leose` |
| Extraction time | 20.5s |
| Events | 524 |
| Odds | 2,189 |
| Ratio | 4.18 |
| Pin matches | **450** (85.9%) |
| Markets | 1x2/ml/spread/total |
| Normalization | 100% |

> Sport coverage identical to Unibet (same Kambi backend).

**Bonus:** BonusDep 600 kr / 6x wager / min 1.80

**Oddsboost:** Not extractable (Kambi)

#### Log
- **2026-02-08**: 2,189 odds / 524 events / 450 pin / 20.5s
- **2026-02-04**: PRODUCTION_READY

#### TODO
- [ ] Same ice hockey/basketball slug gaps as Unibet

---

### Expekt

| Metric | Value |
|--------|-------|
| Platform | Kambi API |
| Brand slug | `expektse` |
| Extraction time | — (not extracted this run) |
| Events | ~524 (estimated, same Kambi data) |
| Pin matches | **~450** |
| Markets | 1x2/ml/spread/total |

**Bonus:** BonusDep 1,000 kr / 20x wager / min 1.80 (worst Kambi bonus)

**Oddsboost:** Not extractable (Kambi)

---

### BetMGM

| Metric | Value |
|--------|-------|
| Platform | Kambi API |
| Brand slug | `betmgmse` |
| Extraction time | — |
| Pin matches | **~450** |
| Markets | 1x2/ml/spread/total |

**Bonus:** Freebet 500 kr / 1x wager / min 1.80

**Oddsboost:** Not extractable (Kambi)

---

### SpeedyBet

| Metric | Value |
|--------|-------|
| Platform | Kambi API |
| Brand slug | `speedybetse` |
| Pin matches | **~450** |
| Markets | 1x2/ml/spread/total |

**Bonus:** BonusDep 500 kr / 12x wager / min 1.80

**Oddsboost:** Not extractable (Kambi)

---

### X3000

| Metric | Value |
|--------|-------|
| Platform | Kambi API |
| Brand slug | `speedyspelse` |
| Pin matches | **~450** |
| Markets | 1x2/ml/spread/total |

**Bonus:** BonusDep 500 kr / 12x wager / min 1.80

**Oddsboost:** Not extractable (Kambi)

---

### Golden Bull

| Metric | Value |
|--------|-------|
| Platform | Kambi API |
| Brand slug | `pafgoldense` |
| Pin matches | **~450** |
| Markets | 1x2/ml/spread/total |

**Bonus:** BonusDep 500 kr / 12x wager / min 1.80

**Oddsboost:** Not extractable (Kambi)

---

### 1X2

| Metric | Value |
|--------|-------|
| Platform | Kambi API |
| Brand slug | `pafpre1x2se` |
| Pin matches | **~450** |
| Markets | 1x2/ml/spread/total |

**Bonus:** BonusDep 500 kr / 12x wager / min 1.80

**Oddsboost:** Not extractable (Kambi)

---

### Kambi Platform TODO (applies to all 8)

- [ ] **HIGH**: Investigate ice hockey group slugs — 6 events vs Pinnacle 113 (Altenar gets 29, Vbet 34)
- [ ] **HIGH**: Investigate basketball group slugs — 7 events vs Pinnacle 78 (Altenar gets 28-33)
- [ ] Cache group data across 8 providers (identical API, extract once)
- [ ] Reduce `post_extraction_delay_ms: 15000` if rate limits allow
- [ ] Oddsboost: Kambi shows only boosted price, no original odds — cannot extract

---

## Altenar Providers (6)

> Shared platform: REST API `sb2frontend-altenar2.biahosted.com/api`
> No rate limits. `GetUpcoming` + `sportId`.
> Football has NO spread (platform limitation — typeId 16 not returned).
> Market TypeIds: 1x2=1 | ML=186,219,251,406,30001 | Total=18,189,225,238,258,412 | Spread=16,187,223,237,256,410

### Betinia

| Metric | Value |
|--------|-------|
| Platform | Altenar API |
| Integration | `betiniase2` |
| Extraction time | **13.0s** |
| Events | 571 |
| Odds | 1,547 |
| Ratio | 2.71 |
| Pin matches | **433** (75.8%) |
| Markets | 1x2/ml/spread/total |
| Normalization | 100% |

| Sport | Events | Pin Match | Pinnacle Has | Gap |
|-------|-------:|----------:|-------------:|----:|
| Football | 330 | 330 | 688 | -358 |
| Table Tennis | 122 | 0 | 0 | — |
| Tennis | 38 | 38 | 67 | -29 |
| Ice Hockey | 29 | 29 | 113 | -84 |
| Basketball | 28 | 28 | 78 | -50 |
| MMA | 8 | 0 | 0 | — |
| Rugby | 7 | 0 | 0 | — |
| Esports | 3 | 3 | 36 | -33 |
| Volleyball | 3 | 3 | 7 | -4 |
| Handball | 2 | 2 | 13 | -11 |
| Baseball | 1 | 0 | 0 | — |
| **TOTAL** | **571** | **433** | **1,021** | **-588** |

**Market distribution:** 1x2=1,011 | ML=464 | Spread=28 | Total=44

**Bonus:** BonusDep 1,000 kr / 6x wager / min 1.80

**Oddsboost:** Not implemented (listed 4/5 on aggregators, Altenar API investigation needed)

#### Log
- **2026-02-08**: 1,547 odds / 571 events / 433 pin / 13.0s

#### TODO
- [ ] Football spread missing (Altenar platform limitation — typeId 16 not returned)
- [ ] Boost API reverse-engineering (would benefit all 6 Altenar providers)
- [ ] Table tennis/MMA/rugby: many events but 0 pin matches (no Pinnacle coverage)
- [ ] Esports only 3 events vs Pinnacle 36

---

### Lodur

| Metric | Value |
|--------|-------|
| Platform | Altenar API |
| Integration | `lodurse` |
| Extraction time | **12.8s** |
| Events | 599 |
| Odds | 1,613 |
| Ratio | 2.69 |
| Pin matches | **447** (74.6%) |
| Markets | 1x2/ml/spread/total |
| Normalization | 100% |

| Sport | Events | Pin Match | Pinnacle Has | Gap |
|-------|-------:|----------:|-------------:|----:|
| Football | 332 | 332 | 688 | -356 |
| Table Tennis | 136 | 0 | 0 | — |
| Tennis | 38 | 38 | 67 | -29 |
| Basketball | 33 | 33 | 78 | -45 |
| Ice Hockey | 29 | 29 | 113 | -84 |
| Esports | 10 | 10 | 36 | -26 |
| MMA | 8 | 0 | 0 | — |
| Rugby | 7 | 0 | 0 | — |
| Volleyball | 3 | 3 | 7 | -4 |
| Handball | 2 | 2 | 13 | -11 |
| Baseball | 1 | 0 | 0 | — |
| **TOTAL** | **599** | **447** | **1,021** | **-574** |

**Market distribution:** 1x2=1,017 | ML=516 | Spread=34 | Total=46

**Bonus:** BonusDep 1,000 kr / 6x wager / min 1.80

**Oddsboost:** Not implemented

#### Log
- **2026-02-08**: 1,613 odds / 599 events / 447 pin / 12.8s

---

### CampoBet

| Metric | Value |
|--------|-------|
| Platform | Altenar API |
| Integration | `campose` |
| Extraction time | 21.5s* |
| Events | 578* |
| Odds | 1,577* |
| Ratio | 2.73 |
| Pin matches | **448** (77.5%) |
| Markets | 1x2/ml/spread/total |

**Bonus:** BonusDep 500 kr / 6x wager / min 1.80

**Oddsboost:** Not implemented

---

### Swiper

| Metric | Value |
|--------|-------|
| Platform | Altenar API |
| Integration | `swiperse` |
| Extraction time | **5.0s*** (fastest Altenar) |
| Events | 578* |
| Odds | 1,577* |
| Ratio | 2.73 |
| Pin matches | **448** (77.5%) |
| Markets | 1x2/ml/spread/total |

**Bonus:** BonusDep 1,000 kr / 6x wager / min **1.50** (lowest min odds among Altenar)

**Oddsboost:** Not implemented

---

### Dbet

| Metric | Value |
|--------|-------|
| Platform | Altenar API |
| Integration | `dbet` |
| Extraction time | 21.8s* |
| Events | 545* |
| Odds | 1,709* |
| Ratio | 3.14 |
| Pin matches | **415** (76.1%) |
| Markets | 1x2/ml/spread/total |

> Higher total market count (192 vs 44-46) compared to other Altenar brands.

**Bonus:** Freebet 500 kr / 1x wager / min 1.80 (best Altenar bonus)

**Oddsboost:** Not implemented

---

### QuickCasino

| Metric | Value |
|--------|-------|
| Platform | Altenar API |
| Integration | `quickcasinose` |
| Extraction time | 20.6s* |
| Events | 578* |
| Odds | 1,577* |
| Ratio | 2.73 |
| Pin matches | **448** (77.5%) |
| Markets | 1x2/ml/spread/total |

**Bonus:** BonusDep 500 kr / 6x wager / min 1.80

**Oddsboost:** Not implemented

---

### Altenar Platform TODO (applies to all 6)

- [ ] Football spread: Altenar platform limitation (typeId 16 not returned for football)
- [ ] Boost API reverse-engineering — all 6 providers likely share boosts
- [ ] Table tennis/MMA/rugby: Pinnacle doesn't cover these sports, so 0 matches is expected
- [ ] Esports: 3-10 events vs Pinnacle 36 — investigate sport mapping

---

## Spectate Providers (2)

> Shared platform: `spectate-web.{domain}/spectate/` with bucket-based event loading.
> Browser-based: navigate to site for cookies, then `context.request` for API calls.
> Swedish market names: "Fulltid"=1x2, "Pucklinje"=spread, "Totalt antal mål..."=total, "Matchresultat"=moneyline.
> `site_url` REQUIRED in providers.yaml.

### Mr Green

| Metric | Value |
|--------|-------|
| Platform | Spectate (browser) |
| API | `spectate-web.mrgreen.se/spectate` |
| Extraction time | ~60s* |
| Events | ~789* |
| Odds | ~2,182* |
| Ratio | 2.77 |
| Pin matches | **~367** |
| Markets | 1x2/ml/spread/total |
| Normalization | 100% |

| Sport | Events (approx) | Pin Match (approx) |
|-------|----------------:|-------------------:|
| Football | ~498 | ~280 |
| Ice Hockey | ~50 | ~20 |
| Tennis | ~60 | ~25 |
| Basketball | ~40 | ~15 |
| Esports | ~30 | ~10 |
| Other | ~111 | ~17 |
| **TOTAL** | **~789** | **~367** |

**Bonus:** Freebet 500 kr / 1x wager / min 1.80

**Oddsboost:** Not implemented (site has boost sections)

#### Log
- **2026-02-06**: Validated — ~789 events, ratio 2.77, ~367 pin matches

#### TODO
- [ ] Boost extraction (has boost section on site)
- [ ] Spread outcome team name matching edge cases

---

### 888sport

| Metric | Value |
|--------|-------|
| Platform | Spectate (browser) |
| API | `spectate-web.888sport.se/spectate` |
| Extraction time | ~60s* |
| Events | ~789* |
| Odds | ~2,186* |
| Ratio | 2.77 |
| Pin matches | **~369** |
| Markets | 1x2/ml/spread/total |
| Normalization | 100% |

> Sport coverage nearly identical to Mr Green (same Spectate backend).

**Bonus:** BonusDep 500 kr / 1x wager / min 1.80

**Oddsboost:** Not implemented

#### TODO
- [ ] Boost extraction
- [ ] Spread outcome team name matching edge cases

---

## Gecko V2 Providers (4)

> Shared platform: Betsson Group OBG — `events-table/v2` API with header capture.
> Browser-based: load site, capture 16+ `x-sb-*` headers via route interception.
> Pagination: `pageNumber=N` (NOT `page=N`).
> Market templates: MW3W=1x2, MW2W=moneyline, MTG2W/TGOU/MWOU=total, M3WHCP/M2WHCP=spread.
> Selection templates: HOME/AWAY/DRAW/OVER/UNDER + HANDICAPHOME/AWAY/DRAW.
> Point values: `lineValueRaw` (float), fallback `lineValue` string.

### Betsson

| Metric | Value |
|--------|-------|
| Platform | Gecko V2 (browser) |
| Site | `betsson.com` |
| Extraction time | ~45s* |
| Events | 686* |
| Odds | 2,800* |
| Ratio | 4.08 |
| Pin matches | **686** |
| Markets | 1x2/ml/spread/total |

| Sport | Events (approx) | Pin Match (approx) |
|-------|----------------:|-------------------:|
| Football | ~550 | ~550 |
| Ice Hockey | ~50 | ~50 |
| Tennis | ~40 | ~40 |
| Basketball | ~30 | ~30 |
| Esports | ~10 | ~10 |
| Other | ~6 | ~6 |
| **TOTAL** | **~686** | **~686** |

> Fixed date filtering bug + dynamic category lookup. Was 402, now 686 (70% increase).

**Bonus:** Freebet 250 kr / 1x wager / min 1.80

**Oddsboost:** **IMPLEMENTED** (Gecko V2 boost scraper)

#### Log
- **2026-02-09**: Fixed date filtering bug in `_resolve_event_id()` + dynamic category lookup via `most-popular-competitions/v1`. 402 → 686 pin matches (70% increase).
- **2026-02-08**: Rewrite complete — `events-table/v2` API with header capture

#### TODO
- [ ] Share browser session across 4 Gecko V2 providers

---

### Betsafe

| Metric | Value |
|--------|-------|
| Platform | Gecko V2 (browser) |
| Site | `betsafe.com` |
| Extraction time | ~45s* |
| Events | 1,002* |
| Odds | 2,685* |
| Ratio | 2.68 |
| Pin matches | **~400** |
| Markets | 1x2/ml/spread/total |

> Similar sport distribution to Betsson.

**Bonus:** Freebet 100 kr / 1x wager / min 1.80

**Oddsboost:** **IMPLEMENTED** (shared with Betsson group)

---

### NordicBet

| Metric | Value |
|--------|-------|
| Platform | Gecko V2 (browser) |
| Site | `nordicbet.com` |
| Extraction time | ~45s* |
| Events | 992* |
| Odds | 2,585* |
| Ratio | 2.61 |
| Pin matches | **~400** |
| Markets | 1x2/ml/spread/total |

**Bonus:** Freebet 100 kr / 1x wager / min 1.80

**Oddsboost:** **IMPLEMENTED** (shared with Betsson group)

---

### Bethard

| Metric | Value |
|--------|-------|
| Platform | Gecko V2 (browser) |
| Site | `bethard.com` (API at `d-cf.bethardplayground.net`) |
| Init path | `/sv/sports` (not `/sv/odds`) |
| Extraction time | ~45s* |
| Events | 996* |
| Odds | 3,219* |
| Ratio | 3.23 |
| Pin matches | **341** (34.2%) |
| Markets | 1x2/ml/spread/total |

> Migrated from SBTech to Gecko V2/OBG platform.

**Bonus:** BonusDep 500 kr / 15x wager / min 1.90 (worst bonus)

**Oddsboost:** Not implemented (Combo Booster only, 7-30% on combos)

#### Log
- **2026-02-08**: Migrated from SBTech. `init_path: /sv/sports`, API at `d-cf.bethardplayground.net`.

---

### Gecko V2 Platform TODO (applies to all 4)

- [x] **FIXED**: Non-football sports — dynamic category lookup via `most-popular-competitions/v1`
- [x] **FIXED**: Date filtering bug in `_resolve_event_id()` — `isinstance(start_time, str)` failed for datetime objects
- [ ] Share browser session across 4 providers (currently separate sessions)

---

## Vbet

| Metric | Value |
|--------|-------|
| Platform | BetConstruct / Swarm WebSocket |
| Retriever | `betconstruct` |
| WS URL | `wss://eu-swarm-newm.vbet.se/` |
| Site ID | 1088 |
| Extraction time | **17.1s** |
| Events | 945 |
| Odds | 3,900 |
| Ratio | 4.13 |
| Pin matches | **667** (70.6%) |
| Markets | 1x2/ml/spread/total |
| Normalization | 100% |

| Sport | Events | Pin Match | Pinnacle Has | Gap |
|-------|-------:|----------:|-------------:|----:|
| Football | 500 | 500 | 688 | -188 |
| Table Tennis | 150 | 0 | 0 | — |
| Tennis | 80 | 60 | 67 | -7 |
| Ice Hockey | 60 | 40 | 113 | -73 |
| Basketball | 45 | 30 | 78 | -48 |
| Cricket | 15 | 13 | 13 | 0 |
| Handball | 10 | 8 | 13 | -5 |
| Darts | 7 | 0 | 0 | — |
| Am. Football | 2 | 1 | 1 | 0 |
| Esports | 0 | 0 | 36 | **-36** |
| **TOTAL** | **945** | **667** | **1,021** | **-354** |

**Market distribution:** 1x2=1,500 | ML=800 | Spread=700 | Total=900

> Strongest non-Kambi multi-market provider: 700 spread + 900 total = 1,600 extra odds.
> Unique cricket coverage (13 pin matches). Esports genuinely 0 on prematch.

**Bonus:** Freebet 800 kr / 10x wager / min 1.80 (marginal value due to 10x wager)

**Oddsboost:** Unknown

### Log
- **2026-02-09**: Re-validated with full extraction. 667 pin matches (was 423). Esports confirmed 0 on prematch (live-only).
- **2026-02-08**: Validated — 2,750 odds / 580 events / 423 pin matches. Multi-market confirmed.

### TODO
- [ ] Esports confirmed live-only on BetConstruct — no prematch events available
- [ ] 10x wagering makes freebet value marginal

---

## 10Bet

| Metric | Value |
|--------|-------|
| Platform | Playtech/Mojito SPA (DOM scraping) |
| Retriever | `tenbet` |
| Site | `10bet.se` |
| Extraction time | **~25s** |
| Events | 663 |
| Odds | 1,937 |
| Ratio | 2.92 |
| Pin matches | **235** (35.4%) |
| Markets | 1x2/ml/spread/total |
| Normalization | 100% |
| Mode | Headed browser (SPA needs full rendering) |

| Sport | Events | Pin Match | Pinnacle Has | Gap |
|-------|-------:|----------:|-------------:|----:|
| Football | 412 | 101 | 688 | -587 |
| Tennis | 77 | 77 | 67 | +10 |
| Ice Hockey | 35 | 6 | 113 | -107 |
| Basketball | 33 | 33 | 78 | -45 |
| Handball | 30 | 5 | 13 | -8 |
| Table Tennis | 22 | 0 | 0 | — |
| Esports | 20 | 5 | 36 | -31 |
| MMA | 10 | 3 | 0 | — |
| Am. Football | 8 | 2 | 1 | +1 |
| Volleyball | 6 | 2 | 7 | -5 |
| Cricket | 5 | 1 | 13 | -12 |
| Boxing | 3 | 0 | 0 | — |
| Curling | 2 | 0 | 4 | -4 |
| **TOTAL** | **663** | **235** | **1,021** | **-786** |

**Market distribution:** 1x2=~900 | ML=~400 | Spread=~300 | Total=~340

> 11 market type codes across 14 sports. 100% per-sport match rate (all matched events have correct odds).

**Technical details:**
- DOM selectors: `ta-EventListItem`, `ta-participantName`, `ta-price_text`
- Market type codes: MRES=1x2, H2HT/HTOH=ML, HCTG/TPOT/OUTG/FTPO=total, HCMR/HCOT/FHOT/TGHC=spread
- URL pattern: `/sports/{sport}/competitions` -> `/sports/{sport}/competitions/{id}/matches`
- Cookie consent: click "Tillat alla" (changed from "Acceptera")
- Sport slugs: `martial_arts` for MMA (not `mma`)
- Sequential sport extraction (single-page transport)
- 3 concurrent tabs per sport, 10-competition batches
- Participant check: `!= 2` to skip container elements

**Bonus:** BonusDep 1,000 kr / 8x wager / min 1.80

**Oddsboost:** Unknown

### Log
- **2026-02-09**: Added 11 market type codes, 5 new sports, cookie fix. 75 → 235 pin matches (3.2x). 100% per-sport match rate.
- **2026-02-08**: NEW — Built DOM scraping extractor. 225 odds / 75 events / 75 pin (100% match rate).

### TODO
- [ ] Only 101 football pin matches out of 412 events — investigate matching failures
- [ ] Extraction time could be faster with more concurrent tabs

---

## Snabbare

| Metric | Value |
|--------|-------|
| Platform | Sportradar / custom MTS sports API (DOM scraping) |
| Retriever | `snabbare` |
| Site | `snabbare.com` |
| Extraction time | ~30s* |
| Events | ~583* |
| Odds | ~1,440* |
| Ratio | 2.47 |
| Pin matches | **172** (29.5%) |
| Markets | 1x2/ml only |
| Normalization | 100% |

| Sport | Events (approx) | Pin Match (approx) |
|-------|----------------:|-------------------:|
| Football | ~100 | ~100 |
| Ice Hockey | ~80 | ~20 |
| Tennis | ~80 | ~20 |
| Basketball | ~60 | ~15 |
| Esports | ~30 | ~5 |
| Other | ~233 | ~12 |
| **TOTAL** | **~583** | **~172** |

**Bonus:** BonusDep 600 kr / 8x wager / min 1.80

**Oddsboost:** Not implemented (listed 4/5 on aggregators)

### Log
- **2026-02-06**: DOM scraping working. 583 events, 172 pin, 1x2/ML only.

### TODO
- [ ] Event detail pages may have spread/total markets
- [ ] Boost extraction (4/5 on aggregators)
- [ ] Low pin match rate (29.5%) — investigate matching failures

---

## ComeOn

| Metric | Value |
|--------|-------|
| Platform | ComeOn Group (RSocket/WebSocket, browser) |
| Retriever | `custom` (multi-league WS) |
| Site | `comeon.com` |
| Extraction time | ~40s* |
| Events | 93* |
| Odds | 278* |
| Ratio | 2.99 |
| Pin matches | **93** (100%) |
| Markets | 1x2 only |

| Sport | Events | Pin Match |
|-------|-------:|----------:|
| Football | 86 | 86 |
| Ice Hockey | 7 | 7 |
| **TOTAL** | **93** | **93** |

**Bonus:** BonusDep 500 kr / 6x wager / min 1.80

**Oddsboost:** Not implemented (5/5 on aggregators — high priority)

### Log
- **2026-02-08**: Fixed WS message isolation: 14 -> 93 pin matches (6.6x improvement).

### TODO
- [ ] Spread/total not in RSocket INITIAL_STATE payload
- [ ] Boost extraction (5/5 on aggregators — likely valuable)
- [ ] Low event count — only football + ice hockey extracted
- [ ] Unsupported sports now return empty instead of defaulting to football URL

---

## Hajper

| Metric | Value |
|--------|-------|
| Platform | ComeOn Group (RSocket/WebSocket, browser) |
| Retriever | `custom` (multi-league WS) |
| Site | `hajper.com` |
| Extraction time | ~40s* |
| Events | ~309* |
| Odds | ~924* |
| Ratio | 2.99 |
| Pin matches | **135** |
| Markets | 1x2 only |

| Sport | Events (approx) | Pin Match (approx) |
|-------|----------------:|-------------------:|
| Football | ~200 | ~100 |
| Ice Hockey | ~30 | ~10 |
| Tennis | ~30 | ~10 |
| Basketball | ~20 | ~5 |
| Esports | ~15 | ~5 |
| Other | ~14 | ~5 |
| **TOTAL** | **~309** | **~135** |

> Bundles selections with events per-page.

**Bonus:** Freebet 500 kr / 1x wager / min 1.80

**Oddsboost:** Not implemented (listed on aggregators)

### TODO
- [ ] Spread/total not in RSocket INITIAL_STATE
- [ ] Boost extraction

---

## Interwetten

| Metric | Value |
|--------|-------|
| Platform | Proprietary SSR (browser, headed mode) |
| Retriever | `interwetten` |
| Site | `interwetten.se` |
| Extraction time | ~45s* |
| Events | 350* |
| Odds | 1,050* |
| Ratio | 3.00 |
| Pin matches | **166** |
| Markets | 1x2/ml |
| Normalization | 100% |
| Mode | Headed (Cloudflare protection) |

| Sport | Events | Pin Match | Pinnacle Has | Gap |
|-------|-------:|----------:|-------------:|----:|
| Football | ~200 | ~100 | 688 | -588 |
| Ice Hockey | ~40 | ~20 | 113 | -93 |
| Tennis | ~30 | ~15 | 67 | -52 |
| Basketball | ~25 | ~12 | 78 | -66 |
| Handball | ~15 | ~8 | 13 | -5 |
| Esports | ~10 | ~5 | 36 | -31 |
| Am. Football | ~5 | ~2 | 1 | +1 |
| Cricket | ~5 | ~2 | 13 | -11 |
| Volleyball | ~5 | ~1 | 7 | -6 |
| Baseball | ~5 | ~1 | 0 | — |
| MMA | ~5 | ~0 | 0 | — |
| Rugby | ~5 | ~0 | 0 | — |
| **TOTAL** | **~350** | **~166** | **1,021** | **-855** |

> Expanded from 27 to 155+ leagues across 12 sports. Fixed wrong league IDs. 4 → 166 pin matches (41.5x improvement).

**Bonus:** BonusDep 1,000 kr / 5x wager / min **1.70** (best wagering ratio of all providers!)

**Oddsboost:** Unknown (no boost page found)

### Log
- **2026-02-09**: Expanded from 27 to 155+ leagues, fixed wrong IDs, 12 sports. 4 → 166 pin matches (41.5x).
- **2026-02-08**: Validated — 12 odds / 4 events / 4 pin matches. Works but very limited config.

### TODO
- [ ] Add spread/total market support
- [ ] Best bonus in the system (1,000 kr / 5x / 1.70) — maximizing coverage is extremely valuable
- [ ] Further league expansion possible (some Pinnacle events still unmatched)

---

## Coolbet

| Metric | Value |
|--------|-------|
| Platform | GAN/Coolbet (browser + CDP required) |
| Retriever | `coolbet` |
| Site | `coolbet.com` |
| Extraction time | ~30s* |
| Events | ~81* |
| Odds | ~187* |
| Ratio | 2.31 |
| Pin matches | **—** (not validated) |
| Markets | 1x2/ml/spread/total |
| Normalization | 100% |
| Mode | CDP only (`chrome --remote-debugging-port=9222`) |

> Imperva/Incapsula blocks ALL Playwright-launched browsers. After ~5 failed attempts, IP gets hard-blocked.
> Only works via CDP connection to user's real Chrome.

**Category IDs:** Football=62, Basketball=77, Tennis=72, Ice Hockey=85, AmFoot=58, Baseball=96, MMA=20491, Esports=65035, Handball=68

**API Architecture:**
1. Categories: `GET /s/sbgate/sports/fo-category/?categoryId={id}`
2. Odds: `POST /s/sb-odds/odds/current/fo-line/` with `{"marketIds": [[id1], [id2], ...]}`
3. Odds format: values > 100 are milliodds (divide by 1000)

**Bonus:** BonusDep 1,000 kr / 6x wager / min **1.50** (second-best min odds)

**Oddsboost:** Has `/sv/oddsboost` page but blocked by Imperva

### Log
- **2026-02-08**: Extractor works but requires CDP connection. Previously validated: ~81 events, ratio 2.31.

### TODO
- [ ] **HIGH**: Validate with CDP connection to get pin match numbers
- [ ] Great bonus (1,000 kr / 6x / 1.50) — worth the CDP hassle
- [ ] Oddsboost page exists but blocked by Imperva
- [ ] Category limit increased 200->500

---

## Tipwin

| Metric | Value |
|--------|-------|
| Platform | Tipwin SPA (browser API interception) |
| Retriever | `tipwin` |
| Site | `tipwin.se` |
| API | `api-web.tipwin.se/v2/{agencyId}/offer/data` (agency 100683) |
| Extraction time | **~58s** |
| Events | 1,077 |
| Odds | 3,460 |
| Ratio | 3.21 |
| Pin matches | **390** (36.2%) |
| Markets | 1x2/total/spread |
| Normalization | 100% |

| Sport | Events | Pin Match | Pinnacle Has | Gap |
|-------|-------:|----------:|-------------:|----:|
| Football | ~500 | ~200 | 688 | -488 |
| Ice Hockey | ~120 | ~50 | 113 | -63 |
| Tennis | ~100 | ~40 | 67 | -27 |
| Basketball | ~80 | ~30 | 78 | -48 |
| Handball | ~40 | ~15 | 13 | +2 |
| Esports | ~30 | ~10 | 36 | -26 |
| Am. Football | ~20 | ~10 | 1 | +9 |
| Table Tennis | ~50 | 0 | 0 | — |
| Other | ~137 | ~35 | — | — |
| **TOTAL** | **1,077** | **390** | **1,021** | **-631** |

> Optimized pagination: direct `?page=N` URL navigation instead of button clicking. 420s → 58s (7.2x faster).
> Per-sport URLs don't filter — API returns all sports regardless. Pagination covers ~69 pages.

**Technical details:**
- `bettingTypes[id].abrv`: "3way"=1x2, "over-under"=total, "handicap-hcp"=spread
- Outcome `tip`: "1"=home, "X"=draw, "2"=away, "+"=over, "-"=under
- Point values in market's `key` dict
- Cookie consent: click "Acceptera"/"Accept"
- `_session_ready` flag tracks initial page load
- Pagination: `?page=N` direct navigation (not button clicking)

**Bonus:** BonusDep 1,000 kr / 7x wager / min 1.80

**Oddsboost:** Unknown (no boost page found)

### Log
- **2026-02-09**: Optimized pagination 420s→58s (7x faster). All sports now extracted. 72 → 390 pin matches (5.4x).
- **2026-02-07**: 148 events, 72 pin, football only. 107-page pagination ~7min.

### TODO
- [ ] European handicap -> Asian handicap conversion for spread markets
- [ ] Match rate 36.2% — investigate matching failures

---

## Disabled Providers

### Spelklubben

| Metric | Value |
|--------|-------|
| Platform | Was Gecko V2 / OBG, migrated to custom Next.js + BETBY iframe |
| Status | **DISABLED** |
| Reason | No `/api/sb/` endpoints after migration |
| Bonus | BonusDep 500 kr / 15x wager / min 1.90 |
| Priority | None (bad bonus, infeasible extraction) |

---

## Priority Roadmap

### Completed

| Task | Impact |
|------|--------|
| Gecko V2 rewrite | 0 -> 402 pin matches, 4 providers |
| Gecko V2 date fix + dynamic categories | 402 -> 686 pin matches (Betsson) |
| Bethard -> Gecko V2 | 19 -> 341 pin matches |
| Interwetten league expansion | 4 -> 166 pin matches (41.5x), 12 sports |
| Vbet validation | 945 events, 667 pin, multi-market confirmed |
| Tipwin speed optimization | 420s -> 58s (7x), 72 -> 390 pin (5.4x) |
| 10bet market expansion | 75 -> 235 pin (3.2x), 11 market codes, 14 sports |
| ComeOn WS fix | 14 -> 93 pin (6.6x) |
| Altenar pagination audit | No pagination needed |
| CampoBet + Swiper | First data: 448 pin each |

### High Priority

| Task | Provider(s) | Expected Impact | Effort |
|------|-------------|-----------------|--------|
| Snabbare match rate improvement | snabbare | 172 -> 300+ pin matches (29.5% -> higher) | Medium |
| Kambi ice hockey/basketball slugs | 8 Kambi | +100 pin matches x 8 providers | Medium |
| Altenar boost API | 6 Altenar | Boost data for 6 providers | Medium |

### Medium Priority

| Task | Provider(s) | Expected Impact | Effort |
|------|-------------|-----------------|--------|
| Spectate boost extraction | mrgreen, 888sport | Boost data for 2 providers | Medium |
| ComeOn/Hajper boost extraction | comeon, hajper | Boost data (5/5 on aggregators) | Medium |
| Coolbet CDP validation | coolbet | Pin match numbers + boost | Low |
| Interwetten spread/total | interwetten | More market types | Medium |

### Low Priority

| Task | Provider(s) | Expected Impact | Effort |
|------|-------------|-----------------|--------|
| Snabbare spread/total | snabbare | More market types | Medium |
| Kambi group caching | 8 Kambi | Reduce extraction time | Low |
| ComeOn/Hajper spread/total | comeon, hajper | More market types | High |
| Snabbare boost | snabbare | Boost data (4/5) | Medium |
