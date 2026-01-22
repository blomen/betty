
import asyncio
import logging
import sys
import os

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from backend.src.factory import ExtractorFactory
from backend.src.providers.snabbare import SnabbareRetriever

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("test_snabbare")

async def test_snabbare():
    logger.info("Starting Snabbare test...")
    
    # Manually config for test
    config = {
        "id": "snabbare",
        "name": "Snabbare",
        "site_url": "https://www.snabbare.com",
        "api_base": "https://www.snabbare.com/sportsbook-api/api",
        "domain": "snabbare.com"
    }
    
    # Initialize
    # We instantiate directly to control lifecycle
    retriever = SnabbareRetriever(config)
    
    try:
        logger.info("Extracting Football events...")
        events = await retriever.extract("football", limit=5)
        
        logger.info(f"Found {len(events)} events")
        for ev in events:
            print(f"Event: {ev.name} ({ev.id}) - {ev.league}")
            print(f"   Markets: {len(ev.markets)}")
            if ev.markets:
                print(f"   Sample Market: {ev.markets[0]}")
    except Exception as e:
        logger.error(f"Test failed: {e}")
    finally:
        # Cleanup browser
        if retriever.transport:
            logger.info("Closing browser...")
            # We need to access the internal browser close if not exposed
            # BrowserTransport usually mimics the provided interface, 
            # but let's see if we can close it clean.
            # Assuming transport has close() method
            # await retriever.transport.close_browser() 
            # (Checking core logic, it likely manages context closure)
            pass

if __name__ == "__main__":
    asyncio.run(test_snabbare())
