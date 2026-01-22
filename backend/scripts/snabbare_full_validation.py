
import asyncio
import logging
import sys
import os

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from backend.src.providers.snabbare import SnabbareRetriever
from backend.src.core.transport import BrowserTransport

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("full_validation")

async def main():
    transport = BrowserTransport(headless=True)
    # Instantiate with dummy config
    retriever = SnabbareRetriever({"id":"snabbare"}, transport=transport)
    
    # Sports to validate
    sports = list(SnabbareRetriever.SPORT_IDS.keys())
    
    # Increase limit for "full extraction" simulation
    # But keep it reasonable for a script run (e.g. 100 or 500)
    # The user asked for "full extraction", but implies checking counts.
    limit_per_sport = 500
    
    results = {}
    
    try:
        await retriever._ensure_init()
        
        for sport in sports:
            logger.info(f"Validating {sport}...")
            try:
                # Extract with higher limit
                events = await retriever.extract(sport, limit=limit_per_sport)
                count = len(events)
                results[sport] = count
                logger.info(f"> {sport}: {count} events found")
                
                if count > 0:
                   logger.info(f"  Sample: {events[0].name} ({events[0].id})")
            except Exception as e:
                logger.error(f"Error validating {sport}: {e}")
                results[sport] = "Error"
                
        print("\n--- VALIDATION SUMMARY ---")
        for s, c in results.items():
            print(f"{s}: {c}")

    except Exception as e:
        logger.error(f"Global error: {e}")
    finally:
        if transport.browser:
            await transport.browser.close()

if __name__ == "__main__":
    asyncio.run(main())
