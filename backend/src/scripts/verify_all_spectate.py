import asyncio
import logging
import json
from backend.src.factory import ExtractorFactory
from backend.src.providers.spectate import SpectateRetriever

logging.basicConfig(level=logging.INFO, format='%(levelname)s:%(name)s:%(message)s')
logger = logging.getLogger(__name__)

async def main():
    factory = ExtractorFactory.get_instance()
    retriever = factory.get_extractor("mrgreen")
    if not isinstance(retriever, SpectateRetriever):
        print("Error: Mr Green is not using SpectateRetriever")
        return

    sports = [
        "football", "basketball", "ice_hockey", "american_football", 
        "baseball", "tennis", "cricket", "rugby", "esports", 
        "mma", "boxing", "motorsports"
    ]

    print(f"{'Sport':<20} | {'Digest Count':<12} | {'Extracted':<10} | {'Status'}")
    print("-" * 60)

    for sport in sports:
        try:
            # 1. Get Digest
            sport_slug = retriever.SPORT_SLUGS.get(sport, sport)
            digest_url = f"/eventsrequest/getEventsDigest/{sport_slug}"
            digest = await retriever._fetch_api(digest_url)
            
            digest_total = 0
            if isinstance(digest, dict):
                 digest_total += digest.get("today", 0)
                 digest_total += digest.get("tomorrow", 0)
                 digest_total += digest.get("starting_soon", 0)
                 upcoming = digest.get("upcoming", {})
                 if isinstance(upcoming, dict):
                     digest_total += sum(upcoming.values())

            # 2. Extract
            events = await retriever.extract(sport)
            extracted_count = len(events)
            
            status = "OK" if extracted_count >= digest_total else "MISSING"
            if digest_total == 0 and extracted_count == 0:
                status = "EMPTY (OK)"
            elif extracted_count > digest_total:
                # This can happen if buckets overlap or digest is stale
                status = "OK (DIVERGED+)"

            print(f"{sport:<20} | {digest_total:<12} | {extracted_count:<10} | {status}")
            
        except Exception as e:
            print(f"{sport:<20} | Error: {e}")

    await retriever.close()

if __name__ == "__main__":
    asyncio.run(main())
