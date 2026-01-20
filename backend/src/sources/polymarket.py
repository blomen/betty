"""
Polymarket API Client

Fetches sports game bets from Polymarket's public Gamma API.
Uses series_id + tag_id=100639 to get only match/game events (not futures).
"""

import json
import logging
from typing import Any
from dataclasses import dataclass, field
from datetime import datetime

import aiohttp

from ..config.sports import (
    SPORTS_CONFIG, 
    POLYMARKET_GAME_BETS_TAG_ID,
    get_polymarket_series_ids,
)

logger = logging.getLogger(__name__)

# Polymarket Gamma API - public, no auth required
GAMMA_API_BASE = "https://gamma-api.polymarket.com"


@dataclass
class PolymarketEvent:
    """A Polymarket sports game event with markets."""
    id: str
    title: str
    slug: str
    sport: str              # Sport name from config
    series_id: int          # Polymarket series ID
    start_time: datetime | None = None
    markets: list[dict] = field(default_factory=list)
    
    @property
    def has_odds(self) -> bool:
        """Check if event has any markets with active prices."""
        return any(m.get("prices") for m in self.markets)


@dataclass 
class PolymarketMarket:
    """A single market within an event."""
    id: str
    question: str
    outcomes: list[str]
    prices: list[float]  # Probabilities (0-1)
    
    @property
    def decimal_odds(self) -> list[float]:
        """Convert probabilities to decimal odds."""
        return [round(1 / p, 3) if p > 0.02 else 0 for p in self.prices]
    
    @property
    def is_active(self) -> bool:
        """Check if market has active (non-settled) prices."""
        return any(0.02 < p < 0.98 for p in self.prices)


class PolymarketSource:
    """
    Polymarket game bets extractor.
    
    Uses series_id + tag_id=100639 to fetch only game/match bets.
    """
    
    def __init__(self):
        self.base_url = GAMMA_API_BASE
        self._session: aiohttp.ClientSession | None = None
    
    async def __aenter__(self):
        self._session = aiohttp.ClientSession()
        return self
    
    async def __aexit__(self, *args):
        if self._session:
            await self._session.close()
    
    async def get_available_sports(self) -> list[dict]:
        """Fetch all supported sports leagues."""
        if not self._session:
            raise RuntimeError("Use 'async with' context manager")
        
        try:
            async with self._session.get(f"{self.base_url}/sports") as response:
                if response.status != 200:
                    logger.error("Failed to fetch sports list: status %s", response.status)
                    return []
                return await response.json()
        except Exception as e:
            logger.error("Failed to fetch sports: %s", str(e))
            return []
    
    async def get_game_events(
        self, 
        series_id: int,
        sport_name: str,
        active_only: bool = True,
        limit: int = 100
    ) -> list[PolymarketEvent]:
        """
        Fetch game bet events for a specific sport.
        
        Args:
            series_id: Polymarket series ID (e.g., 10345 for NBA)
            sport_name: Human-readable sport name
            active_only: Only return active (non-closed) events
            limit: Max events to fetch
            
        Returns:
            List of PolymarketEvent objects (game bets only, no futures)
        """
        if not self._session:
            raise RuntimeError("Use 'async with' context manager")
        
        params = {
            "series_id": series_id,
            "tag_id": POLYMARKET_GAME_BETS_TAG_ID,
            "active": str(active_only).lower(),
            "closed": "false",
            "order": "startTime",
            "ascending": "true",
            "limit": limit,
        }
        
        try:
            async with self._session.get(f"{self.base_url}/events", params=params) as response:
                if response.status != 200:
                    logger.warning("API error for series %s: status %s", series_id, response.status)
                    return []
                
                data = await response.json()
                return self._parse_events(data, series_id, sport_name)
                
        except Exception as e:
            logger.error("Failed to fetch events for series %s: %s", series_id, str(e))
            return []
    
    async def get_all_game_events(self, active_only: bool = True) -> list[PolymarketEvent]:
        """
        Fetch game bet events for all configured sports.
        
        Returns:
            List of PolymarketEvent objects from all sports
        """
        all_events = []
        
        for sport in SPORTS_CONFIG:
            events = await self.get_game_events(
                series_id=sport.polymarket_series_id,
                sport_name=sport.name,
                active_only=active_only,
            )
            all_events.extend(events)
            logger.info("Fetched %d game events for %s", len(events), sport.name)
        
        # Filter for events with active markets only
        active_events = [e for e in all_events if e.has_odds]
        
        logger.info("Total %d game events (%d with active odds)", len(all_events), len(active_events))
        return all_events
    
    def _parse_events(self, data: list[dict[str, Any]], series_id: int, sport_name: str) -> list[PolymarketEvent]:
        """Parse API response into PolymarketEvent objects."""
        events = []
        
        for item in data:
            try:
                # Parse start time
                start_time = self._parse_date(item.get("startTime"))
                
                # Parse markets
                markets = []
                for market_data in item.get("markets", []):
                    market = self._parse_market(market_data)
                    if market:
                        markets.append({
                            "id": market.id,
                            "question": market.question,
                            "outcomes": market.outcomes,
                            "prices": market.prices,
                            "decimal_odds": market.decimal_odds,
                            "is_active": market.is_active,
                        })
                
                event = PolymarketEvent(
                    id=str(item.get("id", "")),
                    title=item.get("title", ""),
                    slug=item.get("slug", ""),
                    sport=sport_name,
                    series_id=series_id,
                    start_time=start_time,
                    markets=markets,
                )
                events.append(event)
                
            except Exception as e:
                logger.debug("Failed to parse event: %s", str(e))
                continue
        
        return events
    
    def _parse_market(self, data: dict) -> PolymarketMarket | None:
        """Parse market data into PolymarketMarket object."""
        try:
            # Parse outcome prices
            prices_raw = data.get("outcomePrices", "[]")
            if isinstance(prices_raw, str):
                prices = json.loads(prices_raw)
            else:
                prices = prices_raw or []
            prices = [float(p) for p in prices]
            
            # Parse outcomes
            outcomes_raw = data.get("outcomes", [])
            if isinstance(outcomes_raw, str):
                outcomes = json.loads(outcomes_raw)
            else:
                outcomes = outcomes_raw or []
            
            if not outcomes or not prices:
                return None
            
            return PolymarketMarket(
                id=str(data.get("id", "")),
                question=data.get("question", ""),
                outcomes=outcomes,
                prices=prices,
            )
        except Exception:
            return None
    
    def _parse_date(self, date_str: str | None) -> datetime | None:
        """Parse ISO date string to datetime."""
        if not date_str:
            return None
        try:
            return datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            return None


# Test
async def _test():
    async with PolymarketSource() as source:
        # Fetch all game events
        events = await source.get_all_game_events()
        print(f"\nTotal game events: {len(events)}")
        
        # Group by sport
        by_sport = {}
        for event in events:
            by_sport.setdefault(event.sport, []).append(event)
        
        for sport, sport_events in by_sport.items():
            # Filter for events with active odds
            active_events = [e for e in sport_events if any(m.get("is_active") for m in e.markets)]
            print(f"\n[{sport}] {len(active_events)} active / {len(sport_events)} total")
            
            for event in active_events[:3]:
                print(f"  {event.title}")
                if event.start_time:
                    print(f"    Start: {event.start_time}")
                for market in event.markets[:2]:
                    if market.get("is_active"):
                        odds = ", ".join(f"{o}: {d:.2f}" for o, d in zip(market["outcomes"], market["decimal_odds"]))
                        print(f"    {market['question'][:40]}: {odds}")


if __name__ == "__main__":
    import asyncio
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    asyncio.run(_test())
