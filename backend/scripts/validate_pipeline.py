#!/usr/bin/env python3
"""
Provider Pipeline Validation Script

Validates extraction metrics against baseline thresholds.
Run after extraction to verify data quality.

Usage:
    python scripts/validate_pipeline.py              # Validate all providers
    python scripts/validate_pipeline.py --provider X # Validate specific provider
    python scripts/validate_pipeline.py --capture    # Capture current as baseline
"""

import argparse
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

# Paths
SCRIPT_DIR = Path(__file__).parent
BASELINE_PATH = SCRIPT_DIR / "baseline_metrics.json"
DB_PATH = SCRIPT_DIR.parent / "data" / "bankrollbbq.db"


def load_baseline() -> dict:
    """Load baseline metrics from JSON file."""
    if not BASELINE_PATH.exists():
        return {
            "providers": {},
            "thresholds": {
                "min_cross_match_pct": 15,
                "max_score_outcomes": 0,
                "default_min_ratio": 2.4,
                "default_max_ratio": 3.2,
                "default_min_norm": 97,
            },
        }
    with open(BASELINE_PATH) as f:
        return json.load(f)


def get_provider_metrics(cursor: sqlite3.Cursor) -> list[dict]:
    """Get odds/event ratio and count metrics per provider."""
    cursor.execute("""
        SELECT
            p.name,
            COUNT(o.id) as odds_count,
            COUNT(DISTINCT o.event_id) as event_count,
            ROUND(CAST(COUNT(o.id) AS FLOAT) / NULLIF(COUNT(DISTINCT o.event_id), 0), 2) as ratio
        FROM odds o
        JOIN providers p ON o.provider_id = p.id
        GROUP BY p.name
        ORDER BY p.name
    """)

    return [
        {
            "name": row[0],
            "odds": row[1],
            "events": row[2],
            "ratio": row[3] or 0,
        }
        for row in cursor.fetchall()
    ]


def get_normalization_rates(cursor: sqlite3.Cursor) -> dict[str, float]:
    """Get outcome normalization percentage per provider."""
    cursor.execute("""
        SELECT
            p.name,
            ROUND(
                100.0 * SUM(CASE WHEN o.outcome IN ('home', 'away', 'draw') THEN 1 ELSE 0 END)
                / NULLIF(COUNT(*), 0),
                1
            ) as norm_pct
        FROM odds o
        JOIN providers p ON o.provider_id = p.id
        GROUP BY p.name
    """)

    return {row[0]: row[1] or 0 for row in cursor.fetchall()}


def get_score_outcomes(cursor: sqlite3.Cursor) -> dict[str, int]:
    """Get count of score-like outcomes (X-X pattern) per provider."""
    cursor.execute("""
        SELECT
            p.name,
            COUNT(*) as score_count
        FROM odds o
        JOIN providers p ON o.provider_id = p.id
        WHERE o.outcome LIKE '%-%'
        AND o.outcome NOT IN ('home', 'away', 'draw')
        GROUP BY p.name
    """)

    return {row[0]: row[1] for row in cursor.fetchall()}


def get_cross_provider_stats(cursor: sqlite3.Cursor) -> tuple[int, int, float]:
    """Get cross-provider matching statistics."""
    cursor.execute("""
        SELECT
            COUNT(*) as total_events,
            SUM(CASE WHEN provider_count > 1 THEN 1 ELSE 0 END) as matched_events
        FROM (
            SELECT event_id, COUNT(DISTINCT provider_id) as provider_count
            FROM odds
            GROUP BY event_id
        )
    """)

    row = cursor.fetchone()
    total = row[0] or 0
    matched = row[1] or 0
    pct = (matched / total * 100) if total > 0 else 0

    return total, matched, pct


def validate_provider(
    name: str,
    metrics: dict,
    norm_rate: float,
    score_count: int,
    baseline: dict,
) -> tuple[bool, list[str]]:
    """Validate a provider against baseline thresholds."""
    issues = []

    # Get thresholds for this provider
    provider_baseline = baseline.get("providers", {}).get(name, {})
    thresholds = baseline.get("thresholds", {})

    min_ratio = provider_baseline.get("min_ratio", thresholds.get("default_min_ratio", 2.4))
    max_ratio = provider_baseline.get("max_ratio", thresholds.get("default_max_ratio", 3.2))
    min_norm = provider_baseline.get("min_norm", thresholds.get("default_min_norm", 97))
    max_score = thresholds.get("max_score_outcomes", 0)

    # Validate ratio
    ratio = metrics.get("ratio", 0)
    if ratio < min_ratio:
        issues.append(f"Ratio {ratio:.2f} below minimum {min_ratio}")
    elif ratio > max_ratio:
        issues.append(f"Ratio {ratio:.2f} above maximum {max_ratio}")

    # Validate normalization
    if norm_rate < min_norm:
        issues.append(f"Normalization {norm_rate:.1f}% below minimum {min_norm}%")

    # Validate score outcomes
    if score_count > max_score:
        issues.append(f"Score-like outcomes {score_count} above maximum {max_score}")

    return len(issues) == 0, issues


def print_validation_report(
    provider_metrics: list[dict],
    norm_rates: dict[str, float],
    score_outcomes: dict[str, int],
    cross_stats: tuple[int, int, float],
    baseline: dict,
    filter_provider: str | None = None,
) -> bool:
    """Print validation report and return True if all passed."""
    print("\n" + "=" * 60)
    print("Pipeline Validation Report")
    print("=" * 60)

    # Filter if specific provider requested
    if filter_provider:
        provider_metrics = [m for m in provider_metrics if m["name"] == filter_provider]
        if not provider_metrics:
            print(f"\nNo data found for provider: {filter_provider}")
            return False

    # Print header
    print(f"\n{'Provider':<12} | {'Odds':>5} | {'Events':>6} | {'Ratio':>5} | {'Norm%':>5} | {'Status':<6}")
    print("-" * 60)

    all_passed = True

    for metrics in provider_metrics:
        name = metrics["name"]
        norm_rate = norm_rates.get(name, 0)
        score_count = score_outcomes.get(name, 0)

        passed, issues = validate_provider(name, metrics, norm_rate, score_count, baseline)
        status = "PASS" if passed else "FAIL"

        if not passed:
            all_passed = False

        print(
            f"{name:<12} | {metrics['odds']:>5} | {metrics['events']:>6} | "
            f"{metrics['ratio']:>5.2f} | {norm_rate:>5.1f} | {status:<6}"
        )

        # Print issues if any
        for issue in issues:
            print(f"  -> {issue}")

    # Cross-provider stats
    total, matched, pct = cross_stats
    print("\n" + "-" * 60)
    print(f"Cross-provider matching: {matched}/{total} events ({pct:.1f}%)")

    min_cross = baseline.get("thresholds", {}).get("min_cross_match_pct", 15)
    if pct < min_cross and total > 0:
        print(f"  -> WARNING: Below minimum {min_cross}%")
        all_passed = False

    # Score-like outcomes summary
    total_scores = sum(score_outcomes.values())
    max_scores = baseline.get("thresholds", {}).get("max_score_outcomes", 0)
    print(f"Score-like outcomes: {total_scores} {'(PASS)' if total_scores <= max_scores else '(FAIL)'}")

    print("\n" + "=" * 60)
    print(f"Overall: {'ALL CHECKS PASSED' if all_passed else 'SOME CHECKS FAILED'}")
    print("=" * 60 + "\n")

    return all_passed


def capture_baseline(
    provider_metrics: list[dict],
    norm_rates: dict[str, float],
) -> None:
    """Capture current metrics as new baseline."""
    baseline = load_baseline()
    baseline["captured_at"] = datetime.now().strftime("%Y-%m-%d")

    for metrics in provider_metrics:
        name = metrics["name"]
        ratio = metrics["ratio"]
        norm = norm_rates.get(name, 100)

        # Set reasonable bounds around current values
        baseline["providers"][name] = {
            "min_ratio": round(ratio * 0.9, 1),  # 10% below current
            "max_ratio": round(ratio * 1.1, 1),  # 10% above current
            "min_norm": max(95, round(norm - 3, 0)),  # 3% below or 95%
        }

    with open(BASELINE_PATH, "w") as f:
        json.dump(baseline, f, indent=2)

    print(f"Baseline captured to {BASELINE_PATH}")


def main():
    parser = argparse.ArgumentParser(description="Validate provider pipeline metrics")
    parser.add_argument(
        "--provider", "-p",
        help="Validate specific provider only",
    )
    parser.add_argument(
        "--capture", "-c",
        action="store_true",
        help="Capture current metrics as baseline",
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=DB_PATH,
        help="Path to database file",
    )
    args = parser.parse_args()

    # Check database exists
    if not args.db.exists():
        print(f"Database not found: {args.db}")
        print("Run extraction first: python -m src.app extract pinnacle polymarket leovegas")
        sys.exit(1)

    # Connect and gather metrics
    conn = sqlite3.connect(args.db)
    cursor = conn.cursor()

    try:
        provider_metrics = get_provider_metrics(cursor)
        norm_rates = get_normalization_rates(cursor)
        score_outcomes = get_score_outcomes(cursor)
        cross_stats = get_cross_provider_stats(cursor)
    finally:
        conn.close()

    if not provider_metrics:
        print("No provider data found in database")
        print("Run extraction first: python -m src.app extract pinnacle polymarket leovegas")
        sys.exit(1)

    # Load baseline
    baseline = load_baseline()

    # Capture mode
    if args.capture:
        capture_baseline(provider_metrics, norm_rates)
        return

    # Validation mode
    all_passed = print_validation_report(
        provider_metrics,
        norm_rates,
        score_outcomes,
        cross_stats,
        baseline,
        filter_provider=args.provider,
    )

    sys.exit(0 if all_passed else 1)


if __name__ == "__main__":
    main()
