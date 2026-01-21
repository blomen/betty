import asyncio
import argparse
import logging
import sys
import os

# Fix paths for mixed imports (backend.src vs src)
# Add project root (oddopp)
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../../")))
# Add backend dir (oddopp/backend)
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../")))

from src.pipeline import ExtractionPipeline
from sqlalchemy.orm import sessionmaker
from sqlalchemy import create_engine, func
from src.db.models import Base, Event, Odds, Provider, init_db, get_session
from src.factory import ExtractorFactory

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger("validate_pipeline")

async def main():
    parser = argparse.ArgumentParser(description="Validate Extraction Pipeline")
    parser.add_argument("--providers", type=str, help="Comma-separated list of providers to run (e.g. 'unibet,888sport'). Default: all enabled.")
    parser.add_argument("--polymarket", action="store_true", default=True, help="Enable Polymarket extraction (default: True)")
    parser.add_argument("--skip-polymarket", action="store_true", help="Skip Polymarket extraction")
    parser.add_argument("--limit", type=int, default=10, help="Max events per sport per provider")
    args = parser.parse_args()

    # Handle Polymarket flag
    run_polymarket = args.polymarket
    if args.skip_polymarket:
        run_polymarket = False

    # Handle Providers
    providers = None
    if args.providers:
        if args.providers.lower() == "none":
            providers = []
        else:
            providers = [p.strip() for p in args.providers.split(",")]
    
    logger.info("="*50)
    logger.info("STARTING PIPELINE VALIDATION")
    logger.info(f"Providers: {providers if providers else 'ALL'}")
    logger.info(f"Polymarket: {run_polymarket}")
    logger.info(f"Limit: {args.limit}")
    logger.info("="*50)

    # Initialize DB
    init_db()
    session = get_session()

    try:
        pipeline = ExtractionPipeline(session)
        
        # Verify Provider Configs first
        factory = ExtractorFactory.get_instance()
        available = factory.get_enabled_providers()
        logger.info(f"Available Providers in Factory: {available}")
        
        if providers:
            for p in providers:
                if p not in available and p != "polymarket":
                   logger.warning(f"Warning: Requested provider '{p}' is not in enabled list.")

        # Run Pipeline
        logger.info("\n>>> RUNNING EXTRACTION...\n")
        results = await pipeline.run(
            polymarket=run_polymarket,
            providers=providers,
            max_events_per_sport=args.limit
        )

        logger.info("\n" + "="*50)
        logger.info("VALIDATION RESULTS")
        logger.info("="*50)
        
        # Polymarket Stats
        poly = results.get("polymarket", {})
        logger.info(f"Polymarket: {poly.get('events_processed')} processed ({poly.get('events_new')} new), {poly.get('odds_new')} new odds")
        
        # Breakdown by Sport (if available from logs or just query DB)
        if run_polymarket:
            logger.info("  Polymarket Sport Breakdown (DB):")
            # Note: IDs are canonical 'sport:home:away:date'. If generated from Polymarket, they might not contain 'polymarket' string in ID unless we check provider.
            # Actually, `Event` doesn't have a provider column, `Odds` does.
            # But we can check events that have Polymarket odds.
            
            poly_stats = session.query(Event.sport, func.count(func.distinct(Event.id)))\
                .join(Odds)\
                .filter(Odds.provider_id == 'polymarket')\
                .group_by(Event.sport)\
                .all()
                
            for sport, count in poly_stats:
                logger.info(f"    - {sport}: {count}")

        # Provider Stats
        prov_results = results.get("providers", {})
        for pid, res in prov_results.items():
            err = res.get("error")
            if err:
                logger.error(f"  {pid}: FAILED - {err}")
            else:
                logger.info(f"  {pid}: {res.get('events_processed')} processed, {res.get('events_new')} new, {res.get('odds_new')} new odds")

        # Match Stats
        logger.info("-" * 30)
        logger.info(f"Total Events in DB: {results.get('total_events')}")
        logger.info(f"Matched Events (Odds > 1 provider): {results.get('matched_events')}")
        
        # Show some matches
        if results.get('matched_events', 0) > 0:
            logger.info("\nSAMPLE MATCHES:")
            matches = pipeline.get_matched_events(limit=5)
            for m in matches:
                logger.info(f"  [{m['sport']}] {m['home_team']} vs {m['away_team']} ({m['start_time']})")
                for prov, odds in m['providers'].items():
                    logger.info(f"    - {prov}: {len(odds)} odds")

    except Exception as e:
        logger.error(f"Validation Failed: {e}", exc_info=True)
    finally:
        session.close()

if __name__ == "__main__":
    asyncio.run(main())
