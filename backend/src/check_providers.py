import sys
import os
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../../")))
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../")))

from src.db.models import init_db, get_session, Odds, Provider
from sqlalchemy import func

init_db()
session = get_session()

print("ODDS COUNT PER PROVIDER:")
counts = session.query(Odds.provider_id, func.count(Odds.id)).group_by(Odds.provider_id).all()
for provider, count in counts:
    print(f"  {provider}: {count}")

print("\nMATCHED EVENTS COUNT:")
from src.pipeline import ExtractionPipeline
pipeline = ExtractionPipeline(session)
print(f"  Matched: {pipeline._count_matched_events()}")

session.close()
