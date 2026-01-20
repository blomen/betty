"""
Analyze unmatched events to find potential missed matches.
"""
import sys
from pathlib import Path
from datetime import datetime, timedelta

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from src.db.models import init_db, get_session, Event, Odds
from src.utils.matching import match_events, normalize_team_name
from sqlalchemy import func

def analyze_missed_matches():
    init_db()
    session = get_session()
    
    print("Fetching events...")
    
    # 1. Get all Unibet events (unmatched) vs Polymarket events (unmatched)
    # Actually, we can just get ALL events and group by provider
    
    # Get all events with their provider IDs
    events = session.query(Event).all()
    
    unibet_events = []
    poly_events = []
    matched_ids = set()
    
    for event in events:
        providers = set(o.provider_id for o in event.odds)
        if 'unibet' in providers and 'polymarket' in providers:
            matched_ids.add(event.id)
            continue
            
        if 'unibet' in providers:
            unibet_events.append(event)
        elif 'polymarket' in providers:
            poly_events.append(event)
            
    print(f"Total Matched: {len(matched_ids)}")
    print(f"Unmatched Unibet: {len(unibet_events)}")
    print(f"Unmatched Polymarket: {len(poly_events)}")
    
    print(f"\nScanning for missed matches (Loose Threshold 60%)...")
    print("-" * 60)
    
    potential_matches = []
    
    # Group by sport to speed up
    poly_by_sport = {}
    for e in poly_events:
        poly_by_sport.setdefault(e.sport, []).append(e)
        
    for uni in unibet_events:
        candidates = poly_by_sport.get(uni.sport, [])
        if not candidates:
            continue
            
        uni_date = uni.start_time.strftime("%Y%m%d") if uni.start_time else "00000000"
        
        for poly in candidates:
            poly_date = poly.start_time.strftime("%Y%m%d") if poly.start_time else "00000000"
            
            # Date must match exactly (unless timezone issue, but usually date match is safe)
            if uni_date != poly_date:
                # Allow 1 day diff for timezone matching? 
                # Let's keep strict date for now to reduce false positives
                continue
                
            # Check name similarity with lowered threshold
            res = match_events(
                poly.home_team, poly.away_team, poly_date,
                uni.home_team, uni.away_team, uni_date,
                uni.sport,
                threshold=60 # LOOSE threshold
            )
            
            if res.matched:
                potential_matches.append((uni, poly, res.confidence))
            # Even if not matched, if confidence is high, consider it
            elif res.confidence >= 60:
                potential_matches.append((uni, poly, res.confidence))

    # Sort by score desc
    potential_matches.sort(key=lambda x: x[2], reverse=True)
    
    for uni, poly, score in potential_matches[:50]:
        print(f"[{score}%] {uni.sport.upper()}")
        print(f"  Unibet: {uni.home_team} vs {uni.away_team}")
        print(f"  Poly:   {poly.home_team} vs {poly.away_team}")
        print(f"  Date:   {uni.start_time}")
        print("")

if __name__ == "__main__":
    analyze_missed_matches()
