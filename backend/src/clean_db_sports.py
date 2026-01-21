import sys
import os
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../../")))

from src.db.models import init_db, get_session, Odds, Event

init_db()
session = get_session()

sports = ["mma", "boxing", "motorsports", "formula-1"]

print("Cleaning DB for sports:", sports)
for s in sports:
    count = session.query(Event).filter(Event.sport == s).delete()
    print(f"Deleted {count} events for {s}")

session.commit()
session.close()
