#!/usr/bin/env python3
"""
Test Gecko performance with optimizations.
Compare before/after optimization timings.
"""

import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

from src.factory import ExtractorFactory

async def test_provider_performance(provider_name: str, sports: list = None):
    """Test extraction performance for a provider"""

    if sports is None:
        sports = ["football", "basketball", "tennis"]  # Test subset

    print(f"\n{'='*80}")
    print(f"PERFORMANCE TEST: {provider_name.upper()}")
    print(f"Testing {len(sports)} sports")
    print(f"{'='*80}\n")

    provider = ExtractorFactory.get_instance().get_extractor(provider_name)

    sport_times = {}
    sport_events = {}
    total_start = time.time()

    for sport in sports:
        try:
            print(f"[{sport:15s}] Extracting...", end=" ", flush=True)
            start = time.time()
            events = await provider.extract(sport, limit=20)
            elapsed = time.time() - start

            sport_times[sport] = elapsed
            sport_events[sport] = len(events)

            print(f"{len(events):3d} events in {elapsed:5.1f}s")

        except Exception as e:
            print(f"ERROR: {e}")
            sport_times[sport] = 0
            sport_events[sport] = 0

    total_elapsed = time.time() - total_start

    # Summary
    print(f"\n{'='*80}")
    print("PERFORMANCE SUMMARY")
    print(f"{'='*80}\n")

    total_events = sum(sport_events.values())
    avg_time = sum(sport_times.values()) / len(sport_times) if sport_times else 0
    min_time = min(sport_times.values()) if sport_times else 0
    max_time = max(sport_times.values()) if sport_times else 0

    print(f"Total events extracted: {total_events}")
    print(f"Total time: {total_elapsed:.1f}s")
    print(f"Average time per sport: {avg_time:.1f}s")
    print(f"Fastest sport: {min_time:.1f}s")
    print(f"Slowest sport: {max_time:.1f}s")

    # Compare to baseline (pre-optimization)
    baseline_avg = 22.0  # From validation results
    speedup = baseline_avg - avg_time
    speedup_pct = (speedup / baseline_avg) * 100

    print(f"\nOptimization Results:")
    print(f"  Baseline average: {baseline_avg:.1f}s")
    print(f"  Optimized average: {avg_time:.1f}s")
    print(f"  Improvement: {speedup:.1f}s ({speedup_pct:.1f}% faster)")

    # Extrapolate to all 12 sports
    projected_time = avg_time * 12
    baseline_time = baseline_avg * 12
    time_saved = baseline_time - projected_time

    print(f"\nProjected time for all 12 sports:")
    print(f"  Before: {baseline_time/60:.1f} minutes")
    print(f"  After: {projected_time/60:.1f} minutes")
    print(f"  Time saved: {time_saved:.0f}s ({time_saved/60:.1f} minutes)")

    print(f"\n{'='*80}\n")

    return {
        "total_events": total_events,
        "total_time": total_elapsed,
        "avg_time": avg_time,
        "sports": sport_times
    }

async def main():
    """Test all Gecko providers"""

    providers = ["betsson"]  # Test one provider first

    if len(sys.argv) > 1:
        providers = [sys.argv[1]]

    # Test sports (subset for quick test)
    test_sports = ["football", "basketball", "tennis", "ice_hockey"]

    print("\n" + "="*80)
    print("GECKO PERFORMANCE OPTIMIZATION TEST")
    print("="*80)
    print(f"\nTesting {len(providers)} provider(s) with {len(test_sports)} sports")
    print("\nOptimizations enabled:")
    print("  - Headless mode: True (saves ~2-3s per sport)")
    print("  - Reduced wait times: 7s + 2s (saves ~4s per sport)")
    print("  - Expected speedup: ~6s per sport")

    all_results = {}

    for provider in providers:
        results = await test_provider_performance(provider, test_sports)
        all_results[provider] = results

        if len(providers) > 1:
            print("Waiting 5 seconds before next provider...\n")
            await asyncio.sleep(5)

if __name__ == "__main__":
    asyncio.run(main())
