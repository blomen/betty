#!/usr/bin/env python
"""
Full validation of Snabbare provider.
Tests all sports with full extraction and performance monitoring.
"""

import asyncio
import logging
import json
import time
from pathlib import Path
from typing import Dict, List
from backend.src.core.transport import BrowserTransport
from backend.src.providers.snabbare import SnabbareRetriever

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Reduce transport noise but keep provider logs
logging.getLogger("backend.src.core.transport").setLevel(logging.WARNING)


class PerformanceMonitor:
    """Track performance metrics."""

    def __init__(self):
        self.metrics = []

    def record(self, sport: str, event_count: int, duration: float, leagues_checked: int = 0):
        self.metrics.append({
            "sport": sport,
            "event_count": event_count,
            "duration": duration,
            "leagues_checked": leagues_checked,
            "events_per_second": event_count / duration if duration > 0 else 0
        })

    def print_summary(self):
        print("\n" + "=" * 80)
        print("PERFORMANCE SUMMARY")
        print("=" * 80)

        total_events = sum(m["event_count"] for m in self.metrics)
        total_duration = sum(m["duration"] for m in self.metrics)

        print(f"\nTotal Events: {total_events}")
        print(f"Total Time: {total_duration:.1f}s")
        print(f"Average Speed: {total_events / total_duration:.2f} events/sec")

        print(f"\n{'Sport':<20} {'Events':<10} {'Time (s)':<12} {'Events/sec':<12} {'Leagues'}")
        print("-" * 80)

        for m in self.metrics:
            print(f"{m['sport']:<20} {m['event_count']:<10} {m['duration']:<12.1f} "
                  f"{m['events_per_second']:<12.2f} {m['leagues_checked']}")

        # Identify bottlenecks
        print("\n" + "=" * 80)
        print("BOTTLENECK ANALYSIS")
        print("=" * 80)

        slowest = sorted(self.metrics, key=lambda x: x['duration'], reverse=True)[:3]
        print("\nSlowest Sports:")
        for i, m in enumerate(slowest, 1):
            print(f"{i}. {m['sport']}: {m['duration']:.1f}s ({m['leagues_checked']} leagues)")

        lowest_yield = sorted([m for m in self.metrics if m['duration'] > 0],
                             key=lambda x: x['events_per_second'])[:3]
        print("\nLowest Event Yield:")
        for i, m in enumerate(lowest_yield, 1):
            print(f"{i}. {m['sport']}: {m['events_per_second']:.2f} events/sec "
                  f"({m['event_count']} events in {m['duration']:.1f}s)")


async def validate_sport(retriever: SnabbareRetriever, sport: str, monitor: PerformanceMonitor) -> Dict:
    """Validate a single sport with full extraction."""

    logger.info(f"\n{'='*80}")
    logger.info(f"EXTRACTING: {sport.upper()}")
    logger.info(f"{'='*80}\n")

    result = {
        "sport": sport,
        "success": False,
        "event_count": 0,
        "duration": 0,
        "error": None
    }

    start_time = time.time()

    try:
        # Full extraction (no limit)
        events = await retriever.extract(sport, limit=10000)

        duration = time.time() - start_time
        result["success"] = True
        result["event_count"] = len(events)
        result["duration"] = duration

        # Log sample events
        if events:
            logger.info(f"\n✓ SUCCESS: {len(events)} events in {duration:.1f}s")
            logger.info(f"\nSample events:")
            for i, e in enumerate(events[:5], 1):
                logger.info(f"  {i}. {e.home_team} vs {e.away_team} ({e.league})")
                if e.markets:
                    market = e.markets[0]
                    odds_str = ", ".join([f"{o['name']}: {o['price']}" for o in market['outcomes'][:3]])
                    logger.info(f"     {market['type']}: {odds_str}")
        else:
            logger.warning(f"⚠ No events found for {sport}")

        # Record metrics
        monitor.record(sport, len(events), duration)

    except Exception as e:
        duration = time.time() - start_time
        result["error"] = str(e)
        result["duration"] = duration
        logger.error(f"✗ ERROR: {e}")
        logger.exception(e)
        monitor.record(sport, 0, duration)

    return result


async def main():
    """Main validation routine."""

    # Load sports from config
    sports_file = Path("backend/src/config/sports.json")
    with open(sports_file) as f:
        sports_config = json.load(f)

    # All sports supported by snabbare
    sports = [s["key"] for s in sports_config]

    logger.info("=" * 80)
    logger.info("SNABBARE FULL VALIDATION")
    logger.info("=" * 80)
    logger.info(f"\nSports to test: {len(sports)}")
    logger.info(f"Sports: {', '.join(sports)}")
    logger.info(f"\nRunning full extraction (no limits)...")

    # Initialize
    config = {
        "id": "snabbare",
        "name": "Snabbare",
        "api_base": "https://www.snabbare.com/sportsbook-api/api",
        "site_url": "https://www.snabbare.com"
    }

    transport = BrowserTransport(headless=False)
    retriever = SnabbareRetriever(config, transport)
    monitor = PerformanceMonitor()

    results = []
    overall_start = time.time()

    try:
        # Test each sport sequentially
        for sport in sports:
            result = await validate_sport(retriever, sport, monitor)
            results.append(result)

            # Small delay between sports
            await asyncio.sleep(1)

    finally:
        await retriever.close()

    overall_duration = time.time() - overall_start

    # Print performance summary
    monitor.print_summary()

    # Print final summary
    print("\n" + "=" * 80)
    print("FINAL SUMMARY")
    print("=" * 80)

    successful = sum(1 for r in results if r["success"] and r["event_count"] > 0)
    empty = sum(1 for r in results if r["success"] and r["event_count"] == 0)
    failed = sum(1 for r in results if not r["success"])
    total_events = sum(r["event_count"] for r in results)

    print(f"\n✓ Success with events: {successful}/{len(sports)}")
    print(f"⚠ Empty (no events):   {empty}/{len(sports)}")
    print(f"✗ Failed/Error:        {failed}/{len(sports)}")
    print(f"📊 Total events:       {total_events}")
    print(f"⏱  Total time:         {overall_duration:.1f}s")
    print(f"🚀 Overall speed:      {total_events / overall_duration:.2f} events/sec")

    print("\n" + "=" * 80)

    # Optimization recommendations
    print("\nOPTIMIZATION RECOMMENDATIONS:")
    print("-" * 80)

    avg_time = sum(r["duration"] for r in results) / len(results)
    slow_sports = [r for r in results if r["duration"] > avg_time * 2]

    if slow_sports:
        print("\n1. SLOW SPORTS (>2x average time):")
        for r in slow_sports:
            print(f"   - {r['sport']}: {r['duration']:.1f}s")
        print("   → Consider implementing league filtering or pagination")

    if empty:
        print(f"\n2. EMPTY RESULTS ({empty} sports):")
        empty_sports = [r["sport"] for r in results if r["success"] and r["event_count"] == 0]
        print(f"   - Sports: {', '.join(empty_sports)}")
        print("   → Check if sport IDs are correct or events are out of season")

    if failed:
        print(f"\n3. ERRORS ({failed} sports):")
        failed_sports = [(r["sport"], r["error"]) for r in results if not r["success"]]
        for sport, error in failed_sports:
            print(f"   - {sport}: {error[:60]}...")
        print("   → Check for missing sport mappings or API changes")

    print("\n" + "=" * 80 + "\n")


if __name__ == "__main__":
    asyncio.run(main())
