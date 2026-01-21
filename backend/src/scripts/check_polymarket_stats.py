import sys
import os
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../../")))
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../")))

from src.db.models import init_db, get_session, Odds, Event
from sqlalchemy import func

init_db()
session = get_session()

print("POLYMARKET EVENT COUNT PER SPORT:")
poly_stats = session.query(Event.sport, func.count(func.distinct(Event.id)))\
    .join(Odds)\
    .filter(Odds.provider_id == 'polymarket')\
    .group_by(Event.sport)\
    .order_by(func.count(func.distinct(Event.id)).desc())\
    .all()

total = 0
for sport, count in poly_stats:
    print(f"  {sport:<20}: {count}")
    total += count

print(f"\nTOTAL POLYMARKET EVENTS: {total}")
session.close()
