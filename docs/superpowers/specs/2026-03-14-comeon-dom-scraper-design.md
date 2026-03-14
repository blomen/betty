# ComeOn Group DOM-Based League Scraper — Design Spec

**Date:** 2026-03-14
**Status:** Draft
**Scope:** Replace WS-based extraction with DOM scraping for comeon, hajper, lyllo

## Problem

The current ComeOn extractor relies on RSocket WebSocket frames delivered by the SPA. This has two critical problems:

1. **Reliability (~70% success rate):** The WS intermittently fails to connect when navigating sport pages, especially football and tennis. No retry logic exists — the extractor waits the full 180s timeout and moves on with 0 events.

2. **Coverage (~3-5% of football catalog):** The WS only delivers events for the "popular leagues" visible on the default sport page view. ComeOn has 99 country groups with hundreds of football leagues, but only ~5-6 major leagues appear on the sport page. We extract ~15 football events vs ~300-500 available.

3. **Market gaps:** 4 sports (baseball, MMA, handball, esports) extract events but 0 odds. Pass 2 enrichment (event detail page navigation for spread/total) is fragile — total counts swing from 0 to 1,318 between runs.

4. **Boost odds divergence:** Lyllo has different boost odds than ComeOn/Hajper (e.g., 2.47 vs 2.60), but is treated as `shared_with` assuming identical odds.

## Solution

Replace WS-based sport-page extraction with **league-page DOM scraping**. Navigate individual league pages, click market tabs (1x2 → Handikapp → Over/Under), and parse odds from rendered DOM elements. Scrape boosts in the same browser session. Scrape Lyllo boosts independently.

## Architecture

### Extraction Flow

```
For each brand (comeon, hajper, lyllo):
  1. Launch browser, navigate to site
  2. Dismiss cookie overlay
  3. For each sport:
     a. Navigate to /sport/{id}-{name}/leagues
     b. Expand all country accordions
     c. Collect league URLs
     d. Filter to Pinnacle-matched leagues (via sports.yaml)
     e. For each league (up to 8 concurrent pages):
        i.   Navigate to league page
        ii.  Wait for [data-at="game-card"] selector
        iii. Parse 1x2/ML odds from default "Populara" tab
        iv.  Click "Handikapp" pill → parse spread odds
        v.   Click "Over/Under" pill → parse total odds
        vi.  Skip live events (detect by score element presence)
        vii. Close page
  4. Scrape boosts from /sport/85-odds-boost (DOM-based)
  5. Return StandardEvent list + boost list
```

### League Discovery

Each sport has a `/leagues` directory page with:
- **Popular leagues** section at top (direct links)
- **"Alla ligor"** accordion: country buttons → expand → reveal league links

**Selectors:**
- Country button: `button[class*="RegionButton"]`
- Country wrapper: `li[data-expanded]` (click toggles to `data-expanded="true"`)
- League link: `a[href*="/leagues/"]` inside expanded country

**League URL pattern:** `/sv/sportsbook/sport/{sportId}-{name}/leagues/{leagueId}-{leagueName}`

League IDs are numeric and stable (e.g., 134 = Premier League, 171 = LaLiga).

### League Filtering

ComeOn has ~99 countries for football alone. We only want leagues that Pinnacle also covers (otherwise no sharp odds for value detection).

**Strategy:** Add ComeOn league ID mappings to `sports.yaml` under each sport/league entry (same pattern as Kambi league IDs). This is a one-time manual effort using the league discovery output.

**Bootstrap process:**
1. Run a one-time discovery script (CLI command: `python -m src.app discover-leagues comeon`) that:
   - Navigates all sport league directories
   - Collects all league URLs with IDs and names
   - Fuzzy-matches Swedish league names against Pinnacle league names in `sports.yaml` using `rapidfuzz`
   - Outputs a mapping file for manual review
2. Review and merge confirmed matches into `sports.yaml` under `comeon_league_id` fields
3. On subsequent runs, the extractor reads `sports.yaml` and only navigates mapped leagues

**Swedish name handling:** League names on ComeOn are Swedish ("England Premier League", "Spanien La Liga"). Pinnacle uses English. The fuzzy matcher should normalize by stripping country prefixes and comparing core league names.

**Storage:** League mappings live in `sports.yaml` (same as Kambi, Altenar league IDs). No separate cache file.

**Fallback:** If `sports.yaml` has no ComeOn league IDs for a sport yet, extract popular leagues only (top section of league directory — typically 5-10 major leagues per sport). This is already better than the current ~15 football events.

### League Page Parsing

**Event card selectors:**
- Card: `[data-at="game-card"]`
- Event link: `a[data-at="link-to-event"]` → extract event ID from href regex `events/(\d+)`
- Team names: `small[class*="Participant"]` (2 per event: home, away)
- Time: `div[class*="game-card-time"]` → text like "Idag16:00", "Imorgon15:00", "Fre 20 Mars21:00"
- Date header: `div[class*="game-card-list-header"]` → "Idag 14 Mars"

**Date/time parsing from DOM text:**

DOM time text is Swedish and relative. Parsing rules:
- `"Idag"` → today's date (CET/CEST timezone, Europe/Stockholm)
- `"Imorgon"` → tomorrow's date
- `"Fre 20 Mars"` → parse Swedish day/month names to date (year = current year, or next year if month is in the past)
- Time portion: extract `HH:MM` suffix (always present, no space before it: "Idag16:00")
- Swedish month map: `{jan: 1, feb: 2, mar: 3, apr: 4, maj: 5, jun: 6, jul: 7, aug: 8, sep: 9, okt: 10, nov: 11, dec: 12}`
- Swedish day abbreviations: `{Mån, Tis, Ons, Tor, Fre, Lör, Sön}` — ignored for parsing, date comes from day+month
- Timezone: All times are CET (UTC+1) / CEST (UTC+2 during DST). Convert to UTC for storage.
- Combine date header ("Idag 14 Mars") with per-event time ("16:00") for full datetime

**Odds button selectors:**
- Button: `button[data-at="sportsbook-selection-btn"]`
- `aria-label`: `"Lag till val: {name}, Odds: {value}"` — primary data source

**Market tab selectors:**
- Pills: `div[class*="pill__Wrapper"]` (parent is `<button>`)
- Active pill: parent button has `[active]` attribute
- Discover dynamically — match by keyword, not hardcoded names

**Market tab keyword matching (sport-aware):**

Pill selection must be sport-aware because ice hockey and basketball have both regulation and OT-inclusive variants. Per project convention (see MEMORY.md), Pinnacle uses OT-inclusive odds for ice hockey — we must match that.

| Sport | Spread pill keyword | Total pill keyword |
|-------|--------------------|--------------------|
| ice_hockey | "Handikapp (Inkl" or "inkl. övertid" | "Over/Under" + "inkl" or "övertid" |
| basketball | "Handikapp (Inkl" or "inkl. övertid" | "Over/Under" + "inkl" or "övertid" |
| All other sports | "Handikapp" (first match) | "Over/Under" (first match) |

**Selection logic:** For ice_hockey and basketball, prefer pills containing "inkl" or "övertid" (OT-inclusive). For other sports, match the first pill containing "Handikapp" or "Over/Under". If no OT-inclusive pill exists, fall back to the generic one.

**Tabs vary by sport:**
- Football: 6 pills (Populara, Båda lagen gör mål, Over/Under mål, Handikapp, Dubbelchans, Over/Under mål i 1a halvlek)
- Ice Hockey: 5 pills (Populara, Vinnare inkl. övertid, Handikapp inkl. övertid, Over/Under mål inkl. övertid, ...)
- Basketball: 3 pills (Populara, Over/Under poäng inkl övertid, Handikapp inkl övertid)
- Tennis: likely 2-3 pills (Populara, Handikapp)

### Aria-Label Parsing

All odds data comes from `aria-label` on odds buttons. Regex patterns:

```
# 1x2
"Lag till val: Burnley FC, Odds: 4.18"         → team="Burnley FC", odds=4.18
"Lag till val: Oavgjort, Odds: 3.92"           → side="draw", odds=3.92

# Spread
"Lag till val: Burnley FC (+0.5), Odds: 1.97"  → team="Burnley FC", point=+0.5, odds=1.97
"Lag till val: Bournemouth (-0.5), Odds: 1.81" → team="Bournemouth", point=-0.5, odds=1.81

# Total
"Lag till val: Over 2.5, Odds: 1.71"           → side="over", point=2.5, odds=1.71
"Lag till val: Under 2.5, Odds: 2.16"          → side="under", point=2.5, odds=2.16
```

**Regex:** `r"Lag till val: (.+?), Odds: ([\d.]+)"`
- Then parse name for `(+/-X.X)` suffix → spread point
- Check for `Over`/`Under` prefix → total point
- `Oavgjort` → draw outcome

### Live Event Filtering

Live events are mixed into league pages. Detect by checking for score elements:
- `div[class*="ScoreRow"]` inside game card → live event → skip
- Pre-match events have `div[class*="UpcomingGameTime"]` with date text

### Concurrency Model

Unlike the WS approach (single page, sequential), DOM scraping can use **multiple browser tabs** since each league page is independent.

- Open up to 8 concurrent pages via `context.new_page()` — these are tabs within a **single browser context**, not separate browser instances. The `max_browser_instances: 3` limit in providers.yaml controls browser processes, not tabs. 8 tabs in one browser context is lightweight (shared renderer process).
- Each page navigates to one league, scrapes all 3 market tabs, then closes
- Use `asyncio.Semaphore(8)` to limit concurrent pages
- This is already configured in providers.yaml: `concurrent_leagues: 8`

### Boost Scraping

**Navigate to `/sport/85-odds-boost`** in the same browser session (no extra launch).

**Current state:** 4 boosts today, combo/parlay style. Same DOM structure as regular events — game cards with odds buttons.

**Boost-specific parsing:**
- Event name = boost title (e.g., "Inter & Bayern Munchen - båda vinner")
- Single odds button per boost (the boosted odds)
- League name may contain "Odds Boost Plus" → `superboost` category
- Date buttons for future boost dates (click through to discover upcoming boosts)
- `original_odds` not available in DOM (same limitation as WS approach)

**Lyllo independent scraping:** Lyllo has different boost odds (e.g., 2.47 vs 2.60 on ComeOn/Hajper). Scrape Lyllo boosts separately instead of using `shared_with`.

**Required providers.yaml changes:**
- Remove `lyllo` from comeon boost `shared_with` (keep hajper, snabbare)
- Add lyllo boost entry with `enabled: true`, `type: comeon`, `url: https://www.lyllocasino.com`
- Regular odds extraction remains shared (confirmed identical across all brands)

**Snabbare:** Stays on comeon's `shared_with` for both regular odds and boosts. Snabbare uses the same ComeOn platform with identical odds and boost pricing. If future evidence shows divergence (like Lyllo), split it out too.

### Brand Handling

All 3 brands (comeon, hajper, lyllo) use identical sport IDs, URL structure, and DOM selectors. The extractor builds URLs dynamically: `{site_url}/sv/sportsbook/sport/{id}-{slug}`.

| Brand | Site URL | Regular odds | Boost odds |
|-------|----------|-------------|------------|
| comeon | comeon.com | Canonical extraction | Canonical (shared with hajper, snabbare) |
| hajper | hajper.com | shared_with comeon | shared_with comeon |
| lyllo | lyllocasino.com | shared_with comeon | **Independent scrape** |
| snabbare | snabbare.com | shared_with comeon | shared_with comeon |

Regular odds remain shared (confirmed identical across all 4 brands). Only boost odds differ for Lyllo.

## Performance Estimates

| Metric | Current (WS) | New (DOM) |
|--------|-------------|-----------|
| Football events | ~15 | ~200-300 |
| Total events/run | ~103 | ~300-500 |
| Reliability | ~70% | ~95%+ |
| Duration | ~860s (14 min) | ~300-400s (5-7 min) |
| Pass 2 needed? | Yes (fragile) | No (tabs give all markets) |
| Markets per event | Inconsistent | All 3 (1x2 + spread + total) |
| Boost coverage | 4 boosts (WS) | 4+ boosts (DOM, more reliable) |

**Speed improvement** comes from:
- 8 concurrent league pages vs sequential sport navigation
- No 180s timeout waits on WS failures
- No Pass 2 enrichment (saves ~150s per sport)

**Note:** Duration estimate is per-brand. Only comeon runs canonical extraction (hajper/lyllo use `shared_with` for regular odds). Lyllo runs an independent boost scrape (~15s). Total ComeOn group time in browser_soft tier: ~300-420s.

## What Gets Removed

- RSocket frame decoding for sport listing extraction (drop `RSocketMixin` from class inheritance; the mixin stays in codebase for other providers)
- Date-button scrolling logic in `comeon_multileague.py`
- Pass 2 event-detail page enrichment
- Adaptive WS wait timers
- `MARKET_TYPE_MAP` for WS frame parsing (replaced by aria-label parsing)

## What Gets Added

- League discovery (accordion expansion + URL collection)
- League filtering (sports.yaml mapping or fuzzy-match fallback)
- Market tab clicking + DOM `aria-label` parsing
- Concurrent page pool with semaphore
- Aria-label regex parser
- ComeOn league ID entries in `sports.yaml`
- Lyllo independent boost scraper entry in providers.yaml

## What Stays the Same

- `BrowserRetriever` base class and `BrowserTransport`
- Cookie overlay dismissal logic
- `StandardEvent` output format
- Pipeline storage, matching, and analysis layers
- Orchestrator timeout enforcement (provider_timeout, sport_timeout)
- RSocketMixin (used by other providers)
- Boost EV enrichment pipeline (just receives data differently)

## Risks

1. **League mapping gap**: `sports.yaml` has no ComeOn league IDs. Need a one-time discovery pass to build the mapping. Mitigated by falling back to popular leagues if mapping is missing.

2. **DOM selector stability**: `data-at` attributes are likely stable (test framework anchors), but `class*=` selectors could change with React builds. Mitigated by preferring `data-at` selectors where available.

3. **SPA hydration timing**: Page navigation is fast (~20ms) but content takes 2-4s to render. Must wait for `[data-at="game-card"]` selector with a 10s timeout. If timeout expires (league has 0 pre-match events or SPA failed to hydrate), log a warning and skip — do not raise an error. Empty league pages count against `sport_timeout` (180s) but a single 10s timeout is a small fraction.

4. **Rate limiting**: Navigating 50+ league pages rapidly might trigger bot detection. Mitigated by using `concurrent_leagues: 8` limit and small delays between navigations.

## Testing

- Compare event counts: DOM scraper vs current WS extractor on same run
- Verify all 3 market types extracted per league (1x2 + spread + total)
- Verify Pinnacle match rate improves (more events → more matches)
- Verify Lyllo boost odds differ from ComeOn (validates independent scraping)
- Check no live events leak into extraction
- Timing: full run completes within provider_timeout (900s)
