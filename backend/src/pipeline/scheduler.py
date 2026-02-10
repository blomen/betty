"""
Extraction Scheduler

Manages scheduled and continuous extraction tiers:
- Continuous: Polymarket + Pinnacle every 5 minutes
- Scheduled: Betinia every 4 hours
- Manual: All other providers (on-demand only)
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Callable, Optional

logger = logging.getLogger(__name__)


class ExtractionScheduler:
    """
    Manages tiered extraction scheduling.

    Usage:
        scheduler = ExtractionScheduler(pipeline)

        # Start continuous extraction loop
        await scheduler.start_continuous(interval_seconds=300)

        # Later, stop it
        scheduler.stop()
    """

    def __init__(self, pipeline=None):
        """
        Initialize scheduler.

        Args:
            pipeline: ExtractionPipeline instance (lazy-loaded if None)
        """
        self._pipeline = pipeline
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._last_run: Optional[datetime] = None
        self._run_count = 0
        self._interval_seconds: Optional[int] = None
        self._providers: Optional[list[str]] = None

    @property
    def pipeline(self):
        """Lazy-load pipeline if not provided."""
        if self._pipeline is None:
            from src.api.deps import get_pipeline
            self._pipeline = get_pipeline()
        return self._pipeline

    @property
    def running(self) -> bool:
        """Check if continuous extraction is running."""
        return self._running

    @property
    def last_run(self) -> Optional[datetime]:
        """Get timestamp of last extraction run."""
        return self._last_run

    @property
    def run_count(self) -> int:
        """Get number of extraction runs since start."""
        return self._run_count

    @property
    def interval_seconds(self) -> Optional[int]:
        """Get current interval in seconds (only set when running)."""
        return self._interval_seconds

    @property
    def providers(self) -> Optional[list[str]]:
        """Get current provider list (only set when running)."""
        return self._providers

    async def start_continuous(
        self,
        providers: list[str] = None,
        interval_seconds: int = 300,
        on_complete: Callable[[dict], None] = None,
    ):
        """
        Start continuous extraction loop.

        Args:
            providers: Providers to extract (default: polymarket, pinnacle)
            interval_seconds: Seconds between runs (default: 300 = 5 min)
            on_complete: Optional callback after each run
        """
        if self._running:
            logger.warning("[Scheduler] Continuous extraction already running")
            return

        if providers is None:
            providers = ["polymarket", "pinnacle"]

        self._running = True
        self._run_count = 0
        self._interval_seconds = interval_seconds
        self._providers = providers

        logger.info(
            f"[Scheduler] Starting continuous extraction: "
            f"providers={providers}, interval={interval_seconds}s"
        )

        # Create and run the extraction loop
        self._task = asyncio.create_task(
            self._extraction_loop(providers, interval_seconds, on_complete)
        )

    async def _extraction_loop(
        self,
        providers: list[str],
        interval_seconds: int,
        on_complete: Callable[[dict], None] = None,
    ):
        """Internal extraction loop."""
        while self._running:
            try:
                start_time = datetime.now(timezone.utc)

                logger.info(f"[Scheduler] Running extraction #{self._run_count + 1}")

                # Run extraction with state updates for UI
                results = await self._run_with_state_updates(providers)

                self._last_run = datetime.now(timezone.utc)
                self._run_count += 1

                elapsed = (self._last_run - start_time).total_seconds()
                logger.info(
                    f"[Scheduler] Extraction #{self._run_count} complete: "
                    f"{results.get('total_events', 0)} events, "
                    f"{results.get('total_odds', 0)} odds in {elapsed:.1f}s"
                )

                if on_complete:
                    on_complete(results)

                # Wait for next interval
                wait_time = max(0, interval_seconds - elapsed)
                if wait_time > 0:
                    logger.debug(f"[Scheduler] Waiting {wait_time:.0f}s until next run")
                    await asyncio.sleep(wait_time)

            except asyncio.CancelledError:
                logger.info("[Scheduler] Extraction loop cancelled")
                break
            except Exception as e:
                logger.error(f"[Scheduler] Extraction error: {e}", exc_info=True)
                # Clear running state on error
                self._clear_extraction_state()
                # Wait before retry on error
                await asyncio.sleep(60)

        logger.info("[Scheduler] Extraction loop stopped")

    async def _run_with_state_updates(self, providers: list[str]) -> dict:
        """Run extraction with UI state updates."""
        from src.api.state import update_extraction_state
        from src.api.routes.extraction import _build_final_state

        # Initialize extraction state
        update_extraction_state(
            running=True,
            start_time=datetime.now(timezone.utc),
            total_events=0,
            total_odds=0,
            providers={},
            current_provider=None,
            completed_providers=0,
            total_providers=len(providers),
        )

        _results = None
        try:
            # Start metrics polling task
            stop_event = asyncio.Event()
            polling_task = asyncio.create_task(
                self._poll_metrics_loop(stop_event)
            )

            try:
                _results = await self.pipeline.run(providers=providers)
            finally:
                stop_event.set()
                try:
                    await polling_task
                except Exception:
                    pass

        finally:
            # Final state update in finally block (guaranteed to run)
            if _results:
                try:
                    final = _build_final_state(_results)
                    update_extraction_state(
                        total_events=_results.get("total_events", 0),
                        total_odds=_results.get("total_odds", 0),
                        providers=final["providers"],
                        completed_providers=final["completed_providers"],
                        total_providers=final["total_providers"],
                        current_provider=None,
                        last_run=datetime.now(timezone.utc).isoformat(),
                    )
                except Exception:
                    pass
            # Compute final elapsed time before clearing running flag
            from src.api.state import extraction_state
            start = extraction_state.get("start_time")
            if start:
                final_elapsed = (datetime.now(timezone.utc) - start).total_seconds()
                update_extraction_state(elapsed_seconds=final_elapsed)
            update_extraction_state(running=False)

        return _results or {}

    async def _poll_metrics_loop(self, stop_event: asyncio.Event):
        """Poll metrics and update extraction state.

        Uses a single DB session for the entire loop, calling expire_all()
        each iteration so COUNT queries see fresh data without creating
        hundreds of sessions during a run.
        """
        from src.api.state import update_extraction_state
        from src.db.models import Event, Odds, get_session

        db = get_session()
        try:
            while not stop_event.is_set():
                if not self.pipeline.metrics:
                    await asyncio.sleep(0.5)
                    continue

                current_run = self.pipeline.metrics.get_current_run()
                if not current_run:
                    await asyncio.sleep(0.5)
                    continue

                # Build provider states from metrics
                providers_state = {}
                completed_count = 0
                current_provider = None

                for pid, pm in current_run.providers.items():
                    status = "pending"
                    if pm.is_complete:
                        status = "completed" if pm.success else "failed"
                        completed_count += 1
                    elif pm.start_time and not pm.is_complete:
                        status = "running"
                        current_provider = pid

                    providers_state[pid] = {
                        "status": status,
                        "events": pm.total_events,
                        "odds": pm.total_odds,
                        "duration_seconds": pm.duration_seconds,
                        "error": pm.error,
                        "sports_completed": pm.sports_succeeded,
                        "sports_total": pm.sports_attempted,
                    }

                # Expire cached objects so COUNT sees rows committed by
                # the extraction pipeline running in the same process
                db.expire_all()

                total_events = db.query(Event).count()
                total_odds = db.query(Odds).count()

                # Update global state
                update_extraction_state(
                    total_events=total_events,
                    total_odds=total_odds,
                    providers=providers_state,
                    current_provider=current_provider,
                    completed_providers=completed_count,
                )

                await asyncio.sleep(0.5)
        finally:
            db.close()

    def _clear_extraction_state(self):
        """Clear extraction state on error."""
        try:
            from src.api.state import update_extraction_state
            update_extraction_state(running=False)
        except Exception:
            pass

    def stop(self):
        """Stop continuous extraction."""
        if not self._running:
            logger.warning("[Scheduler] Continuous extraction not running")
            return

        self._running = False
        self._interval_seconds = None
        self._providers = None

        if self._task:
            self._task.cancel()
            self._task = None

        logger.info(
            f"[Scheduler] Stopped continuous extraction after {self._run_count} runs"
        )

    async def run_once(self, providers: list[str] = None) -> dict:
        """
        Run a single extraction (for manual tier).

        Args:
            providers: Providers to extract (default: all enabled)

        Returns:
            Extraction results dict
        """
        if providers is None:
            providers = self.pipeline.engine.get_enabled_providers()

        logger.info(f"[Scheduler] Running one-time extraction: {providers}")
        results = await self.pipeline.run(providers=providers)

        return results

    def get_status(self) -> dict:
        """Get scheduler status."""
        # Calculate next run time
        next_run = None
        if self._running and self._last_run and self._interval_seconds:
            from datetime import timedelta
            next_run_dt = self._last_run + timedelta(seconds=self._interval_seconds)
            next_run = next_run_dt.isoformat()

        return {
            "running": self._running,
            "last_run": self._last_run.isoformat() if self._last_run else None,
            "run_count": self._run_count,
            "interval_seconds": self._interval_seconds,
            "providers": self._providers,
            "next_run": next_run,
        }


# Global scheduler instance
_scheduler: Optional[ExtractionScheduler] = None


def get_scheduler() -> ExtractionScheduler:
    """Get or create global scheduler instance."""
    global _scheduler
    if _scheduler is None:
        _scheduler = ExtractionScheduler()
    return _scheduler


def reset_scheduler():
    """Reset global scheduler (for testing)."""
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.stop()
    _scheduler = None
