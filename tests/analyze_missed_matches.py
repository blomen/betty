import sys
import os
from pathlib import Path
from sqlalchemy import func
from difflib import SequenceMatcher

# Add backend to path
sys.path.append(os.path.join(str(Path(__file__).parent.parent), "backend"))

# Force UTF-8 output for Windows
sys.stdout.reconfigure(encoding='utf-8')

from src.db.models import get_session, Event, Odds

def similarity(a, b):
    return SequenceMatcher(None, a, b).ratio()

def analyze_missed_matches():
    session = get_session()
    print("="*60)
    print("MISSED MATCH ANALYSIS")
    print("="*60)

    # 1. Get all events
    # We want to find events that are separate but look similar
    events = session.query(Event).all()
    
    # 2. Group by Sport + Date
    grouped = {}
    for e in events:
        if not e.start_time:
            continue
        
        date_key = e.start_time.strftime("%Y-%m-%d")
        key = (e.sport, date_key)
        
        if key not in grouped:
            grouped[key] = []
        grouped[key].append(e)

    print(f"Scanning {len(grouped)} Sport/Date groups for duplicates...")
    
    suspect_pairs = []

    for (sport, date), ev_list in grouped.items():
        if len(ev_list) < 2:
            continue
            
        # Compare every pair in this group
        # O(N^2) but N per day/sport is small (<100)
        
        # Sort by ID to ensure consistent pair ordering and avoid self-compare
        ev_list.sort(key=lambda x: x.id)
        
        for i in range(len(ev_list)):
            for j in range(i + 1, len(ev_list)):
                e1 = ev_list[i]
                e2 = ev_list[j]
                
                # Check if they are already matched?
                # No, they are separate DB events, so by definition they are NOT matched in our system.
                # We want to see if they SHOULD be.
                
                # Compare team names
                home_sim = similarity(e1.home_team.lower(), e2.home_team.lower())
                away_sim = similarity(e1.away_team.lower(), e2.away_team.lower())
                
                avg_sim = (home_sim + away_sim) / 2
                
                # Threshold for "Suspiciously Similar"
                if avg_sim > 0.6: # 60% similarity
                    # Check provider coverage
                    # Ideally we want pairs where one is Poly and one is Kambi
                    odds1 = session.query(Odds.provider_id).filter(Odds.event_id == e1.id).all()
                    odds2 = session.query(Odds.provider_id).filter(Odds.event_id == e2.id).all()
                    
                    prov1 = set(o[0] for o in odds1)
                    prov2 = set(o[0] for o in odds2)
                    
                    # If they share a provider, they are likely just duplicate entries from same provider?
                    # Or valid different games (e.g. U19 vs regular)?
                    
                    suspect_pairs.append({
                        "score": avg_sim,
                        "e1": e1,
                        "e2": e2,
                        "prov1": prov1,
                        "prov2": prov2
                    })

    # Sort by score desc
    suspect_pairs.sort(key=lambda x: x["score"], reverse=True)
    
    print(f"\nFound {len(suspect_pairs)} potentially missed matches.\n")
    
    shown = 0
    for p in suspect_pairs:
        e1 = p["e1"]
        e2 = p["e2"]
        
        # Filter for interesting cases: Poly vs Non-Poly
        has_poly1 = "polymarket" in p["prov1"]
        has_poly2 = "polymarket" in p["prov2"]
        
        # Case 1: Poly unmatched with Bookie (High Value)
        is_interesting = (has_poly1 and not has_poly2) or (has_poly2 and not has_poly1)
        
        if is_interesting or shown < 20: # Show mostly interesting ones, but some others too
            tag = "[MISSED POLY MATCH]" if ((has_poly1 and not has_poly2) or (has_poly2 and not has_poly1)) else "[DUPLICATE?]"
            
            print(f"{tag} Score: {p['score']:.2f}")
            print(f"  1: {e1.home_team} vs {e1.away_team} ({', '.join(p['prov1'])})")
            print(f"  2: {e2.home_team} vs {e2.away_team} ({', '.join(p['prov2'])})")
            print(f"  Date: {e1.start_time} | Sport: {e1.sport}")
            print("-" * 40)
            shown += 1
            
        if shown >= 50:
            break

    session.close()

if __name__ == "__main__":
    analyze_missed_matches()
