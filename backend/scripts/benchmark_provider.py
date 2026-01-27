#!/usr/bin/env python3
"""
Provider Performance Benchmark Tool

Systematically measures extraction performance for any provider.
Part of the Provider Optimization Workflow.
"""

import asyncio
import argparse
import logging
import sys
import time
from pathlib import Path
from typing import List, Dict
import statistics

# Add backend to path
backend_path = Path(__file__).parent.parent
sys.path.insert(0, str(backend_path))

from src.factory import ExtractorFactory

# Configure logging
logging.basicConfig(
    level=logging.WARNING,  # Suppress info logs for cleaner output
    format='%(message)s'
)


class ProviderBenchmark:
    """Benchmark a provider's extraction performance."""

    def __init__(self, provider_id: str, sport: str = "football"):
        self.provider_id = provider_id
        self.sport = sport
        self.factory = ExtractorFactory.get_instance()

    async def run_single(self, limit: int = 50) -> Dict:
        """Run a single extraction and measure performance."""
        provider = self.factory.get_extractor(self.provider_id)

        start_time = time.time()
        events = await provider.extract(self.sport, limit=limit)
        elapsed = time.time() - start_time

        return {
            'elapsed': elapsed,
            'events': len(events),
            'time_per_event': elapsed / len(events) if events else 0,
            'success': len(events) > 0
        }

    async def run_benchmark(self, runs: int = 3, limit: int = 50) -> Dict:
        """Run multiple extractions and aggregate results."""
        print(f"\n{'='*60}")
        print(f"Benchmarking Provider: {self.provider_id}")
        print(f"{'='*60}")
        print(f"Sport: {self.sport}")
        print(f"Runs: {runs}")
        print(f"Event limit: {limit}")
        print(f"{'='*60}\n")

        results = []

        for i in range(runs):
            print(f"Run {i+1}/{runs}...", end=' ', flush=True)

            try:
                result = await self.run_single(limit)
                results.append(result)

                status = "OK" if result['success'] else "FAILED"
                print(f"{status} - {result['elapsed']:.1f}s - {result['events']} events")

            except Exception as e:
                print(f"ERROR: {e}")
                results.append({
                    'elapsed': 0,
                    'events': 0,
                    'time_per_event': 0,
                    'success': False,
                    'error': str(e)
                })

            # Brief pause between runs
            if i < runs - 1:
                await asyncio.sleep(2)

        return self._aggregate_results(results)

    def _aggregate_results(self, results: List[Dict]) -> Dict:
        """Aggregate multiple run results."""
        successful_runs = [r for r in results if r['success']]

        if not successful_runs:
            return {
                'success_rate': 0,
                'total_runs': len(results),
                'avg_time': 0,
                'avg_events': 0,
                'avg_time_per_event': 0,
            }

        times = [r['elapsed'] for r in successful_runs]
        event_counts = [r['events'] for r in successful_runs]
        time_per_events = [r['time_per_event'] for r in successful_runs]

        return {
            'success_rate': len(successful_runs) / len(results) * 100,
            'total_runs': len(results),
            'avg_time': statistics.mean(times),
            'min_time': min(times),
            'max_time': max(times),
            'stdev_time': statistics.stdev(times) if len(times) > 1 else 0,
            'avg_events': statistics.mean(event_counts),
            'avg_time_per_event': statistics.mean(time_per_events),
        }

    def print_report(self, results: Dict):
        """Print benchmark report."""
        print(f"\n{'='*60}")
        print(f"Benchmark Results")
        print(f"{'='*60}")

        if results['success_rate'] == 0:
            print("\nAll runs FAILED - Check provider implementation")
            return

        print(f"\nSuccess Rate: {results['success_rate']:.0f}% ({results['total_runs']} runs)")
        print(f"\nTiming:")
        print(f"  Average:  {results['avg_time']:.1f}s")
        print(f"  Min:      {results['min_time']:.1f}s")
        print(f"  Max:      {results['max_time']:.1f}s")
        print(f"  StdDev:   {results['stdev_time']:.1f}s")

        print(f"\nEvents:")
        print(f"  Average:  {results['avg_events']:.0f} events/run")
        print(f"  Time/event: {results['avg_time_per_event']:.2f}s")

        print(f"\n{'='*60}")
        print(f"Optimization Target: Reduce time by 40-60%")
        target_time = results['avg_time'] * 0.4  # 60% reduction
        print(f"Target extraction time: {target_time:.1f}s")
        print(f"{'='*60}\n")


async def main():
    parser = argparse.ArgumentParser(
        description='Benchmark provider extraction performance',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Benchmark Hajper with 3 runs
  python benchmark_provider.py hajper

  # Benchmark with 5 runs for basketball
  python benchmark_provider.py unibet --sport basketball --runs 5

  # Quick test (1 run, 10 events)
  python benchmark_provider.py betsson --quick
        """
    )

    parser.add_argument('provider_id', help='Provider ID to benchmark (e.g., unibet, hajper)')
    parser.add_argument('--sport', default='football', help='Sport to extract (default: football)')
    parser.add_argument('--runs', type=int, default=3, help='Number of runs (default: 3)')
    parser.add_argument('--limit', type=int, default=50, help='Event limit per run (default: 50)')
    parser.add_argument('--quick', action='store_true', help='Quick test: 1 run, 10 events')

    args = parser.parse_args()

    # Quick mode overrides
    if args.quick:
        args.runs = 1
        args.limit = 10
        print("\n[Quick Mode: 1 run, 10 events]")

    # Run benchmark
    benchmark = ProviderBenchmark(args.provider_id, args.sport)

    try:
        results = await benchmark.run_benchmark(args.runs, args.limit)
        benchmark.print_report(results)

    except KeyboardInterrupt:
        print("\n\nBenchmark cancelled by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n\nERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
