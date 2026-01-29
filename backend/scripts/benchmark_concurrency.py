#!/usr/bin/env python3
"""
Benchmark Concurrency Settings

Tests different concurrency configurations to find optimal settings
that maximize extraction speed while avoiding 429 rate limit errors.

Usage:
    python scripts/benchmark_concurrency.py
    python scripts/benchmark_concurrency.py --configs conservative moderate aggressive
    python scripts/benchmark_concurrency.py --providers unibet leovegas paf
"""

import argparse
import asyncio
import json
import logging
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.config.loader import ConfigLoader
from src.factory import ExtractorFactory
from src.pipeline.orchestrator import ExtractionPipeline
from src.db.models import get_session

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger(__name__)


@dataclass
class BenchmarkConfig:
    """Configuration for a benchmark run."""
    name: str
    max_providers: int
    max_sports: int
    description: str = ""


@dataclass
class BenchmarkResult:
    """Results from a benchmark run."""
    config_name: str
    max_providers: int
    max_sports: int
    total_time_seconds: float
    events_extracted: int
    providers_succeeded: int
    providers_failed: int
    rate_limit_errors: int = 0
    retries: int = 0
    errors_by_provider: dict = field(default_factory=dict)
    events_per_second: float = 0.0

    def __post_init__(self):
        if self.total_time_seconds > 0:
            self.events_per_second = self.events_extracted / self.total_time_seconds


# Predefined benchmark configurations
BENCHMARK_CONFIGS = {
    "conservative": BenchmarkConfig(
        name="conservative",
        max_providers=3,
        max_sports=2,
        description="Safe settings, low risk of rate limiting"
    ),
    "baseline": BenchmarkConfig(
        name="baseline",
        max_providers=5,
        max_sports=3,
        description="Current default settings"
    ),
    "moderate": BenchmarkConfig(
        name="moderate",
        max_providers=6,
        max_sports=4,
        description="Slightly more aggressive"
    ),
    "aggressive": BenchmarkConfig(
        name="aggressive",
        max_providers=8,
        max_sports=5,
        description="High concurrency, higher risk of 429s"
    ),
    "maximum": BenchmarkConfig(
        name="maximum",
        max_providers=10,
        max_sports=6,
        description="Maximum throughput test"
    ),
}


class BenchmarkRunner:
    """Runs benchmark tests with different concurrency configurations."""

    def __init__(
        self,
        providers: Optional[list[str]] = None,
        sports: Optional[list[str]] = None,
        dry_run: bool = False
    ):
        self.providers = providers
        self.sports = sports or ["football", "basketball", "ice_hockey"]
        self.dry_run = dry_run
        self.results: list[BenchmarkResult] = []

    async def run_config(self, config: BenchmarkConfig) -> BenchmarkResult:
        """
        Run extraction with a specific concurrency configuration.

        Args:
            config: BenchmarkConfig with concurrency settings

        Returns:
            BenchmarkResult with metrics
        """
        logger.info(f"\n{'='*60}")
        logger.info(f"Testing: {config.name}")
        logger.info(f"  max_providers: {config.max_providers}")
        logger.info(f"  max_sports: {config.max_sports}")
        logger.info(f"  {config.description}")
        logger.info(f"{'='*60}")

        if self.dry_run:
            return BenchmarkResult(
                config_name=config.name,
                max_providers=config.max_providers,
                max_sports=config.max_sports,
                total_time_seconds=0,
                events_extracted=0,
                providers_succeeded=0,
                providers_failed=0
            )

        # Temporarily override orchestrator config
        config_loader = ConfigLoader.get_instance()
        original_max_providers = config_loader.orchestrator_config.max_concurrent_providers
        original_max_sports = config_loader.orchestrator_config.max_concurrent_sports_per_provider

        try:
            # Apply test configuration
            config_loader.orchestrator_config.max_concurrent_providers = config.max_providers
            config_loader.orchestrator_config.max_concurrent_sports_per_provider = config.max_sports

            # Create fresh session and pipeline
            session = get_session()
            pipeline = ExtractionPipeline(db_session=session)

            # Run extraction
            start_time = time.time()
            results = await pipeline.run(
                polymarket=False,  # Skip polymarket for benchmark
                providers=self.providers
            )
            elapsed = time.time() - start_time

            # Collect metrics
            total_events = sum(
                p.get("events_processed", 0)
                for p in results.get("providers", {}).values()
            )
            providers_succeeded = sum(
                1 for p in results.get("providers", {}).values()
                if "error" not in p
            )
            providers_failed = sum(
                1 for p in results.get("providers", {}).values()
                if "error" in p
            )

            # Get rate limit info from metrics if available
            rate_limit_errors = 0
            retries = 0
            if "metrics" in results:
                retries = results["metrics"].get("total_retries", 0)

            # Collect errors by provider
            errors_by_provider = {}
            for pid, presult in results.get("providers", {}).items():
                if "error" in presult:
                    errors_by_provider[pid] = presult["error"]
                elif "sport_errors" in presult and presult["sport_errors"]:
                    errors_by_provider[pid] = presult["sport_errors"]

            result = BenchmarkResult(
                config_name=config.name,
                max_providers=config.max_providers,
                max_sports=config.max_sports,
                total_time_seconds=elapsed,
                events_extracted=total_events,
                providers_succeeded=providers_succeeded,
                providers_failed=providers_failed,
                rate_limit_errors=rate_limit_errors,
                retries=retries,
                errors_by_provider=errors_by_provider
            )

            logger.info(f"\nResults for {config.name}:")
            logger.info(f"  Time: {elapsed:.1f}s")
            logger.info(f"  Events: {total_events}")
            logger.info(f"  Events/sec: {result.events_per_second:.1f}")
            logger.info(f"  Providers: {providers_succeeded} OK, {providers_failed} failed")
            logger.info(f"  Retries: {retries}")

            if errors_by_provider:
                logger.warning(f"  Errors: {len(errors_by_provider)} providers had errors")

            return result

        finally:
            # Restore original config
            config_loader.orchestrator_config.max_concurrent_providers = original_max_providers
            config_loader.orchestrator_config.max_concurrent_sports_per_provider = original_max_sports

    async def run_benchmark(self, config_names: list[str]) -> list[BenchmarkResult]:
        """
        Run benchmark with multiple configurations.

        Args:
            config_names: List of configuration names to test

        Returns:
            List of BenchmarkResult
        """
        configs = [BENCHMARK_CONFIGS[name] for name in config_names if name in BENCHMARK_CONFIGS]

        if not configs:
            logger.error(f"No valid configs found. Available: {list(BENCHMARK_CONFIGS.keys())}")
            return []

        logger.info(f"Starting benchmark with {len(configs)} configurations")
        logger.info(f"Providers: {self.providers or 'all enabled'}")
        logger.info(f"Sports: {self.sports}")

        for config in configs:
            # Add delay between runs to let rate limits reset
            if self.results:
                logger.info("\nWaiting 30s before next configuration...")
                await asyncio.sleep(30)

            result = await self.run_config(config)
            self.results.append(result)

        return self.results

    def print_summary(self):
        """Print summary comparison of all benchmark results."""
        if not self.results:
            logger.info("No results to summarize")
            return

        logger.info("\n" + "=" * 80)
        logger.info("BENCHMARK SUMMARY")
        logger.info("=" * 80)

        # Header
        print(f"\n{'Config':<15} {'Providers':<10} {'Sports':<8} {'Time(s)':<10} {'Events':<10} {'Evt/s':<10} {'Errors':<8}")
        print("-" * 80)

        best_throughput = max(r.events_per_second for r in self.results) if self.results else 0
        best_time = min(r.total_time_seconds for r in self.results if r.total_time_seconds > 0) if self.results else 0

        for r in self.results:
            markers = []
            if r.events_per_second == best_throughput and best_throughput > 0:
                markers.append("*FASTEST*")
            if r.rate_limit_errors > 0:
                markers.append(f"[{r.rate_limit_errors} 429s]")

            marker_str = " ".join(markers)

            print(
                f"{r.config_name:<15} "
                f"{r.max_providers:<10} "
                f"{r.max_sports:<8} "
                f"{r.total_time_seconds:<10.1f} "
                f"{r.events_extracted:<10} "
                f"{r.events_per_second:<10.1f} "
                f"{r.providers_failed:<8} "
                f"{marker_str}"
            )

        print("-" * 80)

        # Recommendations
        print("\nRECOMMENDATIONS:")

        # Find optimal config (highest throughput with zero rate limits)
        optimal_candidates = [r for r in self.results if r.rate_limit_errors == 0 and r.providers_failed == 0]
        if optimal_candidates:
            optimal = max(optimal_candidates, key=lambda r: r.events_per_second)
            print(f"  [OK] Optimal config: {optimal.config_name}")
            print(f"       max_providers: {optimal.max_providers}")
            print(f"       max_sports: {optimal.max_sports}")
            print(f"       Throughput: {optimal.events_per_second:.1f} events/sec")
        else:
            # All configs had errors - recommend most conservative
            conservative = min(self.results, key=lambda r: r.max_providers)
            print(f"  [!] All configs had errors. Consider:")
            print(f"      - Reducing Kambi providers (same API)")
            print(f"      - Using {conservative.config_name} as baseline")

        # Check for rate limiting issues
        rate_limited = [r for r in self.results if r.rate_limit_errors > 0]
        if rate_limited:
            print(f"\n  [!] Rate limiting detected in: {[r.config_name for r in rate_limited]}")
            print("      Consider reducing max_providers or adding delays")

    def save_results(self, output_path: str):
        """Save results to JSON file."""
        data = {
            "timestamp": datetime.now().isoformat(),
            "providers": self.providers,
            "sports": self.sports,
            "results": [
                {
                    "config_name": r.config_name,
                    "max_providers": r.max_providers,
                    "max_sports": r.max_sports,
                    "total_time_seconds": r.total_time_seconds,
                    "events_extracted": r.events_extracted,
                    "events_per_second": r.events_per_second,
                    "providers_succeeded": r.providers_succeeded,
                    "providers_failed": r.providers_failed,
                    "rate_limit_errors": r.rate_limit_errors,
                    "retries": r.retries,
                    "errors": r.errors_by_provider
                }
                for r in self.results
            ]
        }

        with open(output_path, 'w') as f:
            json.dump(data, f, indent=2)

        logger.info(f"Results saved to {output_path}")


def main():
    parser = argparse.ArgumentParser(description="Benchmark concurrency settings")
    parser.add_argument(
        "--configs",
        nargs="+",
        default=["conservative", "baseline", "moderate", "aggressive"],
        help="Configurations to test"
    )
    parser.add_argument(
        "--providers",
        nargs="+",
        help="Specific providers to test (default: all enabled)"
    )
    parser.add_argument(
        "--sports",
        nargs="+",
        default=["football", "basketball", "ice_hockey"],
        help="Sports to extract"
    )
    parser.add_argument(
        "--output",
        default="backend/logs/benchmark_results.json",
        help="Output file for results"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print configs without running"
    )

    args = parser.parse_args()

    # Validate configs
    invalid_configs = [c for c in args.configs if c not in BENCHMARK_CONFIGS]
    if invalid_configs:
        logger.error(f"Invalid configs: {invalid_configs}")
        logger.info(f"Available: {list(BENCHMARK_CONFIGS.keys())}")
        sys.exit(1)

    runner = BenchmarkRunner(
        providers=args.providers,
        sports=args.sports,
        dry_run=args.dry_run
    )

    try:
        asyncio.run(runner.run_benchmark(args.configs))
        runner.print_summary()

        if not args.dry_run:
            runner.save_results(args.output)

    except KeyboardInterrupt:
        logger.info("\nBenchmark interrupted")
        runner.print_summary()


if __name__ == "__main__":
    main()
