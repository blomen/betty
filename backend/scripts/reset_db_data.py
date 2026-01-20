"""
Reset database data (Odds, Events, Opportunities) but keep Providers/Profiles.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from src.db.models import init_db, get_session, Event, Odds, Opportunity

def reset_data():
    init_db()
    session = get_session()
    
    print("Deleting Opportunities...")
    session.query(Opportunity).delete()
    
    print("Deleting Odds...")
    session.query(Odds).delete()
    
    print("Deleting Events...")
    session.query(Event).delete()
    
    session.commit()
    print("Database cleared!")

if __name__ == "__main__":
    reset_data()
