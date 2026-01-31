#!/usr/bin/env python3
"""
Extract all Kambi providers and export to CSV.

Usage:
    python scripts/extract_kambi_to_csv.py
"""

import asyncio
import csv
import sys
import logging
from pathlib import Path
from datetime import datetime

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.db.models import DB_PATH, init_db, get_session, Base, Event, Odds, Provider
from src.pipeline.orchestrator import ExtractionPipeline
from sqlalchemy import create_engine

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger(__name__)

# Active Kambi providers (from providers.yaml active list)
KAMBI_PROVIDERS = [
    "unibet", "leovegas", "expekt", "casumo", "svenskaspel",
    "paf", "atg", "betmgm", "speedybet", "x3000"
]


def clear_database():
    """Delete and recreate the database."""
    logger.info(f"Clearing database at {DB_PATH}")

    # Delete existing database
    if DB_PATH.exists():
        DB_PATH.unlink()
        logger.info("Deleted existing database")

    # Recreate
    init_db()
    logger.info("Created fresh database")


def export_to_csv(output_dir: Path):
    """Export all database tables to CSV files."""
    session = get_session()
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    # Export Events
    events = session.query(Event).all()
    events_file = output_dir / f"events_{timestamp}.csv"
    with open(events_file, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow(['id', 'sport', 'league', 'home_team', 'away_team', 'start_time', 'created_at', 'updated_at'])
        for e in events:
            writer.writerow([e.id, e.sport, e.league, e.home_team, e.away_team, e.start_time, e.created_at, e.updated_at])
    logger.info(f"Exported {len(events)} events to {events_file}")

    # Export Odds
    odds = session.query(Odds).all()
    odds_file = output_dir / f"odds_{timestamp}.csv"
    with open(odds_file, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow(['id', 'event_id', 'provider_id', 'market', 'outcome', 'odds', 'point', 'updated_at'])
        for o in odds:
            writer.writerow([o.id, o.event_id, o.provider_id, o.market, o.outcome, o.odds, o.point, o.updated_at])
    logger.info(f"Exported {len(odds)} odds to {odds_file}")

    # Export Providers
    providers = session.query(Provider).all()
    providers_file = output_dir / f"providers_{timestamp}.csv"
    with open(providers_file, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow(['id', 'name', 'url', 'is_enabled', 'balance', 'created_at', 'updated_at'])
        for p in providers:
            writer.writerow([p.id, p.name, p.url, p.is_enabled, p.balance, p.created_at, p.updated_at])
    logger.info(f"Exported {len(providers)} providers to {providers_file}")

    session.close()

    return {
        'events': events_file,
        'odds': odds_file,
        'providers': providers_file,
        'counts': {
            'events': len(events),
            'odds': len(odds),
            'providers': len(providers)
        }
    }


async def run_extraction():
    """Run extraction for all Kambi providers."""
    logger.info(f"Starting extraction for Kambi providers: {', '.join(KAMBI_PROVIDERS)}")

    pipeline = ExtractionPipeline()

    results = await pipeline.run(
        polymarket=False,  # Skip Polymarket
        providers=KAMBI_PROVIDERS,
        max_events_per_sport=9999
    )

    return results


async def main():
    """Main entry point."""
    # Step 1: Clear database
    clear_database()

    # Step 2: Run extraction
    results = await run_extraction()

    # Print extraction summary
    logger.info("=" * 60)
    logger.info("EXTRACTION SUMMARY")
    logger.info("=" * 60)
    logger.info(f"Total events: {results.get('total_events', 0)}")
    logger.info(f"Total odds: {results.get('total_odds', 0)}")

    for provider_id, stats in results.get('providers', {}).items():
        logger.info(f"  {provider_id}: {stats.get('events', 0)} events, {stats.get('odds', 0)} odds")

    # Step 3: Export to CSV
    output_dir = Path(__file__).parent.parent / "data"
    output_dir.mkdir(exist_ok=True)

    export_results = export_to_csv(output_dir)

    logger.info("=" * 60)
    logger.info("EXPORT COMPLETE")
    logger.info("=" * 60)
    logger.info(f"Events: {export_results['counts']['events']} -> {export_results['events']}")
    logger.info(f"Odds: {export_results['counts']['odds']} -> {export_results['odds']}")
    logger.info(f"Providers: {export_results['counts']['providers']} -> {export_results['providers']}")


if __name__ == "__main__":
    asyncio.run(main())
