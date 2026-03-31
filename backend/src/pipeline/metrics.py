"""
Performance Metrics Tracking

Thread-safe performance tracking for extraction pipeline.
Tracks per-sport, per-provider, and full pipeline metrics.
"""

import logging
import time
from collections import deque
from dataclasses import dataclass, field
from threading import Lock
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class SportMetrics:
    """Per-sport extraction metrics."""
    sport: str
    start_time: float = field(default_factory=time.time)
    end_time: Optional[float] = None
    duration_seconds: float = 0.0
    events_processed: int = 0
    events_new: int = 0
    events_matched: int = 0
    events_unmatched: int = 0
    odds_processed: int = 0
    odds_new: int = 0
    success: bool = False
    error: Optional[str] = None
    # Market breakdown (set by MetricsCollector.end_sport)
    market_counts: Dict[str, int] = field(default_factory=dict)

    def end(self, success: bool = True, error: Optional[str] = None):
        """Mark sport extraction complete."""
        self.end_time = time.time()
        self.duration_seconds = self.end_time - self.start_time
        self.success = success
        self.error = error

    @property
    def is_complete(self) -> bool:
        """Check if metrics collection is complete."""
        return self.end_time is not None


@dataclass
class ProviderMetrics:
    """Per-provider extraction metrics."""
    provider_id: str
    start_time: float = field(default_factory=time.time)
    end_time: Optional[float] = None
    duration_seconds: float = 0.0
    sports: Dict[str, SportMetrics] = field(default_factory=dict)
    total_sports_configured: int = 0  # Total sports this provider will extract
    retries: int = 0
    cache_hits: int = 0
    rate_limit_hits: int = 0  # 429 errors encountered
    success: bool = False
    error: Optional[str] = None

    def start_sport(self, sport: str) -> SportMetrics:
        """Start tracking sport extraction."""
        sport_metrics = SportMetrics(sport=sport)
        self.sports[sport] = sport_metrics
        return sport_metrics

    def end_sport(
        self,
        sport: str,
        events_processed: int = 0,
        events_new: int = 0,
        odds_processed: int = 0,
        odds_new: int = 0,
        success: bool = True,
        error: Optional[str] = None
    ):
        """Mark sport extraction complete."""
        if sport in self.sports:
            metrics = self.sports[sport]
            metrics.events_processed = events_processed
            metrics.events_new = events_new
            metrics.odds_processed = odds_processed
            metrics.odds_new = odds_new
            metrics.end(success=success, error=error)

    def end(self, success: bool = True, error: Optional[str] = None):
        """Mark provider extraction complete."""
        self.end_time = time.time()
        self.duration_seconds = self.end_time - self.start_time
        self.success = success
        self.error = error

    @property
    def is_complete(self) -> bool:
        """Check if metrics collection is complete."""
        return self.end_time is not None

    @property
    def total_events(self) -> int:
        """Total events processed across all sports."""
        return sum(s.events_processed for s in self.sports.values())

    @property
    def total_events_new(self) -> int:
        """Total new events across all sports."""
        return sum(s.events_new for s in self.sports.values())

    @property
    def total_odds(self) -> int:
        """Total odds processed across all sports."""
        return sum(s.odds_processed for s in self.sports.values())

    @property
    def total_odds_new(self) -> int:
        """Total new odds across all sports."""
        return sum(s.odds_new for s in self.sports.values())

    @property
    def total_events_matched(self) -> int:
        """Total matched events across all sports."""
        return sum(s.events_matched for s in self.sports.values())

    @property
    def total_events_unmatched(self) -> int:
        """Total unmatched events across all sports."""
        return sum(s.events_unmatched for s in self.sports.values())

    @property
    def match_rate(self) -> float:
        """Pinnacle match rate (0-1). Only meaningful for soft providers."""
        total = self.total_events_matched + self.total_events_unmatched
        return self.total_events_matched / total if total > 0 else 0.0

    @property
    def sports_attempted(self) -> int:
        """Number of sports attempted."""
        return len(self.sports)

    @property
    def sports_succeeded(self) -> int:
        """Number of sports that succeeded."""
        return sum(1 for s in self.sports.values() if s.success)

    @property
    def sports_done(self) -> int:
        """Number of sports fully completed (succeeded or failed)."""
        return sum(1 for s in self.sports.values() if s.is_complete)

    @property
    def sports_failed(self) -> int:
        """Number of sports that failed."""
        return sum(1 for s in self.sports.values() if s.is_complete and not s.success)

    @property
    def success_rate(self) -> float:
        """Sport-level success rate (0-1)."""
        if self.sports_attempted == 0:
            return 0.0
        return self.sports_succeeded / self.sports_attempted


@dataclass
class PipelineMetrics:
    """Full pipeline run metrics."""
    run_id: str
    start_time: float = field(default_factory=time.time)
    end_time: Optional[float] = None
    duration_seconds: float = 0.0
    providers: Dict[str, ProviderMetrics] = field(default_factory=dict)
    polymarket_events: int = 0
    polymarket_odds: int = 0

    def start_provider(self, provider_id: str) -> ProviderMetrics:
        """Start tracking provider extraction."""
        provider_metrics = ProviderMetrics(provider_id=provider_id)
        self.providers[provider_id] = provider_metrics
        return provider_metrics

    def end_provider(
        self,
        provider_id: str,
        success: bool = True,
        error: Optional[str] = None
    ):
        """Mark provider extraction complete."""
        if provider_id in self.providers:
            self.providers[provider_id].end(success=success, error=error)

    def end(self):
        """Mark pipeline run complete."""
        self.end_time = time.time()
        self.duration_seconds = self.end_time - self.start_time

    @property
    def is_complete(self) -> bool:
        """Check if metrics collection is complete."""
        return self.end_time is not None

    @property
    def total_events(self) -> int:
        """Total events processed across all providers."""
        return sum(p.total_events for p in self.providers.values())

    @property
    def total_odds(self) -> int:
        """Total odds processed across all providers."""
        return sum(p.total_odds for p in self.providers.values())

    @property
    def providers_attempted(self) -> int:
        """Number of providers attempted."""
        return len(self.providers)

    @property
    def providers_succeeded(self) -> int:
        """Number of providers that succeeded."""
        return sum(1 for p in self.providers.values() if p.success)

    @property
    def providers_failed(self) -> int:
        """Number of providers that failed."""
        return sum(1 for p in self.providers.values() if not p.success)

    @property
    def overall_success_rate(self) -> float:
        """Provider-level success rate (0-1)."""
        if self.providers_attempted == 0:
            return 0.0
        return self.providers_succeeded / self.providers_attempted

    @property
    def total_retries(self) -> int:
        """Total retries across all providers."""
        return sum(p.retries for p in self.providers.values())

    @property
    def total_cache_hits(self) -> int:
        """Total cache hits across all providers."""
        return sum(p.cache_hits for p in self.providers.values())

    @property
    def total_rate_limit_hits(self) -> int:
        """Total 429 rate limit errors across all providers."""
        return sum(p.rate_limit_hits for p in self.providers.values())

    def to_dict(self) -> dict:
        """Convert to dictionary for API/storage."""
        return {
            "run_id": self.run_id,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "duration_seconds": self.duration_seconds,
            "total_events": self.total_events,
            "total_odds": self.total_odds,
            "providers_attempted": self.providers_attempted,
            "providers_succeeded": self.providers_succeeded,
            "providers_failed": self.providers_failed,
            "overall_success_rate": self.overall_success_rate,
            "total_retries": self.total_retries,
            "total_cache_hits": self.total_cache_hits,
            "polymarket": {
                "events": self.polymarket_events,
                "odds": self.polymarket_odds
            },
            "providers": {
                pid: {
                    "duration_seconds": p.duration_seconds,
                    "total_events": p.total_events,
                    "total_events_new": p.total_events_new,
                    "total_odds": p.total_odds,
                    "total_odds_new": p.total_odds_new,
                    "sports_attempted": p.sports_attempted,
                    "sports_succeeded": p.sports_succeeded,
                    "sports_failed": p.sports_failed,
                    "success_rate": p.success_rate,
                    "retries": p.retries,
                    "cache_hits": p.cache_hits,
                    "rate_limit_hits": p.rate_limit_hits,
                    "success": p.success,
                    "error": p.error,
                    "events_matched": p.total_events_matched,
                    "events_unmatched": p.total_events_unmatched,
                    "match_rate": p.match_rate,
                    "sports": {
                        sport: {
                            "duration_seconds": s.duration_seconds,
                            "events_processed": s.events_processed,
                            "events_new": s.events_new,
                            "events_matched": s.events_matched,
                            "events_unmatched": s.events_unmatched,
                            "odds_processed": s.odds_processed,
                            "odds_new": s.odds_new,
                            "market_counts": s.market_counts,
                            "success": s.success,
                            "error": s.error
                        }
                        for sport, s in p.sports.items()
                    }
                }
                for pid, p in self.providers.items()
            }
        }


class MetricsCollector:
    """
    Thread-safe metrics collector.

    Tracks performance metrics for pipeline runs with history.
    """

    def __init__(self, max_history: int = 100):
        """
        Initialize metrics collector.

        Args:
            max_history: Maximum number of runs to keep in history
        """
        self.max_history = max_history
        self._lock = Lock()
        self._history: deque[PipelineMetrics] = deque(maxlen=max_history)
        self._current_run: Optional[PipelineMetrics] = None

    def start_run(self, run_id: str) -> PipelineMetrics:
        """
        Start tracking a new pipeline run.

        Args:
            run_id: Unique run identifier

        Returns:
            PipelineMetrics instance
        """
        with self._lock:
            self._current_run = PipelineMetrics(run_id=run_id)
            logger.debug(f"[Metrics] Started run: {run_id}")
            return self._current_run

    def end_run(self):
        """Mark current run complete and add to history."""
        with self._lock:
            if self._current_run:
                self._current_run.end()
                self._history.append(self._current_run)
                logger.info(
                    f"[Metrics] Run {self._current_run.run_id} complete: "
                    f"{self._current_run.duration_seconds:.1f}s, "
                    f"{self._current_run.total_events} events, "
                    f"{self._current_run.providers_succeeded}/{self._current_run.providers_attempted} providers"
                )
                self._current_run = None

    def get_current_run(self) -> Optional[PipelineMetrics]:
        """Get current run metrics."""
        with self._lock:
            return self._current_run

    def start_provider(self, provider_id: str) -> Optional[ProviderMetrics]:
        """
        Start tracking provider extraction.

        Args:
            provider_id: Provider identifier

        Returns:
            ProviderMetrics instance or None if no current run
        """
        with self._lock:
            if self._current_run:
                return self._current_run.start_provider(provider_id)
            return None

    def set_provider_total_sports(self, provider_id: str, total: int):
        """Set the total number of configured sports for a provider."""
        with self._lock:
            if self._current_run and provider_id in self._current_run.providers:
                self._current_run.providers[provider_id].total_sports_configured = total

    def end_provider(
        self,
        provider_id: str,
        success: bool = True,
        error: Optional[str] = None
    ):
        """
        Mark provider extraction complete.

        Args:
            provider_id: Provider identifier
            success: Whether extraction succeeded
            error: Optional error message
        """
        with self._lock:
            if self._current_run:
                self._current_run.end_provider(provider_id, success=success, error=error)

    def start_sport(self, provider_id: str, sport: str) -> Optional[SportMetrics]:
        """
        Start tracking sport extraction.

        Args:
            provider_id: Provider identifier
            sport: Sport name

        Returns:
            SportMetrics instance or None if no current run/provider
        """
        with self._lock:
            if self._current_run and provider_id in self._current_run.providers:
                return self._current_run.providers[provider_id].start_sport(sport)
            return None

    def end_sport(
        self,
        provider_id: str,
        sport: str,
        events_processed: int = 0,
        events_new: int = 0,
        events_matched: int = 0,
        events_unmatched: int = 0,
        odds_processed: int = 0,
        odds_new: int = 0,
        success: bool = True,
        error: Optional[str] = None,
        market_counts: Optional[Dict[str, int]] = None,
    ):
        """
        Mark sport extraction complete.

        Args:
            provider_id: Provider identifier
            sport: Sport name
            events_processed: Number of events processed
            events_new: Number of new events
            events_matched: Events matched to Pinnacle
            events_unmatched: Events not matched to Pinnacle
            odds_processed: Number of odds processed
            odds_new: Number of new odds
            success: Whether extraction succeeded
            error: Optional error message
            market_counts: Dict of market type → count (1x2, spread, total, etc.)
        """
        with self._lock:
            if self._current_run and provider_id in self._current_run.providers:
                self._current_run.providers[provider_id].end_sport(
                    sport=sport,
                    events_processed=events_processed,
                    events_new=events_new,
                    odds_processed=odds_processed,
                    odds_new=odds_new,
                    success=success,
                    error=error
                )
                # Set match stats and market counts directly on sport metrics
                sport_metrics = self._current_run.providers[provider_id].sports.get(sport)
                if sport_metrics:
                    sport_metrics.events_matched = events_matched
                    sport_metrics.events_unmatched = events_unmatched
                    if market_counts:
                        sport_metrics.market_counts = market_counts

    def record_retry(self, provider_id: str):
        """
        Record retry for provider.

        Args:
            provider_id: Provider identifier
        """
        with self._lock:
            if self._current_run and provider_id in self._current_run.providers:
                self._current_run.providers[provider_id].retries += 1

    def record_cache_hit(self, provider_id: str):
        """
        Record cache hit for provider.

        Args:
            provider_id: Provider identifier
        """
        with self._lock:
            if self._current_run and provider_id in self._current_run.providers:
                self._current_run.providers[provider_id].cache_hits += 1

    def record_rate_limit(self, provider_id: str):
        """
        Record 429 rate limit error for provider.

        Args:
            provider_id: Provider identifier
        """
        with self._lock:
            if self._current_run and provider_id in self._current_run.providers:
                self._current_run.providers[provider_id].rate_limit_hits += 1
                logger.warning(f"[Metrics] Rate limit (429) recorded for {provider_id}")

    def set_polymarket_stats(self, events: int, odds: int):
        """
        Set Polymarket extraction stats.

        Args:
            events: Number of events
            odds: Number of odds
        """
        with self._lock:
            if self._current_run:
                self._current_run.polymarket_events = events
                self._current_run.polymarket_odds = odds

    def get_history(self, limit: int = 10) -> List[PipelineMetrics]:
        """
        Get historical run metrics.

        Args:
            limit: Maximum number of runs to return

        Returns:
            List of PipelineMetrics (newest first)
        """
        with self._lock:
            # Return newest first
            return list(reversed(list(self._history)))[:limit]

    def get_provider_aggregate(self, provider_id: str, limit: int = 10) -> dict:
        """
        Get aggregate metrics for a provider across recent runs.

        Args:
            provider_id: Provider identifier
            limit: Number of recent runs to consider

        Returns:
            Aggregate statistics dictionary
        """
        with self._lock:
            recent_runs = list(reversed(list(self._history)))[:limit]

            provider_runs = [
                run.providers[provider_id]
                for run in recent_runs
                if provider_id in run.providers
            ]

            if not provider_runs:
                return {
                    "provider_id": provider_id,
                    "runs": 0,
                    "avg_duration_seconds": 0.0,
                    "avg_events": 0.0,
                    "avg_success_rate": 0.0,
                    "total_retries": 0,
                    "total_cache_hits": 0
                }

            return {
                "provider_id": provider_id,
                "runs": len(provider_runs),
                "avg_duration_seconds": sum(p.duration_seconds for p in provider_runs) / len(provider_runs),
                "avg_events": sum(p.total_events for p in provider_runs) / len(provider_runs),
                "avg_success_rate": sum(p.success_rate for p in provider_runs) / len(provider_runs),
                "total_retries": sum(p.retries for p in provider_runs),
                "total_cache_hits": sum(p.cache_hits for p in provider_runs)
            }

    def persist_to_db(self, run_metrics: PipelineMetrics, session, report: str = None, tier_name: str | None = None, max_runs_per_tier: int = 10):
        """
        Persist pipeline metrics to database.
        Keeps the last `max_runs_per_tier` runs per tier — prunes oldest beyond that.

        Args:
            run_metrics: PipelineMetrics instance to persist
            session: SQLAlchemy session
            report: Optional extraction report text
            tier_name: Tier name (sharp/api_soft/browser_soft) stored in trigger field
            max_runs_per_tier: Number of historical runs to keep per tier (default 10)
        """
        from datetime import datetime as dt, timezone as tz
        from src.db.models import ExtractionRun, ProviderRunMetrics, SportRunMetrics

        trigger = tier_name or 'manual'

        try:
            # Prune old runs beyond max_runs_per_tier (keep N-1 since we're about to add 1)
            existing_runs = (
                session.query(ExtractionRun)
                .filter(ExtractionRun.trigger == trigger)
                .order_by(ExtractionRun.start_time.desc())
                .all()
            )
            runs_to_delete = existing_runs[max_runs_per_tier - 1:]  # Keep N-1, adding 1 new = N total
            if runs_to_delete:
                old_ids = [r.id for r in runs_to_delete]
                session.query(SportRunMetrics).filter(SportRunMetrics.run_id.in_(old_ids)).delete(synchronize_session=False)
                session.query(ProviderRunMetrics).filter(ProviderRunMetrics.run_id.in_(old_ids)).delete(synchronize_session=False)
                session.query(ExtractionRun).filter(ExtractionRun.id.in_(old_ids)).delete(synchronize_session=False)
                session.flush()

            # Create extraction run record
            run = ExtractionRun(
                id=run_metrics.run_id,
                start_time=dt.fromtimestamp(run_metrics.start_time, tz=tz.utc),
                end_time=dt.fromtimestamp(run_metrics.end_time, tz=tz.utc) if run_metrics.end_time else None,
                duration_seconds=run_metrics.duration_seconds,
                providers_attempted=run_metrics.providers_attempted,
                providers_succeeded=run_metrics.providers_succeeded,
                providers_failed=run_metrics.providers_failed,
                total_events=run_metrics.total_events,
                total_odds=run_metrics.total_odds,
                polymarket_events=run_metrics.polymarket_events,
                trigger=trigger,
                config=run_metrics.to_dict(),
                report=report,
            )
            session.merge(run)

            # Create provider metrics records
            for provider_id, pmetrics in run_metrics.providers.items():
                # Aggregate market counts across sports for provider-level totals
                prov_ml = 0
                prov_spr = 0
                prov_tot = 0
                for smetrics in pmetrics.sports.values():
                    mc = getattr(smetrics, 'market_counts', {}) or {}
                    prov_ml += mc.get("1x2", 0) + mc.get("moneyline", 0)
                    prov_spr += mc.get("spread", 0)
                    prov_tot += mc.get("total", 0)

                pm = ProviderRunMetrics(
                    run_id=run_metrics.run_id,
                    provider_id=provider_id,
                    start_time=dt.fromtimestamp(pmetrics.start_time, tz=tz.utc),
                    end_time=dt.fromtimestamp(pmetrics.end_time, tz=tz.utc) if pmetrics.end_time else None,
                    duration_seconds=pmetrics.duration_seconds,
                    events_processed=pmetrics.total_events,
                    events_new=pmetrics.total_events_new,
                    odds_processed=pmetrics.total_odds,
                    odds_new=pmetrics.total_odds_new,
                    sports_attempted=pmetrics.sports_attempted,
                    sports_succeeded=pmetrics.sports_succeeded,
                    events_matched=pmetrics.total_events_matched,
                    events_unmatched=pmetrics.total_events_unmatched,
                    ml_count=prov_ml,
                    spread_count=prov_spr,
                    total_count=prov_tot,
                    retries=pmetrics.retries,
                    cache_hits=pmetrics.cache_hits,
                    status='success' if pmetrics.success else 'failed',
                    error_message=pmetrics.error
                )
                session.add(pm)
                session.flush()  # Get pm.id for sport metrics

                # Create sport metrics
                for sport, smetrics in pmetrics.sports.items():
                    mc = getattr(smetrics, 'market_counts', {}) or {}
                    sm = SportRunMetrics(
                        run_id=run_metrics.run_id,
                        provider_run_id=pm.id,
                        provider_id=provider_id,
                        sport=sport,
                        events_extracted=smetrics.events_processed,
                        events_new=smetrics.events_new,
                        odds_extracted=smetrics.odds_processed,
                        odds_new=smetrics.odds_new,
                        duration_seconds=smetrics.duration_seconds,
                        events_matched=smetrics.events_matched,
                        events_unmatched=smetrics.events_unmatched,
                        ml_count=mc.get("1x2", 0) + mc.get("moneyline", 0),
                        spread_count=mc.get("spread", 0),
                        total_count=mc.get("total", 0),
                        success=smetrics.success,
                        error_type='extraction_error' if smetrics.error else None,
                        error_message=smetrics.error
                    )
                    session.add(sm)

            session.commit()
            logger.info(f"[Metrics] Persisted metrics for run {run_metrics.run_id} to database")

        except Exception as e:
            logger.error(f"[Metrics] Failed to persist to database: {e}")
            session.rollback()
            raise

    @property
    def current_run(self) -> Optional[PipelineMetrics]:
        """Get current run (property for backward compatibility)."""
        return self.get_current_run()
