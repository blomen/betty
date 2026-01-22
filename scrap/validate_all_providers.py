#!/usr/bin/env python
"""
Comprehensive provider validation script.
Tests all active providers against all sports in sports.json.
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
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Suppress verbose logs from providers
logging.getLogger("backend.src.providers").setLevel(logging.WARNING)
logging.getLogger("backend.src.core").setLevel(logging.WARNING)


async def validate_provider_sport(provider_id: str, sport: str, factory: ExtractorFactory) -> Dict:
    """Validate a single provider for a single sport."""
    result = {
        "provider": provider_id,
        "sport": sport,
        "success": False,
        "event_count": 0,
        "error": None
    }

    try:
        retriever = await factory.get_retriever(provider_id)
        events = await retriever.extract(sport, limit=5)  # Just fetch 5 events for validation

        result["success"] = True
        result["event_count"] = len(events)

        if events:
            logger.info(f"✓ {provider_id:12} | {sport:20} | {len(events)} events")
        else:
            logger.warning(f"⚠ {provider_id:12} | {sport:20} | 0 events")

    except Exception as e:
        result["error"] = str(e)
        logger.error(f"✗ {provider_id:12} | {sport:20} | ERROR: {e}")

    return result


async def validate_all():
    """Validate all active providers against all sports."""

    # Load sports configuration
    sports_file = Path("backend/src/config/sports.json")
    with open(sports_file) as f:
        sports_config = json.load(f)

    sports = [s["key"] for s in sports_config]

    # Load provider configuration
    config_loader = ConfigLoader()
    active_providers = config_loader.get_active_providers()

    logger.info("=" * 80)
    logger.info("PROVIDER VALIDATION - ALL SPORTS")
    logger.info("=" * 80)
    logger.info(f"\nSports to test: {len(sports)}")
    logger.info(f"Active providers: {len(active_providers)}")
    logger.info(f"\nProviders: {', '.join(active_providers)}")
    logger.info(f"Sports: {', '.join(sports)}")

    # Initialize factory
    factory = ExtractorFactory()

    # Run validations
    results = []

    for provider_id in active_providers:
        logger.info(f"\n{'='*80}")
        logger.info(f"Testing: {provider_id.upper()}")
        logger.info(f"{'='*80}")

        for sport in sports:
            result = await validate_provider_sport(provider_id, sport, factory)
            results.append(result)

            # Small delay to avoid rate limiting
            await asyncio.sleep(0.5)

    # Close all retrievers
    await factory.close_all()

    # Print summary
    print("\n" + "=" * 80)
    print("VALIDATION SUMMARY")
    print("=" * 80)

    # Group by provider
    by_provider: Dict[str, List[Dict]] = {}
    for r in results:
        provider = r["provider"]
        if provider not in by_provider:
            by_provider[provider] = []
        by_provider[provider].append(r)

    for provider, provider_results in by_provider.items():
        successful = sum(1 for r in provider_results if r["success"] and r["event_count"] > 0)
        failed = sum(1 for r in provider_results if not r["success"])
        empty = sum(1 for r in provider_results if r["success"] and r["event_count"] == 0)
        total_events = sum(r["event_count"] for r in provider_results)

        print(f"\n{provider}:")
        print(f"  ✓ Success with events: {successful}/{len(sports)}")
        print(f"  ⚠ Success but empty:   {empty}/{len(sports)}")
        print(f"  ✗ Failed:              {failed}/{len(sports)}")
        print(f"  📊 Total events:       {total_events}")

        # Show which sports had issues
        if empty > 0:
            empty_sports = [r["sport"] for r in provider_results if r["success"] and r["event_count"] == 0]
            print(f"  Empty sports: {', '.join(empty_sports)}")

        if failed > 0:
            failed_sports = [r["sport"] for r in provider_results if not r["success"]]
            print(f"  Failed sports: {', '.join(failed_sports)}")

    # Overall stats
    total_success = sum(1 for r in results if r["success"] and r["event_count"] > 0)
    total_tests = len(results)
    success_rate = (total_success / total_tests) * 100 if total_tests > 0 else 0

    print(f"\n{'='*80}")
    print(f"OVERALL: {total_success}/{total_tests} successful ({success_rate:.1f}%)")
    print(f"{'='*80}\n")


if __name__ == "__main__":
    asyncio.run(validate_all())
