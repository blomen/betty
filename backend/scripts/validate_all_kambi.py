"""
Validate ALL Kambi providers for Polymarket-relevant sports.
"""

import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from src.extractors.kambi import get_extractor, KAMBI_PROVIDERS
from src.config.sports import get_kambi_sports

logging.basicConfig(level=logging.WARNING)

async def validate_all():
    """Test all Kambi providers."""
    kambi_sports = sorted(get_kambi_sports())
    providers = list(KAMBI_PROVIDERS.keys())
    
    print(f"\n{'='*70}")
    print(f"KAMBI PROVIDERS FULL VALIDATION")
    print(f"Providers: {', '.join(providers)}")
    print(f"Sports: {', '.join(kambi_sports)}")
    print(f"{'='*70}\n")
    
    all_results = {}
    
    for provider in providers:
        print(f"\n--- {provider.upper()} ---")
        extractor = get_extractor(provider)
        results = {}
        
        for sport in kambi_sports:
            try:
                events = await extractor.extract(sport, max_groups=5)
                results[sport] = len(events)
                status = "OK" if events else "--"
                print(f"  {status:3} {sport:20} {len(events):4}")
            except Exception as e:
                results[sport] = -1
                print(f"  ERR {sport:20} {str(e)[:30]}")
        
        all_results[provider] = results
    
    # Summary table
    print(f"\n{'='*70}")
    print("SUMMARY TABLE")
    print(f"{'='*70}")
    
    # Header
    header = f"{'Sport':<20}" + "".join(f"{p[:6]:>8}" for p in providers)
    print(header)
    print("-" * len(header))
    
    # Rows per sport
    for sport in kambi_sports:
        row = f"{sport:<20}"
        for provider in providers:
            count = all_results[provider].get(sport, 0)
            if count > 0:
                row += f"{count:>8}"
            elif count == 0:
                row += f"{'--':>8}"
            else:
                row += f"{'ERR':>8}"
        print(row)
    
    # Provider totals
    print("-" * len(header))
    totals_row = f"{'TOTAL':<20}"
    for provider in providers:
        total = sum(c for c in all_results[provider].values() if c > 0)
        totals_row += f"{total:>8}"
    print(totals_row)
    
    # Working sports count
    working_row = f"{'Working Sports':<20}"
    for provider in providers:
        working = len([c for c in all_results[provider].values() if c > 0])
        working_row += f"{working:>8}"
    print(working_row)

if __name__ == "__main__":
    asyncio.run(validate_all())
