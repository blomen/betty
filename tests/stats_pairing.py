import sys
import os
from pathlib import Path
from sqlalchemy import func

# Add backend to path
sys.path.append(os.path.join(str(Path(__file__).parent.parent), "backend"))

from src.db.models import get_session, Event, Odds

def calculate_stats():
    session = get_session()
    print("="*50)
    print("PAIRING STATISTICS")
    print("="*50)

    # 1. Total Matched Events (Odds from > 1 provider)
    matched_query = session.query(Event.id).join(Odds).group_by(Event.id).having(
        func.count(func.distinct(Odds.provider_id)) > 1
    )
    total_matched_count = matched_query.count()
    matched_ids = [r[0] for r in matched_query.all()]
    
    # 2. Polymarket Matches
    # Of the matched events, how many include 'polymarket'?
    
    # This query filters the ALREADY MATCHED events to see if they contain polymarket odds
    poly_matched_count = 0
    
    if matched_ids:
        # Check efficiently
        # Get count of events in matched_ids that have provider_id='polymarket'
        poly_matched_count = session.query(Odds.event_id).filter(
            Odds.event_id.in_(matched_ids),
            Odds.provider_id == 'polymarket'
        ).distinct().count()

    print(f"\nTotal Events in DB: {session.query(Event).count()}")
    print(f"Total Matched Events (Multi-Provider): {total_matched_count}")
    print(f"Events Paired with Polymarket: {poly_matched_count}")
    
    if total_matched_count > 0:
        poly_pct = (poly_matched_count / total_matched_count) * 100
        print(f"Polymarket Coverage in Matches: {poly_pct:.1f}%")

    session.close()

if __name__ == "__main__":
    calculate_stats()
