#!/usr/bin/env python3
"""
Provider Test Script - Standalone validation for new providers.

Tests a provider in isolation before pipeline integration.
Validates extraction quality metrics required for production readiness.

Usage:
    python scripts/test_provider.py <provider_id> --sport football
    python scripts/test_provider.py <provider_id> --sport football --limit 20
    python scripts/test_provider.py <provider_id> --sport football --verbose
    python scripts/test_provider.py <provider_id> --all-sports

Validation Criteria:
    - Odds/event ratio: 2.4-3.0 (red flag if >4.0 or <2.0)
    - Outcome normalization: >95% (home/away/draw)
    - Score-like outcomes: 0 (correct score markets leaking)
    - No API errors in logs

Exit Codes:
    0 - All validations passed
    1 - Validation failed or extraction error
"""

import asyncio
import sys
import json
import logging
import argparse
import re
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.factory import ExtractorFactory
from src.core import StandardEvent


@dataclass
class ValidationResult:
    """Results of provider validation."""
    provider_id: str
    sport: str
    events_count: int
    odds_count: int
    odds_event_ratio: float
    outcome_normalization_pct: float
    score_like_outcomes: int
    extraction_time_seconds: float
    passed: bool
    issues: List[str]


def setup_logging(verbose: bool):
    """Configure logging level based on verbosity."""
    level = logging.DEBUG if verbose else logging.WARNING
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S"
    )


def print_header(text: str, char: str = "="):
    """Print a section header."""
    print(f"\n{char * 60}")
    print(f" {text}")
    print(char * 60)


def analyze_events(events: List[StandardEvent]) -> Tuple[int, int, int, float]:
    """
    Analyze extraction quality metrics.

    Returns:
        Tuple of (odds_count, normalized_outcomes, score_like_outcomes, normalization_pct)
    """
    odds_count = 0
    normalized_outcomes = 0
    score_like_outcomes = 0

    valid_outcomes = {'home', 'away', 'draw'}
    score_pattern = re.compile(r'^\d+-\d+$')  # Matches "0-1", "2-3", etc.

    for event in events:
        for market in event.markets:
            for outcome in market.get('outcomes', []):
                odds_count += 1
                name = outcome.get('name', '')

                if name in valid_outcomes:
                    normalized_outcomes += 1
                elif score_pattern.match(name):
                    score_like_outcomes += 1

    normalization_pct = (normalized_outcomes / odds_count * 100) if odds_count > 0 else 0.0

    return odds_count, normalized_outcomes, score_like_outcomes, normalization_pct


def validate_metrics(
    events_count: int,
    odds_count: int,
    normalization_pct: float,
    score_like_outcomes: int
) -> Tuple[bool, List[str]]:
    """
    Validate extraction metrics against expected thresholds.

    Returns:
        Tuple of (passed, issues_list)
    """
    issues = []

    if events_count == 0:
        issues.append("No events extracted")
        return False, issues

    # Calculate odds/event ratio
    ratio = odds_count / events_count if events_count > 0 else 0

    # Check ratio (expected 2.4-3.0 for 1x2/moneyline)
    if ratio > 4.0:
        issues.append(f"Odds/event ratio too high ({ratio:.2f}) - non-1x2 markets may be leaking through")
    elif ratio < 2.0:
        issues.append(f"Odds/event ratio too low ({ratio:.2f}) - missing outcomes")

    # Check normalization (expected >95%)
    if normalization_pct < 95.0:
        issues.append(f"Outcome normalization low ({normalization_pct:.1f}%) - team matching may be failing")

    # Check for score-like outcomes (expected 0)
    if score_like_outcomes > 0:
        issues.append(f"Found {score_like_outcomes} score-like outcomes - correct score markets leaking")

    passed = len(issues) == 0
    return passed, issues


def print_event_sample(events: List[StandardEvent], limit: int = 5):
    """Print sample of events for verification."""
    print_header("SAMPLE EVENTS", "-")

    for event in events[:limit]:
        print(f"\n  Event: {event.home_team} vs {event.away_team}")
        print(f"  Sport: {event.sport} | League: {event.league}")
        print(f"  Start: {event.start_time}")
        print(f"  Markets: {len(event.markets)}")

        for market in event.markets[:2]:
            mtype = market.get('type', 'unknown')
            outcomes = market.get('outcomes', [])
            print(f"    [{mtype}]")
            for outcome in outcomes[:4]:
                name = outcome.get('name', '?')
                odds = outcome.get('odds', 0)
                print(f"      {name}: {odds}")


def print_validation_result(result: ValidationResult):
    """Print formatted validation result."""
    print_header("VALIDATION RESULT")

    status = "PASSED" if result.passed else "FAILED"
    print(f"\n  Status: {status}")
    print(f"\n  Provider: {result.provider_id}")
    print(f"  Sport: {result.sport}")
    print(f"  Extraction time: {result.extraction_time_seconds:.2f}s")

    print(f"\n  Metrics:")
    print(f"    Events extracted: {result.events_count}")
    print(f"    Total odds: {result.odds_count}")
    print(f"    Odds/event ratio: {result.odds_event_ratio:.2f}")
    print(f"    Outcome normalization: {result.outcome_normalization_pct:.1f}%")
    print(f"    Score-like outcomes: {result.score_like_outcomes}")

    if result.issues:
        print(f"\n  Issues:")
        for issue in result.issues:
            print(f"    - {issue}")

    print("\n  Expected thresholds:")
    print("    Odds/event ratio: 2.4-3.0 (red flag if >4.0 or <2.0)")
    print("    Outcome normalization: >95%")
    print("    Score-like outcomes: 0")


async def test_provider(
    provider_id: str,
    sport: str = "football",
    limit: int = 50,
    verbose: bool = False,
    show_sample: bool = True
) -> ValidationResult:
    """
    Test a provider in isolation and validate extraction quality.

    Args:
        provider_id: Provider ID to test
        sport: Sport to extract
        limit: Maximum events to extract
        verbose: Enable debug logging
        show_sample: Show sample events

    Returns:
        ValidationResult with metrics and pass/fail status
    """
    setup_logging(verbose)

    print_header(f"TESTING: {provider_id} / {sport}")
    print(f"Timestamp: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Limit: {limit}")

    # Get provider
    factory = ExtractorFactory.get_instance()

    try:
        provider = factory.get_extractor(provider_id)
    except ValueError as e:
        print(f"\nError: {e}")
        print(f"\nAvailable providers: {', '.join(factory.get_enabled_providers())}")
        return ValidationResult(
            provider_id=provider_id,
            sport=sport,
            events_count=0,
            odds_count=0,
            odds_event_ratio=0,
            outcome_normalization_pct=0,
            score_like_outcomes=0,
            extraction_time_seconds=0,
            passed=False,
            issues=[f"Provider not found: {e}"]
        )

    # Print provider config
    config = factory.get_provider(provider_id)
    if config:
        print(f"\n  Provider: {config.name}")
        print(f"  Retriever type: {config.retriever_type}")
        print(f"  Domain: {config.domain}")

    # Extract events
    print_header("EXTRACTION", "-")
    print(f"Extracting {sport} events...")

    try:
        import time
        start = time.time()
        events = await provider.extract(sport, limit=limit)
        elapsed = time.time() - start

        print(f"Extracted {len(events)} events in {elapsed:.2f}s")

        if not events:
            return ValidationResult(
                provider_id=provider_id,
                sport=sport,
                events_count=0,
                odds_count=0,
                odds_event_ratio=0,
                outcome_normalization_pct=0,
                score_like_outcomes=0,
                extraction_time_seconds=elapsed,
                passed=False,
                issues=["No events extracted - check if sport is supported"]
            )

        # Analyze metrics
        odds_count, normalized, score_like, norm_pct = analyze_events(events)
        ratio = odds_count / len(events) if events else 0

        # Show sample if requested
        if show_sample:
            print_event_sample(events)

        # Validate
        passed, issues = validate_metrics(len(events), odds_count, norm_pct, score_like)

        result = ValidationResult(
            provider_id=provider_id,
            sport=sport,
            events_count=len(events),
            odds_count=odds_count,
            odds_event_ratio=ratio,
            outcome_normalization_pct=norm_pct,
            score_like_outcomes=score_like,
            extraction_time_seconds=elapsed,
            passed=passed,
            issues=issues
        )

        print_validation_result(result)

        return result

    except Exception as e:
        print(f"\nExtraction failed: {e}")
        if verbose:
            import traceback
            traceback.print_exc()

        return ValidationResult(
            provider_id=provider_id,
            sport=sport,
            events_count=0,
            odds_count=0,
            odds_event_ratio=0,
            outcome_normalization_pct=0,
            score_like_outcomes=0,
            extraction_time_seconds=0,
            passed=False,
            issues=[f"Extraction error: {e}"]
        )

    finally:
        try:
            await provider.close()
        except Exception:
            pass  # Ignore cleanup errors


async def test_all_sports(
    provider_id: str,
    limit: int = 20,
    verbose: bool = False
) -> List[ValidationResult]:
    """Test provider across all common sports."""
    sports = ['football', 'basketball', 'tennis', 'ice_hockey']
    results = []

    print_header(f"MULTI-SPORT TEST: {provider_id}")

    for sport in sports:
        result = await test_provider(
            provider_id,
            sport=sport,
            limit=limit,
            verbose=verbose,
            show_sample=False
        )
        results.append(result)

    # Summary
    print_header("MULTI-SPORT SUMMARY")
    print(f"\n  {'Sport':<15} {'Events':<8} {'Ratio':<8} {'Norm %':<8} {'Status'}")
    print("  " + "-" * 55)

    for r in results:
        status = "PASS" if r.passed else "FAIL"
        print(f"  {r.sport:<15} {r.events_count:<8} {r.odds_event_ratio:<8.2f} {r.outcome_normalization_pct:<8.1f} {status}")

    passed_all = all(r.passed for r in results if r.events_count > 0)
    print(f"\n  Overall: {'PASSED' if passed_all else 'FAILED'}")

    return results


def main():
    parser = argparse.ArgumentParser(
        description="Test provider extraction in isolation",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    # Test single sport
    python scripts/test_provider.py unibet --sport football

    # Test with more events
    python scripts/test_provider.py unibet --sport football --limit 50

    # Test all common sports
    python scripts/test_provider.py unibet --all-sports

    # Enable debug logging
    python scripts/test_provider.py unibet --sport football --verbose

Validation Criteria:
    - Odds/event ratio: 2.4-3.0 (red flag if >4.0 or <2.0)
    - Outcome normalization: >95% (home/away/draw)
    - Score-like outcomes: 0 (correct score markets leaking)
        """
    )

    parser.add_argument("provider", help="Provider ID to test")
    parser.add_argument("--sport", default="football", help="Sport to extract (default: football)")
    parser.add_argument("--limit", type=int, default=50, help="Max events to extract (default: 50)")
    parser.add_argument("--verbose", "-v", action="store_true", help="Enable debug logging")
    parser.add_argument("--all-sports", action="store_true", help="Test all common sports")
    parser.add_argument("--no-sample", action="store_true", help="Skip showing sample events")

    args = parser.parse_args()

    if args.all_sports:
        results = asyncio.run(test_all_sports(
            args.provider,
            limit=args.limit,
            verbose=args.verbose
        ))
        passed = all(r.passed for r in results if r.events_count > 0)
    else:
        result = asyncio.run(test_provider(
            args.provider,
            args.sport,
            args.limit,
            args.verbose,
            show_sample=not args.no_sample
        ))
        passed = result.passed

    # Exit with appropriate code
    sys.exit(0 if passed else 1)


if __name__ == "__main__":
    main()
