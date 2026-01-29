#!/usr/bin/env python3
"""
Provider Debug Script

For investigating specific extraction issues with detailed output.
Shows raw data, parsing steps, and identifies dropped events/markets.

Usage:
    python scripts/debug_provider.py unibet
    python scripts/debug_provider.py unibet --sport football
    python scripts/debug_provider.py unibet --sport football --verbose
    python scripts/debug_provider.py unibet --sport football --limit 5 --show-raw
"""

import asyncio
import sys
import json
import logging
import argparse
from datetime import datetime
from pathlib import Path
from typing import Optional

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.factory import ExtractorFactory
from src.core import StandardEvent


def setup_logging(verbose: bool):
    """Configure logging level based on verbosity."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S"
    )


def print_header(text: str):
    """Print a section header."""
    print(f"\n{'='*60}")
    print(f" {text}")
    print("=" * 60)


def print_event_details(event: StandardEvent, show_markets: bool = True):
    """Print detailed event information."""
    print(f"\n  Event ID: {event.id}")
    print(f"  Name: {event.name}")
    print(f"  Home: {event.home_team}")
    print(f"  Away: {event.away_team}")
    print(f"  Sport: {event.sport}")
    print(f"  League: {event.league}")
    print(f"  Start: {event.start_time}")
    print(f"  URL: {event.url}")
    print(f"  Markets: {len(event.markets)}")

    if show_markets and event.markets:
        print("  Market Details:")
        for i, market in enumerate(event.markets[:5]):  # Show first 5
            mtype = market.get("type", "unknown")
            outcomes = market.get("outcomes", [])
            print(f"    [{i+1}] {mtype}")
            for outcome in outcomes[:4]:  # Show first 4 outcomes
                name = outcome.get("name", "?")
                odds = outcome.get("odds", 0)
                point = outcome.get("point")
                point_str = f" ({point})" if point is not None else ""
                print(f"        - {name}: {odds}{point_str}")
            if len(outcomes) > 4:
                print(f"        ... and {len(outcomes) - 4} more outcomes")
        if len(event.markets) > 5:
            print(f"    ... and {len(event.markets) - 5} more markets")


def analyze_normalization(events: list):
    """Check normalization consistency."""
    print_header("NORMALIZATION ANALYSIS")

    issues = []

    for event in events[:20]:  # Sample first 20
        # Check team name normalization
        if event.home_team != event.home_team.lower():
            issues.append(f"Home not lowercase: '{event.home_team}'")
        if event.away_team != event.away_team.lower():
            issues.append(f"Away not lowercase: '{event.away_team}'")

        # Check for common normalization issues
        if "  " in event.home_team or "  " in event.away_team:
            issues.append(f"Double spaces: '{event.home_team}' vs '{event.away_team}'")

        # Check market outcome names
        for market in event.markets:
            for outcome in market.get("outcomes", []):
                name = outcome.get("name", "")
                if name and name != name.lower() and name.upper() != name:
                    # Mixed case that's not all caps
                    issues.append(f"Outcome not normalized: '{name}' in {market.get('type')}")
                    break

    if issues:
        print(f"Found {len(issues)} normalization issues:")
        for issue in issues[:10]:
            print(f"  - {issue}")
        if len(issues) > 10:
            print(f"  ... and {len(issues) - 10} more")
    else:
        print("No normalization issues found in sampled events")


def analyze_markets(events: list):
    """Analyze market type distribution and quality."""
    print_header("MARKET ANALYSIS")

    market_counts = {}
    outcome_issues = []
    point_issues = []

    for event in events:
        for market in event.markets:
            mtype = market.get("type", "unknown")
            market_counts[mtype] = market_counts.get(mtype, 0) + 1

            for outcome in market.get("outcomes", []):
                odds = outcome.get("odds", 0)
                if odds <= 1.0:
                    outcome_issues.append(f"Invalid odds {odds} in {mtype}")

                # Check point values for spreads/totals
                mtype_lower = mtype.lower()
                if "spread" in mtype_lower or "handicap" in mtype_lower or "total" in mtype_lower:
                    if outcome.get("point") is None:
                        point_issues.append(f"Missing point in {mtype}: {outcome.get('name')}")

    # Print market distribution
    print(f"\nMarket type distribution ({sum(market_counts.values())} total):")
    for mtype, count in sorted(market_counts.items(), key=lambda x: -x[1])[:15]:
        print(f"  {mtype}: {count}")
    if len(market_counts) > 15:
        print(f"  ... and {len(market_counts) - 15} more types")

    # Print issues
    if outcome_issues:
        print(f"\nOdds issues ({len(outcome_issues)}):")
        for issue in outcome_issues[:5]:
            print(f"  - {issue}")
        if len(outcome_issues) > 5:
            print(f"  ... and {len(outcome_issues) - 5} more")

    if point_issues:
        print(f"\nMissing point values ({len(point_issues)}):")
        for issue in point_issues[:5]:
            print(f"  - {issue}")
        if len(point_issues) > 5:
            print(f"  ... and {len(point_issues) - 5} more")


def check_duplicates(events: list):
    """Check for duplicate events."""
    print_header("DUPLICATE CHECK")

    ids = [e.id for e in events]
    unique_ids = set(ids)

    if len(ids) != len(unique_ids):
        duplicates = {}
        for id in ids:
            duplicates[id] = duplicates.get(id, 0) + 1

        print(f"Found {len(ids) - len(unique_ids)} duplicate IDs:")
        for id, count in duplicates.items():
            if count > 1:
                print(f"  - {id}: appears {count} times")
    else:
        print(f"No duplicates found ({len(ids)} unique events)")

    # Check for duplicate matchups (same teams, same time)
    matchups = {}
    for e in events:
        key = f"{e.home_team}|{e.away_team}|{e.start_time}"
        if key in matchups:
            print(f"  Potential duplicate matchup: {e.home_team} vs {e.away_team}")
        matchups[key] = e.id


async def debug_provider(
    provider_id: str,
    sport: str = "football",
    limit: int = 10,
    verbose: bool = False,
    show_raw: bool = False
):
    """Run debug analysis on a provider."""

    setup_logging(verbose)

    print_header(f"DEBUG: {provider_id} / {sport}")
    print(f"Timestamp: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Limit: {limit}")
    print(f"Verbose: {verbose}")

    # Get provider
    factory = ExtractorFactory.get_instance()

    try:
        provider = factory.get_extractor(provider_id)
    except ValueError as e:
        print(f"\nError: {e}")
        print(f"\nAvailable providers: {', '.join(factory.get_enabled_providers())}")
        return

    # Print provider config
    print_header("PROVIDER CONFIG")
    config = factory.get_provider(provider_id)
    if config:
        print(f"  ID: {config.id}")
        print(f"  Name: {config.name}")
        print(f"  Retriever: {config.retriever_type}")
        print(f"  Domain: {config.domain}")
        print(f"  API Base: {config.api_base}")
        if config.params:
            print(f"  Params: {json.dumps(config.params, indent=4)}")

    # Extract events
    print_header("EXTRACTION")
    print(f"Extracting {sport} events...")

    try:
        import time
        start = time.time()
        events = await provider.extract(sport, limit=limit * 10)  # Get more for analysis
        elapsed = time.time() - start

        print(f"Extracted {len(events)} events in {elapsed:.2f}s")

        if not events:
            print("\nNo events returned. Check:")
            print("  - Is the sport supported by this provider?")
            print("  - Are there currently events available?")
            print("  - Check logs with --verbose for API errors")
            return

        # Show sample events
        print_header(f"SAMPLE EVENTS (first {min(limit, len(events))})")
        for event in events[:limit]:
            print_event_details(event, show_markets=True)

        # Run analysis
        analyze_normalization(events)
        analyze_markets(events)
        check_duplicates(events)

        # Summary
        print_header("SUMMARY")
        print(f"Total events: {len(events)}")
        print(f"Total markets: {sum(len(e.markets) for e in events)}")
        print(f"Extraction time: {elapsed:.2f}s")

        events_with_time = sum(1 for e in events if e.start_time)
        print(f"Events with start_time: {events_with_time}/{len(events)}")

        events_with_league = sum(1 for e in events if e.league)
        print(f"Events with league: {events_with_league}/{len(events)}")

    except Exception as e:
        print(f"\nExtraction failed: {e}")
        if verbose:
            import traceback
            traceback.print_exc()

    finally:
        try:
            await provider.close()
        except:
            pass


def main():
    parser = argparse.ArgumentParser(
        description="Debug provider extraction issues",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python scripts/debug_provider.py unibet
  python scripts/debug_provider.py unibet --sport basketball
  python scripts/debug_provider.py unibet --sport football --verbose
  python scripts/debug_provider.py unibet --sport football --limit 5
        """
    )

    parser.add_argument("provider", help="Provider ID to debug")
    parser.add_argument("--sport", default="football", help="Sport to extract (default: football)")
    parser.add_argument("--limit", type=int, default=10, help="Number of events to show in detail")
    parser.add_argument("--verbose", "-v", action="store_true", help="Enable debug logging")
    parser.add_argument("--show-raw", action="store_true", help="Show raw API response (if available)")

    args = parser.parse_args()

    asyncio.run(debug_provider(
        args.provider,
        args.sport,
        args.limit,
        args.verbose,
        args.show_raw
    ))


if __name__ == "__main__":
    main()
