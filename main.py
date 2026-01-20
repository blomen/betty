"""
Oddopp Main Entry Point

Runs the extraction pipeline.
"""
import asyncio
import logging
import sys
import os

# Add backend to path to allow 'src' imports to work
sys.path.append(os.path.join(os.path.dirname(__file__), "backend"))

from src.pipeline import ExtractionPipeline
from src.db.models import init_db

import argparse

async def main():
    """Run extraction pipeline."""
    # Parse CLI arguments
    parser = argparse.ArgumentParser(description="Oddopp Extraction Pipeline")
    parser.add_argument("--providers", nargs="+", help="List of providers to run (e.g. unibet 888sport)")
    parser.add_argument("--limit", type=int, default=50, help="Max events per sport")
    parser.add_argument("--no-poly", action="store_true", help="Skip Polymarket extraction")
    args = parser.parse_args()

    # Configure logging
    logging.basicConfig(
        level=logging.INFO, 
        format='%(levelname)s - %(message)s',
        datefmt='%H:%M:%S'
    )
    
    # Silence detailed logs from libraries
    logging.getLogger("backend.src.extractors.engine").setLevel(logging.INFO)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("asyncio").setLevel(logging.WARNING)
    
    # Initialize DB
    init_db()
    
    print("=" * 70)
    print("ODDOPP EXTRACTION PIPELINE")
    print("=" * 70)
    
    pipeline = ExtractionPipeline()
    
    # Run with CLI args or defaults
    providers = args.providers if args.providers else ["unibet", "888sport", "leovegas", "casumo", "expekt", "mrgreen"]
    
    results = await pipeline.run(
        polymarket=not args.no_poly,
        providers=providers,  
        max_events_per_sport=args.limit, 
    )
    
    print("\n" + "=" * 70)
    print("RESULTS")
    print("=" * 70)
    
    poly = results['polymarket']
    print(f"Polymarket: {poly.get('events_processed', 0)} processed ({poly.get('events_new', 0)} new)")
    print(f"            {poly.get('odds_processed', 0)} odds ({poly.get('odds_new', 0)} new)")
    
    for provider, data in results['providers'].items():
        print(f"{provider.title()}: {data.get('events_processed', 0)} processed ({data.get('events_new', 0)} new)")
        print(f"{' '*len(provider)}  {data.get('odds_processed', 0)} odds ({data.get('odds_new', 0)} new)")
        if 'error' in data:
            print(f"  ERROR: {data['error']}")
            
    print(f"\nTotal events: {results['total_events']}")
    print(f"Matched events: {results['matched_events']}")
    
    # Show some matched events
    if results['matched_events'] > 0:
        print("\n" + "=" * 70)
        print("SAMPLE MATCHED EVENTS")
        print("=" * 70)
        
        matched = pipeline.get_matched_events(limit=5)
        for event in matched:
            print(f"\n{event['home_team']} vs {event['away_team']}")
            print(f"  Sport: {event['sport']}")
            for provider, odds in event['providers'].items():
                print(f"  {provider}: {len(odds)} odds")

if __name__ == "__main__":
    asyncio.run(main())
