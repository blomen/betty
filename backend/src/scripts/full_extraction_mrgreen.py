import asyncio
import logging
import json
from collections import Counter
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

    print(f"\n{'Sport':<20} | {'Events':<7} | {'Market Types Found'}")
    print("-" * 80)

    total_extracted = 0
    all_market_counts = Counter()

    for sport in sports:
        try:
            events = await retriever.extract(sport, limit=1000)
            extracted_count = len(events)
            total_extracted += extracted_count
            
            market_types = set()
            deep_sample = None
            
            for ev in events:
                for m in ev.markets:
                    m_type = m.get("type")
                    market_types.add(m_type)
                    all_market_counts[m_type] += 1
                    
                    if not deep_sample and m_type in ["over_under", "spread"]:
                        deep_sample = (ev, m)

            market_str = ", ".join(sorted(market_types)) if market_types else "None"
            print(f"{sport:<20} | {extracted_count:<7} | {market_str}")
            
            if deep_sample:
                ev, m = deep_sample
                print(f"  [Sample Deep Market] {ev.name}: {m.get('type')} line={m.get('outcomes')[0].get('line') if m.get('outcomes') else 'N/A'}")
            
        except Exception as e:
            print(f"{sport:<20} | Error: {e}")

    print("-" * 80)
    print(f"TOTAL EXTRACTED: {total_extracted}")
    print(f"MARKET DISTRIBUTION: {dict(all_market_counts)}")

    await retriever.close()

if __name__ == "__main__":
    asyncio.run(main())
