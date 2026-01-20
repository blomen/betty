"""
Validate Unibet provider for all Polymarket-relevant sports.
"""

import asyncio
import logging
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from src.extractors.kambi import get_extractor
from src.config.sports import get_kambi_sports

logging.basicConfig(level=logging.WARNING)

async def validate_unibet():
    """Test Unibet extractor for all Polymarket Kambi sports."""
    extractor = get_extractor("unibet")
    
    # Get unique Kambi sports from Polymarket config
    kambi_sports = get_kambi_sports()
    print(f"\n{'='*60}")
    print(f"UNIBET PROVIDER VALIDATION")
    print(f"Testing {len(kambi_sports)} Kambi sports: {', '.join(sorted(kambi_sports))}")
    print(f"{'='*60}\n")
    
    results = {}
    
    for sport in sorted(kambi_sports):
        try:
            # Small max_groups for quick test
            events = await extractor.extract(sport, max_groups=5)
            results[sport] = len(events)
            status = "✓" if events else "✗"
            print(f"{status} {sport:20} -> {len(events):4} events")
        except Exception as e:
            results[sport] = -1
            print(f"✗ {sport:20} -> ERROR: {e}")
    
    # Summary
    print(f"\n{'='*60}")
    print("SUMMARY")
    print(f"{'='*60}")
    working = [s for s, c in results.items() if c > 0]
    empty = [s for s, c in results.items() if c == 0]
    failed = [s for s, c in results.items() if c < 0]
    
    print(f"Working ({len(working)}): {', '.join(working)}")
    print(f"Empty   ({len(empty)}): {', '.join(empty)}")
    print(f"Failed  ({len(failed)}): {', '.join(failed)}")
    
    total = sum(c for c in results.values() if c > 0)
    print(f"\nTotal events found: {total}")

if __name__ == "__main__":
    asyncio.run(validate_unibet())
