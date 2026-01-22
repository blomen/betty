
import asyncio
import logging
import sys
import os
import json

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from backend.src.pipeline import ExtractionPipeline
from backend.src.db.models import init_db

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("run_snabbare_full")

async def main():
    logger.info("Starting Full Snabbare Extraction...")
    
    # Init DB (ensure tables)
    init_db()
    
    pipeline = ExtractionPipeline()
    
    # Run pipeline for Snabbare only (Stats run)
    results = await pipeline.run(
        polymarket=False, 
        providers=["snabbare"],
        max_events_per_sport=10000 
    )
    
    print("\n\n=== Extraction Results ===")
    print(json.dumps(results, indent=2))

if __name__ == "__main__":
    asyncio.run(main())
