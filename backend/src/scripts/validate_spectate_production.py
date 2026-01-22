import asyncio
import logging
from collections import Counter
from backend.src.factory import ExtractorFactory
from backend.src.providers.spectate import SpectateRetriever
import sys

# Configure stdout logging
logging.basicConfig(level=logging.INFO, format='%(levelname)s:%(name)s:%(message)s')
logger = logging.getLogger("validate_spectate")

async def validate_provider(provider_id: str):
    print(f"\n{'='*20} Validating {provider_id.upper()} {'='*20}")
    
    factory = ExtractorFactory.get_instance()
    try:
        retriever = factory.get_extractor(provider_id)
        if not isinstance(retriever, SpectateRetriever):
            print(f"Skipping {provider_id}: Not a SpectateRetriever")
            return
    except Exception as e:
        print(f"Error loading {provider_id}: {e}")
        return

    sports_to_check = ["football", "basketball", "ice_hockey"]
    total_found = 0
    passed = True

    try:
        for sport in sports_to_check:
            print(f"\nChecking Sport: {sport.upper()}")
            try:
                events = await retriever.extract(sport, limit=50)
                count = len(events)
                total_found += count
                
                print(f"  > Events Found: {count}")
                
                if count > 0:
                    # Deep check sample
                    sample = events[0]
                    print(f"  > Sample Event: {sample.name} ({sample.start_time})")
                    print(f"  > Markets: {len(sample.markets)}")
                    
                    market_types = [m.get("type") for m in sample.markets]
                    print(f"  > Types: {', '.join(set(market_types))}")
                    
                    # Validate odds structure
                    valid_odds = True
                    for m in sample.markets:
                        for o in m.get("outcomes", []):
                            if not isinstance(o.get("odds"), (float, int)) or o.get("odds") <= 1.0:
                                valid_odds = False
                                print(f"    ! Invalid Odds detected: {o}")
                    
                    if valid_odds:
                        print("  > Odds Data: OK")
                    else:
                        print("  > Odds Data: FAIL")
                        passed = False
                else:
                    print("  > No events found (Warning)")
                    
            except Exception as e:
                print(f"  > Error extracting {sport}: {e}")
                passed = False

    finally:
        await retriever.close()
        
    print(f"\nResult for {provider_id}: {'PASS' if passed and total_found > 0 else 'FAIL'}")

async def main():
    providers = ["888sport", "mrgreen"]
    
    tasks = [validate_provider(p) for p in providers]
    await asyncio.gather(*tasks)

if __name__ == "__main__":
    asyncio.run(main())
