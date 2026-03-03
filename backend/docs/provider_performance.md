# Provider Performance Tracker

> Last updated: 2026-03-03 (post extraction audit + fixes)

## Pipeline Health

| Metric | Value |
|--------|-------|
| Pinnacle baseline | 1,430 events / 16,928 odds |
| Soft providers | 15 canonical (30 active incl. aliases) |
| Total DB odds | 62,399 (was 106k before fixes) |
| Value opportunities | 1,045 |
| Dutch opportunities | 376 |
| Reverse value | 44 |
| Specials/boosts | 225 scraped, 5 +EV |

### Pinnacle Sport Baseline

| Sport | Events | Odds |
|-------|-------:|-----:|
| Football | 870 | 13,228 |
| Tennis | 185 | 1,322 |
| Ice Hockey | 160 | 1,042 |
| Basketball | 153 | 992 |
| Esports | 39 | 244 |
| Handball | 37 | 214 |
| MMA | 14 | 54 |
| Curling | 8 | 44 |
| Baseball | 3 | 6 |
| Cricket | 1 | 2 |

---

## Extraction Tier Performance

### Sharp (~22s)

| Provider | Events | Odds | Time | Status |
|----------|-------:|-----:|-----:|--------|
| Pinnacle | 1,430 | 16,928 | 11s | OK |
| Polymarket | 465 | 640 | 11s | OK |

### API Soft (~180s)

| Provider | Platform | Events | Odds | ML | Spr | Tot | Time | Status |
|----------|----------|-------:|-----:|---:|----:|----:|-----:|--------|
| betinia | Altenar | 1,836 | 5,377 | 2,927 | 472 | 1,978 | 9s | OK |
| bethard | Gecko V2 | 1,715 | 7,601 | 3,007 | 2,664 | 1,930 | 180s | OK |
| betsson | Gecko V2 | 1,699 | 7,523 | 2,978 | 2,635 | 1,910 | 126s | OK |
| dbet | Altenar | 1,647 | 5,594 | 2,994 | 700 | 1,900 | 9s | OK |
| vbet | BetConstruct | 1,177 | 5,783 | 3,187 | 1,350 | 1,246 | 22s | OK (**FIXED** — was 38k) |
| unibet | Kambi | 954 | 5,021 | 1,752 | 1,548 | 1,721 | 18s | OK (**FIXED** — was 37k) |

Aliases (shared odds): nordicbet=betsson, spelklubben=bethard, campobet/swiper/lodur/quickcasino=betinia, leovegas/expekt/betmgm/speedybet/x3000/goldenbull/1x2=unibet

### Browser Soft (~584s)

| Provider | Platform | Events | Odds | ML | Spr | Tot | Time | Status |
|----------|----------|-------:|-----:|---:|----:|----:|-----:|--------|
| 888sport | Spectate | 1,077 | 2,399 | 2,169 | 120 | 110 | 56s | OK |
| interwetten | Proprietary | 437 | 830 | 420 | 158 | 252 | 542s | SLOW |
| snabbare | Sportradar WS | 328 | 592 | 592 | 0 | 0 | 401s | OK |
| coolbet | GAN/Camoufox | 243 | 2,616 | 352 | 669 | 1,595 | 305s | OK |
| tipwin | Tipwin SPA | 107 | 552 | 207 | 207 | 138 | 126s | OK (**FIXED** — was 0) |
| comeon | ComeOn | 76 | 95 | 91 | 0 | 4 | 204s | OK |
| lyllo | ComeOn | 74 | 95 | 91 | 0 | 4 | 228s | OK |
| 10bet | Playtech DOM | 62 | 145 | 97 | 4 | 44 | 527s | LOW (was 633) |
| hajper | ComeOn | 50 | 59 | 55 | 0 | 4 | 198s | OK |

Alias: mrgreen=888sport

---

## Known Platform Limitations

These are **not code bugs** — they are bookmaker/platform constraints that cannot be fixed with code changes.

| Provider | Limitation | Impact |
|----------|-----------|--------|
| 888sport | Spectate API returns spread/total only for basketball + ice_hockey | Football (largest sport) has 0 spread/total |
| lyllo/hajper/comeon | ComeOn WS only delivers 1x2/ML at sport level | 0 spread, near-0 total across all sports |
| snabbare | Sportradar WS delivers only 1x2/ML at league level | 0 spread, near-0 total |
| tipwin | Only football + ice_hockey events on site | Other sports return 0 |
| 10bet | Football competition pages don't show HCMR (spread) | Football always has 0 spread |
| Altenar (6) | API doesn't return spread (typeId 16) for football | Football spread = 0 |

---

## Active Issues & Watchlist

### Unibet/Kambi — FIXED & VALIDATED

**Status:** DONE — 37,220 → 5,021 odds (-87%)
**Root cause:** Kambi API returns alternate spread/total lines; we weren't filtering to main lines.
**Fix applied (2026-03-03):** Added `MAIN_LINE` tag filter for betOfferType 1/6/7 (spread/total). Added prop market exclusions (team totals, corners, cards, shots, fouls, offsides).

### VBet — FIXED & VALIDATED

**Status:** DONE — 38,118 → 5,783 odds (-85%)
**Root cause:** BetConstruct Swarm API returns all alternate lines; we kept all of them.
**Fix applied (2026-03-03):** Added main line filtering using `order` field — keeps only the spread/total candidate with lowest order value (main line).

### Tipwin — FIXED & VALIDATED

**Status:** DONE — 0 → 107 events (full run). Test script showed 1,609 events (more sports).
**Root cause:** Tipwin renamed market types: `3way` → `winner`, draw outcome `X` → `None` (~Feb 2026).
**Fix applied (2026-03-03):** Added `"winner": "1x2"` to MARKET_ABRV_MAP, `"None": "draw"` to TIP_MAP. Added 2-way sport detection: `winner` market without draw outcome → `moneyline` (for tennis/basketball).
**Note:** 107 events in full run vs 1,609 in test = only football+ice_hockey extracted in orchestrator (known tipwin limitation). Test script runs all sports.

### Interwetten — still slow (542s)

**Status:** MONITOR — league reduction + concurrency increase applied but didn't help enough
**Fix applied (2026-03-03):** Reduced football leagues 104 → 52, increased concurrency.
**Result:** 542s (worse than expected). Events dropped 736 → 437. Possible site-side slowdown.

### Snabbare football — recovered but slow (266s)

**Status:** STABLE — improved from 393s to 266s via prior fixes
**Current state:** Football works via API fallback + direct navigation. Monitor if snabbare.com fixes their React rendering.

### 10bet — improved (297s, 633 events)

**Status:** STABLE — improved from 485s/187ev to 297s/633ev
**Current state:** Football + basketball + ice_hockey work. Some sports still inconsistent (DOM rendering timing variance on 10bet's Playtech widget).

### Comeon Group — low events but stable

**Status:** MONITOR — ComeOn event counts fluctuate (66-154 range). Platform limitation: WS only delivers 1x2/ML. Not fixable.

### Snooker removed from ALLOWED_SPORTS

**Status:** DONE (2026-03-03). No soft provider supports snooker. Saves ~3s per Pinnacle extraction.

---

## Timing Bottlenecks

### API Soft (bottleneck: bethard 180s)
Gecko V2 providers are the bottleneck due to paginated API. Others are fast (9-22s). No action needed.

### Browser Soft (bottleneck: interwetten 542s, 10bet 527s)

| Provider | Time | Events | Efficiency | Action |
|----------|-----:|-------:|-----------|--------|
| interwetten | 542s | 437 | 0.8 ev/s | Still slow despite league reduction |
| 10bet | 527s | 62 | 0.1 ev/s | Regressed (was 633 events) — site variability |
| snabbare | 401s | 328 | 0.8 ev/s | Stable, API fallback approach |
| coolbet | 305s | 243 | 0.8 ev/s | OK |
| lyllo | 228s | 74 | 0.3 ev/s | Platform limitation |
| comeon | 204s | 76 | 0.4 ev/s | Platform limitation |
| hajper | 198s | 50 | 0.3 ev/s | Platform limitation |
| tipwin | 126s | 107 | 0.8 ev/s | Recovered from 0 |
| 888sport | 56s | 1,077 | 19.2 ev/s | Fastest browser provider |

---

## Changelog

### 2026-03-03

**Full extraction audit + provider domain/platform verification**

**Provider audit (31 providers checked):**
- All 30 active providers verified on correct platforms via Playwright + API endpoint checks
- **expekt** removed from active list: sportsbook 404 on ALL paths (Kambi API still works but users can't bet). Removed from: active list, PLATFORM_MAP, PLATFORM_GROUPS, scheduler, extraction routes, boost scraper disabled.
- **x3000** domain updated: x3000.se → x3000.com (was redirecting)
- Previous migration claims (03-01) debunked: campobet, nordicbet, bethard, dbet, swiper all alive and on correct platforms
- Platform verifications: betmgm=Kambi (API "betmgmse" ✓), goldenbull=Kambi (API "pafgoldense" ✓), all Gecko V2 providers on OBG API ✓, all Altenar on sb2frontend ✓

**Extraction fixes (8 issues investigated, 4 code fixes, 3 platform limitations confirmed):**

Fixes applied:
- **tipwin.py**: Added `"winner": "1x2"` to MARKET_ABRV_MAP (Tipwin renamed `3way`→`winner`). Added `"None": "draw"` to TIP_MAP (renamed from `X`). Added 2-way sport detection: `winner` without draw → `moneyline` for tennis/basketball. Went from 0 → 1,609 events.
- **kambi.py**: Added `MAIN_LINE` tag filter for betOfferType 1/6/7 (spread/total). Added EXCLUDE_PATTERNS for team totals ("total goals by", etc.) and prop totals (corners, cards, shots, fouls, offsides). Expected ~90% odds reduction (37k → ~4k).
- **vbet.py**: Added main line filtering using `order` field from BetConstruct Swarm API. Keeps only the spread/total candidate with lowest order value. Expected ~90% odds reduction (38k → ~3.6k).
- **interwetten.py**: Reduced football leagues 104 → 52 (removed obscure leagues without Pinnacle coverage). Increased CONCURRENT_LEAGUE_PAGES 12→16, CONCURRENT_DETAIL_PAGES 16→20, MAX_DETAIL_EVENTS 150→200. Removed ATP Challengers, WTA 125, golf, cycling.
- **constants.py**: Removed `snooker` from ALLOWED_SPORTS (no soft provider supports it).

Platform limitations confirmed (not fixable):
- **ComeOn/Hajper/Lyllo**: ComeOn WS only delivers 1x2/ML at sport level. 0 spread, near-0 total.
- **Snabbare**: Sportradar WS only delivers 1x2/ML at league level. 0 spread, near-0 total.
- **888sport**: Spectate API only returns spread/total for basketball + ice_hockey. Football has 0 spread/total.

Validated results (full extraction run):

| Metric | Before | After | Delta |
|--------|------:|------:|-------|
| Unibet odds/run | 37,220 | 5,021 | **-87%** |
| VBet odds/run | 38,118 | 5,783 | **-85%** |
| Tipwin events | 0 | 107 | **recovered** |
| Total DB odds | 106,225 | 62,399 | **-41%** |
| Value opps | 1,260 | 1,045 | -17% (cleaner data) |
| Dutch opps | 383 | 376 | -2% |
| Reverse opps | 36 | 44 | +22% |

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
