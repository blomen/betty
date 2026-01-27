"""
Tests for provider performance monitoring.
"""

import pytest
import sys
from pathlib import Path

# Add backend to path
sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

from src.pipeline.provider_monitor import (
    ProviderMonitor,
    HealthScore,
    IssueType,
    ProviderHealth,
    ProviderIssue
)
from src.pipeline.metrics import PipelineMetrics, ProviderMetrics, SportMetrics


def create_mock_run(run_id, provider_id, events=100, odds=200, success=True, duration_s=10.0):
    """Create mock pipeline run with provider metrics."""
    run = PipelineMetrics(run_id=run_id)
    provider = run.start_provider(provider_id)

    # Add sport data
    sport = provider.start_sport("football")
    sport.events_processed = events
    sport.odds_processed = odds
    sport.success = success
    sport.end(success=success)

    provider.end(success=success)
    # Override duration after end() call
    provider.duration_seconds = duration_s

    run.end()

    return run


def test_monitor_initialization():
    """Test ProviderMonitor initialization."""
    monitor = ProviderMonitor(
        min_events_threshold=10,
        min_odds_per_event=2.0,
        max_response_time_ms=10000.0
    )

    assert monitor.min_events_threshold == 10
    assert monitor.min_odds_per_event == 2.0
    assert monitor.max_response_time_ms == 10000.0


def test_assess_provider_no_data():
    """Test assessment when no historical data available."""
    monitor = ProviderMonitor()
    history = []

    health = monitor.assess_provider("test_provider", history)

    assert health.provider_id == "test_provider"
    assert health.health_score == HealthScore.CRITICAL
    assert health.score_value == 0.0
    assert len(health.issues) == 1
    assert health.issues[0].issue_type == IssueType.NO_DATA


def test_assess_healthy_provider():
    """Test assessment of healthy provider."""
    monitor = ProviderMonitor()

    # Create 10 successful runs with good data
    history = [
        create_mock_run(f"run_{i}", "unibet", events=100, odds=200, success=True)
        for i in range(10)
    ]

    health = monitor.assess_provider("unibet", history)

    assert health.provider_id == "unibet"
    assert health.health_score in [HealthScore.EXCELLENT, HealthScore.GOOD]
    assert health.is_healthy
    assert not health.has_critical_issues
    assert health.avg_events_per_run == 100.0
    assert health.success_rate == 1.0


def test_detect_no_data_issue():
    """Test detection of provider returning no data."""
    monitor = ProviderMonitor()

    # Create runs with zero events
    history = [
        create_mock_run(f"run_{i}", "broken_provider", events=0, odds=0, success=True)
        for i in range(10)
    ]

    health = monitor.assess_provider("broken_provider", history)

    assert not health.is_healthy
    assert any(issue.issue_type == IssueType.NO_DATA for issue in health.issues)
    assert any(issue.severity == "critical" for issue in health.issues)


def test_detect_low_data_issue():
    """Test detection of provider with low event count."""
    monitor = ProviderMonitor(min_events_threshold=50)

    # Create runs with low events
    history = [
        create_mock_run(f"run_{i}", "slow_provider", events=20, odds=40, success=True)
        for i in range(10)
    ]

    health = monitor.assess_provider("slow_provider", history)

    assert any(issue.issue_type == IssueType.LOW_DATA for issue in health.issues)
    assert health.avg_events_per_run == 20.0


def test_detect_no_odds_issue():
    """Test detection of provider returning events but no odds."""
    monitor = ProviderMonitor()

    # Create runs with events but no odds
    history = [
        create_mock_run(f"run_{i}", "incomplete_provider", events=50, odds=0, success=True)
        for i in range(10)
    ]

    health = monitor.assess_provider("incomplete_provider", history)

    assert any(issue.issue_type == IssueType.NO_ODDS for issue in health.issues)
    assert any(issue.severity == "critical" for issue in health.issues)


def test_detect_sparse_odds_issue():
    """Test detection of sparse odds per event."""
    monitor = ProviderMonitor(min_odds_per_event=2.0)

    # Create runs with few odds per event (1 odd per event)
    history = [
        create_mock_run(f"run_{i}", "sparse_provider", events=100, odds=100, success=True)
        for i in range(10)
    ]

    health = monitor.assess_provider("sparse_provider", history)

    assert any(issue.issue_type == IssueType.SPARSE_ODDS for issue in health.issues)
    assert health.avg_odds_per_event == 1.0


def test_detect_slow_response():
    """Test detection of slow response times."""
    monitor = ProviderMonitor(max_response_time_ms=5000.0)

    # Create runs with slow response times (20s each)
    history = [
        create_mock_run(f"run_{i}", "slow_provider", events=100, odds=200, success=True, duration_s=20.0)
        for i in range(10)
    ]

    health = monitor.assess_provider("slow_provider", history)

    assert any(issue.issue_type == IssueType.SLOW_RESPONSE for issue in health.issues)
    assert health.avg_response_time_ms == 20000.0


def test_detect_high_failure_rate():
    """Test detection of high failure rate."""
    monitor = ProviderMonitor(min_success_rate=0.7)

    # Create runs with 50% failure rate
    history = []
    for i in range(10):
        success = i % 2 == 0  # 50% success
        history.append(create_mock_run(f"run_{i}", "unreliable_provider", events=100 if success else 0, success=success))

    health = monitor.assess_provider("unreliable_provider", history)

    assert any(issue.issue_type == IssueType.HIGH_FAILURE for issue in health.issues)
    assert health.success_rate == 0.5


def test_detect_degradation_trend():
    """Test detection of performance degradation."""
    monitor = ProviderMonitor(degradation_threshold=0.3)

    # Create runs: first half good (100 events), second half degraded (50 events)
    history = []
    for i in range(10):
        events = 100 if i < 5 else 50
        history.append(create_mock_run(f"run_{i}", "degrading_provider", events=events, success=True))

    health = monitor.assess_provider("degrading_provider", history)

    assert health.is_degrading
    assert health.trend_direction == "degrading"
    assert any(issue.issue_type == IssueType.DEGRADING for issue in health.issues)


def test_detect_improvement_trend():
    """Test detection of performance improvement."""
    monitor = ProviderMonitor()

    # Create runs: first half low (50 events), second half improved (100 events)
    history = []
    for i in range(10):
        events = 50 if i < 5 else 100
        history.append(create_mock_run(f"run_{i}", "improving_provider", events=events, success=True))

    health = monitor.assess_provider("improving_provider", history)

    assert not health.is_degrading
    assert health.trend_direction == "improving"


def test_circuit_breaker_integration():
    """Test integration with circuit breaker status."""
    monitor = ProviderMonitor()

    history = [
        create_mock_run(f"run_{i}", "test_provider", events=100, success=True)
        for i in range(5)
    ]

    cb_status = {
        "state": "open",
        "failure_count": 5,
        "success_count": 0
    }

    health = monitor.assess_provider("test_provider", history, circuit_breaker_status=cb_status)

    assert any(issue.issue_type == IssueType.CIRCUIT_OPEN for issue in health.issues)
    assert any(issue.severity == "critical" for issue in health.issues)


def test_health_check_integration():
    """Test integration with health check status."""
    monitor = ProviderMonitor()

    history = [
        create_mock_run(f"run_{i}", "test_provider", events=100, success=True)
        for i in range(5)
    ]

    hc_status = {
        "healthy": False,
        "error": "Connection timeout"
    }

    health = monitor.assess_provider("test_provider", history, health_check_status=hc_status)

    assert any(issue.issue_type == IssueType.UNHEALTHY for issue in health.issues)
    assert any(issue.severity == "critical" for issue in health.issues)


def test_score_calculation():
    """Test health score calculation."""
    monitor = ProviderMonitor()

    # Create provider with multiple issues
    history = [
        create_mock_run(f"run_{i}", "bad_provider", events=5, odds=5, success=i % 2 == 0)
        for i in range(10)
    ]

    health = monitor.assess_provider("bad_provider", history)

    # Should have multiple issues and low score
    assert len(health.issues) > 0
    assert health.score_value < 70.0
    assert not health.is_healthy


def test_score_categories():
    """Test health score category mapping."""
    monitor = ProviderMonitor()

    # Excellent provider (100 points)
    assert monitor._score_to_category(95.0) == HealthScore.EXCELLENT

    # Good provider (70-89 points)
    assert monitor._score_to_category(75.0) == HealthScore.GOOD

    # Fair provider (50-69 points)
    assert monitor._score_to_category(60.0) == HealthScore.FAIR

    # Poor provider (30-49 points)
    assert monitor._score_to_category(40.0) == HealthScore.POOR

    # Critical provider (0-29 points)
    assert monitor._score_to_category(20.0) == HealthScore.CRITICAL


def test_assess_all_providers():
    """Test assessing multiple providers."""
    monitor = ProviderMonitor()

    # Create history with multiple providers
    history = []
    for i in range(10):
        run = PipelineMetrics(run_id=f"run_{i}")

        # Good provider
        p1 = run.start_provider("unibet")
        p1.sports["football"] = SportMetrics(sport="football")
        p1.sports["football"].events_processed = 100
        p1.sports["football"].odds_processed = 200
        p1.end(success=True)

        # Bad provider
        p2 = run.start_provider("broken")
        p2.sports["football"] = SportMetrics(sport="football")
        p2.sports["football"].events_processed = 0
        p2.end(success=True)

        run.end()
        history.append(run)

    assessments = monitor.assess_all_providers(history)

    assert len(assessments) == 2
    assert "unibet" in assessments
    assert "broken" in assessments
    assert assessments["unibet"].is_healthy
    assert not assessments["broken"].is_healthy


def test_get_unhealthy_providers():
    """Test filtering unhealthy providers."""
    monitor = ProviderMonitor()

    # Create mock assessments
    assessments = {
        "healthy1": ProviderHealth("healthy1", HealthScore.EXCELLENT, 95.0),
        "healthy2": ProviderHealth("healthy2", HealthScore.GOOD, 75.0),
        "unhealthy1": ProviderHealth("unhealthy1", HealthScore.POOR, 40.0),
        "unhealthy2": ProviderHealth("unhealthy2", HealthScore.CRITICAL, 10.0),
    }

    unhealthy = monitor.get_unhealthy_providers(assessments)

    assert len(unhealthy) == 2
    assert "unhealthy1" in unhealthy
    assert "unhealthy2" in unhealthy


def test_get_critical_providers():
    """Test filtering providers with critical issues."""
    monitor = ProviderMonitor()

    # Create mock assessments with issues
    health1 = ProviderHealth("provider1", HealthScore.GOOD, 75.0)
    health1.issues.append(ProviderIssue(IssueType.LOW_DATA, "warning", "Low data"))

    health2 = ProviderHealth("provider2", HealthScore.POOR, 40.0)
    health2.issues.append(ProviderIssue(IssueType.NO_DATA, "critical", "No data"))

    health3 = ProviderHealth("provider3", HealthScore.CRITICAL, 10.0)
    health3.issues.append(ProviderIssue(IssueType.CIRCUIT_OPEN, "critical", "Circuit open"))

    assessments = {
        "provider1": health1,
        "provider2": health2,
        "provider3": health3,
    }

    critical = monitor.get_critical_providers(assessments)

    assert len(critical) == 2
    assert "provider2" in critical
    assert "provider3" in critical


def test_provider_health_properties():
    """Test ProviderHealth helper properties."""
    # Healthy provider
    health = ProviderHealth("test", HealthScore.EXCELLENT, 95.0)
    assert health.is_healthy
    assert not health.has_critical_issues

    # Unhealthy provider
    health = ProviderHealth("test", HealthScore.POOR, 40.0)
    assert not health.is_healthy

    # Provider with critical issue
    health = ProviderHealth("test", HealthScore.GOOD, 75.0)
    health.issues.append(ProviderIssue(IssueType.NO_DATA, "critical", "Test"))
    assert health.has_critical_issues


def test_multiple_issue_types():
    """Test provider with multiple different issues."""
    monitor = ProviderMonitor(
        min_events_threshold=50,
        max_response_time_ms=5000.0,
        min_success_rate=0.8
    )

    # Create runs with multiple problems
    history = []
    for i in range(10):
        success = i < 6  # 60% success rate
        events = 20 if success else 0  # Low events when successful
        history.append(create_mock_run(f"run_{i}", "problematic", events=events, odds=20, success=success, duration_s=15.0))

    health = monitor.assess_provider("problematic", history)

    # Should have multiple issue types
    issue_types = {issue.issue_type for issue in health.issues}
    assert IssueType.LOW_DATA in issue_types
    assert IssueType.SLOW_RESPONSE in issue_types
    assert IssueType.HIGH_FAILURE in issue_types

    assert health.health_score in [HealthScore.POOR, HealthScore.CRITICAL]
