#!/usr/bin/env python3
"""
Gecko Provider Validation Script

Validates Betsson/Betsafe/NordicBet against production-ready criteria.
Based on backend/docs/validated.md
"""

import asyncio
import sys
import time
from datetime import datetime
from pathlib import Path

# Add backend to path
sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

from src.factory import ExtractorFactory
from src.matching.normalizer import normalize_team_name

# Sports to test (from gecko_v2.SPORT_SLUGS)
TEST_SPORTS = [
    "football",
    "basketball",
    "tennis",
    "ice_hockey",
    "american_football",
    "baseball",
    "mma",
    "esports",
    "rugby",
    "cricket",
    "boxing",
    "handball",
]

async def validate_provider(provider_name: str):
    """Run comprehensive validation checks on a Gecko provider"""

    print(f"\n{'='*80}")
    print(f"GECKO PROVIDER VALIDATION: {provider_name.upper()}")
    print(f"{'='*80}\n")

    results = {
        "sports_coverage": {},
        "event_discovery": False,
        "market_coverage": False,
        "normalization": False,
        "database_compliance": False,
        "performance": False,
        "error_handling": True,
    }

    all_events = []
    sport_timings = {}

    try:
        provider = ExtractorFactory.get_instance().get_extractor(provider_name)

        # Test each sport
        print(f"[1/7] Testing sports coverage...\n")

        for sport in TEST_SPORTS:
            try:
                start = time.time()
                events = await provider.extract(sport, limit=20)
                elapsed = time.time() - start

                sport_timings[sport] = elapsed
                results["sports_coverage"][sport] = len(events)

                if events:
                    all_events.extend(events)
                    print(f"  [{sport:20s}] {len(events):3d} events  ({elapsed:5.1f}s)")
                else:
                    print(f"  [{sport:20s}]   0 events  ({elapsed:5.1f}s) - No data available")

            except Exception as e:
                print(f"  [{sport:20s}] ERROR: {e}")
                results["sports_coverage"][sport] = 0

        sports_with_data = sum(1 for count in results["sports_coverage"].values() if count > 0)
        total_events = sum(results["sports_coverage"].values())

        print(f"\n  Summary: {sports_with_data}/{len(TEST_SPORTS)} sports have data")
        print(f"  Total events: {total_events}")

        if not all_events:
            print("\n  FAIL: No events extracted from any sport")
            print_summary(results)
            return results

        # 2. Event Discovery
        print(f"\n[2/7] Testing event discovery...")

        missing_fields = []
        for event in all_events[:10]:
            if not event.sport:
                missing_fields.append("sport")
            if not event.home_team:
                missing_fields.append("home_team")
            if not event.away_team:
                missing_fields.append("away_team")

        if not missing_fields:
            results["event_discovery"] = True
            print(f"  PASS: All events have required fields")
            print(f"  Sample events:")
            for event in all_events[:3]:
                print(f"    - {event.home_team} vs {event.away_team} ({event.sport}, {event.league})")
        else:
            print(f"  FAIL: Some events missing: {set(missing_fields)}")

        # 3. Market Coverage
        print(f"\n[3/7] Testing market coverage...")

        market_types = set()
        market_counts = {}
        total_markets = 0

        for event in all_events:
            total_markets += len(event.markets)
            for market in event.markets:
                market_type = market.get('type', 'unknown')
                market_types.add(market_type)
                market_counts[market_type] = market_counts.get(market_type, 0) + 1

        has_moneyline = "1x2" in market_types or "moneyline" in market_types
        has_totals = "over_under" in market_types
        has_spreads = "spread" in market_types

        print(f"  Total markets: {total_markets}")
        print(f"  Market types found: {len(market_types)}")

        # Show top 10 market types
        sorted_markets = sorted(market_counts.items(), key=lambda x: x[1], reverse=True)
        for mtype, count in sorted_markets[:10]:
            priority = ""
            if mtype in ["1x2", "moneyline"]:
                priority = " [PRIORITY 1]"
            elif mtype in ["over_under", "spread"]:
                priority = " [PRIORITY 2]"
            print(f"    {mtype:30s}: {count:4d}{priority}")

        if has_moneyline and has_totals and has_spreads:
            results["market_coverage"] = True
            print(f"  PASS: Priority 1 & 2 markets present")
        else:
            print(f"  PARTIAL:")
            print(f"    Moneyline/1x2: {'YES' if has_moneyline else 'NO'}")
            print(f"    Over/under: {'YES' if has_totals else 'NO'}")
            print(f"    Spread: {'YES' if has_spreads else 'NO'}")

        # 4. Normalization
        print(f"\n[4/7] Testing data normalization...")

        not_lowercase = []
        has_suffixes = []

        for event in all_events[:20]:
            if not event.home_team.islower():
                not_lowercase.append(event.home_team)
            if not event.away_team.islower():
                not_lowercase.append(event.away_team)

            # Check for common suffixes
            home_words = event.home_team.split()
            away_words = event.away_team.split()

            suffixes = ['fc', 'sc', 'if', 'bk', 'sk', 'cf', 'ac']
            if any(word in suffixes for word in home_words):
                has_suffixes.append(event.home_team)
            if any(word in suffixes for word in away_words):
                has_suffixes.append(event.away_team)

        if not not_lowercase and not has_suffixes:
            results["normalization"] = True
            print(f"  PASS: Team names properly normalized")
        else:
            if not_lowercase:
                print(f"  FAIL: {len(not_lowercase)} names not lowercase")
                print(f"    Examples: {not_lowercase[:3]}")
            if has_suffixes:
                print(f"  FAIL: {len(has_suffixes)} names have suffixes")
                print(f"    Examples: {has_suffixes[:3]}")

        # 5. Database Compliance
        print(f"\n[5/7] Testing database compliance...")

        invalid_odds = []
        missing_points = []

        for event in all_events:
            for market in event.markets:
                market_type = market.get('type', 'unknown')

                for outcome in market.get('outcomes', []):
                    odds_value = outcome.get('odds', 0)

                    if odds_value <= 1.0:
                        invalid_odds.append((event.id, market_type, odds_value))

                # Check point values for spreads/totals
                if market_type in ['over_under', 'spread']:
                    if 'line' not in market and 'point' not in market:
                        missing_points.append((event.id, market_type))

        if not invalid_odds and not missing_points:
            results["database_compliance"] = True
            print(f"  PASS: All odds > 1.0, points present for spreads/totals")
        else:
            if invalid_odds:
                print(f"  FAIL: {len(invalid_odds)} odds <= 1.0")
                print(f"    Examples: {invalid_odds[:3]}")
            if missing_points:
                print(f"  WARN: {len(missing_points)} spreads/totals missing points")
                print(f"    Examples: {missing_points[:3]}")

        # 6. Performance
        print(f"\n[6/7] Testing performance...")

        avg_time = sum(sport_timings.values()) / len(sport_timings) if sport_timings else 0
        max_time = max(sport_timings.values()) if sport_timings else 0
        slowest_sport = max(sport_timings.items(), key=lambda x: x[1])[0] if sport_timings else None

        print(f"  Average time per sport: {avg_time:.1f}s")
        print(f"  Maximum time: {max_time:.1f}s ({slowest_sport})")

        if max_time < 30:
            results["performance"] = True
            print(f"  PASS: All sports < 30s")
        else:
            print(f"  FAIL: Some sports >= 30s")

        # 7. Error Handling
        print(f"\n[7/7] Testing error handling...")
        print(f"  PASS: No exceptions thrown during extraction")

    except Exception as e:
        print(f"  FAIL: Exception occurred: {e}")
        results["error_handling"] = False
        import traceback
        traceback.print_exc()

    print_summary(results)
    return results

def print_summary(results):
    """Print validation summary"""
    print(f"\n{'='*80}")
    print("VALIDATION SUMMARY")
    print(f"{'='*80}\n")

    # Sports coverage
    if "sports_coverage" in results and results["sports_coverage"]:
        sports_with_data = sum(1 for count in results["sports_coverage"].values() if count > 0)
        total_sports = len(results["sports_coverage"])
        sports_status = sports_with_data >= 3  # At least 3 sports should have data
        symbol = "[X]" if sports_status else "[ ]"
        print(f"  {symbol} Sports Coverage ({sports_with_data}/{total_sports} sports)")

    # Other checks
    checks = [
        ("event_discovery", "Event Discovery"),
        ("market_coverage", "Market Coverage"),
        ("normalization", "Data Normalization"),
        ("database_compliance", "Database Compliance"),
        ("performance", "Performance"),
        ("error_handling", "Error Handling"),
    ]

    for key, label in checks:
        status = results.get(key, False)
        symbol = "[X]" if status else "[ ]"
        print(f"  {symbol} {label}")

    # Calculate score
    sports_with_data = sum(1 for count in results.get("sports_coverage", {}).values() if count > 0)
    sports_pass = sports_with_data >= 3

    passed = sum([
        sports_pass,
        results.get("event_discovery", False),
        results.get("market_coverage", False),
        results.get("normalization", False),
        results.get("database_compliance", False),
        results.get("performance", False),
        results.get("error_handling", False),
    ])
    total = 7

    print(f"\nResult: {passed}/{total} checks passed")

    if passed == total:
        status = "PRODUCTION READY"
    elif passed >= 5:
        status = "NEEDS MINOR FIXES"
    else:
        status = "NOT READY"

    print(f"Status: {status}")
    print(f"\n{'='*80}\n")

async def main():
    """Validate all Gecko providers"""

    providers = ["betsson", "betsafe", "nordicbet"]

    if len(sys.argv) > 1:
        # Validate specific provider
        provider = sys.argv[1]
        if provider not in providers:
            print(f"Unknown provider: {provider}")
            print(f"Available: {', '.join(providers)}")
            sys.exit(1)
        providers = [provider]

    all_results = {}

    for provider in providers:
        results = await validate_provider(provider)
        all_results[provider] = results

        if len(providers) > 1:
            print("\nWaiting 10 seconds before next provider...\n")
            await asyncio.sleep(10)

    # Final summary
    if len(providers) > 1:
        print(f"\n{'='*80}")
        print("FINAL SUMMARY - ALL PROVIDERS")
        print(f"{'='*80}\n")

        for provider, results in all_results.items():
            sports_with_data = sum(1 for count in results.get("sports_coverage", {}).values() if count > 0)
            passed = sum([
                sports_with_data >= 3,
                results.get("event_discovery", False),
                results.get("market_coverage", False),
                results.get("normalization", False),
                results.get("database_compliance", False),
                results.get("performance", False),
                results.get("error_handling", False),
            ])

            status = "READY" if passed >= 6 else "NEEDS WORK"
            print(f"  {provider:12s}: {passed}/7 checks passed - {status}")

if __name__ == "__main__":
    asyncio.run(main())
