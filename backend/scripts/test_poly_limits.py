"""
Test Polymarket extraction limits and params.
"""
import asyncio
import logging
from src.sources.polymarket import PolymarketSource, SPORTS_CONFIG, POLYMARKET_GAME_BETS_TAG_ID

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def test_limits():
    # Pick a few major sports to test
    test_sports = [
        s for s in SPORTS_CONFIG 
        if s.name in ["Premier League", "NBA", "NFL", "Champions League"]
    ]
    
    async with PolymarketSource() as source:
        print(f"\n{'='*60}")
        print("TESTING POLYMARKET EXTRACTION")
        print(f"{'='*60}")
        
        for sport in test_sports:
            print(f"\nTesting {sport.name} (Series ID: {sport.polymarket_series_id})")
            
            # 1. Standard request (with tag_id)
            events_std = await source.get_game_events(
                series_id=sport.polymarket_series_id,
                sport_name=sport.name,
                limit=100
            )
            print(f"  Standard (tag={POLYMARKET_GAME_BETS_TAG_ID}): {len(events_std)} events")
            
            # 2. Request WITHOUT tag_id (manual fetch using similar logic to get_game_events but no tag)
            params = {
                "series_id": sport.polymarket_series_id,
                "active": "true",
                "closed": "false",
                "limit": 100,
            }
            try:
                async with source._session.get(f"{source.base_url}/events", params=params) as response:
                    raw_data = await response.json()
                    events_no_tag = source._parse_events(raw_data, sport.polymarket_series_id, sport.name)
            except Exception as e:
                print(f"  Error fetching without tag: {e}")
                events_no_tag = []
                
            print(f"  Without Tag ID: {len(events_no_tag)} events")
            
            # Compare and show first few titles from No Tag to see what we're missing
            if len(events_no_tag) > len(events_std):
                print("  Sample events missed by tag filter:")
                missed = [e for e in events_no_tag if e.id not in [x.id for x in events_std]]
                for e in missed[:5]:
                    print(f"    - {e.title}")
            
            # 3. Request with higher limit (standard)
            events_limit = await source.get_game_events(
                series_id=sport.polymarket_series_id,
                sport_name=sport.name,
                limit=500
            )
            print(f"  High Limit (500): {len(events_limit)} events")

if __name__ == "__main__":
    asyncio.run(test_limits())
