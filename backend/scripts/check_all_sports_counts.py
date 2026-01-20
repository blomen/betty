"""
Check counts for ALL sports.
"""
import asyncio
import logging
from src.sources.polymarket import PolymarketSource, SPORTS_CONFIG

logging.basicConfig(level=logging.WARNING)

async def check_all_sports():
    print(f"\nChecking {len(SPORTS_CONFIG)} sports from config...")
    
    total_events = 0
    empty_sports = []
    
    async with PolymarketSource() as source:
        for sport in SPORTS_CONFIG:
            events = await source.get_game_events(
                series_id=sport.polymarket_series_id,
                sport_name=sport.name,
                limit=100
            )
            count = len(events)
            total_events += count
            
            if count > 0:
                print(f"✓ {sport.name:25} ID:{sport.polymarket_series_id} -> {count:3} events")
            else:
                empty_sports.append(sport)
    
    print(f"\nTotal events found: {total_events}")
    print(f"Empty sports: {len(empty_sports)}")
    print(f"\nEmpty Sports List (Sample 10):")
    for s in empty_sports[:10]:
        print(f"  - {s.name} (ID: {s.polymarket_series_id})")

if __name__ == "__main__":
    asyncio.run(check_all_sports())
