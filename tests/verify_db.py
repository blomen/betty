"""Verify the database contents after pipeline run."""
from src.db.models import get_session, Event, Odds, Provider

session = get_session()

# Summary
print("=" * 50)
print("DATABASE VERIFICATION")
print("=" * 50)

print(f"\nEvents: {session.query(Event).count()}")
print(f"Odds entries: {session.query(Odds).count()}")
print(f"Providers: {session.query(Provider).count()}")

# Show matches (events with away team)
matches = session.query(Event).filter(Event.away_team != '').limit(5).all()
print(f"\n--- Sample Matches ---")
for e in matches:
    odds_count = session.query(Odds).filter(Odds.event_id == e.id).count()
    print(f"\n{e.home_team} vs {e.away_team}")
    print(f"  ID: {e.id}")
    print(f"  League: {e.league}")
    print(f"  Odds entries: {odds_count}")
    
    # Show some odds
    odds = session.query(Odds).filter(Odds.event_id == e.id).limit(3).all()
    for o in odds:
        print(f"    {o.market} → {o.outcome}: {o.odds}")

# Show outrights
outrights = session.query(Event).filter(Event.away_team == '').limit(3).all()
print(f"\n--- Sample Outrights ---")
for e in outrights:
    print(f"{e.home_team}")
    print(f"  League: {e.league}")

session.close()
print("\n✅ Verification complete")
