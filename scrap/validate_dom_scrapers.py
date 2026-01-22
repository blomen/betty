#!/usr/bin/env python
"""
Validate DOM scraper providers (Spectate and Snabbare).
Tests mrgreen, 888sport, and snabbare against all sports.
"""

import asyncio
import logging
import json
from pathlib import Path
from typing import Dict, List
from backend.src.factory import ExtractorFactory
from backend.src.config.loader import ConfigLoader

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Reduce noise from transport logs
logging.getLogger("backend.src.core.transport").setLevel(logging.WARNING)


async def validate_provider_sport(provider_id: str, sport: str, factory: ExtractorFactory) -> Dict:
    """Validate a single provider for a single sport."""
    result = {
        "provider": provider_id,
        "sport": sport,
        "success": False,
        "event_count": 0,
        "sample_event": None,
        "error": None
    }

    try:
        retriever = factory.get_extractor(provider_id)
        events = await retriever.extract(sport, limit=3)

        result["success"] = True
        result["event_count"] = len(events)

        if events:
            # Save sample event
            e = events[0]
            result["sample_event"] = {
                "match": f"{e.home_team} vs {e.away_team}",
                "league": e.league,
                "markets": len(e.markets) if e.markets else 0
            }
            logger.info(f"✓ {provider_id:12} | {sport:20} | {len(events):2} events | {e.home_team} vs {e.away_team}")
        else:
            logger.warning(f"⚠ {provider_id:12} | {sport:20} |  0 events | (no matches found)")

    except Exception as e:
        result["error"] = str(e)
        logger.error(f"✗ {provider_id:12} | {sport:20} | ERROR: {str(e)[:50]}")

    return result


async def main():
    """Main validation routine."""

    # DOM scraper providers
    dom_providers = ["mrgreen", "888sport", "snabbare"]

    # Load sports from config
    sports_file = Path("backend/src/config/sports.json")
    with open(sports_file) as f:
        sports_config = json.load(f)

    sports = [s["key"] for s in sports_config]

    logger.info("=" * 80)
    logger.info("DOM SCRAPER VALIDATION")
    logger.info("=" * 80)
    logger.info(f"\nProviders: {', '.join(dom_providers)}")
    logger.info(f"Sports: {', '.join(sports)}")
    logger.info(f"Total tests: {len(dom_providers)} × {len(sports)} = {len(dom_providers) * len(sports)}")

    # Initialize factory
    factory = ExtractorFactory()
    results = []

    for provider_id in dom_providers:
        logger.info(f"\n{'='*80}")
        logger.info(f"TESTING: {provider_id.upper()}")
        logger.info(f"{'='*80}\n")

        for sport in sports:
            result = await validate_provider_sport(provider_id, sport, factory)
            results.append(result)

    # Close all browser-based retrievers
    for provider_id in dom_providers:
        try:
            retriever = factory.get_extractor(provider_id)
            if hasattr(retriever, 'close'):
                await retriever.close()
        except:
            pass

    # Print summary
    print("\n" + "=" * 80)
    print("VALIDATION SUMMARY")
    print("=" * 80)

    for provider_id in dom_providers:
        provider_results = [r for r in results if r["provider"] == provider_id]

        successful = sum(1 for r in provider_results if r["success"] and r["event_count"] > 0)
        empty = sum(1 for r in provider_results if r["success"] and r["event_count"] == 0)
        failed = sum(1 for r in provider_results if not r["success"])
        total_events = sum(r["event_count"] for r in provider_results)

        print(f"\n{provider_id.upper()}:")
        print(f"  ✓ Success with events: {successful}/{len(sports)}")
        print(f"  ⚠ Empty (no events):   {empty}/{len(sports)}")
        print(f"  ✗ Failed/Error:        {failed}/{len(sports)}")
        print(f"  📊 Total events:       {total_events}")

        # Show sports breakdown
        print(f"\n  Sports breakdown:")
        for sport in sports:
            r = next((r for r in provider_results if r["sport"] == sport), None)
            if r:
                if r["success"] and r["event_count"] > 0:
                    status = f"✓ {r['event_count']} events"
                elif r["success"]:
                    status = "⚠ 0 events"
                else:
                    status = f"✗ {r['error'][:30]}"
                print(f"    {sport:20} {status}")

    # Overall stats
    total_success = sum(1 for r in results if r["success"] and r["event_count"] > 0)
    total_tests = len(results)
    success_rate = (total_success / total_tests) * 100 if total_tests > 0 else 0

    print(f"\n{'='*80}")
    print(f"OVERALL: {total_success}/{total_tests} successful ({success_rate:.1f}%)")
    print(f"{'='*80}\n")


if __name__ == "__main__":
    asyncio.run(main())
