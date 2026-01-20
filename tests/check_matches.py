from sqlalchemy import create_engine, func
from sqlalchemy.orm import sessionmaker
import pandas as pd
import sys
import os

# Add to path
sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

from backend.src.db.models import DB_PATH, Event, Odds

def check_cross_provider_matches():
    if not os.path.exists(DB_PATH):
        print("No DB found.")
        return

    engine = create_engine(f"sqlite:///{DB_PATH}")
    
    # Query for events that have BOTH polymarket and unibet odds
    query = """
    SELECT 
        e.id, 
        e.sport, 
        e.home_team, 
        e.away_team, 
        e.start_time,
        GROUP_CONCAT(DISTINCT o.provider_id) as providers
    FROM events e
    JOIN odds o ON e.id = o.event_id
    GROUP BY e.id
    HAVING 
        providers LIKE '%polymarket%' 
        AND (providers LIKE '%unibet%' OR providers LIKE '%888sport%' OR providers LIKE '%leovegas%')
    """
    
    matches = pd.read_sql(query, engine)
    
    print(f"\nFound {len(matches)} Cross-Provider matches (Poly + Kambi).")
    if not matches.empty:
        print(matches.head(10).to_string())
    else:
        print("No matches found between Polymarket and Kambi providers.")
        
        # Debugging: Show unmatched Polymarket vs Unibet stats
        print("\n--- Statistics ---")
        poly_count = pd.read_sql("SELECT count(*) as c FROM events e JOIN odds o ON e.id=o.event_id WHERE o.provider_id='polymarket'", engine)['c'].iloc[0]
        unibet_count = pd.read_sql("SELECT count(*) as c FROM events e JOIN odds o ON e.id=o.event_id WHERE o.provider_id='unibet'", engine)['c'].iloc[0]
        print(f"Events with Polymarket odds: {poly_count}")
        print(f"Events with Unibet odds: {unibet_count}")
        
        # Show sample names to compare manually
        print("\n--- Sample Name Comparison ---")
        print("Polymarket Sample:")
        print(pd.read_sql("SELECT home_team, away_team FROM events e JOIN odds o ON e.id=o.event_id WHERE o.provider_id='polymarket' LIMIT 5", engine))
        print("\nUnibet Sample:")
        print(pd.read_sql("SELECT home_team, away_team FROM events e JOIN odds o ON e.id=o.event_id WHERE o.provider_id='unibet' LIMIT 5", engine))

if __name__ == "__main__":
    check_cross_provider_matches()
