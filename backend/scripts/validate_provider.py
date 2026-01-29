#!/usr/bin/env python3
"""
Provider Validation Script

Usage:
    python scripts/validate_provider.py kambi
    python scripts/validate_provider.py snabbare --sport basketball
"""

import asyncio
import sys
import time
from datetime import datetime
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.factory import ExtractorFactory

async def validate_provider(provider_name: str, sport: str = "football"):
    """Run comprehensive validation checks on a provider"""

    print(f"\n{'='*60}")
    print(f"Validating Provider: {provider_name}")
    print(f"Sport: {sport}")
    print(f"Validation Date: {datetime.now().strftime('%Y-%m-%d')}")
    print(f"{'='*60}\n")

    results = {
        "sports_coverage": False,
        "event_discovery": False,
        "market_coverage": False,
        "normalization": False,
        "database_compliance": False,
        "performance": False,
        "error_handling": True  # Assume pass unless exception
    }

    try:
        # 1. Sports Coverage
        print("[1/7] Testing sports coverage...")
        factory = ExtractorFactory.get_instance()
        provider = factory.get_extractor(provider_name)
        events = await provider.extract(sport, limit=100)

        if len(events) > 0:
            results["sports_coverage"] = True
            print(f"  [PASS] Extracted {len(events)} events")
        else:
            print(f"  [FAIL] No events returned")
            return results, 1

        # 2. Event Discovery
        print("\n[2/7] Testing event discovery...")
        required_fields = all(
            e.sport and e.home_team and e.away_team
            for e in events
        )

        if required_fields:
            results["event_discovery"] = True
            print(f"  [PASS] All events have required fields")
        else:
            print(f"  [FAIL] Some events missing required fields")

        # 3. Market Coverage
        print("\n[3/7] Testing market coverage...")
        market_types = set()
        for event in events:
            for market in event.markets:
                market_types.add(market.get("type", ""))

        has_moneyline = "1x2" in market_types or "moneyline" in market_types
        has_totals = "over_under" in market_types
        has_spreads = "spread" in market_types

        if has_moneyline and has_totals and has_spreads:
            results["market_coverage"] = True
            print(f"  [PASS] Priority 1 & 2 markets present")
            print(f"  Markets found: {', '.join(sorted(market_types))}")
        else:
            print(f"  [FAIL] Missing required markets")
            print(f"  Markets found: {', '.join(sorted(market_types))}")
            print(f"  Has moneyline: {has_moneyline}")
            print(f"  Has totals: {has_totals}")
            print(f"  Has spreads: {has_spreads}")

        # 4. Normalization
        print("\n[4/7] Testing data normalization...")
        normalized = all(
            e.home_team.islower() and e.away_team.islower()
            for e in events
        )

        if normalized:
            results["normalization"] = True
            print(f"  [PASS] Team names normalized")
        else:
            print(f"  [FAIL] Team names not properly normalized")
            for e in events[:3]:
                print(f"    {e.home_team} vs {e.away_team}")

        # 5. Database Compliance
        print("\n[5/7] Testing database compliance...")
        valid_odds = True
        for event in events:
            for market in event.markets:
                for outcome in market.get("outcomes", []):
                    if outcome.get("odds", 0) <= 1.0:
                        valid_odds = False
                        break

        if valid_odds:
            results["database_compliance"] = True
            print(f"  [PASS] All odds > 1.0")
        else:
            print(f"  [FAIL] Some odds <= 1.0")

        # 6. Performance
        print("\n[6/7] Testing performance...")
        start = time.time()
        await provider.extract(sport, limit=100)
        elapsed = time.time() - start

        if elapsed < 30:
            results["performance"] = True
            print(f"  [PASS] Extraction took {elapsed:.1f}s (< 30s)")
        else:
            print(f"  [FAIL] Extraction took {elapsed:.1f}s (>= 30s)")

        # 7. Error Handling
        print("\n[7/7] Testing error handling...")
        print(f"  [PASS] No exceptions thrown")

    except Exception as e:
        print(f"  [FAIL] Exception occurred: {e}")
        results["error_handling"] = False
        import traceback
        traceback.print_exc()

    # Summary
    print(f"\n{'='*60}")
    print("VALIDATION SUMMARY")
    print(f"{'='*60}")

    passed = sum(results.values())
    total = len(results)

    for check, status in results.items():
        symbol = "[X]" if status else "[ ]"
        print(f"  {symbol} {check.replace('_', ' ').title()}")

    print(f"\nResult: {passed}/{total} checks passed")

    if passed == total:
        print("Status: PRODUCTION READY")
        exit_code = 0
    elif passed >= 5:
        print("Status: NEEDS MINOR FIXES")
        exit_code = 0
    else:
        print("Status: NOT READY")
        exit_code = 1

    print(f"{'='*60}\n")

    return results, exit_code

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python scripts/validate_provider.py <provider_name> [sport]")
        sys.exit(1)

    provider_name = sys.argv[1]
    sport = sys.argv[2] if len(sys.argv) > 2 else "football"

    results, exit_code = asyncio.run(validate_provider(provider_name, sport))
    sys.exit(exit_code)
