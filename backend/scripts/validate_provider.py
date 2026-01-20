"""
Validate a Kambi provider for all Polymarket-relevant sports.
Usage: python scripts/validate_provider.py <provider_name>
"""

import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from src.extractors.kambi import get_extractor, KAMBI_PROVIDERS
from src.config.sports import get_kambi_sports

logging.basicConfig(level=logging.WARNING)

async def validate_provider(provider: str):
    """Test a Kambi extractor for all Polymarket sports."""
    if provider not in KAMBI_PROVIDERS:
        print(f"Unknown provider: {provider}")
        print(f"Available: {', '.join(KAMBI_PROVIDERS.keys())}")
        return
    
    extractor = get_extractor(provider)
    kambi_sports = sorted(get_kambi_sports())
    
    print(f"\n{'='*60}")
    print(f"{provider.upper()} PROVIDER VALIDATION")
    print(f"{'='*60}\n")
    
    results = {}
    for sport in kambi_sports:
        try:
            events = await extractor.extract(sport, max_groups=5)
            results[sport] = len(events)
            status = "OK" if events else "EMPTY"
            print(f"{status:6} {sport:20} -> {len(events):4} events")
        except Exception as e:
            results[sport] = -1
            print(f"ERROR  {sport:20} -> {e}")
    
    print(f"\n{'='*60}")
    working = [s for s, c in results.items() if c > 0]
    empty = [s for s, c in results.items() if c == 0]
    failed = [s for s, c in results.items() if c < 0]
    print(f"Working ({len(working)}): {', '.join(working)}")
    print(f"Empty   ({len(empty)}): {', '.join(empty)}")
    if failed:
        print(f"Failed  ({len(failed)}): {', '.join(failed)}")
    print(f"Total: {sum(c for c in results.values() if c > 0)} events")

if __name__ == "__main__":
    provider = sys.argv[1] if len(sys.argv) > 1 else "unibet"
    asyncio.run(validate_provider(provider))
