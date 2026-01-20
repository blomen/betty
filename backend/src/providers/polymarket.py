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
                for s in sports_data:
                    # s is list of objects? Or list of dicts?
                    # sports.json structure: [{"name": "football", "polymarket_series_id": 123}, ...]
                    name = s.get("name")
                    pid = s.get("polymarket_series_id")
                    if name and pid:
                        mapping[name] = pid
                return mapping
        except Exception as e:
            logger.warning(f"Failed to load sports.json for Polymarket: {e}")
        return {}

    def _get_sport_url(self, sport: str) -> str:
        # Not used directly in new logic, we build URL in extract
        return ""

    async def extract(self, sport: str, limit: int = 50) -> List[StandardEvent]:
        series_id = self.sports_map.get(sport)
        if not series_id:
            logger.warning(f"[{self.provider_id}] No series_id found for sport '{sport}'")
            return []
            
        params = {
            "series_id": series_id,
            "tag_id": self.game_bets_tag_id,
            "active": "true",
            "closed": "false",
            "order": "startTime",
            "ascending": "true",
            "limit": limit
        }
        
        url = f"{self.base_url}/events"
        data = await self.transport.get(url, params=params)
        
        if not data: return []
        
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
