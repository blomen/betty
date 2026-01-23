#!/usr/bin/env python3
"""
Quick validation test after optimizations.
Verify data quality is maintained with reduced wait times.
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

from src.factory import ExtractorFactory

async def quick_validate(provider_name: str):
    """Quick validation check"""

    print(f"\n{'='*80}")
    print(f"QUICK VALIDATION: {provider_name.upper()}")
    print(f"{'='*80}\n")

    provider = ExtractorFactory.get_instance().get_extractor(provider_name)

    # Test one sport thoroughly
    sport = "football"
    events = await provider.extract(sport, limit=50)

    print(f"Extracted {len(events)} events for {sport}")

    if not events:
        print("FAIL: No events extracted")
        return False

    # Check data quality
    issues = []

    # Required fields
    for event in events[:10]:
        if not event.sport:
            issues.append("Missing sport")
        if not event.home_team:
            issues.append("Missing home_team")
        if not event.away_team:
            issues.append("Missing away_team")

    # Market coverage
    market_types = set()
    total_markets = 0
    markets_with_points = 0
    markets_without_points = 0

    for event in events:
        total_markets += len(event.markets)
        for market in event.markets:
            market_type = market.get('type', 'unknown')
            market_types.add(market_type)

            # Check points for over_under and spread
            if market_type in ['over_under', 'spread']:
                if 'point' in market:
                    markets_with_points += 1
                else:
                    markets_without_points += 1
                    issues.append(f"Missing point for {market_type}")

    has_moneyline = '1x2' in market_types or 'moneyline' in market_types
    has_totals = 'over_under' in market_types
    has_spreads = 'spread' in market_types

    # Results
    print(f"\nData Quality Checks:")
    print(f"  Events: {len(events)}")
    print(f"  Total markets: {total_markets}")
    print(f"  Market types: {len(market_types)}")
    print(f"  - 1x2/moneyline: {'YES' if has_moneyline else 'NO'}")
    print(f"  - over_under: {'YES' if has_totals else 'NO'}")
    print(f"  - spread: {'YES' if has_spreads else 'NO'}")

    if markets_with_points + markets_without_points > 0:
        print(f"\nPoint validation:")
        print(f"  Markets with points: {markets_with_points}")
        print(f"  Markets without points: {markets_without_points}")

    # Normalization check
    not_lowercase = sum(1 for e in events if not e.home_team.islower() or not e.away_team.islower())

    print(f"\nNormalization:")
    print(f"  Non-lowercase names: {not_lowercase}")

    # Sample events
    print(f"\nSample events:")
    for i, event in enumerate(events[:3], 1):
        print(f"  {i}. {event.home_team} vs {event.away_team}")
        print(f"     League: {event.league}")
        print(f"     Markets: {len(event.markets)}")

    # Pass/Fail
    print(f"\n{'='*80}")

    if issues:
        unique_issues = len(set(issues))
        print(f"VALIDATION: PARTIAL ({len(issues)} issues, {unique_issues} unique)")
        if unique_issues < 10:
            print(f"Issues: {set(list(issues)[:10])}")
    else:
        print(f"VALIDATION: PASS")

    success = (
        len(events) > 0 and
        has_moneyline and
        has_totals and
        not_lowercase == 0 and
        markets_without_points == 0
    )

    print(f"Status: {'SUCCESS' if success else 'NEEDS REVIEW'}")
    print(f"{'='*80}\n")

    return success

async def main():
    provider = sys.argv[1] if len(sys.argv) > 1 else "betsson"
    success = await quick_validate(provider)
    sys.exit(0 if success else 1)

if __name__ == "__main__":
    asyncio.run(main())
