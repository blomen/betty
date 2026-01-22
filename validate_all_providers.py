"""Validate all provider extractions"""
from backend.src.db.models import init_db, get_session, Event, Odds, Provider
from sqlalchemy import func
from collections import defaultdict

init_db()
session = get_session()

print("="*80)
print("PROVIDER VALIDATION SUMMARY")
print("="*80)

# Get all providers
providers = session.query(Provider).all()

provider_data = []

for provider in providers:
    # Get odds count
    odds_count = session.query(Odds).filter(Odds.provider_id == provider.id).count()

    # Get event count (distinct events with odds from this provider)
    event_count = session.query(Event).join(Odds).filter(
        Odds.provider_id == provider.id
    ).distinct().count()

    # Get sport breakdown
    sports = session.query(
        Event.sport,
        func.count(Event.id).label('count')
    ).join(Odds).filter(
        Odds.provider_id == provider.id
    ).group_by(Event.sport).order_by(func.count(Event.id).desc()).all()

    # Get market types
    markets = session.query(
        Odds.market,
        func.count(Odds.id).label('count')
    ).filter(
        Odds.provider_id == provider.id
    ).group_by(Odds.market).order_by(func.count(Odds.id).desc()).limit(5).all()

    provider_data.append({
        'id': provider.id,
        'name': provider.name,
        'odds_count': odds_count,
        'event_count': event_count,
        'sports': sports,
        'markets': markets
    })

# Sort by odds count
provider_data.sort(key=lambda x: x['odds_count'], reverse=True)

# Summary table
print(f"\n{'Provider':<15} {'Odds':>10} {'Events':>10} {'Sports':>10}")
print("-" * 50)

for pd in provider_data:
    sport_count = len(pd['sports'])
    print(f"{pd['id']:<15} {pd['odds_count']:>10} {pd['event_count']:>10} {sport_count:>10}")

print("-" * 50)
total_odds = sum(pd['odds_count'] for pd in provider_data)
print(f"{'TOTAL':<15} {total_odds:>10}")

# Detailed breakdown for each provider with data
print("\n" + "="*80)
print("DETAILED PROVIDER BREAKDOWNS")
print("="*80)

for pd in provider_data:
    if pd['odds_count'] == 0:
        print(f"\n[{pd['id'].upper()}] - NO DATA")
        continue

    print(f"\n[{pd['id'].upper()}] - {pd['name']}")
    print("-" * 80)
    print(f"Total odds: {pd['odds_count']}")
    print(f"Total events: {pd['event_count']}")

    if pd['sports']:
        print(f"\nTop Sports:")
        for sport, count in pd['sports'][:5]:
            print(f"  {sport:<20} {count:>6} events")

    if pd['markets']:
        print(f"\nTop Markets:")
        for market, count in pd['markets']:
            print(f"  {market:<30} {count:>6} odds")

# Check for matching between Polymarket and other providers
print("\n" + "="*80)
print("POLYMARKET MATCHING ANALYSIS")
print("="*80)

poly_events = session.query(Event.id).join(Odds).filter(
    Odds.provider_id == 'polymarket'
).distinct().all()
poly_event_ids = {e[0] for e in poly_events}

print(f"\nPolymarket has odds for: {len(poly_event_ids)} events")
print("\nMatching rate with other providers:")

for pd in provider_data:
    if pd['id'] == 'polymarket' or pd['odds_count'] == 0:
        continue

    # Get events from this provider
    provider_events = session.query(Event.id).join(Odds).filter(
        Odds.provider_id == pd['id']
    ).distinct().all()
    provider_event_ids = {e[0] for e in provider_events}

    # Calculate overlap
    overlap = poly_event_ids & provider_event_ids
    if len(provider_event_ids) > 0:
        match_rate = (len(overlap) / len(provider_event_ids)) * 100
    else:
        match_rate = 0

    print(f"  {pd['id']:<15} {len(overlap):>6}/{len(provider_event_ids):<6} ({match_rate:>5.1f}% of their events match Polymarket)")

session.close()
