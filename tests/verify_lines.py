import pandas as pd
from sqlalchemy import create_engine
import sys
import os
import asyncio

sys.path.append(os.path.join(os.path.dirname(__file__), ".."))
from backend.src.db.models import DB_PATH
from backend.src.pipeline import ExtractionPipeline
from backend.src.db.models import init_db

# Configure logging
import logging
logging.basicConfig(level=logging.INFO)
logging.getLogger("backend.src.providers.spectate").setLevel(logging.WARNING)

async def main():
    # 1. Run quick extraction to populate 'point' column
    print("Running quick extraction for Unibet (spread/total)...")
    pipeline = ExtractionPipeline()
    # Filter for NBA to ensure spread/total markets
    pipeline.engine.sports = [s for s in pipeline.engine.sports if s.name == "NBA"]
    
    await pipeline._extract_provider("unibet", sports=["basketball"], limit=5)
    
    # 2. Inspect DB
    print("\n[Inspecting Data]")
    engine = create_engine(f"sqlite:///{DB_PATH}")
    query = """
    SELECT provider_id, market, outcome, odds, point 
    FROM odds 
    WHERE provider_id='unibet' 
    AND (market LIKE '%spread%' OR point IS NOT NULL)
    LIMIT 20
    """
    df = pd.read_sql(query, engine)
    
    if df.empty:
        print("No data with point found.")
    else:
        print(df.to_string())

if __name__ == "__main__":
    asyncio.run(main())
