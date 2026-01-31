#!/usr/bin/env python3
"""
Provider Extraction Test Script
Runs each active provider individually and logs detailed extraction stats.
"""

import asyncio
import sys
import os
from datetime import datetime
from pathlib import Path
from collections import defaultdict

# Add backend to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.factory import ExtractorFactory
from src.config.loader import ConfigLoader

# Sports to test
SPORTS = ['football', 'basketball', 'ice_hockey', 'tennis', 'american_football']

async def test_provider(provider_id: str) -> dict:
    """Test a single provider and return extraction stats."""
    results = {
        'provider': provider_id,
        'sports': {},
        'total_events': 0,
        'total_markets': 0,
        'market_types': defaultdict(int),
        'errors': [],
        'extraction_time': 0.0
    }

    start_time = datetime.now()

    try:
        factory = ExtractorFactory.get_instance()
        extractor = factory.get_extractor(provider_id)

        for sport in SPORTS:
            sport_start = datetime.now()
            try:
                events = await extractor.extract(sport)
                sport_time = (datetime.now() - sport_start).total_seconds()

                if events:
                    sport_data = {
                        'events': len(events),
                        'time': sport_time,
                        'market_types': defaultdict(int),
                        'ou_count': 0,
                        'spread_count': 0,
                        '1x2_count': 0,
                        'moneyline_count': 0
                    }

                    for event in events:
                        for market in event.markets:
                            # Markets are dicts with 'type' key
                            mt = market.get('type', market.get('market_type', 'unknown'))
                            sport_data['market_types'][mt] += 1
                            results['market_types'][mt] += 1

                            if mt == 'over_under':
                                sport_data['ou_count'] += 1
                            elif mt == 'spread':
                                sport_data['spread_count'] += 1
                            elif mt == '1x2':
                                sport_data['1x2_count'] += 1
                            elif mt == 'moneyline':
                                sport_data['moneyline_count'] += 1

                    results['sports'][sport] = sport_data
                    results['total_events'] += len(events)
                    results['total_markets'] += sum(sport_data['market_types'].values())
                else:
                    results['sports'][sport] = {'events': 0, 'time': sport_time}

            except Exception as e:
                results['errors'].append(f"{sport}: {str(e)[:100]}")
                results['sports'][sport] = {'events': 0, 'error': str(e)[:100]}

    except Exception as e:
        results['errors'].append(f"Init: {str(e)[:100]}")

    results['extraction_time'] = (datetime.now() - start_time).total_seconds()
    return results


def format_results(results: dict) -> str:
    """Format results for logging."""
    lines = []
    lines.append(f"\n{'='*70}")
    lines.append(f"PROVIDER: {results['provider']}")
    lines.append(f"{'='*70}")
    lines.append(f"Total Events: {results['total_events']}")
    lines.append(f"Total Markets: {results['total_markets']}")
    lines.append(f"Extraction Time: {results['extraction_time']:.1f}s")

    if results['errors']:
        lines.append(f"\nErrors: {len(results['errors'])}")
        for err in results['errors'][:3]:
            lines.append(f"  - {err}")

    lines.append(f"\nMarket Types Summary:")
    for mt, count in sorted(results['market_types'].items()):
        lines.append(f"  {mt}: {count}")

    lines.append(f"\nBy Sport:")
    lines.append(f"{'Sport':<20} {'Events':>8} {'O/U':>8} {'Spread':>8} {'1x2':>8} {'ML':>8} {'Time':>8}")
    lines.append("-" * 70)

    for sport in SPORTS:
        if sport in results['sports']:
            s = results['sports'][sport]
            if 'error' not in s:
                lines.append(
                    f"{sport:<20} {s.get('events', 0):>8} "
                    f"{s.get('ou_count', 0):>8} {s.get('spread_count', 0):>8} "
                    f"{s.get('1x2_count', 0):>8} {s.get('moneyline_count', 0):>8} "
                    f"{s.get('time', 0):>7.1f}s"
                )
            else:
                lines.append(f"{sport:<20} ERROR: {s['error'][:40]}")
        else:
            lines.append(f"{sport:<20} {'N/A':>8}")

    return '\n'.join(lines)


async def main():
    # Active providers from config
    active_providers = [
        # Kambi API (test one representative)
        'unibet',
        # Browser-based
        'hajper',
        'comeon',
        # REST API (fast)
        'betinia',
        'pinnacle',
        # DOM scraper
        'fastbet',
    ]

    # If command line args, use those instead
    if len(sys.argv) > 1:
        active_providers = sys.argv[1:]

    print(f"Testing {len(active_providers)} providers...")
    print(f"Sports: {', '.join(SPORTS)}")

    all_results = []
    log_lines = []
    log_lines.append(f"Provider Extraction Test - {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    log_lines.append(f"Sports tested: {', '.join(SPORTS)}")
    log_lines.append("")

    for provider_id in active_providers:
        print(f"\n>>> Testing {provider_id}...")
        results = await test_provider(provider_id)
        all_results.append(results)

        formatted = format_results(results)
        print(formatted)
        log_lines.append(formatted)

    # Summary table
    log_lines.append(f"\n{'='*70}")
    log_lines.append("SUMMARY")
    log_lines.append(f"{'='*70}")
    log_lines.append(f"{'Provider':<15} {'Events':>8} {'O/U':>8} {'Spread':>8} {'1x2/ML':>8} {'Time':>8}")
    log_lines.append("-" * 70)

    for r in all_results:
        ou = r['market_types'].get('over_under', 0)
        spread = r['market_types'].get('spread', 0)
        ml = r['market_types'].get('1x2', 0) + r['market_types'].get('moneyline', 0)
        log_lines.append(
            f"{r['provider']:<15} {r['total_events']:>8} "
            f"{ou:>8} {spread:>8} {ml:>8} {r['extraction_time']:>7.1f}s"
        )

    # Save log
    log_path = Path(__file__).parent.parent / 'docs' / f'extraction_log_{datetime.now().strftime("%Y%m%d_%H%M")}.md'
    log_content = '\n'.join(log_lines)
    log_path.write_text(log_content)
    print(f"\n\nLog saved to: {log_path}")

    return all_results


if __name__ == '__main__':
    asyncio.run(main())
