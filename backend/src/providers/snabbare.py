from typing import List, Any, Optional, Dict, Set
import logging
import asyncio
import json
import re
from datetime import datetime
from ..core import BrowserRetriever, StandardEvent, BrowserTransport
from ..matching.normalizer import normalize_team_name

logger = logging.getLogger(__name__)

class SnabbareRetriever(BrowserRetriever):
    """
    Retriever for Snabbare (Sportradar MTS).
    Uses BrowserTransport to bypass protection and query the internal API.
    """

    # Mapping for sports to internal IDs or slugs if needed
    # Usually MTS uses integer IDs but Snabbare API might use slugs or keys
    SPORT_IDS = {
        "football": 1,
        "basketball": 2,
        "ice_hockey": 4,
        "tennis": 6,
        "american_football": 3,
        "baseball": 12,
        "cricket": 17,
        "rugby": 5,
        "esports": 130,
        "mma": 37,
        "boxing": 31,
        "motorsports": 65,
    }

    def __init__(self, config: Dict[str, Any], transport: Optional[BrowserTransport] = None):
        super().__init__(config, transport)

        # Base is .../api (v2 is part of path for some, but not all)
        self.api_base = config.get("api_base", "https://www.snabbare.com/sportsbook-api/api")
        self.site_url = config.get("site_url", "https://www.snabbare.com")
        self.default_params = {
            "franchiseCode": "SWEDEN_SNABBARE",
            "locale": "sv"
        }

    async def extract(self, sport: str, limit: int = 1000) -> List[StandardEvent]:
        # Initialize session by visiting the odds page
        await self._ensure_init(url=f"{self.site_url}/sv/odds", page_key="odds_page")
        all_events = []
        
        sport_id = self.SPORT_IDS.get(sport)
        if not sport_id:
            logger.warning(f"[{self.provider_id}] Unknown sport ID for {sport}")
            return []

        # 1. Fetch leagues to get IDs
        leagues_url = f"{self.api_base}/v2/leagues"
        params = self.default_params.copy()
        params.update({
            "filter.sportId": sport_id,
            "page": 1,
            "pageSize": 50
        })
        
        target_leagues = []
        try:
            r = await self.transport.get(leagues_url, params=params)
            # Handle possible response formats (list or dict with data)
            items = []
            if isinstance(r, list):
                items = r
            elif isinstance(r, dict) and 'data' in r:
                items = r['data']
            elif isinstance(r, dict) and 'leagues' in r:
                items = r['leagues']
                
            # Filter for leagues with events or specific interesting ones
            for l in items:
                lname = l.get('name', '')
                lid = l.get('_id') or l.get('id') or l.get('entityCode')
                ec = l.get('eventCount', 0)
                
                # Heuristic: Prioritize leagues with > 0 events
                if lid and ec > 0:
                    target_leagues.append({'name': lname, 'id': lid})
            
            logger.info(f"[{self.provider_id}] Found {len(target_leagues)} active leagues for {sport}")
                
        except Exception as e:
            logger.error(f"Failed to fetch leagues list: {e}")
            return []

        # 2. Scrape each league concurrently
        unique_ids = set()

        # Limit concurrency (increased from 5 to 10 for better performance)
        sem = asyncio.Semaphore(10) # 10 parallel tabs
        
        async def process_league_task(league):
            async with sem:
                return await self._process_league(league, sport)

        tasks = [process_league_task(l) for l in target_leagues]
        results = await asyncio.gather(*tasks)
        
        for res in results:
            for ev in res:
                if len(all_events) >= limit: break
                if ev.id not in unique_ids:
                    all_events.append(ev)
                    unique_ids.add(ev.id)
        
        logger.info(f"Returning {len(all_events)} events for {sport}")
        return all_events

    async def _process_league(self, league: Dict, sport: str) -> List[StandardEvent]:
        lid = league['id']
        lname = league['name']
        url = f"{self.site_url}/sv/sportsbook/leagues/{lid}"
        events = []

        # Ensure browser context is ready before creating new page
        try:
            await self.transport._ensure_browser()
        except Exception as e:
            logger.error(f"[{self.provider_id}] Failed to ensure browser for {lname}: {e}")
            return []

        page = await self.transport.new_page()
        
        try:
            logger.info(f"Navigating to {lname} ({url})")
            await page.goto(url, wait_until="domcontentloaded", timeout=60000)
            
            # Quick check for empty state before scrolling
            empty_indicators = await page.query_selector_all('text=/Inga matcher|Inga spel|No matches|No events/i')
            if empty_indicators:
                logger.debug(f"[{self.provider_id}] No matches indicator found for {lname}, skipping")
                return []

            # Scroll down to trigger lazy loading (reduced timeout)
            xpath = "//button[contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'visa mer') or contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'visa fler')]"
            await self.transport.smart_scroll(timeout=30000, button_selector=f"xpath={xpath}", page=page)

            # Wait for match cards (reduced timeout from 15s to 5s)
            try:
                await page.wait_for_selector('[data-at="game-card"]', timeout=5000)
            except:
                # Double-check for empty state after scroll
                empty_check = await page.query_selector_all('text=/Inga matcher|Inga spel|No matches/i')
                if empty_check:
                    logger.debug(f"[{self.provider_id}] No matches for {lname}")
                    return []
                logger.debug(f"[{self.provider_id}] Timeout waiting for matches on {lname}")
                
            # Scrape data using selectors
            scraped_data = await page.evaluate("""
                () => {
                    return Array.from(document.querySelectorAll('[data-at="game-card"]')).map(card => {
                        const homeEl = card.querySelector('[class*="sportsbook-event-scoreboard-layout__ScoreRow"]:nth-of-type(1) [class*="sportsbook-event-scoreboard-layout__ParticipantLabel"]');
                        const awayEl = card.querySelector('[class*="sportsbook-event-scoreboard-layout__ScoreRow"]:nth-of-type(2) [class*="sportsbook-event-scoreboard-layout__ParticipantLabel"]');
                        const timeEl = card.querySelector('[class*="sportsbook-game-card-time__UpcomingGameTime"]');
                        const buttons = Array.from(card.querySelectorAll('button[class*="selection-button__StyledButton"]'));
                        
                        return {
                            home: homeEl ? homeEl.innerText : null,
                            away: awayEl ? awayEl.innerText : null,
                            time: timeEl ? timeEl.innerText : null,
                            odds: buttons.map(b => b.innerText.replace(/[\\n\\r]+/g, ' ').trim()),
                            is_live: !!card.querySelector('[class*="LiveTimer"]')
                        };
                    });
                }
            """)
            
            logger.info(f"Scraped {len(scraped_data)} items from {lname}")
            
            for item in scraped_data:
                if not item['home'] or not item['away']:
                    continue
                if len(item['odds']) < 2:
                    continue
                    
                outcomes = []
                market_type = "1x2"
                
                # Parse odds
                try:
                    h_str = item['odds'][0].split()[-1].replace(',', '.')
                    h_price = float(h_str)

                    # Validate odds > 1.0
                    if h_price <= 1.0:
                        continue

                    if len(item['odds']) >= 3:
                        # 3-way (1x2)
                        x_str = item['odds'][1].split()[-1].replace(',', '.')
                        a_str = item['odds'][2].split()[-1].replace(',', '.')
                        x_price = float(x_str)
                        a_price = float(a_str)

                        # Validate all odds > 1.0
                        if x_price <= 1.0 or a_price <= 1.0:
                            continue

                        outcomes = [
                            {"name": item['home'], "odds": h_price, "side": "home"},
                            {"name": "Draw", "odds": x_price, "side": "draw"},
                            {"name": item['away'], "odds": a_price, "side": "away"}
                        ]
                        market_type = "1x2"
                    elif len(item['odds']) == 2:
                        # 2-way (Moneyline)
                        a_str = item['odds'][1].split()[-1].replace(',', '.')
                        a_price = float(a_str)

                        # Validate odds > 1.0
                        if a_price <= 1.0:
                            continue

                        outcomes = [
                            {"name": item['home'], "odds": h_price, "side": "home"},
                            {"name": item['away'], "odds": a_price, "side": "away"}
                        ]
                        market_type = "moneyline"

                except (ValueError, IndexError, AttributeError):
                    continue
                    
                # Create StandardEvent with normalized team names
                home_normalized = normalize_team_name(item['home'])
                away_normalized = normalize_team_name(item['away'])

                ev_id = f"{home_normalized}-{away_normalized}-{lid}"
                start_time = self._parse_time(item['time'])

                market = {
                    "name": market_type,
                    "type": market_type,
                    "outcomes": outcomes
                }

                ev = StandardEvent(
                    id=ev_id,
                    name=f"{home_normalized} vs {away_normalized}",
                    sport=sport,
                    league=lname,
                    markets=[market],
                    provider="snabbare",
                    home_team=home_normalized,
                    away_team=away_normalized,
                    start_time=start_time.isoformat(),
                    url=url
                )
                events.append(ev)
                
        except Exception as e:
            logger.error(f"Error scraping {lname}: {e}")
        finally:
            await page.close()
            
        return events

    def _parse_time(self, time_str: str) -> datetime:
        """Robust parsing for Snabbare time formats."""
        now = datetime.now()
        if not time_str:
            return now
            
        ts = time_str.lower().strip()
        
        # 1. Format: "13:30" (Implies today)
        if re.match(r'^\d{1,2}:\d{2}$', ts):
            try:
                h, m = map(int, ts.split(':'))
                return now.replace(hour=h, minute=m, second=0, microsecond=0)
            except: return now
            
        # 2. Format: "idag 13:30"
        if 'idag' in ts:
            match = re.search(r'(\d{1,2}:\d{2})', ts)
            if match:
                try:
                    h, m = map(int, match.group(1).split(':'))
                    return now.replace(hour=h, minute=m, second=0, microsecond=0)
                except: return now
                
        # 3. Format: "imorgon 13:30"
        if 'imorgon' in ts:
            match = re.search(r'(\d{1,2}:\d{2})', ts)
            if match:
                from datetime import timedelta
                try:
                    tomorrow = now + timedelta(days=1)
                    h, m = map(int, match.group(1).split(':'))
                    return tomorrow.replace(hour=h, minute=m, second=0, microsecond=0)
                except: return now
                
        # 4. Format: "Lör 24 Jan. 13:30" or "24 Jan. 13:30"
        months = {
            'jan': 1, 'feb': 2, 'mar': 3, 'apr': 4, 'maj': 5, 'jun': 6,
            'jul': 7, 'aug': 8, 'sep': 9, 'okt': 10, 'nov': 11, 'dec': 12
        }
        
        # Try to find day and month
        match = re.search(r'(\d{1,2})\s+([a-zåäö]{3})', ts)
        if match:
            try:
                day = int(match.group(1))
                month_str = match.group(2)
                month = months.get(month_str, now.month)
                
                # Look for time
                time_match = re.search(r'(\d{1,2}:\d{2})', ts)
                h, m = (0, 0)
                if time_match:
                    h, m = map(int, time_match.group(1).split(':'))
                    
                year = now.year
                if month < now.month and now.month == 12:
                    year += 1
                
                return datetime(year, month, day, h, m)
            except: return now

        return now

    async def _fetch_api(self, endpoint: str, params: Optional[Dict] = None) -> Any:
        url = f"{self.api_base}{endpoint}"
        logger.debug(f"[{self.provider_id}] Fetching {url}")
        
        headers = {
            "Accept": "application/json",
            "Referer": f"{self.site_url}/",
        }

        try:
             # Browser context request handles cookies
             response = await self.transport.context.request.get(url, params=params, headers=headers)
             if response.status == 200:
                 return await response.json()
             else:
                 logger.error(f"[{self.provider_id}] API request failed: {response.status} {response.url}")
                 return None
        except Exception as e:
            logger.error(f"[{self.provider_id}] API error: {e}")
            return None

    def parse(self, events_data: List[Dict], sport: str) -> List[StandardEvent]:
        """Not used - extract() is overridden."""
        raise NotImplementedError("SnabbareRetriever uses extract() directly")
