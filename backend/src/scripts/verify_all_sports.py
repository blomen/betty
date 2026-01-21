import asyncio
from backend.src.factory import ExtractorFactory
from backend.src.providers.spectate import SpectateRetriever

async def main():
    factory = ExtractorFactory.get_instance()
    retriever = factory.get_extractor("mrgreen")
    await retriever._ensure_init()
    
    # Use the keys from the retriever's mapping which mirrors sports.json
    sports = list(SpectateRetriever.SPORT_SLUGS.keys())
    results = {}
    
    print(f"Starting verification for {len(sports)} sports: {sports}\n")
    
    for sport in sports:
        print(f"--- Verifying {sport} ---")
        try:
            # Low limit just to check connectivity and parsing
            events = await retriever.extract(sport, limit=30)
            count = len(events)
            results[sport] = count
            print(f"-> SUCCESS: Found {count} events.")
            if count > 0:
                print(f"   Sample: {events[0].name} ({events[0].start_time})")
        except Exception as e:
            results[sport] = f"ERROR: {e}"
            print(f"-> FAILED: {e}")
        print("")

    print("\n=== FINAL RESULTS ===")
    print(f"{'Sport':<20} | {'Events Found':<15}")
    print("-" * 35)
    total = 0
    for sport, res in results.items():
        print(f"{sport:<20} | {res:<15}")
        if isinstance(res, int): total += res
    print("-" * 35)
    print(f"{'TOTAL':<20} | {total:<15}")

if __name__ == "__main__":
    asyncio.run(main())
