"""
Provider Performance Monitoring

Detects providers that aren't providing odds or performing poorly.
Analyzes historical metrics to identify degradation patterns.
"""

import logging
import time
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger(__name__)


class HealthScore(Enum):
    """Provider health score categories."""

    EXCELLENT = "excellent"  # 90-100%
    GOOD = "good"  # 70-89%
    FAIR = "fair"  # 50-69%
    POOR = "poor"  # 30-49%
    CRITICAL = "critical"  # 0-29%


class IssueType(Enum):
    """Types of provider issues."""

    NO_DATA = "no_data"  # Returns 0 events
    LOW_DATA = "low_data"  # Significantly fewer events than baseline
    NO_ODDS = "no_odds"  # Events but no odds
    SPARSE_ODDS = "sparse_odds"  # Very few odds per event
    SLOW_RESPONSE = "slow_response"  # Response time above threshold
    HIGH_FAILURE = "high_failure"  # Failure rate above threshold
    CIRCUIT_OPEN = "circuit_open"  # Circuit breaker open
    UNHEALTHY = "unhealthy"  # Failed health checks
    TIMEOUT_PRONE = "timeout_prone"  # Frequent timeouts
    DEGRADING = "degrading"  # Trend shows degradation


@dataclass
class ProviderIssue:
    """Detected provider issue."""

    issue_type: IssueType
    severity: str  # "critical", "warning", "info"
    message: str
    detected_at: float = field(default_factory=time.time)
    metric_value: float | None = None
    threshold_value: float | None = None


@dataclass
class ProviderHealth:
    """Provider health assessment."""

    provider_id: str
    health_score: HealthScore
    score_value: float  # 0-100
    issues: list[ProviderIssue] = field(default_factory=list)

    # Metrics summary
    avg_events_per_run: float = 0.0
    avg_response_time_ms: float = 0.0
    success_rate: float = 0.0
    uptime_pct: float = 0.0

    # Data quality
    avg_odds_per_event: float = 0.0
    data_completeness_pct: float = 0.0

    # Trend indicators
    is_degrading: bool = False
    trend_direction: str = "stable"  # "improving", "stable", "degrading"

    assessed_at: float = field(default_factory=time.time)

    @property
    def is_healthy(self) -> bool:
        """Check if provider is considered healthy."""
        return self.health_score in [HealthScore.EXCELLENT, HealthScore.GOOD]

    @property
    def has_critical_issues(self) -> bool:
        """Check if provider has critical issues."""
        return any(issue.severity == "critical" for issue in self.issues)


class ProviderMonitor:
    """
    Monitor provider performance and data quality.

    Analyzes historical metrics to detect:
    - Providers not delivering data
    - Performance degradation
    - Data quality issues
    - Reliability problems
    """

    def __init__(
        self,
        min_events_threshold: int = 10,
        min_odds_per_event: float = 2.0,
        max_response_time_ms: float = 10000.0,
        min_success_rate: float = 0.7,
        min_uptime_pct: float = 0.8,
        degradation_threshold: float = 0.3,  # 30% drop from baseline
    ):
        """
        Initialize provider monitor.

        Args:
            min_events_threshold: Minimum events per run to be considered "providing"
            min_odds_per_event: Minimum odds per event for good data quality
            max_response_time_ms: Maximum acceptable response time
            min_success_rate: Minimum success rate (0-1)
            min_uptime_pct: Minimum uptime percentage (0-1)
            degradation_threshold: Percentage drop to trigger degradation alert
        """
        self.min_events_threshold = min_events_threshold
        self.min_odds_per_event = min_odds_per_event
        self.max_response_time_ms = max_response_time_ms
        self.min_success_rate = min_success_rate
        self.min_uptime_pct = min_uptime_pct
        self.degradation_threshold = degradation_threshold

    def assess_provider(
        self,
        provider_id: str,
        metrics_history: list,
        circuit_breaker_status: dict | None = None,
        health_check_status: dict | None = None,
    ) -> ProviderHealth:
        """
        Assess provider health based on historical metrics.

        Args:
            provider_id: Provider identifier
            metrics_history: List of PipelineMetrics from recent runs
            circuit_breaker_status: Optional circuit breaker status
            health_check_status: Optional health check status

        Returns:
            ProviderHealth assessment
        """
        health = ProviderHealth(provider_id=provider_id, health_score=HealthScore.GOOD, score_value=100.0)

        # Extract provider metrics from history
        provider_runs = []
        for run in metrics_history:
            if provider_id in run.providers:
                provider_runs.append(run.providers[provider_id])

        if not provider_runs:
            health.issues.append(
                ProviderIssue(issue_type=IssueType.NO_DATA, severity="critical", message="No historical data available")
            )
            health.health_score = HealthScore.CRITICAL
            health.score_value = 0.0
            return health

        # Calculate metrics
        total_runs = len(provider_runs)
        successful_runs = sum(1 for p in provider_runs if p.success)

        health.success_rate = successful_runs / total_runs if total_runs > 0 else 0.0
        health.uptime_pct = health.success_rate

        # Average events per run (only successful runs)
        successful_provider_runs = [p for p in provider_runs if p.success]
        if successful_provider_runs:
            health.avg_events_per_run = sum(p.total_events for p in successful_provider_runs) / len(
                successful_provider_runs
            )
            health.avg_response_time_ms = sum(p.duration_seconds * 1000 for p in successful_provider_runs) / len(
                successful_provider_runs
            )

            # Calculate odds per event
            total_events = sum(p.total_events for p in successful_provider_runs)
            total_odds = sum(p.total_odds for p in successful_provider_runs)
            health.avg_odds_per_event = total_odds / total_events if total_events > 0 else 0.0

        # Detect issues
        self._detect_data_issues(health, provider_runs)
        self._detect_performance_issues(health, provider_runs)
        self._detect_reliability_issues(health, provider_runs)
        self._detect_trend_issues(health, provider_runs)

        # Check circuit breaker
        if circuit_breaker_status:
            self._check_circuit_breaker(health, circuit_breaker_status)

        # Check health check results
        if health_check_status:
            self._check_health_status(health, health_check_status)

        # Calculate final score
        health.score_value = self._calculate_score(health)
        health.health_score = self._score_to_category(health.score_value)

        return health

    def _detect_data_issues(self, health: ProviderHealth, provider_runs: list):
        """Detect data delivery issues."""
        recent_runs = provider_runs[-5:] if len(provider_runs) >= 5 else provider_runs

        # Check for no data
        zero_event_runs = sum(1 for p in recent_runs if p.total_events == 0)
        if zero_event_runs >= len(recent_runs) * 0.8:  # 80%+ runs have no data
            health.issues.append(
                ProviderIssue(
                    issue_type=IssueType.NO_DATA,
                    severity="critical",
                    message=f"Provider returned 0 events in {zero_event_runs}/{len(recent_runs)} recent runs",
                    metric_value=zero_event_runs,
                    threshold_value=len(recent_runs) * 0.8,
                )
            )

        # Check for low data compared to baseline
        if health.avg_events_per_run < self.min_events_threshold:
            health.issues.append(
                ProviderIssue(
                    issue_type=IssueType.LOW_DATA,
                    severity="warning",
                    message=f"Average events ({health.avg_events_per_run:.1f}) below threshold ({self.min_events_threshold})",
                    metric_value=health.avg_events_per_run,
                    threshold_value=self.min_events_threshold,
                )
            )

        # Check for no odds (events but no odds)
        successful_with_events = [p for p in recent_runs if p.success and p.total_events > 0]
        if successful_with_events:
            no_odds_runs = sum(1 for p in successful_with_events if p.total_odds == 0)
            if no_odds_runs > 0:
                health.issues.append(
                    ProviderIssue(
                        issue_type=IssueType.NO_ODDS,
                        severity="critical",
                        message=f"Provider returned events but no odds in {no_odds_runs} run(s)",
                        metric_value=no_odds_runs,
                    )
                )

        # Check for sparse odds
        if health.avg_odds_per_event > 0 and health.avg_odds_per_event < self.min_odds_per_event:
            health.issues.append(
                ProviderIssue(
                    issue_type=IssueType.SPARSE_ODDS,
                    severity="warning",
                    message=f"Average odds per event ({health.avg_odds_per_event:.1f}) below expected ({self.min_odds_per_event})",
                    metric_value=health.avg_odds_per_event,
                    threshold_value=self.min_odds_per_event,
                )
            )

    def _detect_performance_issues(self, health: ProviderHealth, provider_runs: list):
        """Detect performance issues."""
        # Check response time
        if health.avg_response_time_ms > self.max_response_time_ms:
            health.issues.append(
                ProviderIssue(
                    issue_type=IssueType.SLOW_RESPONSE,
                    severity="warning",
                    message=f"Average response time ({health.avg_response_time_ms:.0f}ms) exceeds threshold ({self.max_response_time_ms:.0f}ms)",
                    metric_value=health.avg_response_time_ms,
                    threshold_value=self.max_response_time_ms,
                )
            )

        # Check for timeout patterns
        recent_runs = provider_runs[-10:] if len(provider_runs) >= 10 else provider_runs
        timeout_count = sum(
            1 for p in recent_runs if not p.success and "timeout" in str(getattr(p, "error", "")).lower()
        )
        if timeout_count >= len(recent_runs) * 0.3:  # 30%+ timeouts
            health.issues.append(
                ProviderIssue(
                    issue_type=IssueType.TIMEOUT_PRONE,
                    severity="warning",
                    message=f"Frequent timeouts: {timeout_count}/{len(recent_runs)} recent runs",
                    metric_value=timeout_count,
                    threshold_value=len(recent_runs) * 0.3,
                )
            )

    def _detect_reliability_issues(self, health: ProviderHealth, provider_runs: list):
        """Detect reliability issues."""
        # Check success rate
        if health.success_rate < self.min_success_rate:
            health.issues.append(
                ProviderIssue(
                    issue_type=IssueType.HIGH_FAILURE,
                    severity="critical" if health.success_rate < 0.5 else "warning",
                    message=f"Success rate ({health.success_rate * 100:.1f}%) below threshold ({self.min_success_rate * 100:.1f}%)",
                    metric_value=health.success_rate,
                    threshold_value=self.min_success_rate,
                )
            )

        # Check uptime
        if health.uptime_pct < self.min_uptime_pct:
            health.issues.append(
                ProviderIssue(
                    issue_type=IssueType.HIGH_FAILURE,
                    severity="warning",
                    message=f"Uptime ({health.uptime_pct * 100:.1f}%) below threshold ({self.min_uptime_pct * 100:.1f}%)",
                    metric_value=health.uptime_pct,
                    threshold_value=self.min_uptime_pct,
                )
            )

    def _detect_trend_issues(self, health: ProviderHealth, provider_runs: list):
        """Detect degradation trends."""
        if len(provider_runs) < 5:
            return  # Need more data for trend analysis

        # Compare recent average to baseline average
        baseline_runs = provider_runs[: len(provider_runs) // 2]  # First half
        recent_runs = provider_runs[len(provider_runs) // 2 :]  # Second half

        baseline_avg = sum(p.total_events for p in baseline_runs if p.success) / max(
            sum(1 for p in baseline_runs if p.success), 1
        )
        recent_avg = sum(p.total_events for p in recent_runs if p.success) / max(
            sum(1 for p in recent_runs if p.success), 1
        )

        if baseline_avg > 0:
            drop_pct = (baseline_avg - recent_avg) / baseline_avg

            if drop_pct > self.degradation_threshold:
                health.is_degrading = True
                health.trend_direction = "degrading"
                health.issues.append(
                    ProviderIssue(
                        issue_type=IssueType.DEGRADING,
                        severity="warning",
                        message=f"Event count dropped {drop_pct * 100:.1f}% from baseline (was {baseline_avg:.0f}, now {recent_avg:.0f})",
                        metric_value=recent_avg,
                        threshold_value=baseline_avg * (1 - self.degradation_threshold),
                    )
                )
            elif recent_avg > baseline_avg * 1.1:  # 10% improvement
                health.trend_direction = "improving"

    def _check_circuit_breaker(self, health: ProviderHealth, status: dict):
        """Check circuit breaker status."""
        if status.get("state") == "open":
            health.issues.append(
                ProviderIssue(
                    issue_type=IssueType.CIRCUIT_OPEN,
                    severity="critical",
                    message=f"Circuit breaker is OPEN (failures: {status.get('failure_count', 0)})",
                    metric_value=status.get("failure_count", 0),
                )
            )

    def _check_health_status(self, health: ProviderHealth, status: dict):
        """Check health check status."""
        if not status.get("healthy", True):
            health.issues.append(
                ProviderIssue(
                    issue_type=IssueType.UNHEALTHY,
                    severity="critical",
                    message=f"Health check failed: {status.get('error', 'Unknown error')}",
                )
            )

    def _calculate_score(self, health: ProviderHealth) -> float:
        """
        Calculate overall health score (0-100).

        Scoring:
        - Base score: 100
        - Critical issues: -30 points each
        - Warning issues: -10 points each
        - Info issues: -5 points each
        """
        score = 100.0

        for issue in health.issues:
            if issue.severity == "critical":
                score -= 30
            elif issue.severity == "warning":
                score -= 10
            elif issue.severity == "info":
                score -= 5

        return max(0.0, score)

    def _score_to_category(self, score: float) -> HealthScore:
        """Convert numeric score to health category."""
        if score >= 90:
            return HealthScore.EXCELLENT
        elif score >= 70:
            return HealthScore.GOOD
        elif score >= 50:
            return HealthScore.FAIR
        elif score >= 30:
            return HealthScore.POOR
        else:
            return HealthScore.CRITICAL

    def assess_all_providers(
        self,
        metrics_history: list,
        circuit_breaker_statuses: dict | None = None,
        health_check_statuses: dict | None = None,
    ) -> dict[str, ProviderHealth]:
        """
        Assess all providers that appear in metrics history.

        Args:
            metrics_history: List of PipelineMetrics
            circuit_breaker_statuses: Dict of provider_id -> status
            health_check_statuses: Dict of provider_id -> status

        Returns:
            Dict of provider_id -> ProviderHealth
        """
        # Collect all provider IDs from history
        provider_ids = set()
        for run in metrics_history:
            provider_ids.update(run.providers.keys())

        # Assess each provider
        results = {}
        for provider_id in provider_ids:
            cb_status = circuit_breaker_statuses.get(provider_id) if circuit_breaker_statuses else None
            hc_status = health_check_statuses.get(provider_id) if health_check_statuses else None

            results[provider_id] = self.assess_provider(provider_id, metrics_history, cb_status, hc_status)

        return results

    def get_unhealthy_providers(self, assessments: dict[str, ProviderHealth]) -> list[str]:
        """
        Get list of unhealthy provider IDs.

        Args:
            assessments: Dict of provider_id -> ProviderHealth

        Returns:
            List of provider IDs that are not healthy
        """
        return [pid for pid, health in assessments.items() if not health.is_healthy]

    def get_critical_providers(self, assessments: dict[str, ProviderHealth]) -> list[str]:
        """
        Get list of providers with critical issues.

        Args:
            assessments: Dict of provider_id -> ProviderHealth

        Returns:
            List of provider IDs with critical issues
        """
        return [pid for pid, health in assessments.items() if health.has_critical_issues]
