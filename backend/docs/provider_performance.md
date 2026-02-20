# Provider Performance Tracker

> Last updated: 2026-02-20 14:13 (post browser_soft run)

## Pipeline Health

| Metric | Value |
|--------|-------|
| Pinnacle baseline | 2,155 events / 15,234 odds |
| Soft providers | 15 canonical (31 incl. aliases) |
| Cross-provider match rate | ~86% |
| Value opportunities | 1,653 (1,730 incl. aliases) |
| Dutch opportunities | 584 (2,103 incl. aliases) |
| Specials/boosts | 602 scraped, 0 +EV this run |

### Pinnacle Sport Baseline

| Sport | Events | Odds |
|-------|-------:|-----:|
| Football | 1,169 | 9,574 |
| Basketball | 385 | 3,060 |
| Ice Hockey | 216 | 592 |
| Esports | 97 | 434 |
| Handball | 94 | 594 |
| Tennis | 88 | 484 |
| Volleyball | 34 | 210 |
| MMA | 23 | 74 |
| Boxing | 18 | 56 |
| Rugby | 13 | 90 |
| Darts | 9 | 36 |
| Cricket | 5 | 10 |
| Snooker | 2 | 8 |
| Curling | 2 | 12 |

---

## Extraction Tier Performance

### Sharp (~15s)

| Provider | Events | Odds | Time | Status |
|----------|-------:|-----:|-----:|--------|
| Pinnacle | 1,942 | 11,411 | 6s | OK |
| Polymarket | 565 | 837 | <1s | OK |

### API Soft (~102s)

| Provider | Platform | Events | Odds | ML | Spr | Tot | Time | Status |
|----------|----------|-------:|-----:|---:|----:|----:|-----:|--------|
| betsson | Gecko V2 | 2,390 | 10,009 | 4,003 | 3,448 | 2,558 | 74s | OK |
| bethard | Gecko V2 | 2,386 | 9,997 | 3,997 | 3,450 | 2,550 | 70s | OK |
| betinia | Altenar | 2,220 | 6,400 | 3,428 | 534 | 2,438 | 8s | OK |
| vbet | BetConstruct | 1,562 | 50,774 | 4,010 | 25,440 | 21,324 | 38s | OK |
| unibet | Kambi | 1,205 | 43,591 | 2,273 | 12,402 | 28,916 | 65s | OK |
| dbet | Altenar | 1,205 | 3,890 | 1,756 | 1,040 | 1,094 | 19s | OK |

Aliases (shared odds): nordicbet=betsson, spelklubben=bethard, campobet/swiper/lodur/quickcasino=betinia, leovegas/expekt/betmgm/speedybet/x3000/goldenbull/1x2=unibet

### Browser Soft (~593s)

| Provider | Platform | Events | Odds | ML | Spr | Tot | Time | Status |
|----------|----------|-------:|-----:|---:|----:|----:|-----:|--------|
| tipwin | Tipwin SPA | 1,301 | 7,037 | 2,697 | 2,604 | 1,736 | 182s | OK |
| 888sport | Spectate | 1,197 | 3,123 | 2,765 | 186 | 172 | 29s | OK |
| interwetten | Proprietary | 863 | 1,681 | 1,261 | 186 | 234 | 261s | OK |
| lyllo | ComeOn | 346 | 572 | 516 | 0 | 56 | 172s | OK |
| coolbet | GAN/Camoufox | 334 | 1,447 | 318 | 328 | 801 | 159s | OK |
| hajper | ComeOn | 302 | 496 | 440 | 0 | 56 | 189s | OK |
| snabbare | Sportradar WS | 239 | 538 | 530 | 0 | 8 | 393s | OK |
| 10bet | Playtech DOM | 187 | 755 | 407 | 54 | 294 | 485s | OK |
| comeon | ComeOn | 94 | 103 | 91 | 0 | 12 | 97s | OK |

Alias: mrgreen=888sport

---

## Known Platform Limitations

These are **not code bugs** — they are bookmaker/platform constraints that cannot be fixed with code changes.

| Provider | Limitation | Impact |
|----------|-----------|--------|
| 888sport | Spectate API returns spread/total only for basketball + ice_hockey | Football (largest sport) has 0 spread/total |
| lyllo/hajper/comeon | ComeOn WS only delivers 1x2/ML/total at sport level | 0 spread across all sports |
| snabbare | Sportradar WS delivers only 1x2/ML at league level | 0 spread, near-0 total |
| tipwin | Only football + ice_hockey events on site | Other sports return 0 |
| 10bet | Football competition pages don't show HCMR (spread) | Football always has 0 spread |
| Altenar (6) | API doesn't return spread (typeId 16) for football | Football spread = 0 |

---

## Active Issues & Watchlist

### Snabbare football — recovered but slow (200s)

**Status:** RECOVERED (was 4 consecutive timeouts, now 164 events)
**Root cause:** DOM sidebar stopped rendering for football (heaviest sport, 60+ leagues).
**Fix applied (2026-02-20):** `wait_until="load"` → `"domcontentloaded"` everywhere. Increased league link selector timeout to 8s for football. Added REST API league discovery fallback + direct navigation for API-discovered leagues.
**Current state:** Football works via API fallback + direct navigation (200s). Slower than SPA click approach (was 55s when DOM sidebar worked). Monitor if snabbare.com fixes their React rendering — will auto-speed-up.

### 10bet — improved but still slow (485s, 0.4 ev/s)

**Status:** IMPROVED (53 → 187 events, basketball recovered)
**Fix applied (2026-02-20):** `ta-EventListItem` selector timeout 6s → 10s, odds wait 500ms → 2s, parallel tabs 6 → 4, removed dead sports (american_football, baseball). Competition discovery timeout 8s → 10s.
**Current state:** Football + basketball work. Other sports still inconsistent (0 events for ice_hockey, handball, tennis, mma, esports this run). These DO work sometimes — it's DOM rendering timing variance on 10bet's Playtech widget.
**Time wasters:** 208s spent on 5 sports that returned 0 events.

### Comeon — dropped (187 → 94 events)

**Status:** MONITOR — not related to our changes, likely site-side variability. ComeOn event counts fluctuate heavily (94-302 range).

### dbet boosts — intermittent 0

**Status:** MONITOR — dbet Altenar boost scraper returned 0 boosts this run (was 109 last run). Intermittent.

---

## Timing Bottlenecks

### API Soft (bottleneck: betsson 74s)
Well-balanced. No action needed.

### Browser Soft (bottleneck: 10bet 485s = 82% wall-clock)

| Provider | Time | Events | Efficiency | Action |
|----------|-----:|-------:|-----------|--------|
| 10bet | 485s | 187 | 0.4 ev/s | Non-football still inconsistent |
| snabbare | 393s | 239 | 0.6 ev/s | Football API fallback slow (200s) |
| interwetten | 261s | 863 | 3.3 ev/s | Above 300s threshold but good yield |
| hajper | 189s | 302 | 1.6 ev/s | OK |
| tipwin | 182s | 1,301 | 7.1 ev/s | OK — massive surge this run |
| lyllo | 172s | 346 | 2.0 ev/s | OK |
| coolbet | 159s | 334 | 2.1 ev/s | OK |
| comeon | 97s | 94 | 1.0 ev/s | Low events but fast |
| 888sport | 29s | 1,197 | 41.3 ev/s | Fastest browser provider |

---

## Changelog

### 2026-02-20

**Snabbare football recovery + 10bet improvement**

Fixes applied:
- **snabbare.py**: All `page.goto()` switched from `wait_until="load"` to `"domcontentloaded"` (6 locations). League link selector timeout increased 3s → 8s for football, 5s for others. Added REST API league discovery fallback when DOM sidebar is empty. Added direct `page.goto()` navigation for API-discovered leagues (when DOM click fails).
- **tenbet.py**: `ta-EventListItem` selector timeout 6s → 10s. Odds selector timeout 500ms → 2s. Competition discovery timeout 8s → 10s (fallback 2s → 3s). Parallel tabs 6 → 4. Competition discovery `page.goto` switched to `domcontentloaded`.
- **providers.yaml**: Removed `american_football` and `baseball` from 10bet (0 events ever).

Results:
| Metric | Before (12:49) | After (14:13) | Delta |
|--------|------:|------:|-------|
| snabbare total | 137 ev | 239 ev | +75% |
| snabbare football | 0 ev (timeout) | 164 ev | **recovered** |
| 10bet total | 53 ev | 187 ev | +253% |
| 10bet basketball | 0 ev | 35 ev | **recovered** |
| tipwin total | 205 ev | 1,301 ev | +535% (site-side) |
| Value opportunities | 476 | 610 | +28% |
| Dutch opportunities | 464 | 584 | +26% |

### 2026-02-15

(See git history for full 2026-02-08 to 2026-02-15 changelog — consolidated from initial provider buildout through boost extraction implementation)

Summary of major milestones:
- All 31 providers operational (2 sharp + 29 soft)
- Boost scraping: 795 boosts from 14 providers
- Kambi event caching saves ~350 HTTP requests/run
- Coolbet cracked with Camoufox (anti-detect Firefox)
- ComeOn Group date-based extraction rewrite
- Interwetten 15x improvement (concurrent tabs)
- 10bet 10x improvement (headless mode)
- Snabbare SPA React Router link-clicking fix
- Per-provider sport_timeout support in orchestrator
