import sys
import os
from pathlib import Path
from datetime import datetime
import collections

# Add backend to path
sys.path.append(os.path.join(str(Path(__file__).parent.parent), "backend"))

from src.db.models import get_session, Event, Odds, Provider
from sqlalchemy import func

def validate():
    session = get_session()
    print("="*60)
    print(f"DATABASE VALIDATION REPORT - {datetime.now()}")
    print("="*60)
    
    # --- 1. Basic Counts ---
    event_count = session.query(Event).count()
    provider_count = session.query(Provider).count()
    odds_count = session.query(Odds).count()
    
    print(f"Total Events: {event_count}")
    print(f"Total Providers: {provider_count}")
    print(f"Total Odds Entries: {odds_count}")
    
    if event_count == 0:
        print("\n❌ CRITICAL: No events found in DB.")
        return

    # --- 2. Provider Distribution ---
    print("\n[Provider Coverage]")
    # Get distinct providers from Odds
    provider_counts = session.query(Odds.provider_id, func.count(Odds.id)).group_by(Odds.provider_id).all()
    for pid, count in provider_counts:
        print(f"  - {pid}: {count} odds")
    
    # --- 3. Match Quality ---
    print("\n[Match Quality]")
    # Events with odds from > 1 provider
    matched_events = session.query(Event.id).join(Odds).group_by(Event.id).having(func.count(func.distinct(Odds.provider_id)) > 1).count()
    print(f"  Matched Events (Mult-Provider): {matched_events}")
    print(f"  Unmatched Events (Single Provider): {event_count - matched_events}")
    
    # --- 4. Orphaned Events (No Odds) ---
    orphaned_events = session.query(Event).filter(~Event.odds.any()).all()
    print(f"\n[Orphaned Events] (No Odds): {len(orphaned_events)}")
    if orphaned_events:
        print("  Sample orphaned events:")
        for e in orphaned_events[:5]:
            print(f"    - [{e.sport}] {e.home_team} vs {e.away_team} ({e.start_time})")

    # --- 5. Potential Duplicates (Fuzzy Match Check) ---
    print("\n[Potential Duplicates Check]")
    # Group by sport and date, then check for similar team names
    events = session.query(Event).all()
    events_by_key = collections.defaultdict(list)
    
    for e in events:
        if e.start_time:
            date_key = e.start_time.strftime("%Y-%m-%d")
        else:
            date_key = "unknown"
        events_by_key[(e.sport, date_key)].append(e)
        
    potential_dupes = 0
    
    for (sport, date), ev_list in events_by_key.items():
        if len(ev_list) < 2:
            continue
            
        # Naive N^2 check for similar names (first 4 chars match or something simple for now)
        # Proper fuzzy check is better but let's just inspect highly suspicious ones
        # like "Man Utd" vs "Manchester United" if mapped to different events
        
        seen_homes = set()
        for e in ev_list:
            # Check if this home team is very similar to another
            # Just listing them for manual review might be better if list is short
            pass
            
    # List top 10 populated events (most odds)
    print("\n[Top Populated Events]")
    top_events = session.query(Event).join(Odds).group_by(Event.id).order_by(func.count(Odds.id).desc()).limit(5).all()
    for e in top_events:
        count = session.query(Odds).filter(Odds.event_id == e.id).count()
        distinct_providers = session.query(Odds.provider_id).filter(Odds.event_id == e.id).distinct().count()
        print(f"  - {e.home_team} vs {e.away_team}: {count} odds ({distinct_providers} providers)")

    session.close()

if __name__ == "__main__":
    validate()
