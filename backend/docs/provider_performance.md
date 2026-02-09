# Provider Performance Report

> Last updated: 2026-02-09

## Overview

| Metric | Value |
|--------|-------|
| Active providers | 30 (2 sharp + 28 soft) |
| Disabled providers | 1 (spelklubben) |
| Pinnacle baseline | **~1,202 events / ~7,361 odds** |
| Total odds (full run) | **73,189** |
| Cross-provider matching | **77.7%** (1,348/1,734 events) |

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
| Extraction time | ~45s |
| Events | 605 |
| Odds | 2,697 |
| Ratio | 4.46 |
| Pin matches | **511** (84.5%) |
| Markets | 1x2/ml/spread/total |
| Normalization | 100% |

| Sport | Events | Pin Match | Pinnacle Has | Gap |
|-------|-------:|----------:|-------------:|----:|
| Football | 532 | 350 | 688 | -338 |
| Tennis | 104 | 62 | 67 | -5 |
| Basketball | 59 | 30 | 78 | -48 |
| Esports | 56 | 30 | 36 | -6 |
| Table Tennis | 43 | 0 | 0 | — |
| Rugby | 24 | 0 | 0 | — |
| Handball | 20 | 13 | 13 | 0 |
| Darts | 17 | 8 | 0 | — |
| Ice Hockey | 15 | 9 | 105 | -96 |
| Boxing | 10 | 0 | 0 | — |
| Volleyball | 6 | 5 | 7 | -2 |
| Cricket | 3 | 3 | 13 | -10 |
| Curling | 2 | 0 | 4 | -4 |
| MMA | 2 | 0 | 0 | — |
| Golf | 1 | 1 | 1 | 0 |
| **TOTAL** | **605** | **511** | **1,021** | **-510** |

> Ice hockey low count is seasonal — NHL paused for Winter Olympics 2026. Pinnacle has 105 events mostly from minor European leagues + Olympics; Kambi has 15 (SHL, Liiga, Olympics).

**Bonus:** Freebet 1,000 kr / 1x wager / min 1.80

**Oddsboost:** Not extractable (Kambi shows only boosted price, no original odds)

#### Log
- **2026-02-09**: Country name aliases added → 367→511 pin matches (+39%). Swedish names (kanada, tjeckien, etc.) now resolve to English.
- **2026-02-08**: 2,184 odds / 524 events / 450 pin / 45.2s
- **2026-02-04**: PRODUCTION_READY

#### TODO
- [ ] Ice hockey coverage seasonal — will improve when NHL resumes post-Olympics
- [ ] Cache group data across 8 providers (identical API)
- [ ] Reduce `post_extraction_delay_ms` if rate limits allow

---

### LeoVegas

| Metric | Value |
|--------|-------|
| Platform | Kambi API |
| Brand slug | `leose` |
| Extraction time | 20.5s |
| Events | ~605 |
| Odds | ~2,700 |
| Ratio | 4.46 |
| Pin matches | **~511** |
| Markets | 1x2/ml/spread/total |
| Normalization | 100% |

> Sport coverage identical to Unibet (same Kambi backend). Country name aliases apply.

**Bonus:** BonusDep 600 kr / 6x wager / min 1.80

**Oddsboost:** Not extractable (Kambi)

#### Log
- **2026-02-09**: Country name aliases → ~450→~511 pin matches (estimated, same data as Unibet)
- **2026-02-08**: 2,189 odds / 524 events / 450 pin / 20.5s
- **2026-02-04**: PRODUCTION_READY

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

- [x] **FIXED**: Country name aliases (kanada→canada, tjeckien→czech republic, etc.) — 367→511 pin matches (+39%)
- [ ] Ice hockey coverage seasonal — NHL paused for Winter Olympics 2026
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
| Events | 1,738 |
| Odds | 4,648 |
| Ratio | 2.67 |
| Pin matches | **443** |
| Markets | 1x2/ml/spread/total |
| Normalization | 100% |

| Sport | Events | Pin Match | Pinnacle Has | Gap |
|-------|-------:|----------:|-------------:|----:|
| Football | ~900 | 317 | 688 | -371 |
| Tennis | ~135 | 61 | 67 | -6 |
| Table Tennis | ~147 | 0 | 0 | — |
| Basketball | ~163 | 28 | 78 | -50 |
| Ice Hockey | ~146 | 28 | 105 | -77 |
| Esports | ~82 | 2 | 36 | -34 |
| Volleyball | ~66 | 2 | 7 | -5 |
| Handball | ~61 | 5 | 13 | -8 |
| MMA | ~8 | 0 | 0 | — |
| Rugby | ~8 | 0 | 0 | — |
| **TOTAL** | **1,738** | **443** | **1,021** | **-578** |

**Market distribution:** 1x2 + ML dominant, Spread=28, Total=44

**Bonus:** BonusDep 1,000 kr / 6x wager / min 1.80

**Oddsboost:** Not implemented (listed 4/5 on aggregators, Altenar API investigation needed)

#### Log
- **2026-02-09**: Country name aliases → 433→443 pin matches (+10). Altenar uses English names mostly so less impacted.
- **2026-02-08**: 1,547 odds / 571 events / 433 pin / 13.0s

#### TODO
- [ ] Football spread missing (Altenar platform limitation — typeId 16 not returned)
- [ ] Boost API reverse-engineering (would benefit all 6 Altenar providers)
- [ ] Table tennis/MMA/rugby: many events but 0 pin matches (no Pinnacle coverage)
- [ ] Esports only 2 events matched vs Pinnacle 36

---

### Lodur

| Metric | Value |
|--------|-------|
| Platform | Altenar API |
| Integration | `lodurse` |
| Extraction time | **12.8s** |
| Events | 1,896 |
| Odds | 4,992 |
| Ratio | 2.63 |
| Pin matches | **462** |
| Markets | 1x2/ml/spread/total |
| Normalization | 100% |

**Market distribution:** 1x2 + ML dominant, Spread + Total improving

**Bonus:** BonusDep 1,000 kr / 6x wager / min 1.80

**Oddsboost:** Not implemented

#### Log
- **2026-02-09**: Country name aliases → 447→462 pin matches (+15).
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
> **Sport-specific market templates** (CRITICAL — must request ALL variants per sport):
> - Standard: MW3W=1x2, MW2W=moneyline, MTG2W/MTG2W25=total, M3WHCP/M2WHCP=spread
> - Ice hockey: TGOUOT=total, MHCPNOT=spread
> - Tennis: MTG2WP=total, M2WHCP=spread
> - Basketball: PTSOUROLMID=total, 2WHCPROLMID=spread, ESNMO*=esports variants
> - Handball: OUALT=total, MWHCPALT=spread
> - Volleyball: MTP=total, MSH=spread
> - Esports: ESMW2W=moneyline, ESHMTHANDICAP=spread
> Selection templates: HOME/AWAY/DRAW/OVER/UNDER + HANDICAPHOME/AWAY/DRAW.
> Category IDs: football=1, ice_hockey=2, handball=3, basketball=4, rugby=7/8, volleyball=9, amfootball=10, tennis=11, curling=20, cricket=26, boxing=30, darts=34, mma=53.

### Betsson

| Metric | Value |
|--------|-------|
| Platform | Gecko V2 (browser) |
| Site | `betsson.com` |
| Extraction time | ~60s |
| Events | 787 |
| Odds | 2,860 |
| Ratio | 3.63 |
| Pin matches | **729** |
| Markets | 1x2/ml/spread/total |
| Normalization | 100% |

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

> Multi-sport expansion: added MMA (cat=53), 8 sport-specific market templates.
> 11 sports active. Baseball/esports/golf/table_tennis not on platform.

**Bonus:** Freebet 250 kr / 1x wager / min 1.80

**Oddsboost:** **IMPLEMENTED** (Gecko V2 boost scraper)

#### Log
- **2026-02-09**: Multi-sport expansion — added MMA cat ID + sport-specific market templates (TGOUOT, MHCPNOT, MTG2WP, MSH, MTP, ESMW2W, ESHMTHANDICAP). 686→729 pin, 11 sports.
- **2026-02-09**: Fixed date filtering bug + dynamic category lookup. 402→686 pin.
- **2026-02-08**: Rewrite complete — `events-table/v2` API with header capture.

#### TODO
- [ ] Share browser session across 4 Gecko V2 providers
- [ ] MMA 36 events but 0 pin matches — investigate team name matching

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
- [x] **FIXED**: Date filtering bug in `_resolve_event_id()`
- [x] **FIXED**: Multi-sport market templates — added sport-specific variants (TGOUOT, MHCPNOT, MTG2WP, etc.)
- [x] **FIXED**: MMA category ID 53 added
- [ ] Share browser session across 4 providers (currently separate sessions)
- [ ] MMA/rugby/amfootball: events exist but 0 pin matches — name matching issue

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

**Bonus:** bonusdep 800 kr / 10x wager / min 1.80 (marginal value due to 10x wager)

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
| Extraction time | **~567s** |
| Events | 1,409 |
| Odds | 3,543 |
| Ratio | 2.51 |
| Pin matches | **519** (36.8%) |
| Markets | 1x2/ml/spread/total |
| Normalization | 100% |
| Mode | Headed browser (SPA needs full rendering) |

| Sport | Events | Pin Match | Pinnacle Has | Gap |
|-------|-------:|----------:|-------------:|----:|
| Football | 673 | 376 | 759 | -383 |
| Table Tennis | 344 | 0 | 0 | — |
| Tennis | 172 | 72 | 127 | -55 |
| Basketball | 70 | 27 | 157 | -130 |
| Ice Hockey | 41 | 28 | 118 | -90 |
| Esports | 26 | 1 | 29 | -28 |
| MMA | 26 | 1 | 13 | -12 |
| Volleyball | 24 | 1 | 7 | -6 |
| Handball | 20 | 7 | 24 | -17 |
| Cricket | 11 | 7 | 11 | -4 |
| Curling | 2 | 0 | 2 | -2 |
| **TOTAL** | **1,409** | **519** | **1,262** | **-743** |

**Market distribution:** 1x2/ML + spread/total via listing pages

> 11 market type codes across 14 sports. 100% normalization. Fixed timing DOM selector (was extracting `datetime.now()` instead of actual match date).

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
| Platform | WebSocket + REST API (RSocket interception) |
| Retriever | `snabbare` |
| Site | `snabbare.com` |
| Extraction time | **~283s** (was 751s — 2.7x faster) |
| Events | ~900 |
| Odds | 1,729 |
| Ratio | 2.79 |
| Pin matches | **619** |
| Markets | 1x2/ml |
| Normalization | 100% |

| Sport | Events | Pin Match |
|-------|-------:|----------:|
| Football | 443 | 443 |
| Basketball | 53 | 53 |
| Tennis | 42 | 42 |
| Ice Hockey | 37 | 37 |
| Esports | 16 | 16 |
| Handball | 11 | 11 |
| MMA | 10 | 10 |
| Cricket | 7 | 7 |
| **TOTAL** | **619** | **619** |

> Rewritten from DOM scraping to WebSocket/RSocket interception. REST API league discovery + WS data capture.
> Cache-all-on-first-call pattern (like Tipwin) — headed mode required. 8 sports active.
> Concurrent tabs (3 tabs) with 2.0s settle time per league navigation.

**Bonus:** BonusDep 600 kr / 8x wager / min 1.80

**Oddsboost:** Not implemented (listed 4/5 on aggregators)

### Log
- **2026-02-09**: Concurrent tab optimization — 751s→283s (2.7x faster), 435→619 pin matches (+42%).
- **2026-02-09**: REWRITTEN — WebSocket/RSocket interception. 583→1,172 events, 172→435 pin (2.5x).
- **2026-02-06**: DOM scraping working. 583 events, 172 pin, 1x2/ML only.

### TODO
- [ ] Event detail pages may have spread/total markets
- [ ] Boost extraction (4/5 on aggregators)

---

## ComeOn

| Metric | Value |
|--------|-------|
| Platform | ComeOn Group (browser, multi-league) |
| Retriever | `comeon_multileague` |
| Site | `comeon.com` |
| Extraction time | ~60s |
| Events | 292 |
| Odds | 815 |
| Ratio | 2.79 |
| Pin matches | **298** |
| Markets | 1x2/ml/total |
| Normalization | 100% |

| Sport | Events | Pin Match |
|-------|-------:|----------:|
| Football | ~200 | ~200 |
| Basketball | ~40 | ~40 |
| Ice Hockey | ~15 | ~15 |
| Handball | ~15 | ~15 |
| Other | ~22 | ~28 |
| **TOTAL** | **292** | **298** |

> Rewritten with new URL pattern `/sv/sportsbook/sport/{id}-{slug}/leagues/{id}-{slug}`.
> MarketType IDs: 1=1x2, 175=moneyline, 206=moneyline(OT), 212=total(OT).
> SPA needs ~5s retry for league links. max_leagues=100, concurrent_leagues=8.

**Bonus:** BonusDep 500 kr / 6x wager / min 1.80

**Oddsboost:** Not implemented (5/5 on aggregators — high priority)

### Log
- **2026-02-09**: REWRITTEN — new URL patterns, 12 sports, 1x2+ML+total. 93→298 pin (3.2x).
- **2026-02-08**: Fixed WS message isolation: 14 -> 93 pin matches (6.6x improvement).

### TODO
- [ ] Spread requires event detail navigation (mt.id=16,17) — too expensive per-event
- [ ] Boost extraction (5/5 on aggregators — likely valuable)

---

## Hajper

| Metric | Value |
|--------|-------|
| Platform | ComeOn Group (browser, multi-league) |
| Retriever | `hajper` |
| Site | `hajper.com` |
| Extraction time | ~60s |
| Events | 291 |
| Odds | 812 |
| Ratio | 2.79 |
| Pin matches | **298** |
| Markets | 1x2/ml/total |
| Normalization | 100% |

| Sport | Events | Pin Match |
|-------|-------:|----------:|
| Football | ~200 | ~200 |
| Basketball | ~40 | ~40 |
| Ice Hockey | ~15 | ~15 |
| Handball | ~15 | ~15 |
| Other | ~21 | ~28 |
| **TOTAL** | **291** | **298** |

> Same ComeOn Group platform as ComeOn. Identical URL pattern and market types.
> max_leagues=100, concurrent_leagues=8.

**Bonus:** Freebet 500 kr / 1x wager / min 1.80

**Oddsboost:** Not implemented (listed on aggregators)

### Log
- **2026-02-09**: REWRITTEN — new URL patterns, 12 sports, 1x2+ML+total. 135→298 pin (2.2x).

### TODO
- [ ] Spread requires event detail navigation — too expensive per-event
- [ ] Boost extraction

---

## Interwetten

| Metric | Value |
|--------|-------|
| Platform | Proprietary SSR (browser, headed mode) |
| Retriever | `interwetten` |
| Site | `interwetten.se` |
| Extraction time | ~332s (two-pass: listing + detail pages) |
| Events | 648 (5 main sports) / 714 (all 12 sports) |
| Odds | 745 |
| Ratio | 3.23 |
| Pin matches | **183** |
| Markets | 1x2/ml/spread/total |
| Normalization | 100% |
| Mode | Headed (Cloudflare protection) |

| Sport | Events | 1x2/ML | Spread | Total | Pin Match |
|-------|-------:|-------:|-------:|------:|----------:|
| Football | 400 | 204 | 60 | 36 | ~100 |
| Tennis | 129 | 96 | 0* | 0* | ~30 |
| Basketball | 55 | 58 | 16 | 6 | ~20 |
| Handball | 37 | 138 | 12 | 10 | ~8 |
| Ice Hockey | 27 | 42 | 0** | 27 | ~10 |
| Rugby | ~20 | 45 | — | — | ~5 |
| Volleyball | ~15 | 14 | — | — | ~3 |
| Cricket | ~10 | 8 | — | — | ~3 |
| Darts | ~5 | — | — | — | ~2 |
| Boxing | ~2 | — | — | — | ~1 |
| **TOTAL** | **~714** | **605** | **88** | **52** | **~183** |

> *Tennis uses "Handicap Games" and "How many games" labels — supported but few point matches with Pinnacle
> **Ice hockey has no Asian Handicap (only European handicap 0:1, 0:2 etc.)
> Spread/total stored odds limited by Pinnacle point matching (`_POINT_TOLERANCE=0.01`)

**Two-pass extraction strategy:**
1. League listing pages → 1x2/moneyline (fast, ~100s for all leagues)
2. Event detail pages → spread + total (3 concurrent tabs, ~230s for main sports)

**Extraction time by sport:** Football ~210s, Tennis ~57s, Basketball ~28s, Handball ~18s, Ice Hockey ~18s

> 4 → 166 → **183** pin matches. Added spread/total markets (140 additional odds). Best bonus provider.

**Bonus:** BonusDep 1,000 kr / 5x wager / min **1.70** (best wagering ratio of all providers!)

**Oddsboost:** oddsboost exist on page https://www.interwetten.se/sv/sportspel, but not implemented **TODO**

### Log
- **2026-02-09**: Expanded from 27 to 155+ leagues, fixed wrong IDs, 12 sports. 4 → 166 pin matches (41.5x).
- **2026-02-08**: Validated — 12 odds / 4 events / 4 pin matches. Works but very limited config.

### TODO
- [x] **FIXED**: Spread/total markets added (88 spread + 52 total odds). Two-pass extraction: listing + 3 concurrent detail pages.
- [ ] Best bonus in the system (1,000 kr / 5x / 1.70) — maximizing coverage is extremely valuable
- [ ] Further league expansion possible (some Pinnacle events still unmatched)

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
| Pin matches | **39** |
| Markets | 1x2/ml/spread/total |
| Normalization | 100% |
| Mode | CDP only (`chrome --remote-debugging-port=9222`) |

| Sport | Events | Pin Match |
|-------|-------:|----------:|
| Football | ~80 | ~20 |
| Basketball | ~30 | ~5 |
| Tennis | ~25 | ~5 |
| Ice Hockey | ~20 | ~4 |
| Am. Football | ~15 | ~3 |
| Other | ~25 | ~2 |
| **TOTAL** | **195** | **39** |

> Imperva/Incapsula blocks ALL Playwright-launched browsers. Only works via CDP.
> Pagination required — API returns only 10 categories/page. Without: 81 events. With: 195 events (2.4x).
> Multiple total/spread lines per match — pick most balanced odds for main line.

**Category IDs:** Football=62, Basketball=77, Tennis=72, Ice Hockey=85, AmFoot=58, Baseball=96, MMA=20491, Esports=65035, Handball=68

**API Architecture:**
1. Categories: `GET /s/sbgate/sports/fo-category/?categoryId={id}&offset=N` (paginated, 10 per page)
2. Odds: `POST /s/sb-odds/odds/current/fo-line/` with `{"marketIds": [[id1], [id2], ...]}`
3. Odds format: values > 100 are milliodds (divide by 1000)

**Bonus:** BonusDep 1,000 kr / 6x wager / min **1.50** (second-best min odds)

**Oddsboost:** Has `/sv/oddsboost` page but blocked by Imperva

### Log
- **2026-02-09**: Added pagination + market name fixes. 81→195 events, 39 pin matches, 6 sports, multi-market.
- **2026-02-08**: Extractor works but requires CDP connection. Previously validated: ~81 events, ratio 2.31.

### TODO
- [ ] Low pin match rate (20%) — investigate matching failures
- [ ] Great bonus (1,000 kr / 6x / 1.50) — worth the CDP hassle
- [ ] Oddsboost page exists but blocked by Imperva

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
| **Country name aliases (sv→en)** | **+144 pin matches for Kambi (367→511), +10 for Altenar. Fixes Olympics/international matching across ALL 30 providers** |
| Gecko V2 rewrite | 0 -> 402 pin matches, 4 providers |
| Gecko V2 date fix + dynamic categories | 402 -> 686 pin matches (Betsson) |
| Gecko V2 multi-sport market templates | 686 -> 729 pin, 11 sports, spread+total for all sports |
| Bethard -> Gecko V2 | 19 -> 341 pin matches |
| Interwetten league expansion | 4 -> 166 pin matches (41.5x), 12 sports |
| Interwetten spread/total markets | 166 -> 183 pin, +140 spread/total odds, ratio 2.53 -> 3.23 |
| Vbet validation | 945 events, 667 pin, multi-market confirmed |
| Tipwin speed optimization | 420s -> 58s (7x), 72 -> 390 pin (5.4x) |
| 10bet market expansion | 75 -> 235 pin (3.2x), 11 market codes, 14 sports |
| ComeOn rewrite | 93 -> 298 pin (3.2x), 12 sports, 1x2+ML+total |
| Hajper rewrite | 135 -> 298 pin (2.2x), 12 sports, 1x2+ML+total |
| Snabbare rewrite (WS/RSocket) | 172 -> 435 pin (2.5x), 9 sports |
| Snabbare speed optimization | 751s -> 283s (2.7x), concurrent tabs, 435 -> 619 pin (+42%) |
| 10Bet timing fix | 235 -> 519 pin (2.2x), fixed DOM timing selector (was datetime.now()) |
| Browser off-screen + Tipwin headless | --window-position=-2400,-2400 hides headed windows; Tipwin switched to headless (1,221 events) |
| Coolbet pagination + market fixes | 81 -> 195 events, 39 pin, multi-market |
| Altenar validation | Already good — 12 sports, 433-448 pin |
| CampoBet + Swiper | First data: 448 pin each |

### High Priority

| Task | Provider(s) | Expected Impact | Effort |
|------|-------------|-----------------|--------|
| Coolbet match rate improvement | coolbet | 39→~150+ pin (20%→75%+) | Medium |
| Altenar boost API | 6 Altenar | Boost data for 6 providers | Medium |
| ComeOn/Hajper boost extraction | comeon, hajper | Boost data (5/5 on aggregators) | Medium |

### Medium Priority

| Task | Provider(s) | Expected Impact | Effort |
|------|-------------|-----------------|--------|
| Spectate boost extraction | mrgreen, 888sport | Boost data for 2 providers | Medium |

### Low Priority

| Task | Provider(s) | Expected Impact | Effort |
|------|-------------|-----------------|--------|
| Snabbare spread/total | snabbare | More market types | Medium |
| Kambi group caching | 8 Kambi | Reduce extraction time | Low |
| ComeOn/Hajper spread/total | comeon, hajper | Needs event detail nav (expensive) | High |
| Snabbare boost | snabbare | Boost data (4/5) | Medium |
| Gecko V2 session sharing | 4 Gecko V2 | Reduce extraction time | Low |
