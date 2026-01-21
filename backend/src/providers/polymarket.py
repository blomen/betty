from typing import List, Any, Optional
import logging
import json
import os
from backend.src.core import Retriever, StandardEvent

logger = logging.getLogger(__name__)

class PolymarketRetriever(Retriever):
    def __init__(self, config: dict, transport=None):
        super().__init__(config, transport)
        self.base_url = config.get("base_url", "https://gamma-api.polymarket.com")
        self.game_bets_tag_id = config.get("params", {}).get("game_bets_tag_id", 100639)
        self.sports_map = self._load_sports_map()

    def _load_sports_map(self) -> dict:
        """Load sports.json to map sport name -> series_id."""
        try:
            # Assuming standard path relative to project root
            # or we could pass this in config.
            # For now, simplistic relative path loading
            path = os.path.join(os.getcwd(), "backend", "src", "config", "sports.json")
            if os.path.exists(path):
                with open(path, "r") as f:
                    sports_data = json.load(f)
                    
                mapping = {}
                
                # Helper to process single item
                def process_item(s):
                    name = s.get("name")
                    pid = s.get("polymarket_series_id")
                    slug = s.get("polymarket_slug")
                    tid = s.get("polymarket_tag_id")
                    
                    if name and (pid or slug or tid):
                        mapping[name] = {"id": pid, "slug": slug, "tag_id": tid}

                if isinstance(sports_data, list) and len(sports_data) > 0 and "leagues" in sports_data[0]:
                    # Nested format
                    for group in sports_data:
                        # Polymarket IDs are usually on the league level, but could be on group level later?
                        # For now, just iterate leagues
                        for league in group.get("leagues", []):
                            process_item(league)
                else:
                    # Flat format
                    for s in sports_data:
                        process_item(s)
                            
                return mapping
        except Exception as e:
            logger.warning(f"Failed to load sports.json for Polymarket: {e}")
        return {}

    def _get_sport_url(self, sport: str) -> str:
        # Not used directly in new logic, we build URL in extract
        return ""

    async def extract(self, sport: str, limit: int = 50) -> List[StandardEvent]:
        config = self.sports_map.get(sport)
        if not config:
            logger.warning(f"[{self.provider_id}] No series_id/slug/tag found for sport '{sport}'")
            return []
        
        series_id = config.get("id")
        series_slug = config.get("slug")
        tag_id = config.get("tag_id")
            
        params = {
            "active": "true",
            "closed": "false",
            "order": "startTime",
            "ascending": "true",
            "limit": limit
        }
        
        # Strategy: Series ID > Tag ID > Slug (Client Filter)
        if series_id:
            params["series_id"] = series_id
            # Don't restrict by tag if we have series ID, to imply broader search
        elif tag_id:
            params["tag_id"] = tag_id
        elif series_slug:
            # Fallback: Fetch broad (no tag) and filter client-side
            # We explicitly do NOT set tag_id here to avoid "Game Bets" restriction
            pass
        else:
             return []
        
        url = f"{self.base_url}/events"
        data = await self.transport.get(url, params=params)
        
        if not data: return []
        
        # Client-Side Filtering if using Slug strategy
        if not series_id and not tag_id and series_slug:
            filtered_data = []
            for item in data:
                # Check Series Slug OR potentially Event Slug if lenient
                actual_slug = item.get("seriesSlug") or ""
                if actual_slug == series_slug:
                    filtered_data.append(item)
            data = filtered_data
            
        return self.parse(data, sport)

    def parse(self, data: Any, sport: str) -> List[StandardEvent]:
        events = []
        if not isinstance(data, list): return events
        
        for item in data:
            try:
                # Basic Parsing
                start_time = item.get("startTime")
                title = item.get("title", "")
                slug = item.get("slug", "")
                event_id = str(item.get("id", ""))
                
                # Check markets
                raw_markets = item.get("markets", [])
                markets = []
                for m_data in raw_markets:
                    m = self._parse_market(m_data)
                    if m: markets.append(m)
                
                # Assume home/away from title 
                home, away = "", ""
                if " vs " in title:
                    parts = title.split(" vs ")
                    if len(parts) == 2:
                        home, away = parts[0].strip(), parts[1].strip()
                
                events.append(StandardEvent(
                    id=event_id,
                    name=title,
                    home_team=home,
                    away_team=away,
                    sport=sport,
                    league="", 
                    start_time=start_time,
                    markets=markets,
                    provider=self.provider_id,
                ))
            except Exception as e:
                logger.debug(f"Failed to parse Polymarket event: {e}")
        return events

    def _parse_market(self, data: dict) -> dict | None:
        try:
            # Parse outcome prices
            prices_raw = data.get("outcomePrices", "[]")
            prices = json.loads(prices_raw) if isinstance(prices_raw, str) else (prices_raw or [])
            prices = [float(p) for p in prices]
            
            outcomes_raw = data.get("outcomes", [])
            outcomes = json.loads(outcomes_raw) if isinstance(outcomes_raw, str) else (outcomes_raw or [])
            
            if not outcomes or not prices: return None
            
            # Check active (liquidity check simplified)
            if not any(0.02 < p < 0.98 for p in prices): return None
            
            # Convert to odds
            formatted_outcomes = []
            for name, p in zip(outcomes, prices):
                if p > 0.02:
                    formatted_outcomes.append({
                        "name": name,
                        "odds": round(1 / p, 3)
                    })
            
            if not formatted_outcomes: return None

            return {
                "type": data.get("question", ""),
                "outcomes": formatted_outcomes
            }
        except Exception:
            return None
