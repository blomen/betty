"""Check database contents"""
from backend.src.db.models import init_db, get_session, Event, Odds
from sqlalchemy import func

init_db()
session = get_session()

print("="*60)
print("DATABASE SPORT BREAKDOWN")
print("="*60)

# Get events grouped by sport
sport_counts = session.query(
    Event.sport,
    func.count(Event.id).label('count')
).group_by(Event.sport).order_by(func.count(Event.id).desc()).all()

if sport_counts:
    print(f"\n{'Sport':<20} {'Event Count':>12}")
    print("-" * 35)
    for sport, count in sport_counts:
        print(f"{sport:<20} {count:>12}")

    total = sum(count for _, count in sport_counts)
    print("-" * 35)
    print(f"{'TOTAL':<20} {total:>12}")
else:
    print("\nNo events found in database")

print("\n" + "="*60)
print("TOTAL COUNTS")
print("="*60)
print(f"Total events: {session.query(Event).count()}")
print(f"Total odds: {session.query(Odds).count()}")

session.close()
