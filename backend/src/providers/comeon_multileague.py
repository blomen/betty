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
    filter to Pinnacle-matched leagues → scrape each league page with
    concurrent tabs → parse 1x2/spread/total from market tabs.
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

    MAX_CONCURRENT_LEAGUES = 8

    def __init__(self, config: Dict[str, Any], transport: Optional[BrowserTransport] = None):
        super().__init__(config, transport)
        raw_site_url = config.get("site_url", f"https://www.{config.get('domain')}")
        self.site_url: str = raw_site_url.rstrip("/")
        self._concurrent_leagues = config.get("concurrent_leagues", self.MAX_CONCURRENT_LEAGUES)

    def parse(self, data: Any, sport: str) -> List[StandardEvent]:
        raise NotImplementedError("ComeOnMultiLeagueRetriever uses extract() directly")

    async def extract(self, sport: str | List[str], limit: Optional[int] = None, **kwargs) -> List[StandardEvent]:
        """Extract events from one or more sports via league page DOM scraping."""
        target_leagues: Optional[Set[str]] = kwargs.get("target_leagues")
        sports_to_extract = self._resolve_sports(sport)
        logger.debug(f"[{self.provider_id}] Extracting {len(sports_to_extract)} sports: {', '.join(sports_to_extract)}")

        await self.transport._ensure_browser()
        page = self.transport.page

        await self._dismiss_cookie_overlay(page)
        self._cookie_dismissed = True

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

        if not all_events and sports_attempted >= 3:
            raise RetryableError(
                f"0 events from {sports_attempted} sports — possible page/SPA failure",
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

        page = self.transport.page

        # Validate page is still alive
        try:
            await page.evaluate("() => true", timeout=5000)
        except Exception:
            logger.warning(f"[{self.provider_id}] Page context dead, creating new page")
            try:
                page = await self.transport.context.new_page()
                self.transport.page = page
            except Exception:
                logger.warning(f"[{self.provider_id}] Context dead, full browser reinit")
                await self.transport.close()
                await self.transport._ensure_browser()
                page = self.transport.page
                self._cookie_dismissed = False

        # Step 1: Navigate to league directory
        leagues_url = f"{self.site_url}/sv{sport_path}/leagues"
        try:
            await page.goto(leagues_url, wait_until='domcontentloaded', timeout=15000)
            await asyncio.sleep(1.5)
        except Exception as e:
            logger.error(f"[{self.provider_id}] Failed to load leagues directory for {sport_normalized}: {e}")
            return []

        if not getattr(self, '_cookie_dismissed', False):
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

        # Step 3: Expand all country accordions
        try:
            expanded = await page.evaluate(JS.JS_EXPAND_ALL_COUNTRIES)
            if expanded > 0:
                await asyncio.sleep(0.5)
                logger.debug(f"[{self.provider_id}] {sport_normalized}: expanded {expanded} countries")
        except Exception as e:
            logger.debug(f"[{self.provider_id}] Country expansion failed: {e}")

        # Step 4: Collect all league URLs
        all_leagues = await page.evaluate(JS.JS_COLLECT_LEAGUE_URLS)
        if not all_leagues:
            logger.warning(f"[{self.provider_id}] {sport_normalized}: no leagues found")
            return []

        logger.debug(f"[{self.provider_id}] {sport_normalized}: discovered {len(all_leagues)} leagues")

        # Step 5: Filter leagues
        filtered_leagues = self._filter_leagues(all_leagues, target_leagues, sport_normalized)
        logger.info(
            f"[{self.provider_id}] {sport_normalized}: "
            f"scraping {len(filtered_leagues)}/{len(all_leagues)} leagues"
        )

        # Step 6: Scrape league pages concurrently
        semaphore = asyncio.Semaphore(self._concurrent_leagues)
        all_events = []

        async def scrape_with_semaphore(league_info):
            async with semaphore:
                league_page = await self.transport.context.new_page()
                try:
                    events = await scrape_league_page(
                        page=league_page,
                        league_href=league_info["href"],
                        site_url=self.site_url,
                        sport=sport_normalized,
                        league_name=league_info["name"],
                        provider_id=self.provider_id,
                    )
                    return events
                except Exception as e:
                    logger.debug(f"[{self.provider_id}] {league_info['name']}: scrape failed: {e}")
                    return []
                finally:
                    try:
                        await league_page.close()
                    except Exception:
                        pass

        tasks = [scrape_with_semaphore(league) for league in filtered_leagues]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for result in results:
            if isinstance(result, list):
                all_events.extend(result)
            elif isinstance(result, Exception):
                logger.debug(f"[{self.provider_id}] League scrape exception: {result}")

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
