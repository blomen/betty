"""
Interwetten Retriever - JSON API extraction via XHR header

Interwetten uses a proprietary platform (Sportsbook Software GmbH).
Adding X-Requested-With: XMLHttpRequest to any page URL returns full JSON
instead of HTML, enabling fast API-style extraction without DOM parsing.

Extraction strategy:
1. Browser session init (headed, for Cloudflare bypass)
2. Discover leagues from sport overview page (DOM links)
3. Fetch each league page via XHR → collect event hrefs
4. Fetch each event detail via XHR → full JSON → parse with interwetten_api_parser
"""

from typing import Dict, Any, List, Optional
import asyncio
import logging
import re
from datetime import datetime, timezone, timedelta

from ..core import StandardEvent
from ..core.browser_retriever import BrowserRetriever
from ..core.transport import BrowserTransport
from .interwetten_api_parser import parse_event_json, parse_top_leagues_response

logger = logging.getLogger(__name__)


class InterwettenRetriever(BrowserRetriever):
    """
    Interwetten JSON API extractor.

    Uses browser session for Cloudflare bypass, then fetches data
    via XHR header to get JSON responses from page URLs.
    """

    XHR_HEADERS = {"X-Requested-With": "XMLHttpRequest"}

    SPORT_OVERVIEW_MAP = {
        "football": (10, "football"),
        "ice_hockey": (40, "ice-hockey"),
        "basketball": (15, "basketball"),
        "tennis": (11, "tennis"),
        "handball": (1002, "handball"),
        "volleyball": (1012, "volleyball"),
        "rugby": (16, "rugby"),
        "cricket": (1027, "cricket"),
        "american_football": (13, "american-football"),
        "baseball": (14, "baseball"),
        "boxing": (90, "boxing"),
        "darts": (42, "darts"),
    }

    def __init__(self, config: Dict[str, Any], transport: Optional[BrowserTransport] = None):
        # Interwetten needs headed browser to bypass Cloudflare
        transport = transport or BrowserTransport(headless=False)
        super().__init__(config, transport=transport)
        self.base_url = config.get("site_url", "https://www.interwetten.se")

    async def extract(self, sport: str, limit: int = 500, **kwargs) -> List[StandardEvent]:
        """
        Extract events via JSON API:
        1. Discover leagues from sport overview page
        2. Fetch league pages via XHR for event hrefs
        3. Fetch each event detail via XHR for full JSON
        """
        await self.transport._ensure_browser()
        page = self.transport.page
        await self._ensure_init(f"{self.base_url}/en/sportsbook", "sportsbook")

        # Discover leagues from sport overview (single page load)
        leagues = await self._discover_leagues_from_overview(page, sport)

        # Filter to target_leagues if provided
        target_leagues = kwargs.get("target_leagues")
        if target_leagues:
            leagues = self._filter_leagues(leagues, target_leagues)

        logger.info(f"[{self.provider_id}] {sport}: discovered {len(leagues)} leagues")

        if not leagues:
            return []

        # Fetch each league page via XHR to get event hrefs
        event_hrefs = await self._collect_event_hrefs(leagues)
        logger.info(f"[{self.provider_id}] {sport}: found {len(event_hrefs)} events across {len(leagues)} leagues")

        # Fetch each event detail via XHR -> full JSON -> parse
        context = self.transport.page.context
        sem = asyncio.Semaphore(10)
        events = []
        errors = 0

        async def fetch_one(href):
            nonlocal errors
            if errors > 50:
                return None
            async with sem:
                try:
                    url = f"{self.base_url}{href}"
                    resp = await asyncio.wait_for(
                        context.request.get(url, headers=self.XHR_HEADERS), timeout=10
                    )
                    if resp.status != 200:
                        return None
                    data = await resp.json()
                    return parse_event_json(data, provider_id=self.provider_id)
                except Exception as e:
                    errors += 1
                    logger.debug(f"[{self.provider_id}] Event {href}: {e}")
                    return None

        tasks = [fetch_one(href) for href in event_hrefs]
        results = await asyncio.gather(*tasks)
        for r in results:
            if r is not None:
                events.append(r)

        logger.info(f"[{self.provider_id}] {sport}: {len(events)} events extracted via JSON API ({errors} errors)")
        return events[:limit] if limit else events

    async def _discover_leagues_from_overview(self, page, sport: str) -> List[dict]:
        """Navigate to sport overview page and extract league links from DOM."""
        sport_info = self.SPORT_OVERVIEW_MAP.get(sport)
        if not sport_info:
            logger.warning(f"[{self.provider_id}] No sport overview mapping for {sport}")
            return []
        sport_id, slug = sport_info
        url = f"{self.base_url}/en/sportsbook/o/{sport_id}/{slug}"
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=15000)
            try:
                await page.wait_for_selector('a[href*="/l/"]', timeout=5000)
            except Exception:
                logger.debug(f"[{self.provider_id}] No league links found for {sport}")
                return []
            # Click "Show more" if present
            try:
                show_more = await page.query_selector('button:has-text("Show more"), a:has-text("Show more"), button:has-text("Visa fler")')
                if show_more:
                    await show_more.click()
                    await page.wait_for_timeout(500)
            except Exception:
                pass
            leagues = await page.evaluate("""() => {
                return Array.from(document.querySelectorAll('a[href*="/l/"]'))
                    .map(a => {
                        const m = a.href.match(/\\/l\\/(\\d+)\\/(.+?)(?:\\/|$|\\?)/);
                        return m ? {id: parseInt(m[1]), slug: m[2], name: a.textContent.trim()} : null;
                    })
                    .filter(Boolean)
                    .filter((v, i, a) => a.findIndex(x => x.id === v.id) === i);
            }""")
            return leagues or []
        except Exception as e:
            logger.error(f"[{self.provider_id}] Failed to discover leagues for {sport}: {e}")
            return []

    def _filter_leagues(self, leagues: List[dict], target_leagues: set) -> List[dict]:
        """Filter discovered leagues to match target_leagues names."""
        target_lower = {t.lower() for t in target_leagues}
        matched = []
        for league in leagues:
            league_name = league.get("name", "").lower()
            if any(target in league_name or league_name in target for target in target_lower):
                matched.append(league)
        if not matched:
            logger.info(f"[{self.provider_id}] No leagues matched target_leagues, using top {min(20, len(leagues))}")
            return leagues[:20]
        return matched

    async def _collect_event_hrefs(self, leagues: List[dict]) -> List[str]:
        """Fetch each league page via XHR and collect unique event hrefs."""
        context = self.transport.page.context
        sem = asyncio.Semaphore(10)
        all_hrefs = []

        async def fetch_league(league):
            async with sem:
                url = f"{self.base_url}/en/sportsbook/l/{league['id']}/{league['slug']}"
                try:
                    resp = await asyncio.wait_for(
                        context.request.get(url, headers=self.XHR_HEADERS), timeout=10
                    )
                    if resp.status != 200:
                        return []
                    data = await resp.json()
                    events = data.get("events", [])
                    return [e.get("href", "") for e in events if e.get("href")]
                except Exception as e:
                    logger.debug(f"[{self.provider_id}] League {league.get('slug')}: {e}")
                    return []

        tasks = [fetch_league(lg) for lg in leagues]
        results = await asyncio.gather(*tasks)
        for hrefs in results:
            all_hrefs.extend(hrefs)
        return list(dict.fromkeys(all_hrefs))

    def _parse_datetime_str(self, dt_str: str) -> Optional[datetime]:
        """Parse interwetten datetime string like '15.03. - 15:00' into UTC datetime."""
        m = re.search(r'(\d{1,2})\.(\d{1,2})\.\s*-\s*(\d{1,2}):(\d{2})', dt_str)
        if not m:
            return None
        try:
            day, month = int(m.group(1)), int(m.group(2))
            hour, minute = int(m.group(3)), int(m.group(4))
            now = datetime.now(timezone.utc)
            year = now.year
            if now.month >= 11 and month <= 2:
                year += 1
            return datetime(
                year, month, day, hour, minute, 0,
                tzinfo=timezone(timedelta(hours=1)),  # CET
            ).astimezone(timezone.utc)
        except (ValueError, TypeError):
            return None

    def parse(self, data: Any, sport: str) -> List[StandardEvent]:
        """Not used — browser-based extraction."""
        return []
