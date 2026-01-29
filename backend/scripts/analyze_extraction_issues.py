"""
Analyze extraction runs to identify systematic issues.

Detects:
- Consistently failing providers
- Sports with low coverage
- Degrading performance trends
- Circuit breaker patterns
- Zero-event extractions

Usage:
    python scripts/analyze_extraction_issues.py
    python scripts/analyze_extraction_issues.py --run-id abc123
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.db.models import get_session, ExtractionRun, ProviderRunMetrics, SportRunMetrics
from sqlalchemy import desc, func
import argparse
from collections import defaultdict


def analyze_issues(run_id: str = None):
    """Analyze extraction issues."""
    session = get_session()

    # Get runs to analyze
    if run_id:
        runs = session.query(ExtractionRun).filter(ExtractionRun.id == run_id).all()
    else:
        runs = session.query(ExtractionRun).order_by(desc(ExtractionRun.start_time)).limit(10).all()

    if not runs:
        print("No runs found")
        return

    print(f"\n{'='*80}")
    print(f"ISSUE ANALYSIS - {len(runs)} Run(s)")
    print(f"{'='*80}\n")

    # Issue categories
    zero_event_providers = defaultdict(int)
    failing_sports = defaultdict(list)
    circuit_breaker_trips = []
    slow_providers = []

    for run in runs:
        for pm in run.provider_metrics:
            # Zero events
            if pm.events_processed == 0:
                zero_event_providers[pm.provider_id] += 1

            # Circuit breaker trips
            if pm.circuit_breaker_tripped:
                circuit_breaker_trips.append((run.start_time, pm.provider_id))

            # Slow providers (>60s)
            if pm.duration_seconds and pm.duration_seconds > 60:
                slow_providers.append((pm.provider_id, pm.duration_seconds))

            # Sport-level failures
            sport_metrics = session.query(SportRunMetrics).filter(
                SportRunMetrics.provider_run_id == pm.id,
                SportRunMetrics.success == False
            ).all()

            for sm in sport_metrics:
                failing_sports[f"{pm.provider_id}:{sm.sport}"].append(sm.error_type)

    # Report zero-event providers
    if zero_event_providers:
        print("\n[!!] PROVIDERS WITH ZERO EVENTS:")
        for pid, count in sorted(zero_event_providers.items(), key=lambda x: -x[1]):
            print(f"  * {pid}: {count}/{len(runs)} runs")

    # Report circuit breaker trips
    if circuit_breaker_trips:
        print("\n[!!] CIRCUIT BREAKER TRIPS:")
        for timestamp, pid in circuit_breaker_trips:
            print(f"  * {pid} at {timestamp.strftime('%Y-%m-%d %H:%M:%S')}")

    # Report slow providers
    if slow_providers:
        print("\n[!!] SLOW PROVIDERS (>60s):")
        slow_summary = defaultdict(list)
        for pid, duration in slow_providers:
            slow_summary[pid].append(duration)

        for pid, durations in sorted(slow_summary.items(), key=lambda x: -sum(x[1])/len(x[1])):
            avg = sum(durations) / len(durations)
            print(f"  * {pid}: avg {avg:.1f}s ({len(durations)} occurrences)")

    # Report sport-level failures
    if failing_sports:
        print("\n[XX] CONSISTENTLY FAILING SPORTS:")
        for key, errors in sorted(failing_sports.items(), key=lambda x: -len(x[1])):
            if len(errors) >= len(runs) * 0.5:  # Fails in 50%+ of runs
                pid, sport = key.split(':')
                print(f"  * {pid} / {sport}: {len(errors)}/{len(runs)} runs")
                print(f"      Error types: {', '.join(set(filter(None, errors)))}")

    # Summary
    if not any([zero_event_providers, circuit_breaker_trips, slow_providers, failing_sports]):
        print("\n[OK] No systematic issues detected!")

    print("\n" + "="*80)

    session.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument('--run-id', help='Analyze specific run ID')
    args = parser.parse_args()

    analyze_issues(args.run_id)
