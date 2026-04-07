"""
ComeOn DOM-Based League Retriever

Extracts events by navigating individual league pages and scraping odds
from the rendered DOM. Market tabs (1x2, Handikapp, Over/Under) provide
all three market types without needing event detail page enrichment.

Replaces the previous WS-based approach which had ~70% reliability
and only captured ~3-5% of football events.
"""

from typing import Dict, Any, List, Optional, Set
import asyncio
import logging
import time

from ..core import BrowserRetriever, BrowserTransport, StandardEvent
from ..core.exceptions import RetryableError
from ..core.transport import get_proxy_dict
from ..matching.normalizer import normalize_team_name
from . import comeon_dom_js as JS
from .comeon_dom_parser import scrape_league_page

logger = logging.getLogger(__name__)


class ComeOnMultiLeagueRetriever(BrowserRetriever):
    """
    DOM-based ComeOn retriever.

    Strategy: Navigate to sport league directory → discover leagues →
    filter to Pinnacle-matched leagues → scrape each league page
    sequentially on main page → parse 1x2/spread/total from market tabs.
    """

    SPORT_URL_MAP = {
        'football': '/sportsbook/sport/1-fotboll',
        'basketball': '/sportsbook/sport/2-basket',
        'american_football': '/sportsbook/sport/3-amerikansk-fotboll',
        'ice_hockey': '/sportsbook/sport/4-ishockey',
        'tennis': '/sportsbook/sport/6-tennis',
        'mma': '/sportsbook/sport/7-mma',
        'esports': '/sportsbook/sport/130-esport',
        'baseball': '/sportsbook/sport/12-baseboll',
        'handball': '/sportsbook/sport/10-handboll',
        'rugby': '/sportsbook/sport/16-rugby',
        'cricket': '/sportsbook/sport/17-cricket',
        'table_tennis': '/sportsbook/sport/26-bordtennis',
    }

    # Sport key → ComeOn API sport ID (extracted from SPORT_URL_MAP paths)
    SPORT_API_IDS = {
        'football': 1, 'basketball': 2, 'american_football': 3,
        'ice_hockey': 4, 'tennis': 6, 'mma': 7, 'handball': 10,
        'baseball': 12, 'rugby': 16, 'cricket': 17, 'table_tennis': 26,
        'esports': 130,
    }

    API_BASE = "/sportsbook-api/api"
    API_PARAMS = "franchiseCode=SWEDEN_COMEON&locale=sv"

    # Sports ordered by extraction speed (fastest first).
    # Tennis/handball/mma complete in <60s. Football/basketball often timeout at 360s.
    # Extracting fast sports first ensures we get data before provider timeout hits.
    SPORT_PRIORITY = [
        'mma', 'handball', 'tennis', 'esports',
        'ice_hockey', 'basketball', 'football',
    ]

    _camoufox_unavailable = False  # Class-level flag to avoid repeated ImportError

    def __init__(self, config: Dict[str, Any], transport: Optional[BrowserTransport] = None):
        super().__init__(config, transport)
        raw_site_url = config.get("site_url", f"https://www.{config.get('domain')}")
        self.site_url: str = raw_site_url.rstrip("/")
        self._camoufox_browser = None
        self._camoufox_page = None

    # ------------------------------------------------------------------
    # Camoufox anti-detect browser (Cloudflare bypass)
    # ------------------------------------------------------------------

    async def _ensure_camoufox(self):
        """Launch Camoufox anti-detect browser if not already running."""
        if self._camoufox_page is not None:
            try:
                await self._camoufox_page.evaluate("() => true", timeout=5000)
                return self._camoufox_page
            except Exception:
                logger.warning(f"[{self.provider_id}] Camoufox page died, recovering...")
                if self._camoufox_browser:
                    try:
                        self._camoufox_page = await self._camoufox_browser.new_page()
                        self._warmed_up = False  # Force re-warmup on new page
                        self._cookie_dismissed = False
                        logger.info(f"[{self.provider_id}] Recovered with new page (browser alive)")
                        return self._camoufox_page
                    except Exception:
                        logger.warning(f"[{self.provider_id}] Browser also dead, full relaunch")
                await self._cleanup_camoufox()

        if ComeOnMultiLeagueRetriever._camoufox_unavailable:
            return None

        try:
            from camoufox.async_api import AsyncCamoufox
        except ImportError:
            ComeOnMultiLeagueRetriever._camoufox_unavailable = True
            logger.error(
                f"[{self.provider_id}] camoufox not installed. "
                f"Install with: pip install camoufox[geoip] && python -m camoufox fetch"
            )
            return None

        logger.info(f"[{self.provider_id}] Launching Camoufox anti-detect browser...")
        t0 = time.time()
        try:
            proxy = get_proxy_dict()
            self._camoufox_browser = await AsyncCamoufox(
                headless=True,
                geoip=False,
                humanize=0.2,
                os="windows",
                proxy=proxy,
            ).__aenter__()

            self._camoufox_page = await self._camoufox_browser.new_page()
            proxy_msg = " with residential proxy" if proxy else ""
            logger.info(f"[{self.provider_id}] Camoufox browser ready{proxy_msg} in {time.time()-t0:.1f}s")
            return self._camoufox_page
        except Exception as e:
            logger.error(f"[{self.provider_id}] Failed to launch Camoufox: {e}")
            self._camoufox_browser = None
            self._camoufox_page = None
            return None

    async def _cleanup_camoufox(self):
        """Close camoufox browser."""
        if self._camoufox_browser:
            try:
                await self._camoufox_browser.__aexit__(None, None, None)
            except (Exception, OSError, ValueError):
                pass
            finally:
                self._camoufox_browser = None
                self._camoufox_page = None

    async def _get_page(self):
        """Get a browser page — Camoufox for Cloudflare bypass, Playwright fallback."""
        page = await self._ensure_camoufox()
        if page:
            return page

        # Fallback to regular Playwright transport
        try:
            await self.transport._ensure_browser()
            return self.transport.page
        except Exception as e:
            logger.warning(f"[{self.provider_id}] Playwright fallback failed: {e}")

        return None

    def parse(self, data: Any, sport: str) -> List[StandardEvent]:
        raise NotImplementedError("ComeOnMultiLeagueRetriever uses extract() directly")

    async def extract(self, sport: str | List[str], limit: Optional[int] = None, **kwargs) -> List[StandardEvent]:
        """Extract events from one or more sports via league page DOM scraping."""
        target_leagues: Optional[Set[str]] = kwargs.get("target_leagues")
        sports_to_extract = self._resolve_sports(sport)
        logger.debug(f"[{self.provider_id}] Extracting {len(sports_to_extract)} sports: {', '.join(sports_to_extract)}")

        page = await self._get_page()
        if not page:
            raise RetryableError(
                "No browser available (camoufox not installed?)",
                provider_id=self.provider_id,
            )
        self._page = page
        self._cookie_dismissed = False

        # Warm up: load homepage to pass Cloudflare + dismiss cookies
        if self._camoufox_page and not getattr(self, '_warmed_up', False):
            try:
                await page.goto(f"{self.site_url}/sv", wait_until='domcontentloaded', timeout=30000)
                await asyncio.sleep(3)
                await self._dismiss_cookie_overlay(page)
                self._warmed_up = True
                logger.info(f"[{self.provider_id}] Camoufox session warmed up")
            except Exception as e:
                logger.warning(f"[{self.provider_id}] Warm-up failed: {e}")
                # Proxy/network error — kill browser so next run gets a fresh one
                if "NS_ERROR" in str(e) or "PROXY" in str(e) or "net::" in str(e):
                    logger.warning(f"[{self.provider_id}] Network error on warm-up — restarting browser")
                    await self._cleanup_camoufox()
                    raise RetryableError(
                        f"Warm-up network error: {e}",
                        provider_id=self.provider_id,
                    )

        all_events = []
        sports_attempted = 0
        provider_start = time.time()

        # Sort sports by priority (fast sports first, heavy sports last)
        sports_to_extract = sorted(
            sports_to_extract,
            key=lambda s: self.SPORT_PRIORITY.index(s) if s in self.SPORT_PRIORITY else 99,
        )

        for sport_idx, sport_key in enumerate(sports_to_extract):

            # Proactively recycle the Camoufox page between sports.
            # After 20+ page.goto() calls per sport, the page accumulates SPA state
            # and memory until it crashes. Recycling prevents the crash entirely
            # and keeps the API league discovery working (faster than DOM fallback).
            if sport_idx > 0 and self._camoufox_browser:
                # Close old page (may already be dead — that's fine)
                if self._camoufox_page:
                    try:
                        await self._camoufox_page.close()
                    except Exception:
                        pass
                    self._camoufox_page = None

                # Create fresh page from existing browser
                try:
                    self._camoufox_page = await self._camoufox_browser.new_page()
                    self._page = self._camoufox_page
                    # Re-warm: visit homepage to pass Cloudflare + set cookies
                    await self._page.goto(f"{self.site_url}/sv", wait_until='domcontentloaded', timeout=30000)
                    await asyncio.sleep(2)
                    await self._dismiss_cookie_overlay(self._page)
                    self._cookie_dismissed = True
                    logger.debug(f"[{self.provider_id}] Recycled page before {sport_key}")
                except Exception as e:
                    logger.warning(f"[{self.provider_id}] Page recycle failed ({e}), full relaunch...")
                    await self._cleanup_camoufox()
                    page = await self._ensure_camoufox()
                    if page:
                        self._page = page
                        try:
                            await page.goto(f"{self.site_url}/sv", wait_until='domcontentloaded', timeout=30000)
                            await asyncio.sleep(2)
                            await self._dismiss_cookie_overlay(page)
                            self._warmed_up = True
                            self._cookie_dismissed = True
                        except Exception:
                            pass
                    else:
                        logger.error(f"[{self.provider_id}] Page recovery failed, stopping extraction")
                        break

            try:
                sports_attempted += 1
                sport_events = await self._extract_single_sport(
                    sport_key, target_leagues=target_leagues, limit=limit
                )
                logger.debug(f"[{self.provider_id}] {sport_key}: {len(sport_events)} events")
                all_events.extend(sport_events)
            except Exception as e:
                logger.error(f"[{self.provider_id}] Failed to extract {sport_key}: {e}")

        if not all_events:
            raise RetryableError(
                f"0 events from {sports_attempted} sport(s) — possible page/SPA failure",
                provider_id=self.provider_id,
            )

        return all_events

    def _resolve_sports(self, sport: str | List[str]) -> List[str]:
        if isinstance(sport, list):
            return sport
        if sport == "all":
            return list(self.SPORT_URL_MAP.keys())
        return [sport.split('/')[0] if '/' in sport else sport]

    async def _dismiss_cookie_overlay(self, page) -> None:
        """Dismiss OneTrust cookie consent overlay."""
        try:
            btn = await page.query_selector('#onetrust-accept-btn-handler')
            if btn:
                await btn.click()
                await page.wait_for_load_state('domcontentloaded', timeout=5000)
                await page.wait_for_timeout(1000)
        except Exception:
            pass
        try:
            await page.evaluate('''() => {
                const filter = document.querySelector('.onetrust-pc-dark-filter');
                if (filter) filter.remove();
                const sdk = document.querySelector('#onetrust-consent-sdk');
                if (sdk) sdk.remove();
            }''')
        except Exception:
            pass

    # Max leagues to scrape per sport — prevents football (60+ leagues) from timing out.
    # Sorted by eventCount (highest first) so we get the most valuable leagues.
    SPORT_LEAGUE_CAPS: Dict[str, int] = {
        "football": 60,     # No provider timeout — extract all valuable leagues
        "basketball": 40,
        "ice_hockey": 30,
        "tennis": 30,
    }
    DEFAULT_LEAGUE_CAP = 15

    async def _discover_leagues_via_api(self, page, sport: str) -> list[dict]:
        """Discover leagues using the REST API instead of accordion expansion.

        Returns leagues in the same format as JS_COLLECT_LEAGUE_URLS:
        [{id: int, name: str, href: str}, ...] sorted by eventCount descending.
        """
        sport_id = self.SPORT_API_IDS.get(sport)
        if not sport_id:
            return []

        url = f"{self.site_url}{self.API_BASE}/leagues?sportId={sport_id}&{self.API_PARAMS}"
        try:
            leagues_raw = await asyncio.wait_for(page.evaluate(f"""async () => {{
                const resp = await fetch('{url}');
                if (!resp.ok) return null;
                return await resp.json();
            }}"""), timeout=10)
        except Exception as e:
            logger.warning(f"[{self.provider_id}] {sport}: API league discovery failed: {e}")
            return []

        if not leagues_raw or not isinstance(leagues_raw, list):
            return []

        # Convert API format to the format expected by scrape_league_page:
        # API returns: {id, name, sportId, externalId, eventCount, ...}
        # We need:     {id, name, href}  where href is the league page URL path
        # The DOM uses the API's `id` field (not externalId) in league page URLs.
        leagues = []
        for lg in leagues_raw:
            event_count = lg.get("eventCount", 0)
            if event_count <= 0:
                continue  # Skip empty leagues

            league_id = lg.get("id")
            name = lg.get("name", "")
            # Slugify: "Premier League" → "premier-league"
            slug = name.lower().replace(" ", "-").replace("/", "-").replace(",", "")
            slug = "".join(c for c in slug if c.isalnum() or c == "-")
            # Collapse multiple dashes
            while "--" in slug:
                slug = slug.replace("--", "-")
            slug = slug.strip("-")

            # Get the sport slug from SPORT_URL_MAP (e.g., "1-fotboll")
            sport_slug = self.SPORT_URL_MAP.get(sport, "").split("/")[-1]

            leagues.append({
                "id": league_id,
                "name": name,
                "href": f"/sv/sportsbook/sport/{sport_slug}/leagues/{league_id}-{slug}",
                "eventCount": event_count,
            })

        # Sort by event count (highest first) — most valuable leagues first
        leagues.sort(key=lambda x: x["eventCount"], reverse=True)

        logger.info(
            f"[{self.provider_id}] {sport}: API discovered {len(leagues)} leagues "
            f"with events (from {len(leagues_raw)} total)"
        )
        return leagues

    async def _discover_leagues_via_dom(self, page, sport: str, sport_path: str) -> list[dict]:
        """Fallback: discover leagues via DOM accordion expansion (slow)."""
        leagues_url = f"{self.site_url}/sv{sport_path}/leagues"
        try:
            await page.goto(leagues_url, wait_until='domcontentloaded', timeout=30000)
            await asyncio.sleep(3)
        except Exception as e:
            logger.error(f"[{self.provider_id}] Failed to load leagues directory for {sport}: {e}")
            return []

        if not self._cookie_dismissed:
            await self._dismiss_cookie_overlay(page)
            self._cookie_dismissed = True

        # Click "Alla ligor" tab
        try:
            await page.evaluate("""() => {
                const tabs = document.querySelectorAll('[role="tab"]');
                for (const tab of tabs) {
                    if (tab.textContent.trim().toLowerCase().includes('alla ligor')) {
                        tab.click();
                        return true;
                    }
                }
                return false;
            }""")
            await asyncio.sleep(0.5)
        except Exception:
            pass

        # Collect popular leagues
        all_leagues = await page.evaluate(JS.JS_COLLECT_LEAGUE_URLS)
        popular_count = len(all_leagues) if all_leagues else 0

        # Expand country accordions
        try:
            country_count = await page.evaluate(JS.JS_GET_COUNTRY_COUNT)
            if country_count > 0:
                logger.info(
                    f"[{self.provider_id}] {sport}: "
                    f"expanding {country_count} countries sequentially"
                )
                for i in range(country_count):
                    clicked = await page.evaluate(JS.JS_CLICK_COUNTRY_AT_INDEX, i)
                    if clicked:
                        await asyncio.sleep(0.3)
                        country_leagues = await page.evaluate(JS.JS_COLLECT_LEAGUE_URLS)
                        existing_ids = {lg["id"] for lg in all_leagues}
                        for lg in country_leagues:
                            if lg["id"] not in existing_ids:
                                all_leagues.append(lg)
                                existing_ids.add(lg["id"])
        except Exception as e:
            logger.debug(f"[{self.provider_id}] Country expansion failed: {e}")

        logger.info(
            f"[{self.provider_id}] {sport}: DOM discovered {len(all_leagues)} leagues "
            f"({popular_count} popular + {len(all_leagues) - popular_count} from countries)"
        )
        return all_leagues or []

    async def _extract_single_sport(
        self,
        sport: str,
        target_leagues: Optional[Set[str]] = None,
        limit: Optional[int] = None,
    ) -> List[StandardEvent]:
        """Extract events for a single sport via league page DOM scraping."""
        sport_normalized = sport.split('/')[0] if '/' in sport else sport
        sport_path = self.SPORT_URL_MAP.get(sport_normalized)
        if not sport_path:
            logger.warning(f"[{self.provider_id}] Sport '{sport_normalized}' not supported")
            return []

        page = self._page

        if not self._cookie_dismissed:
            await self._dismiss_cookie_overlay(page)
            self._cookie_dismissed = True

        # Step 1: Discover leagues via REST API (fast, ~1s)
        # Falls back to DOM accordion expansion if API fails
        t_discovery = time.time()
        all_leagues = await self._discover_leagues_via_api(page, sport_normalized)
        if not all_leagues:
            logger.info(f"[{self.provider_id}] {sport_normalized}: API discovery failed, falling back to DOM")
            all_leagues = await self._discover_leagues_via_dom(page, sport_normalized, sport_path)

        if not all_leagues:
            logger.warning(f"[{self.provider_id}] {sport_normalized}: no leagues found")
            return []

        logger.debug(
            f"[{self.provider_id}] {sport_normalized}: "
            f"discovery took {time.time() - t_discovery:.1f}s"
        )

        # Step 2: Filter leagues and enforce per-sport cap
        filtered_leagues = self._filter_leagues(all_leagues, target_leagues, sport_normalized)
        league_cap = self.SPORT_LEAGUE_CAPS.get(sport_normalized, self.DEFAULT_LEAGUE_CAP)
        if len(filtered_leagues) > league_cap:
            logger.info(
                f"[{self.provider_id}] {sport_normalized}: capping from "
                f"{len(filtered_leagues)} to {league_cap} leagues"
            )
            filtered_leagues = filtered_leagues[:league_cap]

        logger.info(
            f"[{self.provider_id}] {sport_normalized}: "
            f"scraping {len(filtered_leagues)}/{len(all_leagues)} leagues"
        )

        # Step 6: Scrape league pages sequentially on main page
        # ComeOn's SPA only renders fully on the active page — new tabs
        # don't hydrate the React app reliably. Sequential is required.
        all_events = []
        sport_timeout = self.config.get("sport_timeout", 360)
        sport_start = time.time()
        leagues_scraped = 0

        for league_info in filtered_leagues:

            try:
                events = await scrape_league_page(
                    page=page,
                    league_href=league_info["href"],
                    site_url=self.site_url,
                    sport=sport_normalized,
                    league_name=league_info["name"],
                    provider_id=self.provider_id,
                )
                all_events.extend(events)
                leagues_scraped += 1
            except Exception as e:
                leagues_scraped += 1
                logger.debug(f"[{self.provider_id}] {league_info['name']}: scrape failed: {e}")

        logger.info(
            f"[{self.provider_id}] {sport_normalized}: "
            f"{len(all_events)} events from {leagues_scraped}/{len(filtered_leagues)} leagues "
            f"in {time.time() - sport_start:.0f}s"
        )
        return all_events

    def _filter_leagues(
        self,
        all_leagues: list[dict],
        target_leagues: Optional[Set[str]],
        sport: str,
    ) -> list[dict]:
        """Filter discovered leagues to those with Pinnacle coverage.

        Uses the same target_leagues set that Kambi uses — fuzzy substring
        matching of ComeOn's Swedish league names against Pinnacle league names.

        Falls back to top leagues by event count if no target_leagues provided.
        The league cap from SPORT_LEAGUE_CAPS is applied by the caller.
        """
        if not target_leagues:
            return all_leagues  # Already sorted by eventCount from API discovery

        filtered = []
        for league in all_leagues:
            league_name = league["name"].lower().strip()
            for target in target_leagues:
                if target in league_name or league_name in target:
                    filtered.append(league)
                    break
                # Also try stripping the first word (country name)
                parts = league_name.split(" ", 1)
                if len(parts) > 1 and (target in parts[1] or parts[1] in target):
                    filtered.append(league)
                    break

        if not filtered:
            logger.debug(
                f"[{self.provider_id}] {sport}: league filter matched 0/{len(all_leagues)}, "
                f"using all (cap applied by caller)"
            )
            return all_leagues

        return filtered
