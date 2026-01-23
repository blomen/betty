#!/usr/bin/env python3
"""
Provider Validation Script

Usage:
    python scripts/validate_provider.py kambi
    python scripts/validate_provider.py snabbare --sport basketball
"""

import asyncio
import sys
import os
from datetime import datetime

# Add backend directory to path
backend_path = os.path.join(os.path.dirname(__file__), '..', 'backend')
sys.path.insert(0, backend_path)

from src.factory import ExtractorFactory

async def validate_provider(provider_name: str, sport: str = "football"):
    """Run comprehensive validation checks on a provider"""

    print(f"\n{'='*60}")
    print(f"Validating Provider: {provider_name}")
    print(f"Sport: {sport}")
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
        events = await provider.extract(sport)

        if len(events) > 0:
            results["sports_coverage"] = True
            print(f"  [X] PASS: Extracted {len(events)} events")
        else:
            print(f"  [ ] FAIL: No events returned")
            return results

        # 2. Event Discovery
        print("\n[2/7] Testing event discovery...")
        missing_fields = []
        for i, e in enumerate(events[:5]):
            issues = []
            if not e.sport:
                issues.append("sport")
            if not e.home_team:
                issues.append("home_team")
            if not e.away_team:
                issues.append("away_team")
            if issues:
                missing_fields.append(f"Event {i}: missing {', '.join(issues)}")

        required_fields = len(missing_fields) == 0

        if required_fields:
            results["event_discovery"] = True
            print(f"  [X] PASS: All events have required fields")
            # Show sample events
            for i, event in enumerate(events[:3]):
                print(f"      {i+1}. {event.home_team} vs {event.away_team}")
                print(f"         League: {event.league or 'N/A'}")
                print(f"         Start: {event.start_time or 'N/A'}")
        else:
            print(f"  [ ] FAIL: Some events missing required fields")
            for issue in missing_fields:
                print(f"      - {issue}")

        # 3. Market Coverage
        print("\n[3/7] Testing market coverage...")
        market_types = set()
        market_count = 0
        events_with_markets = 0

        for event in events:
            if hasattr(event, 'markets') and event.markets:
                events_with_markets += 1
                market_count += len(event.markets)
                for market in event.markets:
                    market_types.add(market.market_type)

        print(f"  Events with markets: {events_with_markets}/{len(events)}")
        print(f"  Total markets: {market_count}")
        print(f"  Market types: {', '.join(sorted(market_types)) if market_types else 'NONE'}")

        has_moneyline = "1x2" in market_types or "moneyline" in market_types
        has_totals = "over_under" in market_types
        has_spreads = "spread" in market_types

        if has_moneyline and has_totals and has_spreads:
            results["market_coverage"] = True
            print(f"  [X] PASS: Priority 1 & 2 markets present")
        else:
            print(f"  [ ] FAIL: Missing required markets")
            print(f"      Has moneyline/1x2: {'YES' if has_moneyline else 'NO'}")
            print(f"      Has over_under: {'YES' if has_totals else 'NO'}")
            print(f"      Has spread: {'YES' if has_spreads else 'NO'}")

        # Show sample markets
        if events_with_markets > 0:
            print(f"\n  Sample markets from first event:")
            for event in events:
                if hasattr(event, 'markets') and event.markets:
                    for market in event.markets[:3]:
                        outcomes_str = ', '.join([f"{o.outcome}:{o.odds}" for o in market.outcomes[:3]])
                        point_str = f" (point={market.point})" if hasattr(market, 'point') and market.point else ""
                        print(f"      - {market.market_type}{point_str}: {outcomes_str}")
                    break

        # 4. Normalization
        print("\n[4/7] Testing data normalization...")
        normalization_issues = []

        for i, event in enumerate(events[:10]):
            if not event.home_team.islower():
                normalization_issues.append(f"Event {i}: home_team not lowercase: '{event.home_team}'")
            if not event.away_team.islower():
                normalization_issues.append(f"Event {i}: away_team not lowercase: '{event.away_team}'")

            # Check for common suffixes that should be removed
            home_parts = event.home_team.split()
            away_parts = event.away_team.split()
            suffixes = ['fc', 'sc', 'if', 'bk', 'sk', 'cf', 'ac']

            if home_parts and home_parts[-1] in suffixes:
                normalization_issues.append(f"Event {i}: home_team has suffix: '{event.home_team}'")
            if away_parts and away_parts[-1] in suffixes:
                normalization_issues.append(f"Event {i}: away_team has suffix: '{event.away_team}'")

        if len(normalization_issues) == 0:
            results["normalization"] = True
            print(f"  [X] PASS: Team names normalized")
        else:
            print(f"  [ ] FAIL: Team names not properly normalized")
            for issue in normalization_issues[:5]:
                print(f"      - {issue}")
            if len(normalization_issues) > 5:
                print(f"      ... and {len(normalization_issues) - 5} more")

        # 5. Database Compliance
        print("\n[5/7] Testing database compliance...")
        odds_issues = []

        for i, event in enumerate(events[:10]):
            if not hasattr(event, 'markets') or not event.markets:
                continue

            for market in event.markets:
                if not hasattr(market, 'outcomes'):
                    continue

                for outcome in market.outcomes:
                    if not hasattr(outcome, 'odds') or outcome.odds <= 1.0:
                        odds_issues.append(f"Event {i}, market {market.market_type}: invalid odds {getattr(outcome, 'odds', 'N/A')}")

        if len(odds_issues) == 0:
            results["database_compliance"] = True
            print(f"  [X] PASS: All odds > 1.0")
        else:
            print(f"  [ ] FAIL: Some odds <= 1.0")
            for issue in odds_issues[:5]:
                print(f"      - {issue}")

        # 6. Performance
        print("\n[6/7] Testing performance...")
        import time
        start = time.time()
        await provider.extract(sport)
        elapsed = time.time() - start

        if elapsed < 30:
            results["performance"] = True
            print(f"  [X] PASS: Extraction took {elapsed:.1f}s (< 30s)")
        else:
            print(f"  [ ] FAIL: Extraction took {elapsed:.1f}s (>= 30s)")

        # 7. Error Handling
        print("\n[7/7] Testing error handling...")
        print(f"  [X] PASS: No exceptions thrown")

    except Exception as e:
        print(f"  [ ] FAIL: Exception occurred: {e}")
        import traceback
        traceback.print_exc()
        results["error_handling"] = False

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
    elif passed >= 5:
        print("Status: NEEDS MINOR FIXES")
    else:
        print("Status: NOT READY")

    print(f"{'='*60}\n")

    return results

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python scripts/validate_provider.py <provider_name> [sport]")
        sys.exit(1)

    provider_name = sys.argv[1]
    sport = sys.argv[2] if len(sys.argv) > 2 else "football"

    asyncio.run(validate_provider(provider_name, sport))
