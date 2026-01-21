from typing import List, Any, Optional
import logging
import sys
from backend.src.core import Retriever, StandardEvent, BrowserTransport

logger = logging.getLogger(__name__)

class SpectateRetriever(Retriever):
    """
    Retriever for 888sport / Spectate based sites.
    Uses BrowserTransport to bypass protections.
    """
    SPORT_SLUGS = {
        "football": "football", "basketball": "basketball", "tennis": "tennis",
        "ice_hockey": "ice-hockey", "american_football": "american-football",
        "baseball": "baseball", "mma": "mma", "esports": "esports",
        "rugby": "rugby-union", "cricket": "cricket",
    }

    def __init__(self, config: dict, transport=None):
        # Enforce BrowserTransport
        transport = transport or BrowserTransport(headless=True)
        super().__init__(config, transport)
        
        self.api_base = config.get("api_base", "https://spectate-web.888sport.se/spectate")
        self.site_url = config.get("site_url", f"https://www.{config.get('domain', '888sport.se')}")
        self.tournaments_map = config.get("params", {}).get("tournaments", {})
        
        self._initialized = False

    async def _ensure_init(self):
        if self._initialized: return
        
        # We need to visit the site once to set cookies/bypass checks
        logger.info(f"[{self.provider_id}] Visiting {self.site_url} to initialize session...")
        try:
            # We use the transport's internal page logic if available
            # BrowserTransport exposes .page? No, it's abstract. 
            # We need to cast or rely on 'get' to do it?
            # 'get' in BrowserTransport does ensure_browser.
            # But we want to visit the home page first, not just GET the API.
            
            # Hack: Access transport internals if we know it's BrowserTransport
            if isinstance(self.transport, BrowserTransport):
                await self.transport._ensure_browser()
                await self.transport.page.goto(self.site_url, wait_until="domcontentloaded")
                await self.transport.page.screenshot(path="backend/src/debug_888_init.png")
                logger.info(f"[{self.provider_id}] Initialized. Screenshot saved to backend/src/debug_888_init.png")
                self._initialized = True
        except Exception as e:
            logger.error(f"Spectate init failed: {e}")

    def _get_sport_url(self, sport: str) -> str:
        return ""

    async def extract(self, sport: str, limit: int = 50) -> List[StandardEvent]:
        try:
            await self._ensure_init()
        except Exception:
            pass # Try anyway?
        
        tournaments = self.tournaments_map.get(sport, [])
        if limit and len(tournaments) > limit:
            tournaments = tournaments[:limit]

        all_events = []
        if not tournaments:
             # Upcoming fallback
             sport_slug = self.SPORT_SLUGS.get(sport, sport)
             endpoint = f"/sportsbook-req/getUpcomingEvents/{sport_slug}/today"
             data = await self._fetch_api(endpoint)
             all_events.extend(self.parse(data, sport, ""))
        else:
            for slug in tournaments:
                sport_slug = self.SPORT_SLUGS.get(sport, sport)
                endpoint = f"/sportsbook-req/getTournamentMatches/{sport_slug}/{slug}"
                data = await self._fetch_api(endpoint)
                all_events.extend(self.parse(data, sport, slug))
        
        return all_events

    async def _fetch_api(self, endpoint: str) -> dict:
        url = f"{self.api_base}{endpoint}"
        logger.info(f"[{self.provider_id}] Fetching API: {url}")
        # Use Transport.get - our BrowserTransport logic handles context requests
        # We pass headers to simulate the referer
        try:
            data = await self.transport.get(url, headers={
                 "accept": "application/json",
                 "origin": self.site_url,
                 "referer": f"{self.site_url}/",
            })
            if not data:
                logger.warning(f"[{self.provider_id}] API returned empty/None for {url}")
            return data if isinstance(data, dict) else {}
        except Exception as e:
            logger.error(f"[{self.provider_id}] API fetch failed: {e}")
            return {}

    def parse(self, data: Any, sport: str, league: str = "") -> List[StandardEvent]:
        events = []
        if not data: return events
        
        # Spectate API structure normalization
        # Sometimes events are in data['events'], sometimes data itself
        events_data = data.get("events", data) if isinstance(data, dict) else data
        
        items = []
        if isinstance(events_data, dict):
            items = events_data.values()
        elif isinstance(events_data, list):
            items = events_data
            
        for event_data in items:
            if not isinstance(event_data, dict): continue
            ev = self._parse_event(event_data, sport, league)
            if ev: events.append(ev)
            
        return events

    def _parse_event(self, event_data: dict, sport: str, league: str) -> StandardEvent | None:
        try:
            if event_data.get("inplay"): return None
            name = event_data.get("name", "")
            
            # Competitors
            competitors = event_data.get("competitors", {})
            home, away = "", ""
            comps = competitors.values() if isinstance(competitors, dict) else competitors if isinstance(competitors, list) else []
            for c in comps:
                 if isinstance(c, dict):
                    if c.get("home") or c.get("is_home_team"): home = c.get("name", "")
                    else: away = c.get("name", "")
            
            # Fallback name parsing
            if (not home or not away) and " vs " in name:
                parts = name.split(" vs ", 1)
                home, away = parts[0].strip(), parts[1].strip()

            if not home or not away: return None
            
            markets = self._parse_markets(event_data)
            if not markets: return None
            
            return StandardEvent(
                id=str(event_data.get("id", "")),
                name=name, home_team=home, away_team=away, sport=sport,
                league=event_data.get("tournament", {}).get("name", "") or league,
                start_time=event_data.get("start_time", ""),
                markets=markets, provider=self.provider_id
            )
        except Exception:
            return None

    def _parse_markets(self, event_data: dict) -> list[dict]:
        markets = []
        m_data = event_data.get("markets", {})
        items = m_data.values() if isinstance(m_data, dict) else m_data
        
        for m in items:
            if not isinstance(m, dict): continue
            s_data = m.get("selections", {})
            s_items = s_data.values() if isinstance(s_data, dict) else s_data
            
            outcomes = []
            for s in s_items:
                if not isinstance(s, dict) or not s.get("active", True): continue
                try:
                    odds = float(s.get("decimal_price") or s.get("price") or 0)
                    if odds > 1:
                        outcomes.append({"name": s.get("name", ""), "odds": round(odds, 3)})
                except: continue
            
            if outcomes: markets.append({"type": m.get("name", ""), "outcomes": outcomes})
        return markets
