# Rainbet provider — design spec

**Date:** 2026-05-10
**Author:** brainstorming session, validated against live spikes
**Status:** Ready for plan-writing
**Scope:** Add `rainbet` as a server-side signal-only provider in arnold's extraction pipeline.

## Goal

Wire `rainbet.com` as another odds source for arnold's consensus matching (same role as `cloudbet`, `kalshi`, `polymarket`): server-side extraction, no bet placement, matched against Pinnacle for value detection.

## Why this is harder than cloudbet/kalshi/polymarket

Those three are clean public REST APIs (~360-line extractors). **Rainbet is not.** Live discovery in this session established:

- Rainbet's sportsbook is a **Betby** white-label (BTRenderer SPA, brand_id `2374656571012681728`).
- Rainbet has **no public odds API**. The only public REST endpoint, `services.rainbet.com/v1/external/affiliates`, is a streamer-leaderboard, not odds.
- Rainbet's site is fronted by **Cloudflare Bot Management + an embedded Cloudflare Turnstile widget**. Curl returns 403 from any IP. Camoufox passes the JS-based outer challenge but fails the embedded Turnstile (known issue: daijro/camoufox#150, daijro/camoufox#574).
- Rainbet **geoblocks Germany** (Hetzner location). Bahnhof Sweden proxy (existing `PROXY_URL`) is required.
- Rainbet **DMCAs scrapers** (filed in `github/dmca`). All ~15 GitHub repos using rainbet's API hit the leaderboard endpoint, none scrape odds.

The integration is therefore in the **`browser_antibot` tier** alongside ComeOn — a Chromium-based extractor that runs the actual Betby renderer, lets it make its own data calls, and harvests them.

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│ Extraction tier: browser_antibot (25-min cadence)       │
│                                                          │
│  RainbetRetriever (BrowserRetriever subclass)           │
│    ├── patchright (Chromium + iframe-click patches)     │
│    ├── PROXY_URL (Bahnhof Sweden — geo requirement)     │
│    └── per-cycle:                                        │
│         1. Launch headless Chromium                      │
│         2. Goto https://rainbet.com/sportsbook           │
│         3. patchright auto-passes CF JS challenge        │
│         4. Click Turnstile widget at iframe bbox center  │
│         5. Wait for *.sptpub.com network activity        │
│         6. Per sport: navigate, capture WS+REST, parse   │
│         7. Close browser, emit StandardEvent[]           │
└─────────────────────────────────────────────────────────┘

           ↓ writes to ↓

┌─────────────────────────────────────────────────────────┐
│ Existing arnold storage path                            │
│  StandardEvent[] → matching → DB → scanner → opps        │
└─────────────────────────────────────────────────────────┘
```

### Why patchright instead of Camoufox

Arnold's existing browser providers (ComeOn, Coolbet) use Camoufox (anti-detect Firefox). Camoufox passes Imperva and the older Cloudflare JS challenges, but **cannot reliably click cross-origin Turnstile iframes inside Docker**. Daijro (Camoufox author) confirmed in issue #150 that the recommended approach for interactive Turnstile is Patchright + Chromium. Issue #574 (still open) documents Camoufox's Docker-specific Turnstile failure across all browser versions tested (v135, v142, v146).

Patchright is a patched Playwright + Chromium that:
- Uses real-browser fingerprinting (CF auto-passes the algorithmic challenge)
- Supports cross-origin iframe interaction (Turnstile widget click)
- Pip-installable, free, open-source, MIT-licensed

**Validated in spike v4 (this session):** total time from cold-launch to working Betby data WebSocket: 32.8 seconds. Numbers from the actual run on the production server's backend container:
- 278 HTTP requests to `start3.sptpub.com` (Betby bootstrap)
- 65 HTTP requests to `api-a-c7818b61-600.sptpub.com` (Betby data API)
- 1 WebSocket opened: `wss://api-a-c7818b61-600.sptpub.com/api/v1/ws_new?brand_id=2374656571012681728&lang=en`

## Provider config

```yaml
# backend/src/config/providers.yaml
rainbet:
  id: rainbet
  name: Rainbet
  domain: rainbet.com
  retriever_type: rainbet
  site_url: https://rainbet.com/sportsbook
  brand_id: "2374656571012681728"
  theme_name: rainbet
  sport_timeout: 600
  supported_sports:
    - football
    - basketball
    - tennis
    - ice_hockey
    - american_football
    - baseball
    - mma
    - boxing
    - esports
```

Active list adds `- rainbet` under the existing international section.

Scheduling tier (extraction_scheduling block):

```yaml
browser_antibot:
  max_concurrent_browsers: 1
  providers:
    - comeon
    - rainbet         # NEW
  interval_minutes: 25
  grouped: false
```

Both providers run a Chromium/Firefox browser; serializing them via `max_concurrent_browsers: 1` keeps memory under the 48 GB Docker limit.

## File layout

| File | Purpose |
|---|---|
| `backend/src/providers/rainbet.py` | `RainbetRetriever` class (subclass of `BrowserRetriever`). Owns the patchright lifecycle, CF/Turnstile bypass, navigation, parsing. ~600-800 lines estimated based on ComeOn's size. |
| `backend/src/factory.py` | Add `elif retriever_type == "rainbet": return RainbetRetriever(...)` branch. |
| `backend/pyproject.toml` | Add `patchright>=1.40` dependency. |
| `backend/Dockerfile` | Add a `RUN patchright install chromium` step. (Chromium is already cached at `/app/.playwright/chromium-*` from vanilla playwright install — confirm it works without re-download or add the line.) |
| `backend/src/config/providers.yaml` | Provider entry + active list + scheduling tier as shown above. |
| `backend/src/constants.py` | If `SHARP_PROVIDERS` or any whitelist references it, leave unchanged — rainbet is not sharp. |

No new core abstractions. No changes to `BrowserRetriever`, transport, orchestrator, or scanner.

## Operational flow per extraction cycle

1. **Launch.** `patchright.async_api.async_playwright()` → `chromium.launch(headless=True, proxy=PROXY_URL, args=["--disable-http2", "--disable-quic"])`. Match the spike-v4-validated args.
2. **Context.** New context with `user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36"`, `viewport={"width": 1280, "height": 720}` (Turnstile click coordinates assume this size).
3. **Goto landing.** `page.goto(https://rainbet.com/sportsbook, wait_until="domcontentloaded")`. Patchright passes the JS challenge automatically and rainbet sets `cf_clearance` cookie within ~1s.
4. **Clear embedded Turnstile.** Loop until `*.sptpub.com` HTTP requests start firing OR `wss://api-a-*.sptpub.com/api/v1/ws_new` opens. The validated approach (spike v4) is clicking at the hardcoded coordinate `(210, 290)` on the 1280×720 viewport — this is what `Xewdy444/CF-Clearance-Scraper` ships with and what successfully cleared rainbet's widget. Use this as the primary click strategy. As a defensive secondary attempt within the loop, also try `iframe[src*="challenges.cloudflare.com"]` bbox-center click — useful if rainbet ever moves the widget. Sleep 1.5s between attempts. Cap at ~60s; abort cycle if not cleared.
5. **Per supported sport, sequentially:**
   - `page.goto(f"{site_url}/{sport_slug}")`
   - Hook `page.on("response")` for `api-a-*.sptpub.com` and `start3.sptpub.com` JSON responses
   - Hook `page.on("websocket")` → `framereceived` for binary + text frames
   - Wait until either: a sport-listing JSON response arrives, OR 30s elapses
   - Save raw responses to per-sport buffer
6. **Parse.** From buffered responses + WS frames: extract events, markets, outcomes, odds. Map to `StandardEvent[]`. Apply `normalize_team_name`, `normalize_outcome`. Filter to `ALLOWED_MARKETS = {"1x2", "moneyline", "spread", "total"}`.
7. **Cleanup.** `await context.close()` then `await browser.close()`. Existing `BrowserRetriever` cleanup pattern handles subprocess reaping.

## Things still to discover at build time

These are deferred from this design phase to the implementation phase. They are tractable engineering questions, not architectural unknowns.

1. **Sport-slug map.** Rainbet's URL routes vs. arnold's internal sport keys. Discovery: visit each sport from a manual browser session, log the URL pattern. Examples we have: `/sportsbook/esports/counter-strike`. Need confirmation for `soccer`/`football`, `basketball`, `tennis`, `ice-hockey`, `american-football`, `baseball`, `mma`, `boxing`. Build phase: a 5-line table in `rainbet.py`.
2. **Wire format.** Spike v4 saw 65 HTTP responses to `api-a-*.sptpub.com` and a WS open, but `framereceived` callback returned 0 frames during the 60s capture. Likely either (a) frames are binary subprotocol patchright filters by default, (b) data lives in REST not WS for the listing page, or (c) we cut off too early before the renderer subscribed. Build phase: capture full HAR with `--save-har`, parse the `start3.sptpub.com` bootstrap responses, identify whether REST or WS carries the markets, write the parser to that protocol.
3. **Shard discovery.** `api-a-c7818b61-600.sptpub.com` looks like a dynamic shard URL. The renderer derives it from somewhere — probably a config response from `start3.sptpub.com`. Build phase: read the bootstrap response, extract the shard URL, pass it to subsequent calls.
4. **Cookie reuse vs. per-cycle re-bypass.** `cf_clearance` lasts ~30 min. With a 25-min cadence we could either (a) refresh on every cycle (~33s overhead per cycle) or (b) cache the cookie in a redis/file and only re-bypass when expired (saves ~33s per cycle but adds a caching layer). Pick (a) for the initial build — simpler, no shared state, the 33s/25min overhead is negligible. Revisit only if extraction frequency increases.
5. **Brotli encoding.** Camoufox issue #574 noted Brotli decompression bugs. Patchright doesn't have this issue (vanilla Chromium handles `br` correctly). No mitigation needed.
6. **TLS / JA4 fingerprint.** Modern CF detection includes TLS handshake analysis. Patchright's bundled Chromium handles this. If we ever see CF blocks resume, that's a re-investigation point.

## Tradeoffs and risks

| Risk | Likelihood | Mitigation |
|---|---|---|
| CF/Turnstile evolves and patchright bypass breaks | Medium-high (this is an arms race) | Patchright is actively maintained. If a CF update breaks the bypass, this provider goes dark for that cycle and reports failure to the orchestrator (same failure mode as any other browser provider). Recovery requires bumping patchright version or adapting the click strategy. |
| Rainbet detects + blocks our IP/UA | Low-medium | Bahnhof PROXY_URL is residential ISP (Sweden, AS8473); UA matches default Chrome 133. If blocked, retry with rotated UA. |
| Rainbet bans the proxy IP | Low | Bahnhof IP is shared with ~10 other arnold extractors. If they shadow-ban, we'd see other providers (Pinnacle, Altenar) impacted too — easy to detect. |
| Match rate against Pinnacle is poor | Unknown | Won't know until built. ComeOn-class providers typically hit 60-80% match rate on football/basketball. Rainbet's catalog is crypto-skewed (esports-heavy), so esports match rate may be high but football lower. |
| Build cost overruns | Medium | Estimate is 2-3 days. Risk areas: WS frame parsing (item 2 above) and shard discovery (item 3). If those go sideways, could extend to 5 days. |
| Adds 200-400 MB to backend Docker image | Confirmed | Patchright bundles Chromium (~200 MB). Existing image already has Camoufox Firefox (~150 MB). Total impact: ~+250 MB. Reasonable. |
| 1-2 GB Chromium memory during runs | Confirmed | Identical profile to ComeOn. `max_concurrent_browsers: 1` in `browser_antibot` keeps total under 2 GB at any moment. |

## What this is NOT

- **Not a sharp source.** Rainbet is a soft book; their odds reflect their own margin + customer flow, not true market.
- **Not a place we can place bets.** No mirror workflow, no bet placement automation. Server-side extraction only — same role as cloudbet/kalshi/poly.
- **Not multi-tenant Betby.** This spec scopes only `rainbet`. Adding `stake.com` or other Betby tenants later is a separate project that would extend the existing `RainbetRetriever` (or factor out a base class) at that time.
- **Not a "10-minute drop-in".** It's a real provider build with browser orchestration, antibot bypass, protocol parsing. Equivalent effort to ComeOn's original integration.

## Success criteria

- Daily extraction completes successfully ≥ 80% of cycles (matches ComeOn baseline)
- ≥ 50% match rate against Pinnacle for football, ≥ 60% for esports
- ≥ 1 opportunity per day surfaced in scanner output
- Zero impact on other providers (no shared lock contention beyond `max_concurrent_browsers: 1`)
- Memory budget held under 48 GB Docker limit during peak

## Out of scope

- Stake.com or other Betby tenants
- Bet-placement automation (mirror workflow)
- Live odds (we extract pre-match only, like all other arnold providers)
- Markets beyond `ALLOWED_MARKETS = {1x2, moneyline, spread, total}`
- Player props, corners, cards, correct score, etc.

## References

- Spike captures: `/tmp/betby_v4_capture.json` on production server (ephemeral, not committed)
- Bypass technique: [Xewdy444/CF-Clearance-Scraper](https://github.com/Xewdy444/CF-Clearance-Scraper) (Patchright branch), MIT license
- Camoufox limitations: [daijro/camoufox#150](https://github.com/daijro/camoufox/issues/150), [#574](https://github.com/daijro/camoufox/issues/574)
- Betby SDK reference: bundled in rainbet's chunk `pages/sportsbook/[[...slug]]-3bb70568609ca2aa.js`, which calls `new BTRenderer().initialize({...})` against `https://rainbet.sportsbookcdn.com/bt-renderer.min.js`
- Provider pattern reference: `backend/src/providers/comeon_multileague.py`, `backend/src/providers/coolbet.py`
