"""
Debug why Polymarket events are not being stored.
Mimics pipeline._extract_polymarket logic.
"""
import asyncio
import logging
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from src.config.sports import SPORTS_CONFIG
from src.sources.polymarket import PolymarketSource
from src.pipeline import ExtractionPipeline, parse_teams_from_title

logging.basicConfig(level=logging.INFO)

async def debug_extraction():
    pipeline = ExtractionPipeline()
    
    print(f"\nScanning {len(SPORTS_CONFIG)} sports...")
    
    total_fetched = 0
    total_stored = 0
    rejection_reasons = {}
    
    async with PolymarketSource() as source:
        for sport in SPORTS_CONFIG:
            events = await source.get_game_events(
                series_id=sport.polymarket_series_id,
                sport_name=sport.name,
                limit=100
            )
            
            if not events:
                continue
                
            total_fetched += len(events)
            local_stored = 0
            
            for event in events:
                # 1. Check teams parsing
                teams = parse_teams_from_title(event.title)
                if not teams:
                    reason = "Team Parsing Failed"
                    rejection_reasons[reason] = rejection_reasons.get(reason, 0) + 1
                    continue
                
                # simulate _store_polymarket_event logic regarding markets
                has_valid_odds = False
                
                for market in event.markets:
                    if not market.get("is_active"):
                        # reason = "Market Inactive"
                        # rejection_reasons[reason] = rejection_reasons.get(reason, 0) + 1
                        continue
                    
                    # check odds values
                    valid_outcomes = 0
                    odds_values = market.get("decimal_odds", [])
                    for odds in odds_values:
                        if odds > 1 and odds <= 100:
                            valid_outcomes += 1
                    
                    if valid_outcomes > 0:
                        has_valid_odds = True
                
                if has_valid_odds:
                    local_stored += 1
                    total_stored += 1
                else:
                    reason = "No Valid Odds (Active/Range)"
                    rejection_reasons[reason] = rejection_reasons.get(reason, 0) + 1
            
            if local_stored > 0:
                print(f"  {sport.name:20}: {len(events)} fetched -> {local_stored} stored")
            elif len(events) > 0:
                print(f"  {sport.name:20}: {len(events)} fetched -> 0 stored (All Rejected)")

    print(f"\n{'='*50}")
    print(f"Total Fetched: {total_fetched}")
    print(f"Total Stored:  {total_stored}")
    print(f"Drop Rate:     {100 - (total_stored/total_fetched*100):.1f}%")
    print(f"{'='*50}")
    print("Rejection Reasons:")
    for reason, count in rejection_reasons.items():
        print(f"  {reason}: {count}")

if __name__ == "__main__":
    asyncio.run(debug_extraction())
