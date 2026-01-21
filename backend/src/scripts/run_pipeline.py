import asyncio
import logging
from backend.src.pipeline import ExtractionPipeline
from backend.src.db.models import init_db

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

async def main():
    pipeline = ExtractionPipeline()
    print("Starting pipeline for mrgreen (ALL sports)...")
    
    # We rely on pipeline.run() to iterate over all sports in config
    results = await pipeline.run(
        polymarket=False,
        providers=["mrgreen"], 
        max_events_per_sport=10 # Small limit per sport to verify breadth without taking forever
        # If we wanted to test *all* sports from config/sports.json, 
        # pipeline.run does exactly that: it iterates pipeline.engine.sports
    )
    
    print("\n--- Execution Results ---")
    print(results)
    
    # Check DB stats by sport for mrgreen
    from backend.src.db.models import Event, Odds
    from sqlalchemy import func
    
    print("\n--- Mr Green Events per Sport ---")
    
    # Query events that have odds from mrgreen
    counts = pipeline.session.query(Event.sport, func.count(func.distinct(Event.id)))\
        .join(Odds)\
        .filter(Odds.provider_id == "mrgreen")\
        .group_by(Event.sport)\
        .all()
        
    for sport, count in sorted(counts, key=lambda x: x[1], reverse=True):
        print(f"{sport}: {count}")
    
    # Total
    total_mg = pipeline.session.query(Odds).filter(Odds.provider_id == "mrgreen").count()
    print(f"Total Mr Green Odds: {total_mg}")
    
    # Check DB immediately
    from backend.src.db.models import Provider, Event, Odds
    p = pipeline.session.query(Provider).get("mrgreen")
    print(f"Provider 'mrgreen' in DB: {p}")
    
    events_count = pipeline.session.query(Event).join(Odds).filter(Odds.provider_id == "mrgreen").count()
    print(f"Events with mrgreen odds: {events_count}")
    
    odds_count = pipeline.session.query(Odds).filter(Odds.provider_id == "mrgreen").count()
    print(f"Total mrgreen odds: {odds_count}")

if __name__ == "__main__":
    asyncio.run(main())
