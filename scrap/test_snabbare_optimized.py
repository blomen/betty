#!/usr/bin/env python
"""
Test Snabbare optimizations with before/after comparison.
Tests a subset of sports to quickly validate improvements.
"""

import asyncio
import logging
import time
from backend.src.core.transport import BrowserTransport
from backend.src.providers.snabbare import SnabbareRetriever

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Reduce noise
logging.getLogger("backend.src.core.transport").setLevel(logging.WARNING)


async def test_sport(retriever: SnabbareRetriever, sport: str, limit: int = 50):
    """Test a single sport extraction."""

    logger.info(f"\n{'='*60}")
    logger.info(f"Testing: {sport.upper()}")
    logger.info(f"{'='*60}")

    start = time.time()
    events = await retriever.extract(sport, limit=limit)
    duration = time.time() - start

    logger.info(f"✓ Extracted {len(events)} events in {duration:.1f}s")
    logger.info(f"  Speed: {len(events)/duration:.2f} events/sec" if duration > 0 else "  Speed: N/A")

    return {
        "sport": sport,
        "events": len(events),
        "duration": duration,
        "speed": len(events)/duration if duration > 0 else 0
    }


async def main():
    """Run optimization test."""

    config = {
        "id": "snabbare",
        "name": "Snabbare",
        "api_base": "https://www.snabbare.com/sportsbook-api/api",
        "site_url": "https://www.snabbare.com"
    }

    # Test subset of sports (fast + slow examples)
    test_sports = [
        "football",      # Many leagues (was 100s)
        "basketball",    # Many leagues (was 108s)
        "cricket",       # Low yield sport (was 51s)
        "tennis",        # Medium speed (was 73s)
    ]

    logger.info("=" * 80)
    logger.info("SNABBARE OPTIMIZATION TEST")
    logger.info("=" * 80)
    logger.info("\nOptimizations applied:")
    logger.info("  1. Empty league timeout: 15s → 5s")
    logger.info("  2. Smart scroll timeout: 60s → 30s")
    logger.info("  3. Early empty detection (skip scraping)")
    logger.info("  4. Concurrency increased: 5 → 10 parallel tabs")
    logger.info(f"\nTesting {len(test_sports)} sports...")

    transport = BrowserTransport(headless=False)
    retriever = SnabbareRetriever(config, transport)

    results = []
    total_start = time.time()

    try:
        for sport in test_sports:
            result = await test_sport(retriever, sport, limit=100)
            results.append(result)
            await asyncio.sleep(1)

    finally:
        await retriever.close()

    total_duration = time.time() - total_start

    # Print summary
    print("\n" + "=" * 80)
    print("OPTIMIZATION RESULTS")
    print("=" * 80)

    print(f"\n{'Sport':<20} {'Events':<10} {'Time (s)':<12} {'Speed (ev/s)':<15}")
    print("-" * 80)

    total_events = 0
    for r in results:
        print(f"{r['sport']:<20} {r['events']:<10} {r['duration']:<12.1f} {r['speed']:<15.2f}")
        total_events += r['events']

    print("-" * 80)
    print(f"{'TOTAL':<20} {total_events:<10} {total_duration:<12.1f} "
          f"{total_events/total_duration:<15.2f}")

    print("\n" + "=" * 80)
    print("EXPECTED IMPROVEMENTS (vs baseline):")
    print("=" * 80)

    baseline = {
        "football": 100.3,
        "basketball": 108.2,
        "cricket": 51.4,
        "tennis": 73.1
    }

    print(f"\n{'Sport':<20} {'Baseline':<12} {'New':<12} {'Savings':<12} {'Improvement'}")
    print("-" * 80)

    for r in results:
        if r['sport'] in baseline:
            old_time = baseline[r['sport']]
            new_time = r['duration']
            savings = old_time - new_time
            improvement = (savings / old_time) * 100

            print(f"{r['sport']:<20} {old_time:<12.1f} {new_time:<12.1f} "
                  f"{savings:<12.1f} {improvement:>5.1f}%")

    print("\n" + "=" * 80 + "\n")


if __name__ == "__main__":
    asyncio.run(main())
