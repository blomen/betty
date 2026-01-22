#!/usr/bin/env python
"""
Check database statistics for events and markets by sport.
"""

import sqlite3
from pathlib import Path

db_path = Path("backend/data/oddopp.db")

if not db_path.exists():
    print(f"Database not found at {db_path}")
    exit(1)

conn = sqlite3.connect(db_path)
cursor = conn.cursor()

print("=" * 80)
print("DATABASE STATISTICS - EVENTS AND MARKETS BY SPORT")
print("=" * 80)

# Get events by sport
print("\n[EVENTS BY SPORT]")
cursor.execute("""
    SELECT sport, COUNT(*) as event_count
    FROM events
    GROUP BY sport
    ORDER BY event_count DESC
""")
events_by_sport = cursor.fetchall()

for sport, count in events_by_sport:
    print(f"  {sport}: {count} events")

# Get total events
cursor.execute("SELECT COUNT(*) FROM events")
total_events = cursor.fetchone()[0]
print(f"\nTotal Events: {total_events}")

# Get odds entries by provider and sport
print("\n[ODDS ENTRIES BY PROVIDER AND SPORT]")
cursor.execute("""
    SELECT o.provider_id, e.sport, COUNT(*) as odds_count
    FROM odds o
    JOIN events e ON o.event_id = e.id
    GROUP BY o.provider_id, e.sport
    ORDER BY o.provider_id, odds_count DESC
""")
odds_by_provider_sport = cursor.fetchall()

current_provider = None
for provider, sport, count in odds_by_provider_sport:
    if provider != current_provider:
        print(f"\n{provider}:")
        current_provider = provider
    print(f"  {sport}: {count} odds")

# Get total odds entries
cursor.execute("SELECT COUNT(*) FROM odds")
total_odds = cursor.fetchone()[0]
print(f"\nTotal Odds Entries: {total_odds}")

# Get market types distribution
print("\n[MARKET TYPES DISTRIBUTION]")
cursor.execute("""
    SELECT market, COUNT(*) as count
    FROM odds
    GROUP BY market
    ORDER BY count DESC
""")
market_types = cursor.fetchall()

for market_type, count in market_types:
    print(f"  {market_type}: {count} odds")

# Get recent events sample
print("\n[RECENT EVENTS SAMPLE (last 5)]")
cursor.execute("""
    SELECT id, sport, home_team, away_team, start_time
    FROM events
    ORDER BY created_at DESC
    LIMIT 5
""")
recent_events = cursor.fetchall()

for event_id, sport, home, away, start in recent_events:
    print(f"\n  {event_id}")
    print(f"    {home} vs {away}")
    print(f"    Sport: {sport}")
    print(f"    Start: {start}")

    # Get odds for this event
    cursor.execute("""
        SELECT provider_id, market, outcome, odds
        FROM odds
        WHERE event_id = ?
        ORDER BY provider_id, market, outcome
    """, (event_id,))
    odds = cursor.fetchall()

    if odds:
        print(f"    Odds ({len(odds)} entries):")
        for provider, market, outcome, price in odds[:6]:  # Show first 6
            print(f"      {provider} - {market} - {outcome}: {price}")
        if len(odds) > 6:
            print(f"      ... and {len(odds) - 6} more")

conn.close()

print("\n" + "=" * 80)
