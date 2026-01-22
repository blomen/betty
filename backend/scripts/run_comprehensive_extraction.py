import asyncio
import logging
import sys
import os
import json

# Add project root and backend to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))) # Add backend folder for 'src' imports

from backend.src.pipeline import ExtractionPipeline
from backend.src.db.models import init_db
from backend.src.factory import ExtractorFactory

# Setup logging
logging.basicConfig(level=logging.WARNING) # Warn for root
logger = logging.getLogger("FullExtraction")
logger.setLevel(logging.INFO)

# Enable provider/transport logs
logging.getLogger("backend.src.providers.snabbare").setLevel(logging.INFO)
logging.getLogger("backend.src.core.transport").setLevel(logging.INFO)

async def main():
    logger.info("Starting Comprehensive Snabbare Extraction...")
    
    # Init DB
    init_db()
    
    engine = ExtractorFactory.get_instance()
    sports = engine.sports # Loaded from sports.json
    
    pipeline = ExtractionPipeline()
    
    print("\n" + "="*80)
    print(f"{'FULL EXTRACTION REPORT':^80}")
    print("="*80 + "\n")
    
    # Deduplicate sports (engine.sports contains leagues)
    unique_sports = sorted(list(set(s.kambi_sport for s in sports if s.kambi_sport)))
    # unique_sports = ['basketball'] # TEMP: Prioritize high volume for verification
    
    # Track stats for final report
    summary_stats = {}

    for sport_name in unique_sports:
        print(f"--- Processing Sport: {sport_name.upper()} ---")
        
        try:
            # We use the pipeline's internal logic but targeted per sport for cleaner output
            # Actually, standard pipeline.run does all sports if providers list is passed.
            # But we want to iterate manually to control the output per sport.
            
            extractor = engine.get_extractor("snabbare")
            events = await extractor.extract(sport_name, limit=100) # Increased limit
            
            if not events:
                print(f"No events found for {sport_name}.\n")
                summary_stats[sport_name] = 0
                continue
                
            count = len(events)
            summary_stats[sport_name] = count
            print(f"Found {count} events:\n")
            
            for event in events:
                print(f"Event: {event.name}")
                print(f"  ID: {event.id}")
                print(f"  Time: {event.start_time}")
                print(f"  League: {event.league}")
                print(f"  Markets: {len(event.markets)}")
                
                # Print first few markets/odds
                for market in event.markets[:3]: # Limit to avoid huge scroll
                    m_name = market.get('name') or market.get('type')
                    print(f"    - Market: {m_name}")
                    for outcome in market.get('outcomes', []):
                        print(f"      {outcome.get('name')}: {outcome.get('odds')}")
                print("-" * 40)
            
            print(f"\nTotal for {sport_name}: {count}\n")
            
        except Exception as e:
            print(f"Error extracting {sport_name}: {e}\n")
            summary_stats[sport_name] = "Error"
            
    # Cleanup
    if hasattr(extractor, 'close'):
         if asyncio.iscoroutinefunction(extractor.close):
             await extractor.close()
         else:
             extractor.close()

    print("\n" + "="*40)
    print(f"{'FINAL EVENT SUMMARY':^40}")
    print("="*40)
    print(f"{'SPORT':<20} | {'EVENTS':>10}")
    print("-" * 33)
    total_all = 0
    for sport, count in summary_stats.items():
        print(f"{sport.title():<20} | {count:>10}")
        if isinstance(count, int):
            total_all += count
    print("-" * 33)
    print(f"{'TOTAL':<20} | {total_all:>10}")
    print("="*40 + "\n")

if __name__ == "__main__":
    asyncio.run(main())
