"""
Kambi Extractor for Oddopp

Simplified Kambi API client for extracting odds from Kambi-powered bookmakers.
Covers: Unibet, Betsson, 888sport, LeoVegas, NordicBet, Betsafe, etc.

Based on proven oddsview implementation but stripped to essentials.
"""

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

from ..utils.http import HTTPClient

logger = logging.getLogger(__name__)


@dataclass
class KambiEvent:
    """A Kambi sporting event with odds."""
    id: str
    name: str
    home_team: str
    away_team: str
    sport: str
    league: str
    start_time: str
    markets: list[dict]  # [{type, outcomes: [{name, odds}]}]
    provider: str


class KambiExtractor:
    """
    Kambi API extractor using group-first architecture.
    
    Flow:
    1. Fetch all groups from /{brand}/group.json
    2. Filter groups by sport
    3. Fetch events from each group via /{brand}/betoffer/group/{id}.json
    4. Extract and normalize events with odds
    """
    
    # Kambi base URL for all providers
    BASE_URL = "https://eu1.offering-api.kambicdn.com/offering/v2018"
    
    # Standard params for Swedish market
    DEFAULT_PARAMS = {
        "market": "SE",
        "lang": "sv_SE",
        "channel_id": "1",
        "client_id": "2",
    }
    
    def __init__(self, provider: str, brand: str, domain: str = ""):
        """
        Args:
            provider: Provider name (unibet, betsson, etc.)
            brand: Kambi brand code (ubse, betsafe, etc.)
            domain: Domain for referer header
        """
        self.provider = provider
        self.brand = brand
        self.domain = domain or f"{provider}.se"
        self.headers = {
            "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "accept": "application/json",
            "referer": f"https://{self.domain}/",
            "origin": f"https://{self.domain}",
        }
        self.rate_limits = {"rpm": 60}
    
    async def get_sports(self) -> list[str]:
        """Get available sports from the provider."""
        groups = await self._get_groups()
        sports = set()
        for group in groups:
            sport = group.get("sport", "").lower()
            if sport and sport != "unknown":
                sports.add(sport)
        return sorted(sports)
    
    async def extract(self, sport: str, max_groups: int = 50) -> list[KambiEvent]:
        """
        Extract events for a sport.
        
        Args:
            sport: Sport name (football, tennis, etc.)
            max_groups: Max groups to fetch (for testing)
        """
        logger.info(f"[{self.provider}] Extracting {sport}...")
        
        # Get groups and filter by sport
        groups = await self._get_groups()
        sport_groups = [g for g in groups if self._match_sport(g.get("sport", ""), sport)]
        
        if not sport_groups:
            logger.warning(f"[{self.provider}] No groups found for {sport}")
            return []
        
        logger.info(f"[{self.provider}] Found {len(sport_groups)} groups for {sport}")
        
        # Limit groups for testing
        if max_groups and len(sport_groups) > max_groups:
            sport_groups = sport_groups[:max_groups]
            logger.info(f"[{self.provider}] Limited to {max_groups} groups")
        
        # Fetch events from each group
        all_events = []
        async with HTTPClient(self.rate_limits, self.headers) as client:
            for group in sport_groups:
                events = await self._fetch_group_events(client, group)
                all_events.extend(events)
        
        logger.info(f"[{self.provider}] Extracted {len(all_events)} events")
        return all_events
    
    async def _get_groups(self) -> list[dict]:
        """Fetch all groups (sports/leagues hierarchy)."""
        url = f"{self.BASE_URL}/{self.brand}/group.json"
        
        async with HTTPClient(self.rate_limits, self.headers) as client:
            data = await client.get(url, params=self.DEFAULT_PARAMS)
        
        if not data:
            logger.error(f"[{self.provider}] Failed to fetch groups")
            return []
        
        # Recursively extract all groups
        groups = []
        self._extract_groups_recursive(data, groups)
        return groups
    
    def _extract_groups_recursive(self, obj: Any, groups: list, depth: int = 0):
        """Recursively extract groups from nested structure."""
        if isinstance(obj, dict):
            if "id" in obj and "name" in obj:
                groups.append({
                    "id": obj["id"],
                    "name": obj.get("name", obj.get("englishName", "")),
                    "sport": obj.get("sport", ""),
                    "depth": depth,
                })
            
            # Check nested groups
            for key in ["group", "groups", "children"]:
                if key in obj and isinstance(obj[key], (list, dict)):
                    self._extract_groups_recursive(obj[key], groups, depth + 1)
        
        elif isinstance(obj, list):
            for item in obj:
                self._extract_groups_recursive(item, groups, depth)
    
    def _match_sport(self, group_sport: str, target_sport: str) -> bool:
        """Check if group matches target sport."""
        group_sport = group_sport.lower()
        target_sport = target_sport.lower()
        
        # Sport aliases - maps our standard sport names to Kambi variations
        aliases = {
            "football": ["football", "fotboll", "soccer", "fussball", "fútbol", "calcio"],
            "ice_hockey": ["ice_hockey", "ishockey", "hockey", "eishockey"],
            "tennis": ["tennis"],
            "basketball": ["basketball", "basket"],
            "american_football": ["american_football", "nfl", "amerikanischer_football"],
            "baseball": ["baseball"],
            "cricket": ["cricket"],
            "rugby": ["rugby", "rugby_union", "rugby_league"],
            "mma": ["mma", "mixed_martial_arts", "ufc", "martial_arts"],
            "esports": ["esports", "e-sports", "e_sports", "gaming"],
        }
        
        target_aliases = aliases.get(target_sport, [target_sport])
        return group_sport in target_aliases
    
    async def _fetch_group_events(
        self, 
        client: HTTPClient, 
        group: dict
    ) -> list[KambiEvent]:
        """Fetch events from a single group."""
        url = f"{self.BASE_URL}/{self.brand}/betoffer/group/{group['id']}.json"
        
        data = await client.get(url, params=self.DEFAULT_PARAMS)
        if not data:
            return []
        
        # Parse events and betOffers
        events_raw = data.get("events", [])
        betoffers = data.get("betOffers", [])
        outcomes = data.get("outcomes", [])
        
        # Build outcome lookup
        outcome_map = {}
        for outcome in outcomes:
            outcome_map[outcome.get("id")] = outcome
        
        events = []
        for event_raw in events_raw:
            # Skip live events
            if event_raw.get("state") == "STARTED":
                continue
            
            event = self._parse_event(event_raw, betoffers, outcome_map, group)
            if event:
                events.append(event)
        
        return events
    
    def _parse_event(
        self,
        event_raw: dict,
        betoffers: list,
        outcome_map: dict,
        group: dict
    ) -> KambiEvent | None:
        """Parse raw event data into KambiEvent."""
        try:
            event_id = str(event_raw.get("id", ""))
            
            # Get teams from homeName/awayName (Kambi v2018 format)
            home_team = event_raw.get("homeName", "")
            away_team = event_raw.get("awayName", "")
            
            # Fallback to participants if present
            if not home_team or not away_team:
                participants = event_raw.get("participants", [])
                for p in participants:
                    if p.get("home"):
                        home_team = p.get("name", "")
                    else:
                        away_team = p.get("name", "")
            
            # Get event name
            name = event_raw.get("name", "") or f"{home_team} vs {away_team}"
            
            # FILTER: Skip futures/outright events (not individual games)
            name_lower = name.lower()
            futures_patterns = [
                "vinnare",        # Swedish: winner
                "winner of",
                "winner -",
                "division 20",    # Division winners
                "conference 20",  # Conference futures
                "league 20",      # League futures
                "2025/2026",      # Season markets
                "2024/2025",
                "2026/2027",
                "mvp",            # MVP awards
                "top scorer",
                "topscorer",
                "topskytteligaen",  # Swedish: top scorer league
                "mest antal",       # Swedish: most number
                "playoff",          # Playoff winner
                "första mål",       # First goal
                "champion",
                "relegation",
                "promotion",
                # Additional Swedish patterns for futures/outrights
                "mästerskapet",     # Swedish: championship
                "mästerskap",       # Swedish: championship (variant)
                "-mästerskapet",    # Championship suffix
                "specialer",        # Swedish: specials
                "vinnarspel",       # Swedish: winner bet
                "slutspel",         # Swedish: playoffs/finals
                "säsong",           # Swedish: season
                "outright",         # Common Kambi term
            ]
            
            if any(pattern in name_lower for pattern in futures_patterns):
                return None  # Skip futures/outrights
            
            # Skip events without proper home/away teams (likely futures)
            if not home_team or not away_team:
                return None
            
            # Get start time
            start_time = event_raw.get("start", "")
            
            # Get league from path
            path = event_raw.get("path", [])
            league = path[-1].get("name", "") if path else group.get("name", "")
            
            # Get odds for this event
            markets = []
            for betoffer in betoffers:
                if betoffer.get("eventId") != event_raw.get("id"):
                    continue
                
                market = self._parse_market(betoffer, outcome_map)
                if market:
                    markets.append(market)
            
            if not markets:
                return None  # Skip events without odds
            
            return KambiEvent(
                id=event_id,
                name=name,
                home_team=home_team,
                away_team=away_team,
                sport=group.get("sport", "").lower(),
                league=league,
                start_time=start_time,
                markets=markets,
                provider=self.provider,
            )
            
        except Exception as e:
            logger.debug(f"Failed to parse event: {e}")
            return None
    
    def _parse_market(self, betoffer: dict, outcome_map: dict) -> dict | None:
        """Parse a bet offer into market format."""
        try:
            market_type = betoffer.get("criterion", {}).get("label", "")
            outcomes = []
            
            for outcome_ref in betoffer.get("outcomes", []):
                outcome_id = outcome_ref.get("id")
                outcome = outcome_map.get(outcome_id, outcome_ref)
                
                odds = outcome.get("odds", 0) / 1000  # Kambi uses milliods
                if odds <= 1:
                    continue
                
                outcomes.append({
                    "name": outcome.get("label", ""),
                    "odds": round(odds, 3),
                })
            
            if not outcomes:
                return None
            
            return {
                "type": market_type,
                "outcomes": outcomes,
            }
            
        except Exception:
            return None


# Provider configurations - verified brand codes from oddsview manifests
KAMBI_PROVIDERS = {
    "unibet": {"brand": "ubse", "domain": "unibet.se"},
    "leovegas": {"brand": "leose", "domain": "leovegas.se"},
    "casumo": {"brand": "case", "domain": "casumo.com"},
    "expekt": {"brand": "expektse", "domain": "expekt.se"},
    "paf": {"brand": "pafse", "domain": "paf.se"},
    "speedybet": {"brand": "speedybetse", "domain": "speedybet.se"},
    "x3000": {"brand": "speedyspelse", "domain": "x3000.se"},
    "goldenbull": {"brand": "pafgoldense", "domain": "goldenbull.se"},
}


def get_extractor(provider: str) -> KambiExtractor:
    """Get a Kambi extractor for a provider."""
    if provider not in KAMBI_PROVIDERS:
        raise ValueError(f"Unknown Kambi provider: {provider}")
    
    config = KAMBI_PROVIDERS[provider]
    return KambiExtractor(
        provider=provider,
        brand=config["brand"],
        domain=config["domain"],
    )


# Test
async def _test():
    extractor = get_extractor("unibet")
    events = await extractor.extract("football", max_groups=3)
    
    for event in events[:5]:
        print(f"\n{event.name}")
        print(f"  {event.league}")
        for market in event.markets[:2]:
            outcomes = ", ".join(f"{o['name']}: {o['odds']}" for o in market["outcomes"][:3])
            print(f"  {market['type']}: {outcomes}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(_test())
