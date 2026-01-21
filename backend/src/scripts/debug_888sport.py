import asyncio
import logging
import sys
import os

# Adjust path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../../")))

from backend.src.factory import ExtractorFactory

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("debug_888sport")

async def main():
    logger.info("Starting 888sport debug...")
    
    factory = ExtractorFactory.get_instance()
    
    try:
        extractor = factory.get_extractor("888sport")
    except Exception as e:
        logger.error(f"Failed to get extractor: {e}")
        return

    logger.info(f"Got extractor: {extractor}")
    
    # Try extraction
    try:
        async with extractor as source:
            logger.info("Attempting extraction for 'football'...")
            events = await source.extract("football", limit=5)
            logger.info(f"Extracted {len(events)} events.")
            for e in events:
                logger.info(f" - {e.name} (Markets: {len(e.markets)})")
                if e.markets:
                    logger.info(f"   First market: {e.markets[0]}")
                
    except Exception as e:
        logger.error(f"Extraction failed: {e}", exc_info=True)

if __name__ == "__main__":
    asyncio.run(main())
