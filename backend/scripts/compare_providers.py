#!/usr/bin/env python3
"""
Compare provider performance and coverage.

Supports two modes:
1. Live comparison - Extract from multiple providers simultaneously and compare
2. Historical comparison - Compare metrics from database records

Usage:
    python scripts/compare_providers.py unibet betsson pinnacle
    python scripts/compare_providers.py unibet betsson --sport football
    python scripts/compare_providers.py --historical --runs 5
"""

import sys
import asyncio
import argparse
import time
from pathlib import Path
from datetime import datetime
from typing import List, Optional
from collections import defaultdict

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.factory import ExtractorFactory
from src.db.models import get_session, ExtractionRun, ProviderRunMetrics
from sqlalchemy import desc


ALL_SPORTS = [
    "football", "basketball", "ice_hockey", "american_football",
    "baseball", "tennis", "mma", "esports"
]


async def extract_for_provider(provider, sport: str, timeout: float = 60.0) -> dict:
    """Extract events from a provider for comparison."""
    start = time.time()
    result = {
        "events": 0,
        "markets": 0,
        "time": 0,
        "status": "OK",
        "market_types": set()
    }

    try:
        events = await asyncio.wait_for(
            provider.extract(sport, limit=500),
            timeout=timeout
        )
        result["time"] = time.time() - start
        result["events"] = len(events)
        result["markets"] = sum(len(e.markets) for e in events)

        # Collect market types
        for e in events:
            for m in e.markets:
                result["market_types"].add(m.get("type", "unknown"))

    except asyncio.TimeoutError:
        result["status"] = "TIMEOUT"
        result["time"] = timeout
    except Exception as e:
        result["status"] = f"ERROR: {str(e)[:30]}"
        result["time"] = time.time() - start

    return result


async def live_comparison(provider_ids: List[str], sports: List[str]):
    """Run live extraction comparison across providers."""
    factory = ExtractorFactory.get_instance()

    # Validate and get providers
    providers = {}
    for pid in provider_ids:
        try:
            providers[pid] = factory.get_extractor(pid)
        except ValueError as e:
            print(f"Warning: {e}")

    if len(providers) < 2:
        print("Need at least 2 valid providers to compare")
        print(f"Available: {', '.join(factory.get_enabled_providers())}")
        return

    print(f"\n{'='*80}")
    print(f"LIVE PROVIDER COMPARISON")
    print(f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Providers: {', '.join(providers.keys())}")
    print(f"Sports: {', '.join(sports)}")
    print(f"{'='*80}\n")

    # Results storage
    results = defaultdict(lambda: defaultdict(dict))

    # Extract for each sport
    for sport in sports:
        print(f"Testing {sport}...", flush=True)

        # Run all providers in parallel for this sport
        tasks = {
            pid: extract_for_provider(provider, sport)
            for pid, provider in providers.items()
        }

        # Wait for all
        provider_results = await asyncio.gather(*tasks.values())

        for pid, result in zip(tasks.keys(), provider_results):
            results[sport][pid] = result

        # Print quick status
        for pid in providers:
            r = results[sport][pid]
            status = f"{r['events']} events" if r["status"] == "OK" else r["status"]
            print(f"  {pid}: {status}")

    # Print comparison table - Events
    print(f"\n{'='*80}")
    print("EVENT COUNT COMPARISON")
    print(f"{'='*80}")

    # Header
    header = f"{'Sport':<18}"
    for pid in providers:
        header += f" | {pid[:12]:>12}"
    print(header)
    print("-" * len(header))

    # Data rows
    totals = {pid: 0 for pid in providers}
    for sport in sports:
        row = f"{sport:<18}"
        for pid in providers:
            r = results[sport][pid]
            val = r["events"] if r["status"] == "OK" else "-"
            row += f" | {val:>12}"
            if r["status"] == "OK":
                totals[pid] += r["events"]
        print(row)

    # Totals
    print("-" * len(header))
    row = f"{'TOTAL':<18}"
    for pid in providers:
        row += f" | {totals[pid]:>12}"
    print(row)

    # Print comparison table - Timing
    print(f"\n{'='*80}")
    print("EXTRACTION TIME (seconds)")
    print(f"{'='*80}")

    print(header)
    print("-" * len(header))

    time_totals = {pid: 0 for pid in providers}
    for sport in sports:
        row = f"{sport:<18}"
        for pid in providers:
            r = results[sport][pid]
            row += f" | {r['time']:>12.1f}"
            time_totals[pid] += r["time"]
        print(row)

    print("-" * len(header))
    row = f"{'TOTAL':<18}"
    for pid in providers:
        row += f" | {time_totals[pid]:>12.1f}"
    print(row)

    # Print market type coverage
    print(f"\n{'='*80}")
    print("MARKET TYPE COVERAGE")
    print(f"{'='*80}")

    all_market_types = set()
    provider_market_types = {pid: set() for pid in providers}

    for sport in sports:
        for pid in providers:
            r = results[sport][pid]
            provider_market_types[pid].update(r.get("market_types", set()))
            all_market_types.update(r.get("market_types", set()))

    # Priority markets
    priority = ["1x2", "moneyline", "over_under", "spread"]

    print(f"\n{'Market Type':<20}", end="")
    for pid in providers:
        print(f" | {pid[:12]:^12}", end="")
    print()
    print("-" * (20 + 15 * len(providers)))

    # Show priority markets first
    for mtype in priority:
        if mtype in all_market_types:
            row = f"{mtype:<20}"
            for pid in providers:
                has = "X" if mtype in provider_market_types[pid] else "-"
                row += f" | {has:^12}"
            print(row)

    # Show other common markets
    other_types = sorted(all_market_types - set(priority))
    for mtype in other_types[:10]:
        row = f"{mtype[:20]:<20}"
        for pid in providers:
            has = "X" if mtype in provider_market_types[pid] else "-"
            row += f" | {has:^12}"
        print(row)

    if len(other_types) > 10:
        print(f"  ... and {len(other_types) - 10} more market types")

    # Cleanup
    for provider in providers.values():
        try:
            await provider.close()
        except:
            pass

    print(f"\n{'='*80}\n")


def historical_comparison(limit: int = 5, provider_ids: Optional[List[str]] = None):
    """Compare provider performance from historical database records."""
    session = get_session()

    # Get recent runs
    runs = session.query(ExtractionRun).order_by(
        desc(ExtractionRun.start_time)
    ).limit(limit).all()

    if not runs:
        print("No extraction runs found in database")
        return

    print(f"\n{'='*80}")
    print(f"HISTORICAL PROVIDER COMPARISON - Last {len(runs)} Runs")
    print(f"{'='*80}\n")

    # Aggregate metrics per provider
    provider_stats = {}

    for run in runs:
        for pm in run.provider_metrics:
            pid = pm.provider_id
            if provider_ids and pid not in provider_ids:
                continue

            if pid not in provider_stats:
                provider_stats[pid] = {
                    'runs': 0,
                    'total_events': 0,
                    'total_odds': 0,
                    'success_count': 0,
                    'total_duration': 0,
                    'sports_attempted': 0,
                    'sports_succeeded': 0
                }

            stats = provider_stats[pid]
            stats['runs'] += 1
            stats['total_events'] += pm.events_processed
            stats['total_odds'] += pm.odds_processed
            stats['success_count'] += 1 if pm.status == 'success' else 0
            stats['total_duration'] += pm.duration_seconds or 0
            stats['sports_attempted'] += pm.sports_attempted
            stats['sports_succeeded'] += pm.sports_succeeded

    if not provider_stats:
        print("No matching provider data found")
        return

    # Print comparison table
    print(f"{'Provider':<20} {'Runs':>5} {'Avg Events':>12} {'Avg Odds':>12} {'Success%':>9} {'Avg Time':>10}")
    print("-" * 80)

    for pid in sorted(provider_stats.keys()):
        stats = provider_stats[pid]
        run_count = stats['runs']
        avg_events = stats['total_events'] / run_count
        avg_odds = stats['total_odds'] / run_count
        success_rate = (stats['success_count'] / run_count) * 100
        avg_time = stats['total_duration'] / run_count

        print(f"{pid:<20} {run_count:>5} {avg_events:>12.0f} {avg_odds:>12.0f} {success_rate:>8.1f}% {avg_time:>9.1f}s")

    print("\n" + "=" * 80)

    session.close()


def main():
    parser = argparse.ArgumentParser(
        description="Compare provider performance",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Live comparison
  python scripts/compare_providers.py unibet betsson pinnacle
  python scripts/compare_providers.py unibet betsson --sport football,basketball

  # Historical comparison
  python scripts/compare_providers.py --historical --runs 5
  python scripts/compare_providers.py --historical --providers unibet,betsson
        """
    )

    parser.add_argument("providers", nargs="*", help="Provider IDs to compare (live mode)")
    parser.add_argument("--sport", "--sports", help="Comma-separated sports to test")
    parser.add_argument("--historical", action="store_true", help="Use historical data from database")
    parser.add_argument("--runs", type=int, default=5, help="Number of historical runs to compare")
    parser.add_argument("--list", action="store_true", help="List available providers")

    args = parser.parse_args()

    if args.list:
        factory = ExtractorFactory.get_instance()
        providers = factory.get_enabled_providers()
        print(f"\nEnabled providers ({len(providers)}):")
        for p in sorted(providers):
            print(f"  - {p}")
        return

    if args.historical:
        provider_ids = args.providers if args.providers else None
        historical_comparison(args.runs, provider_ids)
    else:
        if len(args.providers) < 2:
            print("Live comparison requires at least 2 providers")
            print("Usage: python scripts/compare_providers.py provider1 provider2 ...")
            print("Or use --historical for database comparison")
            return

        sports = args.sport.split(",") if args.sport else ALL_SPORTS[:4]
        asyncio.run(live_comparison(args.providers, sports))


if __name__ == "__main__":
    main()
