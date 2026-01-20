from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
import pandas as pd
import sys
import os

# Add to path
sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

from backend.src.db.models import DB_PATH, Event, Odds

def view_results():
    if not os.path.exists(DB_PATH):
        print("No DB found.")
        return

    engine = create_engine(f"sqlite:///{DB_PATH}")
    
    print("\n=== EXTRACTED EVENTS BY SPORT ===")
    
    with open("events_dump.txt", "w", encoding="utf-8") as f:
        # query unique sports
        sports = pd.read_sql("SELECT DISTINCT sport FROM events", engine)['sport'].tolist()
        
        for sport in sports:
            f.write(f"\n--- {sport.upper()} ---\n")
            sport_events = pd.read_sql(f"SELECT home_team, away_team, start_time FROM events WHERE sport='{sport}'", engine)
            if sport_events.empty:
                f.write("  No events found.\n")
            else:
                for _, row in sport_events.iterrows():
                    f.write(f"  {row['home_team']} vs {row['away_team']} ({row['start_time']})\n")

        f.write(f"\nTotal Events: {pd.read_sql('SELECT count(*) as c FROM events', engine)['c'].iloc[0]}\n")
    
    print("Dumped events to events_dump.txt")


if __name__ == "__main__":
    view_results()
