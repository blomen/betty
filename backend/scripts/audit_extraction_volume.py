#!/usr/bin/env python3
"""
Extraction Volume Audit Script

Compares extracted event counts against manually observed volumes.
Use this after provider changes to catch data loss.

Workflow:
1. Browse provider site manually, count events for a sport
2. Run this script with --expected flag
3. If mismatch > 10%, investigate pagination/mappings

Usage:
    python scripts/audit_extraction_volume.py polymarket --expected 2800
    python scripts/audit_extraction_volume.py pinnacle --sport ice_hockey --expected 150
    python scripts/audit_extraction_volume.py kambi --sport football --expected 900

Options:
    --expected N     Expected event count from manual site inspection
    --sport SPORT    Sport to audit (default: football)
    --threshold PCT  Acceptable variance percentage (default: 10)
    --verbose        Show detailed extraction logs
"""

import argparse
import asyncio
import sys
import time
from pathlib import Path

# Add backend to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.factory import ExtractorFactory


async def audit_provider(
    provider_name: str,
    sport: str,
    expected_count: int | None,
    threshold_pct: float,
    verbose: bool
) -> dict:
    """Run extraction audit for a provider."""

    print(f"\n{'='*60}")
    print(f"EXTRACTION VOLUME AUDIT")
    print(f"{'='*60}")
    print(f"Provider: {provider_name}")
    print(f"Sport: {sport}")
    print(f"Expected: {expected_count or 'Not specified'}")
    print(f"Threshold: {threshold_pct}%")
    print(f"{'='*60}\n")

    results = {
        "provider": provider_name,
        "sport": sport,
        "expected": expected_count,
        "actual": 0,
        "variance_pct": None,
        "status": "UNKNOWN",
        "extraction_time": 0,
        "warnings": [],
    }

    try:
        # Get provider
        print(f"[1/3] Loading provider...")
        provider = ExtractorFactory.get_provider(provider_name)
        print(f"      Provider type: {type(provider).__name__}")

        # Run extraction
        print(f"\n[2/3] Extracting {sport} events...")
        start_time = time.time()
        events = await provider.extract(sport)
        elapsed = time.time() - start_time
        results["extraction_time"] = elapsed
        results["actual"] = len(events)

        print(f"      Extracted: {len(events)} events")
        print(f"      Time: {elapsed:.1f}s")

        # Check for pagination indicators
        if len(events) in [100, 200, 500, 1000]:
            results["warnings"].append(
                f"Suspicious round number ({len(events)}) - possible pagination limit"
            )

        # Analyze results
        print(f"\n[3/3] Analyzing results...")

        if expected_count:
            variance = ((results["actual"] - expected_count) / expected_count) * 100
            results["variance_pct"] = variance

            if abs(variance) <= threshold_pct:
                results["status"] = "PASS"
                print(f"      Variance: {variance:+.1f}% (within {threshold_pct}% threshold)")
            else:
                results["status"] = "FAIL"
                print(f"      Variance: {variance:+.1f}% (EXCEEDS {threshold_pct}% threshold)")

                # Provide diagnostic hints
                if results["actual"] < expected_count:
                    if results["actual"] in [100, 200, 500, 1000]:
                        results["warnings"].append(
                            "Missing pagination likely - check API limit/offset params"
                        )
                    else:
                        results["warnings"].append(
                            "Check: category mappings, rate limits, filter params"
                        )
                else:
                    results["warnings"].append(
                        "Extracting more than expected - verify manual count"
                    )
        else:
            results["status"] = "NEEDS_BASELINE"
            print("      No expected count provided - record this as baseline")

        # Show sample events if verbose
        if verbose and events:
            print(f"\n      Sample events ({min(5, len(events))}):")
            for event in events[:5]:
                print(f"        - {event.home_team} vs {event.away_team}")
                if hasattr(event, 'league') and event.league:
                    print(f"          League: {event.league}")

        # Show warnings
        if results["warnings"]:
            print(f"\n      Warnings:")
            for warning in results["warnings"]:
                print(f"        - {warning}")

    except Exception as e:
        results["status"] = "ERROR"
        results["warnings"].append(f"Extraction failed: {e}")
        print(f"      ERROR: {e}")

    # Summary
    print(f"\n{'='*60}")
    print(f"AUDIT RESULT: {results['status']}")
    print(f"{'='*60}")
    print(f"Expected: {expected_count or 'N/A'}")
    print(f"Actual:   {results['actual']}")
    if results["variance_pct"] is not None:
        print(f"Variance: {results['variance_pct']:+.1f}%")
    print(f"Time:     {results['extraction_time']:.1f}s")
    print(f"{'='*60}\n")

    return results


def main():
    parser = argparse.ArgumentParser(
        description="Audit extraction volume against expected counts"
    )
    parser.add_argument(
        "provider",
        help="Provider name (e.g., polymarket, pinnacle, kambi)"
    )
    parser.add_argument(
        "--expected", "-e",
        type=int,
        help="Expected event count from manual site inspection"
    )
    parser.add_argument(
        "--sport", "-s",
        default="football",
        help="Sport to audit (default: football)"
    )
    parser.add_argument(
        "--threshold", "-t",
        type=float,
        default=10.0,
        help="Acceptable variance percentage (default: 10)"
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Show detailed extraction logs"
    )

    args = parser.parse_args()

    results = asyncio.run(audit_provider(
        provider_name=args.provider,
        sport=args.sport,
        expected_count=args.expected,
        threshold_pct=args.threshold,
        verbose=args.verbose
    ))

    # Exit with appropriate code
    if results["status"] == "PASS":
        sys.exit(0)
    elif results["status"] == "NEEDS_BASELINE":
        sys.exit(0)  # Not a failure, just needs baseline
    else:
        sys.exit(1)


if __name__ == "__main__":
    main()
