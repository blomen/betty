#!/usr/bin/env python3
"""
Comprehensive Provider Validation Script

Runs multi-sport validation across all configured sports, generates detailed
reports showing sports coverage, market types, data quality, and performance.

Usage:
    python scripts/validate_provider_full.py unibet
    python scripts/validate_provider_full.py unibet --json
    python scripts/validate_provider_full.py unibet --sports football,basketball
"""

import asyncio
import sys
import json
import time
import argparse
from datetime import datetime
from pathlib import Path
from dataclasses import dataclass, field, asdict
from typing import List, Dict, Optional, Any
from collections import defaultdict

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.factory import ExtractorFactory
from src.config.loader import ConfigLoader


@dataclass
class SportResult:
    """Result of validating a single sport."""
    sport: str
    events: int
    time_seconds: float
    status: str  # "OK", "NO_EVENTS", "ERROR", "TIMEOUT"
    error: Optional[str] = None
    markets_count: int = 0


@dataclass
class MarketStats:
    """Statistics for a market type."""
    market_type: str
    count: int
    events_with_market: int
    coverage_pct: float


@dataclass
class QualityChecks:
    """Data quality check results."""
    names_normalized: bool = False
    odds_valid: bool = False
    points_present: bool = False
    start_times_present: bool = False
    no_duplicates: bool = False
    events_missing_start_time: int = 0


@dataclass
class ProviderReport:
    """Complete provider validation report."""
    provider_id: str
    timestamp: str
    sports: List[SportResult] = field(default_factory=list)
    markets: List[MarketStats] = field(default_factory=list)
    quality_checks: QualityChecks = field(default_factory=QualityChecks)
    total_events: int = 0
    total_markets: int = 0
    total_time_seconds: float = 0
    status: str = "UNKNOWN"  # "PRODUCTION", "STAGING", "BLOCKED"
    checks_passed: int = 0
    checks_total: int = 0


# Sports to test (from sports.json keys)
ALL_SPORTS = [
    "football",
    "basketball",
    "ice_hockey",
    "american_football",
    "baseball",
    "tennis",
    "cricket",
    "rugby",
    "esports",
    "mma",
    "boxing",
    "motorsports"
]


async def test_sport(provider, sport: str, timeout: float = 60.0) -> SportResult:
    """Test extraction for a single sport."""
    start = time.time()

    try:
        # Use asyncio.wait_for for timeout
        events = await asyncio.wait_for(
            provider.extract(sport, limit=500),
            timeout=timeout
        )
        elapsed = time.time() - start

        if not events:
            return SportResult(
                sport=sport,
                events=0,
                time_seconds=elapsed,
                status="NO_EVENTS"
            )

        # Count markets
        markets_count = sum(len(e.markets) for e in events)

        return SportResult(
            sport=sport,
            events=len(events),
            time_seconds=elapsed,
            status="OK",
            markets_count=markets_count
        )

    except asyncio.TimeoutError:
        elapsed = time.time() - start
        return SportResult(
            sport=sport,
            events=0,
            time_seconds=elapsed,
            status="TIMEOUT",
            error=f"Timed out after {timeout}s"
        )
    except Exception as e:
        elapsed = time.time() - start
        return SportResult(
            sport=sport,
            events=0,
            time_seconds=elapsed,
            status="ERROR",
            error=str(e)
        )


def analyze_markets(events: list) -> List[MarketStats]:
    """Analyze market type distribution across events."""
    counts = defaultdict(lambda: {"count": 0, "events": set()})

    for event in events:
        event_id = event.id
        for market in event.markets:
            mtype = market.get("type", "unknown")
            # Normalize market type for analysis
            mtype_normalized = normalize_market_type(mtype)
            counts[mtype_normalized]["count"] += 1
            counts[mtype_normalized]["events"].add(event_id)

    total_events = len(events)

    # Priority order for display
    priority = ["1x2", "moneyline", "over_under", "spread"]
    stats = []

    # Add priority markets first
    for mtype in priority:
        if mtype in counts:
            data = counts[mtype]
            coverage = (len(data["events"]) / total_events * 100) if total_events > 0 else 0
            stats.append(MarketStats(
                market_type=mtype,
                count=data["count"],
                events_with_market=len(data["events"]),
                coverage_pct=round(coverage, 1)
            ))
            del counts[mtype]

    # Add remaining markets
    for mtype, data in sorted(counts.items()):
        coverage = (len(data["events"]) / total_events * 100) if total_events > 0 else 0
        stats.append(MarketStats(
            market_type=mtype,
            count=data["count"],
            events_with_market=len(data["events"]),
            coverage_pct=round(coverage, 1)
        ))

    return stats


def normalize_market_type(mtype: str) -> str:
    """Normalize market type string for consistent analysis."""
    mtype_lower = mtype.lower()

    # Map common variations
    if "1x2" in mtype_lower or "match result" in mtype_lower or "full time" in mtype_lower:
        return "1x2"
    if "moneyline" in mtype_lower or "money line" in mtype_lower or "to win" in mtype_lower:
        return "moneyline"
    if "over" in mtype_lower and "under" in mtype_lower:
        return "over_under"
    if "total" in mtype_lower:
        return "over_under"
    if "spread" in mtype_lower or "handicap" in mtype_lower:
        return "spread"
    if "point spread" in mtype_lower:
        return "spread"

    # Return original if no match (truncate long names)
    if len(mtype) > 20:
        return mtype[:17] + "..."
    return mtype


def check_data_quality(events: list) -> QualityChecks:
    """Run data quality checks on events."""
    if not events:
        return QualityChecks()

    checks = QualityChecks()

    # Check team name normalization (should be lowercase)
    checks.names_normalized = all(
        e.home_team.islower() and e.away_team.islower()
        for e in events
        if e.home_team and e.away_team
    )

    # Check odds validity (all > 1.0)
    all_odds = []
    for e in events:
        for m in e.markets:
            for o in m.get("outcomes", []):
                odds = o.get("odds", 0)
                if odds:
                    all_odds.append(odds)

    checks.odds_valid = all(o > 1.0 for o in all_odds) if all_odds else True

    # Check point values present for spread/totals
    needs_points = 0
    has_points = 0
    for e in events:
        for m in e.markets:
            mtype = m.get("type", "").lower()
            if "spread" in mtype or "handicap" in mtype or "total" in mtype or "over" in mtype:
                for o in m.get("outcomes", []):
                    needs_points += 1
                    if o.get("point") is not None:
                        has_points += 1

    checks.points_present = (has_points >= needs_points * 0.8) if needs_points > 0 else True

    # Check start times
    events_with_time = sum(1 for e in events if e.start_time)
    checks.events_missing_start_time = len(events) - events_with_time
    checks.start_times_present = events_with_time >= len(events) * 0.9

    # Check for duplicates
    event_ids = [e.id for e in events]
    checks.no_duplicates = len(event_ids) == len(set(event_ids))

    return checks


def generate_console_report(report: ProviderReport) -> str:
    """Generate human-readable console report."""
    lines = []
    width = 60

    lines.append("=" * width)
    lines.append(f"PROVIDER VALIDATION REPORT: {report.provider_id}")
    lines.append(f"Date: {report.timestamp}")
    lines.append("=" * width)
    lines.append("")

    # Sports coverage
    sports_ok = sum(1 for s in report.sports if s.status == "OK")
    total_sports = len(report.sports)
    lines.append(f"SPORTS COVERAGE ({sports_ok}/{total_sports} with events)")
    lines.append("-" * width)
    lines.append(f"{'Sport':<20} | {'Events':>7} | {'Time':>7} | Status")
    lines.append("-" * width)

    for sport in report.sports:
        status_symbol = "[OK]" if sport.status == "OK" else f"[{sport.status}]"
        lines.append(
            f"{sport.sport:<20} | {sport.events:>7} | {sport.time_seconds:>6.1f}s | {status_symbol}"
        )

    lines.append("-" * width)
    lines.append(
        f"{'TOTAL':<20} | {report.total_events:>7} | {report.total_time_seconds:>6.1f}s |"
    )
    lines.append("")

    # Market coverage
    if report.markets:
        lines.append("MARKET COVERAGE")
        lines.append("-" * width)
        lines.append(f"{'Market Type':<15} | {'Count':>7} | {'Events':>7} | {'Coverage':>8}")
        lines.append("-" * width)

        total_markets = sum(m.count for m in report.markets)
        for market in report.markets[:10]:  # Show top 10
            lines.append(
                f"{market.market_type:<15} | {market.count:>7} | "
                f"{market.events_with_market:>7} | {market.coverage_pct:>7.1f}%"
            )

        if len(report.markets) > 10:
            lines.append(f"  ... and {len(report.markets) - 10} more market types")

        lines.append("-" * width)
        lines.append(f"{'TOTAL MARKETS':<15} | {total_markets:>7} |")
        lines.append("")

    # Data quality
    lines.append("DATA QUALITY")
    lines.append("-" * width)

    qc = report.quality_checks
    checks = [
        ("names_normalized", "All team names normalized (lowercase)", qc.names_normalized),
        ("odds_valid", "All odds > 1.0 (valid decimal)", qc.odds_valid),
        ("points_present", "Point values present for spreads/totals", qc.points_present),
        ("start_times_present", "Start times present", qc.start_times_present),
        ("no_duplicates", "No duplicate events", qc.no_duplicates),
    ]

    for key, desc, passed in checks:
        symbol = "[X]" if passed else "[ ]"
        extra = ""
        if key == "start_times_present" and qc.events_missing_start_time > 0:
            extra = f" ({qc.events_missing_start_time} missing)"
        lines.append(f"{symbol} {desc}{extra}")

    lines.append("")

    # Performance
    lines.append("PERFORMANCE")
    lines.append("-" * width)
    lines.append(f"Total extraction time: {report.total_time_seconds:.1f}s")

    if report.sports:
        ok_sports = [s for s in report.sports if s.status == "OK" and s.time_seconds > 0]
        if ok_sports:
            avg_time = sum(s.time_seconds for s in ok_sports) / len(ok_sports)
            slowest = max(ok_sports, key=lambda s: s.time_seconds)
            lines.append(f"Average per sport: {avg_time:.2f}s")
            lines.append(f"Slowest sport: {slowest.sport} ({slowest.time_seconds:.1f}s)")

    lines.append("")

    # Final status
    lines.append(f"STATUS: {report.status} ({report.checks_passed}/{report.checks_total} checks passed)")
    lines.append("=" * width)

    return "\n".join(lines)


def determine_status(report: ProviderReport) -> tuple[str, int, int]:
    """Determine overall provider status based on checks."""
    checks = []

    # Sports with events (at least 3 required for production)
    sports_ok = sum(1 for s in report.sports if s.status == "OK")
    checks.append(sports_ok >= 3)

    # Has core markets (1x2 or moneyline, and over_under)
    market_types = {m.market_type for m in report.markets}
    has_moneyline = "1x2" in market_types or "moneyline" in market_types
    has_totals = "over_under" in market_types
    checks.append(has_moneyline)
    checks.append(has_totals)

    # Data quality checks
    qc = report.quality_checks
    checks.append(qc.names_normalized)
    checks.append(qc.odds_valid)
    checks.append(qc.points_present)
    checks.append(qc.start_times_present)
    checks.append(qc.no_duplicates)

    # Performance (total < 2 minutes)
    checks.append(report.total_time_seconds < 120)

    # No errors or timeouts
    errors = sum(1 for s in report.sports if s.status in ("ERROR", "TIMEOUT"))
    checks.append(errors == 0)

    passed = sum(checks)
    total = len(checks)

    if passed == total:
        status = "PRODUCTION READY"
    elif passed >= total - 2:
        status = "STAGING"
    else:
        status = "NEEDS WORK"

    return status, passed, total


async def validate_provider(
    provider_id: str,
    sports: Optional[List[str]] = None,
    save_json: bool = False
) -> ProviderReport:
    """Run full validation on a provider."""

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    report = ProviderReport(
        provider_id=provider_id,
        timestamp=timestamp
    )

    # Get provider
    factory = ExtractorFactory.get_instance()

    try:
        provider = factory.get_extractor(provider_id)
    except ValueError as e:
        print(f"Error: {e}")
        print(f"\nAvailable providers: {', '.join(factory.get_enabled_providers())}")
        return report

    # Determine which sports to test
    sports_to_test = sports if sports else ALL_SPORTS

    print(f"\nValidating {provider_id} across {len(sports_to_test)} sports...\n")

    # Test each sport
    all_events = []

    for sport in sports_to_test:
        print(f"  Testing {sport}...", end=" ", flush=True)
        result = await test_sport(provider, sport)
        report.sports.append(result)

        # Print inline result
        if result.status == "OK":
            print(f"{result.events} events ({result.time_seconds:.1f}s)")
            # Collect events for market analysis
            try:
                events = await provider.extract(sport, limit=500)
                all_events.extend(events)
            except:
                pass
        else:
            print(f"{result.status}" + (f" - {result.error}" if result.error else ""))

    # Aggregate results
    report.total_events = sum(s.events for s in report.sports)
    report.total_time_seconds = sum(s.time_seconds for s in report.sports)

    # Analyze markets
    if all_events:
        report.markets = analyze_markets(all_events)
        report.total_markets = sum(m.count for m in report.markets)
        report.quality_checks = check_data_quality(all_events)

    # Determine status
    report.status, report.checks_passed, report.checks_total = determine_status(report)

    # Close provider
    try:
        await provider.close()
    except:
        pass

    # Print console report
    print("\n" + generate_console_report(report))

    # Save JSON report if requested
    if save_json:
        reports_dir = Path(__file__).parent.parent / "reports"
        reports_dir.mkdir(exist_ok=True)

        timestamp_file = datetime.now().strftime("%Y%m%d_%H%M%S")
        json_path = reports_dir / f"{provider_id}_{timestamp_file}.json"

        # Convert to dict for JSON
        report_dict = {
            "provider_id": report.provider_id,
            "timestamp": report.timestamp,
            "sports": [asdict(s) for s in report.sports],
            "markets": [asdict(m) for m in report.markets],
            "quality_checks": asdict(report.quality_checks),
            "total_events": report.total_events,
            "total_markets": report.total_markets,
            "total_time_seconds": report.total_time_seconds,
            "status": report.status,
            "checks_passed": report.checks_passed,
            "checks_total": report.checks_total
        }

        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(report_dict, f, indent=2)

        print(f"\nReport saved to: {json_path}")

    return report


def main():
    parser = argparse.ArgumentParser(
        description="Comprehensive provider validation",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python scripts/validate_provider_full.py unibet
  python scripts/validate_provider_full.py unibet --json
  python scripts/validate_provider_full.py unibet --sports football,basketball
  python scripts/validate_provider_full.py --list
        """
    )

    parser.add_argument("provider", nargs="?", help="Provider ID to validate")
    parser.add_argument("--json", action="store_true", help="Save JSON report to backend/reports/")
    parser.add_argument("--sports", help="Comma-separated list of sports to test")
    parser.add_argument("--list", action="store_true", help="List available providers")

    args = parser.parse_args()

    if args.list:
        factory = ExtractorFactory.get_instance()
        providers = factory.get_enabled_providers()
        print(f"\nEnabled providers ({len(providers)}):")
        for p in sorted(providers):
            print(f"  - {p}")
        return

    if not args.provider:
        parser.print_help()
        return

    sports = args.sports.split(",") if args.sports else None

    asyncio.run(validate_provider(args.provider, sports, args.json))


if __name__ == "__main__":
    main()
