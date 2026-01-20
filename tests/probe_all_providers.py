import asyncio
import sys
import os
import logging
from datetime import datetime

# Add project root to path
sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

from backend.src.factory import ExtractorFactory
from backend.src.pipeline import ExtractionPipeline
from backend.src.db.models import init_db

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("test_all_providers")

# Silence noisy loggers
logging.getLogger("backend.src.providers.spectate").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)

async def test_provider(pipeline, provider_id, sport_name="Premier League"):
    """Test a single provider with a quick extraction."""
    logger.info(f"Testing {provider_id}...")
    try:
        results = await pipeline._extract_provider(
            provider_id, 
            sports=[s.kambi_sport for s in pipeline.engine.sports if s.name == sport_name], 
            limit=5
        )
        return results
    except Exception as e:
        logger.error(f"FAILED {provider_id}: {e}")
        return {"error": str(e)}

async def main():
    init_db()
    pipeline = ExtractionPipeline()
    
    # Get all active providers from Factory
    factory = ExtractorFactory.get_instance()
    all_providers = factory.get_enabled_providers()
    
    logger.info(f"Found {len(all_providers)} enabled providers.")
    
    results_summary = []
    
    for pid in all_providers:
        if pid == "polymarket": continue # Tested already
        
        logger.info(f"\n--- Probimg {pid} ---")
        res = await test_provider(pipeline, pid)
        
        processed = res.get('events_processed', 0)
        odds = res.get('odds_new', 0)
        error = res.get('error')
        
        status = "✅ OK" if processed > 0 else "❌ NO DATA"
        if error: status = f"💥 ERROR: {error}"
        
        results_summary.append({
            "id": pid,
            "status": status,
            "events": processed,
            "odds": odds
        })
        
        # small delay to be nice
        await asyncio.sleep(1)

    print("\n" + "="*60)
    print(f"{'PROVIDER':<30} | {'STATUS':<20} | {'EVENTS':<5}")
    print("="*60)
    for r in results_summary:
        print(f"{r['id']:<30} | {r['status']:<20} | {r['events']:<5}")

if __name__ == "__main__":
    asyncio.run(main())
