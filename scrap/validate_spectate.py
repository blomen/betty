#!/usr/bin/env python
"""
Validate Spectate providers (mrgreen & 888sport).
Tests both providers across all sports with performance monitoring.
"""

import asyncio
import logging
import json
import time
from pathlib import Path
from typing import Dict, List
from backend.src.factory import ExtractorFactory

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Reduce transport noise
logging.getLogger("backend.src.core.transport").setLevel(logging.WARNING)


class PerformanceMonitor:
    """Track performance metrics."""

    def __init__(self):
        self.metrics = []

    def record(self, provider: str, sport: str, event_count: int, duration: float, buckets: int = 0):
        self.metrics.append({
            "provider": provider,
            "sport": sport,
            "event_count": event_count,
            "duration": duration,
            "buckets": buckets,
            "events_per_second": event_count / duration if duration > 0 else 0
        })

    def print_summary(self, provider: str):
        provider_metrics = [m for m in self.metrics if m["provider"] == provider]

        if not provider_metrics:
            return

        print(f"\n{'='*80}")
        print(f"{provider.upper()} PERFORMANCE SUMMARY")
        print(f"{'='*80}")

        total_events = sum(m["event_count"] for m in provider_metrics)
        total_duration = sum(m["duration"] for m in provider_metrics)

        print(f"\nTotal Events: {total_events}")
        print(f"Total Time: {total_duration:.1f}s")
        print(f"Average Speed: {total_events / total_duration:.2f} events/sec" if total_duration > 0 else "N/A")

        print(f"\n{'Sport':<20} {'Events':<10} {'Time (s)':<12} {'Events/sec':<12} {'Buckets'}")
        print("-" * 80)

        for m in provider_metrics:
            print(f"{m['sport']:<20} {m['event_count']:<10} {m['duration']:<12.1f} "
                  f"{m['events_per_second']:<12.2f} {m['buckets']}")

        # Identify bottlenecks
        print(f"\n{'='*80}")
        print(f"{provider.upper()} BOTTLENECK ANALYSIS")
        print(f"{'='*80}")

        slowest = sorted(provider_metrics, key=lambda x: x['duration'], reverse=True)[:3]
        print("\nSlowest Sports:")
        for i, m in enumerate(slowest, 1):
            print(f"{i}. {m['sport']}: {m['duration']:.1f}s ({m['buckets']} buckets checked)")

        lowest_yield = sorted([m for m in provider_metrics if m['duration'] > 0],
                             key=lambda x: x['events_per_second'])[:3]
        print("\nLowest Event Yield:")
        for i, m in enumerate(lowest_yield, 1):
            print(f"{i}. {m['sport']}: {m['events_per_second']:.2f} events/sec "
                  f"({m['event_count']} events in {m['duration']:.1f}s)")


async def validate_provider_sport(provider_id: str, sport: str, factory: ExtractorFactory, monitor: PerformanceMonitor) -> Dict:
    """Validate a single provider for a single sport."""

    logger.info(f"\n{'='*60}")
    logger.info(f"{provider_id.upper()} - {sport.upper()}")
    logger.info(f"{'='*60}")

    result = {
        "provider": provider_id,
        "sport": sport,
        "success": False,
        "event_count": 0,
        "duration": 0,
        "error": None
    }

    start = time.time()

    try:
        retriever = factory.get_extractor(provider_id)
        events = await retriever.extract(sport, limit=100)

        duration = time.time() - start
        result["success"] = True
        result["event_count"] = len(events)
        result["duration"] = duration

        if events:
            logger.info(f"✓ SUCCESS: {len(events)} events in {duration:.1f}s ({len(events)/duration:.2f} ev/s)")

            # Sample events
            logger.info(f"\nSample events:")
            for i, e in enumerate(events[:3], 1):
                logger.info(f"  {i}. {e.home_team} vs {e.away_team} ({e.league})")
                if e.markets:
                    market = e.markets[0]
                    odds_str = ", ".join([f"{o['name']}: {o['price']}" for o in market['outcomes'][:2]])
                    logger.info(f"     {market['type']}: {odds_str}")
        else:
            logger.warning(f"⚠ No events found")

        # Record metrics
        monitor.record(provider_id, sport, len(events), duration)

    except Exception as e:
        duration = time.time() - start
        result["error"] = str(e)
        result["duration"] = duration
        logger.error(f"✗ ERROR: {e}")
        monitor.record(provider_id, sport, 0, duration)

    return result


async def main():
    """Main validation routine."""

    # Spectate providers to test
    providers = ["mrgreen", "888sport"]

    # Load sports from config
    sports_file = Path("backend/src/config/sports.json")
    with open(sports_file) as f:
        sports_config = json.load(f)

    sports = [s["key"] for s in sports_config]

    logger.info("=" * 80)
    logger.info("SPECTATE PROVIDERS VALIDATION")
    logger.info("=" * 80)
    logger.info(f"\nProviders: {', '.join(providers)}")
    logger.info(f"Sports: {', '.join(sports)}")
    logger.info(f"\nTotal tests: {len(providers)} × {len(sports)} = {len(providers) * len(sports)}")
    logger.info("\nNote: Spectate uses API calls (not DOM scraping)")

    # Initialize
    factory = ExtractorFactory()
    monitor = PerformanceMonitor()

    all_results = []
    overall_start = time.time()

    try:
        for provider_id in providers:
            logger.info(f"\n{'='*80}")
            logger.info(f"TESTING: {provider_id.upper()}")
            logger.info(f"{'='*80}")

            provider_start = time.time()
            provider_results = []

            for sport in sports:
                result = await validate_provider_sport(provider_id, sport, factory, monitor)
                provider_results.append(result)
                all_results.append(result)

                # Small delay between sports
                await asyncio.sleep(0.5)

            provider_duration = time.time() - provider_start

            # Print provider summary
            monitor.print_summary(provider_id)

            successful = sum(1 for r in provider_results if r["success"] and r["event_count"] > 0)
            empty = sum(1 for r in provider_results if r["success"] and r["event_count"] == 0)
            failed = sum(1 for r in provider_results if not r["success"])
            total_events = sum(r["event_count"] for r in provider_results)

            print(f"\n{provider_id.upper()} Summary:")
            print(f"  ✓ Success with events: {successful}/{len(sports)}")
            print(f"  ⚠ Empty (no events):   {empty}/{len(sports)}")
            print(f"  ✗ Failed/Error:        {failed}/{len(sports)}")
            print(f"  📊 Total events:       {total_events}")
            print(f"  ⏱  Total time:         {provider_duration:.1f}s")
            print(f"  🚀 Speed:              {total_events/provider_duration:.2f} events/sec")

    finally:
        # Close retrievers
        for provider_id in providers:
            try:
                retriever = factory.get_extractor(provider_id)
                if hasattr(retriever, 'close'):
                    await retriever.close()
            except:
                pass

    overall_duration = time.time() - overall_start

    # Final summary
    print("\n" + "=" * 80)
    print("FINAL SUMMARY - BOTH PROVIDERS")
    print("=" * 80)

    for provider_id in providers:
        provider_results = [r for r in all_results if r["provider"] == provider_id]
        successful = sum(1 for r in provider_results if r["success"] and r["event_count"] > 0)
        total_events = sum(r["event_count"] for r in provider_results)
        total_time = sum(r["duration"] for r in provider_results)

        print(f"\n{provider_id.upper()}:")
        print(f"  Success rate: {successful}/{len(sports)} sports ({successful/len(sports)*100:.1f}%)")
        print(f"  Total events: {total_events}")
        print(f"  Total time:   {total_time:.1f}s")
        print(f"  Speed:        {total_events/total_time:.2f} events/sec" if total_time > 0 else "N/A")

    print(f"\n{'='*80}")
    print(f"Overall Time: {overall_duration:.1f}s")
    print(f"{'='*80}\n")

    # Optimization recommendations
    print("\n" + "=" * 80)
    print("OPTIMIZATION RECOMMENDATIONS")
    print("=" * 80)

    print("\nBased on performance analysis:")
    print("1. Check for slow API endpoints (>5s per sport)")
    print("2. Identify sports with many empty buckets (wasted API calls)")
    print("3. Consider bucket filtering based on digest data")
    print("4. Evaluate if parallel bucket fetching would help")
    print("5. Monitor API rate limiting (429 errors)")

    print("\n" + "=" * 80 + "\n")


if __name__ == "__main__":
    asyncio.run(main())
