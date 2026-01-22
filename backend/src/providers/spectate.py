from typing import List, Any, Optional, Dict, Set, Union
import logging
import re
import json
import asyncio
from backend.src.core import Retriever, StandardEvent, BrowserTransport

logger = logging.getLogger(__name__)

class SpectateRetriever(Retriever):
    """
    Retriever for 888sport / Spectate based sites.
    Uses BrowserTransport to bypass protections.
    """
    SPORT_SLUGS: Dict[str, str] = {
        "football": "football", "basketball": "basketball", "tennis": "tennis",
        "ice_hockey": "ice-hockey", "american_football": "american-football",
        "baseball": "baseball", "mma": "mma", "esports": "esports",
        "rugby": "rugby-union", "cricket": "cricket", "boxing": "boxing",
        "motorsports": "motor-racing",
    }

    SITE_SLUGS: Dict[str, str] = {
        "football": "fotboll", "basketball": "basket", "tennis": "tennis",
        "ice_hockey": "ishockey", "american_football": "amerikansk-fotboll",
        "baseball": "baseboll", "mma": "mma", "esports": "esports",
        "rugby": "rugby-union", "cricket": "cricket", "boxing": "boxning",
        "motorsports": "motorsport",
    }

    def __init__(self, config: Dict[str, Any], transport: Optional[BrowserTransport] = None):
        # Enforce BrowserTransport
        transport = transport or BrowserTransport(headless=True)
        super().__init__(config, transport)
        
        self.api_base: str = config.get("api_base", "https://spectate-web.888sport.se/spectate")
        # Ensure site_url is clean (no trailing slash)
        raw_site_url = config.get("site_url", f"https://www.{config.get('domain', '888sport.se')}")
        self.site_url: str = raw_site_url.rstrip("/")
        
        self._initialized_sports: Set[str] = set()

    def _get_sport_url(self, sport: str) -> str:
        # Not used in this retriever type
        return ""

    async def _ensure_init(self, sport: str = None) -> None:
        """Initializes session by visiting the site and optionally a specific sport page."""
        target_path = "/"
        if sport:
            slug = self.SITE_SLUGS.get(sport, sport)
            target_path = f"/sport/{slug}/"
            
        if target_path in self._initialized_sports:
            return

        url = f"{self.site_url}{target_path}"
        logger.info(f"[{self.provider_id}] Initializing session via {url}...")
        try:
            if isinstance(self.transport, BrowserTransport):
                await self.transport._ensure_browser()
                await self.transport.page.goto(url, wait_until="domcontentloaded", timeout=30000)
                # Wait a bit for cookies to settle
                await self.transport.page.wait_for_timeout(1000)
                self._initialized_sports.add(target_path)
                logger.info(f"[{self.provider_id}] Initialized {target_path}")
        except Exception as e:
            logger.error(f"[{self.provider_id}] Initialization failed for {url}: {e}")
            # Don't raise, try to proceed as existing cookies might be enough

    async def extract(self, sport: str, limit: int = 1000) -> List[StandardEvent]:
        # 1. Ensure session is initialized for this sport
        await self._ensure_init(sport)
        
        sport_slug = self.SPORT_SLUGS.get(sport, sport)
        all_events: List[StandardEvent] = []
        
        # 2. Fetch Digest (Discovery)
        digest_url = f"/eventsrequest/getEventsDigest/{sport_slug}"
        digest = await self._fetch_api(digest_url)
        
        buckets_to_fetch: List[str] = ["upcoming"]
             
        if isinstance(digest, dict):
            # Prioritize near-term buckets
            for key in ["today", "tomorrow", "starting_soon"]:
                if digest.get(key, 0) > 0:
                    buckets_to_fetch.append(key)
            
            # Check specific dates if upcoming has counts
            upcoming_counts = digest.get("upcoming", {})
            if isinstance(upcoming_counts, dict):
                for date_key, count in upcoming_counts.items():
                    if count > 0 and date_key not in buckets_to_fetch:
                        buckets_to_fetch.append(date_key)
        
        # Default fallback
        if not buckets_to_fetch:
            buckets_to_fetch = ["today", "upcoming"]
            
        # Deduplicate buckets
        unique_buckets: List[str] = []
        seen_buckets: Set[str] = set()
        for b in buckets_to_fetch:
            if b not in seen_buckets:
                unique_buckets.append(b)
                seen_buckets.add(b)
        
        logger.debug(f"[{self.provider_id}] {sport}: Crawling buckets: {unique_buckets}")
        
        # 3. Fetch Buckets
        seen_events: Set[str] = set()
        
        for bucket in unique_buckets:
            # Spectate requires Multipart boundary for GET-like POST requests
            method = "POST"
            boundary = "----WebKitFormBoundaryQ5RAQxk9ozbkr9H6"
            content_type = f"multipart/form-data; boundary={boundary}"
            data = f"--{boundary}--\r\n".encode('utf-8')

            headers = {
                "content-type": content_type
            }

            endpoint = f"/sportsbook-req/getUpcomingEvents/{sport_slug}/{bucket}"
            resp_data = await self._fetch_api(endpoint, method=method, data=data, headers=headers)
            
            events = self.parse(resp_data, sport)
            for ev in events:
                if ev.id not in seen_events:
                    all_events.append(ev)
                    seen_events.add(ev.id)
            
            if limit and len(all_events) >= limit:
                break
                
        return all_events

    async def _fetch_api(self, endpoint: str, method: str = "GET", data: Any = None, headers: Optional[Dict[str, str]] = None) -> Any:
        url = f"{self.api_base}{endpoint}"
        
        base_headers = {
             "accept": "application/json",
             "origin": self.site_url,
             "referer": f"{self.site_url}/",
        }
        if headers:
            base_headers.update(headers)
            
        try:
            if not isinstance(self.transport, BrowserTransport):
                logger.error(f"[{self.provider_id}] SpectateRetriever requires BrowserTransport")
                return {}

            if method.upper() == "POST":
                response = await self.transport.context.request.post(url, data=data, headers=base_headers)
            else:
                response = await self.transport.context.request.get(url, headers=base_headers)
                
            if response.status == 403:
                logger.warning(f"[{self.provider_id}] 403 Forbidden for {url}. Origin/Headers might be rejected.")
                return {}
            elif response.status == 429:
                logger.warning(f"[{self.provider_id}] 429 Rate Limited. Backing off.")
                await asyncio.sleep(2) # Simple backoff
                return {}
            elif response.status not in (200, 201):
                logger.warning(f"[{self.provider_id}] {method} {url} returned {response.status}")
                return {}
                
            try:
                return await response.json()
            except Exception:
                # Fallback for text/html responses that might be JSON
                text = await response.text()
                try:
                    return json.loads(text)
                except Exception:
                    logger.debug(f"[{self.provider_id}] Failed to parse JSON response from {url}")
                    return {}
        except Exception as e:
            logger.error(f"[{self.provider_id}] API fetch failed: {e}")
            return {}

    def parse(self, data: Any, sport: str, league: str = "") -> List[StandardEvent]:
        events: List[StandardEvent] = []
        if not data: return events
        
        # unexpected types
        if not isinstance(data, (dict, list)):
            return events

        # Handle list-wrapped responses
        if isinstance(data, list):
            for item in data:
                events.extend(self.parse(item, sport, league))
            return events

        # Handle Dict with 'events' key
        events_data = data.get("events")
        if events_data:
            items = events_data.values() if isinstance(events_data, dict) else events_data if isinstance(events_data, list) else []
            for ev_data in items:
                if isinstance(ev_data, dict):
                    ev = self._parse_event(ev_data, sport, league)
                    if ev: events.append(ev)
        
        # Handle single event dict structure (Top-level)
        # Note: Spectate usually wraps in 'events', but sometimes for single event requests it's direct
        if not events and isinstance(data, dict) and data.get("name") and data.get("id") and data.get("markets"):
            ev = self._parse_event(data, sport, league)
            if ev: events.append(ev)
            
        return events

    def _parse_event(self, event_data: dict, sport: str, league: str) -> Optional[StandardEvent]:
        try:
            if event_data.get("inplay"): return None
            
            ev_id = str(event_data.get("id", ""))
            name = event_data.get("name", "")
            start_time = event_data.get("start_time", "")
            
            # Competitors Logic
            competitors = event_data.get("competitors", {})
            home, away = "", ""
            comps = competitors.values() if isinstance(competitors, dict) else competitors if isinstance(competitors, list) else []
            
            for c in comps:
                if not isinstance(c, dict): continue
                if c.get("home") or c.get("is_home_team"): 
                    home = c.get("name", "")
                else: 
                    away = c.get("name", "")
            
            # Fallback name split
            if (not home or not away):
                if " v " in name:
                    parts = name.split(" v ", 1)
                    home, away = parts[0].strip(), parts[1].strip()
                elif " vs " in name:
                    parts = name.split(" vs ", 1)
                    home, away = parts[0].strip(), parts[1].strip()

            if not home or not away or not ev_id:
                return None
            
            markets = self._parse_markets(event_data)
            if not markets:
                return None
            
            return StandardEvent(
                id=ev_id, 
                name=name, 
                home_team=home, 
                away_team=away, 
                sport=sport,
                league=event_data.get("tournament_name") or event_data.get("tournament", {}).get("name") or league,
                start_time=start_time, 
                markets=markets, 
                provider=self.provider_id
            )
        except Exception as e:
            # logger.debug(f"Event parsing error: {e}")
            return None

    def _parse_markets(self, event_data: dict) -> List[dict]:
        markets: List[dict] = []
        m_data = event_data.get("markets", {})
        items = m_data.values() if isinstance(m_data, dict) else m_data if isinstance(m_data, list) else []
        
        # Standardized Mappings
        MARKET_MAP = {
            "match winner": "moneyline",
            "vinnare": "moneyline",
            "utdelningsrader": "moneyline", # "Payout lines" (1X2)
            "1x2": "moneyline",
            "matchresultat": "moneyline",
            "matchresultat (2-vägs)": "moneyline", # Tennis/US Sports
            "matchresultat (3-vägs)": "moneyline", # Soccer
            "matchvinnare": "moneyline",
            "matchvinnare tvåvägs": "moneyline",
            "matchvinnare (3-vägs)": "moneyline",
            "fightodds": "moneyline",
            
            "över/under": "over_under",
            "over/under": "over_under",
            "totalt antal poäng": "over_under", # "Total points"
            "totalt antal mål": "over_under",   # "Total goals"
            "totalt antal poäng, över/under": "over_under",
            "totalt antal mål i match, över/under": "over_under",
            "totalt antal runs, över/under": "over_under",
            
            "poänghandikapp": "spread",         # "Point handicap"
            "handikapp": "spread",
            "spread": "spread",
            "pucklinje": "spread"               # Hockey Puck Line
        }

        for m in items:
            if not isinstance(m, dict): continue
            raw_name = m.get("name", "").lower().strip()
            
            # Direct Map Check
            m_type = MARKET_MAP.get(raw_name)
            
            # Fuzzy / Contains Checks if not exact match
            if not m_type:
                if "över/under" in raw_name or "over/under" in raw_name:
                    m_type = "over_under"
                elif "handikapp" in raw_name or "handicap" in raw_name or "spread" in raw_name:
                    m_type = "spread"
            
            # If still no type, skip or tag as unknown (we skip for now)
            if not m_type:
                continue
            
            s_data = m.get("selections", {})
            s_items = s_data.values() if isinstance(s_data, dict) else s_data if isinstance(s_data, list) else []
            
            outcomes: List[dict] = []
            for s in s_items:
                if not isinstance(s, dict) or not s.get("active", True): continue
                try:
                    price = s.get("decimal_price") or s.get("price")
                    if price:
                        outcome = {
                            "name": s.get("name", ""), 
                            "odds": round(float(price), 3)
                        }
                        # Capture line/handicap
                        line = s.get("line") or s.get("handicap")
                        
                        # Fallback: Extract from name
                        if line is None:
                            name_val = s.get("name", "")
                            # Matches (+3.5), (-3.5), (3.5) or "Over 150.5"
                            # Regex to capture finding signed/unsigned floats inside text
                            match = re.search(r'(?:^|[\s\(])([+-]?\d+\.?\d*)', name_val)
                            if match:
                                val_str = match.group(1)
                                # Filter out likely jersey numbers or pure odds if mistakenly grabbed
                                try:
                                    line = float(val_str)
                                except: pass

                        if line is not None:
                             outcome["line"] = float(line)
                        outcomes.append(outcome)
                except: continue
            
            if outcomes:
                markets.append({"type": m_type, "outcomes": outcomes})
                
        return markets
