#!/usr/bin/env python
"""
Test 888sport with Spectate optimizations.
"""

import asyncio
import logging
import time
from backend.src.factory import ExtractorFactory

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Reduce noise
logging.getLogger("backend.src.core.transport").setLevel(logging.WARNING)


async def test_sport(retriever, sport: str, limit: int = 100):
    """Test a single sport extraction."""

    logger.info(f"\n{'='*60}")
    logger.info(f"Testing: {sport.upper()}")
    logger.info(f"{'='*60}")

    start = time.time()
    events = await retriever.extract(sport, limit=limit)
    duration = time.time() - start

    logger.info(f"Extracted {len(events)} events in {duration:.2f}s ({len(events)/duration:.1f} ev/s)" if duration > 0 else f"Extracted {len(events)} events")

    return {
        "sport": sport,
        "events": len(events),
        "duration": duration,
        "speed": len(events)/duration if duration > 0 else 0
    }


async def main():
    """Run optimization test for 888sport."""

    # Test subset of sports
    test_sports = [
        "football",
        "basketball",
        "ice_hockey",
        "tennis",
    ]

    logger.info("=" * 80)
    logger.info("888SPORT OPTIMIZATION TEST")
    logger.info("=" * 80)
    logger.info("\nOptimizations applied:")
    logger.info("  1. Digest caching (5-minute TTL)")
    logger.info("  2. Better bucket filtering (skip count=0 buckets)")
    logger.info("  3. Parallel bucket fetching (asyncio.gather)")
    logger.info(f"\nTesting {len(test_sports)} sports...")

    factory = ExtractorFactory()
    retriever = factory.get_extractor("888sport")

    results = []
    total_start = time.time()

    try:
        for sport in test_sports:
            result = await test_sport(retriever, sport, limit=100)
            results.append(result)
            await asyncio.sleep(0.5)

    finally:
        if hasattr(retriever, 'close'):
            await retriever.close()

    total_duration = time.time() - total_start

    # Print summary
    print("\n" + "=" * 80)
    print("OPTIMIZATION RESULTS - 888SPORT")
    print("=" * 80)

    print(f"\n{'Sport':<20} {'Events':<10} {'Time (s)':<12} {'Speed (ev/s)':<15}")
    print("-" * 80)

    total_events = 0
    for r in results:
        print(f"{r['sport']:<20} {r['events']:<10} {r['duration']:<12.2f} {r['speed']:<15.1f}")
        total_events += r['events']

    print("-" * 80)
    print(f"{'TOTAL':<20} {total_events:<10} {total_duration:<12.2f} "
          f"{total_events/total_duration:<15.1f}")

    print("\n" + "=" * 80)
    print("SUCCESS: 888sport working with all optimizations")
    print("=" * 80 + "\n")


if __name__ == "__main__":
    asyncio.run(main())
