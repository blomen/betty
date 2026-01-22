#!/usr/bin/env python
"""
Test bucket response caching for Spectate providers.
Extracts the same sport twice to verify cache hits.
"""

import asyncio
import logging
import time
from backend.src.factory import ExtractorFactory

# Configure logging
logging.basicConfig(
    level=logging.DEBUG,  # DEBUG to see cache logs
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Reduce noise
logging.getLogger("backend.src.core.transport").setLevel(logging.WARNING)


async def main():
    """Test bucket caching."""

    logger.info("=" * 80)
    logger.info("BUCKET CACHING TEST")
    logger.info("=" * 80)
    logger.info("\nTest: Extract basketball twice within 2 minutes")
    logger.info("Expected: Second extraction uses cached buckets (much faster)")
    logger.info("")

    factory = ExtractorFactory()
    retriever = factory.get_extractor("mrgreen")

    try:
        # First extraction (cold cache)
        logger.info("\n[RUN 1] Cold cache - First extraction")
        logger.info("-" * 60)
        start1 = time.time()
        events1 = await retriever.extract("basketball", limit=100)
        duration1 = time.time() - start1
        logger.info(f"[RUN 1] Extracted {len(events1)} events in {duration1:.2f}s")

        # Wait a moment
        await asyncio.sleep(1)

        # Second extraction (warm cache)
        logger.info("\n[RUN 2] Warm cache - Second extraction")
        logger.info("-" * 60)
        start2 = time.time()
        events2 = await retriever.extract("basketball", limit=100)
        duration2 = time.time() - start2
        logger.info(f"[RUN 2] Extracted {len(events2)} events in {duration2:.2f}s")

        # Calculate cache effectiveness
        print("\n" + "=" * 80)
        print("RESULTS")
        print("=" * 80)
        print(f"\nFirst extraction (cold):  {duration1:.3f}s")
        print(f"Second extraction (warm): {duration2:.3f}s")
        print(f"Speedup:                  {duration1/duration2:.2f}x faster")
        print(f"Time saved:               {duration1-duration2:.3f}s")

        improvement = ((duration1 - duration2) / duration1) * 100
        print(f"Improvement:              {improvement:.1f}%")

        # Verify same events
        print(f"\nEvent count match: {len(events1) == len(events2)}")

        if duration2 < duration1 * 0.3:
            print("\n+ SUCCESS: Bucket caching working effectively!")
        elif duration2 < duration1 * 0.8:
            print("\n+ PARTIAL: Some cache hits, but not all buckets cached")
        else:
            print("\n- WARNING: Cache may not be working as expected")

        print("=" * 80 + "\n")

    finally:
        if hasattr(retriever, 'close'):
            await retriever.close()


if __name__ == "__main__":
    asyncio.run(main())
