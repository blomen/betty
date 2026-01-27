"""
Tests for performance metrics tracking.
"""

import time
import pytest
import sys
from pathlib import Path

# Add backend to path
sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

from src.pipeline.metrics import (
    SportMetrics,
    ProviderMetrics,
    PipelineMetrics,
    MetricsCollector
)


def test_sport_metrics():
    """Test SportMetrics dataclass."""
    sport = SportMetrics(sport="football")

    assert sport.sport == "football"
    assert not sport.is_complete

    # Simulate extraction
    time.sleep(0.01)  # Small delay
    sport.end(success=True)

    assert sport.is_complete
    assert sport.success
    assert sport.duration_seconds > 0
    assert sport.error is None


def test_sport_metrics_with_error():
    """Test SportMetrics with error."""
    sport = SportMetrics(sport="basketball")
    sport.end(success=False, error="Connection timeout")

    assert sport.is_complete
    assert not sport.success
    assert sport.error == "Connection timeout"


def test_provider_metrics():
    """Test ProviderMetrics dataclass."""
    provider = ProviderMetrics(provider_id="unibet")

    assert provider.provider_id == "unibet"
    assert not provider.is_complete
    assert provider.total_events == 0

    # Add sport metrics
    sport1 = provider.start_sport("football")
    provider.end_sport("football", events_processed=50, events_new=10, success=True)

    sport2 = provider.start_sport("basketball")
    provider.end_sport("basketball", events_processed=30, events_new=5, success=True)

    assert provider.sports_attempted == 2
    assert provider.sports_succeeded == 2
    assert provider.sports_failed == 0
    assert provider.total_events == 80
    assert provider.total_events_new == 15
    assert provider.success_rate == 1.0

    # End provider
    time.sleep(0.01)
    provider.end(success=True)

    assert provider.is_complete
    assert provider.duration_seconds > 0


def test_provider_metrics_with_failures():
    """Test ProviderMetrics with failed sports."""
    provider = ProviderMetrics(provider_id="leovegas")

    provider.start_sport("football")
    provider.end_sport("football", events_processed=50, success=True)

    provider.start_sport("basketball")
    provider.end_sport("basketball", events_processed=0, success=False, error="API error")

    provider.start_sport("tennis")
    provider.end_sport("tennis", events_processed=20, success=True)

    assert provider.sports_attempted == 3
    assert provider.sports_succeeded == 2
    assert provider.sports_failed == 1
    assert provider.success_rate == pytest.approx(0.666, rel=0.01)
    assert provider.total_events == 70


def test_pipeline_metrics():
    """Test PipelineMetrics dataclass."""
    pipeline = PipelineMetrics(run_id="run_123")

    assert pipeline.run_id == "run_123"
    assert not pipeline.is_complete

    # Add Polymarket stats
    pipeline.polymarket_events = 100
    pipeline.polymarket_odds = 200

    # Add provider metrics
    provider1 = pipeline.start_provider("unibet")
    provider1.end(success=True)

    provider2 = pipeline.start_provider("leovegas")
    provider2.end(success=False, error="Timeout")

    assert pipeline.providers_attempted == 2
    assert pipeline.providers_succeeded == 1
    assert pipeline.providers_failed == 1
    assert pipeline.overall_success_rate == 0.5

    # End pipeline
    time.sleep(0.01)
    pipeline.end()

    assert pipeline.is_complete
    assert pipeline.duration_seconds > 0


def test_pipeline_metrics_to_dict():
    """Test PipelineMetrics serialization."""
    pipeline = PipelineMetrics(run_id="run_456")
    pipeline.polymarket_events = 50
    pipeline.polymarket_odds = 100

    provider = pipeline.start_provider("unibet")
    provider.start_sport("football")
    provider.end_sport("football", events_processed=30, events_new=5, success=True)
    provider.end(success=True)

    pipeline.end()

    result = pipeline.to_dict()

    assert result["run_id"] == "run_456"
    assert result["total_events"] == 80  # 50 + 30
    assert result["providers_attempted"] == 1
    assert result["providers_succeeded"] == 1
    assert result["overall_success_rate"] == 1.0
    assert result["polymarket"]["events"] == 50
    assert "unibet" in result["providers"]
    assert result["providers"]["unibet"]["total_events"] == 30
    assert "football" in result["providers"]["unibet"]["sports"]


def test_metrics_collector_basic():
    """Test MetricsCollector basic functionality."""
    collector = MetricsCollector(max_history=5)

    # Start run
    run = collector.start_run("run_001")
    assert run is not None
    assert collector.get_current_run() is not None

    # End run
    collector.end_run()
    assert collector.get_current_run() is None

    # Check history
    history = collector.get_history()
    assert len(history) == 1
    assert history[0].run_id == "run_001"


def test_metrics_collector_provider_tracking():
    """Test MetricsCollector provider tracking."""
    collector = MetricsCollector()

    collector.start_run("run_002")

    # Start provider
    provider = collector.start_provider("unibet")
    assert provider is not None
    assert provider.provider_id == "unibet"

    # Record retries and cache hits
    collector.record_retry("unibet")
    collector.record_retry("unibet")
    collector.record_cache_hit("unibet")

    current = collector.get_current_run()
    assert current.providers["unibet"].retries == 2
    assert current.providers["unibet"].cache_hits == 1

    # End provider
    collector.end_provider("unibet", success=True)
    assert current.providers["unibet"].is_complete

    collector.end_run()


def test_metrics_collector_sport_tracking():
    """Test MetricsCollector sport tracking."""
    collector = MetricsCollector()

    collector.start_run("run_003")
    collector.start_provider("leovegas")

    # Start sport
    sport = collector.start_sport("leovegas", "football")
    assert sport is not None
    assert sport.sport == "football"

    # End sport
    collector.end_sport(
        "leovegas",
        "football",
        events_processed=50,
        events_new=10,
        odds_processed=100,
        odds_new=20,
        success=True
    )

    current = collector.get_current_run()
    sport_metrics = current.providers["leovegas"].sports["football"]
    assert sport_metrics.events_processed == 50
    assert sport_metrics.events_new == 10
    assert sport_metrics.success

    collector.end_run()


def test_metrics_collector_history():
    """Test MetricsCollector history management."""
    collector = MetricsCollector(max_history=3)

    # Create 5 runs
    for i in range(5):
        collector.start_run(f"run_{i}")
        collector.end_run()

    # Should only keep last 3
    history = collector.get_history(limit=10)
    assert len(history) == 3
    assert history[0].run_id == "run_4"  # Newest first
    assert history[1].run_id == "run_3"
    assert history[2].run_id == "run_2"


def test_metrics_collector_provider_aggregate():
    """Test MetricsCollector provider aggregation."""
    collector = MetricsCollector()

    # Create 3 runs with same provider
    for i in range(3):
        collector.start_run(f"run_{i}")
        collector.start_provider("unibet")

        # Simulate extraction
        time.sleep(0.01)

        collector.end_provider("unibet", success=True)

        # Set some stats
        current = collector.get_current_run()
        current.providers["unibet"].sports["football"] = SportMetrics(sport="football")
        current.providers["unibet"].sports["football"].events_processed = 50 * (i + 1)
        current.providers["unibet"].retries = i
        current.providers["unibet"].cache_hits = i * 2

        collector.end_run()

    # Get aggregate
    agg = collector.get_provider_aggregate("unibet", limit=10)

    assert agg["provider_id"] == "unibet"
    assert agg["runs"] == 3
    assert agg["avg_duration_seconds"] > 0
    assert agg["avg_events"] > 0
    assert agg["total_retries"] == 3  # 0 + 1 + 2
    assert agg["total_cache_hits"] == 6  # 0 + 2 + 4


def test_metrics_collector_polymarket():
    """Test MetricsCollector Polymarket stats."""
    collector = MetricsCollector()

    collector.start_run("run_poly")
    collector.set_polymarket_stats(events=150, odds=300)

    current = collector.get_current_run()
    assert current.polymarket_events == 150
    assert current.polymarket_odds == 300
    assert current.total_events == 150  # No providers yet

    collector.end_run()


def test_metrics_collector_thread_safety():
    """Test MetricsCollector thread safety (basic check)."""
    import threading

    collector = MetricsCollector()
    collector.start_run("run_threads")

    def record_retries():
        for _ in range(100):
            collector.record_retry("unibet")

    def record_cache_hits():
        for _ in range(100):
            collector.record_cache_hit("unibet")

    collector.start_provider("unibet")

    # Run in parallel
    t1 = threading.Thread(target=record_retries)
    t2 = threading.Thread(target=record_cache_hits)

    t1.start()
    t2.start()
    t1.join()
    t2.join()

    current = collector.get_current_run()
    assert current.providers["unibet"].retries == 100
    assert current.providers["unibet"].cache_hits == 100

    collector.end_run()


def test_metrics_no_current_run():
    """Test MetricsCollector operations with no current run."""
    collector = MetricsCollector()

    # Should handle gracefully
    provider = collector.start_provider("unibet")
    assert provider is None

    sport = collector.start_sport("unibet", "football")
    assert sport is None

    collector.end_provider("unibet", success=True)
    collector.end_sport("unibet", "football", success=True)
    collector.record_retry("unibet")
    collector.record_cache_hit("unibet")

    # Should not raise errors
    assert collector.get_current_run() is None
