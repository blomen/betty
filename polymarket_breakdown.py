"""Show Polymarket-specific data breakdown by sport"""
from backend.src.db.models import init_db, get_session, Event, Odds
from sqlalchemy import func

init_db()
session = get_session()

print("="*70)
print("POLYMARKET DATA BREAKDOWN BY SPORT")
print("="*70)

# Get events that have Polymarket odds
polymarket_events = session.query(Event).join(Odds).filter(
    Odds.provider_id == 'polymarket'
).distinct().all()

# Group by sport manually
sport_counts = {}
for event in polymarket_events:
    sport_counts[event.sport] = sport_counts.get(event.sport, 0) + 1

# Sort by count
sorted_sports = sorted(sport_counts.items(), key=lambda x: x[1], reverse=True)

print(f"\n{'Sport':<20} {'Event Count':>12}")
print("-" * 35)

for sport, count in sorted_sports:
    print(f"{sport:<20} {count:>12}")

total = sum(count for _, count in sorted_sports)
print("-" * 35)
print(f"{'TOTAL':<20} {total:>12}")

# Show total Polymarket odds
poly_odds_count = session.query(Odds).filter(Odds.provider_id == 'polymarket').count()
print(f"\nTotal Polymarket odds entries: {poly_odds_count}")

# Show sample Polymarket event
print("\n" + "="*70)
print("SAMPLE POLYMARKET EVENT")
print("="*70)

sample = session.query(Event).join(Odds).filter(
    Odds.provider_id == 'polymarket'
).first()

if sample:
    print(f"\nEvent: {sample.home_team} vs {sample.away_team}")
    print(f"Sport: {sample.sport}")
    print(f"League: {sample.league}")
    print(f"Canonical ID: {sample.id}")
    print(f"Start time: {sample.start_time}")

    poly_odds = session.query(Odds).filter(
        Odds.event_id == sample.id,
        Odds.provider_id == 'polymarket'
    ).all()

    print(f"\nPolymarket odds for this event:")
    for odd in poly_odds:
        print(f"  {odd.market} - {odd.outcome}: {odd.odds}")

session.close()
