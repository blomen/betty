"""
Full extraction run with comprehensive monitoring.

Features:
- Runs all active providers across all sports
- Real-time progress display
- Metrics persistence to database
- Post-extraction analysis report
- Error summary and recommendations
- Export metrics to JSON/CSV

Usage:
    python scripts/run_monitored_extraction.py [--sports football,basketball] [--providers unibet,leovegas]
    python scripts/run_monitored_extraction.py --export-json output.json
    python scripts/run_monitored_extraction.py --analyze-only  # Skip extraction, analyze last run
"""

import asyncio
import sys
import argparse
import json
from pathlib import Path
from datetime import datetime
from typing import List, Optional

# Add parent to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.logging_config import setup_logging
from src.pipeline.orchestrator import ExtractionPipeline
from src.db.models import get_session
from src.factory import ExtractorFactory

logger = setup_logging('monitored_extraction')


class ExtractionMonitor:
    """Monitors and reports on full extraction runs."""

    def __init__(self, sports: Optional[List[str]] = None, providers: Optional[List[str]] = None):
        self.sports = sports or ["football", "basketball", "tennis", "ice_hockey"]
        self.providers = providers  # None = all active
        self.factory = ExtractorFactory.get_instance()
        self.run_id = None
        self.start_time = None

    async def run_extraction(self):
        """Run full extraction with monitoring."""
        logger.info("="*80)
        logger.info("STARTING FULL EXTRACTION RUN")
        logger.info("="*80)
        logger.info(f"Providers: {'ALL ACTIVE' if not self.providers else ', '.join(self.providers)}")

        self.start_time = datetime.now()

        # Progress callback for real-time updates
        def on_progress(msg):
            logger.info(msg)

        # Run extraction
        pipeline = ExtractionPipeline()

        # Run provider extraction
        logger.info("\n--- Running extraction pipeline ---")
        providers_to_run = self.providers or self.factory.get_enabled_providers()

        results = await pipeline.run(
            polymarket=True,
            providers=providers_to_run,
            on_progress=on_progress
        )

        # Get run_id from metrics
        metrics_data = results.get('metrics', {})
        self.run_id = metrics_data.get('run_id')

        # Persist metrics to database - get from history since run is complete
        if pipeline.metrics:
            history = pipeline.metrics.get_history(limit=1)
            if history:
                last_run = history[0]
                session = get_session()
                try:
                    pipeline.metrics.persist_to_db(last_run, session)
                    logger.info(f"Metrics persisted to database (run_id: {self.run_id})")
                except Exception as e:
                    logger.error(f"Failed to persist metrics: {e}")
                finally:
                    session.close()
            else:
                logger.warning("No metrics history available to persist")

        return results

    def generate_report(self, results: dict):
        """Generate comprehensive analysis report."""
        logger.info("\n" + "="*80)
        logger.info("EXTRACTION REPORT")
        logger.info("="*80)

        duration = (datetime.now() - self.start_time).total_seconds()
        logger.info(f"Duration: {duration:.1f}s ({duration/60:.1f} minutes)")
        logger.info(f"Run ID: {self.run_id}")

        # Summary
        logger.info("\n--- SUMMARY ---")
        logger.info(f"Total Events: {results.get('total_events', 0)}")
        logger.info(f"Total Odds: {results.get('total_odds', 0)}")
        logger.info(f"Matched Events: {results.get('matched_events', 0)}")
        logger.info(f"Polymarket Events: {results.get('polymarket', {}).get('events', 0)}")

        # Provider breakdown
        logger.info("\n--- PROVIDER RESULTS ---")
        providers = results.get('providers', {})

        successful = []
        partial = []
        failed = []

        for pid, pdata in providers.items():
            # Try both events_processed (orchestrator) and total_events (metrics)
            events = pdata.get('events_processed', pdata.get('total_events', 0))
            sports_ok = pdata.get('sports_succeeded', 0)
            sports_total = pdata.get('sports_attempted', 0)
            sport_details = pdata.get('sports', {})

            # Collect errors from sport details
            errors = []
            for sport_name, sport_data in sport_details.items():
                if not sport_data.get('success', True) and sport_data.get('error'):
                    errors.append({'sport': sport_name, 'error': sport_data['error']})

            if sports_ok == sports_total and events > 0:
                successful.append((pid, events, sports_ok))
            elif events > 0:
                partial.append((pid, events, sports_ok, sports_total, errors))
            else:
                failed.append((pid, sports_total, errors))

        logger.info(f"\nSuccessful ({len(successful)}):")
        for pid, events, sports in sorted(successful, key=lambda x: -x[1]):
            logger.info(f"  [OK] {pid:20s} {events:5d} events across {sports} sports")

        if partial:
            logger.info(f"\nPartial Success ({len(partial)}):")
            for pid, events, sports_ok, sports_total, errors in partial:
                logger.info(f"  [!!] {pid:20s} {events:5d} events, {sports_ok}/{sports_total} sports OK")
                for err in errors[:2]:  # Show first 2 errors
                    logger.info(f"       Error in {err.get('sport', '?')}: {err.get('error', 'Unknown')[:60]}")

        if failed:
            logger.info(f"\nFailed ({len(failed)}):")
            for pid, sports_total, errors in failed:
                logger.info(f"  [XX] {pid:20s} 0 events")
                for err in errors[:2]:
                    logger.info(f"       Error in {err.get('sport', '?')}: {err.get('error', 'Unknown')[:60]}")

        # Recommendations
        logger.info("\n--- RECOMMENDATIONS ---")
        if failed:
            logger.info(f" * Investigate {len(failed)} failed providers (see logs/errors.log)")
        if partial:
            logger.info(f" * Review {len(partial)} providers with partial failures")
        if not failed and not partial:
            logger.info(" * All providers running successfully!")

        logger.info("\n" + "="*80)
        logger.info("Report complete. Full logs available in logs/extraction.log")
        logger.info("="*80 + "\n")

    def export_metrics(self, results: dict, output_file: str):
        """Export metrics to JSON file."""
        export_data = {
            "run_id": self.run_id,
            "timestamp": self.start_time.isoformat(),
            "results": results
        }

        with open(output_file, 'w') as f:
            json.dump(export_data, f, indent=2)

        logger.info(f"Metrics exported to {output_file}")


async def main():
    parser = argparse.ArgumentParser(description='Run monitored full extraction')
    parser.add_argument('--sports', help='Comma-separated list of sports')
    parser.add_argument('--providers', help='Comma-separated list of providers')
    parser.add_argument('--export-json', help='Export metrics to JSON file')
    parser.add_argument('--analyze-only', action='store_true', help='Skip extraction, analyze last run')

    args = parser.parse_args()

    sports = args.sports.split(',') if args.sports else None
    providers = args.providers.split(',') if args.providers else None

    monitor = ExtractionMonitor(sports=sports, providers=providers)

    if not args.analyze_only:
        results = await monitor.run_extraction()
        monitor.generate_report(results)

        if args.export_json:
            monitor.export_metrics(results, args.export_json)
    else:
        # TODO: Load last run from database and generate report
        logger.info("Analysis-only mode not yet implemented")


if __name__ == "__main__":
    asyncio.run(main())
