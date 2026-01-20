import sys
import os
import pandas as pd
from sqlalchemy import create_engine

# Add project root to path
sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

from backend.src.db.models import DB_PATH

def analyze_markets():
    if not os.path.exists(DB_PATH):
        print("No DB found.")
        return

    engine = create_engine(f"sqlite:///{DB_PATH}")
    
    print("Querying distinct market types...")
    query = """
    SELECT market, COUNT(*) as count 
    FROM odds 
    GROUP BY market 
    ORDER BY count DESC
    LIMIT 50
    """
    
    df = pd.read_sql(query, engine)
    
    if df.empty:
        print("No odds data found.")
        return

    print(f"\nFound {len(df)} distinct market types. Top 50:")
    print(df.to_string(index=False))
    
    # Check specifically for keywords
    print("\n--- Keyword Check ---")
    keywords = ["handicap", "spread", "total", "over", "under", "points", "goals"]
    for kw in keywords:
        matches = df[df['market'].str.lower().str.contains(kw)]
        if not matches.empty:
            print(f"✅ Found '{kw}' related markets:")
            print(matches['market'].head(5).tolist())
        else:
            print(f"❌ No markets found containing '{kw}'")

if __name__ == "__main__":
    analyze_markets()
