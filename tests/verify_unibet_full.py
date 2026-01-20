import asyncio
import sys
import os
import logging
import pandas as pd
from sqlalchemy import create_engine

# Add project root to path
sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

from backend.src.pipeline import ExtractionPipeline
from backend.src.db.models import init_db, DB_PATH

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("verify_unibet_full")

async def main():
    logger.info("Initializing Pipeline...")
    pipeline = ExtractionPipeline()
    
    # 1. Retrieve all sports from sports.json (as loaded in pipeline.engine.sports)
    all_sports_config = pipeline.engine.sports
    
    # Keep only unique Kambi sports to avoid duplicate API calls
    kambi_sports = set()
    for s in all_sports_config:
        if s.kambi_sport:
            kambi_sports.add(s.kambi_sport)
    
    unique_kambi_sports = sorted(list(kambi_sports))
    logger.info(f"Identified {len(unique_kambi_sports)} unique Kambi sports from config: {unique_kambi_sports}")
    
    # Run Unibet Extraction for ALL sports
    logger.info("Run Extraction Pipeline for UNIBET only...")
    results = await pipeline._extract_provider(
        provider_id="unibet",
        sports=unique_kambi_sports,
        limit=20 # Limit 20 events per sport to be reasonable but covering
    )
    
    logger.info(f"Extraction Results: {results}")
    
    # Verification Steps
    logger.info("\n=== VERIFICATION ===")
    
    # 2. Check Database
    engine = create_engine(f"sqlite:///{DB_PATH}")
    
    # A. Count odds by sport for Unibet
    odds_by_sport_query = """
    SELECT e.sport, COUNT(*) as outcomes_count 
    FROM odds o 
    JOIN events e ON o.event_id = e.id 
    WHERE o.provider_id = 'unibet' 
    GROUP BY e.sport
    ORDER BY outcomes_count DESC
    """
    df_sport = pd.read_sql(odds_by_sport_query, engine)
    
    print("\n[Unibet Odds Count per Sport]")
    if df_sport.empty:
        print("❌ NO DATA FOUND")
    else:
        print(df_sport.to_string(index=False))
        
    # B. Check for missing sports
    found_sports = set(df_sport['sport'].tolist())
    missing_sports = [s for s in unique_kambi_sports if s not in found_sports]
    
    if missing_sports:
        print(f"\n⚠️ Missing data for Kambi sports: {missing_sports}")
        print("Note: Some sports might genuinely have no events (e.g. offseason).")
    else:
        print("\n✅ All Kambi sports have data!")

    # C. Sample Events
    print("\n[Sample Events]")
    sample_df = pd.read_sql("""
    SELECT e.id, e.sport, e.home_team, e.away_team 
    FROM events e 
    JOIN odds o ON e.id = o.event_id 
    WHERE o.provider_id = 'unibet' 
    GROUP BY e.sport 
    LIMIT 5
    """, engine)
    print(sample_df.to_string(index=False))

if __name__ == "__main__":
    asyncio.run(main())
