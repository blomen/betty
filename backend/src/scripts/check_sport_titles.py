import sys
import os
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../../")))
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../")))

from src.db.models import init_db, get_session, Odds, Event
from sqlalchemy import func

init_db()
session = get_session()

sports = ["mma", "boxing", "motorsports"]

for s in sports:
    print(f"SPORT: {s}")
    events = session.query(Event.home_team, Event.away_team).filter(Event.sport == s).limit(5).all()
    for e in events:
        print(f"  - {e.home_team} vs {e.away_team}")
    print("-" * 20)
session.close()
