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

    @property
    def pipeline(self):
        """Lazy-load pipeline if not provided."""
        if self._pipeline is None:
            from .orchestrator import ExtractionPipeline
            self._pipeline = ExtractionPipeline()
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
                results = await self.pipeline.run(providers=providers)

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
                # Wait before retry on error
                await asyncio.sleep(60)

        logger.info("[Scheduler] Extraction loop stopped")

    def stop(self):
        """Stop continuous extraction."""
        if not self._running:
            logger.warning("[Scheduler] Continuous extraction not running")
            return

        self._running = False

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
        return {
            "running": self._running,
            "last_run": self._last_run.isoformat() if self._last_run else None,
            "run_count": self._run_count,
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
