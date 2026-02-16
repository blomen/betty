#!/usr/bin/env python3
"""
Provider Integration Validation Script

Validates cross-provider data quality after pipeline integration.
Run after extracting a new provider alongside sharp sources.

Usage:
    # After extraction
    python -m src.app extract pinnacle polymarket new_provider

    # Run validation
    python scripts/validate_integration.py
    python scripts/validate_integration.py --provider new_provider
    python scripts/validate_integration.py --detailed

Validation Checks:
1. Odds/event ratio per provider (expected: 2.4-3.0)
2. Outcome normalization rate (expected: >97%)
3. Score-like outcomes (expected: 0)
4. Cross-provider match rate (higher = better)
5. Sample matched events verification
"""

import sys
import argparse
import sqlite3
from pathlib import Path
from typing import Optional

# Database path
DB_PATH = Path(__file__).parent.parent / "data" / "degentraderxd.db"


def get_connection():
    """Get database connection."""
    if not DB_PATH.exists():
        print(f"Error: Database not found at {DB_PATH}")
        print("Run extraction first: python -m src.app extract pinnacle <provider>")
        sys.exit(1)

    return sqlite3.connect(DB_PATH)


def print_header(text: str, char: str = "="):
    """Print section header."""
    print(f"\n{char * 60}")
    print(f" {text}")
    print(char * 60)


def check_odds_event_ratio(conn, provider_filter: Optional[str] = None):
    """
    Check odds/event ratio per provider.

    Expected: 2.4-3.0 for 1x2/moneyline markets
    Red flags:
        - >4.0: Non-1x2 markets leaking through
        - <2.0: Missing outcomes
    """
    print_header("1. ODDS/EVENT RATIO")

    query = """
        SELECT
            p.name,
            COUNT(o.id) as odds,
            COUNT(DISTINCT o.event_id) as events,
            ROUND(CAST(COUNT(o.id) AS FLOAT) / NULLIF(COUNT(DISTINCT o.event_id), 0), 2) as ratio
        FROM odds o
        JOIN providers p ON o.provider_id = p.id
    """
    params = []
    if provider_filter:
        query += " WHERE p.id = ?"
        params.append(provider_filter)
    query += " GROUP BY p.name ORDER BY p.name"

    cursor = conn.cursor()
    cursor.execute(query, params)
    rows = cursor.fetchall()

    print(f"\n  {'Provider':<16} | {'Odds':<6} | {'Events':<6} | {'Ratio':<6} | Status")
    print("  " + "-" * 55)

    issues = []
    for row in rows:
        name, odds, events, ratio = row
        ratio = ratio or 0

        if ratio > 4.0:
            status = "HIGH - non-1x2 leaking?"
            issues.append(f"{name}: ratio {ratio} (>4.0)")
        elif ratio < 2.0 and events > 0:
            status = "LOW - missing outcomes?"
            issues.append(f"{name}: ratio {ratio} (<2.0)")
        else:
            status = "OK"

        print(f"  {name:<16} | {odds:<6} | {events:<6} | {ratio:<6} | {status}")

    return issues


def check_outcome_normalization(conn, provider_filter: Optional[str] = None):
    """
    Check outcome normalization rate.

    Expected: >97% outcomes should be home/away/draw
    Low rate indicates team name matching failing.
    """
    print_header("2. OUTCOME NORMALIZATION")

    query = """
        SELECT
            p.name,
            COUNT(*) as total,
            SUM(CASE WHEN o.outcome IN ('home', 'away', 'draw') THEN 1 ELSE 0 END) as normalized,
            ROUND(100.0 * SUM(CASE WHEN o.outcome IN ('home', 'away', 'draw') THEN 1 ELSE 0 END) / COUNT(*), 1) as pct
        FROM odds o
        JOIN providers p ON o.provider_id = p.id
    """
    params = []
    if provider_filter:
        query += " WHERE p.id = ?"
        params.append(provider_filter)
    query += " GROUP BY p.name ORDER BY p.name"

    cursor = conn.cursor()
    cursor.execute(query, params)
    rows = cursor.fetchall()

    print(f"\n  {'Provider':<16} | {'Total':<6} | {'Normalized':<10} | {'%':<6} | Status")
    print("  " + "-" * 60)

    issues = []
    for row in rows:
        name, total, normalized, pct = row
        pct = pct or 0

        if pct < 95.0:
            status = "LOW - check normalizer"
            issues.append(f"{name}: {pct}% (<95%)")
        elif pct < 97.0:
            status = "WARN"
        else:
            status = "OK"

        print(f"  {name:<16} | {total:<6} | {normalized:<10} | {pct:<6} | {status}")

    return issues


def check_score_like_outcomes(conn, provider_filter: Optional[str] = None):
    """
    Check for score-like outcomes (e.g., "1-0", "2-1").

    Expected: 0 for all providers
    Non-zero indicates correct score markets leaking through.
    """
    print_header("3. SCORE-LIKE OUTCOMES")

    query = """
        SELECT
            p.name,
            COUNT(*) as count
        FROM odds o
        JOIN providers p ON o.provider_id = p.id
        WHERE o.outcome LIKE '%-%'
          AND o.outcome NOT IN ('home', 'away', 'draw')
    """
    params = []
    if provider_filter:
        query += " AND p.id = ?"
        params.append(provider_filter)
    query += " GROUP BY p.name"

    cursor = conn.cursor()
    cursor.execute(query, params)
    rows = cursor.fetchall()

    issues = []
    if rows:
        print(f"\n  {'Provider':<16} | {'Count':<6} | Status")
        print("  " + "-" * 35)
        for row in rows:
            name, count = row
            issues.append(f"{name}: {count} score-like outcomes")
            print(f"  {name:<16} | {count:<6} | FAIL - filter market type")
    else:
        print("\n  No score-like outcomes found - OK")

    return issues


def check_cross_provider_matching(conn):
    """
    Check cross-provider event matching rate.

    Higher match rate = better data quality for arbitrage/value detection.
    """
    print_header("4. CROSS-PROVIDER MATCHING")

    cursor = conn.cursor()

    # Total events vs matched events
    cursor.execute("""
        SELECT
            COUNT(DISTINCT event_id) as total,
            SUM(CASE WHEN cnt > 1 THEN 1 ELSE 0 END) as matched
        FROM (
            SELECT event_id, COUNT(DISTINCT provider_id) as cnt
            FROM odds GROUP BY event_id
        )
    """)
    row = cursor.fetchone()
    total, matched = row
    match_pct = (matched / total * 100) if total > 0 else 0

    print(f"\n  Total events: {total}")
    print(f"  Matched across providers: {matched} ({match_pct:.1f}%)")

    # Events by provider count
    cursor.execute("""
        SELECT cnt as provider_count, COUNT(*) as event_count
        FROM (
            SELECT event_id, COUNT(DISTINCT provider_id) as cnt
            FROM odds GROUP BY event_id
        )
        GROUP BY cnt ORDER BY cnt
    """)
    rows = cursor.fetchall()

    print(f"\n  {'Providers':<12} | {'Events'}")
    print("  " + "-" * 25)
    for row in rows:
        count, events = row
        print(f"  {count:<12} | {events}")

    return []


def show_matched_sample(conn, limit: int = 10):
    """Show sample of matched events for verification."""
    print_header("5. MATCHED EVENTS SAMPLE")

    cursor = conn.cursor()
    cursor.execute(f"""
        SELECT
            e.id,
            e.home_team,
            e.away_team,
            e.sport,
            p.name as provider,
            o.outcome,
            o.odds
        FROM events e
        JOIN odds o ON e.id = o.event_id
        JOIN providers p ON o.provider_id = p.id
        WHERE e.id IN (
            SELECT event_id
            FROM odds
            GROUP BY event_id
            HAVING COUNT(DISTINCT provider_id) > 1
        )
        ORDER BY e.id, p.name, o.outcome
        LIMIT {limit * 6}
    """)
    rows = cursor.fetchall()

    if not rows:
        print("\n  No matched events found")
        return

    # Group by event
    events = {}
    for row in rows:
        event_id, home, away, sport, provider, outcome, odds = row
        if event_id not in events:
            events[event_id] = {
                'home': home,
                'away': away,
                'sport': sport,
                'odds': {}
            }
        if provider not in events[event_id]['odds']:
            events[event_id]['odds'][provider] = {}
        events[event_id]['odds'][provider][outcome] = odds

    # Print sample
    count = 0
    for event_id, data in events.items():
        if count >= limit:
            break

        print(f"\n  {data['home']} vs {data['away']} ({data['sport']})")
        for provider, outcomes in data['odds'].items():
            odds_str = ", ".join(f"{o}: {v}" for o, v in sorted(outcomes.items()))
            print(f"    {provider}: {odds_str}")
        count += 1


def show_unnormalized_outcomes(conn, limit: int = 20):
    """Show outcomes that failed normalization."""
    print_header("6. UNNORMALIZED OUTCOMES SAMPLE")

    cursor = conn.cursor()
    cursor.execute(f"""
        SELECT
            p.name,
            o.outcome,
            COUNT(*) as count
        FROM odds o
        JOIN providers p ON o.provider_id = p.id
        WHERE o.outcome NOT IN ('home', 'away', 'draw')
        GROUP BY p.name, o.outcome
        ORDER BY count DESC
        LIMIT {limit}
    """)
    rows = cursor.fetchall()

    if not rows:
        print("\n  All outcomes normalized - OK")
        return

    print(f"\n  {'Provider':<16} | {'Outcome':<30} | Count")
    print("  " + "-" * 55)
    for row in rows:
        provider, outcome, count = row
        print(f"  {provider:<16} | {outcome:<30} | {count}")


def main():
    parser = argparse.ArgumentParser(
        description="Validate provider integration data quality",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    # Full validation
    python scripts/validate_integration.py

    # Filter to specific provider
    python scripts/validate_integration.py --provider unibet

    # Show detailed samples
    python scripts/validate_integration.py --detailed

Expected Benchmarks:
    Odds/event ratio: 2.4-3.0
    Outcome normalization: >97%
    Score-like outcomes: 0
    Cross-provider match: >50% (depends on provider coverage)
        """
    )

    parser.add_argument("--provider", "-p", help="Filter to specific provider ID")
    parser.add_argument("--detailed", "-d", action="store_true", help="Show detailed samples")

    args = parser.parse_args()

    conn = get_connection()

    all_issues = []

    # Run checks
    all_issues.extend(check_odds_event_ratio(conn, args.provider))
    all_issues.extend(check_outcome_normalization(conn, args.provider))
    all_issues.extend(check_score_like_outcomes(conn, args.provider))
    check_cross_provider_matching(conn)

    # Detailed samples
    if args.detailed:
        show_matched_sample(conn)
        show_unnormalized_outcomes(conn)

    # Summary
    print_header("VALIDATION SUMMARY")
    if all_issues:
        print(f"\n  FAILED - Found {len(all_issues)} issues:")
        for issue in all_issues:
            print(f"    - {issue}")
        sys.exit(1)
    else:
        print("\n  PASSED - All checks OK")
        sys.exit(0)


if __name__ == "__main__":
    main()
