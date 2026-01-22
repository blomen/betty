#!/usr/bin/env python
"""
Test Spectate optimizations with before/after comparison.
Tests mrgreen with optimizations to measure improvement.
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
    """Run optimization test."""

    # Test subset of sports (fast + medium + slow examples)
    test_sports = [
        "football",      # Many buckets
        "basketball",    # Fast (was 208 ev/s)
        "ice_hockey",    # Medium (was 128 ev/s)
        "tennis",        # Medium (was 86 ev/s)
        "boxing",        # Many 400 errors before
        "cricket",       # Low events
    ]

    logger.info("=" * 80)
    logger.info("SPECTATE OPTIMIZATION TEST (mrgreen)")
    logger.info("=" * 80)
    logger.info("\nOptimizations applied:")
    logger.info("  1. Digest caching (5-minute TTL)")
    logger.info("  2. Better bucket filtering (skip count=0 buckets)")
    logger.info("  3. Parallel bucket fetching (asyncio.gather)")
    logger.info(f"\nTesting {len(test_sports)} sports...")

    factory = ExtractorFactory()
    retriever = factory.get_extractor("mrgreen")

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
    print("OPTIMIZATION RESULTS")
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
    print("EXPECTED IMPROVEMENTS (vs baseline):")
    print("=" * 80)

    # Baseline from previous run (unoptimized sequential)
    baseline = {
        "football": 5.2,
        "basketball": 0.7,
        "ice_hockey": 0.9,
        "tennis": 0.5,
        "boxing": 0.8,
        "cricket": 0.6
    }

    print(f"\n{'Sport':<20} {'Baseline':<12} {'New':<12} {'Savings':<12} {'Improvement'}")
    print("-" * 80)

    for r in results:
        if r['sport'] in baseline:
            old_time = baseline[r['sport']]
            new_time = r['duration']
            savings = old_time - new_time
            improvement = (savings / old_time) * 100

            symbol = "+" if improvement > 0 else "-"
            print(f"{r['sport']:<20} {old_time:<12.2f} {new_time:<12.2f} "
                  f"{savings:<12.2f} {improvement:>5.1f}% {symbol}")

    print("\n" + "=" * 80)
    print("KEY METRICS:")
    print("=" * 80)

    baseline_total = sum(baseline.values())
    current_total = total_duration
    total_savings = baseline_total - current_total
    total_improvement = (total_savings / baseline_total) * 100 if baseline_total > 0 else 0

    print(f"\nBaseline Total Time: {baseline_total:.2f}s")
    print(f"Optimized Total Time: {current_total:.2f}s")
    print(f"Time Saved: {total_savings:.2f}s")
    print(f"Overall Improvement: {total_improvement:.1f}%")

    print("\n" + "=" * 80 + "\n")


if __name__ == "__main__":
    asyncio.run(main())
