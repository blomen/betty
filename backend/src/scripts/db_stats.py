import logging
from sqlalchemy import func
from backend.src.db.models import init_db, get_session, Event, Odds, Provider

logging.basicConfig(level=logging.ERROR)

def run_stats():
    session = get_session()
    
    print("\n=== Database Statistics ===")
    
    # Total Events
    total_events = session.query(Event).count()
    print(f"Total Events: {total_events}")
    
    # Matched Events (Multiple Providers)
    matched_count = session.query(Event).join(Odds).group_by(Event.id).having(
        func.count(func.distinct(Odds.provider_id)) > 1
    ).count()
    print(f"Matched Events (Cross-Provider): {matched_count}")
    
    print("\n--- Events per Sport ---")
    sport_counts = session.query(Event.sport, func.count(Event.id)).group_by(Event.sport).all()
    for sport, count in sorted(sport_counts, key=lambda x: x[1], reverse=True):
        print(f"{sport}: {count}")
        
    print("\n--- Odds per Provider ---")
    provider_counts = session.query(Odds.provider_id, func.count(Odds.id)).group_by(Odds.provider_id).all()
    for provider, count in sorted(provider_counts, key=lambda x: x[1], reverse=True):
        print(f"{provider}: {count}")

    print("\n--- Example Mr Green Mappings ---")
    # Show some Mr Green events matched to Polymarket or others
    mrgreen_odds = session.query(Odds).filter(Odds.provider_id == "mrgreen").limit(5).all()
    if mrgreen_odds:
        for odd in mrgreen_odds:
            event = session.query(Event).get(odd.event_id)
            print(f"- {event.home_team} vs {event.away_team} [{event.sport}] (Odds: {odd.odds})")
    else:
        print("No odds found for 'mrgreen'.")

    # Check for unmapped sports/leagues in Mr Green events if any
    # This might require checking if league mapping was successful if we had such logic, 
    # but here we just show what we have.

if __name__ == "__main__":
    run_stats()
