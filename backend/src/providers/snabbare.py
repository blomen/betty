"""
Snabbare Retriever - WebSocket-based sportsbook extraction

Snabbare (Sportradar MTS platform / Komigen) uses WebSocket for real-time event data.
Binary frames contain JSON payloads with events, markets, and selections arrays.

Strategy:
1. page.goto() to each sport page (React Router SPA)
2. DOM sidebar contains league <a> links (React Router <Link> components)
3. Click each link via el.click() → React Router navigates → WS delivers league data
4. history.back() → back to sport page → click next league

CRITICAL: Only DOM link clicks trigger WS data delivery. pushState/popstate
does NOT work — React Router updates the route but component lifecycle hooks
don't fire → no WS subscription → no data.

Event data (odds/markets) is exclusively delivered via WebSocket.
REST API (/sportsbook-api/api/) only provides metadata (sports, leagues, config).

Data structure (from WS frames):
- payload.events[]: {id, eventName, startingOn, sportId, leagueId, leagueName, status, ...}
- payload.markets[]: {id, eventId, marketType: {id, originalName}, ...}
- payload.selections[]: {eventId, marketId, outcomeType, name, trueOdds, points}

URL structure: /sv/sportsbook/sport/{sportId}-{slug}/leagues/{leagueId}-{slug}

Notes:
- Sport overview page only shows ~16 featured events with 1x2 markets
- League detail pages return all events for that league with 1x2 + goal markets
- Spread/total markets only available on individual event pages (not extracted)
- Event separator is " - " (dash), not " vs "
"""

from typing import Dict, Any, List, Optional
import json
import logging
import re
from datetime import datetime
import asyncio

from ..core import BrowserRetriever, BrowserTransport, StandardEvent
from ..matching.normalizer import normalize_team_name
from .mixins import RSocketMixin

logger = logging.getLogger(__name__)


class SnabbareRetriever(BrowserRetriever, RSocketMixin):
    """
    WebSocket-based retriever for Snabbare sportsbook.

    On first call, discovers all leagues via REST API, navigates to each
    to collect WS data, then caches results and returns filtered by sport.
    """

    # Snabbare sport ID → (canonical name, URL slug)
    SPORT_MAP: Dict[int, tuple] = {
        1:   ("football",          "fotboll"),
        2:   ("basketball",        "basket"),
        3:   ("american_football", "amerikansk-fotboll"),
        4:   ("ice_hockey",        "ishockey"),
        5:   ("rugby",             "rugby"),
        6:   ("tennis",            "tennis"),
        7:   ("handball",          "handboll"),
        8:   ("volleyball",        "volleyboll"),
        10:  ("table_tennis",      "bordtennis"),
        12:  ("baseball",          "baseboll"),
        17:  ("cricket",           "cricket"),
        31:  ("boxing",            "boxning"),
        37:  ("mma",               "mma"),
        130: ("esports",           "esport"),
        48:  ("darts",             "dart"),
    }

    # Reverse: canonical name → sport ID
    SPORT_ID_BY_NAME = {v[0]: k for k, v in SPORT_MAP.items()}

    # Outcome type → standard name (include lowercase for inconsistent WS data)
    OUTCOME_MAP = {
        "Home":  "home",
        "Away":  "away",
        "Draw":  "draw",
        "Over":  "over",
        "Under": "under",
        "home":  "home",
        "away":  "away",
        "draw":  "draw",
        "over":  "over",
        "under": "under",
    }

    # Market type IDs we want (discovered via diag_snabbare_markets.py)
    MARKET_TYPE_MAP = {
        # 1x2 (3-way)
        1: "1x2",
        # Moneyline (2-way)
        175: "moneyline",   # Winner
        206: "moneyline",   # Winner (incl. overtime)
        376: "moneyline",   # Winner (incl. overtime and penalties)
        # Total (over/under)
        18: "total",         # Over/Under (generic — football, handball)
        212: "total",        # Total (incl. overtime) — basketball
        225: "total",        # Total Points O/U — basketball
        1621: "total",       # Total Goals Over/Under (Regular Time) — ice hockey
        1622: "total",       # Total Goals Over/Under — ice hockey
        # Spread (handicap)
        16: "spread",        # Asian Handicap (generic — football)
        187: "spread",       # Handicap — basketball
        1619: "spread",      # Puck Line (Regular Time) — ice hockey
        1625: "spread",      # Puck Line — ice hockey
    }

    # Track unknown market type IDs for discovery (class-level set to avoid noise)
    _logged_unknown_market_ids: set = set()

    def __init__(self, config: Dict[str, Any], transport: Optional[BrowserTransport] = None):
        super().__init__(config, transport)
        self.site_url = config.get("site_url", "https://www.snabbare.com")
        self.api_base = "https://www.snabbare.com/sportsbook-api/api"
        self.default_params = {
            "franchiseCode": "SWEDEN_SNABBARE",
            "locale": "sv",
        }

    async def extract(self, sport: str, limit: int = 1000, **kwargs) -> List[StandardEvent]:
        """
        Extract events for a given sport via SPA league-link clicking.

        Each sport call navigates to that sport's page, clicks league links
        via React Router (SPA, no page reload), and collects WS data.
        Fits within the orchestrator's per-sport timeout (~120s).
        """
        # Health check — return quickly without full extraction
        if limit <= 1:
            return await self._quick_health_check()

        events = await self._extract_sport(sport)
        return events[:limit]

    async def _quick_health_check(self) -> List[StandardEvent]:
        """Quick health check: verify site is accessible."""
        try:
            if not isinstance(self.transport, BrowserTransport):
                return []
            await self.transport._ensure_browser()
            page = self.transport.page

            if not self._session_ready:
                await page.goto(self.site_url, wait_until="domcontentloaded", timeout=30000)
                await self._handle_cookie_consent(page)
                await asyncio.sleep(2)
                self._session_ready = True

            return []
        except Exception as e:
            logger.error(f"[{self.provider_id}] Health check failed: {e}")
            raise

    LEAGUE_SETTLE_TIME = 0.04  # min seconds to wait for WS data after SPA link click
    MAX_LEAGUE_SETTLE_TIME = 0.15  # max seconds to wait (if WS data still arriving)

    # Per-sport league caps — football has 200+ leagues but most are tiny
    SPORT_LEAGUE_CAPS: Dict[str, int] = {
        "football": 40,
        "basketball": 30,
        "ice_hockey": 30,
        "tennis": 25,
        "handball": 25,
    }
    DEFAULT_LEAGUE_CAP = 60

    # Multi-tab parallelism for league clicking
    PARALLEL_TABS = 3
    MIN_LEAGUES_FOR_PARALLEL = 6

    async def _extract_sport(self, sport: str) -> List[StandardEvent]:
        """
        Extract events for one sport by navigating to the sport page and
        clicking each league link via React Router (SPA, no page reload).

        Strategy:
        1. page.goto() to the sport page (1 full navigation per sport)
        2. DOM sidebar contains league <a> links (React Router <Link> components)
        3. Click each link via JS el.click() → React Router navigates → same WS delivers data
        4. history.back() → back to sport page → click next league

        IMPORTANT: Only DOM link clicks trigger WS data delivery. pushState/popstate
        does NOT trigger React component lifecycle → no WS subscription → no data.

        Each sport fits within the orchestrator's per-sport timeout (~120s).
        """
        sport_id = self.SPORT_ID_BY_NAME.get(sport)
        if sport_id is None:
            return []

        sport_info = self.SPORT_MAP.get(sport_id)
        if not sport_info:
            return []
        canonical, slug = sport_info

        try:
            if not isinstance(self.transport, BrowserTransport):
                logger.error(f"[{self.provider_id}] Requires BrowserTransport")
                return []

            await self.transport._ensure_browser()
            page = self.transport.page

            # WS message store for this sport
            ws_messages: List = []

            # Setup WS interception (handles binary RSocket + text JSON)
            self._setup_snabbare_ws(page, ws_messages)

            # Initial session setup (cookie consent, etc.) — only once
            if not self._session_ready:
                await page.goto(
                    f"{self.site_url}/sv/sportsbook",
                    wait_until="domcontentloaded", timeout=30000
                )
                await self._handle_cookie_consent(page)
                await self._remove_overlays(page)
                await asyncio.sleep(2)
                self._session_ready = True

            # Navigate to sport page (retry once on connection error)
            # Use domcontentloaded — "load" waits for all images/fonts which can hang
            # on heavy pages like football with 60+ leagues
            sport_url = f"{self.site_url}/sv/sportsbook/sport/{sport_id}-{slug}"
            try:
                await page.goto(sport_url, wait_until="domcontentloaded", timeout=30000)
            except Exception as nav_err:
                if "Connection closed" in str(nav_err) or "closed" in str(nav_err).lower():
                    logger.warning(
                        f"[{self.provider_id}] Browser connection lost for {sport}, reconnecting..."
                    )
                    page = await self._reconnect_browser()
                    await page.goto(sport_url, wait_until="domcontentloaded", timeout=30000)
                else:
                    raise
            # Re-register WS after goto (goto destroys page context)
            self._setup_snabbare_ws(page, ws_messages)
            await self._remove_overlays(page)

            # Wait for league links to appear in the sidebar (React renders async)
            # Football has 60+ leagues — React needs extra time to hydrate the sidebar
            link_timeout = 8000 if sport == "football" else 5000
            try:
                await page.wait_for_selector(
                    'a[href*="/leagues/"]', timeout=link_timeout
                )
            except Exception:
                logger.debug(f"[{self.provider_id}] {canonical}: no league links after {link_timeout}ms wait")
            await asyncio.sleep(0.5)

            # Pre-filter leagues via REST API: skip outright-only and 0-prematch leagues
            # This avoids wasting time clicking leagues that yield no match odds
            valid_league_ids = await self._get_valid_league_ids(page, sport_id)

            # Discover league links from DOM sidebar
            # These are React Router <Link> components — clicking them triggers
            # SPA navigation and WS subscription for that league's events.
            # IMPORTANT: Filter OUT event-level links (/events/) — we only want
            # league-level links like /leagues/123-premier-league (not /events/456-team-a-team-b)
            league_links = await page.evaluate("""() => {
                const links = document.querySelectorAll('a[href*="/leagues/"]');
                return Array.from(links)
                    .filter(l => !l.getAttribute('href').includes('/events/'))
                    .map(l => ({
                        href: l.getAttribute('href'),
                        text: l.textContent.trim().substring(0, 60)
                    }));
            }""")
            league_links = league_links or []

            # Fallback: if DOM sidebar is empty, try REST API for league discovery
            # This handles cases where the React sidebar doesn't render (page too heavy)
            if not league_links:
                logger.info(f"[{self.provider_id}] {canonical}: DOM sidebar empty, trying REST API league discovery")
                try:
                    api_leagues = await page.evaluate(f"""async () => {{
                        const r = await fetch(
                            'https://www.snabbare.com/sportsbook-api/api/leagues' +
                            '?franchiseCode=SWEDEN_SNABBARE&locale=sv&sportIds={sport_id}'
                        );
                        if (!r.ok) return [];
                        const data = await r.json();
                        return (data || []).map(l => ({{
                            href: '/sv/sportsbook/sport/{sport_id}-{slug}/leagues/' + l.id + '-' + (l.slug || l.name || '').toLowerCase().replace(/\\s+/g, '-'),
                            text: l.name || ''
                        }}));
                    }}""")
                    if api_leagues:
                        league_links = api_leagues
                        logger.info(f"[{self.provider_id}] {canonical}: REST API returned {len(league_links)} leagues")
                except Exception as api_err:
                    logger.debug(f"[{self.provider_id}] {canonical}: REST API league fallback failed: {api_err}")

            # Dedup by league ID (sidebar has duplicate links: e.g.
            # "898-fa-cup" AND "898-england-fa-cup" are the same league)
            seen_league_ids = set()
            unique_links = []
            for link in league_links:
                href = link.get("href", "")
                if not href:
                    continue
                lid = self._extract_league_id(href)
                if lid and lid not in seen_league_ids:
                    seen_league_ids.add(lid)
                    unique_links.append(link)
                elif not lid and href not in seen_league_ids:
                    # Fallback: dedup by full href if no ID extractable
                    seen_league_ids.add(href)
                    unique_links.append(link)
            league_links = unique_links

            # Filter out leagues with no prematch events (REST API pre-filter)
            if valid_league_ids is not None:
                before_filter = len(league_links)
                league_links = [
                    link for link in league_links
                    if self._extract_league_id(link.get("href", "")) in valid_league_ids
                ]
                skipped = before_filter - len(league_links)
                if skipped > 0:
                    logger.debug(
                        f"[{self.provider_id}] {canonical}: skipped {skipped}/{before_filter} "
                        f"leagues (outright-only or 0 prematch events)"
                    )

            if not league_links:
                logger.debug(f"[{self.provider_id}] {canonical}: no league links in DOM")
                return []

            # Cap leagues per sport to stay within sport_timeout
            max_leagues = self.SPORT_LEAGUE_CAPS.get(sport, self.DEFAULT_LEAGUE_CAP)
            if len(league_links) > max_leagues:
                logger.debug(
                    f"[{self.provider_id}] {canonical}: capping {len(league_links)} leagues "
                    f"to top {max_leagues}"
                )
                league_links = league_links[:max_leagues]

            logger.debug(
                f"[{self.provider_id}] {canonical}: {len(league_links)} league links to process"
            )

            # Click league links (parallel across tabs if enough leagues)
            if len(league_links) >= self.MIN_LEAGUES_FOR_PARALLEL:
                leagues_processed, leagues_with_data, errors = await self._click_leagues_parallel(
                    sport_url, league_links, ws_messages, page, canonical,
                )
            else:
                leagues_processed, leagues_with_data, errors = await self._click_league_group(
                    page, league_links, sport_url, ws_messages,
                )

            # Parse WS messages into events
            events_by_sport = self._parse_ws_data(ws_messages)
            events = events_by_sport.get(canonical, [])

            events_with_markets = sum(1 for e in events if e.markets)
            logger.debug(
                f"[{self.provider_id}] {canonical}: {leagues_processed}/{len(league_links)} leagues, "
                f"{leagues_with_data} with WS data, "
                f"{len(ws_messages)} msgs -> {len(events)} events "
                f"({events_with_markets} with markets, {errors} errors)"
            )

            return events

        except Exception as e:
            logger.error(
                f"[{self.provider_id}] Error extracting {sport}: {e}", exc_info=True
            )
            return []

    async def _click_leagues_parallel(
        self, sport_url: str, league_links: list, ws_messages: list,
        main_page, canonical: str,
    ) -> tuple:
        """Click league links in parallel across multiple browser tabs.

        Creates extra browser pages in the same context, splits league links
        round-robin, and clicks in parallel via asyncio.gather().
        All pages share the same ws_messages list for WS data collection.
        Returns (leagues_processed, leagues_with_data, errors).
        """
        num_tabs = min(self.PARALLEL_TABS, len(league_links))

        # Split league links round-robin across tabs
        groups: list[list] = [[] for _ in range(num_tabs)]
        for i, link in enumerate(league_links):
            groups[i % num_tabs].append(link)

        extra_pages = []
        try:
            context = self.transport.context
            for _ in range(num_tabs - 1):
                new_page = await context.new_page()
                self._setup_snabbare_ws(new_page, ws_messages)
                await new_page.goto(sport_url, wait_until="domcontentloaded", timeout=30000)
                self._setup_snabbare_ws(new_page, ws_messages)  # Re-register after goto
                await self._remove_overlays(new_page)
                extra_pages.append(new_page)

            all_pages = [main_page] + extra_pages

            logger.debug(
                f"[{self.provider_id}] {canonical}: clicking {len(league_links)} leagues "
                f"across {num_tabs} parallel tabs"
            )

            results = await asyncio.gather(
                *[
                    self._click_league_group(all_pages[i], groups[i], sport_url, ws_messages)
                    for i in range(num_tabs)
                ],
                return_exceptions=True,
            )

            total_processed = 0
            total_with_data = 0
            total_errors = 0
            for r in results:
                if isinstance(r, tuple):
                    total_processed += r[0]
                    total_with_data += r[1]
                    total_errors += r[2]
                else:
                    logger.warning(f"[{self.provider_id}] {canonical}: parallel tab error: {r}")
                    total_errors += 1

            return total_processed, total_with_data, total_errors

        finally:
            for p in extra_pages:
                try:
                    await p.close()
                except Exception:
                    pass

    async def _click_league_group(
        self, page, links: list, sport_url: str, ws_messages: list,
    ) -> tuple:
        """Click a group of league links on a single page.

        Returns (processed, with_data, errors).
        """
        processed = 0
        with_data = 0
        errors = 0

        for link in links:
            ws_before = len(ws_messages)
            try:
                href = link["href"]
                clicked = await page.evaluate(
                    f"""() => {{
                        const el = document.querySelector('a[href="{href}"]');
                        if (el) {{ el.click(); return true; }}
                        return false;
                    }}"""
                )
                if not clicked:
                    full_url = f"{self.site_url}{href}" if href.startswith("/") else href
                    try:
                        await page.goto(full_url, wait_until="domcontentloaded", timeout=15000)
                        self._setup_snabbare_ws(page, ws_messages)
                    except Exception:
                        errors += 1
                        continue

                # Adaptive wait
                await asyncio.sleep(self.LEAGUE_SETTLE_TIME)
                elapsed = self.LEAGUE_SETTLE_TIME
                while len(ws_messages) == ws_before and elapsed < self.MAX_LEAGUE_SETTLE_TIME:
                    await asyncio.sleep(0.05)
                    elapsed += 0.05

                processed += 1
                if len(ws_messages) - ws_before > 0:
                    with_data += 1

                # Navigate back to sport page for next league
                if not clicked:
                    await page.goto(sport_url, wait_until="domcontentloaded", timeout=15000)
                    self._setup_snabbare_ws(page, ws_messages)
                    await asyncio.sleep(0.05)
                else:
                    await page.evaluate("window.history.back()")
                    await asyncio.sleep(0.02)

            except Exception as e:
                errors += 1
                err_str = str(e)
                if "Connection closed" in err_str or "closed" in err_str.lower() or "Target crashed" in err_str:
                    break  # Page is dead, stop this group
                try:
                    await page.goto(sport_url, wait_until="domcontentloaded", timeout=15000)
                    self._setup_snabbare_ws(page, ws_messages)
                    await asyncio.sleep(0.5)
                except Exception:
                    break

        return processed, with_data, errors

    @staticmethod
    def _extract_league_id(href: str) -> str:
        """Extract league ID from a URL like /sv/sportsbook/sport/1-fotboll/leagues/123-premier-league."""
        import re as _re
        match = _re.search(r'/leagues/(\d+)', href)
        return match.group(1) if match else ""

    async def _get_valid_league_ids(self, page, sport_id: int) -> Optional[set]:
        """Fetch league metadata from REST API and return IDs of leagues worth clicking.

        Filters out:
        - Outright-only leagues (no match odds, just futures)
        - Leagues with 0 prematch events (eventCount - liveEventCount <= 0)

        Returns None if API call fails (caller should skip filtering).
        """
        try:
            data = await page.evaluate(f"""async () => {{
                try {{
                    const r = await fetch(
                        'https://www.snabbare.com/sportsbook-api/api/leagues' +
                        '?franchiseCode=SWEDEN_SNABBARE&locale=sv&sportIds={sport_id}'
                    );
                    if (!r.ok) return null;
                    const leagues = await r.json();
                    // Return only id, eventCount, liveEventCount, isOutrightsOnlyLeague
                    return (leagues || []).map(l => ({{
                        id: String(l.id),
                        prematch: (l.eventCount || 0) - (l.liveEventCount || 0),
                        outright: !!l.isOutrightsOnlyLeague
                    }}));
                }} catch(e) {{ return null; }}
            }}""")
            if data is None:
                return None

            valid = set()
            for lg in data:
                if lg.get("outright"):
                    continue
                if (lg.get("prematch", 0) or 0) <= 0:
                    continue
                valid.add(str(lg["id"]))

            logger.debug(
                f"[{self.provider_id}] REST API: {len(valid)}/{len(data)} leagues "
                f"have prematch events for sportId={sport_id}"
            )
            return valid
        except Exception as e:
            logger.debug(f"[{self.provider_id}] REST API league pre-filter failed: {e}")
            return None

    async def _remove_overlays(self, page) -> None:
        """Remove OneTrust cookie overlay and other blocking elements."""
        try:
            await page.evaluate("""() => {
                document.querySelectorAll(
                    '.onetrust-pc-dark-filter, #onetrust-consent-sdk, .ot-fade-in'
                ).forEach(e => e.remove());
            }""")
        except Exception:
            pass

    def _parse_ws_data(self, ws_messages: List) -> Dict[str, List[StandardEvent]]:
        """
        Parse WebSocket messages into StandardEvents grouped by sport.

        WS messages from RSocketMixin are already decoded to JSON lists.
        Each message is a list of dicts with 'payload' containing events/markets/selections.
        """
        # Collect all raw data (dedup by ID to avoid duplicates from repeated WS messages)
        all_events: Dict[str, Dict] = {}
        all_markets_by_id: Dict[str, Dict] = {}  # market_id -> market dict
        all_selections_by_id: Dict[str, Dict] = {}  # selection unique key -> selection dict

        for msg_list in ws_messages:
            if not isinstance(msg_list, list):
                continue
            for item in msg_list:
                if not isinstance(item, dict):
                    continue
                payload = item.get("payload", {})
                if not isinstance(payload, dict):
                    continue

                for ev in payload.get("events", []):
                    eid = str(ev.get("id", ""))
                    if eid:
                        all_events[eid] = ev

                for mkt in payload.get("markets", []):
                    mid = str(mkt.get("id", ""))
                    if mid:
                        all_markets_by_id[mid] = mkt

                for sel in payload.get("selections", []):
                    # Dedup selections by (marketId, outcomeType) or (marketId, name)
                    mid = str(sel.get("marketId", ""))
                    sel_key = f"{mid}:{sel.get('outcomeType', '')}:{sel.get('name', '')}"
                    if mid:
                        all_selections_by_id[sel_key] = sel

        # Rebuild grouped structures from deduped data
        all_markets: Dict[str, List[Dict]] = {}
        for mkt in all_markets_by_id.values():
            eid = str(mkt.get("eventId", ""))
            if eid:
                all_markets.setdefault(eid, []).append(mkt)

        all_selections: Dict[str, List[Dict]] = {}
        for sel in all_selections_by_id.values():
            mid = str(sel.get("marketId", ""))
            if mid:
                all_selections.setdefault(mid, []).append(sel)

        logger.debug(
            f"[{self.provider_id}] WS data: "
            f"{len(all_events)} events, "
            f"{sum(len(v) for v in all_markets.values())} markets, "
            f"{sum(len(v) for v in all_selections.values())} selections"
        )

        # Build StandardEvents
        events_by_sport: Dict[str, List[StandardEvent]] = {}
        seen: set = set()

        for eid, ev in all_events.items():
            std_event = self._build_event(eid, ev, all_markets.get(eid, []), all_selections)
            if not std_event:
                continue

            key = f"{std_event.home_team}:{std_event.away_team}:{std_event.start_time}"
            if key in seen:
                continue
            seen.add(key)

            events_by_sport.setdefault(std_event.sport, []).append(std_event)

        return events_by_sport

    def _build_event(
        self,
        event_id: str,
        ev: Dict,
        markets_raw: List[Dict],
        all_selections: Dict[str, List[Dict]],
    ) -> Optional[StandardEvent]:
        """Build a StandardEvent from WS event + markets + selections data."""
        event_name = ev.get("eventName", "")
        if not event_name:
            return None

        # Snabbare uses " - " as separator
        parts = re.split(r'\s+-\s+', event_name, maxsplit=1)
        if len(parts) != 2:
            # Fallback: try other separators
            parts = re.split(r'\s+(?:vs\.?|–|—)\s+', event_name, maxsplit=1)
            if len(parts) != 2:
                return None

        home_raw, away_raw = parts[0].strip(), parts[1].strip()
        if not home_raw or not away_raw:
            return None

        # Skip live events
        if ev.get("isLive", False):
            return None
        status = ev.get("status", "")
        if status and status.lower() in ("live", "started", "inprogress"):
            return None

        # Determine sport from sportId
        sport_id = ev.get("sportId")
        sport_info = self.SPORT_MAP.get(sport_id)
        if not sport_info:
            return None
        canonical_sport = sport_info[0]

        # Parse start time
        start_time = self._parse_start_time(ev.get("startingOn"))

        # Parse markets
        markets = self._parse_markets(markets_raw, all_selections, home_raw, away_raw, canonical_sport)
        if not markets:
            return None

        home_team = normalize_team_name(home_raw)
        away_team = normalize_team_name(away_raw)
        league = ev.get("leagueName", "")

        return StandardEvent(
            id=f"snabbare_{event_id}",
            name=f"{home_raw} vs {away_raw}",
            provider=self.provider_id,
            sport=canonical_sport,
            league=league,
            home_team=home_team,
            away_team=away_team,
            start_time=start_time.isoformat() if start_time else "",
            markets=markets,
        )

    def _parse_markets(
        self,
        markets_raw: List[Dict],
        all_selections: Dict[str, List[Dict]],
        home_raw: str,
        away_raw: str,
        canonical_sport: str = "",
    ) -> List[Dict]:
        """Parse markets and their selections into standardized format.

        For 1x2/moneyline: keep the appropriate one based on sport.
        For spread/total: store ALL lines — storage pipeline filters to Pinnacle's point.
        """
        markets: List[Dict] = []
        has_1x2 = False
        has_moneyline = False

        for mkt in markets_raw:
            mt_info = mkt.get("marketType", {})
            mt_id = mt_info.get("id")

            market_type = self.MARKET_TYPE_MAP.get(mt_id)
            if not market_type:
                # Fallback: check name patterns
                mt_name = (mt_info.get("originalName", "") or mt_info.get("name", "")).lower()
                market_type = self._classify_market_by_name(mt_name)
                if not market_type and mt_id not in self._logged_unknown_market_ids:
                    self._logged_unknown_market_ids.add(mt_id)
                    logger.debug(
                        f"[{self.provider_id}] Unknown market typeId={mt_id} "
                        f"name='{mt_info.get('originalName', mt_info.get('name', ''))}'"
                    )

            if not market_type:
                continue

            # For winner markets (1x2/moneyline), only keep one
            if market_type == "1x2" and has_1x2:
                continue
            if market_type == "moneyline" and (has_moneyline or has_1x2):
                continue

            market_id = str(mkt.get("id", ""))
            selections = all_selections.get(market_id, [])
            if not selections:
                continue

            outcomes = []
            point_value = None

            for sel in selections:
                odds = sel.get("trueOdds")
                if odds is None:
                    continue
                try:
                    odds_val = float(odds)
                except (ValueError, TypeError):
                    continue
                if odds_val <= 1.0:
                    continue

                # Skip suspended selections
                if sel.get("status", "").lower() == "suspended":
                    continue

                outcome_name = self.OUTCOME_MAP.get(sel.get("outcomeType", ""))
                if not outcome_name:
                    # Try matching by selection name
                    outcome_name = self._match_outcome_by_name(
                        sel.get("name", ""), home_raw, away_raw
                    )
                if not outcome_name:
                    continue

                # Extract point value for spread/total
                points = sel.get("points")
                if points is not None and points != 0.0:
                    try:
                        point_value = float(points)
                    except (ValueError, TypeError):
                        pass

                outcome_dict: Dict[str, Any] = {"name": outcome_name, "odds": odds_val}
                if point_value is not None and market_type in ("spread", "total"):
                    outcome_dict["point"] = point_value
                outcomes.append(outcome_dict)

            if outcomes:
                markets.append({"type": market_type, "outcomes": outcomes})
                if market_type == "1x2":
                    has_1x2 = True
                elif market_type == "moneyline":
                    has_moneyline = True

        # Dedup: if both 1x2 and moneyline present, keep the appropriate one
        types = {m["type"] for m in markets}
        if "1x2" in types and "moneyline" in types:
            # No-draw sports: keep moneyline (2-way), remove 1x2
            # Draw sports (football, rugby, cricket): keep 1x2 (3-way)
            no_draw_sports = {"basketball", "ice_hockey", "tennis", "esports",
                              "mma", "table_tennis", "american_football", "baseball", "handball"}
            if canonical_sport in no_draw_sports:
                markets = [m for m in markets if m["type"] != "1x2"]
            else:
                markets = [m for m in markets if m["type"] != "moneyline"]

        return markets

    def _classify_market_by_name(self, name: str) -> Optional[str]:
        """Fallback: classify market type from name string.

        Enhanced to catch sport-specific market names across football, basketball,
        handball, volleyball, tennis, etc.
        """
        if not name:
            return None
        nl = name.lower()
        # 1x2 (3-way match result)
        if "1x2" in nl:
            return "1x2"
        # Moneyline (2-way winner)
        if any(w in nl for w in ("vinnare", "matchvinnare", "winner", "match result",
                                  "to win", "att vinna", "money line", "moneyline")):
            return "moneyline"
        # Total (over/under)
        if any(w in nl for w in ("över/under", "over/under", "totalt", "total goals",
                                  "total points", "total maps", "total sets",
                                  "total games", "o/u", "antal mål")):
            return "total"
        # Spread (handicap)
        if any(w in nl for w in ("handikapp", "handicap", "spread", "puck line",
                                  "pucklinje", "run line", "asian handicap",
                                  "poänghandikapp", "game handicap")):
            return "spread"
        return None

    def _match_outcome_by_name(
        self, sel_name: str, home_raw: str, away_raw: str
    ) -> Optional[str]:
        """Match selection name to outcome when outcomeType isn't standard."""
        sel_lower = sel_name.lower().strip()
        home_lower = home_raw.lower().strip()
        away_lower = away_raw.lower().strip()

        if sel_lower == home_lower or sel_lower.startswith(home_lower):
            return "home"
        if sel_lower == away_lower or sel_lower.startswith(away_lower):
            return "away"
        if sel_lower in ("draw", "oavgjort", "x"):
            return "draw"
        if "over" in sel_lower or "över" in sel_lower:
            return "over"
        if "under" in sel_lower:
            return "under"
        return None

    def _parse_start_time(self, dt_val: Any) -> Optional[datetime]:
        """Parse ISO datetime string from WS data."""
        if not dt_val:
            return None
        try:
            if isinstance(dt_val, str):
                return datetime.fromisoformat(dt_val.replace("Z", "+00:00"))
            elif isinstance(dt_val, (int, float)):
                ts = dt_val / 1000 if dt_val > 10**10 else dt_val
                return datetime.fromtimestamp(ts)
        except Exception:
            pass
        return None

    def _setup_snabbare_ws(self, page, messages: list) -> None:
        """Setup WS interception that handles both binary and text frames.

        Snabbare may send data as binary (RSocket) or text (JSON) frames.
        The standard RSocketMixin only handles binary — this also captures text.
        """
        ws_count = [0]

        def on_websocket(ws):
            ws_count[0] += 1
            ws_url = ws.url if hasattr(ws, 'url') else 'unknown'
            logger.debug(
                f"[{self.provider_id}] WS #{ws_count[0]} connected: "
                f"{ws_url[:80]}"
            )

            def on_frame_received(payload):
                if isinstance(payload, bytes):
                    decoded = self._decode_rsocket_frame(payload)
                    if decoded:
                        messages.append(decoded)
                elif isinstance(payload, str):
                    # Text frame — try direct JSON parse
                    try:
                        if payload.startswith('[{') or payload.startswith('{"'):
                            data = json.loads(payload)
                            if isinstance(data, list):
                                messages.append(data)
                            elif isinstance(data, dict):
                                messages.append([data])
                    except (json.JSONDecodeError, ValueError):
                        pass

            ws.on("framereceived", on_frame_received)

        page.on("websocket", on_websocket)

    async def _reconnect_browser(self):
        """Kill dead browser and start a fresh one after a crash.

        _ensure_browser() checks `self.page` and returns early if set,
        so we must clear all references first to force a full restart.
        """
        # Clear dead references so _ensure_browser() actually restarts
        try:
            if self.transport.browser:
                await self.transport.browser.close()
        except Exception:
            pass
        self.transport.page = None
        self.transport.context = None
        self.transport.browser = None
        if self.transport.playwright:
            try:
                await self.transport.playwright.stop()
            except Exception:
                pass
            self.transport.playwright = None

        # Start fresh browser
        await self.transport._ensure_browser()
        page = self.transport.page
        self._session_ready = False
        await page.goto(f"{self.site_url}/sv/sportsbook", wait_until="domcontentloaded", timeout=30000)
        await self._handle_cookie_consent(page)
        await self._remove_overlays(page)
        self._session_ready = True
        return page

    async def _handle_cookie_consent(self, page):
        """Handle cookie consent dialogs."""
        selectors = [
            'button:has-text("Acceptera")',
            'button:has-text("Accept")',
            'button:has-text("Godkänn")',
            'button:has-text("OK")',
            '[class*="cookie"] button',
        ]
        for sel in selectors:
            try:
                await page.click(sel, timeout=3000)
                logger.info(f"[{self.provider_id}] Clicked cookie consent")
                await asyncio.sleep(1)
                return
            except Exception:
                continue

    def parse(self, events_data: List[Dict], sport: str) -> List[StandardEvent]:
        """Not used - extract() is overridden."""
        raise NotImplementedError("SnabbareRetriever uses extract() directly")
