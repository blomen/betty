#!/usr/bin/env python
"""
Validate Snabbare DOM scraper functionality.

Tests:
1. Browser initialization
2. League discovery via API
3. DOM scraping of match cards
4. Odds extraction
"""

import asyncio
import logging
from backend.src.core.transport import BrowserTransport
from backend.src.providers.snabbare import SnabbareRetriever

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


async def validate_snabbare():
    """Validate Snabbare scraper functionality."""

    config = {
        "id": "snabbare",
        "name": "Snabbare",
        "api_base": "https://www.snabbare.com/sportsbook-api/api",
        "site_url": "https://www.snabbare.com"
    }

    # Use visible browser for debugging (headless=False)
    transport = BrowserTransport(headless=False)
    retriever = SnabbareRetriever(config, transport)

    try:
        logger.info("=" * 60)
        logger.info("SNABBARE VALIDATION TEST")
        logger.info("=" * 60)

        # Test football extraction
        sport = "football"
        logger.info(f"\n[TEST 1] Extracting {sport} events...")
        events = await retriever.extract(sport, limit=10)

        logger.info(f"\n[RESULT] Retrieved {len(events)} events")

        if events:
            logger.info("\n[SAMPLE EVENTS]")
            for i, event in enumerate(events[:3], 1):
                logger.info(f"\nEvent {i}:")
                logger.info(f"  ID: {event.id}")
                logger.info(f"  Match: {event.home_team} vs {event.away_team}")
                logger.info(f"  League: {event.league}")
                logger.info(f"  Start: {event.start_time}")
                logger.info(f"  URL: {event.url}")

                if event.markets:
                    market = event.markets[0]
                    logger.info(f"  Market: {market['type']}")
                    for outcome in market['outcomes']:
                        logger.info(f"    {outcome['name']}: {outcome['price']}")
        else:
            logger.warning("[WARNING] No events extracted!")

        logger.info("\n" + "=" * 60)
        logger.info("VALIDATION COMPLETE")
        logger.info("=" * 60)

    except Exception as e:
        logger.error(f"[ERROR] Validation failed: {e}", exc_info=True)
    finally:
        await retriever.close()


if __name__ == "__main__":
    asyncio.run(validate_snabbare())
