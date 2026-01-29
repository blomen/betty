#!/usr/bin/env python3
"""
Verify Extraction Completeness

Validates extraction results by checking:
- Events and odds per provider
- Multi-provider coverage (matched events)
- Total database counts
- Recent extraction activity

Usage:
    python scripts/verify_extraction.py
    python scripts/verify_extraction.py --minutes 30
"""

import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from sqlalchemy import func, distinct
from db.models import get_session, Event, Odds, Provider


def verify_extraction(minutes_ago: int = 60, verbose: bool = False) -> dict:
    """
    Verify extraction completeness.

    Args:
        minutes_ago: Check events updated within this time window
        verbose: Print detailed output

    Returns:
        Dictionary with verification results
    """
    session = get_session()
    # Use naive UTC datetime to match DB storage
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=minutes_ago)

    results = {
        "window_minutes": minutes_ago,
        "providers": {},
        "matched_events": 0,
        "total_events": 0,
        "total_odds": 0,
        "issues": []
    }

    # Events and odds per provider (recent)
    recent_stats = session.query(
        Odds.provider_id,
        func.count(distinct(Odds.event_id)).label('events'),
        func.count(Odds.id).label('odds')
    ).filter(Odds.updated_at >= cutoff)\
     .group_by(Odds.provider_id)\
     .order_by(func.count(Odds.id).desc())\
     .all()

    print(f"\n{'='*60}")
    print(f"Extraction Verification (last {minutes_ago} minutes)")
    print(f"{'='*60}")

    print(f"\n{'Provider':<20} {'Events':>8} {'Odds':>10}")
    print("-" * 40)

    for row in recent_stats:
        results["providers"][row.provider_id] = {
            "events": row.events,
            "odds": row.odds
        }
        print(f"{row.provider_id:<20} {row.events:>8} {row.odds:>10}")

        # Flag providers with very low counts
        if row.events < 10:
            results["issues"].append(f"{row.provider_id}: Only {row.events} events")

    # Multi-provider coverage
    matched = session.query(Event).join(Odds).group_by(Event.id).having(
        func.count(distinct(Odds.provider_id)) > 1
    ).count()
    results["matched_events"] = matched

    # Total counts
    total_events = session.query(Event).count()
    total_odds = session.query(Odds).count()
    results["total_events"] = total_events
    results["total_odds"] = total_odds

    # Provider coverage distribution
    if verbose:
        print(f"\n{'='*60}")
        print("Provider Coverage Distribution")
        print(f"{'='*60}")

        coverage_dist = session.query(
            func.count(distinct(Odds.provider_id)).label('provider_count'),
            func.count(Event.id).label('event_count')
        ).join(Odds).group_by(Event.id).subquery()

        dist_results = session.query(
            coverage_dist.c.provider_count,
            func.count().label('num_events')
        ).group_by(coverage_dist.c.provider_count)\
         .order_by(coverage_dist.c.provider_count)\
         .all()

        print(f"\n{'Providers':>10} {'Events':>10}")
        print("-" * 22)
        for row in dist_results:
            print(f"{row.provider_count:>10} {row.num_events:>10}")

    # Summary
    print(f"\n{'='*60}")
    print("Summary")
    print(f"{'='*60}")
    print(f"Matched events (2+ providers): {matched:,}")
    print(f"Total events in DB: {total_events:,}")
    print(f"Total odds in DB: {total_odds:,}")
    print(f"Providers with recent data: {len(recent_stats)}")

    # Issues
    if results["issues"]:
        print(f"\n{'='*60}")
        print("Potential Issues")
        print(f"{'='*60}")
        for issue in results["issues"]:
            print(f"  [!] {issue}")

    # Health status
    print(f"\n{'='*60}")
    if matched > 0 and len(recent_stats) > 5:
        print("[OK] Extraction appears healthy")
        results["healthy"] = True
    elif len(recent_stats) == 0:
        print("[ERROR] No recent extraction data found")
        results["healthy"] = False
    else:
        print("[WARNING] Limited extraction coverage")
        results["healthy"] = True

    session.close()
    return results


def main():
    parser = argparse.ArgumentParser(description="Verify extraction completeness")
    parser.add_argument(
        "--minutes", "-m",
        type=int,
        default=60,
        help="Check events from last N minutes (default: 60)"
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Show detailed coverage distribution"
    )

    args = parser.parse_args()
    results = verify_extraction(args.minutes, args.verbose)

    # Exit code based on health
    sys.exit(0 if results.get("healthy", False) else 1)


if __name__ == "__main__":
    main()
