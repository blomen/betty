#!/usr/bin/env python3
"""
Provider Performance Profiler

Provides detailed timing breakdown to identify bottlenecks.
Part of the Provider Optimization Workflow.
"""

import asyncio
import argparse
import logging
import sys
import time
from pathlib import Path
from contextlib import asynccontextmanager

# Add backend to path
backend_path = Path(__file__).parent.parent
sys.path.insert(0, str(backend_path))

from src.factory import ExtractorFactory

logging.basicConfig(level=logging.WARNING, format='%(message)s')


class TimingProfiler:
    """Context manager for timing code sections."""

    def __init__(self):
        self.timings = {}
        self.current_section = None
        self.start_time = None

    @asynccontextmanager
    async def section(self, name: str):
        """Time a code section."""
        start = time.time()
        try:
            yield
        finally:
            elapsed = time.time() - start
            if name not in self.timings:
                self.timings[name] = []
            self.timings[name].append(elapsed)

    def print_report(self):
        """Print timing breakdown."""
        print(f"\n{'='*60}")
        print(f"Timing Profile")
        print(f"{'='*60}\n")

        # Calculate totals
        total_time = sum(sum(times) for times in self.timings.values())

        if total_time == 0:
            print("No timing data collected")
            return

        # Sort by total time (descending)
        sorted_sections = sorted(
            self.timings.items(),
            key=lambda x: sum(x[1]),
            reverse=True
        )

        print(f"{'Section':<30} {'Total':>10} {'Calls':>6} {'Avg':>8} {'%':>6}")
        print("-" * 60)

        for section, times in sorted_sections:
            total = sum(times)
            count = len(times)
            avg = total / count
            pct = (total / total_time) * 100

            print(f"{section:<30} {total:>9.1f}s {count:>6} {avg:>7.2f}s {pct:>5.1f}%")

        print("-" * 60)
        print(f"{'TOTAL':<30} {total_time:>9.1f}s")
        print(f"{'='*60}\n")


async def profile_provider(provider_id: str, sport: str = "football", limit: int = 10):
    """Profile a provider extraction with detailed timing."""

    print(f"\n{'='*60}")
    print(f"Profiling Provider: {provider_id}")
    print(f"{'='*60}")
    print(f"Sport: {sport}")
    print(f"Event limit: {limit} (small sample for profiling)")
    print(f"{'='*60}\n")

    profiler = TimingProfiler()

    try:
        factory = ExtractorFactory.get_instance()

        async with profiler.section("factory_init"):
            provider = factory.get_extractor(provider_id)

        print(f"Provider type: {type(provider).__name__}")
        print(f"Starting extraction...\n")

        # Profile the extraction
        async with profiler.section("total_extraction"):
            # Note: We can't instrument internal methods without modifying source,
            # so this provides high-level timing. For detailed profiling, add
            # timing calls in the provider's extract() method.

            events = await provider.extract(sport, limit=limit)

        print(f"Extracted {len(events)} events")

        # Print timing report
        profiler.print_report()

        # Print recommendations
        print_recommendations(profiler.timings)

    except Exception as e:
        print(f"\nERROR: {e}")
        import traceback
        traceback.print_exc()


def print_recommendations(timings: dict):
    """Print optimization recommendations based on timing data."""
    print(f"{'='*60}")
    print(f"Optimization Recommendations")
    print(f"{'='*60}\n")

    total_time = sum(sum(times) for times in timings.values())

    if total_time < 10:
        print("Provider already fast (<10s) - optimization may not be needed")
    elif total_time < 30:
        print("Moderate optimization potential:")
        print("  - Review page load strategy (networkidle -> domcontentloaded)")
        print("  - Reduce wait times by 30-50%")
        print("  - Expected gain: 30-40% faster")
    elif total_time < 60:
        print("Good optimization potential:")
        print("  - Change wait strategy to domcontentloaded")
        print("  - Reduce all timeouts by 50-60%")
        print("  - Increase concurrency if multi-page")
        print("  - Expected gain: 40-60% faster")
    else:
        print("HIGH optimization potential:")
        print("  - Critical: Change from networkidle to domcontentloaded")
        print("  - Aggressive timeout reduction (60-70%)")
        print("  - Increase concurrency significantly")
        print("  - Remove unnecessary waits")
        print("  - Expected gain: 60-70% faster")

    print(f"\nSee: CLAUDE.md (Optimization Patterns section)")
    print(f"{'='*60}\n")


async def main():
    parser = argparse.ArgumentParser(
        description='Profile provider extraction with timing breakdown',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Profile Hajper
  python profile_provider.py hajper

  # Profile Unibet basketball
  python profile_provider.py unibet --sport basketball

Note: This provides high-level timing. For detailed profiling,
add timing instrumentation in the provider's extract() method.
        """
    )

    parser.add_argument('provider_id', help='Provider ID to profile')
    parser.add_argument('--sport', default='football', help='Sport to extract (default: football)')
    parser.add_argument('--limit', type=int, default=10, help='Event limit (default: 10 for speed)')

    args = parser.parse_args()

    try:
        await profile_provider(args.provider_id, args.sport, args.limit)
    except KeyboardInterrupt:
        print("\n\nProfiling cancelled by user")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
