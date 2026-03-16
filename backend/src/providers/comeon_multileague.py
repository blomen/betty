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
            self._camoufox_browser = await AsyncCamoufox(
                headless=True,
                geoip=True,
                humanize=0.2,
                os="windows",
            ).__aenter__()

            self._camoufox_page = await self._camoufox_browser.new_page()
            logger.info(f"[{self.provider_id}] Camoufox browser ready in {time.time()-t0:.1f}s")
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

        all_events = []
        sports_attempted = 0

        for sport_key in sports_to_extract:
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

        # Step 1: Navigate to league directory
        leagues_url = f"{self.site_url}/sv{sport_path}/leagues"
        try:
            await page.goto(leagues_url, wait_until='domcontentloaded', timeout=15000)
            await asyncio.sleep(3)  # SPA needs time to render league list
        except Exception as e:
            logger.error(f"[{self.provider_id}] Failed to load leagues directory for {sport_normalized}: {e}")
            return []

        if not self._cookie_dismissed:
            await self._dismiss_cookie_overlay(page)
            self._cookie_dismissed = True

        # Step 2: Click "Alla ligor" tab if not already active
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

        # Step 3: Collect league URLs from popular section first
        all_leagues = await page.evaluate(JS.JS_COLLECT_LEAGUE_URLS)
        popular_count = len(all_leagues) if all_leagues else 0

        # Step 4: Expand country accordions ONE AT A TIME (mutually exclusive)
        try:
            country_count = await page.evaluate(JS.JS_GET_COUNTRY_COUNT)
            if country_count > 0:
                logger.debug(
                    f"[{self.provider_id}] {sport_normalized}: "
                    f"expanding {country_count} countries sequentially"
                )
                for i in range(country_count):
                    clicked = await page.evaluate(JS.JS_CLICK_COUNTRY_AT_INDEX, i)
                    if clicked:
                        await asyncio.sleep(0.3)
                        country_leagues = await page.evaluate(JS.JS_COLLECT_LEAGUE_URLS)
                        # Merge new leagues (JS deduplicates by ID via seen Set)
                        existing_ids = {lg["id"] for lg in all_leagues}
                        for lg in country_leagues:
                            if lg["id"] not in existing_ids:
                                all_leagues.append(lg)
                                existing_ids.add(lg["id"])
        except Exception as e:
            logger.debug(f"[{self.provider_id}] Country expansion failed: {e}")

        if not all_leagues:
            logger.warning(f"[{self.provider_id}] {sport_normalized}: no leagues found")
            return []

        logger.debug(
            f"[{self.provider_id}] {sport_normalized}: "
            f"discovered {len(all_leagues)} leagues ({popular_count} popular + "
            f"{len(all_leagues) - popular_count} from countries)"
        )

        # Step 5: Filter leagues
        filtered_leagues = self._filter_leagues(all_leagues, target_leagues, sport_normalized)
        logger.info(
            f"[{self.provider_id}] {sport_normalized}: "
            f"scraping {len(filtered_leagues)}/{len(all_leagues)} leagues"
        )

        # Step 6: Scrape league pages sequentially on main page
        # ComeOn's SPA only renders fully on the active page — new tabs
        # don't hydrate the React app reliably. Sequential is required.
        all_events = []

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
            except Exception as e:
                logger.debug(f"[{self.provider_id}] {league_info['name']}: scrape failed: {e}")

        logger.info(
            f"[{self.provider_id}] {sport_normalized}: "
            f"{len(all_events)} events from {len(filtered_leagues)} leagues"
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

        Falls back to popular leagues (first 10) if no target_leagues provided.
        """
        if not target_leagues:
            return all_leagues[:10]

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
                f"using top 10"
            )
            return all_leagues[:10]

        return filtered
