
import asyncio
import logging
import sys
import os
import argparse
import json

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from backend.src.providers.snabbare import SnabbareRetriever
from backend.src.core.transport import BrowserTransport

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("snabbare_stats")

async def main():
    parser = argparse.ArgumentParser(description="Count events for specific sport in Snabbare")
    parser.add_argument("sport", help="Sport key (e.g. football, basketball)")
    args = parser.parse_args()
    
    sport = args.sport.lower()
    
    logger.info(f"Starting stats extraction for: {sport}")
    
    config = {
        "id": "snabbare", 
        "site_url": "https://www.snabbare.com", 
        "api_base": "https://www.snabbare.com/sportsbook-api/api"
    }
    
    transport = BrowserTransport(headless=True)
    retriever = SnabbareRetriever(config, transport=transport)
    
    try:
        events = await retriever.extract(sport, limit=10000)
        print(f"\nRESULTS_FOR_{sport.upper()}:{len(events)}")
    except Exception as e:
        logger.error(f"Failed: {e}")
    finally:
        if transport.browser:
            await transport.browser.close()

if __name__ == "__main__":
    asyncio.run(main())
