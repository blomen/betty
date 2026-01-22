from datetime import datetime
import asyncio
import logging
import sys
import os

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../')))

from backend.src.core.transport import BrowserTransport
from backend.src.providers.snabbare import SnabbareRetriever

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("VerifySnabbare")

async def test_smart_scroll():
    logger.info("Starting Smart Scroll Verification...")
    
    # Initialize transport and retriever
    transport = BrowserTransport(headless=True) # Set to False if you want to watch
    retriever = SnabbareRetriever(config={}, transport=transport)
    
    try:
        # We need to manually initialize to get the page ready if we were calling internal methods,
        # but extract() handles init.
        
        # Test extraction on a busy sport like Football
        logger.info("Extracting Football events...")
        events = await retriever.extract(sport="football", limit=50)
        
        logger.info(f"Extracted {len(events)} events.")
        
        if len(events) > 20:
             logger.info("SUCCESS: Extracted more than 20 events, generic pagination/scrolling worked!")
        else:
             logger.warning(f"Result count {len(events)} is low. This might indicate scaffolding failed or just few events available.")
             
        # Print first few events to verify data quality
        for ev in events[:3]:
            print(f" - {ev.name} ({ev.start_time}) [{ev.id}]")

    except Exception as e:
        logger.error(f"Verification failed: {e}")
    finally:
        await transport.close()

if __name__ == "__main__":
    asyncio.run(test_smart_scroll())
