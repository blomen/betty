#!/usr/bin/env python3
"""
Provider Production Monitor

Monitors provider performance and success rates over time.
Part of the Provider Optimization Workflow.
"""

import argparse
import sys
from pathlib import Path
from datetime import datetime, timedelta
import sqlite3

# Add backend to path
backend_path = Path(__file__).parent.parent
sys.path.insert(0, str(backend_path))


def analyze_provider_logs(provider_id: str, days: int = 7):
    """Analyze provider logs from database."""

    db_path = backend_path / "data" / "firev.db"

    if not db_path.exists():
        print(f"Database not found: {db_path}")
        print("Run an extraction first to create the database")
        return

    print(f"\n{'='*60}")
    print(f"Provider Monitor: {provider_id}")
    print(f"{'='*60}")
    print(f"Period: Last {days} days")
    print(f"{'='*60}\n")

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # Calculate date threshold
    cutoff_date = datetime.now() - timedelta(days=days)
    cutoff_str = cutoff_date.strftime('%Y-%m-%d')

    # Query odds records (proxy for successful extractions)
    cursor.execute("""
        SELECT
            DATE(o.updated_at) as date,
            COUNT(DISTINCT o.event_id) as events,
            COUNT(*) as odds_records
        FROM odds o
        WHERE o.provider_id = ?
        AND o.updated_at >= ?
        GROUP BY DATE(o.updated_at)
        ORDER BY date DESC
    """, (provider_id, cutoff_str))

    results = cursor.fetchall()

    if not results:
        print(f"No data found for {provider_id} in last {days} days")
        print("\nPossible reasons:")
        print("  - Provider not run recently")
        print("  - Provider configured but not active")
        print("  - Extraction failures")
        conn.close()
        return

    # Print daily breakdown
    print(f"{'Date':<12} {'Events':>8} {'Odds':>10}")
    print("-" * 35)

    total_events = 0
    total_odds = 0

    for date, events, odds in results:
        print(f"{date:<12} {events:>8} {odds:>10}")
        total_events += events
        total_odds += odds

    print("-" * 35)
    print(f"{'TOTAL':<12} {total_events:>8} {total_odds:>10}")

    # Calculate averages
    days_with_data = len(results)
    avg_events = total_events / days_with_data if days_with_data else 0
    avg_odds = total_odds / days_with_data if days_with_data else 0

    print(f"\nAverages ({days_with_data} days with data):")
    print(f"  Events/day: {avg_events:.0f}")
    print(f"  Odds/day:   {avg_odds:.0f}")

    # Check for issues
    print(f"\n{'='*60}")
    print("Health Check")
    print(f"{'='*60}\n")

    issues_found = False

    # Check for gaps
    if days_with_data < days * 0.7:  # Less than 70% coverage
        print("WARNING: Data gaps detected")
        print(f"  Expected: {days} days")
        print(f"  Found: {days_with_data} days")
        print("  Action: Check extraction schedule")
        issues_found = True

    # Check for declining events
    if len(results) >= 3:
        recent_avg = sum(r[1] for r in results[:3]) / 3
        older_avg = sum(r[1] for r in results[-3:]) / 3

        if recent_avg < older_avg * 0.8:  # 20% decline
            print("WARNING: Event count declining")
            print(f"  Recent average: {recent_avg:.0f} events/day")
            print(f"  Previous average: {older_avg:.0f} events/day")
            print("  Action: Investigate provider changes")
            issues_found = True

    # Check for zero-event days
    zero_days = [r for r in results if r[1] == 0]
    if zero_days:
        print(f"WARNING: {len(zero_days)} days with 0 events")
        print("  Action: Check extraction logs for errors")
        issues_found = True

    if not issues_found:
        print("Status: HEALTHY")
        print("  No issues detected")
        print("  Provider performing as expected")

    print(f"\n{'='*60}\n")

    conn.close()


def main():
    parser = argparse.ArgumentParser(
        description='Monitor provider performance over time',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Monitor Hajper for last 7 days
  python monitor_provider.py hajper

  # Monitor Unibet for last 30 days
  python monitor_provider.py unibet --days 30

Note: Requires database with historical extraction data.
        """
    )

    parser.add_argument('provider_id', help='Provider ID to monitor')
    parser.add_argument('--days', type=int, default=7, help='Days to analyze (default: 7)')

    args = parser.parse_args()

    try:
        analyze_provider_logs(args.provider_id, args.days)
    except Exception as e:
        print(f"\nERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
