"""
Check parsing success rate for Polymarket events.
"""
import asyncio
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from src.sources.polymarket import PolymarketSource, SPORTS_CONFIG
from src.pipeline import parse_teams_from_title

async def check_parsing():
    print(f"\nChecking picking/parsing for {len(SPORTS_CONFIG)} sports...")
    
    total_events = 0
    parsed_ok = 0
    failed_parsing = []
    
    async with PolymarketSource() as source:
        for sport in SPORTS_CONFIG:
            events = await source.get_game_events(
                series_id=sport.polymarket_series_id,
                sport_name=sport.name,
                limit=50
            )
            
            if not events:
                continue
                
            total_events += len(events)
            
            # Test parsing
            for e in events:
                teams = parse_teams_from_title(e.title)
                if teams:
                    parsed_ok += 1
                else:
                    failed_parsing.append(e.title)
            
            print(f"{sport.name:20}: {len(events)} events, {sum(1 for e in events if parse_teams_from_title(e.title))} parsed OK")

    print(f"\nTotal Events: {total_events}")
    print(f"Parsed OK:    {parsed_ok}")
    print(f"Failed:       {len(failed_parsing)}")
    
    if failed_parsing:
        print(f"\nSample Failed Titles:")
        for t in failed_parsing[:20]:
            print(f"  - {t}")

if __name__ == "__main__":
    asyncio.run(check_parsing())
