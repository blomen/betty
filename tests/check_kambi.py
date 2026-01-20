"""Quick test to check raw Kambi data."""
import asyncio
from src.extractors.kambi import get_extractor

async def check_raw():
    extractor = get_extractor('unibet')
    events = await extractor.extract('football', max_groups=2)
    
    print(f"Extracted {len(events)} events\n")
    
    for ev in events[:5]:
        print(f"Name: {ev.name}")
        print(f"  Home: '{ev.home_team}'")
        print(f"  Away: '{ev.away_team}'")
        print(f"  Markets: {len(ev.markets)}")
        print()

if __name__ == "__main__":
    asyncio.run(check_raw())
